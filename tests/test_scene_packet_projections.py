"""Pure unit tests for ScenePacket body → consumer contract projections."""

from __future__ import annotations

from typing import Any

from dominion.workers.scene_packet.projections import project


def _scene_body(word_budget: dict[str, Any] | None = None) -> dict[str, Any]:
    mole = "Serra is the mole"
    return {
        "scene_no": 1,
        "scene_job": "Marcus intercepts.",
        "scene_type": "combat",
        "word_budget": word_budget or {"target": 1500, "min": 1050, "max": 2025, "hard_max": 2400},
        "known_before_scene": {"reader": ["the route"], "pov": ["the route"], "omniscient_author": [mole]},
        "learned_during_scene": {
            "reader_must_learn": ["the cohort is converging"],
            "reader_may_learn": [],
            "reader_may_infer_only": [],
        },
        "must_remain_hidden": {"reader": [mole], "pov": [], "all_surface_prose": []},
        "pov_permissions": {
            "may_notice": [],
            "may_infer": [],
            "must_not_know": [mole],
            "may_be_wrong_about": [],
        },
        "intentional_mysteries": [
            {"mystery": "who tipped the cohort", "desired_reader_effect": "unease", "do_not_explain": True},
        ],
        "reviewer_false_positive_traps": ["the missing tip source is intentional"],
        "required_beats": ["land the hit"],
        "forbidden_beats": ["Marcus uses his Aspect"],
        "exit_state": "both wounded",
        "phrases_to_avoid_echoing": ["reader must learn"],
        "reviewer_instructions": {"combat": ["track stamina"], "continuity": []},
    }


def test_flat_contract_omits_empty_keys():
    body = {
        "known_before_scene": {},
        "learned_during_scene": {},
        "word_budget": {"target": 1000},
    }
    p = project(body, {})

    assert p.drafter_flat == {}
    assert p.word_budget == {"target": 1000}


def test_word_budget_non_dict_becomes_none():
    body = {"word_budget": "bad", "known_before_scene": {}, "learned_during_scene": {}}
    p = project(body, {})

    assert p.word_budget is None
    assert p.reviewer["word_budget"] is None


def test_chapter_locks_only_in_flat():
    body = _scene_body()
    chapter = {"canon_locks": ["the Realm is real"], "roster_locks": ["Marcus leads"]}
    p = project(body, chapter)

    assert p.drafter_flat["canon_locks"] == ["the Realm is real"]
    assert p.drafter_flat["roster_locks"] == ["Marcus leads"]
    assert "canon_locks" not in p.reader_state
    assert "canon_locks" not in p.reviewer
    assert "roster_locks" not in p.reader_state
    assert "roster_locks" not in p.reviewer


def test_reader_state_defaults():
    p = project({}, {})

    assert p.reader_state == {
        "known_before_scene": {},
        "learned_during_scene": {},
        "must_remain_hidden": {},
        "pov_permissions": {},
        "intentional_mysteries": [],
        "reviewer_false_positive_traps": [],
    }
    assert p.reviewer == {
        "scene_job": None,
        "scene_type": None,
        "required_beats": [],
        "forbidden_beats": [],
        "reviewer_false_positive_traps": [],
        "reviewer_instructions": {},
        "word_budget": None,
    }
