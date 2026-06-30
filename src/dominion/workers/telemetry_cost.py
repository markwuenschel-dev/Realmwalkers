"""Approximate USD cost for persisted LLM-call telemetry (Anthropic-style weighting)."""

from __future__ import annotations

from dataclasses import dataclass

from dominion.shared.models import LlmCall


@dataclass(frozen=True)
class ModelPricing:
    """Per-million-token rates in USD."""

    input: float
    output: float
    cache_write: float
    cache_read: float


# Prefix match: longer keys first when resolving model id strings.
_MODEL_PRICING: dict[str, ModelPricing] = {
    "claude-opus-4": ModelPricing(input=15.0, output=75.0, cache_write=18.75, cache_read=1.50),
    "claude-sonnet-4": ModelPricing(input=3.0, output=15.0, cache_write=3.75, cache_read=0.30),
    "claude-haiku-4": ModelPricing(input=0.80, output=4.0, cache_write=1.0, cache_read=0.08),
}


def _pricing_for_model(model: str) -> ModelPricing:
    m = model.lower()
    for prefix, tier in sorted(_MODEL_PRICING.items(), key=lambda kv: len(kv[0]), reverse=True):
        if prefix in m:
            return tier
    # Conservative default (Sonnet-tier) for unknown models.
    return _MODEL_PRICING["claude-sonnet-4"]


def estimate_call_cost_usd(call: LlmCall) -> float:
    tier = _pricing_for_model(call.model)
    return (
        call.input_tokens * tier.input
        + call.output_tokens * tier.output
        + call.cache_creation_tokens * tier.cache_write
        + call.cache_read_tokens * tier.cache_read
    ) / 1_000_000


def estimate_calls_cost_usd(calls: list[LlmCall]) -> float:
    return sum(estimate_call_cost_usd(c) for c in calls)


def estimate_cache_savings_usd(calls: list[LlmCall]) -> float:
    """Dollars avoided by cache reads vs paying full input rate."""
    saved = 0.0
    for c in calls:
        if c.cache_read_tokens <= 0:
            continue
        tier = _pricing_for_model(c.model)
        full = c.cache_read_tokens * tier.input / 1_000_000
        actual = c.cache_read_tokens * tier.cache_read / 1_000_000
        saved += max(0.0, full - actual)
    return saved
