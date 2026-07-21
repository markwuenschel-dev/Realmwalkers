"""First unit tests for the claim-source precedence policy (ADR 0029, shared/claim_precedence.py).

Pure — no DB / LLM / network. The module was an inert engine with no importers until Lane A2 integrated
it as the asserted-fact precedence policy for import-adoption authoring; these pin its four public
functions (`rank`, `outranks`, `conflict_needs_open_question`, `conflict_kind`) so the enforced order and
the conflict-routing rules can't drift.
"""

from __future__ import annotations

from dominion.shared import claim_precedence
from dominion.shared.enums import ClaimSource

# The enforced order, strongest first (ADR 0029). FORBIDDEN is deliberately absent — a prohibition, not a rank.
_ORDER = [
    ClaimSource.LOCKED_CANON,
    ClaimSource.DERIVED_FROM_MANUSCRIPT,
    ClaimSource.DERIVED_FROM_OUTLINE,
    ClaimSource.PLAUSIBLE_INFERENCE,
    ClaimSource.UNRESOLVED,
]


# --- rank -----------------------------------------------------------------------------------------


def test_rank_is_strictly_increasing_down_the_order():
    ranks = [claim_precedence.rank(s) for s in _ORDER]
    assert ranks == sorted(ranks)
    assert len(set(ranks)) == len(ranks)  # every rung distinct
    assert ranks[0] == 0  # LOCKED_CANON is strongest (index 0)


def test_rank_accepts_str_and_enum_alike():
    assert claim_precedence.rank("locked_canon") == claim_precedence.rank(ClaimSource.LOCKED_CANON)
    assert claim_precedence.rank("derived_from_manuscript") == claim_precedence.rank(
        ClaimSource.DERIVED_FROM_MANUSCRIPT
    )


def test_forbidden_and_unknown_sort_last_so_they_never_silently_win():
    last = len(_ORDER)
    assert claim_precedence.rank(ClaimSource.FORBIDDEN) == last
    assert claim_precedence.rank("not_a_source") == last
    # A ranked source is always strictly stronger than FORBIDDEN / unknown.
    for source in _ORDER:
        assert claim_precedence.rank(source) < claim_precedence.rank(ClaimSource.FORBIDDEN)


# --- outranks -------------------------------------------------------------------------------------


def test_locked_canon_outranks_everything_below_it():
    for weaker in _ORDER[1:]:
        assert claim_precedence.outranks(ClaimSource.LOCKED_CANON, weaker)
        assert not claim_precedence.outranks(weaker, ClaimSource.LOCKED_CANON)


def test_manuscript_outranks_outline_but_not_canon():
    # The durable, surprising rule: the actual prose ranks BELOW locked canon yet ABOVE an outline guess.
    assert claim_precedence.outranks(ClaimSource.DERIVED_FROM_MANUSCRIPT, ClaimSource.DERIVED_FROM_OUTLINE)
    assert not claim_precedence.outranks(ClaimSource.DERIVED_FROM_MANUSCRIPT, ClaimSource.LOCKED_CANON)


def test_outranks_is_irreflexive():
    for source in _ORDER:
        assert not claim_precedence.outranks(source, source)


# --- conflict_needs_open_question -----------------------------------------------------------------


def test_manuscript_vs_locked_canon_always_needs_an_open_question():
    # Even though locked canon outranks manuscript, the pair is never auto-resolved — an imported prose
    # fact contradicting canon is a real editorial decision, in BOTH argument orders.
    assert claim_precedence.conflict_needs_open_question(ClaimSource.LOCKED_CANON, ClaimSource.DERIVED_FROM_MANUSCRIPT)
    assert claim_precedence.conflict_needs_open_question(ClaimSource.DERIVED_FROM_MANUSCRIPT, ClaimSource.LOCKED_CANON)


def test_equal_strength_conflict_needs_an_open_question():
    for source in _ORDER:
        assert claim_precedence.conflict_needs_open_question(source, source)


def test_precedence_breakable_conflicts_do_not_need_an_open_question():
    # Manuscript > outline > inference: the order picks a winner, no human needed.
    assert not claim_precedence.conflict_needs_open_question(
        ClaimSource.DERIVED_FROM_MANUSCRIPT, ClaimSource.DERIVED_FROM_OUTLINE
    )
    assert not claim_precedence.conflict_needs_open_question(
        ClaimSource.DERIVED_FROM_OUTLINE, ClaimSource.PLAUSIBLE_INFERENCE
    )
    assert not claim_precedence.conflict_needs_open_question(ClaimSource.LOCKED_CANON, ClaimSource.DERIVED_FROM_OUTLINE)


# --- conflict_kind --------------------------------------------------------------------------------


def test_conflict_kind_names_the_manuscript_canon_case():
    assert (
        claim_precedence.conflict_kind(ClaimSource.LOCKED_CANON, ClaimSource.DERIVED_FROM_MANUSCRIPT)
        == "manuscript_canon_conflict"
    )


def test_conflict_kind_names_the_equal_strength_case():
    assert (
        claim_precedence.conflict_kind(ClaimSource.DERIVED_FROM_MANUSCRIPT, ClaimSource.DERIVED_FROM_MANUSCRIPT)
        == "equal_strength_conflict"
    )


def test_conflict_kind_is_none_when_precedence_decides():
    assert claim_precedence.conflict_kind(ClaimSource.DERIVED_FROM_MANUSCRIPT, ClaimSource.DERIVED_FROM_OUTLINE) is None
    assert claim_precedence.conflict_kind(ClaimSource.LOCKED_CANON, ClaimSource.UNRESOLVED) is None
