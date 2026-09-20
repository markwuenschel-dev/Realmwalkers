"""Read-through endpoints (ADR 0035): an editor's notes on chapters the author supplies.

The router owns admission and the author's controls; the worker (`workers/read_through/run.py`) owns
the paid work. Admission is one transaction under a global advisory lock, because the all-books active
bound is a COUNT and no index can enforce it: two concurrent POSTs would both see room and both insert.
The per-book bound and request-id idempotency are also unique indexes, so a bypass of this lock still
fails closed as a 409 instead of a second paid run.

Everything the author will see is validated before anything is written: an over-long chapter is refused
up front by building its real prompt with the same builder the worker uses, rather than being admitted
and failing after earlier chapters were already paid for.

Reads lazily run `recover_read_throughs` first, so a run whose owner died with the last process shows as
interrupted on the next poll instead of "running" forever.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta

import structlog
from fastapi import APIRouter, BackgroundTasks, HTTPException
from sqlalchemy import ColumnElement, delete, func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from dominion.api.deps import SessionDep
from dominion.shared.config import settings
from dominion.shared.enums import (
    READ_THROUGH_ACTIVE_STATUSES,
    ReadThroughBookPassStatus,
    ReadThroughChapterStatus,
    ReadThroughNoteStatus,
    ReadThroughStatus,
)
from dominion.shared.models import Book, ReadThrough, ReadThroughChapter, ReadThroughNote
from dominion.shared.schemas import (
    ReadThroughAnchorOut,
    ReadThroughChapterOut,
    ReadThroughCreateIn,
    ReadThroughDeleteOut,
    ReadThroughNoteOut,
    ReadThroughNotePatchIn,
    ReadThroughOut,
    ReadThroughProseSuggestionOut,
    ReadThroughProseVariantOut,
    ReadThroughStatusOut,
    ReadThroughSummaryOut,
)
from dominion.workers import telemetry, telemetry_db
from dominion.workers.budget import BudgetExceeded
from dominion.workers.context.style_source import load_style_document
from dominion.workers.read_through import prompts, run
from dominion.workers.read_through.suggest import suggest_prose

log = structlog.get_logger()

router = APIRouter(tags=["read-through"])

# One key for every admission, across all books: the all-books bound is what it protects.
_ADMISSION_LOCK_KEY = "read_through_admission"

UQ_ACTIVE_BOOK = "uq_read_throughs_active_book"
UQ_BOOK_REQUEST = "uq_read_throughs_book_request"

BUSY_BOOK_DETAIL = "A read-through is already running for this book."
TOO_MANY_DETAIL = "Too many read-throughs are running right now. Try again when one finishes."
REQUEST_REUSED_DETAIL = "This request id was already used for different chapters. Start the read-through again."


def payload_sha256(body: ReadThroughCreateIn) -> str:
    """The identity of a submission: title and chapters exactly as sent, canonically serialized."""
    canonical = {
        "title": body.title or None,
        "chapters": [{"label": c.label, "text": c.text} for c in body.chapters],
    }
    encoded = json.dumps(canonical, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def violated_constraint(exc: IntegrityError) -> str | None:
    """The unique constraint/index this error names, if it is one of the two read-through ones.

    asyncpg exposes `constraint_name` on the driver error (sometimes one `__cause__` down); the message
    text is the fallback. Anything else returns None so the caller re-raises it untouched."""
    orig = getattr(exc, "orig", None)
    for candidate in (orig, getattr(orig, "__cause__", None)):
        name = getattr(candidate, "constraint_name", None)
        if name is not None:
            return name if name in (UQ_ACTIVE_BOOK, UQ_BOOK_REQUEST) else None
    message = str(orig if orig is not None else exc)
    for name in (UQ_ACTIVE_BOOK, UQ_BOOK_REQUEST):
        if name in message:
            return name
    return None


def _validate_chapters(body: ReadThroughCreateIn) -> None:
    """Refuse a submission the worker could not read, naming the chapter. No I/O."""
    chapters = body.chapters
    if not chapters:
        raise HTTPException(status_code=422, detail="Add at least one chapter.")
    limit = settings.read_through_max_chapters
    if len(chapters) > limit:
        raise HTTPException(
            status_code=422,
            detail=f"A read-through takes at most {limit} chapters; this one has {len(chapters)}.",
        )
    max_chars = settings.read_through_max_chapter_chars
    for n, chapter in enumerate(chapters, start=1):
        label = chapter.label.strip()
        if not label:
            raise HTTPException(status_code=422, detail=f"Chapter {n} needs a label.")
        if not chapter.text.strip():
            raise HTTPException(status_code=422, detail=f"Chapter {n} ({label}) has no text.")
        if len(chapter.text) > max_chars:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Chapter {n} ({label}) is too long: {len(chapter.text):,} characters; "
                    f"the limit is {max_chars:,}. Split it into smaller chapters."
                ),
            )


def _default_title(chapter_count: int) -> str:
    noun = "chapter" if chapter_count == 1 else "chapters"
    return f"{chapter_count} {noun} · {datetime.now(UTC).date().isoformat()}"


async def _find_by_request(session: AsyncSession, book_id: uuid.UUID, client_request_id: str) -> ReadThrough | None:
    return (
        await session.execute(
            select(ReadThrough)
            .where(ReadThrough.book_id == book_id, ReadThrough.client_request_id == client_request_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()


async def _active_count(session: AsyncSession) -> int:
    return (
        await session.execute(
            select(func.count()).select_from(ReadThrough).where(ReadThrough.status.in_(READ_THROUGH_ACTIVE_STATUSES))
        )
    ).scalar_one()


async def _book_has_active_run(session: AsyncSession, book_id: uuid.UUID) -> bool:
    found = (
        await session.execute(
            select(ReadThrough.id)
            .where(ReadThrough.book_id == book_id, ReadThrough.status.in_(READ_THROUGH_ACTIVE_STATUSES))
            .limit(1)
        )
    ).scalar_one_or_none()
    return found is not None


async def _summaries(session: AsyncSession, *where: ColumnElement[bool]) -> list[ReadThroughSummaryOut]:
    """Summaries with chapter counts from one aggregate query (no per-run chapter load)."""
    total = func.count(ReadThroughChapter.id)
    done = func.count(ReadThroughChapter.id).filter(ReadThroughChapter.status == ReadThroughChapterStatus.DONE.value)
    rows = (
        await session.execute(
            select(ReadThrough, total, done)
            .outerjoin(ReadThroughChapter, ReadThroughChapter.read_through_id == ReadThrough.id)
            .where(*where)
            .group_by(ReadThrough.id)
            .order_by(ReadThrough.created_at.desc(), ReadThrough.id.desc())
            .execution_options(populate_existing=True)
        )
    ).all()
    return [
        ReadThroughSummaryOut(
            id=rt.id,
            book_id=rt.book_id,
            title=rt.title,
            status=rt.status,
            chapters_total=chapters_total,
            chapters_done=chapters_done,
            book_pass_status=rt.book_pass_status,
            book_input_mode=rt.book_input_mode,
            error=rt.error,
            created_at=rt.created_at,
            started_at=rt.started_at,
            finished_at=rt.finished_at,
        )
        for rt, chapters_total, chapters_done in rows
    ]


async def _summary(session: AsyncSession, read_through_id: uuid.UUID) -> ReadThroughSummaryOut:
    found = await _summaries(session, ReadThrough.id == read_through_id)
    if not found:
        raise HTTPException(status_code=404, detail="read-through not found")
    return found[0]


async def _load(session: AsyncSession, read_through_id: uuid.UUID) -> ReadThrough:
    rt = (
        await session.execute(
            select(ReadThrough).where(ReadThrough.id == read_through_id).execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if rt is None:
        raise HTTPException(status_code=404, detail="read-through not found")
    return rt


async def _status_out(session: AsyncSession, read_through_id: uuid.UUID) -> ReadThroughStatusOut:
    rt = await _load(session, read_through_id)
    counts: dict[str, int] = {
        status: count
        for status, count in (
            await session.execute(
                select(ReadThroughChapter.status, func.count())
                .where(ReadThroughChapter.read_through_id == read_through_id)
                .group_by(ReadThroughChapter.status)
            )
        ).all()
    }
    current_label = await _chapter_label(session, read_through_id, ReadThroughChapterStatus.RUNNING)
    if current_label is None and rt.status == ReadThroughStatus.RUNNING.value:
        # Between chapters the owner has no running chapter; the next one it will read is still useful.
        current_label = await _chapter_label(session, read_through_id, ReadThroughChapterStatus.PENDING)
    return ReadThroughStatusOut(
        id=rt.id,
        status=rt.status,
        chapters_total=sum(counts.values()),
        chapters_done=counts.get(ReadThroughChapterStatus.DONE.value, 0),
        chapters_failed=counts.get(ReadThroughChapterStatus.FAILED.value, 0),
        chapters_skipped=counts.get(ReadThroughChapterStatus.SKIPPED.value, 0),
        current_label=current_label,
        attempts_used=rt.attempts_used,
        attempt_allowance=rt.attempt_allowance,
        tokens_charged=rt.tokens_charged,
        book_pass_status=rt.book_pass_status,
        book_input_mode=rt.book_input_mode,
        stop_requested=rt.stop_requested_at is not None,
        error=rt.error,
    )


async def _chapter_label(
    session: AsyncSession, read_through_id: uuid.UUID, status: ReadThroughChapterStatus
) -> str | None:
    return (
        await session.execute(
            select(ReadThroughChapter.label)
            .where(ReadThroughChapter.read_through_id == read_through_id, ReadThroughChapter.status == status.value)
            .order_by(ReadThroughChapter.position)
            .limit(1)
        )
    ).scalar_one_or_none()


async def _recover(session: AsyncSession) -> None:
    """Lazy recovery: interrupt runs whose ownership expired, so a read never reports a dead run as live."""
    await run.recover_read_throughs(session)
    await session.commit()


def _note_out(note: ReadThroughNote) -> ReadThroughNoteOut:
    return ReadThroughNoteOut(
        id=note.id,
        read_through_id=note.read_through_id,
        chapter_id=note.chapter_id,
        position=note.position,
        category=note.category,
        priority=note.priority,
        title=note.title,
        observation=note.observation,
        recommendation=note.recommendation,
        anchor_role=note.anchor_role,
        anchors=[ReadThroughAnchorOut.model_validate(anchor) for anchor in note.anchors or []],
        scope_chapter_ids=[uuid.UUID(str(cid)) for cid in note.scope_chapter_ids or []],
        status=note.status,
        created_at=note.created_at,
        updated_at=note.updated_at,
    )


async def _replay(session: AsyncSession, existing: ReadThrough, sha: str) -> ReadThroughSummaryOut:
    """The same request id again: the same chapters return the run it already started; different
    chapters are a conflict, never a silent second run."""
    if existing.payload_sha256 != sha:
        await session.commit()  # keep whatever recovery already did
        raise HTTPException(status_code=409, detail=REQUEST_REUSED_DETAIL)
    await session.commit()
    summary = await _summary(session, existing.id)
    await session.commit()  # end the read's transaction too; see create_read_through
    return summary


@router.post("/books/{book_id}/read-throughs", response_model=ReadThroughSummaryOut)
async def create_read_through(
    book_id: uuid.UUID, body: ReadThroughCreateIn, background: BackgroundTasks, session: SessionDep
) -> ReadThroughSummaryOut:
    """Start a read-through of the supplied chapters. The text is stored exactly as sent and read in
    the background; poll the status route. Sending the same `client_request_id` with the same chapters
    returns the run already started. 422 names a chapter that is empty or too long; 409 when this book
    already has a read-through running or too many are running overall."""
    if await session.get(Book, book_id) is None:
        raise HTTPException(status_code=404, detail="book not found")

    _validate_chapters(body)
    snapshot = prompts.snapshot_settings()
    voice_guide = await load_style_document(session, settings.voice_guide_path)
    for n, chapter in enumerate(body.chapters, start=1):
        label = chapter.label.strip()
        parts = prompts.build_chapter_prompt(
            snapshot, voice_guide, prompts.ChapterInput(position=n, label=label, text=chapter.text)
        )
        estimate = prompts.estimated_input_tokens(parts)
        if estimate > parts.input_budget:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Chapter {n} ({label}) is too long to read in one call (about {estimate:,} tokens; "
                    f"the limit is {parts.input_budget:,}). Split it into smaller chapters."
                ),
            )
    sha = payload_sha256(body)

    # --- admission: one transaction, serialized across all books -------------------------------------
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": _ADMISSION_LOCK_KEY})
    await run.recover_read_throughs(session)

    existing = await _find_by_request(session, book_id, body.client_request_id)
    if existing is not None:
        return await _replay(session, existing, sha)
    if await _active_count(session) >= settings.read_through_max_active:
        await session.commit()
        raise HTTPException(status_code=409, detail=TOO_MANY_DETAIL)
    if await _book_has_active_run(session, book_id):
        await session.commit()
        raise HTTPException(status_code=409, detail=BUSY_BOOK_DETAIL)

    chapter_count = len(body.chapters)
    rt = ReadThrough(
        id=uuid.uuid4(),
        book_id=book_id,
        title=body.title or _default_title(chapter_count),
        client_request_id=body.client_request_id,
        payload_sha256=sha,
        status=ReadThroughStatus.QUEUED.value,
        deadline_at=datetime.now(UTC) + timedelta(seconds=settings.read_through_run_deadline_s),
        settings_snapshot=snapshot,
        voice_guide_snapshot=voice_guide,
        attempt_allowance=run.attempt_allowance_for(chapter_count, snapshot),
        book_pass_status=ReadThroughBookPassStatus.PENDING.value,
    )
    session.add(rt)
    for n, chapter in enumerate(body.chapters, start=1):
        session.add(
            ReadThroughChapter(
                read_through_id=rt.id,
                position=n,
                label=chapter.label.strip(),
                text=chapter.text,  # never normalized: anchors index into exactly this string
                word_count=len(chapter.text.split()),
                status=ReadThroughChapterStatus.PENDING.value,
            )
        )
    try:
        await session.flush()
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        name = violated_constraint(exc)
        if name == UQ_ACTIVE_BOOK:
            raise HTTPException(status_code=409, detail=BUSY_BOOK_DETAIL) from exc
        if name == UQ_BOOK_REQUEST:
            winner = await _find_by_request(session, book_id, body.client_request_id)
            if winner is not None:
                return await _replay(session, winner, sha)
        raise

    summary = await _summary(session, rt.id)
    # End the read's transaction BEFORE the worker is scheduled. The request session is only closed after
    # the response, and background tasks run inside the response, so a transaction left open here would
    # hold a pooled connection idle in transaction for the whole run, across every model call.
    await session.commit()
    background.add_task(run.run_read_through, rt.id)
    return summary


@router.get("/books/{book_id}/read-throughs", response_model=list[ReadThroughSummaryOut])
async def list_read_throughs(book_id: uuid.UUID, session: SessionDep) -> list[ReadThroughSummaryOut]:
    """This book's read-throughs, newest first, with how many chapters each has finished."""
    await _recover(session)
    return await _summaries(session, ReadThrough.book_id == book_id)


@router.get("/read-throughs/{read_through_id}/status", response_model=ReadThroughStatusOut)
async def read_through_status(read_through_id: uuid.UUID, session: SessionDep) -> ReadThroughStatusOut:
    """Progress of one read-through: its state, chapter counts, the chapter being read, and what it
    has spent. No chapter text or notes."""
    await _recover(session)
    return await _status_out(session, read_through_id)


@router.get("/read-throughs/{read_through_id}", response_model=ReadThroughOut)
async def get_read_through(read_through_id: uuid.UUID, session: SessionDep) -> ReadThroughOut:
    """One read-through in full: every chapter with the text that was read, and every note. Notes that
    span chapters come first, then each chapter's notes in chapter order."""
    await _recover(session)
    rt = await _load(session, read_through_id)
    chapters = list(
        (
            await session.execute(
                select(ReadThroughChapter)
                .where(ReadThroughChapter.read_through_id == read_through_id)
                .order_by(ReadThroughChapter.position)
                .execution_options(populate_existing=True)
            )
        ).scalars()
    )
    notes = list(
        (
            await session.execute(
                select(ReadThroughNote)
                .outerjoin(ReadThroughChapter, ReadThroughNote.chapter_id == ReadThroughChapter.id)
                .where(ReadThroughNote.read_through_id == read_through_id)
                # Book notes have no chapter, so a NULL chapter position sorts them first.
                .order_by(
                    ReadThroughChapter.position.asc().nulls_first(),
                    ReadThroughNote.position,
                    ReadThroughNote.id,
                )
                .execution_options(populate_existing=True)
            )
        ).scalars()
    )
    return ReadThroughOut(
        id=rt.id,
        book_id=rt.book_id,
        title=rt.title,
        status=rt.status,
        error=rt.error,
        created_at=rt.created_at,
        started_at=rt.started_at,
        finished_at=rt.finished_at,
        deadline_at=rt.deadline_at,
        settings_snapshot=rt.settings_snapshot or {},
        voice_guide_used=rt.voice_guide_snapshot is not None,
        attempt_allowance=rt.attempt_allowance,
        attempts_used=rt.attempts_used,
        tokens_charged=rt.tokens_charged,
        accounting_gap=rt.accounting_gap,
        stop_requested=rt.stop_requested_at is not None,
        book_pass_status=rt.book_pass_status,
        book_pass_error=rt.book_pass_error,
        book_input_mode=rt.book_input_mode,
        book_chapter_ids=[uuid.UUID(str(cid)) for cid in rt.book_chapter_ids or []],
        book_model_used=rt.book_model_used,
        chapters=[
            ReadThroughChapterOut(
                id=ch.id,
                position=ch.position,
                label=ch.label,
                text=ch.text,
                word_count=ch.word_count,
                status=ch.status,
                digest=ch.digest,
                notes_dropped=ch.notes_dropped,
                notes_capped=ch.notes_capped,
                model_used=ch.model_used,
                attempts=ch.attempts,
                error=ch.error,
            )
            for ch in chapters
        ],
        notes=[_note_out(note) for note in notes],
    )


@router.post("/read-throughs/{read_through_id}/stop", response_model=ReadThroughStatusOut)
async def stop_read_through(read_through_id: uuid.UUID, session: SessionDep) -> ReadThroughStatusOut:
    """Stop a read-through. One that has not started stops at once and reads nothing; one that is
    reading stops after cancelling its current call (a request already sent to the model may still be
    billed). 409 if it has already finished."""
    # Every write is conditional on the status it was decided from, so a worker claiming the run between
    # the read and the write sends this round back to re-read rather than overwriting it.
    for _ in range(3):
        rt = await _load(session, read_through_id)
        if rt.status == ReadThroughStatus.STOPPING.value:
            await session.commit()
            return await _status_out(session, read_through_id)
        if rt.status == ReadThroughStatus.QUEUED.value:
            stopped = (
                await session.execute(
                    update(ReadThrough)
                    .where(ReadThrough.id == read_through_id, ReadThrough.status == ReadThroughStatus.QUEUED.value)
                    .values(
                        status=ReadThroughStatus.STOPPED.value,
                        stop_requested_at=func.now(),
                        finished_at=func.now(),
                        book_pass_status=ReadThroughBookPassStatus.NOT_RUN.value,
                    )
                    .returning(ReadThrough.id)
                )
            ).scalar_one_or_none()
            if stopped is not None:
                await session.execute(
                    update(ReadThroughChapter)
                    .where(
                        ReadThroughChapter.read_through_id == read_through_id,
                        ReadThroughChapter.status == ReadThroughChapterStatus.PENDING.value,
                    )
                    .values(status=ReadThroughChapterStatus.SKIPPED.value)
                )
                await session.commit()
                return await _status_out(session, read_through_id)
        elif rt.status == ReadThroughStatus.RUNNING.value:
            stopping = (
                await session.execute(
                    update(ReadThrough)
                    .where(ReadThrough.id == read_through_id, ReadThrough.status == ReadThroughStatus.RUNNING.value)
                    .values(status=ReadThroughStatus.STOPPING.value, stop_requested_at=func.now())
                    .returning(ReadThrough.id)
                )
            ).scalar_one_or_none()
            if stopping is not None:
                await session.commit()
                return await _status_out(session, read_through_id)
        else:
            raise HTTPException(status_code=409, detail="This read-through has already finished.")
        await session.rollback()
    raise HTTPException(status_code=409, detail="This read-through changed state while stopping. Try again.")


@router.patch("/read-through-notes/{note_id}", response_model=ReadThroughNoteOut)
async def update_read_through_note(
    note_id: uuid.UUID, body: ReadThroughNotePatchIn, session: SessionDep
) -> ReadThroughNoteOut:
    """Mark a note open, done or dismissed."""
    allowed = [s.value for s in ReadThroughNoteStatus]
    if body.status not in allowed:
        raise HTTPException(status_code=422, detail=f"A note's status must be one of: {', '.join(allowed)}.")
    note = await session.get(ReadThroughNote, note_id)
    if note is None:
        raise HTTPException(status_code=404, detail="note not found")
    note.status = body.status
    await session.commit()
    await session.refresh(note)
    return _note_out(note)


# The telemetry stage stamped on every call this endpoint makes. It matches the `stages` tuple on the
# `prose_suggestion_model` agent, which is what `STAGE_TO_SETTING` reads to attribute the row in Agent
# Operations. Deliberately NOT added to `PIPELINE_STAGE_ORDER` — that orders the scene pipeline, and
# this is an author-invoked one-off, exactly as `style_review.py:41-45` reasons for the style audit.
_PROSE_SUGGESTION_STAGE = "prose_suggestion"


async def _persist_suggestion_telemetry(
    session: AsyncSession, sink: telemetry.TelemetrySink, *, run_id: uuid.UUID, book_id: uuid.UUID
) -> bool:
    """Flush this request's captured calls to `llm_calls` and commit. True iff the rows landed.

    Never raises. It runs in a `finally`, including the path where the provider itself failed, and a
    bookkeeping error thrown from there would replace the original exception — the author would see a
    database message for what was actually a model outage.
    """
    if not sink.records:
        return False
    try:
        telemetry_db.persist_sink(session, sink, run_id=run_id, book_id=book_id)
        await session.commit()
    except Exception:
        await session.rollback()
        log.exception(
            "read_through.suggestion_telemetry_persist_failed",
            run_id=str(run_id),
            stage=_PROSE_SUGGESTION_STAGE,
            records=len(sink.records),
            detail="the suggestion succeeded; its cost is not in llm_calls and will not appear in Agent Operations",
        )
        return False
    return True


def _anchor_for_suggestion(note: ReadThroughNote) -> tuple[uuid.UUID | None, str] | None:
    """(chapter_id, quote) for the anchor a suggestion should attach to, or None if there is none.

    Prefers a LOCATED anchor over an ambiguous one: ambiguous means the server found the quote in
    several places and deliberately refused to choose, so writing against it would be picking one at
    random. An unlocated anchor carries no usable span at all.
    """
    anchors = note.anchors or []
    for wanted in ("located", "ambiguous"):
        for anchor in anchors:
            if anchor.get("state") != wanted:
                continue
            segments = anchor.get("segments") or []
            quote = "".join(str(s.get("text") or "") for s in segments) or str(anchor.get("text_quoted") or "")
            if quote.strip():
                chapter_id = anchor.get("chapter_id")
                return (uuid.UUID(str(chapter_id)) if chapter_id else None), quote
    return None


@router.post("/read-through-notes/{note_id}/prose-suggestion", response_model=ReadThroughProseSuggestionOut)
async def suggest_prose_for_note(note_id: uuid.UUID, session: SessionDep) -> ReadThroughProseSuggestionOut:
    """Draft prose answering this note, in the author's voice and against canon. Persists nothing.

    Separate from the read-through itself on purpose: the read-through refuses to write prose so its
    notes stay diagnoses rather than arguments for their own fix. This is the author, having read the
    note, asking one of them to show its work — so it is a distinct agent, a distinct paid call, and
    it happens only on this request.
    """
    note = await session.get(ReadThroughNote, note_id)
    if note is None:
        raise HTTPException(status_code=404, detail="note not found")

    read_through = await session.get(ReadThrough, note.read_through_id)
    if read_through is None:
        raise HTTPException(status_code=404, detail="the read-through this note belongs to is gone")

    anchored = _anchor_for_suggestion(note)
    if anchored is None:
        raise HTTPException(
            status_code=422,
            detail="This note has no anchor in the text, so there is no passage to write against.",
        )
    anchor_chapter_id, anchor_quote = anchored

    chapter_id = note.chapter_id or anchor_chapter_id
    chapter = await session.get(ReadThroughChapter, chapter_id) if chapter_id else None
    if chapter is None:
        raise HTTPException(
            status_code=422,
            detail="This note is not tied to a chapter in this read-through, so there is nothing to quote from.",
        )

    # One sink per request, but the run id is the READ-THROUGH's, not a fresh uuid: the suggestion is
    # spend belonging to that reading, and sharing the run keeps it on the same Telemetry row the
    # author already recognises (which `run_kind_for_stages` still labels "Read-through", because the
    # run's other stages map to `read_through_model`).
    sink = telemetry.TelemetrySink()
    telemetry_recorded = False
    try:
        with telemetry.call_context(telemetry.CallContext(sink=sink, stage=_PROSE_SUGGESTION_STAGE)):
            result = await suggest_prose(
                session,
                book_id=read_through.book_id,
                chapter_text=chapter.text,
                anchor_quote=anchor_quote,
                note_title=note.title,
                observation=note.observation,
                recommendation=note.recommendation,
                voice_guide=read_through.voice_guide_snapshot or "",
            )
    except BudgetExceeded as exc:
        raise HTTPException(
            status_code=413,
            detail=f"This chapter plus the author's standards is too long to write against: {exc}",
        ) from exc
    finally:
        # In `finally` deliberately: a provider failure is still a billable call that `llm.py` records
        # to the sink, and dropping that row would make exactly the failures worth investigating the
        # ones with no telemetry.
        telemetry_recorded = await _persist_suggestion_telemetry(
            session, sink, run_id=read_through.id, book_id=read_through.book_id
        )

    if not result.standards_loaded:
        # With no voice guide and no prose standards this is a general writing assistant wearing the
        # app's clothes, which is precisely what the author's rules exist to not be. Returning prose
        # anyway would hide that.
        raise HTTPException(
            status_code=503,
            detail=(
                "None of the author's standards could be loaded, so there is nothing to write in the "
                "voice of. Push them with `python -m dominion.tools.push_style`."
            ),
        )

    return ReadThroughProseSuggestionOut(
        suggestions=[
            ReadThroughProseVariantOut(mode=s.mode, anchor_quote=s.anchor_quote, prose=s.prose, why=s.why)
            for s in result.suggestions
        ],
        standards_loaded=result.standards_loaded,
        standards_missing=result.standards_missing,
        canon_sources=result.canon_sources,
        drift_scope_characters=result.drift_scope_characters,
        fabricated_dropped=result.fabricated_dropped,
        telemetry_recorded=telemetry_recorded,
        model=result.model,
        tokens_used=result.tokens_used,
    )


@router.delete("/read-throughs/{read_through_id}", response_model=ReadThroughDeleteOut)
async def delete_read_through(read_through_id: uuid.UUID, session: SessionDep) -> ReadThroughDeleteOut:
    """Delete a finished read-through with its chapter text and notes. 409 while it is still running."""
    deleted = (
        await session.execute(
            delete(ReadThrough)
            .where(ReadThrough.id == read_through_id, ReadThrough.status.not_in(READ_THROUGH_ACTIVE_STATUSES))
            .returning(ReadThrough.id)
        )
    ).scalar_one_or_none()
    if deleted is None:
        await session.rollback()
        exists = await session.get(ReadThrough, read_through_id)
        if exists is None:
            raise HTTPException(status_code=404, detail="read-through not found")
        raise HTTPException(status_code=409, detail="Stop the read-through before deleting it.")
    await session.commit()
    return ReadThroughDeleteOut(deleted=read_through_id)
