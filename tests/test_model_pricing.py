"""Guards on the per-model rate table behind every dollar figure the Desk shows.

`pricing_for_model` answers for ANY model id by falling through to claude-sonnet-4, so a model the app
offers but never prices is invisible: the cost column keeps rendering, quietly wrong. Until 2026-09-18
every `gpt-*` id was in exactly that state, and `gpt-5.6-luna` — the default for a dozen roles — was
billed on screen at roughly fifteen times its real rate. These tests make that state fail instead.
"""

from __future__ import annotations

from dominion.shared.agent_registry import PROVIDER_TIERS
from dominion.shared.config import Settings
from dominion.shared.model_pricing import (
    _MODEL_PRICING,
    OPENAI_LONG_CONTEXT_THRESHOLD_TOKENS,
    ModelPricing,
    estimate_call_cost_usd,
    pricing_for_model,
)

_FALLBACK = _MODEL_PRICING["claude-sonnet-4"]


def _cost_of_a_big_call(model: str) -> float:
    """USD for 1M input + 100k output tokens on `model`."""
    return round(estimate_call_cost_usd(model=model, input_tokens=1_000_000, output_tokens=100_000), 4)


def _explicit_key(model: str) -> str | None:
    """The table key that matches `model`, or None when only the fallback would answer."""
    return next((prefix for prefix in _MODEL_PRICING if prefix in model.lower()), None)


def test_every_offered_model_is_priced_explicitly():
    """Every id the Settings picker can select has its own rates, not the fallback's."""
    offered = {model for tiers in PROVIDER_TIERS.values() for model in tiers.values()}
    unpriced = sorted(m for m in offered if _explicit_key(m) is None)
    assert unpriced == [], f"offered models with no rates of their own (they would bill at Sonnet rates): {unpriced}"


def test_fallback_still_answers_for_an_unknown_model():
    """The fallback stays: a retired or hand-typed model id must still produce a number, not an error."""
    assert pricing_for_model("some-model-nobody-priced") == _FALLBACK


def test_gpt_rates_are_the_owner_supplied_ones_not_the_sonnet_fallback():
    luna = pricing_for_model("gpt-5.6-luna")
    assert luna == ModelPricing(input=0.20, output=1.20, cache_write=0.25, cache_read=0.02)
    assert luna != _FALLBACK
    # The concrete consequence, stated in dollars: 1M in + 100k out on the default model, priced right
    # and priced by the old fallback.
    assert _cost_of_a_big_call("gpt-5.6-luna") == 0.32
    assert _cost_of_a_big_call("claude-sonnet-4") == 4.5


def test_no_configured_budget_reaches_openai_long_context_pricing():
    """The gpt rates are the standard tier. If a budget ever crosses OpenAI's long-context threshold,
    the table starts under-pricing real calls — so that change has to fail here first."""
    # Per-CALL sizes only. `read_through_run_token_ceiling` and friends cap a whole run's cumulative
    # spend across many calls, which says nothing about any single call's context, so they are excluded.
    defaults = Settings.model_fields
    budgets = {
        name: field.default
        for name, field in defaults.items()
        if isinstance(field.default, int)
        and any(part in name for part in ("input_budget", "token_budget", "context_window"))
        and "run_token" not in name
    }
    assert budgets, "no budget settings found — the guard would pass vacuously"
    over = {n: v for n, v in budgets.items() if v >= OPENAI_LONG_CONTEXT_THRESHOLD_TOKENS}
    assert over == {}, f"these budgets reach OpenAI's >272k price tier, which this table does not model: {over}"
