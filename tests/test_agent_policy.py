"""Unit tests for agent_policy quality-level resolution (no DB required)."""

from __future__ import annotations

from dominion.shared.agent_policy import quality_effort, resolve_policy
from dominion.shared.agent_registry import AGENT_BY_KEY


def test_quality_level_maps_to_temperature_and_effort():
    # A preset's quality_level resolves to BOTH a temperature (older/Haiku + OpenAI/xAI) and an effort
    # (Anthropic flagship models); llm.complete then sends whichever the target model accepts.
    agent = AGENT_BY_KEY["draft_model"]
    fast = resolve_policy(agent, {"quality_level": "fast"})
    balanced = resolve_policy(agent, {"quality_level": "balanced"})
    quality = resolve_policy(agent, {"quality_level": "quality"})

    assert (fast.temperature, fast.effort) == (0.9, "low")
    assert (balanced.temperature, balanced.effort) == (0.7, "medium")
    assert (quality.temperature, quality.effort) == (0.5, "high")


def test_default_and_invalid_quality_level_fall_back_to_balanced():
    agent = AGENT_BY_KEY["draft_model"]
    assert resolve_policy(agent, None).effort == "medium"
    assert resolve_policy(agent, {"quality_level": "bogus"}).effort == "medium"


def test_quality_effort_accessor_reads_the_runtime_policy():
    # Default (no persisted override) is balanced -> medium.
    assert quality_effort("draft_model") == "medium"
