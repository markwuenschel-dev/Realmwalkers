"""Approximate USD cost for persisted LLM-call telemetry (Anthropic-style weighting)."""

from __future__ import annotations

from dominion.shared.model_pricing import ModelPricing, estimate_call_cost_usd, pricing_for_model
from dominion.shared.models import LlmCall

__all__ = ["ModelPricing", "estimate_call_cost_usd", "estimate_calls_cost_usd", "estimate_cache_savings_usd"]


def estimate_calls_cost_usd(calls: list[LlmCall]) -> float:
    return sum(
        estimate_call_cost_usd(model=c.model, input_tokens=c.input_tokens, output_tokens=c.output_tokens)
        + c.cache_creation_tokens * pricing_for_model(c.model).cache_write / 1_000_000
        + c.cache_read_tokens * pricing_for_model(c.model).cache_read / 1_000_000
        for c in calls
    )


def estimate_cache_savings_usd(calls: list[LlmCall]) -> float:
    """Dollars avoided by cache reads vs paying full input rate."""
    saved = 0.0
    for c in calls:
        if c.cache_read_tokens <= 0:
            continue
        tier = pricing_for_model(c.model)
        full = c.cache_read_tokens * tier.input / 1_000_000
        actual = c.cache_read_tokens * tier.cache_read / 1_000_000
        saved += max(0.0, full - actual)
    return saved
