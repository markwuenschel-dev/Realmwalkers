"""Load the named drift patterns from disk and scope them to the scene actually being drafted.

`forbidden_drift.md` is the project's diagnostic ontology — twenty-four named ways the prose can leave
its lane, each tagged with one or two family tags. This module turns that document into a drafting
constraint instead of a thing a human reads afterwards.

WARNING SIGNS ARE DIAGNOSTIC. CORRECTIONS ARE GENERATIVE. That distinction is the whole design. A
warning sign ("neighboring sentences competing to be quotable") tells a reader how to *recognize* a
failure in prose that already exists. It does not tell a writer what to do instead, and handing it to a
drafter mostly teaches the model what the failure looks like. The **Correction** line is the half that
can actually be followed while generating. So `DRAFT` mode carries names plus corrections, and `AUDIT`
mode carries the full entries including warning signs, for a reviewer looking at finished prose.

The sizing is not incidental. Measured on the real Book 1 run, drafting input averages ~5,440 tokens per
call. Injecting all twenty-four entries whole is ~6,350 tokens — it would **more than double** the input
cost of every draft, on a pipeline where contract derivation is already about three-quarters of spend.
The corrections for all twenty-four total ~1,590 tokens, and scoping typically cuts that further.

THE SELECTION RULE, in two halves:

1. **Four families always load** — GENRE, VOICE, PROSE, STRUCTURE. Craft and tonal walls apply to a
   landing scene and a battle equally. There is no scene where "the prose keeps thinking after the
   meaningful thinking is done" stops being a risk.

2. **Three families are conditional** — CHOREOGRAPHY when bodies are doing something, RELATIONSHIP and
   CANON when someone other than the POV is on the page. A scene of Marcus alone on a hillside cannot
   violate Brent's death shape or the Eriadne interval, and telling the drafter to avoid them is noise —
   which is how a model learns to skim the whole block.

   Note the POV is deliberately excluded from the cast test. Marcus is present in essentially every
   scene, so gating on "any cast member present" would make CANON unconditional and quietly defeat the
   scoping.

Plus a narrowing pass: a pattern whose TITLE names characters loads only when one of them is on the
page. Titles rather than bodies, deliberately — nearly every entry mentions Marcus somewhere in its
prose, so body-matching would keep everything.

FAIL SOFT, ALWAYS. A missing or unparseable file warns once and returns None. Drift guidance improves
prose; its absence must never fail a draft. Same contract `dialogue_rules.py` holds.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

from dominion.shared.config import settings

_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_warned = False

DRAFT = "draft"
AUDIT = "audit"

#: Applies to every scene. Craft and tonal walls do not switch off.
ALWAYS_ON = frozenset({"GENRE", "VOICE", "PROSE", "STRUCTURE"})

#: Loaded only when the scene contains what they govern.
CONDITIONAL = frozenset({"CHOREOGRAPHY", "RELATIONSHIP", "CANON"})

#: Beat tags / text meaning bodies are doing something. Deliberately broad: a false positive costs a
#: few hundred tokens of relevant guidance; a false negative costs the Deleted Middle, which is the
#: most expensive prose failure to repair after the fact.
_PHYSICAL = re.compile(
    r"\b(combat|fight|fought|fighting|battle|duel|spar|chase|flee|fled|climb|fall|fell|"
    r"wound|wounded|injur|blood|blade|sword|strike|struck|dodge|parry|grapple|"
    r"physical|action|escape|pursuit|weapon|armou?r|impact|collide)\w*",
    re.IGNORECASE,
)

#: Cast whose names in a pattern TITLE gate that pattern on their presence.
_CAST = ("marcus", "serra", "brent", "mathias", "mara", "sebastian", "illyri")

_BLOCK_RE = re.compile(
    r"^### (?P<num>\d+)\. (?P<title>[^\n·]+?)\s*·\s*`(?P<tags>\[[^`]+\])`\n(?P<body>.*?)(?=^### |^## |\Z)",
    re.MULTILINE | re.DOTALL,
)
_TAG_RE = re.compile(r"\[([A-Z]+)\]")
_CORRECTION_RE = re.compile(r"^\*\*Correction:\*\*\s*(?P<text>.+?)(?=\n\n|\Z)", re.MULTILINE | re.DOTALL)


def _families_for_scene(*, present: set[str], pov: str, signals: str) -> set[str]:
    """Which families this scene can actually violate."""
    families = set(ALWAYS_ON)
    if _PHYSICAL.search(signals):
        families.add("CHOREOGRAPHY")
    others = present - {pov.strip().lower()}
    if others:
        families.add("RELATIONSHIP")
    # Canon arcs are locked to the named cast. Excluding the POV is load-bearing — see the module
    # docstring: gating on "any cast present" makes this unconditional.
    if others & set(_CAST):
        families.add("CANON")
    return families


def _entry(match: re.Match[str], mode: str) -> str:
    num, title, body = match.group("num"), match.group("title").strip(), match.group("body")
    if mode == AUDIT:
        return match.group(0).rstrip()
    corr = _CORRECTION_RE.search(body)
    if corr is None:
        # No correction to give — in DRAFT mode a name alone teaches nothing, so skip it rather than
        # spend tokens naming a failure the drafter cannot act on.
        return ""
    text = " ".join(corr.group("text").split())
    return f"{num}. **{title}** — {text}"


def scope_forbidden_drift(text: str, *, pov: str, present: Iterable[str], signals: str = "", mode: str = DRAFT) -> str:
    """Return the patterns that apply to this scene, in file order."""
    present_l = {p.strip().lower() for p in present if p and p.strip()}
    present_l.add(pov.strip().lower())
    wanted = _families_for_scene(present=present_l, pov=pov, signals=signals)

    kept: list[str] = []
    for m in _BLOCK_RE.finditer(text):
        if not set(_TAG_RE.findall(m.group("tags"))) & wanted:
            continue
        named = {c for c in _CAST if c in m.group("title").lower()}
        if named and not (named & present_l):
            continue
        rendered = _entry(m, mode)
        if rendered:
            kept.append(rendered)

    if not kept:
        return ""
    joiner = "\n\n---\n\n" if mode == AUDIT else "\n"
    head = (
        "Named ways this story's prose has failed before, scoped to what this scene can violate "
        f"({', '.join(sorted(wanted))}). Patterns governing absent characters or absent situations "
        "are omitted deliberately.\n\n"
        "Audit RECURRENCE, not occurrence — none of these is forbidden once; their visibility is the "
        "failure.\n\n"
    )
    return head + joiner.join(kept)


def load_forbidden_drift(*, pov: str, present: Iterable[str], signals: str = "", mode: str = DRAFT) -> str | None:
    """Read the drift patterns fresh for each draft and scope them to the scene. None when absent."""
    global _warned
    configured = Path(settings.forbidden_drift_path)
    candidates = [configured] if configured.is_absolute() else [_PROJECT_ROOT / configured, Path.cwd() / configured]
    for path in candidates:
        try:
            raw = path.read_text(encoding="utf-8")
        except (FileNotFoundError, NotADirectoryError):
            continue
        return scope_forbidden_drift(raw, pov=pov, present=present, signals=signals, mode=mode) or None
    if not _warned:
        print(
            f"[context] drift patterns not found at {settings.forbidden_drift_path!r}; drafts will run without them",
            flush=True,
        )
        _warned = True
    return None
