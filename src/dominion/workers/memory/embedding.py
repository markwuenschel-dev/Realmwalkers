"""Text embedding with a swappable provider seam (DESIGN §7; RAG upgrade).

`embed()` returns a 1536-dim vector for the pgvector column. Two backends, chosen by
`settings.embedding_provider`:

  * "openai" — real semantic vectors from the OpenAI embeddings REST API (text-embedding-3-small),
    called over httpx (no extra SDK dep). Selected automatically once an OpenAI key is configured.
  * "hash"   — deterministic signed feature-hashing bag-of-words. No key, no network. The default
    fallback used offline, in CI, and in tests — and the silent fallback when "openai" is selected
    but no key is present, so retrieval degrades gracefully instead of failing.

`embedding_version()` encodes the active backend + model, so a provider switch forces a re-embed of
changed chunks (ingest compares the stored version) rather than mixing incompatible vector spaces.

Note: `embed()` is synchronous (sync callers in scripts/tests rely on that). The OpenAI path makes a
blocking HTTP call; it is bounded by `settings.embedding_time_budget_s` and falls back to the hash
vector on any error, so a provider outage never breaks drafting. Async callers (API handlers, the
in-process drafting worker) MUST use `embed_async()` instead — the worker shares the API's single
event loop, so a blocking embed call would freeze every in-flight HTTP request for the duration.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import re

import httpx
import structlog

from dominion.shared.config import settings

log = structlog.get_logger()

DIM = 1536
_TOKEN = re.compile(r"[a-z0-9']+")
_OPENAI_URL = "https://api.openai.com/v1/embeddings"
_warned_no_key = False


def _hash_embed(text: str) -> list[float]:
    """Deterministic signed hashing-trick bag-of-words into a unit-norm DIM-vector. Captures lexical
    overlap, not deep semantics. Empty/blank text -> zero vector."""
    vec = [0.0] * DIM
    for token in _TOKEN.findall(text.lower()):
        h = int.from_bytes(hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest(), "little")
        vec[h % DIM] += 1.0 if (h >> 63) & 1 else -1.0  # signed buckets reduce collision bias
    norm = math.sqrt(sum(x * x for x in vec))
    if norm:
        vec = [x / norm for x in vec]
    return vec


def _use_openai() -> bool:
    return settings.embedding_provider == "openai" and bool(settings.openai_api_key)


def _openai_embed(text: str) -> list[float]:
    """One bounded OpenAI embeddings call. Raises on any HTTP/transport error (caller falls back)."""
    resp = httpx.post(
        _OPENAI_URL,
        headers={"Authorization": f"Bearer {settings.openai_api_key}"},
        json={"model": settings.embedding_model, "input": text or " "},
        timeout=settings.embedding_time_budget_s,
    )
    resp.raise_for_status()
    vec = resp.json()["data"][0]["embedding"]
    if len(vec) != DIM:
        raise ValueError(f"embedding dim {len(vec)} != expected {DIM}")
    return [float(x) for x in vec]


def embed(text: str) -> list[float]:
    """Map text to a 1536-vector via the configured backend. Falls back to the deterministic hash
    vector if the provider is unavailable or errors, so retrieval never hard-fails."""
    global _warned_no_key
    if settings.embedding_provider == "openai" and not settings.openai_api_key:
        if not _warned_no_key:  # once, not per chunk
            log.warning("embedding.no_openai_key", note="OPENAI_API_KEY unset; using hash fallback")
            _warned_no_key = True
        return _hash_embed(text)
    if not _use_openai():
        return _hash_embed(text)
    try:
        return _openai_embed(text)
    except Exception as exc:  # noqa: BLE001 — never let an embedding outage break ingest/retrieval
        log.warning("embedding.openai_failed", error=str(exc), note="falling back to hash vector")
        return _hash_embed(text)


async def embed_async(text: str) -> list[float]:
    """`embed()` off the event loop. The OpenAI path blocks on HTTP for up to
    `embedding_time_budget_s` (30s default); called directly from async code that stall freezes the
    whole process — API responses AND the co-resident drafting worker — so every async caller goes
    through this thread offload."""
    return await asyncio.to_thread(embed, text)


def embedding_version() -> str:
    """Identifier for the vector space the active backend produces. Bumped implicitly by switching
    provider/model, so stale chunks from another backend get re-embedded on the next ingest."""
    if _use_openai():
        return f"openai:{settings.embedding_model}"
    return "hash:v1"
