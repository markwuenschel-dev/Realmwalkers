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


def test_valid_source_handles_pass():
    v = _run(_body(claim_sources=[{"claim": "a", "source_id": "C1"}, {"claim": "b", "source_id": "C2"}]))
    assert v == []


def test_invalid_source_handle_warns_not_blocks():
    # WRITER-FIRST: an invalid claim source_id is optional-provenance hygiene, so the low-level contract
    # check surfaces it as a WARNING and never blocks drafting.
    v = _run(_body(claim_sources=[{"claim": "x", "source_id": "C99"}]), sources=_sources("C1"))
    assert any(x.kind == "invalid_source_handle" and x.severity == "warn" for x in v)
    assert not any(x.severity == "block" for x in v)


def test_null_source_id_is_inference_and_passes():
    v = _run(_body(claim_sources=[{"claim": "x", "source_id": None}]), sources=_sources("C1"))
    assert v == []


def test_word_budget_override_blocks():
    v = _run(_body(word_budget={"target": 9999}), word_budget=_wb())
    assert any(x.kind == "word_budget_override" and x.severity == "block" for x in v)


def test_matching_word_budget_passes():
    v = _run(_body(word_budget=_wb()), word_budget=_wb())
    assert not any(x.kind == "word_budget_override" for x in v)


def test_scene_no_mismatch_blocks():
    v = _run(_body(scene_no=5), seed=_seed(scene_no=2), word_budget=_wb())
    assert any(x.kind == "scene_no_mismatch" and x.severity == "block" for x in v)


def test_absent_character_on_page_blocks_required_beat_and_reviewer():
    body = _body(
        required_beats=["Eriadne strikes first"],
        reviewer_instructions={"continuity": ["track Eriadne's wound"], "pacing": []},
    )
    v = _run(body, chapter=_chapter(absent=["Eriadne"]))
    blocked = [x for x in v if x.kind == "absent_character_on_page" and x.severity == "block"]
    assert {x.field for x in blocked} >= {"required_beats", "reviewer_instructions"}


def test_absent_character_in_pov_may_notice_blocks():
    body = _body(pov_permissions={"may_notice": ["Eriadne in the doorway"], "must_not_know": []})
    v = _run(body, chapter=_chapter(absent=["Eriadne"]))
    assert any(x.field == "pov_permissions.may_notice" and x.severity == "block" for x in v)


def test_absent_character_in_must_not_know_does_not_block():
    # must_not_know legitimately references an absent character (the POV must NOT know their plan).
    body = _body(pov_permissions={"may_notice": [], "must_not_know": ["Eriadne's true allegiance"]})
    v = _run(body, chapter=_chapter(absent=["Eriadne"]))
    assert not any(x.severity == "block" for x in v)


def test_absent_character_off_page_warns_only():
    body = _body(
        intentional_mysteries=[
            {"mystery": "where is Eriadne", "desired_reader_effect": "unease", "do_not_explain": True}
        ],
        known_before_scene={"reader": ["Eriadne betrayed the cohort"], "pov": [], "omniscient_author": []},
    )
    v = _run(body, chapter=_chapter(absent=["Eriadne"]))
    assert any(x.kind == "absent_character_off_page" and x.severity == "warn" for x in v)
    assert not any(x.severity == "block" for x in v)


def test_short_name_does_not_match_inside_unrelated_word():
    # Whole-word matching: an absent "Al" must not trip on "always" in an on-page field.
    body = _body(required_beats=["Marcus always holds the line"])
    v = _run(body, chapter=_chapter(absent=["Al"]))
    assert v == []


def test_required_beat_dropped_warns():
    body = _body(required_beats=["something completely unrelated happens"])
    v = _run(body, seed=_seed(required_beats=["the cohort converges on the bridge"]))
    assert any(x.kind == "required_beat_dropped" and x.severity == "warn" for x in v)


def test_required_beat_preserved_no_warning():
    body = _body(required_beats=["the cohort converges on the bridge at dawn"])
    v = _run(body, seed=_seed(required_beats=["the cohort converges on the bridge"]))
    assert not any(x.kind == "required_beat_dropped" for x in v)


def test_non_dict_body_blocks():
    v = validate_scene_packet_contract(
        body="not a dict",  # type: ignore[arg-type]
        chapter_packet_body=_chapter(),
        scene_seed=_seed(),
        word_budget=_wb(),
        sources=[],
    )
    assert len(v) == 1 and v[0].severity == "block"


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


def test_normalize_provenance_noop_when_all_valid_or_null():
    body = _body(claim_sources=[{"claim": "a", "source_id": "C1"}, {"claim": "b", "source_id": None}])
    normalized, warnings = normalize_provenance(body, _sources("C1", "C2"))
    assert warnings == []
    assert normalized is body  # unchanged reference when nothing was rewritten


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


def test_evaluate_block_on_provenance_escalates():
    result = _evaluate(
        _body(claim_sources=[{"claim": "x", "source_id": "OUTLINE"}]),
        block_on_provenance=True,
    )
    assert result.draftable is False
    assert any(b.kind == "provenance_normalized" for b in result.draft_blockers)


def test_evaluate_absent_active_character_still_blocks():
    result = _evaluate(
        _body(required_beats=["Mara enters and attacks"], exit_state="Mara escapes"),
        chapter=_chapter(absent=["Mara"]),
    )
    assert result.draftable is False
    assert any(b.kind == "absent_character_on_page" for b in result.draft_blockers)


def test_evaluate_off_page_absent_reference_warns_not_blocks():
    result = _evaluate(
        _body(
            known_before_scene={"reader": ["Mara left earlier"], "pov": [], "omniscient_author": []},
            intentional_mysteries=[{"mystery": "why Mara is absent", "desired_reader_effect": "unease"}],
        ),
        chapter=_chapter(absent=["Mara"]),
    )
    assert result.draftable is True
    assert any(w.kind == "absent_character_off_page" for w in result.warnings)


def test_evaluate_non_dict_body_blocks():
    result = _evaluate("not a dict")  # type: ignore[arg-type]
    assert result.draftable is False
    assert any(b.kind == "invalid_body" for b in result.draft_blockers)


def test_evaluate_unrecoverable_word_budget_blocks():
    result = _evaluate(_body(), word_budget={})
    assert result.draftable is False
    assert any(b.kind == "word_budget_unrecoverable" for b in result.draft_blockers)


def test_evaluate_unrecoverable_scene_no_blocks():
    result = _evaluate(_body(), scene_no=None)
    assert result.draftable is False
    assert any(b.kind == "scene_no_unrecoverable" for b in result.draft_blockers)


def test_evaluate_stamps_deterministic_facts():
    # A model echo that overrides the budget/scene_no is silently corrected server-side, not blocked.
    result = _evaluate(_body(word_budget={"target": 9999}, scene_no=42), scene_no=1)
    assert result.draftable is True
    assert result.normalized_body["word_budget"] == _wb()
    assert result.normalized_body["scene_no"] == 1
