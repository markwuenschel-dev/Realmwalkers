"""Scene entry/exit chaining contract (workers/production.py chain_scene_entry_states).

The Ch1 failure (tests/fixtures/ch1_bad_run/): every scene in the derived ChapterSequence carried
the identical global entry_state despite depends_on 1→2→3 and independent_draft_allowed=false, so
scenes 2–4 restarted the whole chapter arc. The deterministic post-pass must rewrite entry_states:
scene 1 = global_entry_state; dependent scene N = its depends_on scene's exit_state (default N−1).
Pure-Python — no network, LLM, or Postgres.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from dominion.workers.production_sequence import (
    chain_scene_entry_states,
    derive_chapter_sequence,
    evaluate_chapter_sequence,
)

_FIXTURE = Path(__file__).parent / "fixtures" / "ch1_bad_run" / "chapter_sequence.json"


def _bad_run_body() -> dict[str, Any]:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))["body"]


def _scenes_by_no(body: dict[str, Any]) -> dict[int, dict[str, Any]]:
    return {int(s["scene_no"]): s for s in body["scenes"]}


def test_bad_run_fixture_reproduces_the_break() -> None:
    body = _bad_run_body()
    scenes = _scenes_by_no(body)
    # Preserved evidence: all four scenes open at the global entry while exits are distinct.
    for scene_no in (2, 3, 4):
        assert scenes[scene_no]["entry_state"] == body["global_entry_state"]
        assert scenes[scene_no]["entry_state"] != scenes[scene_no - 1]["exit_state"]


def test_chain_pass_repairs_the_bad_run() -> None:
    body = chain_scene_entry_states(_bad_run_body())
    scenes = _scenes_by_no(body)
    assert scenes[1]["entry_state"] == body["global_entry_state"]
    assert scenes[2]["entry_state"] == scenes[1]["exit_state"]
    assert scenes[3]["entry_state"] == scenes[2]["exit_state"]
    assert scenes[4]["entry_state"] == scenes[3]["exit_state"]
    # Exit states are authored per scene and must never be touched by the pass.
    original = _scenes_by_no(_bad_run_body())
    for scene_no, scene in scenes.items():
        assert scene["exit_state"] == original[scene_no]["exit_state"]


def test_chain_pass_enforces_dependency_links() -> None:
    body = _bad_run_body()
    scenes = _scenes_by_no(body)
    scenes[1]["depends_on_scene_no"] = 4  # first scene can depend on nothing
    scenes[2]["depends_on_scene_no"] = None  # missing -> defaults to N-1
    scenes[3]["depends_on_scene_no"] = 9  # dangling -> defaults to N-1
    scenes[4]["depends_on_scene_no"] = 2  # valid earlier reference is kept
    scenes[2]["unlocks_scene_no"] = 2  # self-unlock is invalid -> next scene
    scenes[4]["unlocks_scene_no"] = 1  # backward unlock is invalid -> None (last scene)

    chained = _scenes_by_no(chain_scene_entry_states(body))
    assert chained[1]["depends_on_scene_no"] is None
    assert chained[2]["depends_on_scene_no"] == 1
    assert chained[3]["depends_on_scene_no"] == 2
    assert chained[4]["depends_on_scene_no"] == 2
    assert chained[2]["unlocks_scene_no"] == 3
    assert chained[4]["unlocks_scene_no"] is None
    # Scene 4 depends on scene 2, so it opens at scene 2's exit — not scene 3's.
    assert chained[4]["entry_state"] == chained[2]["exit_state"]


def test_independent_scene_keeps_authored_entry() -> None:
    body = _bad_run_body()
    scenes = _scenes_by_no(body)
    scenes[3]["independent_draft_allowed"] = True
    scenes[3]["entry_state"] = "Elsewhere, Serra watches the bracket board alone."

    chained = _scenes_by_no(chain_scene_entry_states(body))
    assert chained[3]["entry_state"] == "Elsewhere, Serra watches the bracket board alone."
    # Dependent neighbors still chain.
    assert chained[2]["entry_state"] == chained[1]["exit_state"]
    assert chained[4]["entry_state"] == chained[3]["exit_state"]


def test_evaluate_accepts_chained_body_and_flags_unchained() -> None:
    unchained = _bad_run_body()
    warnings = evaluate_chapter_sequence(copy.deepcopy(unchained))["warnings"] or {}
    assert warnings.get("entry_exit_mismatches"), "the bad run must be flagged"

    chained = chain_scene_entry_states(unchained)
    warnings = evaluate_chapter_sequence(chained)["warnings"] or {}
    assert "entry_exit_mismatches" not in warnings


def test_evaluate_does_not_flag_independent_scene() -> None:
    body = chain_scene_entry_states(_bad_run_body())
    scenes = _scenes_by_no(body)
    scenes[3]["independent_draft_allowed"] = True
    scenes[3]["entry_state"] = "A cold open somewhere else entirely."
    warnings = evaluate_chapter_sequence(body)["warnings"] or {}
    assert "entry_exit_mismatches" not in warnings


def test_derive_chapter_sequence_chains_seed_entries() -> None:
    """The derivation path itself must emit a chained sequence even when every seed carries the
    global entry (the exact shape the Ch1 chapter packet authored)."""
    packet_body = {
        "chapter_no": 1,
        "chapter_job": "job",
        "one_sentence_spine": "spine",
        "entry_state": "Global entry: Marcus late at work.",
        "exit_state": "Global exit: the scrim is hijacked.",
        "target_words": 3000,
        "max_words": 3600,
        "scene_seeds": [
            {
                "seed_id": f"00000000-0000-0000-0000-00000000000{n}",
                "scene_no": n,
                "scene_job": f"scene {n} job",
                "required_beats": [f"beat {n}"],
                "forbidden_beats": [],
                "entry_state": "Global entry: Marcus late at work.",
                "exit_state": f"Exit of scene {n}.",
            }
            for n in (1, 2, 3)
        ],
    }
    body = derive_chapter_sequence(packet_body)
    scenes = _scenes_by_no(body)
    assert scenes[1]["entry_state"] == "Global entry: Marcus late at work."
    assert scenes[1]["depends_on_scene_no"] is None
    assert scenes[2]["entry_state"] == "Exit of scene 1."
    assert scenes[2]["depends_on_scene_no"] == 1
    assert scenes[3]["entry_state"] == "Exit of scene 2."
    assert scenes[3]["depends_on_scene_no"] == 2
    assert scenes[3]["unlocks_scene_no"] is None


def test_drafter_contract_carries_entry_state() -> None:
    """The flat drafter contract must surface the chained entry_state so the drafting prompt can
    forbid restaging (the drafter previously never received any opening state)."""
    from dominion.workers.scene_packet.projections import project

    scene_body = {
        "entry_state": "The match has started and Marcus is engaged.",
        "exit_state": "Mutual recognition lands.",
        "required_beats": ["beat"],
    }
    contract = project(scene_body, {}).drafter_flat
    assert contract["entry_state"] == "The match has started and Marcus is engaged."
    assert contract["exit_state"] == "Mutual recognition lands."
