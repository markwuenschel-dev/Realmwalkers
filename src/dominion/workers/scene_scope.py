"""Beat-ownership scope guards over assembled scene prose (recovery lane L2).

The ChapterSequence body carries ``beat_ownership`` (beat text -> owning scene_no), per-scene
``owned_beats``, and ``forbidden_duplicate_functions`` — but historically nothing enforced them:
every drafter restarted from the global chapter entry and re-performed later scenes' irreversible
beats (recognition, hood-tear, interruption, forced consent), and chapter QA never compared prose
against ownership. These pure functions close that gap deterministically:

- ``detect_scene_scope_bleed``    — a scene's prose performs a beat OWNED BY A LATER scene
                                    -> issue kind ``scene_scope_bleed``.
- ``detect_duplicate_irreversible_beats`` — an irreversible beat appears in MORE THAN ONE scene's
                                    prose -> issue kind ``duplicate_irreversible_beat``.
- ``evaluate_scene_scope``        — both checks over a whole assembled chapter.

Match patterns are DERIVED from the beat_ownership entries themselves (keyword extraction +
light stemming), never hardcoded story strings; the only fixed vocabulary is generic English
(stopwords, beat-authoring directives, and narrative-function markers of irreversibility).
No LLM, no I/O, no dominion imports — keep this module importable and pure (regression tests
in lane 10 import from here).
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from typing import Any

# Issue kinds (pinned vocabulary — triage clusters key on these exact strings).
SCENE_SCOPE_BLEED = "scene_scope_bleed"
DUPLICATE_IRREVERSIBLE_BEAT = "duplicate_irreversible_beat"

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z']*")

# Generic English function words + very common narrative nouns. Filtering these keeps a beat's
# keyword signature down to its distinctive content words. Deliberately story-agnostic.
_STOPWORDS: frozenset[str] = frozenset(
    """
    a an the and or but nor so yet for of to in on at by with from into onto over under between
    among through during before after until while as if than then once again also too very just
    only own same such other another each every any some all both few more most further
    he she it they them him her his hers its their theirs we us our ours you your yours i me my
    mine who whom whose which what that this these those there here where when how why
    is are was were be been being am do does did done doing have has had having will would shall
    should can could may might must not no none
    toward towards without within against about above below up down out off near per
    thing things way ways one ones man woman men women time times day days night nights moment
    moments face hand hands eye eyes head voice look looks point back place first last least
    rather scene page chapter reader beat beats prose story equivalent
    """.split()
)

# Imperative verbs beat authors use to phrase ownership entries ("Show X", "Have Y do Z", ...).
# They describe the act of authoring, not the beat's content.
_DIRECTIVE_VERBS: frozenset[str] = frozenset(
    """
    show have let use keep make end establish set bridge introduce give take get put stay begin
    start stop run go come
    """.split()
)

# Narrative-function words that mark a beat as IRREVERSIBLE — once performed on the page, it
# cannot be un-performed for the reader (reveals, recognitions, deaths, interruptions of an
# established frame, coerced consent, arrivals/introductions). Generic craft vocabulary, not
# story strings.
_IRREVERSIBLE_MARKERS: tuple[str, ...] = (
    "reveal",
    "revelation",
    "recognize",
    "recognition",
    "unmask",
    "unveil",
    "expose",
    "discover",
    "discovery",
    "confess",
    "confession",
    "betray",
    "betrayal",
    "interrupt",
    "interruption",
    "consent",
    "coerce",
    "coercion",
    "forced",
    "kill",
    "killed",
    "death",
    "die",
    "died",
    "dying",
    "kiss",
    "tear",
    "crack",
    "break",
    "breaking",
    "broken",
    "shatter",
    "arrive",
    "arrival",
    "entrance",
    "introduce",
    "introduction",
    "violate",
    "violation",
    "irreversible",
)

# Suffixes stripped (longest first) when the remainder keeps >= 4 chars — a cheap stemmer good
# enough to align "recognition"/"recognized", "coercion"/"coerced", "interruption"/"interrupted".
_SUFFIXES: tuple[str, ...] = (
    "izations",
    "ization",
    "ations",
    "ation",
    "ingly",
    "tions",
    "tion",
    "sions",
    "sion",
    "ences",
    "ence",
    "ments",
    "ment",
    "ness",
    "ings",
    "ing",
    "ions",
    "ion",
    "edly",
    "ers",
    "ies",
    "ied",
    "es",
    "ed",
    "er",
    "ly",
    "s",
)


def _stem(word: str) -> str:
    for suffix in _SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)]
    return word


def _stems_match(a: str, b: str) -> bool:
    """Two stemmed tokens refer to the same content word: equal, or one is a prefix of the other
    with at least 4 shared chars (aligns "recogni"/"recogniz"). Short stems must match exactly so
    "red" can never match "reduce"."""
    if a == b:
        return True
    if min(len(a), len(b)) < 4:
        return False
    return a.startswith(b) or b.startswith(a)


def _beat_tokens(beat: str) -> list[tuple[str, bool]]:
    """(token, is_proper_noun) pairs. A capitalized token that is not sentence-initial is treated
    as a proper noun — names (POV, factions) recur in every scene and carry no scope signal."""
    out: list[tuple[str, bool]] = []
    for index, match in enumerate(_WORD_RE.finditer(beat)):
        token = match.group(0)
        out.append((token.lower(), index > 0 and token[0].isupper()))
    return out


def beat_keywords(beat: str, proper_nouns: frozenset[str] = frozenset()) -> list[str]:
    """Distinctive content keywords of one beat_ownership entry, in order, deduped.

    Drops the leading authoring directive ("Show", "Have", ...), proper nouns, stopwords, and
    sub-3-char tokens. What survives is the beat's own fingerprint — the match pattern is derived
    from the beat text, never hardcoded.

    ``_beat_tokens`` only flags a capitalized token as proper when it is NOT sentence-initial, so a
    name that LEADS a beat ("Marcus arrives ...") slips through as a keyword. ``proper_nouns`` closes
    that gap: it is the corpus-derived set (see ``_corpus_proper_nouns``) of lowercased tokens seen
    capitalized mid-sentence ANYWHERE in the beat corpus, compared in the same lowercased form these
    tokens carry. Any token in it is dropped regardless of position. Defaults to empty, so a bare
    ``beat_keywords(beat)`` call is unchanged."""
    keywords: list[str] = []
    seen: set[str] = set()
    for token, is_proper in _beat_tokens(beat):
        if is_proper or len(token) < 3:
            continue
        if token in _STOPWORDS or token in _DIRECTIVE_VERBS:
            continue
        if token in proper_nouns:
            continue
        if token not in seen:
            seen.add(token)
            keywords.append(token)
    return keywords


def is_irreversible_beat(beat: str, proper_nouns: frozenset[str] = frozenset()) -> bool:
    """A beat is irreversible when its own text names an irreversible narrative function
    (reveal / recognition / interruption / consent / death / arrival ...). Proper nouns are
    excluded so a faction name can't accidentally match a marker — including a corpus-derived
    leading name (``proper_nouns``, default empty) that ``_beat_tokens`` misses at position 0."""
    marker_stems = [_stem(m) for m in _IRREVERSIBLE_MARKERS]
    for token, is_proper in _beat_tokens(beat):
        if is_proper or token in proper_nouns:
            continue
        stem = _stem(token)
        if any(_stems_match(stem, marker) for marker in marker_stems):
            return True
    return False


def _prose_stems(prose: str) -> set[str]:
    return {_stem(match.group(0).lower()) for match in _WORD_RE.finditer(prose)}


def _required_hits(keyword_count: int) -> int:
    if keyword_count <= 2:
        return keyword_count
    if keyword_count == 3:
        return 2
    return max(3, math.ceil(0.4 * keyword_count))


def matched_beat_keywords(beat: str, prose: str, proper_nouns: frozenset[str] = frozenset()) -> list[str]:
    """The beat keywords whose stems appear in the prose (whole-token, stem-aligned). ``proper_nouns``
    (default empty) is forwarded to ``beat_keywords`` so a corpus-derived leading name is not matched."""
    stems = _prose_stems(prose)
    return [k for k in beat_keywords(beat, proper_nouns) if any(_stems_match(_stem(k), s) for s in stems)]


def beat_matches_prose(beat: str, prose: str) -> bool:
    """True when enough of the beat's derived keywords co-occur in the prose to say the beat was
    performed there. Threshold scales with the beat's keyword count (all of <=2, 2 of 3, then
    >= max(3, 40%)) so short beats need a full match and long beats tolerate paraphrase."""
    keywords = beat_keywords(beat)
    if not keywords:
        return False
    return len(matched_beat_keywords(beat, prose)) >= _required_hits(len(keywords))


# --------------------------------------------------------------------------------------------
# Sequence-body projections


def _scene_items(sequence_body: Mapping[str, Any]) -> list[dict[str, Any]]:
    return sorted(
        [s for s in (sequence_body.get("scenes") or []) if isinstance(s, dict)],
        key=lambda s: int(s.get("scene_no") or 0),
    )


def beat_ownership_map(sequence_body: Mapping[str, Any]) -> dict[str, int]:
    """beat text -> owning scene_no, merged from per-scene owned_beats/required_beats and the
    body-level beat_ownership map (the explicit map wins on conflict)."""
    owners: dict[str, int] = {}
    for item in _scene_items(sequence_body):
        scene_no = item.get("scene_no")
        if not isinstance(scene_no, int) or scene_no <= 0:
            continue
        beats = item.get("owned_beats") or item.get("required_beats") or []
        if isinstance(beats, list):
            for beat in beats:
                text = str(beat).strip()
                if text and text not in owners:
                    owners[text] = scene_no
    explicit = sequence_body.get("beat_ownership")
    if isinstance(explicit, Mapping):
        for beat, scene_no in explicit.items():
            text = str(beat).strip()
            if text and isinstance(scene_no, int) and scene_no > 0:
                owners[text] = scene_no
    return owners


def _corpus_proper_nouns(sequence_body: Mapping[str, Any]) -> frozenset[str]:
    """The corpus-derived proper-noun set: every token ``_beat_tokens`` already marks proper
    (capitalized, non-sentence-initial) in ANY beat of this sequence body, lowercased to the exact
    form ``beat_keywords`` compares against.

    A recurring name is capitalized mid-sentence somewhere in the corpus, so it lands here; passing
    this set into ``beat_keywords``/``is_irreversible_beat`` then also drops that name when it LEADS a
    beat — the sentence-initial position ``_beat_tokens`` cannot classify on its own. A capitalized
    leading noun that never recurs proper elsewhere (e.g. "Tower collapses ...") is absent from the
    set and is retained as content. Pure over the beat corpus; no new input needed."""
    proper: set[str] = set()
    for beat in beat_ownership_map(sequence_body):
        for token, is_proper in _beat_tokens(beat):
            if is_proper:
                proper.add(token)
    return frozenset(proper)


def owned_beats_for_scene(scene_no: int, sequence_body: Mapping[str, Any]) -> list[str]:
    """Beats this scene owns (must perform HERE), ordered deterministically."""
    return sorted(beat for beat, owner in beat_ownership_map(sequence_body).items() if owner == scene_no)


def beats_owned_by_later_scenes(scene_no: int, sequence_body: Mapping[str, Any]) -> list[tuple[str, int]]:
    """(beat, owner_scene_no) pairs owned by scenes AFTER scene_no — the beats this scene's
    drafter must NOT perform. Ordered by owner, then beat text."""
    owners = beat_ownership_map(sequence_body)
    return sorted(
        ((beat, owner) for beat, owner in owners.items() if owner > scene_no),
        key=lambda pair: (pair[1], pair[0]),
    )


def _forbidden_duplicate_beats(sequence_body: Mapping[str, Any]) -> list[str]:
    raw = sequence_body.get("forbidden_duplicate_functions")
    return [str(b).strip() for b in raw if str(b).strip()] if isinstance(raw, list) else []


# --------------------------------------------------------------------------------------------
# Detectors


def detect_scene_scope_bleed(
    scene_no: int,
    prose: str,
    sequence_body: Mapping[str, Any],
    proper_nouns: frozenset[str] = frozenset(),
) -> list[dict[str, Any]]:
    """Issues (kind ``scene_scope_bleed``) for every LATER scene's owned beat this scene's prose
    performs. Severity is "block" when the leaked beat is irreversible, "repair" otherwise
    (shared/severity.py vocabulary — deterministic checks may block).

    ``proper_nouns`` (default empty → unchanged behavior) is the corpus-derived name set, forwarded to
    keyword derivation so a beat that LEADS with a name isn't matched on that name."""
    issues: list[dict[str, Any]] = []
    if not prose.strip():
        return issues
    for beat, owner in beats_owned_by_later_scenes(scene_no, sequence_body):
        matched = matched_beat_keywords(beat, prose, proper_nouns)
        keywords = beat_keywords(beat, proper_nouns)
        if not keywords or len(matched) < _required_hits(len(keywords)):
            continue
        irreversible = is_irreversible_beat(beat, proper_nouns)
        issues.append(
            {
                "kind": SCENE_SCOPE_BLEED,
                "scene_no": scene_no,
                "owner_scene_no": owner,
                "beat": beat,
                "irreversible": irreversible,
                "matched_keywords": matched,
                "severity": "block" if irreversible else "repair",
                "detail": (
                    f"Scene {scene_no} performs a beat owned by later scene {owner}: {beat!r} "
                    f"(matched: {', '.join(matched)}). Remove it here; scene {owner} must stage it."
                ),
            }
        )
    return issues


def detect_duplicate_irreversible_beats(
    scene_prose_by_no: Mapping[int, str],
    sequence_body: Mapping[str, Any],
    proper_nouns: frozenset[str] = frozenset(),
) -> list[dict[str, Any]]:
    """Issues (kind ``duplicate_irreversible_beat``) for every irreversible owned beat — or beat
    listed in forbidden_duplicate_functions — that is performed in MORE THAN ONE scene's prose.

    ``proper_nouns`` (default empty → unchanged behavior) is the corpus-derived name set, forwarded to
    irreversibility and keyword derivation so a leading name is not treated as content."""
    owners = beat_ownership_map(sequence_body)
    forced = set(_forbidden_duplicate_beats(sequence_body))
    issues: list[dict[str, Any]] = []
    for beat in sorted(set(owners) | forced, key=lambda b: (owners.get(b, 0), b)):
        if beat not in forced and not is_irreversible_beat(beat, proper_nouns):
            continue
        required = _required_hits(len(beat_keywords(beat, proper_nouns)))
        matched_by_scene = {
            scene_no: matched
            for scene_no, prose in sorted(scene_prose_by_no.items())
            if prose.strip() and len(matched := matched_beat_keywords(beat, prose, proper_nouns)) >= required
        }
        if len(matched_by_scene) < 2:
            continue
        owner = owners.get(beat)
        scene_nos = sorted(matched_by_scene)
        issues.append(
            {
                "kind": DUPLICATE_IRREVERSIBLE_BEAT,
                "scene_nos": scene_nos,
                "owner_scene_no": owner,
                "beat": beat,
                "irreversible": True,
                "matched_keywords_by_scene": matched_by_scene,
                "severity": "block",
                "detail": (
                    f"Irreversible beat performed in scenes {scene_nos}: {beat!r} — it may occur "
                    + (f"only in its owning scene {owner}." if owner else "in at most one scene.")
                ),
            }
        )
    return issues


def evaluate_scene_scope(
    scene_prose_by_no: Mapping[int, str],
    sequence_body: Mapping[str, Any],
    proper_nouns: frozenset[str] = frozenset(),
) -> list[dict[str, Any]]:
    """Both scope checks over an assembled chapter: per-scene bleed (in scene order), then
    chapter-wide duplicate irreversible beats. Pure and deterministic.

    Derives the corpus proper-noun set once via ``_corpus_proper_nouns`` (unless a non-empty set is
    supplied) and threads it into both detectors, so a beat that LEADS with a name is not mistaken for
    content. On a corpus whose beats all lead with directive verbs (the normal case) the derived set
    changes no keyword, so results are identical to the pre-fix behavior."""
    if not proper_nouns:
        proper_nouns = _corpus_proper_nouns(sequence_body)
    issues: list[dict[str, Any]] = []
    for scene_no, prose in sorted(scene_prose_by_no.items()):
        issues.extend(detect_scene_scope_bleed(scene_no, prose, sequence_body, proper_nouns))
    issues.extend(detect_duplicate_irreversible_beats(scene_prose_by_no, sequence_body, proper_nouns))
    return issues
