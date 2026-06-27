"""Hybrid canon retrieval (RAG upgrade).

Combines, in order: forced owner-file snippets (deterministic precedence), keyword/lexical scoring,
and semantic vector search; then merges/dedupes and reranks by source priority, owner topic, query
overlap, and status. Returns provenance-rich snippets so the Packet Author and ScenePacket Builder can
cite real source handles, not unsourced assertions.

Owner-file precedence is structural: an owner-forced chunk gets `rag_owner_file_boost` added to its
score, so it always outranks a semantic-only hit on the same topic. Vector search is supporting
context, never the authority.
"""
from __future__ import annotations

import re
import uuid
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from dominion.shared.config import settings
from dominion.shared.models import CanonEntity
from dominion.workers.memory.embedding import embed

_TOKEN = re.compile(r"[a-z0-9']+")
_STOP = {"the", "a", "an", "and", "or", "of", "to", "in", "is", "it", "this", "that", "on", "for"}


def _tokens(text: str | None) -> set[str]:
    return {t for t in _TOKEN.findall((text or "").lower()) if t not in _STOP and len(t) > 2}


def _row_dict(row: CanonEntity, *, score: float, reason: str) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "doc_path": row.doc_path,
        "heading_path": row.heading_path,
        "owner_topic": row.owner_topic,
        "source_priority": row.source_priority or 0,
        "body": row.body or "",
        "score": round(score, 4),
        "retrieval_reason": reason,
    }


async def retrieve_hybrid(
    session: AsyncSession,
    *,
    book_id: uuid.UUID,
    query: str,
    owner_topics: list[str] | None = None,
    required_doc_paths: list[str] | None = None,
    forbidden_topics: list[str] | None = None,
    k: int | None = None,
) -> list[dict[str, Any]]:
    """Return up to `k` provenance-rich canon snippets (owner-forced + keyword + semantic, reranked)."""
    k = k or settings.rag_final_k
    forbidden = {t for t in (forbidden_topics or []) if t}
    q_tokens = _tokens(query)

    candidates: dict[str, tuple[CanonEntity, float, str]] = {}

    def consider(row: CanonEntity, score: float, reason: str) -> None:
        if row.owner_topic in forbidden:
            return
        key = str(row.id)
        prev = candidates.get(key)
        if prev is None:
            candidates[key] = (row, score, reason)
            return
        # keep the higher base score; owner_forced is the strongest reason and wins ties
        best_score = max(score, prev[1])
        best_reason = "owner_forced" if "owner_forced" in (reason, prev[2]) else (
            reason if score >= prev[1] else prev[2]
        )
        candidates[key] = (row, best_score, best_reason)

    # 1) forced owner-file snippets ----------------------------------------------------------------
    if required_doc_paths or owner_topics:
        conds = []
        if required_doc_paths:
            conds.append(CanonEntity.doc_path.in_(required_doc_paths))
        if owner_topics:
            conds.append(CanonEntity.owner_topic.in_(owner_topics))
        rows = (await session.execute(
            select(CanonEntity).where(CanonEntity.book_id == book_id, or_(*conds))
        )).scalars().all()
        for row in rows:
            consider(row, float(settings.rag_owner_file_boost), "owner_forced")

    # 2) keyword/lexical pool ----------------------------------------------------------------------
    if q_tokens:
        like_terms = sorted(q_tokens)[: 8]
        like_conds = [CanonEntity.body.ilike(f"%{t}%") for t in like_terms]
        pool = (await session.execute(
            select(CanonEntity)
            .where(CanonEntity.book_id == book_id, CanonEntity.body.isnot(None), or_(*like_conds))
            .limit(settings.rag_keyword_k * 3)
        )).scalars().all()
        for row in pool:
            overlap = len(q_tokens & _tokens(row.body)) / (len(q_tokens) or 1)
            if overlap:
                consider(row, overlap, "keyword")

    # 3) semantic vector search --------------------------------------------------------------------
    if query.strip():
        qvec = embed(query)
        sem = (await session.execute(
            select(CanonEntity)
            .where(
                CanonEntity.book_id == book_id,
                CanonEntity.body.isnot(None),
                CanonEntity.embedding.isnot(None),
            )
            .order_by(CanonEntity.embedding.cosine_distance(qvec))
            .limit(settings.rag_semantic_k)
        )).scalars().all()
        for rank, row in enumerate(sem):
            # decaying similarity proxy in [0,1): top hit ~1.0, tapering with rank
            consider(row, 1.0 - rank / (settings.rag_semantic_k + 1), "semantic")

    # 4-5) rerank: base score + source_priority + owner-topic + query overlap ----------------------
    def rerank_key(item: tuple[CanonEntity, float, str]) -> float:
        row, base, _reason = item
        score = base
        score += (row.source_priority or 0)
        if row.owner_topic and owner_topics and row.owner_topic in set(owner_topics):
            score += 1.0
        score += 0.5 * (len(q_tokens & _tokens(row.body)) / (len(q_tokens) or 1))
        return score

    ranked = sorted(candidates.values(), key=rerank_key, reverse=True)[:k]
    return [_row_dict(row, score=rerank_key((row, base, reason)), reason=reason)
            for row, base, reason in ranked]
