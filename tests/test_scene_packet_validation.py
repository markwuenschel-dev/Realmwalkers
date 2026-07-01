"""Unit tests for deterministic ScenePacket contract validation (pure, no DB, no LLM)."""

from __future__ import annotations

from typing import Any

from dominion.workers.scene_packet.validation import validate_scene_packet_contract


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


def test_invalid_source_handle_blocks():
    v = _run(_body(claim_sources=[{"claim": "x", "source_id": "C99"}]), sources=_sources("C1"))
    assert any(x.kind == "invalid_source_handle" and x.severity == "block" for x in v)


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
