"""Regression coverage for SEQ-IDX: derive_chapter_sequence cross-indexed two differently-ordered
lists.

`derive_chapter_sequence` (src/dominion/workers/production_sequence.py) built `scene_numbers` from
the raw, possibly out-of-order `scene_seeds` list, but the loop that assigns `depends_on_scene_no` /
`unlocks_scene_no` enumerates the separately-sorted `ordered` copy while indexing into
`scene_numbers` at that same position. Whenever a chapter packet's scene_seeds were authored out of
ascending scene_no order, the two lists disagreed on order, so a scene could be chained to a
plausible-looking but WRONG predecessor (an earlier scene number that just happened to also be a
valid map key). The self-healing pass in `chain_scene_entry_states` only rejects missing/>=/absent
links, so this "valid but wrong" link was never caught -- a scene would silently open on the wrong
predecessor's exit_state.

Pure-Python -- no network, LLM, or Postgres. Mirrors the ascending-order coverage already in
tests/test_sequence_chaining.py::test_derive_chapter_sequence_chains_seed_entries, but authors the
scene_seeds array out of order (scene_no 3, 1, 2) to exercise the sort/index mismatch directly.
"""

from __future__ import annotations

from typing import Any

from dominion.workers.production_sequence import derive_chapter_sequence


def _scenes_by_no(body: dict[str, Any]) -> dict[int, dict[str, Any]]:
    return {int(s["scene_no"]): s for s in body["scenes"]}


def _seed(scene_no: int) -> dict[str, Any]:
    return {
        "seed_id": f"00000000-0000-0000-0000-00000000000{scene_no}",
        "scene_no": scene_no,
        "scene_job": f"scene {scene_no} job",
        "required_beats": [f"beat {scene_no}"],
        "forbidden_beats": [],
        "entry_state": "Global entry: Marcus late at work.",
        "exit_state": f"Exit of scene {scene_no}.",
    }


def test_derive_chapter_sequence_chains_out_of_order_seeds() -> None:
    """scene_seeds authored out of ascending order (3, 1, 2) must still chain each scene to its
    true immediate predecessor/successor by scene_no -- not whatever seed happened to sit at the
    matching array offset before the seeds were sorted."""
    packet_body = {
        "chapter_no": 1,
        "chapter_job": "job",
        "one_sentence_spine": "spine",
        "entry_state": "Global entry: Marcus late at work.",
        "exit_state": "Global exit: the scrim is hijacked.",
        "target_words": 3000,
        "max_words": 3600,
        # Deliberately NOT in ascending scene_no order -- this is the exact shape that cross-indexes
        # `scene_numbers` (built from this list) against `ordered` (the sorted copy the loop walks).
        "scene_seeds": [_seed(3), _seed(1), _seed(2)],
    }

    body = derive_chapter_sequence(packet_body)
    scenes = _scenes_by_no(body)

    # Predecessor links: each scene depends on its true immediate predecessor by scene_no, never on
    # whatever scene sat at the corresponding offset in the unsorted authoring order.
    assert scenes[1]["depends_on_scene_no"] is None
    assert scenes[2]["depends_on_scene_no"] == 1
    assert scenes[3]["depends_on_scene_no"] == 2  # bug produced 1 here (scene 1's offset, not scene 2's)

    # Successor links, same story in the other direction.
    assert scenes[1]["unlocks_scene_no"] == 2
    assert scenes[2]["unlocks_scene_no"] == 3
    assert scenes[3]["unlocks_scene_no"] is None

    # The chaining contract's actual observable effect: each dependent scene opens on its true
    # predecessor's exit_state. Under the bug, scene 3 silently opened on scene 1's exit_state.
    assert scenes[2]["entry_state"] == scenes[1]["exit_state"]
    assert scenes[3]["entry_state"] == scenes[2]["exit_state"]
    assert scenes[3]["entry_state"] != scenes[1]["exit_state"]
