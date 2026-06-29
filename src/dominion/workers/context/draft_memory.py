"""Oracle ledger, exemplars, canon RAG, POV summary, and prior-scene tail."""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dominion.shared.config import settings
from dominion.shared.enums import SceneStatus
from dominion.shared.models import Job, PovProfile, Scene
from dominion.workers.context.types import DraftMemory, ResolvedJob
from dominion.workers.memory import owner_router, retrieval, summaries
from dominion.workers.oracle import Oracle

_PRIOR_TAIL_CHARS = 800


async def build_draft_memory(
    session: AsyncSession, resolved: ResolvedJob, job: Job
) -> DraftMemory:
    characters_present = list(resolved.beat.characters_present or [])
    beat = resolved.beat
    chapter = resolved.chapter

    oracle = Oracle(session)
    ledger: dict[str, dict[str, Any]] = {}
    for character in characters_present:
        stats = await oracle.current(book_id=resolved.book_id, character=character)
        if stats:
            ledger[character] = stats

    exemplars = await _load_exemplars(
        session, resolved.profile, exclude_scene_id=job.target_scene_id
    )

    retrieval_query = " ".join(p for p in [beat.beat_text or "", *characters_present] if p)
    routing = owner_router.route(retrieval_query, characters=characters_present)
    snippets = await retrieval.retrieve_hybrid(
        session, book_id=resolved.book_id, query=retrieval_query,
        owner_topics=routing.owner_topics, required_doc_paths=routing.doc_paths, k=6,
    )
    owner_first = [s for s in snippets if s["retrieval_reason"] == "owner_forced"]
    rest = [s for s in snippets if s["retrieval_reason"] != "owner_forced"]
    canon = [s["body"] for s in [*owner_first, *rest] if s["body"]]

    pov_summary = await summaries.pov_summary(
        session, book_id=resolved.book_id, pov=chapter.pov
    )
    prior_scene_tail = await _prior_tail(
        session, chapter_id=chapter.id, scene_no=resolved.scene_no
    )

    return DraftMemory(
        ledger=ledger,
        exemplars=exemplars,
        canon=canon,
        pov_summary=pov_summary,
        prior_scene_tail=prior_scene_tail,
    )


async def _load_exemplars(
    session: AsyncSession, profile: PovProfile | None, *, exclude_scene_id: uuid.UUID | None
) -> list[str]:
    if profile is None or not profile.exemplar_scene_ids:
        return []
    ids: list[uuid.UUID] = []
    for raw in profile.exemplar_scene_ids:
        try:
            sid = uuid.UUID(raw)
        except (ValueError, AttributeError, TypeError):
            continue
        if sid != exclude_scene_id and sid not in ids:
            ids.append(sid)
    if not ids:
        return []

    rows = (await session.execute(
        select(Scene.id, Scene.prose).where(Scene.id.in_(ids))
    )).all()
    prose_by_id = {sid: prose for sid, prose in rows if prose}

    exemplars: list[str] = []
    for sid in ids:
        prose = prose_by_id.get(sid)
        if not prose:
            continue
        exemplars.append(prose[: settings.exemplar_max_chars])
        if len(exemplars) >= settings.exemplar_max_count:
            break
    return exemplars


async def _prior_tail(
    session: AsyncSession, *, chapter_id: uuid.UUID, scene_no: int
) -> str | None:
    prose = (await session.execute(
        select(Scene.prose)
        .where(
            Scene.chapter_id == chapter_id,
            Scene.scene_no < scene_no,
            Scene.status == SceneStatus.APPROVED,
        )
        .order_by(Scene.scene_no.desc())
        .limit(1)
    )).scalar_one_or_none()
    return prose[-_PRIOR_TAIL_CHARS:] if prose else None
