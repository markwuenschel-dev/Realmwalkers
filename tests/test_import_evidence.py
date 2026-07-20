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

from dominion.workers.import_evidence import (
    EVIDENCE_SCHEMA_VERSION,
    LEDGER_SECTIONS,
    EvidenceExtractionError,
    ExtractionBudget,
    FakeImportEvidenceExtractor,
    SceneSource,
    ValidatedEvidence,
    _deterministic_chunks,
    _merge_chunk_ledgers,
    validate_ledger,
)

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
    assert result.chunk_ledgers == []
    assert result.merged_shard_ids == []
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


async def test_fake_chunk_ledgers_are_validated_and_returned():
    sid = uuid.uuid4()
    fake = FakeImportEvidenceExtractor(
        chunk_ledgers={sid: [{"events": [{"span": [0, 1]}]}, {"pov": "P"}]},
    )
    result = await fake.extract_scene(_source(4, "prose", scene_id=sid), ExtractionBudget())
    assert len(result.chunk_ledgers) == 2
    assert all(set(cl.keys()) == set(LEDGER_SECTIONS) for cl in result.chunk_ledgers)
    assert result.chunk_ledgers[0]["events"] == [{"span": [0, 1]}]
    assert result.chunk_ledgers[1]["pov"] == "P"
