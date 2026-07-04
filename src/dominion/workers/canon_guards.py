"""Deterministic canon-contract guards: scan assembled/drafted prose against the chapter packet's
OWN prohibition fields and emit ``canon_contract_leak`` issues when a prohibited concept appears
on-page.

Why this exists (Ch1 bad run, run 51d635ec): the chapter contract's ruling said "No Eyes
notification in Chapter 1 … no Neurochromatic Eyes, no Meszkhal item signal", yet the assembled
draft opened the duel with "Neurochromatic Eyes flickered at the edge of his perception" and ZERO
of the run's 24 issues touched it. The prohibition lived only in free text (a resolved ruling in
``open_questions``) while ``canon_locks`` / ``surface_terms`` listed "Neurochromatic Eyes" as the
ALLOWED canonical name — so every allowlist-shaped check saw the term as fine and no layer ever
scanned prose against the prohibition. General canon locks state background TRUTH ("Marcus uses
Neurochromatic Eyes; do not rename it"); a chapter-scoped ruling states on-page PROHIBITION ("it
must not appear in this chapter"). This module keeps those apart: locks and allowlists never
create prohibitions, and a ruling-derived prohibition is never masked by an allowlist.

Prohibited terms are DERIVED from the packet — nothing is hardcoded here. Three tiers:

- ``resolved_ruling`` (severity ``block``): explicit negations in resolved author rulings
  (``open_questions.resolved[*].resolution``) — "no <Capitalized Phrase>" / "not <Capitalized
  Phrase>" / "no [ BRACKET TOKEN ]". Character names are dropped (a present character's name being
  in a ruling like "No Serra interiority" does not ban the name on-page); allowlists are NOT
  consulted — a chapter ruling overrides general naming policy.
- ``forbidden_surface_term`` (``block``/``repair``): explicit ``forbidden_surface_terms`` from
  ``surface_terms`` / ``entity_bindings``, plus short literal ``characters_forbidden`` entries.
  Entries time-scoped to an IN-chapter event ("until visual identity is revealed mid-duel") are
  skipped — a whole-prose scan cannot know position; entries deferred BEYOND this chapter ("after
  chapter 1 …", "deferred beyond chapter 1") stay prohibited for the whole chapter.
- ``prohibition_field`` (``warn``, advisory): non-possessive proper-noun phrases extracted from
  the abstract prohibition sentences (``forbidden_ui_concepts`` / ``forbidden_knowledge`` /
  ``forbidden_reveals`` / ``must_remain_hidden`` and sentence-like ``characters_forbidden``
  entries), filtered against the packet's allowed names/concepts. Heuristic, so never gating.

Derivation reads only the AUTHORITATIVE body fields — derived ``_``-prefixed sections
(``_surface_contract``) are ignored, same rule as the QA prompt builders (they carry mechanically
truncated duplicates like "Any on" that would poison a literal scan).

Pure module by design: stdlib + pure ``dominion.shared`` helpers only — no DB, no LLM, no I/O —
so the chapter QA path, scene QA prompt builder, and the lane-10 regression harness can all
import it freely.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from dominion.shared.severity import issue_gates
from dominion.shared.text_match import as_str_list, collect_strings

ISSUE_KIND = "canon_contract_leak"

_SEVERITY_RANK = {"warn": 0, "repair": 1, "block": 2}

# Sentence-leading noise words a capitalized-phrase extractor must not turn into scan terms.
_LEADING_STOPWORDS = frozenset({"the", "any", "a", "an", "no", "not", "do", "if", "on", "in", "or", "and", "of"})
# Single-word candidates that are structural vocabulary, never a leakable concept name.
_TERM_STOPWORDS = frozenset({"ui", "chapter", "chapters", "book", "book-1", "book-2", "ruling", "ch"})

# "no/Not <Capitalized Phrase>" inside a resolved ruling — the explicit on-page prohibition form.
_NEGATED_CAP_RE = re.compile(r"\b[Nn]ot?\b\s+((?:[A-Z][\w'’\-]*)(?:\s+[A-Z][\w'’\-]*)*)")
# "no [ INTERFACE ]" — a negated bracketed system/UI token.
_NEGATED_BRACKET_RE = re.compile(r"\b[Nn]ot?\b\s+(\[[^\]\n]{1,60}\])")
# Any run of capitalized words (proper-noun phrase) for the abstract prohibition-sentence tier.
_CAP_PHRASE_RE = re.compile(r"(?:[A-Z][\w'’\-]*)(?:\s+[A-Z][\w'’\-]*)*")
_NAME_TOKEN_RE = re.compile(r"[\w'’\-]+")
# `until` texts that defer a reveal BEYOND this chapter (still prohibited for the whole chapter),
# as opposed to an in-chapter timed reveal (not scannable without position awareness).
_BEYOND_CHAPTER_RE = re.compile(r"(?:\bafter\b|\bbeyond\b|\blater\b).{0,40}\bchapter\b|\bdeferred\b", re.IGNORECASE)

# How many characters of surrounding prose a finding's excerpt carries on each side of the match.
_EXCERPT_RADIUS = 70


@dataclass(frozen=True)
class ProhibitedTerm:
    """One on-page-prohibited surface form, traceable to the contract field that prohibits it."""

    term: str
    source: str  # "resolved_ruling" | "forbidden_surface_term" | "prohibition_field"
    contract_reference: str
    severity: str  # "block" | "repair" | "warn"
    case_sensitive: bool = False


def _normalize(text: str) -> str:
    """Typographic quotes -> ASCII so contract text and prose match regardless of quote style.
    1:1 character translation — offsets into the normalized string are valid in the original."""
    return text.translate({0x2019: "'", 0x2018: "'", 0x201C: '"', 0x201D: '"'})


def _clean_word(word: str) -> str:
    return word.strip(".,;:!?\"'")


def _phrase_words(phrase: str) -> list[str]:
    """Usable words of an extracted capitalized phrase: leading stopwords dropped, and the phrase
    cut at the first possessive — "Marcus's true name" prohibits the NAME, not "Marcus"."""
    out: list[str] = []
    for raw in _normalize(phrase).split():
        word = _clean_word(raw)
        if not word:
            continue
        if not out and word.lower() in _LEADING_STOPWORDS:
            continue
        if word.lower().endswith("'s"):
            break
        out.append(word)
    return out


def _allowed_name_tokens(body: dict[str, Any]) -> set[str]:
    """Lower-cased names (and their word tokens) legitimately on-page or mentionable per the
    packet: characters_present / characters_mentioned_only leading names plus the POV name.
    Deliberately NOT ``cast`` — on real packets it is a union dump that includes the forbidden
    aliases themselves."""
    out: set[str] = set()
    for field in ("characters_present", "characters_mentioned_only"):
        for entry in as_str_list(body.get(field)):
            lead = _normalize(entry).split("(", 1)[0].strip()
            if not lead:
                continue
            out.add(lead.lower())
            for token in _NAME_TOKEN_RE.findall(lead):
                if len(token) >= 2:
                    out.add(token.lower())
    pov = str(body.get("pov") or "").strip()
    if pov:
        out.add(pov.lower())
    return out


def _allowed_concept_strings(body: dict[str, Any]) -> set[str]:
    """Lower-cased allowlisted surface strings (allowed_ui_concepts + every allowed_surface_terms
    entry). Consulted ONLY by the heuristic prohibition_field tier — a resolved ruling's
    prohibition must never be masked by a general allowlist (that masking is exactly how the Ch1
    Eyes leak survived)."""
    out = {c.lower() for c in as_str_list(body.get("allowed_ui_concepts"))}
    for entry in body.get("surface_terms") or []:
        if isinstance(entry, dict):
            out.update(t.lower() for t in as_str_list(entry.get("allowed_surface_terms")))
    return out


def _ruling_terms(open_questions: dict[str, Any] | None) -> list[tuple[str, str]]:
    """(term, contract_reference) pairs from explicit negations in resolved rulings."""
    found: list[tuple[str, str]] = []
    resolved = (open_questions or {}).get("resolved")
    if not isinstance(resolved, list):
        return found
    for index, entry in enumerate(resolved):
        if not isinstance(entry, dict):
            continue
        resolution = str(entry.get("resolution") or "")
        question = str(entry.get("q") or "").strip()
        reference = f"open_questions.resolved[{index}]" + (f" — Q: {question[:90]}" if question else "")
        for match in _NEGATED_BRACKET_RE.finditer(resolution):
            inner = _normalize(match.group(1))[1:-1].strip()
            if inner:
                found.append((f"[{inner}]", reference))
        for match in _NEGATED_CAP_RE.finditer(resolution):
            words = _phrase_words(match.group(1))
            if words:
                found.append((" ".join(words), reference))
    return found


def _surface_term_entries(body: dict[str, Any]) -> list[tuple[str, str, str]]:
    """(term, contract_reference, severity) from surface_terms / entity_bindings /
    short-literal characters_forbidden — the contract's explicit forbidden surface forms."""
    found: list[tuple[str, str, str]] = []
    for index, entry in enumerate(body.get("surface_terms") or []):
        if not isinstance(entry, dict):
            continue
        until = str(entry.get("until") or "").strip()
        policy = str(entry.get("policy") or "").strip().lower()
        if until and not _BEYOND_CHAPTER_RE.search(until):
            continue  # in-chapter timed reveal — a whole-prose scan cannot adjudicate position
        severity = "block" if policy == "block" or (until and _BEYOND_CHAPTER_RE.search(until)) else "repair"
        label = str(entry.get("surface_label") or entry.get("canonical_term") or "").strip()
        reference = f"surface_terms[{index}]" + (f" ({label})" if label else "")
        for term in as_str_list(entry.get("forbidden_surface_terms")):
            found.append((_normalize(term), reference, severity))
    for index, entry in enumerate(body.get("entity_bindings") or []):
        if not isinstance(entry, dict):
            continue
        label = str(entry.get("surface_label") or "").strip()
        reference = f"entity_bindings[{index}]" + (f" ({label})" if label else "")
        for term in as_str_list(entry.get("forbidden_surface_terms")):
            found.append((_normalize(term), reference, "repair"))
    for index, entry in enumerate(as_str_list(body.get("characters_forbidden"))):
        if len(entry.split()) <= 4:  # short literal name/alias; sentence-like entries go to tier 3
            found.append((_normalize(entry).strip(".,;:!?"), f"characters_forbidden[{index}]", "repair"))
    return found


def _prohibition_field_terms(body: dict[str, Any]) -> list[tuple[str, str]]:
    """(term, contract_reference) proper-noun phrases extracted from abstract prohibition
    sentences. Heuristic tier — callers treat these as advisory (severity ``warn``)."""
    found: list[tuple[str, str]] = []
    sources: list[tuple[str, str]] = []
    for field in ("forbidden_ui_concepts", "forbidden_knowledge", "forbidden_reveals", "must_remain_hidden"):
        for index, sentence in enumerate(collect_strings(body.get(field))):
            sources.append((sentence, f"{field}[{index}]"))
    for index, entry in enumerate(as_str_list(body.get("characters_forbidden"))):
        if len(entry.split()) > 4:
            sources.append((entry, f"characters_forbidden[{index}]"))
    for sentence, reference in sources:
        for match in _CAP_PHRASE_RE.finditer(sentence):
            words = _phrase_words(match.group(0))
            if words:
                found.append((" ".join(words), reference))
    return found


def derive_prohibited_terms(
    packet_body: dict[str, Any] | None,
    open_questions: dict[str, Any] | None = None,
) -> list[ProhibitedTerm]:
    """Every on-page-prohibited term the chapter contract itself declares, deduped (highest
    severity wins), longest term first. Single-word terms from the heuristic extraction tiers
    match case-sensitively (a ruling's "no Eyes" must not flag "his eyes narrowed"); explicit
    contract terms and multi-word phrases match case-insensitively."""
    body = packet_body if isinstance(packet_body, dict) else {}
    allowed_names = _allowed_name_tokens(body)
    allowed_concepts = _allowed_concept_strings(body)

    def name_allowed(term: str) -> bool:
        tokens = [w.lower() for w in term.split()]
        return term.lower() in allowed_names or all(t in allowed_names for t in tokens)

    def usable(term: str) -> bool:
        return bool(term) and not (len(term.split()) == 1 and term.lower() in _TERM_STOPWORDS)

    candidates: list[ProhibitedTerm] = []
    for term, reference in _ruling_terms(open_questions):
        # Rulings override allowlists, but a character's own name never becomes a banned term.
        if usable(term) and not (not term.startswith("[") and name_allowed(term)):
            case_sensitive = not term.startswith("[") and len(term.split()) == 1
            candidates.append(ProhibitedTerm(term, "resolved_ruling", reference, "block", case_sensitive))
    for term, reference, severity in _surface_term_entries(body):
        if term.strip():
            candidates.append(ProhibitedTerm(term.strip(), "forbidden_surface_term", reference, severity, False))
    for term, reference in _prohibition_field_terms(body):
        if usable(term) and not name_allowed(term) and term.lower() not in allowed_concepts:
            candidates.append(ProhibitedTerm(term, "prohibition_field", reference, "warn", len(term.split()) == 1))

    merged: dict[str, ProhibitedTerm] = {}
    for candidate in candidates:
        key = candidate.term.lower()
        existing = merged.get(key)
        if existing is None or _SEVERITY_RANK[candidate.severity] > _SEVERITY_RANK[existing.severity]:
            merged[key] = candidate
    return sorted(merged.values(), key=lambda t: (-len(t.term), t.term.lower()))


def _term_regex(term: ProhibitedTerm) -> re.Pattern[str]:
    text = _normalize(term.term)
    if text.startswith("[") and text.endswith("]"):
        inner_words = text[1:-1].split()
        pattern = r"\[\s*" + r"\s+".join(re.escape(w) for w in inner_words) + r"\s*\]"
        return re.compile(pattern, re.IGNORECASE)
    pattern = r"\b" + r"[\s\-]+".join(re.escape(w) for w in text.split()) + r"\b"
    return re.compile(pattern, 0 if term.case_sensitive else re.IGNORECASE)


def scan_prose_for_leaks(prose: str, terms: list[ProhibitedTerm]) -> list[dict[str, Any]]:
    """Scan prose for the given prohibited terms -> ``canon_contract_leak`` findings, one per
    matched term. A match wholly inside a longer term's match is subsumed ("Eyes" inside
    "Neurochromatic Eyes" is one leak, not two). Deterministic; pure."""
    if not prose or not prose.strip() or not terms:
        return []
    haystack = _normalize(prose)
    matches: list[tuple[int, int, ProhibitedTerm]] = []
    for term in terms:
        for match in _term_regex(term).finditer(haystack):
            matches.append((match.start(), match.end(), term))
    kept: list[tuple[int, int, ProhibitedTerm]] = [
        (start, end, term)
        for start, end, term in matches
        if not any(
            (o_start <= start and end <= o_end and (o_end - o_start) > (end - start))
            for o_start, o_end, _o_term in matches
        )
    ]

    findings: list[dict[str, Any]] = []
    for term in terms:
        spans = [(start, end) for start, end, t in kept if t is term]
        if not spans:
            continue
        start, end = spans[0]
        excerpt = " ".join(prose[max(0, start - _EXCERPT_RADIUS) : end + _EXCERPT_RADIUS].split())
        findings.append(
            {
                "kind": ISSUE_KIND,
                "term": term.term,
                "source": term.source,
                "contract_reference": term.contract_reference,
                "match_count": len(spans),
                "excerpt": excerpt,
                "detail": (
                    f"Prohibited on-page term {term.term!r} appears in the prose "
                    f"({len(spans)}x) but the chapter contract forbids it on-page "
                    f"({term.source}: {term.contract_reference})."
                ),
                "severity": term.severity,
                **issue_gates(term.severity),
            }
        )
    findings.sort(key=lambda f: (-_SEVERITY_RANK[f["severity"]], str(f["term"]).lower()))
    return findings


def scan_packet_prose(
    prose: str,
    packet_body: dict[str, Any] | None,
    open_questions: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Convenience: derive the prohibition list from the packet, then scan the prose."""
    return scan_prose_for_leaks(prose, derive_prohibited_terms(packet_body, open_questions))


def format_prohibited_terms_block(
    packet_body: dict[str, Any] | None,
    open_questions: dict[str, Any] | None = None,
) -> str | None:
    """Prompt block naming the chapter's hard on-page-prohibited terms explicitly, so scene-level
    author/QA calls carry the prohibition as a scannable list instead of buried ruling free text.
    Advisory (warn-tier) extractions are excluded — the block states only hard contract facts."""
    terms = [t for t in derive_prohibited_terms(packet_body, open_questions) if t.severity in ("block", "repair")]
    if not terms:
        return None
    lines = [f"- {t.term} [{t.severity}] — {t.contract_reference}" for t in terms]
    return (
        "ON-PAGE PROHIBITED TERMS (deterministically derived from this chapter's contract; any of "
        "these appearing in drafted prose or an on-page packet field is a canon_contract_leak, even "
        "if canon locks or naming allowlists mention the same term as the correct NAME — a lock "
        "states background truth, not permission to put the concept on the page this chapter):\n" + "\n".join(lines)
    )
