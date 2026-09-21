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

# Rough wall-clock seconds per call by tier (planning estimate). `fable` is the frontier band: slower than opus, which
# is the trade it exists to offer.
TIER_LATENCY_SEC: dict[str, int] = {"haiku": 4, "sonnet": 10, "opus": 22, "fable": 40}


@dataclass(frozen=True)
class ModelPricing:
    """Per-million-token rates in USD (Anthropic-style weighting)."""

    input: float
    output: float
    cache_write: float
    cache_read: float


# This table is a SUPERSET of the live catalog (`PROVIDER_TIERS`), on purpose. Costs are computed at
# READ time from the model id stored on each `llm_calls` row, so a model retired from the picker must
# keep its entry forever or its historical spend silently re-prices at the fallback rate. Entries below
# marked "retired from the catalog" exist only to keep old rows honest — do not delete them.
_MODEL_PRICING: dict[str, ModelPricing] = {
    # Fable 5.1 — the frontier tier, owner-supplied 2026-09-21: $10 in, $50 out, $0.25 cache read,
    # 1M context. Twice Opus 5's rate, which matches Anthropic's own framing of it as the step up for
    # the hardest long-running work. No cache-WRITE rate was supplied; 12.50 is 1.25x input, the ratio
    # every other Anthropic row in this table already uses (opus 5.00/6.25, sonnet 3.00/3.75,
    # haiku 0.80/1.00), so it is consistent rather than invented.
    "claude-fable-5-1": ModelPricing(input=10.0, output=50.0, cache_write=12.50, cache_read=0.25),
    # The prior Fable generation. Not in the catalog, but `_ANTHROPIC_EFFORT_MODELS` already names
    # it, so the app considers it a real id — and without this line it fell through to the Sonnet
    # default at $3/$15, under-pricing it 3.3x. Same defect as the gpt-* family before #319.
    "claude-fable-5": ModelPricing(input=10.0, output=50.0, cache_write=12.50, cache_read=0.50),
    # Opus 5 / Opus 4.8 share one rate ($5/$25); `claude-opus-latest` is the floating alias and resolves
    # to the Opus 5 generation today. Keyed separately from "claude-opus-4" because the substring match
    # ("claude-opus-4" in id) does not catch either "claude-opus-5" or "claude-opus-latest".
    "claude-opus-latest": ModelPricing(input=5.0, output=25.0, cache_write=6.25, cache_read=0.50),
    "claude-opus-5": ModelPricing(input=5.0, output=25.0, cache_write=6.25, cache_read=0.50),
    "claude-opus-4-8": ModelPricing(input=5.0, output=25.0, cache_write=6.25, cache_read=0.50),
    # Opus 4.7 and older still bill at the pre-4.8 flagship rate. Longest-prefix matching puts the
    # "claude-opus-4-8" entry above ahead of this one, so 4.8 is not caught here.
    "claude-opus-4": ModelPricing(input=15.0, output=75.0, cache_write=18.75, cache_read=1.50),
    # Standard Sonnet 5 rates ($3/$15); intro pricing ($2/$10) runs through 2026-08-31, so estimate
    # at the durable standard rate. Keyed separately from claude-sonnet-4 because the prefix match
    # ("claude-sonnet-4" in id) does not catch "claude-sonnet-5".
    "claude-sonnet-5": ModelPricing(input=3.0, output=15.0, cache_write=3.75, cache_read=0.30),
    "claude-sonnet-4": ModelPricing(input=3.0, output=15.0, cache_write=3.75, cache_read=0.30),
    # Retired from the catalog 2026-09-20. Kept deliberately: this book's telemetry already holds 59
    # claude-haiku-4-5 calls, and deleting this entry would re-price every one of them at the fallback.
    "claude-haiku-4": ModelPricing(input=0.80, output=4.0, cache_write=1.0, cache_read=0.08),
    # Gemini standard paid-tier text pricing from Google's Gemini Developer API pricing page. The panel's
    # chapter estimates only consume input/output today, but cache fields are filled so the model table
    # stays internally complete. Google bills context caching as a read rate plus per-hour storage rather
    # than a write rate, so cache_write mirrors cache_read for these entries.
    #
    # gemini-3.8-flash: $0.75 in / $3.75 out / $0.075 cache read, verified 2026-09-20 against Google's
    # pricing page and corroborated by three trackers. This is the INTRODUCTORY rate and it runs only
    # through 2026-12-31 — on 2027-01-01 both halves double to $1.50 / $7.50. Telemetry prices each row
    # at read time, so the rate below is the one actually billed today; revisit this entry in January or
    # every Gemini row in the book's history silently re-prices at half what it cost.
    "gemini-3.8-flash": ModelPricing(input=0.75, output=3.75, cache_write=0.075, cache_read=0.075),
    # Retired from the catalog 2026-09-20 (replaced by 3.8 Flash); kept so existing rows price correctly.
    "gemini-3.5-flash": ModelPricing(input=0.30, output=2.50, cache_write=0.03, cache_read=0.03),
    "gemini-3.1-pro-preview": ModelPricing(input=1.25, output=10.0, cache_write=0.25, cache_read=0.25),
    # OpenAI rates supplied by the owner on 2026-09-18, STANDARD tier. OpenAI charges a second, higher
    # tier above a 272k-token context; nothing here can reach it — the largest configured context budget
    # is `scene_packet_context_window_budget` (180k), and `tests/test_model_pricing.py` fails if any
    # budget setting crosses the threshold, so this table cannot silently under-price a long call.
    # Until 2026-09-18 every one of these fell through to the claude-sonnet-4 default below, which
    # over-stated gpt-5.6-luna — the default model for a dozen roles — by roughly fifteen times.
    "gpt-6-astra": ModelPricing(input=10.0, output=50.0, cache_write=12.50, cache_read=1.00),
    "gpt-5.6-sol": ModelPricing(input=4.0, output=20.0, cache_write=5.00, cache_read=0.40),
    "gpt-5.6-terra": ModelPricing(input=2.0, output=12.0, cache_write=2.50, cache_read=0.20),
    "gpt-5.6-luna": ModelPricing(input=0.20, output=1.20, cache_write=0.25, cache_read=0.02),
    # Grok 4.6 (flagship), owner-supplied 2026-09-18: $2.00 in, $6.00 out, $0.50 cached input. xAI
    # publishes no separate cache-WRITE rate, so it is priced at the input rate — an assumption, and the
    # only one in this table. It moves no number today: no grok row exists in telemetry.
    "grok-4.6": ModelPricing(input=2.0, output=6.0, cache_write=2.0, cache_read=0.50),
    # Kimi K3 (Moonshot flagship), owner-supplied 2026-09-20 and confirmed against the Kimi platform
    # docs: $3.00 in, $15.00 out, $0.30 cache hit. Moonshot publishes no separate cache-WRITE rate, so
    # it is priced at the input rate — the same assumption the grok entry above carries.
    "kimi-k3": ModelPricing(input=3.0, output=15.0, cache_write=3.0, cache_read=0.30),
    # Meta Muse Spark 1.3, verified 2026-09-20 against Meta Model API's pricing page. ONE model, TWO
    # tiers, and the tier is chosen by the model id:
    #   muse-spark-1.3              $1.25 in / $4.25 out / $0.15 cached — prompts NOT used for training.
    #   muse-spark-1.3-contributor  $0.10 in / $0.20 out / $0.002 cached — 12.5x cheaper IN EXCHANGE FOR
    #                               permission to train future Meta models on the prompts and completions.
    # Anything this app sends carries manuscript prose, so the id chosen here decides whether the book
    # becomes training data. Both are priced so switching between them is a one-word edit in the catalog.
    # Meta publishes no cache-write rate; cache_write mirrors cache_read, as for the Gemini entries.
    "muse-spark-1.3-contributor": ModelPricing(input=0.10, output=0.20, cache_write=0.002, cache_read=0.002),
    "muse-spark-1.3": ModelPricing(input=1.25, output=4.25, cache_write=0.15, cache_read=0.15),
}

# OpenAI's higher price tier starts above this context size. See the note above `gpt-6-astra`.
OPENAI_LONG_CONTEXT_THRESHOLD_TOKENS = 272_000


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
