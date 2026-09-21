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


HASH_VERSION = "hash:v1"


def _configured_version() -> str:
    return f"openai:{settings.embedding_model}" if _use_openai() else HASH_VERSION


def embed_with_version(text: str) -> tuple[list[float], str]:
    """The vector AND the version of the backend that ACTUALLY produced it.

    Every caller that persists `embedding_version` must use this rather than pairing `embed()` with
    `embedding_version()`. The latter reports what is CONFIGURED; on a provider error this function
    quietly returns a hash vector, and stamping the configured version would label a bag-of-words
    vector as an OpenAI one — permanently, invisibly, and in the same table the good rows live in.
    Nothing downstream can tell them apart: retrieval applies no version filter, so a mislabelled row
    is ranked by cosine distance against a vector space it does not belong to, and the result is
    noise that looks like a result.
    """
    global _warned_no_key
    if settings.embedding_provider == "openai" and not settings.openai_api_key:
        if not _warned_no_key:  # once, not per chunk
            log.warning("embedding.no_openai_key", note="OPENAI_API_KEY unset; using hash fallback")
            _warned_no_key = True
        return _hash_embed(text), HASH_VERSION
    if not _use_openai():
        return _hash_embed(text), HASH_VERSION
    try:
        return _openai_embed(text), _configured_version()
    except Exception as exc:  # noqa: BLE001 — never let an embedding outage break ingest/retrieval
        log.warning("embedding.openai_failed", error=str(exc), note="falling back to hash vector")
        return _hash_embed(text), HASH_VERSION


def embed(text: str) -> list[float]:
    """Map text to a 1536-vector via the configured backend. Falls back to the deterministic hash
    vector if the provider is unavailable or errors, so retrieval never hard-fails.

    Use this only where the vector is transient — embedding a QUERY. Anything that stores the vector
    must use `embed_with_version` so the stored label matches what actually ran.
    """
    return embed_with_version(text)[0]


async def embed_with_version_async(text: str) -> tuple[list[float], str]:
    """`embed_with_version()` off the event loop (same thread-offload rationale as `embed_async`)."""
    return await asyncio.to_thread(embed_with_version, text)


async def embed_async(text: str) -> list[float]:
    """`embed()` off the event loop. The OpenAI path blocks on HTTP for up to
    `embedding_time_budget_s` (30s default); called directly from async code that stall freezes the
    whole process — API responses AND the co-resident drafting worker — so every async caller goes
    through this thread offload."""
    return await asyncio.to_thread(embed, text)


# Batch size per OpenAI embeddings call — one HTTP round trip returns this many vectors, so a full
# canon re-index goes from N sequential calls to N/64. Kept modest so a batch stays well within request
# size / token limits and the per-call timeout.
_EMBED_BATCH = 64


def _openai_embed_many(texts: list[str]) -> list[list[float]]:
    """One OpenAI embeddings call for a list of inputs. The API echoes each result's `index`; we sort by
    it so the returned order matches the input order. Raises on any error (caller falls back per batch)."""
    resp = httpx.post(
        _OPENAI_URL,
        headers={"Authorization": f"Bearer {settings.openai_api_key}"},
        json={"model": settings.embedding_model, "input": [t or " " for t in texts]},
        timeout=settings.embedding_time_budget_s,
    )
    resp.raise_for_status()
    data = sorted(resp.json()["data"], key=lambda d: d["index"])
    if len(data) != len(texts):
        raise ValueError(f"embedding count {len(data)} != inputs {len(texts)}")
    vecs = [[float(x) for x in d["embedding"]] for d in data]
    for v in vecs:
        if len(v) != DIM:
            raise ValueError(f"embedding dim {len(v)} != expected {DIM}")
    return vecs


def embed_many_with_version(texts: list[str]) -> list[tuple[list[float], str]]:
    """Vectors AND the version that actually produced each one, order preserved.

    The version is PER ITEM, not per call, because the fallback is per BATCH: a rebuild of 200 chunks
    is four OpenAI calls, and the third one timing out leaves 64 hash vectors among 136 real ones. A
    single version for the whole rebuild would mislabel whichever group it did not describe.
    """
    if not texts:
        return []
    if not _use_openai():
        return [(_hash_embed(t), HASH_VERSION) for t in texts]
    version = _configured_version()
    out: list[tuple[list[float], str]] = []
    for i in range(0, len(texts), _EMBED_BATCH):
        batch = texts[i : i + _EMBED_BATCH]
        try:
            out.extend((vec, version) for vec in _openai_embed_many(batch))
        except Exception as exc:  # noqa: BLE001 — never let an embedding outage break a rebuild
            log.warning("embedding.openai_batch_failed", error=str(exc), note="hash fallback for batch")
            out.extend((_hash_embed(t), HASH_VERSION) for t in batch)
    return out


def embed_many(texts: list[str]) -> list[list[float]]:
    """Embed many texts, order preserved. See `embed()` on when this is the wrong function."""
    return [vec for vec, _ in embed_many_with_version(texts)]


async def embed_many_with_version_async(texts: list[str]) -> list[tuple[list[float], str]]:
    """`embed_many_with_version()` off the event loop."""
    return await asyncio.to_thread(embed_many_with_version, texts)


async def embed_many_async(texts: list[str]) -> list[list[float]]:
    """`embed_many()` off the event loop (same thread-offload rationale as `embed_async`)."""
    return await asyncio.to_thread(embed_many, texts)


def embedding_version() -> str:
    """The vector space the CONFIGURED backend would produce. Used to decide what needs re-embedding.

    NOT for stamping a row: it describes configuration, not what happened. A provider error makes the
    actual backend differ from this, which is why the write paths take their version from
    `embed_with_version` / `embed_many_with_version` instead.
    """
    return _configured_version()
