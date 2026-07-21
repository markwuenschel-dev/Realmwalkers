"""Unit tests for the manuscript-vs-canon conflict grammar + author-time detection (ADR 0028 Slice 3b /
ADR 0029). Pure — no DB / LLM / network; the canon retriever is a scripted fake.

Covers:
  * grammar round-trip (`format_conflict` -> `parse_conflict` is identity), marker recognition, and that
    an ordinary human question is NOT mistaken for an encoded conflict;
  * the vocabulary is pinned to `shared/claim_precedence` so the kind can't drift;
  * a re-anchorable conflict -> an encoded open question whose payload traces both sides;
  * a non-re-anchorable conflict -> a fail-closed signal (no current canon, or an unanchored M# span).
"""

from __future__ import annotations

import uuid

import pytest

from dominion.shared import claim_precedence
from dominion.shared.enums import ClaimSource
from dominion.shared.manuscript_conflict import (
    KIND,
    MARKER,
    ManuscriptCanonConflict,
    append_conflict,
    format_conflict,
    is_conflict_question,
    parse_conflict,
)
from dominion.workers.packet.canon_conflict import (
    FailClosedReason,
    ManuscriptClaim,
    detect_manuscript_canon_conflicts,
)


def _conflict(**overrides) -> ManuscriptCanonConflict:
    base = dict(
        canon_handle="C3",
        canon_id=str(uuid.uuid4()),
        canon_name="Marcus Vale",
        manuscript_handle="M1",
        scene_id=str(uuid.uuid4()),
        scene_version=2,
        prose_hash="a" * 64,
        span=(10, 42),
        canon_claim='Marcus is left-handed | says "pipe delimited" & {json}',
        manuscript_claim="the prose shows Marcus signing with his right hand",
    )
    base.update(overrides)
    return ManuscriptCanonConflict(**base)  # type: ignore[arg-type]


def _fixed_retriever(hits):
    async def _retrieve(_query):
        return list(hits)

    return _retrieve


# --- grammar: round-trip, marker, non-conflict rejection ------------------------------------------


def test_round_trip_is_identity():
    c = _conflict()
    encoded = format_conflict(c)
    assert encoded.startswith(MARKER)
    assert is_conflict_question(encoded)
    assert parse_conflict(encoded) == c


def test_round_trip_with_null_canon_name():
    c = _conflict(canon_name=None)
    assert parse_conflict(format_conflict(c)) == c


def test_round_trip_survives_delimiters_and_unicode():
    c = _conflict(
        canon_claim="brackets ] and marker-ish [manuscript_canon_conflict] inside — dashes, ünïcode",
        manuscript_claim='quotes " and commas, colons: braces {} and pipes |',
    )
    assert parse_conflict(format_conflict(c)) == c


def test_plain_human_question_is_not_a_conflict():
    for item in ("Does Marcus know his brother survived?", "", "  ", MARKER + " {not json"):
        assert not is_conflict_question(item) or parse_conflict(item) is None
    assert parse_conflict("Does Marcus know his brother survived?") is None
    assert is_conflict_question("Does Marcus know his brother survived?") is False


def test_parse_rejects_non_string_and_malformed_payload():
    assert parse_conflict(None) is None
    assert parse_conflict(123) is None
    assert parse_conflict(f"{MARKER} [1, 2, 3]") is None  # JSON, but not an object
    assert parse_conflict(f'{MARKER} {{"canon_handle": "C1"}}') is None  # missing required keys
    assert parse_conflict(f'{MARKER} {{"span": [1]}}') is None  # bad span shape


def test_bad_span_is_rejected_at_construction():
    with pytest.raises(ValueError):
        _conflict(span=(42, 10))  # end before start
    with pytest.raises(ValueError):
        _conflict(span=(1, 2, 3))  # not a pair


def test_kind_matches_claim_precedence_vocabulary():
    # The grammar's KIND must equal the precedence policy's named kind for manuscript × locked canon,
    # so the two modules can never disagree on the vocabulary.
    assert KIND == claim_precedence.conflict_kind(ClaimSource.LOCKED_CANON, ClaimSource.DERIVED_FROM_MANUSCRIPT)
    assert MARKER == f"[{KIND}]"


def test_append_conflict_is_append_only():
    items = ["Does Marcus know his brother survived?", "Is the vault sealed?"]
    c = _conflict()
    out = append_conflict(items, c)
    assert out == [*items, format_conflict(c)]
    assert items == ["Does Marcus know his brother survived?", "Is the vault sealed?"]  # input untouched
    assert parse_conflict(out[-1]) == c


# --- detection: re-anchorable -> encoded question -------------------------------------------------


async def test_reanchorable_conflict_yields_encoded_question():
    scene_id = uuid.uuid4()
    canon_id = uuid.uuid4()
    claim = ManuscriptClaim(
        handle="M1",
        scene_id=str(scene_id),
        scene_version=3,
        prose_hash="b" * 64,
        span=(5, 20),
        assertion="the prologue shows the gate already breached",
        snapshot_prose_len=100,
    )
    hits = [{"id": canon_id, "name": "The Gate", "body": "The gate has never been breached in canon."}]
    result = await detect_manuscript_canon_conflicts(claim_batch := [claim], retrieve=_fixed_retriever(hits))
    assert result.fail_closed == ()
    assert len(result.reanchored) == 1
    assert result.blocks_approval is True

    question = result.open_questions()[0]
    parsed = parse_conflict(question)
    assert parsed is not None
    assert parsed.canon_id == str(canon_id)
    assert parsed.canon_name == "The Gate"
    assert parsed.manuscript_handle == "M1"
    assert parsed.scene_id == str(scene_id)
    assert parsed.scene_version == 3
    assert parsed.span == (5, 20)
    assert "breached" in parsed.manuscript_claim
    assert result.reanchored[0].conflict == parsed
    assert len(claim_batch) == 1


async def test_canon_handle_taken_from_caller_map():
    canon_id = uuid.uuid4()
    claim = ManuscriptClaim(
        handle="M2",
        scene_id=str(uuid.uuid4()),
        scene_version=1,
        prose_hash="c" * 64,
        span=(0, 4),
        assertion="prose asserts the artifact is destroyed",
    )
    hits = [{"id": canon_id, "name": "Artifact", "body": "The artifact endures."}]
    result = await detect_manuscript_canon_conflicts(
        [claim], retrieve=_fixed_retriever(hits), canon_handle_by_id={str(canon_id): "C7"}
    )
    assert result.reanchored[0].conflict.canon_handle == "C7"


async def test_named_canon_id_must_still_be_retrievable():
    present = uuid.uuid4()
    claim = ManuscriptClaim(
        handle="M1",
        scene_id=str(uuid.uuid4()),
        scene_version=1,
        prose_hash="d" * 64,
        span=(0, 3),
        assertion="prose fact",
        canon_id=str(uuid.uuid4()),  # a fingerprint NOT in the live hits below
    )
    hits = [{"id": present, "name": "Other", "body": "unrelated current canon"}]
    result = await detect_manuscript_canon_conflicts([claim], retrieve=_fixed_retriever(hits))
    assert result.reanchored == ()
    assert [fc.reason for fc in result.fail_closed] == [FailClosedReason.NO_CURRENT_CANON]


# --- detection: non-re-anchorable -> fail closed -------------------------------------------------


async def test_no_current_canon_fails_closed():
    claim = ManuscriptClaim(
        handle="M1",
        scene_id=str(uuid.uuid4()),
        scene_version=1,
        prose_hash="e" * 64,
        span=(1, 9),
        assertion="a prose assertion with no matching locked canon",
    )
    result = await detect_manuscript_canon_conflicts([claim], retrieve=_fixed_retriever([]))
    assert result.reanchored == ()
    assert result.open_questions() == []
    assert len(result.fail_closed) == 1
    assert result.fail_closed[0].reason == FailClosedReason.NO_CURRENT_CANON
    assert result.fail_closed[0].manuscript_handle == "M1"
    assert result.blocks_approval is True


async def test_empty_bodied_canon_row_does_not_re_anchor():
    claim = ManuscriptClaim(
        handle="M1",
        scene_id=str(uuid.uuid4()),
        scene_version=1,
        prose_hash="f" * 64,
        span=(0, 2),
        assertion="prose fact",
    )
    hits = [{"id": uuid.uuid4(), "name": "Empty", "body": "   "}]  # no assertion to conflict with
    result = await detect_manuscript_canon_conflicts([claim], retrieve=_fixed_retriever(hits))
    assert [fc.reason for fc in result.fail_closed] == [FailClosedReason.NO_CURRENT_CANON]


async def test_unanchored_manuscript_span_fails_closed_without_retrieval():
    async def _explode(_query):
        raise AssertionError("retrieval must not run for an unanchored manuscript claim")

    for bad in (
        ManuscriptClaim("M1", str(uuid.uuid4()), 1, "1" * 64, None, "no span"),
        ManuscriptClaim("M2", str(uuid.uuid4()), 1, "", (0, 5), "no prose_hash"),
        ManuscriptClaim("M3", str(uuid.uuid4()), 1, "2" * 64, (5, 3), "end before start"),
        ManuscriptClaim("M4", str(uuid.uuid4()), 1, "3" * 64, (0, 50), "span past snapshot", snapshot_prose_len=10),
    ):
        result = await detect_manuscript_canon_conflicts([bad], retrieve=_explode)
        assert result.reanchored == ()
        assert [fc.reason for fc in result.fail_closed] == [FailClosedReason.UNANCHORED_MANUSCRIPT_SPAN]


async def test_batch_partitions_reanchored_and_fail_closed():
    good = ManuscriptClaim("M1", str(uuid.uuid4()), 1, "a" * 64, (0, 5), "conflicting prose")
    bad = ManuscriptClaim("M2", str(uuid.uuid4()), 1, "b" * 64, None, "unanchored")
    hits = [{"id": uuid.uuid4(), "name": "Canon", "body": "canon says otherwise"}]
    result = await detect_manuscript_canon_conflicts([good, bad], retrieve=_fixed_retriever(hits))
    assert len(result.reanchored) == 1
    assert result.reanchored[0].conflict.manuscript_handle == "M1"
    assert len(result.fail_closed) == 1
    assert result.fail_closed[0].manuscript_handle == "M2"
