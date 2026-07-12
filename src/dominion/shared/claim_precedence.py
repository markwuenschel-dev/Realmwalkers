"""Claim Source Precedence (ADR 0029).

`ClaimSource` is an ENFORCED total order, not just a per-claim label:

    LOCKED_CANON > DERIVED_FROM_MANUSCRIPT > DERIVED_FROM_OUTLINE > PLAUSIBLE_INFERENCE > UNRESOLVED

`FORBIDDEN` is NOT a rank — it is an independent surface-term prohibition (packet/surface_policy.py).

A conflict is resolved by the order; a conflict the order cannot break (equal strength, or manuscript
vs locked canon which must never be auto-resolved) becomes a structured open question that BLOCKS
ChapterPacket approval. Manuscript evidence may govern the adopted chapter but never enters canon
retrieval and never overrides locked canon automatically.
"""

from __future__ import annotations

from dominion.shared.enums import ClaimSource

# Strongest first. FORBIDDEN is intentionally absent — it is a prohibition, not a precedence rank.
_PRECEDENCE: tuple[ClaimSource, ...] = (
    ClaimSource.LOCKED_CANON,
    ClaimSource.DERIVED_FROM_MANUSCRIPT,
    ClaimSource.DERIVED_FROM_OUTLINE,
    ClaimSource.PLAUSIBLE_INFERENCE,
    ClaimSource.UNRESOLVED,
)
_RANK: dict[ClaimSource, int] = {src: i for i, src in enumerate(_PRECEDENCE)}


def rank(source: ClaimSource | str) -> int:
    """Precedence index (0 = strongest). Unknown/FORBIDDEN sort last so they never silently win."""
    try:
        return _RANK[ClaimSource(source)]
    except (ValueError, KeyError):
        return len(_PRECEDENCE)


def outranks(a: ClaimSource | str, b: ClaimSource | str) -> bool:
    """True if `a` is strictly stronger than `b` by the precedence order."""
    return rank(a) < rank(b)


def conflict_needs_open_question(a: ClaimSource | str, b: ClaimSource | str) -> bool:
    """Whether a factual conflict between two claims of these sources must become an approval-blocking
    open question rather than being auto-resolved by precedence.

    - manuscript × locked_canon → ALWAYS an open question (the system never picks a side silently),
      even though locked_canon outranks manuscript: an imported prose fact contradicting canon is a
      real editorial decision, not a strength tiebreak.
    - equal strength (e.g. manuscript × manuscript) → open question; same-strength evidence must not
      silently choose a winner.
    - otherwise the higher-precedence source wins outright (e.g. manuscript × outline → manuscript).
    """
    sa, sb = ClaimSource(a), ClaimSource(b)
    pair = {sa, sb}
    if pair == {ClaimSource.LOCKED_CANON, ClaimSource.DERIVED_FROM_MANUSCRIPT}:
        return True
    return rank(sa) == rank(sb)


def conflict_kind(a: ClaimSource | str, b: ClaimSource | str) -> str | None:
    """The open-question kind for a conflict that can't be auto-resolved, or None if precedence decides.

    `manuscript_canon_conflict` is the ADR 0029 named kind for manuscript × locked canon.
    """
    if not conflict_needs_open_question(a, b):
        return None
    if {ClaimSource(a), ClaimSource(b)} == {ClaimSource.LOCKED_CANON, ClaimSource.DERIVED_FROM_MANUSCRIPT}:
        return "manuscript_canon_conflict"
    return "equal_strength_conflict"
