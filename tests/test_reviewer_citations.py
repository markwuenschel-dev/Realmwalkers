"""The unsupported-citation guard: a reviewer that quotes prose the scene does not contain has
fabricated its evidence, and the finding resting on it was never right.

Pure unit tests for the deterministic check itself — no DB, no LLM, no network. The DB-backed proof
that the persistence funnel actually drops such a finding lives in `test_pipeline_reviewers.py`,
next to the harness it needs.

The bar these tests hold: a false REJECTION is worse than a false acceptance. Suppressing a real
finding because a model wrote a curly apostrophe would be a far more expensive failure than letting
one fabricated quote through, so the forgiving-normalization cases below are the load-bearing ones.
"""

from __future__ import annotations

from dominion.shared.enums import Severity
from dominion.workers.reviewers.base import Flag, cited_quote, quote_is_supported

PROSE = (
    "She turned toward the window, and the rain came down hard against the glass.\n\n"
    'Marcus said nothing at all. "It\'s done," he managed, finally — and meant it.'
)


def _flag(payload: dict | None) -> Flag:
    return Flag(reviewer="voice", severity=Severity.INFO, note="n", payload=payload)


# --- cited_quote: what counts as a claim having been made -----------------------------------------


def test_no_payload_is_not_a_citation():
    assert cited_quote(_flag(None)) is None


def test_payload_without_a_quote_key_is_not_a_citation():
    assert cited_quote(_flag({"kind": "infra_rate_limit"})) is None


def test_blank_and_whitespace_quotes_are_not_citations():
    # A blank quote is not a weak claim, it is NO claim — it must not be judged as a false one.
    assert cited_quote(_flag({"quote": ""})) is None
    assert cited_quote(_flag({"quote": "   \n\t "})) is None


def test_non_string_quote_is_not_a_citation():
    assert cited_quote(_flag({"quote": ["she", "turned"]})) is None
    assert cited_quote(_flag({"quote": 42})) is None


def test_a_real_quote_is_a_citation_and_is_stripped():
    assert cited_quote(_flag({"quote": "  She turned  "})) == "She turned"


# --- quote_is_supported: the actual judgement ------------------------------------------------------


def test_verbatim_quote_is_supported():
    assert quote_is_supported("the rain came down hard", PROSE)


def test_quote_absent_from_the_prose_is_not_supported():
    assert not quote_is_supported("Marcus drew his blade and charged", PROSE)


def test_a_plausible_but_fabricated_quote_is_not_supported():
    # The dangerous case: reads like this scene, appears nowhere in it. This is the finding that
    # costs the author a read and resolves to nothing.
    assert not quote_is_supported("She turned toward the door", PROSE)


def test_case_is_ignored():
    assert quote_is_supported("SHE TURNED TOWARD THE WINDOW", PROSE)


def test_collapsed_and_reflowed_whitespace_is_still_supported():
    # Models routinely reflow a quote across lines or double a space. That is not a fabrication.
    assert quote_is_supported("Marcus   said\n  nothing at all", PROSE)


def test_typographic_lookalikes_are_folded():
    # The prose has a straight apostrophe and an em dash; the model quotes back curly and en.
    assert quote_is_supported("“It’s done,” he managed", PROSE)
    assert quote_is_supported("he managed, finally – and meant it", PROSE)


def test_a_quote_elided_with_an_ellipsis_is_supported_when_segments_appear_in_order():
    assert quote_is_supported("She turned … against the glass", PROSE)
    assert quote_is_supported("She turned ... against the glass", PROSE)


def test_ellipsis_segments_out_of_order_are_not_supported():
    # Order matters: reversed segments are not a quotation of this passage.
    assert not quote_is_supported("against the glass … She turned", PROSE)


def test_ellipsis_with_a_fabricated_segment_is_not_supported():
    assert not quote_is_supported("She turned … a dragon landed", PROSE)


def test_empty_quote_is_treated_as_supported():
    # Defence in depth: `cited_quote` already screens these out, so if one reaches the check it must
    # not be reported as a fabrication.
    assert quote_is_supported("", PROSE)
    assert quote_is_supported("   ", PROSE)


def test_the_check_is_deterministic():
    for _ in range(3):
        assert quote_is_supported("the rain came down hard", PROSE)
        assert not quote_is_supported("Marcus drew his blade", PROSE)


def test_empty_prose_rejects_any_real_citation():
    assert not quote_is_supported("anything at all", "")
    assert quote_is_supported("", "")
