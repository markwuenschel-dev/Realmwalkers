"""Guards on the two providers added to the catalog on 2026-09-20: Moonshot (Kimi) and Meta (Muse).

Both reach the app the same way Gemini and xAI do — an OpenAI-compatible chat-completions endpoint
selected by the model-id prefix. The failure mode being guarded here is silent: an id the dispatcher
does not recognise is treated as an Anthropic model (`_is_anthropic_model` is a deny-list, not an
allow-list), so a missing prefix does not raise — it sends the manuscript to the wrong vendor with the
wrong key and fails as a confusing 404.
"""

from __future__ import annotations

import pytest

from dominion.shared.agent_registry import PROVIDER_LABELS, PROVIDER_TIERS, model_for_tier, provider_of
from dominion.shared.config import settings
from dominion.shared.model_pricing import pricing_for_model
from dominion.workers.llm import _is_anthropic_model, _openai_compatible_endpoint


def test_catalog_offers_both_new_providers() -> None:
    assert model_for_tier("opus", "moonshot") == "kimi-k3"
    assert model_for_tier("opus", "meta") == "muse-spark-1.3"
    assert provider_of("kimi-k3") == "moonshot"
    assert provider_of("muse-spark-1.3") == "meta"
    assert PROVIDER_LABELS["moonshot"] == "Moonshot"
    assert PROVIDER_LABELS["meta"] == "Meta"


def test_anthropic_no_longer_offers_haiku_but_old_ids_still_resolve() -> None:
    """Haiku left the picker; it did not leave the data. Historical rows and any persisted setting
    still have to answer, or their telemetry re-prices at the fallback."""
    assert model_for_tier("haiku", "anthropic") is None
    assert provider_of("claude-haiku-4-5") == "anthropic"
    assert pricing_for_model("claude-haiku-4-5").input == 0.80


@pytest.mark.parametrize(
    ("model", "expected_provider"),
    [("kimi-k3", "moonshot"), ("muse-spark-1.3", "meta"), ("gemini-3.8-flash", "google")],
)
def test_new_model_ids_are_not_mistaken_for_anthropic(model: str, expected_provider: str) -> None:
    assert provider_of(model) == expected_provider
    assert _is_anthropic_model(model) is False


def test_kimi_routes_to_moonshot_endpoint() -> None:
    old_key, old_base = settings.moonshot_api_key, settings.moonshot_base_url
    settings.moonshot_api_key = "  moonshot-test-key  "
    settings.moonshot_base_url = "https://api.moonshot.ai/v1"
    try:
        assert _openai_compatible_endpoint("kimi-k3") == ("https://api.moonshot.ai/v1", "moonshot-test-key")
    finally:
        settings.moonshot_api_key, settings.moonshot_base_url = old_key, old_base


def test_muse_routes_to_meta_endpoint() -> None:
    old_key, old_base = settings.muse_api_key, settings.muse_base_url
    settings.muse_api_key = "  muse-test-key  "
    settings.muse_base_url = "https://api.meta.ai/v1"
    try:
        assert _openai_compatible_endpoint("muse-spark-1.3") == ("https://api.meta.ai/v1", "muse-test-key")
    finally:
        settings.muse_api_key, settings.muse_base_url = old_key, old_base


def test_a_missing_new_provider_key_names_the_variable_to_set() -> None:
    old = settings.moonshot_api_key
    settings.moonshot_api_key = None
    try:
        with pytest.raises(RuntimeError, match="MOONSHOT_API_KEY"):
            _openai_compatible_endpoint("kimi-k3")
    finally:
        settings.moonshot_api_key = old


def test_the_catalog_never_offers_a_tier_that_trains_on_the_manuscript() -> None:
    """Meta's "-contributor" ids are the same models at a discount, bought with permission to train on
    every prompt sent. This app sends manuscript prose, so that trade was declined on 2026-09-20. Putting
    such an id back is a decision for the author to make deliberately — this test is what makes it
    deliberate, by failing rather than letting it arrive inside an unrelated change."""
    offered = {model for tiers in PROVIDER_TIERS.values() for model in tiers.values()}
    trains_on_input = sorted(m for m in offered if m.endswith("-contributor"))
    assert trains_on_input == [], f"catalog offers a train-on-your-data tier: {trains_on_input}"


def test_the_two_muse_tiers_are_priced_apart() -> None:
    """The contributor tier's discount is paid for with permission to train on what is sent. Both ids
    are priced so the opt-out is a one-word catalog edit — if they ever collapse to one rate, the
    cheaper one is silently answering for the other."""
    contributor = pricing_for_model("muse-spark-1.3-contributor")
    standard = pricing_for_model("muse-spark-1.3")
    assert contributor.input == 0.10 and contributor.output == 0.20
    assert standard.input == 1.25 and standard.output == 4.25
    assert contributor != standard


def test_opus_alias_is_priced_and_does_not_inherit_the_sonnet_fallback() -> None:
    """`claude-opus-latest` shares no prefix with any other key in the table, so without its own entry
    it would bill at Sonnet rates — the exact defect #319 fixed for the gpt-* family."""
    opus = pricing_for_model("claude-opus-latest")
    assert (opus.input, opus.output) == (5.0, 25.0)
    assert opus != pricing_for_model("claude-sonnet-4")
