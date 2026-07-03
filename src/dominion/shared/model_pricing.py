"""Static per-model pricing and chapter-cost estimates for the Agent Operations panel."""

from __future__ import annotations

from dataclasses import dataclass

from dominion.shared.agent_registry import AGENTS, tier_of

# Typical (input, output) tokens per call — conservative planning numbers, not telemetry averages.
TYPICAL_CALL_TOKENS: dict[str, tuple[int, int]] = {
    "draft_model": (12_000, 5_000),
    "review_model": (4_000, 800),
    "enrich_model": (6_000, 2_000),
    "packet_author_model": (16_000, 8_000),
    "packet_qa_model": (8_000, 1_500),
    "scene_packet_author_model": (10_000, 4_000),
    "scene_packet_qa_model": (6_000, 1_200),
}

# Rough wall-clock seconds per call by tier (planning estimate).
TIER_LATENCY_SEC: dict[str, int] = {"haiku": 4, "sonnet": 10, "opus": 22}


@dataclass(frozen=True)
class ModelPricing:
    """Per-million-token rates in USD (Anthropic-style weighting)."""

    input: float
    output: float
    cache_write: float
    cache_read: float


_MODEL_PRICING: dict[str, ModelPricing] = {
    "claude-opus-4": ModelPricing(input=15.0, output=75.0, cache_write=18.75, cache_read=1.50),
    # Standard Sonnet 5 rates ($3/$15); intro pricing ($2/$10) runs through 2026-08-31, so estimate
    # at the durable standard rate. Keyed separately from claude-sonnet-4 because the prefix match
    # ("claude-sonnet-4" in id) does not catch "claude-sonnet-5".
    "claude-sonnet-5": ModelPricing(input=3.0, output=15.0, cache_write=3.75, cache_read=0.30),
    "claude-sonnet-4": ModelPricing(input=3.0, output=15.0, cache_write=3.75, cache_read=0.30),
    "claude-haiku-4": ModelPricing(input=0.80, output=4.0, cache_write=1.0, cache_read=0.08),
    # Gemini 3.5 Flash / 3.1 Pro Preview standard paid-tier text pricing from Google's Gemini Developer API pricing
    # page. The panel's chapter estimates only consume input/output today, but cache fields are filled so
    # the model table stays internally complete.
    "gemini-3.5-flash": ModelPricing(input=0.30, output=2.50, cache_write=0.03, cache_read=0.03),
    "gemini-3.1-pro-preview": ModelPricing(input=1.25, output=10.0, cache_write=0.25, cache_read=0.25),
}


def pricing_for_model(model: str) -> ModelPricing:
    m = model.lower()
    for prefix, tier in sorted(_MODEL_PRICING.items(), key=lambda kv: len(kv[0]), reverse=True):
        if prefix in m:
            return tier
    return _MODEL_PRICING["claude-sonnet-4"]


def estimate_call_cost_usd(*, model: str, input_tokens: int, output_tokens: int) -> float:
    tier = pricing_for_model(model)
    return (input_tokens * tier.input + output_tokens * tier.output) / 1_000_000


def estimate_agent_chapter_usd(setting_key: str, model: str) -> tuple[float, int]:
    """Return (estimated USD per chapter, estimated latency seconds per chapter) for one agent role."""
    agent = next((a for a in AGENTS if a.setting_key == setting_key), None)
    if agent is None:
        return 0.0, 0
    n = agent.estimate.typical_calls_per_chapter
    inp, out = TYPICAL_CALL_TOKENS.get(setting_key, (4_000, 1_000))
    per_call = estimate_call_cost_usd(model=model, input_tokens=inp, output_tokens=out)
    tier = tier_of(model) or "sonnet"
    latency = TIER_LATENCY_SEC.get(tier, 10) * n
    return round(per_call * n, 3), latency


def estimate_pipeline_chapter_usd(agent_models: dict[str, str]) -> tuple[float, float, int]:
    """Sum per-agent estimates. Returns (usd_total, latency_sec_max_parallel, total_calls)."""
    total_usd = 0.0
    max_latency = 0
    total_calls = 0
    for agent in AGENTS:
        model = agent_models.get(agent.setting_key, "")
        if not model:
            continue
        usd, latency = estimate_agent_chapter_usd(agent.setting_key, model)
        total_usd += usd
        max_latency = max(max_latency, latency)
        total_calls += agent.estimate.typical_calls_per_chapter
    # Pipeline runs many agents sequentially within a chapter — use sum of latencies as upper bound.
    seq_latency = sum(estimate_agent_chapter_usd(a.setting_key, agent_models.get(a.setting_key, ""))[1] for a in AGENTS)
    return round(total_usd, 2), round(total_usd * 0.85, 2), seq_latency
