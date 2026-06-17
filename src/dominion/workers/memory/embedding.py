"""Deterministic text embedding (feature hashing) — no API key, no heavy deps (DESIGN §7).

A signed hashing-trick bag-of-words into the 1536-dim pgvector column. It captures lexical overlap,
not deep semantics; it is intentionally a drop-in seam — swap in a Voyage/OpenAI/sentence-transformers
model later behind this same `embed()` signature without touching callers.
"""
from __future__ import annotations

import hashlib
import math
import re

DIM = 1536
_TOKEN = re.compile(r"[a-z0-9']+")


def embed(text: str) -> list[float]:
    """Map text to a unit-norm 1536-vector. Empty/blank text -> zero vector."""
    vec = [0.0] * DIM
    for token in _TOKEN.findall(text.lower()):
        h = int.from_bytes(hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest(), "little")
        vec[h % DIM] += 1.0 if (h >> 63) & 1 else -1.0  # signed buckets reduce collision bias
    norm = math.sqrt(sum(x * x for x in vec))
    if norm:
        vec = [x / norm for x in vec]
    return vec
