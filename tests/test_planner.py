"""Gate-1 planner unit tests — mock the LLM, so no network or API key (DESIGN §4, §8)."""
from __future__ import annotations

from dominion.workers import llm, planner
from dominion.workers.budget import Usage


def _mock(monkeypatch, response: str) -> dict[str, object]:
    captured: dict[str, object] = {}

    async def fake_complete(**kwargs):
        captured.update(kwargs)
        kwargs["budget"].charge(Usage(10, 10))
        return response, Usage(10, 10)

    monkeypatch.setattr(llm, "complete", fake_complete)
    return captured


async def test_propose_beats_parses_and_normalizes(monkeypatch):
    response = (
        '[{"scene_no": 1, "beat_text": "Soren wakes in the Realm.", '
        '"characters_present": ["Soren"], "tags": ["dialogue"], '
        '"expected_state_changes": {"Soren": {"level": "+1"}}, '
        '"knowledge_injections": ["the interface is not the truth"]},'
        '{"scene_no": 2, "beat_text": "He tests the eyes.", "characters_present": ["Soren"], '
        '"tags": [], "expected_state_changes": null, "knowledge_injections": []}]'
    )
    captured = _mock(monkeypatch, response)
    beats = await planner.propose_beats(outline="Soren wakes, then tests his eyes.", pov="Soren")

    assert [b["scene_no"] for b in beats] == [1, 2]
    assert beats[0]["beat_text"] == "Soren wakes in the Realm."
    assert beats[0]["tags"] == ["dialogue"]
    assert beats[0]["expected_state_changes"] == {"Soren": {"level": "+1"}}
    assert beats[1]["expected_state_changes"] is None
    assert "Soren" in str(captured["user"])             # POV + outline carried into the prompt


async def test_propose_beats_tolerates_fences_and_drops_unusable(monkeypatch):
    # Fenced output + one item with no beat_text (dropped) + one malformed scene_no (renumbered).
    response = (
        "```json\n"
        '[{"beat_text": "", "characters_present": []},'
        '{"scene_no": "x", "beat_text": "A real beat."}]\n'
        "```"
    )
    _mock(monkeypatch, response)
    beats = await planner.propose_beats(outline="something happens", pov="Serra")
    assert len(beats) == 1
    assert beats[0]["beat_text"] == "A real beat."
    assert beats[0]["scene_no"] == 2              # fell back to its index when scene_no was unparseable


async def test_propose_beats_returns_empty_on_garbage(monkeypatch):
    _mock(monkeypatch, "I could not produce JSON, sorry.")
    assert await planner.propose_beats(outline="x", pov="Soren") == []


async def test_propose_beats_empty_outline_skips_model(monkeypatch):
    called = {"n": 0}

    async def boom(**kwargs):
        called["n"] += 1
        return "[]", Usage(0, 0)

    monkeypatch.setattr(llm, "complete", boom)
    assert await planner.propose_beats(outline="   ", pov="Soren") == []
    assert called["n"] == 0               # no outline -> no plan-call
