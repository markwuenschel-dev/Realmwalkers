"""Unit tests for the ImportSceneEvidence extraction seam (ADR 0028) — pure, no DB/LLM/network.

Covers the three deterministic helpers that both the real adapter and the fake share
(`validate_ledger` structural + span validation, `_deterministic_chunks` oversized-scene split,
`_merge_chunk_ledgers` whole-scene union), plus the scripted `FakeImportEvidenceExtractor` that CI
uses to prove retry/resume and chunk bookkeeping without a provider. The LLM adapter and any adoption
caller are out of scope (no caller is wired yet).
"""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from dominion.shared.config import Settings, settings
from dominion.workers.budget import BudgetExceeded, TokenBudget, Usage
from dominion.workers.import_evidence import (
    EVIDENCE_SCHEMA_VERSION,
    LEDGER_SECTIONS,
    SPAN_QUARANTINE_KEY,
    EvidenceExtractionError,
    ExtractionBudget,
    FakeImportEvidenceExtractor,
    LlmImportEvidenceExtractor,
    SceneSource,
    ValidatedEvidence,
    _deterministic_chunks,
    _merge_chunk_ledgers,
    assert_scene_within_ceiling,
    default_work_token_budget,
    quarantined_span_count,
    validate_ledger,
)
from dominion.workers.llm import estimate_tokens

_SCALAR_SECTIONS = ("pov", "setting", "entry_state", "exit_state")
_LIST_SECTIONS = tuple(s for s in LEDGER_SECTIONS if s not in _SCALAR_SECTIONS)


def _source(scene_no: int, prose: str, *, scene_id: uuid.UUID | None = None) -> SceneSource:
    return SceneSource(
        scene_id=scene_id or uuid.uuid4(),
        scene_version=1,
        prose_hash="0" * 64,
        chapter_id=uuid.uuid4(),
        scene_no=scene_no,
        prose=prose,
    )


# --- validate_ledger: section-fill, scalar coercion, span bounds ----------------------------------


def test_validate_ledger_fills_missing_sections():
    # An empty ledger is normalized to the full flat contract: scalar keys -> None, list keys -> [].
    out = validate_ledger({}, prose_len=100)
    assert set(out.keys()) == set(LEDGER_SECTIONS)
    assert all(out[s] is None for s in _SCALAR_SECTIONS)
    assert all(out[s] == [] for s in _LIST_SECTIONS)


def test_validate_ledger_coerces_scalar_section_to_str():
    # A non-str, non-None scalar is stringified; a real string is left as-is; missing stays None.
    out = validate_ledger({"pov": 5, "setting": "The docks"}, prose_len=10)
    assert out["pov"] == "5"
    assert out["setting"] == "The docks"
    assert out["entry_state"] is None


def test_validate_ledger_wraps_nonlist_list_section():
    # A bare dict handed to a list section is wrapped into a single-item list (never dropped).
    out = validate_ledger({"events": {"summary": "x"}}, prose_len=10)
    assert out["events"] == [{"summary": "x"}]


def test_validate_ledger_accepts_in_range_and_none_spans():
    # A boundary span [0, prose_len] is valid, and an explicit span of None is skipped, not rejected.
    out = validate_ledger(
        {"events": [{"span": [0, 10]}, {"span": None, "note": "anchorless"}]},
        prose_len=10,
    )
    assert out["events"] == [{"span": [0, 10]}, {"span": None, "note": "anchorless"}]


def test_validate_ledger_raises_on_out_of_range_spans():
    bad_spans = [
        [0, 20],  # end past prose_len
        [-1, 5],  # negative start
        [7, 3],  # start > end
        [5],  # too short
        [0, 5, 9],  # too long
        [0, "5"],  # non-int member
        "nope",  # not a list/tuple
        5,  # scalar, not a sequence
    ]
    for span in bad_spans:
        with pytest.raises(EvidenceExtractionError):
            validate_ledger({"events": [{"span": span}]}, prose_len=10)


def test_validate_ledger_rejects_non_dict():
    with pytest.raises(EvidenceExtractionError):
        validate_ledger([], prose_len=10)  # type: ignore[arg-type]


# --- _deterministic_chunks: passthrough + boundary cuts + total coverage --------------------------


def test_deterministic_chunks_single_chunk_when_within_max():
    assert _deterministic_chunks("hello", 100) == [(0, "hello")]
    assert _deterministic_chunks("abcde", 5) == [(0, "abcde")]  # exactly at the limit is one chunk


def test_deterministic_chunks_cuts_at_paragraph_boundary():
    # A "\n\n" inside the [start+max//2, end) window is preferred; the cut keeps the separator.
    prose = "abcde\n\nfghijABCDE"  # len 17
    assert _deterministic_chunks(prose, 10) == [(0, "abcde\n\n"), (7, "fghijABCDE")]


def test_deterministic_chunks_cuts_at_line_boundary():
    # No paragraph break in-window, so it falls back to a single "\n".
    prose = "abcdef\nghijklmnop"  # len 17
    assert _deterministic_chunks(prose, 10) == [(0, "abcdef\n"), (7, "ghijklmnop")]


def test_deterministic_chunks_cuts_at_space_boundary():
    # No newlines in-window, so it falls back to a space.
    prose = "abcdef ghijklmnop"  # len 17
    assert _deterministic_chunks(prose, 10) == [(0, "abcdef "), (7, "ghijklmnop")]


def test_deterministic_chunks_hard_cut_when_no_separator():
    # A run with no separators is cut at exactly max_chars, no truncation, no overlap.
    assert _deterministic_chunks("a" * 25, 10) == [
        (0, "a" * 10),
        (10, "a" * 10),
        (20, "a" * 5),
    ]


def test_deterministic_chunks_full_coverage_no_overlap():
    # Every char lands in exactly one chunk; offsets are contiguous and text matches the slice.
    prose = ("Para one has words here.\n\n" * 40) + "tail\ntail2 tail3 " * 30
    chunks = _deterministic_chunks(prose, 50)
    assert len(chunks) > 1
    assert "".join(text for _off, text in chunks) == prose
    expected_offset = 0
    for offset, text in chunks:
        assert offset == expected_offset
        assert prose[offset : offset + len(text)] == text
        expected_offset += len(text)
    assert expected_offset == len(prose)


# --- _merge_chunk_ledgers: span shift, entry/exit picks, first-non-empty scalars ------------------


def test_merge_shifts_spans_into_scene_coords():
    chunk_ledgers = [
        (0, {"events": [{"summary": "a", "span": [0, 5]}]}),
        (10, {"events": [{"summary": "b", "span": [2, 7]}]}),
    ]
    merged = _merge_chunk_ledgers(chunk_ledgers, prose_len=20)
    assert merged["events"] == [
        {"summary": "a", "span": [0, 5]},
        {"summary": "b", "span": [12, 17]},  # shifted by the chunk offset of 10
    ]


def test_merge_entry_from_first_exit_from_last():
    chunk_ledgers = [
        (0, {"entry_state": "calm", "exit_state": "midA"}),
        (8, {"entry_state": "midB", "exit_state": "resolved"}),
    ]
    merged = _merge_chunk_ledgers(chunk_ledgers, prose_len=20)
    assert merged["entry_state"] == "calm"  # first chunk wins entry
    assert merged["exit_state"] == "resolved"  # last chunk wins exit


def test_merge_first_non_empty_pov_and_setting():
    chunk_ledgers = [
        (0, {"pov": None, "setting": ""}),  # empty -> skipped
        (6, {"pov": "Kestrel", "setting": "The docks"}),  # first non-empty -> wins
        (12, {"pov": "Ignored", "setting": "Ignored place"}),  # must not override
    ]
    merged = _merge_chunk_ledgers(chunk_ledgers, prose_len=20)
    assert merged["pov"] == "Kestrel"
    assert merged["setting"] == "The docks"


def test_merge_validates_shifted_spans_against_whole_scene():
    # A span valid within its chunk but out of range once shifted must fail whole-scene validation.
    with pytest.raises(EvidenceExtractionError):
        _merge_chunk_ledgers([(10, {"events": [{"span": [0, 5]}]})], prose_len=12)


def test_merge_fills_all_sections_and_concatenates_lists():
    chunk_ledgers = [
        (0, {"entities": [{"name": "A", "span": [0, 1]}], "pov": "P"}),
        (5, {"entities": [{"name": "B", "span": [1, 2]}]}),
    ]
    merged = _merge_chunk_ledgers(chunk_ledgers, prose_len=10)
    assert set(merged.keys()) == set(LEDGER_SECTIONS)
    assert len(merged["entities"]) == 2
    assert merged["entities"][0]["span"] == [0, 1]
    assert merged["entities"][1]["span"] == [6, 7]  # 1,2 shifted by offset 5
    assert merged["pov"] == "P"


# --- FakeImportEvidenceExtractor: scripting, determinism, retry, chunk shards ----------------------


async def test_fake_default_ledger_and_validated_evidence_shape():
    fake = FakeImportEvidenceExtractor()
    src = _source(3, "hello world")
    result = await fake.extract_scene(src, ExtractionBudget())

    assert isinstance(result, ValidatedEvidence)
    assert result.schema_version == EVIDENCE_SCHEMA_VERSION == "1"
    assert result.token_usage == 0
    assert result.chunks == []
    assert set(result.ledger.keys()) == set(LEDGER_SECTIONS)
    # Default ledger anchors an events item spanning [0, min(len(prose), 1)].
    assert result.ledger["events"] == [{"summary": "scene 3", "span": [0, 1]}]
    assert result.ledger["pov"] is None
    assert fake.calls == [src.scene_id]


async def test_fake_by_scene_id_takes_priority_over_scene_no():
    sid = uuid.uuid4()
    fake = FakeImportEvidenceExtractor(
        by_scene_id={sid: {"pov": "Kestrel"}},
        by_scene_no={7: {"pov": "WrongOne"}},
    )
    result = await fake.extract_scene(_source(7, "prose here", scene_id=sid), ExtractionBudget())
    assert result.ledger["pov"] == "Kestrel"


async def test_fake_by_scene_no_fallback():
    fake = FakeImportEvidenceExtractor(by_scene_no={5: {"setting": "The docks"}})
    result = await fake.extract_scene(_source(5, "prose"), ExtractionBudget())
    assert result.ledger["setting"] == "The docks"
    assert result.ledger["events"] == []  # missing list section filled by validate_ledger


async def test_fake_is_deterministic_for_same_source():
    sid = uuid.uuid4()
    fake = FakeImportEvidenceExtractor(by_scene_id={sid: {"events": [{"summary": "x", "span": [0, 2]}]}})
    src = _source(9, "prose text", scene_id=sid)
    r1 = await fake.extract_scene(src, ExtractionBudget())
    r2 = await fake.extract_scene(src, ExtractionBudget())
    assert r1.ledger == r2.ledger
    assert r1.ledger["events"] == [{"summary": "x", "span": [0, 2]}]
    assert fake.calls == [sid, sid]


async def test_fake_fail_times_then_succeeds():
    sid = uuid.uuid4()
    fake = FakeImportEvidenceExtractor(fail_times={sid: 2})
    src = _source(2, "prose", scene_id=sid)
    budget = ExtractionBudget()

    with pytest.raises(EvidenceExtractionError):
        await fake.extract_scene(src, budget)
    with pytest.raises(EvidenceExtractionError):
        await fake.extract_scene(src, budget)
    result = await fake.extract_scene(src, budget)  # third attempt succeeds

    assert isinstance(result, ValidatedEvidence)
    assert fake.calls == [sid, sid, sid]  # every attempt, including the failures, is recorded


async def test_fake_chunks_are_validated_and_returned_with_windows():
    sid = uuid.uuid4()
    fake = FakeImportEvidenceExtractor(
        chunk_ledgers={sid: [{"events": [{"span": [0, 1]}]}, {"pov": "P"}]},
    )
    result = await fake.extract_scene(_source(4, "prose", scene_id=sid), ExtractionBudget())
    assert len(result.chunks) == 2
    # Each chunk is a cohesive EvidenceChunk (R2): validated chunk-local ledger + its [offset, end)
    # window, indexed in order, with monotonic non-overlapping synthetic windows.
    assert [c.chunk_index for c in result.chunks] == [0, 1]
    assert [(c.char_offset, c.char_end) for c in result.chunks] == [(0, 500), (1000, 1500)]
    assert all(set(c.ledger.keys()) == set(LEDGER_SECTIONS) for c in result.chunks)
    assert result.chunks[0].ledger["events"] == [{"span": [0, 1]}]
    assert result.chunks[1].ledger["pov"] == "P"


# =================================================================================================
# The extraction size envelope: budgets, chunk cap, scene hard ceiling, invalid-span quarantine.
#
# Every test below is a regression test for a defect that shipped. Before this block existed, the
# suite asserted nothing about any ExtractionBudget field -- the defaults could be changed to any
# value at all and the suite stayed green, which is exactly how a work ceiling of 4000 tokens came to
# sit below the ~6000 input tokens the chunker guaranteed to send.
# =================================================================================================


def _budget(**overrides) -> ExtractionBudget:
    """An ExtractionBudget with the shipped config defaults, minus whatever a test pins explicitly."""
    return ExtractionBudget(**overrides)


# --- extraction / output budget coherence ---------------------------------------------------------


def test_work_ceiling_affords_one_full_chunk_plus_its_whole_output_allowance():
    """THE regression test. TokenBudget charges input AND output against one ceiling and raises AFTER
    the provider call has already succeeded and been paid for. So a ceiling below what the chunker is
    guaranteed to send is not a budget, it is a guaranteed loss: with max_tokens=4000 against
    24000-char chunks, every scene over ~14k chars bought a valid extraction and then threw it away.
    Uses the real TokenBudget, the real transport estimator, and the shipped defaults."""
    budget = _budget()
    worst_case_input = estimate_tokens("x" * budget.max_chars_per_chunk)
    worst_case_output = settings.import_evidence_max_tokens

    token_budget = TokenBudget(max_tokens=budget.max_tokens)
    token_budget.charge(Usage(input_tokens=worst_case_input, output_tokens=worst_case_output))

    assert not token_budget.hard_exceeded, (
        f"a full {budget.max_chars_per_chunk}-char chunk (~{worst_case_input} input tokens) plus its "
        f"{worst_case_output}-token output allowance costs {token_budget.used}, over the "
        f"{budget.max_tokens} work ceiling -- every oversized scene would pay for its call and then die"
    )


def test_the_shipped_defect_would_now_be_caught():
    """Teeth check for the invariant above. These are the exact literals that were live on main
    (ExtractionBudget.max_tokens=4000, max_chars_per_chunk=24000). If the invariant test can pass
    against THIS pair, it is not testing anything -- so assert that the old configuration is
    demonstrably rejected by the same arithmetic."""
    old = ExtractionBudget(max_tokens=4000, max_chars_per_chunk=24000)
    token_budget = TokenBudget(max_tokens=old.max_tokens)
    with pytest.raises(BudgetExceeded):
        # A minimal 500-token ledger -- the best case the old config could ever have had.
        token_budget.charge(Usage(input_tokens=estimate_tokens("x" * old.max_chars_per_chunk), output_tokens=500))


def test_work_ceiling_still_blocks_a_genuine_runaway():
    """The fix must not turn the ceiling into a rubber stamp. A call that burns several times its
    estimated input still has to fail closed."""
    budget = _budget()
    token_budget = TokenBudget(max_tokens=budget.max_tokens)
    runaway = estimate_tokens("x" * budget.max_chars_per_chunk) * 10
    with pytest.raises(BudgetExceeded):
        token_budget.charge(Usage(input_tokens=runaway, output_tokens=settings.import_evidence_max_tokens))


def test_work_ceiling_is_derived_from_the_two_gates_never_a_literal():
    """The ceiling is the input gate plus the output allowance. Derived, so the three cannot drift."""
    assert default_work_token_budget() == (settings.import_evidence_prompt_budget + settings.import_evidence_max_tokens)
    assert _budget().max_tokens == default_work_token_budget()


def test_chunk_token_estimate_mirrors_the_transport_estimator():
    """config.py sizes the prompt budget with its own ceil(chars/4), because importing the LLM stack
    into settings would be absurd. That duplication is only safe while the two agree -- if
    llm.estimate_tokens ever changes, this fails and the derived budgets get resized with it."""
    from math import ceil

    for chars in (0, 1, 999, 12_000, 24_000):
        assert estimate_tokens("x" * chars) == (ceil(chars / 4) if chars else 0)


def test_one_full_chunk_passes_the_prompt_gate_it_will_actually_be_measured_against():
    """The input gate must not refuse what the chunker is guaranteed to produce, or every chunked
    extraction fails at the gate and no oversized scene can ever be adopted."""
    budget = _budget()
    assert estimate_tokens("x" * budget.max_chars_per_chunk) <= settings.import_evidence_prompt_budget


def test_extraction_budget_defaults_track_configuration(monkeypatch):
    """The envelope is operator-tunable in ONE place. Before this, the live values were dataclass
    literals that duplicated (and silently disagreed with) the settings of the same name."""
    monkeypatch.setattr(settings, "import_evidence_max_chars_per_chunk", 4321)
    monkeypatch.setattr(settings, "import_evidence_max_chunks", 7)
    monkeypatch.setattr(settings, "import_evidence_max_scene_chars", 8642)
    monkeypatch.setattr(settings, "import_evidence_max_quarantine_ratio", 0.5)
    monkeypatch.setattr(settings, "import_evidence_max_tokens", 111)
    monkeypatch.setattr(settings, "import_evidence_prompt_budget", 222)

    budget = ExtractionBudget()
    assert budget.max_chars_per_chunk == 4321
    assert budget.max_chunks == 7
    assert budget.max_scene_chars == 8642
    assert budget.max_quarantine_ratio == 0.5
    assert budget.max_tokens == 333  # derived: prompt budget + output allowance


# --- the boot-time envelope validator -------------------------------------------------------------


def test_settings_reject_a_prompt_budget_below_one_chunk():
    with pytest.raises(ValidationError, match="incoherent"):
        Settings(import_evidence_max_chars_per_chunk=12_000, import_evidence_prompt_budget=100)


def test_settings_reject_a_scene_ceiling_the_chunk_cap_cannot_carry():
    """A scene under the hard ceiling must never be refused later by the chunk cap -- that would spend
    the chunker's work and then fail, instead of refusing up front for free."""
    with pytest.raises(ValidationError, match="incoherent"):
        Settings(
            import_evidence_max_chars_per_chunk=12_000,
            import_evidence_max_chunks=2,
            import_evidence_max_scene_chars=120_000,
        )


def test_settings_reject_an_out_of_range_quarantine_ratio():
    with pytest.raises(ValidationError, match="fraction"):
        Settings(import_evidence_max_quarantine_ratio=1.5)


def test_shipped_settings_satisfy_the_envelope():
    """The defaults in config.py -- plus whatever DOMINION_* the environment sets -- actually cohere.
    This is the test that fails if an operator ships a broken override."""
    Settings()


# --- scene hard ceiling: refused before any provider traffic ---------------------------------------


def test_over_ceiling_scene_is_refused_and_names_the_next_human_action():
    budget = _budget(max_scene_chars=1_000)
    source = _source(1, "x" * 1_001)
    with pytest.raises(EvidenceExtractionError) as exc:
        assert_scene_within_ceiling(source, budget)
    message = str(exc.value)
    assert "1001" in message and "1000" in message
    assert "re-split the import" in message
    assert "No provider call was made." in message


def test_scene_exactly_at_the_ceiling_is_accepted():
    assert_scene_within_ceiling(_source(1, "x" * 1_000), _budget(max_scene_chars=1_000))


async def test_fake_extractor_refuses_an_over_ceiling_scene_without_recording_a_call():
    """The ceiling is a property of the SEAM, so the fake honours it too -- otherwise a test using the
    fake could not tell a refusal from a silent success."""
    fake = FakeImportEvidenceExtractor()
    source = _source(1, "x" * 5_001)
    with pytest.raises(EvidenceExtractionError, match="hard ceiling"):
        await fake.extract_scene(source, _budget(max_scene_chars=5_000))
    assert fake.calls == [], "an over-ceiling scene must not be attempted at all"


async def test_production_adapter_refuses_an_over_ceiling_scene_before_touching_a_provider():
    """No provider is reachable in this suite (conftest hard-disables every key), so reaching one at
    all would raise something other than EvidenceExtractionError. Getting the ceiling error proves the
    guard ran first."""
    with pytest.raises(EvidenceExtractionError, match="hard ceiling"):
        await LlmImportEvidenceExtractor().extract_scene(_source(1, "x" * 2_001), _budget(max_scene_chars=2_000))


# --- chunk cap: a chunker bug can never become unbounded provider spend -----------------------------


def test_chunk_cap_stops_the_fan_out():
    with pytest.raises(EvidenceExtractionError, match="chunk cap"):
        _deterministic_chunks("a" * 1_000, 10, max_chunks=5)


def test_chunk_cap_allows_a_fan_out_that_fits():
    chunks = _deterministic_chunks("a" * 50, 10, max_chunks=5)
    assert len(chunks) == 5
    assert "".join(text for _, text in chunks) == "a" * 50


def test_chunk_cap_is_opt_in_so_existing_callers_are_unchanged():
    assert len(_deterministic_chunks("a" * 1_000, 10)) == 100


def test_chunk_cap_accounts_for_boundary_seeking_producing_short_chunks():
    """Boundary-seeking can cut as early as max_chars // 2, so the chunk count is NOT ceil(len/max) --
    it can be up to twice that. The cap therefore has to count real chunks, not estimate them."""
    prose = ("word " * 2 + "\n\n") * 40  # frequent paragraph breaks -> short chunks
    naive_estimate = -(-len(prose) // 40)
    produced = len(_deterministic_chunks(prose, 40))
    assert produced > naive_estimate, "this fixture must actually exercise the short-chunk case"
    with pytest.raises(EvidenceExtractionError, match="chunk cap"):
        _deterministic_chunks(prose, 40, max_chunks=naive_estimate)


# --- deterministic invalid-span quarantine ---------------------------------------------------------


def _ledger_with(good: int, bad: int) -> dict:
    items = [{"summary": f"good {i}", "span": [0, 5]} for i in range(good)]
    items += [{"summary": f"bad {i}", "span": [900, 950]} for i in range(bad)]
    return {"events": items}


def test_one_fabricated_span_is_quarantined_not_fatal():
    """A single bad anchor used to raise and discard the whole scene's extraction -- every good fact
    with it -- then re-run the identical call under the worker's retry policy for the identical
    result. The item is now kept, its content intact, with the fabricated anchor removed."""
    out = validate_ledger(_ledger_with(good=3, bad=1), prose_len=10, max_quarantine_ratio=0.25)

    assert len(out["events"]) == 4, "nothing is dropped"
    kept = out["events"][3]
    assert kept["summary"] == "bad 0", "the item's content survives"
    assert kept["span"] is None, "the fabricated anchor does not"
    assert kept[SPAN_QUARANTINE_KEY] == {
        "section": "events",
        "reason": "span [900, 950] is not within [0, 10]",
        "rejected_span": [900, 950],
    }
    assert [e["span"] for e in out["events"][:3]] == [[0, 5]] * 3, "good anchors are untouched"


def test_quarantine_leaves_the_ledger_top_level_shape_untouched():
    """The marker rides on the ITEM, never as a 14th ledger key. Every consumer reads named sections
    (workers/packet/evidence.py re-lists them locally), and LEDGER_SECTIONS is joined into the
    extraction system prompt -- so a new top-level key would either be dropped by the section rebuild
    or instruct the model to emit it."""
    out = validate_ledger(_ledger_with(good=3, bad=1), prose_len=10, max_quarantine_ratio=0.25)
    assert set(out.keys()) == set(LEDGER_SECTIONS)


def test_quarantine_at_exactly_the_ratio_is_allowed():
    validate_ledger(_ledger_with(good=3, bad=1), prose_len=10, max_quarantine_ratio=0.25)


def test_quarantine_past_the_ratio_fails_closed():
    """Bounded, because 'quarantine everything' is its own failure: a model that anchors nothing has
    not done the job, and that must reach retry/escalation instead of persisting an unanchored ledger."""
    with pytest.raises(EvidenceExtractionError, match="did not anchor its evidence"):
        validate_ledger(_ledger_with(good=2, bad=2), prose_len=10, max_quarantine_ratio=0.25)


def test_quarantine_ratio_ignores_anchorless_items():
    """An item with no span at all is a legitimate shape (an omission anchors elsewhere), so it must
    not pad the denominator and mask a model that is fabricating every anchor it does emit."""
    ledger = {"events": [{"summary": "no anchor"}, {"summary": "explicit", "span": None}, {"span": [900, 950]}]}
    with pytest.raises(EvidenceExtractionError, match="1/1"):
        validate_ledger(ledger, prose_len=10, max_quarantine_ratio=0.25)


def test_quarantine_is_idempotent_across_revalidation():
    """The chunk merge re-validates an already-validated ledger. Re-running must count the same items
    and must never resurrect a rejected span."""
    once = validate_ledger(_ledger_with(good=3, bad=1), prose_len=10, max_quarantine_ratio=0.25)
    twice = validate_ledger(once, prose_len=10, max_quarantine_ratio=0.25)
    assert twice["events"] == once["events"]
    assert quarantined_span_count(twice) == 1


def test_quarantine_is_deterministic():
    ledger = _ledger_with(good=3, bad=1)
    first = validate_ledger(dict(ledger), prose_len=10, max_quarantine_ratio=0.25)
    second = validate_ledger(dict(ledger), prose_len=10, max_quarantine_ratio=0.25)
    assert first == second


def test_quarantined_item_survives_the_chunk_merge_unshifted():
    """The failure this guards: a quarantine record that vanishes on merge, so exactly the oversized
    scenes that need chunking are the ones whose quarantine is invisible."""
    chunk = validate_ledger(_ledger_with(good=3, bad=1), prose_len=10, max_quarantine_ratio=0.25)
    merged = _merge_chunk_ledgers([(100, chunk)], prose_len=500, max_quarantine_ratio=0.25)

    assert quarantined_span_count(merged) == 1
    quarantined = [e for e in merged["events"] if e.get(SPAN_QUARANTINE_KEY)]
    assert quarantined[0]["span"] is None, "an anchorless item is not shifted into scene coordinates"
    assert quarantined[0][SPAN_QUARANTINE_KEY]["rejected_span"] == [900, 950], "recorded verbatim"
    assert [e["span"] for e in merged["events"][:3]] == [[100, 105]] * 3, "good spans still shift"


def test_quarantined_span_count_ignores_clean_and_anchorless_ledgers():
    clean = validate_ledger({"events": [{"span": [0, 5]}, {"summary": "anchorless"}]}, prose_len=10)
    assert quarantined_span_count(clean) == 0


def test_booleans_are_not_a_valid_span():
    """bool is an int subclass in Python, so `[True, False]` passed the old all-ints check."""
    with pytest.raises(EvidenceExtractionError):
        validate_ledger({"events": [{"span": [True, False]}]}, prose_len=10)


async def test_fake_extractor_applies_the_budgets_quarantine_ratio():
    """The ratio must be read from the budget at every validation site, not silently fall back to the
    global default -- a per-scene override that nothing consults is a write-only field."""
    fake = FakeImportEvidenceExtractor(by_scene_no={1: _ledger_with(good=2, bad=2)})
    source = _source(1, "x" * 10)

    with pytest.raises(EvidenceExtractionError, match="did not anchor"):
        await fake.extract_scene(source, _budget(max_quarantine_ratio=0.25))

    result = await fake.extract_scene(source, _budget(max_quarantine_ratio=0.9))
    assert quarantined_span_count(result.ledger) == 2
