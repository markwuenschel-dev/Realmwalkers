"""World ledger + in-prose entity cards (DESIGN §5, §7).

The Desk's Ledger screen and the hover-cards over names in the prose read here. Hard numbers come
from the Oracle's CharacterState; prose/lore bodies from the canon (CanonEntity). Both are real
story state — nothing here is a fixture.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import structlog
from fastapi import APIRouter, BackgroundTasks, HTTPException
from sqlalchemy import ColumnElement, delete, func, null, or_, select

from dominion.api.deps import SessionDep
from dominion.shared.config import settings
from dominion.shared.db import SessionFactory
from dominion.shared.models import Book, CanonEntity, Chapter, CharacterState, KnowledgeFact
from dominion.shared.schemas import (
    CanonBulkDeleteOut,
    CanonCleanupIn,
    CanonCleanupItemOut,
    CanonCleanupPreviewOut,
    CanonEntityIn,
    CanonEntityOut,
    CanonEntityUpdateIn,
    CanonRebuildStartedOut,
    CanonRetireOut,
    CharacterStateIn,
    CharacterStateOut,
    KnowledgeFactOut,
)
from dominion.workers.activity import record_activity
from dominion.workers.memory import canon_rag
from dominion.workers.memory.embedding import embed_async, embedding_version

log = structlog.get_logger()
router = APIRouter(tags=["world"])

# …/src/dominion/api/routers/world.py -> repo root (mirrors docs.py); shared canon docs live under series/.
_PROJECT_ROOT = Path(__file__).resolve().parents[4]


async def _require_book(book_id: uuid.UUID, session: SessionDep) -> Book:
    book = await session.get(Book, book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="book not found")
    return book


async def _character_out(book_id: uuid.UUID, state: CharacterState, session: SessionDep) -> CharacterStateOut:
    """One CharacterState -> DTO, resolving POV flag (from chapters) + canon body (kind='character')."""
    is_pov = (
        await session.execute(
            select(Chapter.id).where(Chapter.book_id == book_id, Chapter.pov == state.character).limit(1)
        )
    ).first() is not None
    body = (
        await session.execute(
            select(CanonEntity.body)
            .where(
                CanonEntity.book_id == book_id,
                CanonEntity.kind == "character",
                func.lower(CanonEntity.name) == state.character.lower(),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    return CharacterStateOut(
        character=state.character,
        stats=state.stats_json or {},
        provisional=state.provisional,
        is_pov=is_pov,
        body=body,
    )


@router.get("/books/{book_id}/characters", response_model=list[CharacterStateOut])
async def list_characters(book_id: uuid.UUID, session: SessionDep) -> list[CharacterStateOut]:
    """Every character the Oracle is tracking, with stats + (if present) the canon body and POV flag."""
    await _require_book(book_id, session)

    states = (
        (
            await session.execute(
                select(CharacterState).where(CharacterState.book_id == book_id).order_by(CharacterState.character)
            )
        )
        .scalars()
        .all()
    )
    pov_set = set((await session.execute(select(Chapter.pov).where(Chapter.book_id == book_id))).scalars().all())
    canon = (
        (
            await session.execute(
                select(CanonEntity).where(CanonEntity.book_id == book_id, CanonEntity.kind == "character")
            )
        )
        .scalars()
        .all()
    )
    bodies = {(c.name or "").lower(): c.body for c in canon}

    return [
        CharacterStateOut(
            character=st.character,
            stats=st.stats_json or {},
            provisional=st.provisional,
            is_pov=st.character in pov_set,
            body=bodies.get(st.character.lower()),
        )
        for st in states
    ]


@router.put("/books/{book_id}/characters/{character}", response_model=CharacterStateOut)
async def upsert_character(
    book_id: uuid.UUID, character: str, body: CharacterStateIn, session: SessionDep
) -> CharacterStateOut:
    """Seed or replace a character's Oracle stats (absolute values) + optional canon description.

    This is how you establish a baseline BEFORE writing — the continuity reviewer checks prose against
    these hard numbers, and the drafter/RAG see the description. Stats are set wholesale (not deltas);
    beat-declared deltas still advance them on approval as usual (workers/memory/ledger.py)."""
    await _require_book(book_id, session)
    name = character.strip()
    if not name:
        raise HTTPException(status_code=400, detail="character name required")

    row = (
        await session.execute(
            select(CharacterState).where(CharacterState.book_id == book_id, CharacterState.character == name)
        )
    ).scalar_one_or_none()
    if row is None:
        row = CharacterState(book_id=book_id, character=name, stats_json={})
        session.add(row)
    row.stats_json = body.stats or {}
    row.provisional = False

    # An optional description lives as a kind='character' canon entity (what list_characters reads).
    if body.body is not None:
        canon = (
            await session.execute(
                select(CanonEntity)
                .where(
                    CanonEntity.book_id == book_id,
                    CanonEntity.kind == "character",
                    func.lower(CanonEntity.name) == name.lower(),
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        text = body.body.strip() or None
        if canon is None:
            canon = CanonEntity(book_id=book_id, kind="character", name=name)
            session.add(canon)
        canon.body = text
        canon.embedding = (await embed_async(text)) if text else None
        canon.embedding_version = embedding_version() if text else None
        canon.embedding_model = settings.embedding_model if text else None

    await session.commit()
    return await _character_out(book_id, row, session)


@router.delete("/books/{book_id}/characters/{character}")
async def delete_character(book_id: uuid.UUID, character: str, session: SessionDep) -> dict[str, str]:
    """Drop a character's tracked stat row (the kind='character' canon description is left in place)."""
    row = (
        await session.execute(
            select(CharacterState).where(CharacterState.book_id == book_id, CharacterState.character == character)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="character not found")
    await session.delete(row)
    await session.commit()
    return {"deleted": character}


@router.get("/books/{book_id}/knowledge", response_model=list[KnowledgeFactOut])
async def list_knowledge(book_id: uuid.UUID, session: SessionDep) -> list[KnowledgeFact]:
    """The knowledge ledger: discrete story facts + who knows them when (scene-packet knowledge layer).
    Populated from approved scenes' ScenePacket reveals."""
    book = await session.get(Book, book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="book not found")
    rows = (
        (
            await session.execute(
                select(KnowledgeFact).where(KnowledgeFact.book_id == book_id).order_by(KnowledgeFact.created_at)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


def _status_match(status: str) -> ColumnElement[bool]:
    """SQL predicate for a canon `status` filter. `active` also admits legacy NULL rows (rows written
    before the status column existed are treated as active), matching status-aware retrieval."""
    if status == "active":
        return or_(CanonEntity.status.is_(None), CanonEntity.status == "active")
    return CanonEntity.status == status


@router.get("/books/{book_id}/canon", response_model=list[CanonEntityOut])
async def list_canon(
    book_id: uuid.UUID,
    session: SessionDep,
    kind: str | None = None,
    status: str = "active",
    source: str | None = None,
    include_bodies: bool = True,
) -> list[CanonEntity] | list[CanonEntityOut]:
    """The story bible: characters, locations, factions, items, lore.

    Filters (all optional): `kind`; `status` (active|stale|retired|superseded|all, default `active` so
    the Ledger hides retired/stale canon by default — pass `all` to see everything); `source`
    (manual|repo_ingested|packet_derived|draft_derived|legacy|all).

    include_bodies=false returns the slim index (id/kind/name/source/status, body=null): the full
    corpus is megabytes and the Desk's global provider only needs the index for first paint — bodies
    upgrade in the background (command-palette search) or load on the Ledger screen itself.

    Column-targeted on purpose: `select(CanonEntity)` dragged the 1536-dim `embedding` vector for
    EVERY row (tens of MB of DB transfer + decode per call, multi-second queries). Concurrent canon
    fetches then hogged the whole connection pool and unrelated cheap endpoints queued for seconds
    behind them. The API never returns the vector, so it must never be fetched here.
    """
    book = await session.get(Book, book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="book not found")
    body_col = CanonEntity.body if include_bodies else null().label("body")
    stmt = select(
        CanonEntity.id,
        CanonEntity.kind,
        CanonEntity.name,
        body_col,
        CanonEntity.source,
        CanonEntity.status,
        CanonEntity.doc_path,
        CanonEntity.heading_path,
        CanonEntity.owner_topic,
        CanonEntity.source_priority,
        CanonEntity.embedding_version,
    ).where(CanonEntity.book_id == book_id)
    if kind:
        stmt = stmt.where(CanonEntity.kind == kind)
    if status and status != "all":
        stmt = stmt.where(_status_match(status))
    if source and source != "all":
        stmt = stmt.where(CanonEntity.source == source)
    rows = (await session.execute(stmt.order_by(CanonEntity.kind, CanonEntity.name))).all()
    # Staleness = the row's recorded embedding backend differs from the active one. Rows with no
    # recorded version (hand-authored, pre-versioning) make no staleness claim.
    current_version = embedding_version()
    return [
        CanonEntityOut(
            id=r.id,
            kind=r.kind,
            name=r.name,
            body=r.body,
            source=r.source,
            status=r.status,
            doc_path=r.doc_path,
            heading_path=r.heading_path,
            owner_topic=r.owner_topic,
            source_priority=r.source_priority,
            embedding_version=r.embedding_version,
            embedding_stale=r.embedding_version is not None and r.embedding_version != current_version,
        )
        for r in rows
    ]


@router.post("/books/{book_id}/canon", response_model=CanonEntityOut)
async def create_canon(book_id: uuid.UUID, body: CanonEntityIn, session: SessionDep) -> CanonEntity:
    """Add a canon entity (location/faction/item/lore/character/…). Embedded on write so it's
    immediately retrievable by the drafter/planner RAG (DESIGN §7)."""
    await _require_book(book_id, session)
    text = (body.body or "").strip() or None
    entity = CanonEntity(
        book_id=book_id,
        kind=(body.kind or "").strip() or None,
        name=(body.name or "").strip() or None,
        body=text,
        embedding=(await embed_async(text)) if text else None,
        embedding_version=embedding_version() if text else None,
        embedding_model=settings.embedding_model if text else None,
    )
    session.add(entity)
    await session.commit()
    return entity


@router.put("/canon/{canon_id}", response_model=CanonEntityOut)
async def update_canon(canon_id: uuid.UUID, body: CanonEntityUpdateIn, session: SessionDep) -> CanonEntity:
    """Edit a canon entity. Only provided fields change; a body change re-embeds it for retrieval."""
    entity = await session.get(CanonEntity, canon_id)
    if entity is None:
        raise HTTPException(status_code=404, detail="canon entity not found")
    data = body.model_dump(exclude_unset=True)
    if "kind" in data:
        entity.kind = (data["kind"] or "").strip() or None
    if "name" in data:
        entity.name = (data["name"] or "").strip() or None
    if "body" in data:
        text = (data["body"] or "").strip() or None
        entity.body = text
        entity.embedding = (await embed_async(text)) if text else None
        entity.embedding_version = embedding_version() if text else None
        entity.embedding_model = settings.embedding_model if text else None
    await session.commit()
    return entity


@router.delete("/canon/{canon_id}")
async def delete_canon(canon_id: uuid.UUID, session: SessionDep) -> dict[str, str]:
    entity = await session.get(CanonEntity, canon_id)
    if entity is None:
        raise HTTPException(status_code=404, detail="canon entity not found")
    await session.delete(entity)
    await session.commit()
    return {"deleted": str(canon_id)}


async def _run_canon_rebuild(book_id: uuid.UUID) -> None:
    """Background canon re-index: incremental (skip-unchanged), batched embeddings, correct retire — with
    progress in the Activity feed. Runs on its OWN session (the request's is long closed). This is what
    keeps a full re-embed off the request path, so the endpoint returns instantly instead of 499-timing
    out behind the proxy. Errors are recorded to the feed, never raised (nothing awaits this)."""
    root = _PROJECT_ROOT / "series" / "canon"
    try:
        async with SessionFactory() as session:
            out = await canon_rag.ingest_incremental(session, book_id=book_id, root=root)
            await record_activity(
                session,
                source="canon",
                kind="canon_rebuild_done",
                severity="success",
                title=(
                    f"Canon rebuilt · {out['indexed']} new, {out['skipped']} unchanged, "
                    f"{out['retired']} retired"
                ),
                book_id=book_id,
            )
            await session.commit()
    except Exception as exc:  # noqa: BLE001 — surface the failure to the feed, don't crash the loop
        log.warning("canon.rebuild_failed", book_id=str(book_id), error=str(exc))
        try:
            async with SessionFactory() as session:
                await record_activity(
                    session,
                    source="canon",
                    kind="canon_rebuild_failed",
                    severity="error",
                    title="Canon rebuild failed",
                    detail=str(exc),
                    book_id=book_id,
                )
                await session.commit()
        except Exception:  # noqa: BLE001
            log.warning("canon.rebuild_failed_record_failed", book_id=str(book_id))


@router.post("/books/{book_id}/canon/ingest", status_code=202, response_model=CanonRebuildStartedOut)
async def ingest_canon(
    book_id: uuid.UUID, session: SessionDep, background: BackgroundTasks
) -> CanonRebuildStartedOut:
    """Rebuild the retrieval index from the on-disk canon docs (series/canon) — the bridge from the
    read-only Canon tab into the RAG the drafter/planner actually query.

    ASYNC: the heavy delete/re-embed used to run inside this request and time out (HTTP 499) on a full
    corpus. It now runs in the background (`_run_canon_rebuild`) with batched embeds + incremental
    skip-unchanged, preserving hand-authored entries (doc_path IS NULL). This handler records a 'started'
    activity, schedules the work, and returns 202 immediately; completion appears in the Activity feed.
    """
    await _require_book(book_id, session)
    await record_activity(
        session,
        source="canon",
        kind="canon_rebuild_started",
        severity="info",
        title="Canon rebuild started…",
        book_id=book_id,
    )
    await session.commit()
    background.add_task(_run_canon_rebuild, book_id)
    return CanonRebuildStartedOut(status="started")


# --- Canon cleanup: status-aware retire / bulk-delete / rebuild (Workstream H) ---------------------


async def _cleanup_candidates(
    book_id: uuid.UUID, req: CanonCleanupIn, session: SessionDep
) -> tuple[list[CanonEntity], set[uuid.UUID]]:
    """Rows a cleanup selection matches, plus the set of explicitly-listed ids.

    Selection is by explicit `ids` OR by (`source_filter`, `status_filter`); with neither, nothing is
    matched (fail-safe against an accidental mass purge). The returned id set is what overrides
    manual-source protection downstream (a manual row acts only when its id was named explicitly).
    """
    explicit_ids = set(req.ids or [])
    stmt = select(CanonEntity).where(CanonEntity.book_id == book_id)
    if explicit_ids:
        stmt = stmt.where(CanonEntity.id.in_(explicit_ids))
    else:
        has_source = bool(req.source_filter and req.source_filter != "all")
        has_status = bool(req.status_filter and req.status_filter != "all")
        if not (has_source or has_status):
            return [], explicit_ids
        if has_source:
            stmt = stmt.where(CanonEntity.source == req.source_filter)
        if has_status:
            stmt = stmt.where(_status_match(req.status_filter or ""))
    rows = (await session.execute(stmt.order_by(CanonEntity.kind, CanonEntity.name))).scalars().all()
    return list(rows), explicit_ids


def _is_protected(row: CanonEntity, explicit_ids: set[uuid.UUID]) -> bool:
    """Manual-source canon is never auto-retired/deleted by a filter — only when named by id."""
    return (row.source or "manual") == "manual" and row.id not in explicit_ids


@router.post("/books/{book_id}/canon/cleanup-preview", response_model=CanonCleanupPreviewOut)
async def cleanup_preview(book_id: uuid.UUID, req: CanonCleanupIn, session: SessionDep) -> CanonCleanupPreviewOut:
    """Dry-run a retire/delete: report what the same selection would affect, mutating nothing.

    Manual-source rows are protected (counted in `protected_manual`, reason "protected …") unless their
    id is listed explicitly. `would_retire` counts actionable rows not already retired; `would_delete`
    counts all actionable rows.
    """
    await _require_book(book_id, session)
    rows, explicit_ids = await _cleanup_candidates(book_id, req, session)

    items: list[CanonCleanupItemOut] = []
    protected = would_retire = would_delete = 0
    for row in rows:
        body = row.body or ""
        summary = body[:120] + ("…" if len(body) > 120 else "") if body else None
        if _is_protected(row, explicit_ids):
            protected += 1
            reason = "protected: manual source (list id to override)"
        elif (row.status or "active") == "retired":
            would_delete += 1
            reason = "already retired"
        else:
            would_retire += 1
            would_delete += 1
            reason = "eligible"
        items.append(
            CanonCleanupItemOut(
                id=row.id,
                kind=row.kind,
                name=row.name,
                source=row.source or "manual",
                status=row.status or "active",
                summary=summary,
                reason=reason,
            )
        )

    return CanonCleanupPreviewOut(
        dry_run=True,
        matched=len(rows),
        would_retire=would_retire,
        would_delete=would_delete,
        protected_manual=protected,
        items=items,
    )


@router.post("/books/{book_id}/canon/retire", response_model=CanonRetireOut)
async def retire_canon(book_id: uuid.UUID, req: CanonCleanupIn, session: SessionDep) -> CanonRetireOut:
    """Soft-retire matched canon rows: set status='retired' so they drop out of RAG + `?status=active`
    (reversible via PUT). Manual-source rows are protected unless their id is listed explicitly."""
    await _require_book(book_id, session)
    rows, explicit_ids = await _cleanup_candidates(book_id, req, session)
    retired = protected = 0
    for row in rows:
        if _is_protected(row, explicit_ids):
            protected += 1
            continue
        if (row.status or "active") != "retired":
            row.status = "retired"
            retired += 1
    await session.commit()
    return CanonRetireOut(retired=retired, protected_manual=protected)


@router.delete("/books/{book_id}/canon", response_model=CanonBulkDeleteOut)
async def bulk_delete_canon(book_id: uuid.UUID, req: CanonCleanupIn, session: SessionDep) -> CanonBulkDeleteOut:
    """Hard-delete matched canon rows (bulk). Manual-source rows are protected unless their id is listed
    explicitly. The single-row DELETE /canon/{canon_id} is unaffected (no protection there)."""
    await _require_book(book_id, session)
    rows, explicit_ids = await _cleanup_candidates(book_id, req, session)
    doomed = [row.id for row in rows if not _is_protected(row, explicit_ids)]
    protected = len(rows) - len(doomed)
    if doomed:
        await session.execute(delete(CanonEntity).where(CanonEntity.id.in_(doomed)))
        await session.commit()
    return CanonBulkDeleteOut(deleted=len(doomed), protected_manual=protected)


@router.post("/books/{book_id}/canon/rebuild", status_code=202, response_model=CanonRebuildStartedOut)
async def rebuild_canon(
    book_id: uuid.UUID, session: SessionDep, background: BackgroundTasks
) -> CanonRebuildStartedOut:
    """Clean rebuild of repo-ingested canon from on-disk docs (series/canon) — the named alias of
    POST .../canon/ingest, sharing the same async background rebuild. Re-indexes only repo rows
    (doc_path IS NOT NULL); hand-authored / manual rows (doc_path IS NULL) are NEVER touched."""
    return await ingest_canon(book_id, session, background)
