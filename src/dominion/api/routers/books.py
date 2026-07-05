"""Books + the assembled manuscript (DESIGN §9, §13).

Creating/listing books seeds the planning flow; the manuscript endpoint assembles the approved prose
in reading order (latest approved version of each scene) for a continuous read of the book so far.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from dominion.api.deps import SessionDep
from dominion.api.scene_delete import hard_delete_scene
from dominion.shared.enums import SceneStatus
from dominion.shared.models import Book, Chapter, ChapterPacket, ProductionRun, Scene
from dominion.shared.schemas import (
    BookIn,
    BookOut,
    ChapterPipelineOut,
    ChapterRunFactsOut,
    ClearDraftScenesOut,
    ManuscriptChapter,
    ManuscriptOut,
    ManuscriptScene,
)
from dominion.workers.draft_readiness import derive_draft_readiness, fetch_book_readiness_rows
from dominion.workers.packet.approval_policy import approval_state as packet_approval_state

router = APIRouter(prefix="/books", tags=["books"])


def _as_int(v: object) -> int:
    """DraftReadinessOut's axis dicts are typed dict[str, object]; every count in them is an int."""
    return v if isinstance(v, int) else 0


@router.get("", response_model=list[BookOut])
async def list_books(session: SessionDep) -> list[Book]:
    rows = (await session.execute(select(Book).order_by(Book.created_at))).scalars().all()
    return list(rows)


@router.post("", response_model=BookOut)
async def create_book(body: BookIn, session: SessionDep) -> Book:
    book = Book(title=body.title, premise=body.premise)
    session.add(book)
    await session.commit()
    return book


@router.get("/{book_id}/manuscript", response_model=ManuscriptOut)
async def manuscript(book_id: uuid.UUID, session: SessionDep) -> ManuscriptOut:
    book = await session.get(Book, book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="book not found")

    chapters = (
        (await session.execute(select(Chapter).where(Chapter.book_id == book_id).order_by(Chapter.chapter_no)))
        .scalars()
        .all()
    )

    # Every approved scene in the book in ONE query (was one query per chapter — an N+1 on the widest
    # table). Ordered so the first row seen per (chapter_id, scene_no) is the latest version.
    scene_rows = (
        (
            await session.execute(
                select(Scene)
                .join(Chapter, Scene.chapter_id == Chapter.id)
                .where(Chapter.book_id == book_id, Scene.status == SceneStatus.APPROVED)
                .order_by(Scene.chapter_id, Scene.scene_no, Scene.version.desc())
            )
        )
        .scalars()
        .all()
    )
    latest_by_chapter: dict[uuid.UUID, dict[int, Scene]] = {}
    for sc in scene_rows:
        latest_by_chapter.setdefault(sc.chapter_id, {}).setdefault(sc.scene_no, sc)

    out_chapters: list[ManuscriptChapter] = []
    for chapter in chapters:
        latest = latest_by_chapter.get(chapter.id, {})
        if not latest:
            continue
        out_chapters.append(
            ManuscriptChapter(
                chapter_no=chapter.chapter_no,
                title=chapter.title,
                pov=chapter.pov,
                kind=chapter.kind,
                epigraph=chapter.epigraph,
                scenes=[ManuscriptScene(scene_no=no, prose=latest[no].prose) for no in sorted(latest)],
            )
        )

    return ManuscriptOut(book_id=book_id, title=book.title, chapters=out_chapters)


@router.get("/{book_id}/chapters/overview", response_model=list[ChapterPipelineOut])
async def chapters_overview(book_id: uuid.UUID, session: SessionDep) -> list[ChapterPipelineOut]:
    """Per-chapter pipeline facts for the Chapters command center in ONE request: chapter packet
    approval state, scene-contract + prose coverage, contract-violation counts, the authoritative
    draft gate (identical derivation to GET /chapters/{id}/draft/readiness), and the latest
    production run's status + issue/repair counts."""
    book = await session.get(Book, book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="book not found")

    chapters = (
        (await session.execute(select(Chapter).where(Chapter.book_id == book_id).order_by(Chapter.chapter_no)))
        .scalars()
        .all()
    )
    chapter_ids = [c.id for c in chapters]
    readiness_rows = await fetch_book_readiness_rows(session, book_id)

    # Latest chapter packet per chapter, ANY status — a proposed/blocked packet must read as such,
    # not as "no packet" (the readiness rows only carry the approved one).
    latest_packet: dict[uuid.UUID, ChapterPacket] = {}
    if chapter_ids:
        for row in (
            (
                await session.execute(
                    select(ChapterPacket)
                    .where(ChapterPacket.chapter_id.in_(chapter_ids))
                    .order_by(ChapterPacket.chapter_id, ChapterPacket.created_at.desc())
                )
            )
            .scalars()
            .all()
        ):
            latest_packet.setdefault(row.chapter_id, row)

    latest_run: dict[uuid.UUID, ProductionRun] = {}
    if chapter_ids:
        for run in (
            (
                await session.execute(
                    select(ProductionRun)
                    .where(ProductionRun.chapter_id.in_(chapter_ids))
                    .order_by(ProductionRun.chapter_id, ProductionRun.created_at.desc())
                )
            )
            .scalars()
            .all()
        ):
            latest_run.setdefault(run.chapter_id, run)

    out: list[ChapterPipelineOut] = []
    for chapter in chapters:
        rows = readiness_rows[chapter.id]
        readiness = derive_draft_readiness(rows)
        pkt = latest_packet.get(chapter.id)
        state, blockers = packet_approval_state(pkt) if pkt is not None else (None, [])

        # Same violation fold as GET /chapters/{id}/scene-packets/summary, summed chapter-wide.
        violation_counts: dict[str, int] = {}
        for sp in rows.sp_rows:
            warnings = sp.qa_warnings if isinstance(sp.qa_warnings, dict) else {}
            raw_violations = warnings.get("violations")
            for v in raw_violations if isinstance(raw_violations, list) else []:
                if isinstance(v, dict):
                    sev = str(v.get("severity") or "warn")
                    violation_counts[sev] = violation_counts.get(sev, 0) + 1

        run = latest_run.get(chapter.id)
        summary = run.summary_json if run is not None and isinstance(run.summary_json, dict) else {}
        prose = readiness.prose
        out.append(
            ChapterPipelineOut(
                chapter_id=chapter.id,
                chapter_no=chapter.chapter_no,
                packet_status=str(pkt.status) if pkt is not None else None,
                packet_approval_state=state,
                packet_approval_blockers=blockers,
                scene_packets_total=len(rows.sp_rows),
                scene_packets_approved=_as_int(readiness.scene_packets.get("approved")),
                scene_packets_blocked=_as_int(readiness.scene_packets.get("blocked")),
                scene_packets_stale=readiness.scene_packets_stale,
                scene_packets_rate_limited=_as_int(readiness.scene_packets.get("rate_limited")),
                violation_counts=violation_counts,
                scenes_with_prose=_as_int(prose.get("scenes_with_prose")),
                expected_scenes=_as_int(prose.get("expected_scenes")),
                assembly_ready=bool(prose.get("assembly_ready", False)),
                can_draft=readiness.can_draft,
                disabled_reason=readiness.disabled_reason,
                active_draft_jobs=readiness.active_draft_jobs,
                provider_rate_limited=readiness.provider_rate_limited,
                latest_run=ChapterRunFactsOut(
                    id=run.id,
                    status=str(run.status),
                    current_stage=run.current_stage,
                    issue_count=int(summary.get("issue_count") or 0),
                    repair_task_count=int(summary.get("repair_task_count") or 0),
                    updated_at=run.updated_at,
                )
                if run is not None
                else None,
            )
        )
    return out


@router.post("/{book_id}/scenes/clear-draft", response_model=ClearDraftScenesOut)
async def clear_draft_scenes(
    book_id: uuid.UUID,
    session: SessionDep,
    chapter_id: uuid.UUID | None = None,
) -> ClearDraftScenesOut:
    """Delete all non-approved scenes for a book (optional chapter scope). Approved prose is kept."""
    book = await session.get(Book, book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="book not found")

    chapter_ids = list((await session.execute(select(Chapter.id).where(Chapter.book_id == book_id))).scalars().all())
    if chapter_id is not None:
        if chapter_id not in chapter_ids:
            raise HTTPException(status_code=404, detail="chapter not found in book")
        chapter_ids = [chapter_id]

    rows = (
        (
            await session.execute(
                select(Scene.id).where(
                    Scene.chapter_id.in_(chapter_ids),
                    Scene.status != SceneStatus.APPROVED,
                )
            )
        )
        .scalars()
        .all()
    )
    scene_ids = list(rows)
    total_jobs = 0
    for sid in scene_ids:
        _, jobs_purged = await hard_delete_scene(session, sid)
        total_jobs += jobs_purged
    await session.commit()
    return ClearDraftScenesOut(purged=len(scene_ids), jobs_purged=total_jobs)
