"""Unit tests for deterministic ScenePacket contract validation (pure, no DB, no LLM)."""

from __future__ import annotations

from typing import Any

from dominion.workers.scene_packet.validation import (
    evaluate_scene_packet,
    normalize_provenance,
    validate_scene_packet_contract,
)


def _wb() -> dict[str, Any]:
    return {"target": 1500, "min": 1050, "max": 2025, "hard_max": 2400}


def _seed(*, scene_no: int = 1, required_beats: list[str] | None = None) -> dict[str, Any]:
    return {"seed_id": "s", "scene_no": scene_no, "required_beats": required_beats or []}


def _chapter(*, absent: list[str] | None = None) -> dict[str, Any]:
    return {"characters_present": ["Marcus", "Serra"], "characters_absent": absent or []}


def _sources(*handles: str) -> list[dict[str, Any]]:
    return [{"handle": h, "id": h, "doc_path": "x.md"} for h in handles]


def _body(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"scene_no": 1, "word_budget": _wb()}
    base.update(over)
    return base


def _run(body: dict[str, Any], *, chapter=None, seed=None, word_budget=None, sources=None):
    return validate_scene_packet_contract(
        body=body,
        chapter_packet_body=chapter if chapter is not None else _chapter(),
        scene_seed=seed if seed is not None else _seed(),
        word_budget=word_budget if word_budget is not None else _wb(),
        sources=sources if sources is not None else _sources("C1", "C2"),
    )


def test_clean_body_has_no_violations():
    assert _run(_body(claim_sources=[{"claim": "x", "source_id": "C1"}])) == []


def test_invalid_source_handle_warns_not_blocks():
    # WRITER-FIRST: an invalid claim source_id is optional-provenance hygiene, so the low-level contract
    # check surfaces it as a WARNING and never blocks drafting.
    v = _run(_body(claim_sources=[{"claim": "x", "source_id": "C99"}]), sources=_sources("C1"))
    assert any(x.kind == "invalid_source_handle" and x.severity == "warn" for x in v)
    assert not any(x.severity == "block" for x in v)


def test_absent_character_on_page_blocks_required_beat():
    body = _body(required_beats=["Eriadne strikes first"])
    v = _run(body, chapter=_chapter(absent=["Eriadne"]))
    blocked = [x for x in v if x.kind == "absent_character_on_page" and x.severity == "block"]
    assert {x.field for x in blocked} >= {"required_beats"}


def test_absent_character_in_intentional_mystery_warns_only():
    # A hidden/absent character named as an intentional mystery is CORRECT layering, not a leak.
    body = _body(
        intentional_mysteries=[
            {"mystery": "where is Eriadne", "desired_reader_effect": "unease", "do_not_explain": True}
        ],
    )
    v = _run(body, chapter=_chapter(absent=["Eriadne"]))
    assert any(x.kind == "absent_character_author_only_reference" and x.severity == "warn" for x in v)
    assert not any(x.severity == "block" for x in v)


def test_absent_character_leaked_into_known_before_scene_reader_blocks():
    # The reader/POV-knowledge collapse bug: a hidden reveal leaked into reader-known facts must BLOCK,
    # not just warn — the reader is not supposed to know this character exists yet.
    body = _body(known_before_scene={"reader": ["Eriadne betrayed the cohort"], "pov": [], "omniscient_author": []})
    v = _run(body, chapter=_chapter(absent=["Eriadne"]))
    blocked = [x for x in v if x.kind == "absent_character_reader_pov_leak" and x.severity == "block"]
    assert any(b.field == "known_before_scene.reader" for b in blocked)


# --- normalize_provenance -------------------------------------------------------------------------


def test_normalize_provenance_nulls_invalid_and_keeps_valid():
    body = _body(
        claim_sources=[
            {"claim": "from outline", "source_id": "OUTLINE"},
            {"claim": "from a real handle", "source_id": "C1"},
            {"claim": "already inference", "source_id": None},
            {"claim": "a uuid", "source_id": "f332489e-faba-443f-9860-518ea790510b"},
            {"claim": "out of range", "source_id": "C7"},
        ]
    )
    normalized, warnings = normalize_provenance(body, _sources("C1", "C2", "C3", "C4", "C5", "C6"))
    ids = [c["source_id"] for c in normalized["claim_sources"]]
    # OUTLINE, the UUID and C7 are nulled; the real C1 handle and the pre-existing null are untouched.
    assert ids == [None, "C1", None, None, None]
    # Collapsed to ONE warning, never a wall of per-claim messages.
    assert len(warnings) == 1
    w = warnings[0]
    assert w.kind == "provenance_normalized" and w.severity == "warn"
    assert "3 claim source id" in w.detail
    # The original body is not mutated in place.
    assert body["claim_sources"][0]["source_id"] == "OUTLINE"


# --- evaluate_scene_packet (normalize -> validate -> draftability) --------------------------------


def _evaluate(body, *, chapter=None, seed=None, word_budget=None, scene_no=1, sources=None, block_on_provenance=False):
    return evaluate_scene_packet(
        body=body,
        chapter_packet_body=chapter if chapter is not None else _chapter(),
        scene_seed=seed if seed is not None else _seed(),
        word_budget=word_budget if word_budget is not None else _wb(),
        scene_no=scene_no,
        sources=sources if sources is not None else _sources("C1", "C2", "C3", "C4", "C5", "C6"),
        block_on_provenance=block_on_provenance,
    )


def test_evaluate_provenance_only_is_draftable():
    # The exact screenshot failure class: OUTLINE / UUID / C7 as the ONLY defect must not block drafting.
    result = _evaluate(
        _body(
            claim_sources=[
                {"claim": "follows outline", "source_id": "OUTLINE"},
                {"claim": "seed beat", "source_id": "f332489e-faba-443f-9860-518ea790510b"},
                {"claim": "out of range", "source_id": "C7"},
            ]
        )
    )
    assert result.draftable is True
    assert result.draft_blockers == []
    # Source ids were normalized to null on the body we would persist/draft from.
    assert all(c["source_id"] is None for c in result.normalized_body["claim_sources"])
    # Deduped to a single provenance warning.
    prov = [w for w in result.warnings if w.kind == "provenance_normalized"]
    assert len(prov) == 1


def test_evaluate_reader_known_leak_blocks():
    # The Mara/Roth regression class: a hidden reveal leaked into reader-known facts must block, even
    # though the character is correctly absent from the on-page cast.
    result = _evaluate(
        _body(known_before_scene={"reader": ["Mara left earlier"], "pov": [], "omniscient_author": []}),
        chapter=_chapter(absent=["Mara"]),
    )
    assert result.draftable is False
    assert any(b.kind == "absent_character_reader_pov_leak" for b in result.draft_blockers)
