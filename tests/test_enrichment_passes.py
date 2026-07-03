"""Enrichment-pass unit tests — mock the LLM, so no network or API key (DESIGN §5-6).

Each pass TRANSFORMS the drafted spine, deepening one dimension while preserving everything else
(POV, events, and the ```stat``` marker blocks the pipeline renders later). Failures fail SOFT: a
`BudgetExceeded` propagates (the pipeline keeps the spine, aborts the rest), and any other failure —
including empty/degenerate output — becomes a `PassError` so the spine still lands, flagged (OPEN-10).
"""

from __future__ import annotations

import uuid

import pytest

from dominion.workers import llm
from dominion.workers.budget import BudgetExceeded, TokenBudget, Usage
from dominion.workers.context import SceneContext
from dominion.workers.specialists.base import PassError
from dominion.workers.specialists.enrich import combat_pass, dialogue_pass, sensory_pass

_ALL_PASSES = [combat_pass, dialogue_pass, sensory_pass]
_STAT_BLOCK = "```stat\nLEVEL UP\nPerception: 15\n```"


def _ctx(**overrides: object) -> SceneContext:
    base: dict[str, object] = dict(
        book_id=uuid.uuid4(),
        chapter_id=uuid.uuid4(),
        pov="Marcus",
        scene_no=1,
        tags=[],
        characters_present=["Marcus"],
        beat_text="Marcus tests the limits of his eyes.",
        expected_state_changes=None,
        knowledge_injections=[],
        voice_spec=None,
        budget=TokenBudget(max_tokens=40_000),
    )
    base.update(overrides)
    return SceneContext(**base)  # type: ignore[arg-type]


def _mock_return(monkeypatch, text: str) -> dict[str, object]:
    captured: dict[str, object] = {}

    async def fake_complete(**kwargs):
        captured.update(kwargs)
        kwargs["budget"].charge(Usage(100, 250))
        return text, Usage(100, 250)

    monkeypatch.setattr(llm, "complete", fake_complete)
    return captured


def _mock_raise(monkeypatch, exc: BaseException) -> None:
    async def fake_complete(**kwargs):
        raise exc

    monkeypatch.setattr(llm, "complete", fake_complete)


@pytest.mark.parametrize("enrichment_pass", _ALL_PASSES)
async def test_pass_preserves_stat_block(monkeypatch, enrichment_pass):
    source = "He raised his hand and the world resolved into data.\n\n" + _STAT_BLOCK + "\n\nThen it faded."
    captured = _mock_return(monkeypatch, "Deepened opening.\n\n" + _STAT_BLOCK + "\n\nDeepened close.")
    out = await enrichment_pass.run(source, _ctx())

    assert _STAT_BLOCK in captured["user"]  # the block is given to the model to preserve
    assert _STAT_BLOCK in out  # ...and survives in the returned prose, fences and all


@pytest.mark.parametrize("enrichment_pass", _ALL_PASSES)
async def test_pass_raises_passerror_on_empty_output(monkeypatch, enrichment_pass):
    _mock_return(monkeypatch, "   ")
    with pytest.raises(PassError):
        await enrichment_pass.run("A drafted spine.", _ctx())


@pytest.mark.parametrize("enrichment_pass", _ALL_PASSES)
async def test_pass_propagates_budget_exceeded(monkeypatch, enrichment_pass):
    _mock_raise(monkeypatch, BudgetExceeded("over"))
    # BudgetExceeded must NOT be wrapped in PassError: the pipeline keeps the spine and aborts the rest.
    with pytest.raises(BudgetExceeded):
        await enrichment_pass.run("A drafted spine.", _ctx())


@pytest.mark.parametrize("enrichment_pass", _ALL_PASSES)
async def test_pass_wraps_other_errors_as_passerror(monkeypatch, enrichment_pass):
    _mock_raise(monkeypatch, ValueError("boom"))
    with pytest.raises(PassError) as excinfo:
        await enrichment_pass.run("A drafted spine.", _ctx())
    assert isinstance(excinfo.value.__cause__, ValueError)  # original cause preserved


async def test_dialogue_pass_injects_dialogue_rules_as_authoritative(monkeypatch):
    captured = _mock_return(monkeypatch, "Deepened dialogue.")
    await dialogue_pass.run("A drafted spine.", _ctx(dialogue_rules="New speaker = new paragraph. Always."))

    system = captured["system"]
    assert "New speaker = new paragraph. Always." in system  # the rules are loaded into the prompt
    assert "AUTHORITATIVE" in system  # ...and marked as the source of truth
