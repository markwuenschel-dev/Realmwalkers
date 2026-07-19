from dominion.shared.agent_registry import provider_and_tier_of
from dominion.workers import llm
from dominion.workers.budget import TokenBudget


def test_responses_request_preserves_stable_prefix_and_explicit_storage():
    request = llm._responses_request(
        model="gpt-5.6-terra",
        system="system",
        user="dynamic",
        blocks=(llm.CachedPrefixBlock(name="canon", text="stable canon"),),
        max_tokens=321,
        effort="medium",
        text_format=None,
    )

    assert request["instructions"] == "system"
    assert request["input"] == "stable canon\n\ndynamic"
    assert request["max_output_tokens"] == 321
    assert request["reasoning"] == {"effort": "medium"}
    assert request["store"] is True
    assert request["prompt_cache_key"] == llm._prompt_cache_key(
        "system", (llm.CachedPrefixBlock(name="canon", text="stable canon"),)
    )


def test_responses_usage_splits_cached_input_and_marks_incomplete():
    usage = llm._responses_usage(
        {
            "input_tokens": 100,
            "output_tokens": 20,
            "input_tokens_details": {"cached_tokens": 40},
        },
        status="incomplete",
    )

    assert usage.input_tokens == 60
    assert usage.cache_read_tokens == 40
    assert usage.output_tokens == 20
    assert usage.truncated is True


def test_legacy_openai_override_retains_its_provider_and_tier():
    assert provider_and_tier_of("gpt-5.4-mini") == ("openai", "sonnet")


async def test_complete_normalizes_a_responses_result(monkeypatch):
    monkeypatch.setattr(llm.settings, "openai_api_key", "test-key")
    captured = {}

    class Response:
        output_text = '{"ok":true}'

        def model_dump(self):
            return {
                "status": "completed",
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 20,
                    "input_tokens_details": {"cached_tokens": 40},
                },
            }

    class Responses:
        async def create(self, **kwargs):
            captured.update(kwargs)
            return Response()

    class Client:
        responses = Responses()

    monkeypatch.setattr(llm, "_openai_client", lambda _key: Client())
    text, usage = await llm.complete(
        model="gpt-5.6-terra",
        system="system",
        user="dynamic",
        max_tokens=200,
        budget=TokenBudget(max_tokens=1_000),
        user_prefix_blocks=(llm.CachedPrefixBlock(name="user_prefix", text="stable"),),
    )

    assert text == '{"ok":true}'
    assert usage.input_tokens == 60
    assert usage.cache_read_tokens == 40
    assert captured["store"] is True
    assert captured["input"] == "stable\n\ndynamic"
