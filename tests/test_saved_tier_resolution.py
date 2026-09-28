"""A saved (tier, provider) pair must survive its slot being emptied by a catalog change.

Fallback policies are persisted by TIER, not model id. When GPT-6 replaced GPT-5.6 on 2026-09-28,
OpenAI lost its opus slot, and every path that re-applies a saved tier did an exact `model_for_tier`
lookup and turned the resulting None into "" — which switches the role's fallback OFF at the next
restart, with nothing in the log and nothing on the Desk. These pin the nearest-slot behavior instead.
"""

from __future__ import annotations

import pytest

from dominion.shared import agent_ops
from dominion.shared.agent_policy import load_runtime_policies
from dominion.shared.agent_registry import (
    AGENTS,
    FALLBACK_ATTR,
    model_for_tier,
    nearest_model_for_tier,
    tier_of,
)
from dominion.shared.config import Settings, settings
from dominion.shared.models import AgentPolicyOverride
from dominion.workers.length.guard import _length_model


def test_a_saved_openai_opus_tier_resolves_to_sol_not_to_nothing():
    assert model_for_tier("opus", "openai") is None, "the gap this file guards has been filled; re-check it"
    assert nearest_model_for_tier("opus", "openai") == "gpt-6-sol"
    # Never rounded UP into the frontier band, even though fable is just as near.
    assert nearest_model_for_tier("opus", "openai") != model_for_tier("fable", "openai")
    # Exact slots are untouched.
    assert nearest_model_for_tier("haiku", "openai") == "gpt-6-luna"
    assert nearest_model_for_tier("fable", "openai") == "gpt-6-astra"


def test_no_role_defaults_to_a_fallback_its_own_policy_blocks():
    """A default fallback in a tier the role will never fall back to is displayed but never fires."""
    defaults = Settings.model_fields
    blocked = {}
    for agent in AGENTS:
        attr = FALLBACK_ATTR.get(agent.setting_key)
        if not attr or attr not in defaults:
            continue
        fallback = defaults[attr].default
        if fallback and tier_of(fallback) in agent.never_fallback_tiers:
            blocked[agent.setting_key] = fallback
    assert blocked == {}, f"default fallbacks the role's never_fallback_tiers blocks: {blocked}"


def test_length_guard_takes_the_nearest_slot_when_the_drafters_provider_lacks_the_tier(
    monkeypatch: pytest.MonkeyPatch,
):
    # An opus-tier length model with an OpenAI drafter: an exact lookup found no OpenAI opus and
    # handed back the Anthropic model — the cross-provider call this function exists to prevent.
    monkeypatch.setattr(settings, "draft_model", "gpt-6-luna")
    assert _length_model("claude-opus-latest") == "gpt-6-sol"


async def test_startup_keeps_a_saved_openai_opus_fallback_switched_on(db_factory, monkeypatch: pytest.MonkeyPatch):
    attr = FALLBACK_ATTR["packet_author_model"]
    monkeypatch.setattr(settings, attr, "sentinel-before-startup")
    async with db_factory() as session:
        session.add(
            AgentPolicyOverride(
                setting_name="packet_author_model",
                policy_json={"fallback_tier": "opus", "fallback_provider": "openai"},
            )
        )
        await session.flush()
        try:
            await agent_ops.apply_model_overrides(session)
            assert getattr(settings, attr) == "gpt-6-sol", "the saved fallback was switched off, not re-pointed"
            # The Desk shows the same model the runtime will call, not an empty fallback.
            out = agent_ops._policy_from_live(
                next(a for a in AGENTS if a.setting_key == "packet_author_model"),
                await session.get(AgentPolicyOverride, "packet_author_model"),
            )
            assert (out.fallback_model, out.fallback_provider) == ("gpt-6-sol", "openai")
        finally:
            load_runtime_policies({})
