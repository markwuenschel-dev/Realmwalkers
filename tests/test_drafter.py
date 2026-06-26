"""Drafter unit tests — mock the LLM, so no network or API key (DESIGN §4)."""
from __future__ import annotations

import uuid

from dominion.shared.config import settings
from dominion.workers import llm
from dominion.workers.budget import TokenBudget, Usage
from dominion.workers.context import SceneContext
from dominion.workers.specialists.drafter import drafter


def _ctx(**overrides: object) -> SceneContext:
    base: dict[str, object] = dict(
        book_id=uuid.uuid4(), chapter_id=uuid.uuid4(), pov="Marcus", scene_no=1,
        tags=[], characters_present=["Marcus"], beat_text="Marcus tests the limits of his eyes.",
        expected_state_changes=None, knowledge_injections=[], voice_spec="Terse, sensory, wry.",
        budget=TokenBudget(max_tokens=40_000),
    )
    base.update(overrides)
    return SceneContext(**base)  # type: ignore[arg-type]


async def test_drafter_calls_model_and_returns_stripped_prose(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_complete(**kwargs):
        captured.update(kwargs)
        kwargs["budget"].charge(Usage(100, 250))
        return "  Marcus opened his eyes to a humming sky.  ", Usage(100, 250)

    monkeypatch.setattr(llm, "complete", fake_complete)
    ctx = _ctx()
    out = await drafter.run(None, ctx)

    assert out == "Marcus opened his eyes to a humming sky."        # leading/trailing space stripped
    assert captured["model"] == settings.draft_model               # the configured draft model
    assert "Marcus" in captured["system"]                           # POV in the voice/system prompt
    assert "Terse, sensory, wry." in captured["system"]            # voice_spec carried through
    assert "Marcus tests the limits of his eyes." in captured["user"]  # the beat in the user prompt
    assert ctx.budget.used == 350                                  # usage charged to the job budget


async def test_drafter_includes_phase2_context_when_present(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_complete(**kwargs):
        captured.update(kwargs)
        kwargs["budget"].charge(Usage(10, 10))
        return "prose", Usage(10, 10)

    monkeypatch.setattr(llm, "complete", fake_complete)
    ctx = _ctx(
        canon=["The Eyes of Meszkhal perceive spectral seams."],
        pov_summary="Marcus has just woken in the Realm.",
        knowledge_injections=["The interface is not the truth of the place."],
    )
    await drafter.run(None, ctx)

    # canon + pov_summary are stable and go into the cached user_prefix; knowledge_injections
    # are per-call volatile and stay in the main user message.
    user_prefix = captured["user_prefix"]
    assert "Eyes of Meszkhal" in user_prefix
    assert "has just woken" in user_prefix
    assert "not the truth of the place" in captured["user"]


async def test_drafter_system_prompt_carries_interiority_and_litrpg_craft_rules(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_complete(**kwargs):
        captured.update(kwargs)
        kwargs["budget"].charge(Usage(10, 10))
        return "prose", Usage(10, 10)

    monkeypatch.setattr(llm, "complete", fake_complete)
    await drafter.run(None, _ctx())

    system = captured["system"]
    # Interiority craft rule: analytical POV's read of the scene is first-class, humor is seasoning.
    assert "interiority is the POV's primary mode" in system
    assert "Humor is seasoning, not the default register" in system
    assert "does not speak his reasoning aloud as a bit" in system
    # LitRPG stat-window rule: the model EMITS a ```stat``` marker and must NOT draw boxes itself.
    assert "This is LitRPG" in system
    assert "```stat" in system
    assert "Do NOT draw borders" in system


async def test_drafter_injects_dialogue_rules_as_authoritative(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_complete(**kwargs):
        captured.update(kwargs)
        kwargs["budget"].charge(Usage(10, 10))
        return "prose", Usage(10, 10)

    monkeypatch.setattr(llm, "complete", fake_complete)
    ctx = _ctx(dialogue_rules="New speaker = new paragraph. Always.")
    await drafter.run(None, ctx)

    system = captured["system"]
    assert "New speaker = new paragraph. Always." in system   # the rules are loaded into the prompt
    assert "AUTHORITATIVE" in system                          # marked as the source of truth
    assert system.index("Terse, sensory, wry.") < system.index("New speaker")  # rules win (placed after voice)
