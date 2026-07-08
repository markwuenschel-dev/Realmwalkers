"""Live-provider seam test for text embedding.

`conftest.py` forces `DOMINION_EMBEDDING_PROVIDER=hash` suite-wide (so tests never make live OpenAI
calls), which means the REAL provider branch of `embedding.embed()` / `embed_many()` — the httpx path
behind `_use_openai()` — was never exercised by any test. These flip the provider to "openai" with a
fake key for a single test and stub `httpx.post`, so the provider seam is driven end to end with a FAKE
client (not the hash substitute), proving `embed()` actually calls the provider and returns its vector
rather than silently falling back to the hash bag-of-words. No network, no DB.
"""

from __future__ import annotations

import pytest

from dominion.shared.config import settings
from dominion.workers.memory import embedding


class _FakeResp:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:  # httpx.Response API surface the code touches
        pass

    def json(self) -> dict:
        return self._payload


@pytest.fixture
def live_openai(monkeypatch):
    """Override conftest's suite-wide hash forcing for one test: select the real provider + a fake key."""
    monkeypatch.setattr(settings, "embedding_provider", "openai")
    monkeypatch.setattr(settings, "openai_api_key", "sk-test-not-real")
    return settings


def test_embed_drives_the_live_openai_provider_path(live_openai, monkeypatch):
    text = "Mara crosses the breach into the Reserve."
    fake_vec = [0.001 * (i % 250) for i in range(embedding.DIM)]  # distinct from any hash vector

    captured: dict = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured.update(url=url, headers=headers, json=json, timeout=timeout)
        return _FakeResp({"data": [{"index": 0, "embedding": fake_vec}]})

    monkeypatch.setattr(embedding.httpx, "post", fake_post)

    out = embedding.embed(text)

    # Returned the PROVIDER vector, not the deterministic hash fallback — proves the seam was taken.
    assert out == fake_vec
    assert out != embedding._hash_embed(text)
    # The provider was actually called, with the configured model/key/endpoint.
    assert captured["url"] == embedding._OPENAI_URL
    assert captured["headers"]["Authorization"] == "Bearer sk-test-not-real"
    assert captured["json"]["model"] == settings.embedding_model
    assert captured["json"]["input"] == text
    # embedding_version encodes the active backend, so a provider switch forces a re-embed.
    assert embedding.embedding_version() == f"openai:{settings.embedding_model}"


def test_embed_many_drives_the_live_openai_batch_path(live_openai, monkeypatch):
    texts = ["first chunk", "second chunk", "third chunk"]
    # Return in a shuffled `index` order to prove the code re-sorts by index back to input order.
    fake_vecs = {t: [0.002 * (i + n) for i in range(embedding.DIM)] for n, t in enumerate(texts)}

    def fake_post(url, headers=None, json=None, timeout=None):
        data = [
            {"index": idx, "embedding": fake_vecs[texts[idx]]}
            for idx in (2, 0, 1)  # deliberately out of order
        ]
        return _FakeResp({"data": data})

    monkeypatch.setattr(embedding.httpx, "post", fake_post)

    out = embedding.embed_many(texts)

    assert out == [fake_vecs[t] for t in texts]  # order restored to match inputs
    assert out[0] != embedding._hash_embed(texts[0])  # provider path, not hash fallback
