"""Integrity guard: planner.py must not let its LLM prompt vocabulary drift from
scene_packet.beats._LANE_TAGS, the canonical lane-tag list.

`_plan_prompt` hardcodes the tags as literal text inside the beat-proposal prompt
(`"tags": [str] (any of "combat", "sensory", "dialogue"; else [])`) instead of deriving them
from `_LANE_TAGS`. This hand-maintained mirror is the SAME failure class that has already
shipped twice, each time patched only locally with no test to stop a third recurrence:

- router.py (audit candidate C1, commit 0f919e2): the scene-packet producer stamped beats
  "sensory", but router.py's DRAFT_PASSES/_PASS_ORDER/TAG_REVIEWERS were keyed on the stale
  "physical_description" -- passes_for(["sensory"])/reviewers_for(["sensory"]) silently
  returned [] (no pass, no review lane, no repair, no error, no log). That SAME commit had to
  hand-align THIS file's prompt text from "physical_description" to "sensory" -- direct proof
  this exact mirror has drifted before, caught only because the router bug forced a full audit,
  not by any guard on planner.py itself.
- production_repair.py (audit candidate D2, commit 53b4879): a "combat" critique fell through
  _infer_repair_kind to "reader_context", which _target_pass_for_task mapped nowhere, so it
  silently became a full scene revision instead of routing back to the combat pass built to fix
  exactly what the combat reviewer flags.

Adding a 4th lane tag to `_LANE_TAGS` without updating this prompt would silently make the
planner never propose it -- no error, just an LLM that was never told the tag exists.
"""

from __future__ import annotations

from dominion.workers.planner import _plan_prompt


def _prompt() -> str:
    """Smallest realistic `_plan_prompt` call: shaped like the real call site
    (runs.py's `start_run` -> `planner.propose_beats`) with the fixture values
    tests/test_gate1.py uses for the same inputs (outline, pov)."""
    return _plan_prompt(
        outline="Marcus wakes, then explores.",
        pov="Marcus",
        omniscient_summary=None,
        canon=[],
        max_beats=24,
    )


def test_every_producible_lane_tag_appears_in_the_plan_prompt() -> None:
    # Integrity guard against tag vocabulary drift, mirroring test_router.py's
    # test_every_producible_lane_tag_is_routable. Every lane tag the scene-packet producer can
    # stamp onto a Beat (beats._LANE_TAGS) must be named in planner._plan_prompt's hardcoded
    # tag list, or the LLM is never told that tag exists and can never propose it. Passes today
    # because the two vocabularies are in sync; fails the moment _LANE_TAGS grows (or renames) a
    # tag this hardcoded prompt string doesn't mention.
    from dominion.workers.scene_packet.beats import _LANE_TAGS

    prompt = _prompt()
    for tag in _LANE_TAGS:
        assert f'"{tag}"' in prompt, f"lane tag {tag!r} is missing from planner._plan_prompt's hardcoded tag list"
