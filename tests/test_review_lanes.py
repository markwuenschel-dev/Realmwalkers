"""Tag-gated review-lane unit tests — mock the LLM (DESIGN §6, OPEN-8). Like the always-on advisory
reviewers, the combat/sensory/dialogue lanes never block, never emit HARD, never raise on malformed
output, and stay silent (no LLM call) on prose below the assess-able floor."""

from __future__ import annotations

import uuid

import pytest

from dominion.shared.enums import Severity
from dominion.workers import llm
from dominion.workers.budget import TokenBudget, Usage
from dominion.workers.context import SceneContext
from dominion.workers.reviewers.lane import combat_reviewer, dialogue_reviewer, sensory_reviewer

_LANES = [
    pytest.param(combat_reviewer, "combat", id="combat"),
    pytest.param(sensory_reviewer, "sensory", id="sensory"),
    pytest.param(dialogue_reviewer, "dialogue", id="dialogue"),
]
_LONG_PROSE = "word " * 400  # > _MIN_PROSE_CHARS (1000) so the lane actually runs


def _ctx(**overrides: object) -> SceneContext:
    base: dict[str, object] = dict(
        book_id=uuid.uuid4(),
        chapter_id=uuid.uuid4(),
        pov="Marcus",
        scene_no=1,
        tags=[],
        characters_present=["Marcus"],
        beat_text="A duel on the rampart.",
        expected_state_changes=None,
        knowledge_injections=[],
        voice_spec=None,
        budget=TokenBudget(max_tokens=40_000),
    )
    base.update(overrides)
    return SceneContext(**base)  # type: ignore[arg-type]


def _mock(monkeypatch, response: str) -> dict[str, int]:
    calls = {"n": 0}

    async def fake_complete(**kwargs):
        calls["n"] += 1
        kwargs["budget"].charge(Usage(5, 5))
        return response, Usage(5, 5)

    monkeypatch.setattr(llm, "complete", fake_complete)
    return calls


@pytest.mark.parametrize("reviewer,name", _LANES)
async def test_lane_flags_on_long_prose(monkeypatch, reviewer, name):
    _mock(monkeypatch, '[{"severity": "warn", "note": "this dimension falters", "quote": "a flat line"}]')
    flags = await reviewer.review(_LONG_PROSE, _ctx())
    assert len(flags) == 1
    assert flags[0].reviewer == name and flags[0].severity == Severity.WARN
    assert flags[0].payload == {"quote": "a flat line"}


@pytest.mark.parametrize("reviewer,name", _LANES)
async def test_lane_noops_on_short_prose(monkeypatch, reviewer, name):
    calls = _mock(monkeypatch, "[]")
    assert await reviewer.review("Too short to assess.", _ctx()) == []
    assert calls["n"] == 0  # below the floor -> no LLM call, no tokens spent


@pytest.mark.parametrize("reviewer,name", _LANES)
async def test_lane_never_emits_hard(monkeypatch, reviewer, name):
    _mock(monkeypatch, '[{"severity": "hard", "note": "trying to escalate"}]')
    flags = await reviewer.review(_LONG_PROSE, _ctx())
    assert flags and all(f.severity != Severity.HARD for f in flags)  # clamped to advisory


@pytest.mark.parametrize("reviewer,name", _LANES)
async def test_lane_tolerates_garbage(monkeypatch, reviewer, name):
    _mock(monkeypatch, "not json at all")
    assert await reviewer.review(_LONG_PROSE, _ctx()) == []
