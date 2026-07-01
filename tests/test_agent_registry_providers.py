"""Unit tests for agent_registry's provider-aware tier catalog (no DB required)."""

from __future__ import annotations

from dominion.shared.agent_registry import (
    PROVIDER_TIERS,
    TIERS,
    model_for_tier,
    provider_of,
    resolve_tier_for_provider,
    supports_effort,
    supports_temperature,
    tier_of,
)


def test_tiers_alias_stays_the_anthropic_catalog():
    assert TIERS is PROVIDER_TIERS["anthropic"]


def test_model_for_tier_defaults_to_anthropic():
    assert model_for_tier("opus") == "claude-opus-4-8"
    assert model_for_tier("haiku") == "claude-haiku-4-5"


def test_model_for_tier_respects_provider():
    assert model_for_tier("opus", "openai") == "gpt-5.5"
    assert model_for_tier("sonnet", "openai") == "gpt-5.4-mini"
    assert model_for_tier("haiku", "openai") == "gpt-5.4-nano"
    assert model_for_tier("opus", "xai") == "grok-4.3"


def test_model_for_tier_returns_none_for_unfilled_tier():
    assert model_for_tier("haiku", "xai") is None
    assert model_for_tier("sonnet", "xai") is None


def test_model_for_tier_returns_none_for_unknown_provider():
    assert model_for_tier("opus", "mistral") is None


def test_tier_of_exact_catalog_match():
    assert tier_of("gpt-5.5") == "opus"
    assert tier_of("gpt-5.4-mini") == "sonnet"
    assert tier_of("gpt-5.4-nano") == "haiku"
    assert tier_of("grok-4.3") == "opus"


def test_tier_of_legacy_substring_fallback_for_dated_anthropic_ids():
    # Predates the exact catalog -- dated ids aren't in PROVIDER_TIERS but must still resolve by family.
    assert tier_of("claude-haiku-4-5-20251001") == "haiku"


def test_tier_of_unknown_model_returns_none():
    assert tier_of("some-random-model") is None
    assert tier_of(None) is None


def test_provider_of_exact_catalog_match():
    assert provider_of("gpt-5.5") == "openai"
    assert provider_of("grok-4.3") == "xai"
    assert provider_of("claude-opus-4-8") == "anthropic"


def test_provider_of_unknown_model_defaults_to_anthropic():
    # Every existing caller/test predates multi-provider support and uses bare placeholders like "m" --
    # those must stay attributed to Anthropic, not silently misclassified.
    assert provider_of("m") == "anthropic"
    assert provider_of(None) == "anthropic"


# --- resolve_tier_for_provider: preset tier resolution against a provider's actual coverage --------


def test_resolve_tier_for_provider_returns_exact_match_unchanged():
    assert resolve_tier_for_provider("opus", "anthropic") == "opus"
    assert resolve_tier_for_provider("haiku", "anthropic") == "haiku"
    assert resolve_tier_for_provider("sonnet", "openai") == "sonnet"
    assert resolve_tier_for_provider("opus", "xai") == "opus"


def test_resolve_tier_for_provider_clamps_to_xais_only_tier():
    # xAI only ships one model today (slotted at "opus") -- any requested tier must resolve to it,
    # not raise and not switch to a different provider.
    assert resolve_tier_for_provider("haiku", "xai") == "opus"
    assert resolve_tier_for_provider("sonnet", "xai") == "opus"


def test_resolve_tier_for_provider_prefers_higher_tier_on_a_rank_distance_tie(monkeypatch):
    # Hypothetical provider missing the middle tier: "sonnet" is equidistant from "haiku" and "opus" --
    # the tie-break rounds UP (never resolves to a lower tier than requested when a tie must be broken).
    monkeypatch.setitem(PROVIDER_TIERS, "_test_partial", {"haiku": "x-small", "opus": "x-large"})
    assert resolve_tier_for_provider("sonnet", "_test_partial") == "opus"


def test_resolve_tier_for_provider_unknown_provider_returns_tier_unchanged():
    # No catalog to resolve against -- nothing to clamp to, so the caller's own downstream lookup
    # (e.g. model_for_tier) is what actually surfaces the "unknown provider" error.
    assert resolve_tier_for_provider("opus", "mistral") == "opus"


# --- supports_temperature: Anthropic flagship models 400 on the `temperature` sampling param -------


def test_supports_temperature_true_for_openai_and_xai():
    # OpenAI and xAI accept `temperature` on every configured model.
    assert supports_temperature("gpt-5.5") is True
    assert supports_temperature("gpt-5.4-nano") is True
    assert supports_temperature("grok-4.3") is True


def test_supports_temperature_false_for_anthropic_flagship_models():
    # The exact bug: the configured sonnet/opus Anthropic tiers reject `temperature` with a 400.
    assert supports_temperature("claude-sonnet-5") is False
    assert supports_temperature("claude-opus-4-8") is False


def test_supports_temperature_true_for_haiku_and_older_anthropic():
    assert supports_temperature("claude-haiku-4-5") is True
    assert supports_temperature("claude-haiku-4-5-20251001") is True  # dated id tolerated
    assert supports_temperature("claude-sonnet-4-6") is True


def test_supports_temperature_false_for_unknown_or_empty_model():
    # Unknown models classify as Anthropic (provider_of default) and aren't allowlisted -> omit
    # temperature, so a future/unrecognized Anthropic model can never re-introduce the 400.
    assert supports_temperature("claude-fable-5") is False
    assert supports_temperature("") is False
    assert supports_temperature(None) is False


# --- supports_effort: the quality knob for the flagship models that dropped `temperature` -----------


def test_supports_effort_true_for_flagship_anthropic_false_for_haiku():
    assert supports_effort("claude-opus-4-8") is True
    assert supports_effort("claude-sonnet-5") is True
    assert supports_effort("claude-haiku-4-5") is False  # Haiku 4.5 rejects effort (it takes temperature)


def test_supports_effort_false_for_non_anthropic_and_unknown():
    assert supports_effort("gpt-5.5") is False  # OpenAI/xAI don't take output_config.effort on this path
    assert supports_effort("grok-4.3") is False
    assert supports_effort("claude-something-unknown") is False  # unknown -> no effort (safe default)
    assert supports_effort(None) is False


def test_configured_anthropic_models_take_exactly_one_of_temperature_or_effort():
    # Every configured Anthropic model accepts exactly one knob — never both, never neither — so a
    # preset's quality_level always lands as a real setting.
    for model in PROVIDER_TIERS["anthropic"].values():
        assert supports_temperature(model) != supports_effort(model)
