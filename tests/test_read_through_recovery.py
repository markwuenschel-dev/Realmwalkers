"""Read-through recovery (ADR 0035): only EXPIRED ownership, or a run nobody started, is interrupted.

`recover_read_throughs` runs at boot and lazily on every status read, so it runs WHILE live workers hold
leases. Every timestamp is set on the database clock (`now()`), as the worker and recovery both use it.
All prose is synthetic.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, update

from dominion.shared.config import settings
from dominion.shared.models import Book, ReadThrough, ReadThroughChapter, ReadThroughNote
from dominion.workers import llm, telemetry
from dominion.workers.budget import Usage
from dominion.workers.read_through import prompts
from dominion.workers.read_through import run as rt_run

TEXT = (
    "The ferry bell rang twice across the flat water, and nobody on the jetty moved to answer it.\n\n"
    "Tomas folded the timetable into quarters and put it in his coat, as if the times might change."
)
QUOTE = "The ferry bell rang twice across the flat water"


async def seed(
    db_factory: Any,
    *,
    status: str,
    lease_offset_s: float | None = None,
    owned: bool = True,
    created_offset_s: float = 0,
    chapter_statuses: tuple[str, ...] = ("done", "running", "pending"),
    book_pass_status: str = "pending",
) -> uuid.UUID:
    snapshot = prompts.snapshot_settings()
    async with db_factory() as s:
        book = Book(title="Synthetic Book")
        s.add(book)
        await s.flush()
        rt = ReadThrough(
            book_id=book.id,
            title="Synthetic read-through",
            client_request_id=str(uuid.uuid4()),
            payload_sha256="0" * 64,
            status=status,
            owner_token=uuid.uuid4() if owned else None,
            deadline_at=datetime.now(UTC) + timedelta(hours=3),
            settings_snapshot=snapshot,
            attempt_allowance=rt_run.attempt_allowance_for(len(chapter_statuses), snapshot),
            book_pass_status=book_pass_status,
        )
        s.add(rt)
        await s.flush()
        for position, chapter_status in enumerate(chapter_statuses, start=1):
            s.add(
                ReadThroughChapter(
                    read_through_id=rt.id,
                    position=position,
                    label=f"Chapter {position}",
                    text=TEXT,
                    word_count=len(TEXT.split()),
                    status=chapter_status,
                )
            )
        values: dict[str, Any] = {"created_at": func.now() - timedelta(seconds=created_offset_s)}
        if lease_offset_s is not None:
            values["lease_expires_at"] = func.now() + timedelta(seconds=lease_offset_s)
        await s.execute(
            update(ReadThrough)
            .where(ReadThrough.id == rt.id)
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        await s.commit()
        return rt.id


async def recover(db_factory: Any) -> int:
    async with db_factory() as s:
        count = await rt_run.recover_read_throughs(s)
        await s.commit()
    return count


async def state(db_factory: Any, rid: uuid.UUID) -> tuple[ReadThrough, list[ReadThroughChapter]]:
    async with db_factory() as s:
        run = await s.get(ReadThrough, rid)
        assert run is not None
        chapters = list(
            (
                await s.execute(
                    select(ReadThroughChapter)
                    .where(ReadThroughChapter.read_through_id == rid)
                    .order_by(ReadThroughChapter.position)
                )
            ).scalars()
        )
    return run, chapters


async def test_recovery_leaves_live_lease_alone(db_factory):
    running = await seed(db_factory, status="running", lease_offset_s=60)
    stopping = await seed(db_factory, status="stopping", lease_offset_s=60)
    fresh_queued = await seed(db_factory, status="queued", owned=False, chapter_statuses=("pending", "pending"))

    assert await recover(db_factory) == 0

    for rid, expected in ((running, "running"), (stopping, "stopping"), (fresh_queued, "queued")):
        run, chapters = await state(db_factory, rid)
        assert run.status == expected
        assert run.finished_at is None and run.error is None
        assert run.book_pass_status == "pending"
    _, chapters = await state(db_factory, running)
    assert [c.status for c in chapters] == ["done", "running", "pending"]


async def test_expired_lease_marked_interrupted(db_factory):
    running = await seed(db_factory, status="running", lease_offset_s=-5)
    stopping = await seed(
        db_factory, status="stopping", lease_offset_s=-1, chapter_statuses=("done", "done"), book_pass_status="skipped"
    )

    assert await recover(db_factory) == 2

    run, chapters = await state(db_factory, running)
    assert run.status == "interrupted"
    assert run.error == rt_run.INTERRUPTED_MESSAGE
    assert run.finished_at is not None
    assert run.book_pass_status == "not_run"
    assert [c.status for c in chapters] == ["done", "failed", "skipped"]
    assert chapters[1].error == rt_run.INTERRUPTED_CHAPTER_MESSAGE

    run, chapters = await state(db_factory, stopping)
    assert run.status == "interrupted"
    assert run.book_pass_status == "skipped"  # a settled book-pass outcome is kept
    assert [c.status for c in chapters] == ["done", "done"]

    assert await recover(db_factory) == 0  # idempotent: terminal rows are never touched again


async def test_queued_past_admission_deadline_interrupted(db_factory):
    deadline = settings.read_through_admission_deadline_s
    stale = await seed(
        db_factory,
        status="queued",
        owned=False,
        created_offset_s=deadline + 60,
        chapter_statuses=("pending", "pending"),
    )
    young = await seed(
        db_factory,
        status="queued",
        owned=False,
        created_offset_s=max(deadline - 60, 0),
        chapter_statuses=("pending",),
    )

    assert await recover(db_factory) == 1

    run, chapters = await state(db_factory, stale)
    assert run.status == "interrupted"
    assert run.error == f"Didn't start within {deadline} seconds — nothing was spent."
    assert run.book_pass_status == "not_run"
    assert run.finished_at is not None
    assert [c.status for c in chapters] == ["skipped", "skipped"]

    run, chapters = await state(db_factory, young)
    assert run.status == "queued"
    assert [c.status for c in chapters] == ["pending"]


async def test_recovered_run_rejects_late_owner_write(db_factory, monkeypatch):
    """The owner is still awaiting the provider when its lease expires and recovery interrupts the run. The
    reply that arrives afterwards is paid for, but it must not overwrite the interrupted state."""
    rid = await seed(db_factory, status="queued", owned=False, chapter_statuses=("pending", "pending"))
    async with db_factory() as s:
        snapshot = prompts.snapshot_settings()
        snapshot["model"] = "rt-test-primary"
        await s.execute(
            update(ReadThrough)
            .where(ReadThrough.id == rid)
            .values(settings_snapshot=snapshot)
            .execution_options(synchronize_session=False)
        )
        await s.commit()
    calls: list[dict[str, Any]] = []

    async def fake_complete(**kwargs: Any) -> tuple[str, Usage]:
        calls.append(kwargs)
        async with db_factory() as s:
            await s.execute(
                update(ReadThrough)
                .where(ReadThrough.id == rid)
                .values(lease_expires_at=func.now() - timedelta(seconds=1))
                .execution_options(synchronize_session=False)
            )
            await s.commit()
        assert await recover(db_factory) == 1
        usage = Usage(input_tokens=900, output_tokens=150)
        telemetry.record(
            model=kwargs["model"],
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_creation_tokens=0,
            cache_read_tokens=0,
            truncated=False,
            latency_ms=4,
        )
        digest = {"summary": "A ferry bell rings and nobody answers."}
        reply = {
            "notes": [
                {
                    "category": "clarity",
                    "priority": "low",
                    "title": "Who rang the bell",
                    "observation": "The reader cannot tell who rang it.",
                    "recommendation": "Show the bell ringer or who hears it.",
                    "quotes": [QUOTE],
                }
            ],
            "capped": False,
            "digest": digest,
        }
        return json.dumps(reply), usage

    monkeypatch.setattr(llm, "complete", fake_complete)

    await rt_run.run_read_through(rid, session_factory=db_factory)

    assert len(calls) == 1
    run, chapters = await state(db_factory, rid)
    assert run.status == "interrupted"
    assert run.error == rt_run.INTERRUPTED_MESSAGE
    assert run.tokens_charged == 0  # the late charge was refused with the rest
    assert [c.status for c in chapters] == ["failed", "skipped"]
    assert chapters[0].digest is None
    async with db_factory() as s:
        notes = list((await s.execute(select(ReadThroughNote).where(ReadThroughNote.read_through_id == rid))).scalars())
    assert notes == []
