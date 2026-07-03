from dominion.shared.agent_registry import model_for_tier, provider_of
from dominion.shared.config import settings
from dominion.workers.llm import _is_anthropic_model, _openai_compatible_endpoint


def test_google_provider_catalog_maps_flash_and_pro() -> None:
    assert model_for_tier("sonnet", "google") == "gemini-3.5-flash"
    assert model_for_tier("opus", "google") == "gemini-3.1-pro-preview"
    assert provider_of("gemini-3.5-flash") == "google"
    assert provider_of("gemini-3.1-pro-preview") == "google"


def test_gemini_routes_to_google_openai_compatible_endpoint() -> None:
    old_key = settings.google_api_key
    old_base = settings.google_base_url
    settings.google_api_key = "  gemini-test-key  "
    settings.google_base_url = "https://generativelanguage.googleapis.com/v1beta/openai"
    try:
        assert _is_anthropic_model("gemini-3.5-flash") is False
        assert _openai_compatible_endpoint("gemini-3.5-flash") == (
            "https://generativelanguage.googleapis.com/v1beta/openai",
            "gemini-test-key",
        )
    finally:
        settings.google_api_key = old_key
        settings.google_base_url = old_base
