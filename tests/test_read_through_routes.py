"""Read-through routes (ADR 0035): admission, lazy recovery, reads, stop, note status and delete.

Every chapter here is synthetic prose. No model is touched: the admission tests replace the background
worker with a recorder (`scheduled`), and the end-to-end test fakes `llm.complete` at the provider seam.
The voice guide is a synthetic tmp file, so the real `series/` guide is never read into a snapshot.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select, text, update

from dominion.api.routers import read_throughs as router_mod
from dominion.shared.config import settings
from dominion.shared.models import Book, LlmCall, ReadThrough, ReadThroughChapter, ReadThroughNote
from dominion.shared.schemas import ReadThroughCreateIn
from dominion.workers import llm, telemetry
from dominion.workers.budget import Usage
from dominion.workers.read_through import prompts
from dominion.workers.read_through import run as run_mod

VOICE_GUIDE = "## Voice\n\nPlain, close third person. Short sentences under pressure.\n"

# CRLF, trailing spaces and an astral character: the snapshot must keep all of them byte for byte.
CHAPTER_ONE = (
    "The lighthouse keeper counted the gulls twice before breakfast.\r\n\r\n"
    "Nobody came up the hill that week, and the lamp oil ran low.  \n"
)
CHAPTER_TWO = "Mira found the ledger under a loose board. It listed ships that never docked \U0001f41a, nine of them.\n"


@pytest.fixture(autouse=True)
def _synthetic_voice_guide(tmp_path, monkeypatch) -> None:
    path = tmp_path / "voice_guide.md"
    path.write_text(VOICE_GUIDE, encoding="utf-8")
    monkeypatch.setattr(settings, "voice_guide_path", str(path))
    monkeypatch.setattr(settings, "read_through_max_active", 2)


@pytest.fixture
def scheduled(monkeypatch) -> list[uuid.UUID]:
    """Replace the background worker with a recorder, so admission tests never reach a model."""
    calls: list[uuid.UUID] = []

    async def fake_run_read_through(read_through_id: uuid.UUID, *, session_factory=None) -> None:
        calls.append(read_through_id)

    monkeypatch.setattr(run_mod, "run_read_through", fake_run_read_through)
    return calls


async def _book(db_factory, title: str = "Synthetic Book") -> uuid.UUID:
    async with db_factory() as s:
        book = Book(title=title)
        s.add(book)
        await s.commit()
        return book.id


def _body(chapters: Sequence[tuple[str, str]], *, request_id: str | None = None) -> dict[str, Any]:
    return {
        "client_request_id": request_id or f"req-{uuid.uuid4()}",
        "chapters": [{"label": label, "text": text} for label, text in chapters],
    }


async def _seed_run(
    db_factory,
    book_id: uuid.UUID,
    *,
    status: str,
    chapters: Sequence[tuple[str, str, str]] = (),
    live_lease: bool = True,
    **fields: Any,
) -> tuple[uuid.UUID, list[uuid.UUID]]:
    """A run written directly, as the worker would leave it. Running/stopping rows get an owner and a
    lease, live by default so lazy recovery leaves them alone."""
    now = datetime.now(UTC)
    values: dict[str, Any] = {
        "book_id": book_id,
        "title": "Seeded",
        "client_request_id": f"seed-{uuid.uuid4()}",
        "payload_sha256": "0" * 64,
        "status": status,
        "deadline_at": now + timedelta(hours=3),
        "settings_snapshot": {"model": "fake-model"},
        "attempt_allowance": 2 * max(1, len(chapters)) + 2,
    }
    if status in ("running", "stopping"):
        values["owner_token"] = uuid.uuid4()
        values["lease_expires_at"] = now + timedelta(hours=1) if live_lease else now - timedelta(minutes=10)
        values["started_at"] = now - timedelta(minutes=1)
    values.update(fields)
    async with db_factory() as s:
        rt = ReadThrough(**values)
        s.add(rt)
        await s.flush()
        chapter_ids: list[uuid.UUID] = []
        for position, (label, text, chapter_status) in enumerate(chapters, start=1):
            chapter = ReadThroughChapter(
                read_through_id=rt.id,
                position=position,
                label=label,
                text=text,
                word_count=len(text.split()),
                status=chapter_status,
            )
            s.add(chapter)
            await s.flush()
            chapter_ids.append(chapter.id)
        await s.commit()
        return rt.id, chapter_ids


async def _run_count(db_factory, book_id: uuid.UUID) -> int:
    async with db_factory() as s:
        return (
            await s.execute(select(func.count()).select_from(ReadThrough).where(ReadThrough.book_id == book_id))
        ).scalar_one()


async def _set_status(db_factory, read_through_id: uuid.UUID, status: str) -> None:
    async with db_factory() as s:
        await s.execute(update(ReadThrough).where(ReadThrough.id == read_through_id).values(status=status))
        await s.commit()


# --- admission -------------------------------------------------------------------------------------


async def test_post_creates_queued_run_and_snapshots_text_unchanged(app_client, db_factory, scheduled):
    book_id = await _book(db_factory)
    body = _body([("  Chapter One  ", CHAPTER_ONE), ("Chapter Two", CHAPTER_TWO)])

    resp = await app_client.post(f"/books/{book_id}/read-throughs", json=body)
    assert resp.status_code == 200, resp.text
    summary = resp.json()
    assert summary["status"] == "queued"
    assert summary["book_id"] == str(book_id)
    assert (summary["chapters_total"], summary["chapters_done"]) == (2, 0)
    assert summary["book_pass_status"] == "pending"
    assert summary["title"].startswith("2 chapters · ")
    run_id = uuid.UUID(summary["id"])
    # app_client runs BackgroundTasks before the response returns: exactly one worker, for this run.
    assert scheduled == [run_id]

    async with db_factory() as s:
        rt = await s.get(ReadThrough, run_id)
        chapters = list(
            (
                await s.execute(
                    select(ReadThroughChapter)
                    .where(ReadThroughChapter.read_through_id == run_id)
                    .order_by(ReadThroughChapter.position)
                )
            ).scalars()
        )
    assert rt is not None
    assert rt.client_request_id == body["client_request_id"]
    assert rt.payload_sha256 == router_mod.payload_sha256(ReadThroughCreateIn.model_validate(body))
    assert rt.voice_guide_snapshot == VOICE_GUIDE
    snapshot = prompts.snapshot_settings()
    assert rt.settings_snapshot == json.loads(json.dumps(snapshot))
    assert rt.attempt_allowance == run_mod.attempt_allowance_for(2, snapshot)
    assert rt.owner_token is None and rt.started_at is None
    assert rt.deadline_at > datetime.now(UTC) + timedelta(seconds=settings.read_through_run_deadline_s - 120)
    assert [(c.position, c.label, c.status) for c in chapters] == [
        (1, "Chapter One", "pending"),
        (2, "Chapter Two", "pending"),
    ]
    assert [c.text for c in chapters] == [CHAPTER_ONE, CHAPTER_TWO]
    assert [c.word_count for c in chapters] == [len(CHAPTER_ONE.split()), len(CHAPTER_TWO.split())]

    # And over the wire: the detail route returns the same raw text.
    detail = await app_client.get(f"/read-throughs/{run_id}")
    assert detail.status_code == 200, detail.text
    assert [c["text"] for c in detail.json()["chapters"]] == [CHAPTER_ONE, CHAPTER_TWO]
    assert detail.json()["voice_guide_used"] is True


async def test_same_request_id_same_payload_returns_existing_run(app_client, db_factory, scheduled):
    book_id = await _book(db_factory)
    body = _body([("One", CHAPTER_ONE)])

    first = await app_client.post(f"/books/{book_id}/read-throughs", json=body)
    second = await app_client.post(f"/books/{book_id}/read-throughs", json=body)

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert second.json()["id"] == first.json()["id"]
    # A retried POST must never pay twice: no second row and no second worker.
    assert scheduled == [uuid.UUID(first.json()["id"])]
    assert await _run_count(db_factory, book_id) == 1


async def test_same_request_id_different_payload_409(app_client, db_factory, scheduled):
    book_id = await _book(db_factory)
    body = _body([("One", CHAPTER_ONE)])
    assert (await app_client.post(f"/books/{book_id}/read-throughs", json=body)).status_code == 200

    changed = _body([("One", CHAPTER_ONE + "One more line.")], request_id=body["client_request_id"])
    resp = await app_client.post(f"/books/{book_id}/read-throughs", json=changed)

    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"] == router_mod.REQUEST_REUSED_DETAIL
    assert len(scheduled) == 1
    assert await _run_count(db_factory, book_id) == 1


async def test_admission_bound_across_books_409(app_client, db_factory, scheduled):
    first_other = await _book(db_factory, "Other A")
    second_other = await _book(db_factory, "Other B")
    target = await _book(db_factory, "Target")
    queued_id, _ = await _seed_run(db_factory, first_other, status="queued")
    await _seed_run(db_factory, second_other, status="running")
    body = _body([("One", CHAPTER_ONE)])

    resp = await app_client.post(f"/books/{target}/read-throughs", json=body)
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"] == router_mod.TOO_MANY_DETAIL
    assert scheduled == []
    assert await _run_count(db_factory, target) == 0

    # The bound counts runs across books, not books: once one finishes, the target gets the slot.
    await _set_status(db_factory, queued_id, "succeeded")
    retry = await app_client.post(f"/books/{target}/read-throughs", json=body)
    assert retry.status_code == 200, retry.text
    assert len(scheduled) == 1


async def test_per_book_active_409(app_client, db_factory, scheduled):
    book_id = await _book(db_factory)
    # `stopping` still holds the book's slot: the owner has not reached a terminal state yet.
    await _seed_run(db_factory, book_id, status="stopping")

    resp = await app_client.post(f"/books/{book_id}/read-throughs", json=_body([("One", CHAPTER_ONE)]))

    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"] == router_mod.BUSY_BOOK_DETAIL
    assert scheduled == []
    assert await _run_count(db_factory, book_id) == 1


async def test_running_index_integrity_error_maps_409(app_client, db_factory, scheduled, monkeypatch):
    """With both pre-checks bypassed, the partial unique index is what refuses the second active run,
    and the router maps that constraint to the same 409 rather than a 500."""
    book_id = await _book(db_factory)
    await _seed_run(db_factory, book_id, status="running")

    async def no_active_run(*_args: Any, **_kwargs: Any) -> bool:
        return False

    async def no_active_runs(*_args: Any, **_kwargs: Any) -> int:
        return 0

    monkeypatch.setattr(router_mod, "_book_has_active_run", no_active_run)
    monkeypatch.setattr(router_mod, "_active_count", no_active_runs)

    resp = await app_client.post(f"/books/{book_id}/read-throughs", json=_body([("One", CHAPTER_ONE)]))

    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"] == router_mod.BUSY_BOOK_DETAIL
    assert scheduled == []
    assert await _run_count(db_factory, book_id) == 1


async def test_request_id_integrity_error_replays_existing_run(app_client, db_factory, scheduled, monkeypatch):
    """If the request-id lookup misses and the insert hits `uq_read_throughs_book_request`, the router
    re-reads the winner and applies the same rule: same chapters return it, different ones are a 409."""
    book_id = await _book(db_factory)
    body = _body([("One", CHAPTER_ONE)])
    first = await app_client.post(f"/books/{book_id}/read-throughs", json=body)
    assert first.status_code == 200, first.text
    run_id = uuid.UUID(first.json()["id"])
    # Terminal, so only the request-id constraint can collide.
    await _set_status(db_factory, run_id, "succeeded")

    real_find = router_mod._find_by_request
    missed = {"once": False}

    async def miss_once(session, book, request_id):
        if not missed["once"]:
            missed["once"] = True
            return None
        return await real_find(session, book, request_id)

    monkeypatch.setattr(router_mod, "_find_by_request", miss_once)
    replay = await app_client.post(f"/books/{book_id}/read-throughs", json=body)
    assert replay.status_code == 200, replay.text
    assert replay.json()["id"] == str(run_id)
    assert scheduled == [run_id]

    missed["once"] = False
    changed = _body([("One", CHAPTER_ONE + "Changed.")], request_id=body["client_request_id"])
    conflict = await app_client.post(f"/books/{book_id}/read-throughs", json=changed)
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["detail"] == router_mod.REQUEST_REUSED_DETAIL
    assert await _run_count(db_factory, book_id) == 1


async def test_post_422_names_oversized_chapter(app_client, db_factory, scheduled, monkeypatch):
    book_id = await _book(db_factory)
    big_text = " ".join(["The tide came over the causeway and the keeper wrote it down."] * 400)
    small_parts = prompts.build_chapter_prompt(
        prompts.snapshot_settings(), VOICE_GUIDE, prompts.ChapterInput(position=1, label="Small", text=CHAPTER_ONE)
    )
    big_parts = prompts.build_chapter_prompt(
        prompts.snapshot_settings(), VOICE_GUIDE, prompts.ChapterInput(position=2, label="Big", text=big_text)
    )
    small_estimate = prompts.estimated_input_tokens(small_parts)
    big_estimate = prompts.estimated_input_tokens(big_parts)
    assert small_estimate < big_estimate
    budget = (small_estimate + big_estimate) // 2
    monkeypatch.setattr(settings, "read_through_chapter_input_budget", budget)
    # Precondition: the builder takes its budget from the snapshot of this setting.
    rebuilt = prompts.build_chapter_prompt(
        prompts.snapshot_settings(), VOICE_GUIDE, prompts.ChapterInput(position=2, label="Big", text=big_text)
    )
    assert rebuilt.input_budget == budget

    resp = await app_client.post(
        f"/books/{book_id}/read-throughs", json=_body([("Small", CHAPTER_ONE), ("Big", big_text)])
    )

    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"] == (
        f"Chapter 2 (Big) is too long to read in one call (about {prompts.estimated_input_tokens(rebuilt):,} "
        f"tokens; the limit is {budget:,}). Split it into smaller chapters."
    )
    assert scheduled == []
    assert await _run_count(db_factory, book_id) == 0


async def test_post_422_empty_and_too_many(app_client, db_factory, scheduled, monkeypatch):
    book_id = await _book(db_factory)
    monkeypatch.setattr(settings, "read_through_max_chapters", 2)
    monkeypatch.setattr(settings, "read_through_max_chapter_chars", 100)
    cases: list[tuple[list[tuple[str, str]], str]] = [
        ([], "Add at least one chapter."),
        (
            [("One", "Some text."), ("Two", "More text."), ("Three", "Even more.")],
            "A read-through takes at most 2 chapters; this one has 3.",
        ),
        ([("   ", "Some text.")], "Chapter 1 needs a label."),
        ([("One", "Some text."), ("Two", " \n\t ")], "Chapter 2 (Two) has no text."),
        (
            [("One", "Some text."), ("Long", "word " * 30)],
            "Chapter 2 (Long) is too long: 150 characters; the limit is 100. Split it into smaller chapters.",
        ),
    ]
    for chapters, detail in cases:
        resp = await app_client.post(f"/books/{book_id}/read-throughs", json=_body(chapters))
        assert resp.status_code == 422, (chapters, resp.text)
        assert resp.json()["detail"] == detail

    missing = await app_client.post(f"/books/{uuid.uuid4()}/read-throughs", json=_body([("One", "Some text.")]))
    assert missing.status_code == 404
    assert missing.json()["detail"] == "book not found"
    assert scheduled == []
    assert await _run_count(db_factory, book_id) == 0


# --- reads -----------------------------------------------------------------------------------------


async def test_status_lazily_recovers_expired_lease(app_client, db_factory):
    dead_book = await _book(db_factory, "Dead")
    live_book = await _book(db_factory, "Live")
    reading_book = await _book(db_factory, "Reading")
    dead_id, _ = await _seed_run(
        db_factory,
        dead_book,
        status="running",
        live_lease=False,
        chapters=[("One", CHAPTER_ONE, "done"), ("Two", CHAPTER_TWO, "running")],
    )
    between_id, _ = await _seed_run(
        db_factory,
        live_book,
        status="running",
        chapters=[("One", CHAPTER_ONE, "done"), ("Two", CHAPTER_TWO, "pending")],
    )
    reading_id, _ = await _seed_run(
        db_factory,
        reading_book,
        status="running",
        chapters=[("One", CHAPTER_ONE, "running"), ("Two", CHAPTER_TWO, "pending")],
    )

    resp = await app_client.get(f"/read-throughs/{dead_id}/status")
    assert resp.status_code == 200, resp.text
    status = resp.json()
    assert status["status"] == "interrupted"
    assert status["chapters_total"] == 2
    assert status["chapters_done"] == 1
    assert status["chapters_failed"] == 1  # the chapter being read when the owner died
    assert status["current_label"] is None
    async with db_factory() as s:
        dead = await s.get(ReadThrough, dead_id)
    assert dead is not None and dead.status == "interrupted"  # committed, not just reported

    # A live lease is never touched, and the label names what is being (or about to be) read.
    between = (await app_client.get(f"/read-throughs/{between_id}/status")).json()
    assert (between["status"], between["current_label"]) == ("running", "Two")
    reading = (await app_client.get(f"/read-throughs/{reading_id}/status")).json()
    assert (reading["status"], reading["current_label"]) == ("running", "One")

    assert (await app_client.get(f"/read-throughs/{uuid.uuid4()}/status")).status_code == 404


async def test_list_is_newest_first_with_chapter_counts(app_client, db_factory):
    book_id = await _book(db_factory)
    other_book = await _book(db_factory, "Other")
    now = datetime.now(UTC)
    older_id, _ = await _seed_run(
        db_factory,
        book_id,
        status="partial",
        created_at=now - timedelta(hours=2),
        chapters=[("One", CHAPTER_ONE, "done"), ("Two", CHAPTER_TWO, "failed")],
    )
    newer_id, _ = await _seed_run(
        db_factory,
        book_id,
        status="succeeded",
        created_at=now - timedelta(minutes=5),
        chapters=[("One", CHAPTER_ONE, "done")],
    )
    await _seed_run(db_factory, other_book, status="succeeded")

    resp = await app_client.get(f"/books/{book_id}/read-throughs")

    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert [r["id"] for r in rows] == [str(newer_id), str(older_id)]
    assert [(r["chapters_total"], r["chapters_done"]) for r in rows] == [(1, 1), (2, 1)]


async def test_get_orders_book_notes_first_and_parses_anchors(app_client, db_factory):
    book_id = await _book(db_factory)
    run_id, (ch1, ch2) = await _seed_run(
        db_factory, book_id, status="succeeded", chapters=[("One", CHAPTER_ONE, "done"), ("Two", CHAPTER_TWO, "done")]
    )
    start = CHAPTER_TWO.index("ledger")
    located = {
        "chapter_id": str(ch2),
        "state": "located",
        "text_quoted": "ledger",
        "segments": [{"start": start, "end": start + len("ledger"), "text": "ledger"}],
        "candidates": [],
        "candidate_count": 0,
    }
    placements = [m.start() for m in re.finditer("the ", CHAPTER_ONE)]
    ambiguous = {
        "chapter_id": str(ch1),
        "state": "ambiguous",
        "text_quoted": "the",
        "segments": [],
        "candidates": [[{"start": p, "end": p + 3, "text": "the"}] for p in placements],
        "candidate_count": len(placements),
    }

    def note(title: str, chapter_id: uuid.UUID | None, position: int, **fields: Any) -> ReadThroughNote:
        return ReadThroughNote(
            read_through_id=run_id,
            chapter_id=chapter_id,
            position=position,
            category="structure",
            priority="high",
            title=title,
            observation="An observation.",
            recommendation="A recommendation.",
            **fields,
        )

    async with db_factory() as s:
        # Inserted out of order on purpose.
        s.add_all(
            [
                note("chapter two, first", ch2, 1, anchors=[located]),
                note("chapter one, second", ch1, 2),
                note("book, second", None, 2, scope_chapter_ids=[str(ch1), str(ch2)], anchor_role="location"),
                note("chapter one, first", ch1, 1, anchors=[ambiguous]),
                note("book, first", None, 1),
            ]
        )
        await s.commit()

    resp = await app_client.get(f"/read-throughs/{run_id}")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [c["label"] for c in body["chapters"]] == ["One", "Two"]
    assert body["voice_guide_used"] is False
    notes = body["notes"]
    assert [n["title"] for n in notes] == [
        "book, first",
        "book, second",
        "chapter one, first",
        "chapter one, second",
        "chapter two, first",
    ]
    by_title = {n["title"]: n for n in notes}
    assert by_title["chapter two, first"]["anchors"] == [located]
    parsed_ambiguous = by_title["chapter one, first"]["anchors"][0]
    assert parsed_ambiguous["state"] == "ambiguous"
    assert parsed_ambiguous["candidate_count"] == len(placements) == len(parsed_ambiguous["candidates"])
    assert by_title["book, second"]["chapter_id"] is None
    assert by_title["book, second"]["scope_chapter_ids"] == [str(ch1), str(ch2)]
    assert by_title["book, second"]["anchor_role"] == "location"

    assert (await app_client.get(f"/read-throughs/{uuid.uuid4()}")).status_code == 404


async def test_digest_mode_and_omitted_chapters_visible_after_reload(app_client, db_factory):
    book_id = await _book(db_factory)
    run_id, ids = await _seed_run(
        db_factory,
        book_id,
        status="partial",
        chapters=[
            ("One", CHAPTER_ONE, "done"),
            ("Two", CHAPTER_TWO, "done"),
            ("Three", CHAPTER_ONE, "done"),
            ("Four", CHAPTER_TWO, "failed"),
        ],
    )
    async with db_factory() as s:
        await s.execute(
            update(ReadThrough)
            .where(ReadThrough.id == run_id)
            .values(
                book_pass_status="done",
                book_input_mode="digests",
                book_chapter_ids=[str(ids[0]), str(ids[2])],
                book_model_used="fake-model",
            )
        )
        await s.commit()

    for _ in range(2):  # a reload must show exactly what the first read showed
        body = (await app_client.get(f"/read-throughs/{run_id}")).json()
        assert body["book_input_mode"] == "digests"
        assert body["book_pass_status"] == "done"
        assert body["book_chapter_ids"] == [str(ids[0]), str(ids[2])]
        assert [c["status"] for c in body["chapters"]] == ["done", "done", "done", "failed"]
        omitted = [
            c["label"] for c in body["chapters"] if c["status"] == "done" and c["id"] not in body["book_chapter_ids"]
        ]
        assert omitted == ["Two"]

    summary = (await app_client.get(f"/books/{book_id}/read-throughs")).json()[0]
    assert (summary["status"], summary["book_input_mode"]) == ("partial", "digests")
    assert (summary["chapters_total"], summary["chapters_done"]) == (4, 3)
    status = (await app_client.get(f"/read-throughs/{run_id}/status")).json()
    assert (status["chapters_failed"], status["book_input_mode"]) == (1, "digests")


# --- controls --------------------------------------------------------------------------------------


async def test_stop_route_states(app_client, db_factory, scheduled):
    queued_book = await _book(db_factory, "Queued")
    running_book = await _book(db_factory, "Running")
    done_book = await _book(db_factory, "Done")

    # queued -> stopped at once: nothing was read, so every chapter is skipped and the book pass never runs.
    created = await app_client.post(
        f"/books/{queued_book}/read-throughs", json=_body([("One", CHAPTER_ONE), ("Two", CHAPTER_TWO)])
    )
    queued_id = created.json()["id"]
    stopped = await app_client.post(f"/read-throughs/{queued_id}/stop")
    assert stopped.status_code == 200, stopped.text
    body = stopped.json()
    assert (body["status"], body["stop_requested"], body["chapters_skipped"]) == ("stopped", True, 2)
    async with db_factory() as s:
        row = await s.get(ReadThrough, uuid.UUID(queued_id))
    assert row is not None and row.finished_at is not None and row.book_pass_status == "not_run"

    # running -> stopping: the owner still holds the run and stops itself.
    running_id, _ = await _seed_run(
        db_factory,
        running_book,
        status="running",
        chapters=[("One", CHAPTER_ONE, "done"), ("Two", CHAPTER_TWO, "running")],
    )
    stopping = await app_client.post(f"/read-throughs/{running_id}/stop")
    assert stopping.status_code == 200, stopping.text
    assert (stopping.json()["status"], stopping.json()["stop_requested"]) == ("stopping", True)
    async with db_factory() as s:
        row = await s.get(ReadThrough, running_id)
    assert row is not None and row.stop_requested_at is not None and row.owner_token is not None
    again = await app_client.post(f"/read-throughs/{running_id}/stop")
    assert (again.status_code, again.json()["status"]) == (200, "stopping")

    # terminal -> 409, including a run this route just stopped.
    done_id, _ = await _seed_run(db_factory, done_book, status="succeeded")
    for finished in (done_id, queued_id):
        refused = await app_client.post(f"/read-throughs/{finished}/stop")
        assert refused.status_code == 409, refused.text
        assert refused.json()["detail"] == "This read-through has already finished."

    assert (await app_client.post(f"/read-throughs/{uuid.uuid4()}/stop")).status_code == 404


async def test_patch_note_status(app_client, db_factory):
    book_id = await _book(db_factory)
    run_id, (chapter_id,) = await _seed_run(
        db_factory, book_id, status="succeeded", chapters=[("One", CHAPTER_ONE, "done")]
    )
    async with db_factory() as s:
        note = ReadThroughNote(
            read_through_id=run_id,
            chapter_id=chapter_id,
            position=1,
            category="pacing",
            priority="medium",
            title="A slow opening",
            observation="An observation.",
            recommendation="A recommendation.",
        )
        s.add(note)
        await s.commit()
        note_id = note.id

    done = await app_client.patch(f"/read-through-notes/{note_id}", json={"status": "done"})
    assert done.status_code == 200, done.text
    assert (done.json()["id"], done.json()["status"]) == (str(note_id), "done")
    reloaded = (await app_client.get(f"/read-throughs/{run_id}")).json()
    assert [n["status"] for n in reloaded["notes"]] == ["done"]

    dismissed = await app_client.patch(f"/read-through-notes/{note_id}", json={"status": "dismissed"})
    assert dismissed.json()["status"] == "dismissed"

    bad = await app_client.patch(f"/read-through-notes/{note_id}", json={"status": "archived"})
    assert bad.status_code == 422, bad.text
    assert bad.json()["detail"] == "A note's status must be one of: open, done, dismissed."

    missing = await app_client.patch(f"/read-through-notes/{uuid.uuid4()}", json={"status": "done"})
    assert (missing.status_code, missing.json()["detail"]) == (404, "note not found")


async def test_delete_requires_terminal_and_cascades(app_client, db_factory):
    active_book = await _book(db_factory, "Active")
    done_book = await _book(db_factory, "Done")
    active_id, _ = await _seed_run(db_factory, active_book, status="running")

    refused = await app_client.delete(f"/read-throughs/{active_id}")
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"] == "Stop the read-through before deleting it."
    assert await _run_count(db_factory, active_book) == 1

    done_id, (ch1, _ch2) = await _seed_run(
        db_factory, done_book, status="succeeded", chapters=[("One", CHAPTER_ONE, "done"), ("Two", CHAPTER_TWO, "done")]
    )
    async with db_factory() as s:
        for chapter_id in (ch1, None):
            s.add(
                ReadThroughNote(
                    read_through_id=done_id,
                    chapter_id=chapter_id,
                    position=1,
                    category="other",
                    priority="low",
                    title="A note",
                    observation="An observation.",
                    recommendation="A recommendation.",
                )
            )
        await s.commit()

    resp = await app_client.delete(f"/read-throughs/{done_id}")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"deleted": str(done_id)}

    # The route deletes only the run row; the children go by ON DELETE CASCADE in the database.
    async with db_factory() as s:
        chapters_left = (
            await s.execute(
                select(func.count())
                .select_from(ReadThroughChapter)
                .where(ReadThroughChapter.read_through_id == done_id)
            )
        ).scalar_one()
        notes_left = (
            await s.execute(
                select(func.count()).select_from(ReadThroughNote).where(ReadThroughNote.read_through_id == done_id)
            )
        ).scalar_one()
    assert (chapters_left, notes_left) == (0, 0)
    assert (await app_client.get(f"/read-throughs/{done_id}")).status_code == 404
    assert (await app_client.delete(f"/read-throughs/{done_id}")).status_code == 404


# --- end to end ------------------------------------------------------------------------------------

# Chapter one opens with an astral character, so every UTF-16 offset after it is one past its Python index.
E2E_CHAPTERS = [
    (
        "The Causeway",
        "\U0001f30a The tide rose over the causeway before dawn.\n\n"
        "Orin tied the boat to the iron ring and waited for the bell.\n",
    ),
    (
        "The Ledger",
        "Mira opened the ledger by lamplight.\n\nEvery page listed a ship that had never come home to the harbour.\n",
    ),
    (
        "The Bell",
        "The bell rang once at noon.\n\nOrin untied the boat from the iron ring and rowed toward the empty harbour.\n",
    ),
]
E2E_QUOTES = {
    1: "Orin tied the boat to the iron ring and waited for the bell.",
    2: "Every page listed a ship that had never come home to the harbour.",
    3: "Orin untied the boat from the iron ring and rowed toward the empty harbour.",
}
E2E_BOOK_QUOTE = "The bell rang once at noon."  # only in chapter three of the note's scope (1 and 3)
E2E_USAGE = Usage(input_tokens=1000, output_tokens=200)


def _note_json(title: str, **fields: Any) -> dict[str, Any]:
    return {
        "category": "pacing",
        "priority": "high",
        "title": title,
        "observation": "The scene holds on one image longer than the reader needs.",
        "recommendation": "Consider moving to the next beat sooner.",
        **fields,
    }


def _fake_model(monkeypatch) -> list[dict[str, Any]]:
    """Stand in for the provider at `llm.complete`: valid JSON quoting the synthetic text verbatim, a real
    Usage, and a telemetry record exactly as `llm.py` would leave one."""
    calls: list[dict[str, Any]] = []

    async def fake_complete(**kwargs: Any) -> tuple[str, Usage]:
        calls.append(kwargs)
        user = kwargs["user"]
        chapter = re.search(r"TASK: Give your developmental notes on chapter (\d+) above", user)
        if chapter is not None:
            position = int(chapter.group(1))
            raw = json.dumps(
                {
                    "notes": [_note_json(f"Chapter {position} lingers", quotes=[E2E_QUOTES[position]])],
                    "capped": False,
                    "digest": {
                        "summary": f"What happens in chapter {position}.",
                        "characters": [{"name": "Orin", "state": "waiting at the harbour"}],
                        "threads_opened": [],
                        "threads_resolved": [],
                        "setups": [],
                        "timeline": [],
                    },
                }
            )
        elif "TASK: Give your cross-chapter notes" in user:
            raw = json.dumps(
                {
                    "notes": [
                        _note_json(
                            "The boat and the ring",
                            anchor_role="location",
                            positions=[1, 3],
                            quotes=[E2E_BOOK_QUOTE],
                        )
                    ],
                    "capped": False,
                }
            )
        else:
            raise AssertionError("unexpected prompt: neither a chapter call nor the book pass")
        telemetry.record(
            model=kwargs["model"],
            input_tokens=E2E_USAGE.input_tokens,
            output_tokens=E2E_USAGE.output_tokens,
            cache_creation_tokens=0,
            cache_read_tokens=0,
            truncated=False,
            latency_ms=5,
        )
        return raw, E2E_USAGE

    monkeypatch.setattr(llm, "complete", fake_complete)
    return calls


def _utf16_slice(text: str, start: int, end: int) -> str:
    """What JavaScript's `text.slice(start, end)` returns."""
    return text.encode("utf-16-le")[start * 2 : end * 2].decode("utf-16-le")


async def test_post_runs_to_completion(app_client, db_factory, monkeypatch):
    calls = _fake_model(monkeypatch)
    book_id = await _book(db_factory)

    resp = await app_client.post(f"/books/{book_id}/read-throughs", json=_body(E2E_CHAPTERS))
    assert resp.status_code == 200, resp.text
    run_id = resp.json()["id"]
    # app_client runs the background worker before the POST returns: three chapter calls, one book pass.
    assert len(calls) == 4

    status = (await app_client.get(f"/read-throughs/{run_id}/status")).json()
    assert status["status"] == "succeeded", status
    assert (status["chapters_done"], status["chapters_failed"], status["chapters_skipped"]) == (3, 0, 0)
    assert status["attempts_used"] == 4
    assert status["tokens_charged"] == 4 * E2E_USAGE.budget_cost

    body = (await app_client.get(f"/read-throughs/{run_id}")).json()
    assert (body["status"], body["error"], body["accounting_gap"]) == ("succeeded", None, False)
    chapters = body["chapters"]
    assert [c["text"] for c in chapters] == [text for _, text in E2E_CHAPTERS]
    assert all(c["status"] == "done" and c["digest"]["summary"] for c in chapters)

    # Chapter notes: one per chapter, each anchored where its quote really is.
    chapter_notes = [n for n in body["notes"] if n["chapter_id"] is not None]
    assert [n["chapter_id"] for n in chapter_notes] == [c["id"] for c in chapters]
    for note, chapter, (_position, quote) in zip(chapter_notes, chapters, E2E_QUOTES.items(), strict=True):
        (anchor,) = note["anchors"]
        assert (anchor["state"], anchor["chapter_id"]) == ("located", chapter["id"])
        (segment,) = anchor["segments"]
        assert segment["text"] == quote
        assert _utf16_slice(chapter["text"], segment["start"], segment["end"]) == segment["text"]
    first_segment = chapter_notes[0]["anchors"][0]["segments"][0]
    assert first_segment["start"] == chapters[0]["text"].index(E2E_QUOTES[1]) + 1  # the surrogate pair

    # The book pass: full text of all three chapters, its note first, scoped and located in chapter three.
    assert body["notes"][0]["chapter_id"] is None
    assert body["book_pass_status"] == "done"
    assert body["book_input_mode"] == "full_text"
    assert body["book_chapter_ids"] == [c["id"] for c in chapters]
    (book_note,) = [n for n in body["notes"] if n["chapter_id"] is None]
    assert book_note["anchor_role"] == "location"
    assert book_note["scope_chapter_ids"] == [chapters[0]["id"], chapters[2]["id"]]
    (book_anchor,) = book_note["anchors"]
    assert (book_anchor["state"], book_anchor["chapter_id"]) == ("located", chapters[2]["id"])
    (book_segment,) = book_anchor["segments"]
    assert _utf16_slice(chapters[2]["text"], book_segment["start"], book_segment["end"]) == E2E_BOOK_QUOTE

    # One llm_calls row per call, all under the read-through's id, each saying which call it was.
    async with db_factory() as s:
        rows = list((await s.execute(select(LlmCall).where(LlmCall.run_id == uuid.UUID(run_id)))).scalars())
    assert len(rows) == len(calls)
    assert all(row.book_id == book_id for row in rows)
    metas = [row.metadata_ or {} for row in rows]
    assert all(m["read_through_id"] == run_id and m["attempt"] == 1 and m["attempt_role"] == "primary" for m in metas)
    chapter_rows = sorted(
        (m for row, m in zip(rows, metas, strict=True) if row.stage == "read_through_chapter"),
        key=lambda m: m["chapter_position"],
    )
    assert [(m["phase"], m["chapter_position"], m["snapshot_chapter_id"]) for m in chapter_rows] == [
        ("chapter", position, chapter["id"]) for position, chapter in enumerate(chapters, start=1)
    ]
    (book_row,) = [m for row, m in zip(rows, metas, strict=True) if row.stage == "read_through_book"]
    assert (book_row["phase"], book_row["snapshot_chapter_id"]) == ("book", None)


async def test_post_releases_db_connection_before_background_run(app_client, db_factory, monkeypatch):
    """FastAPI closes the request's session only after the response, and Starlette runs BackgroundTasks
    inside the response, so the session outlives the whole run. A transaction the POST left open would sit
    idle in transaction on a pooled connection across every model call."""
    seen: list[list[str]] = []

    async def probe_run_read_through(read_through_id: uuid.UUID, *, session_factory=None) -> None:
        async with db_factory() as s:
            idle = (
                await s.execute(
                    text(
                        "SELECT query FROM pg_stat_activity WHERE datname = current_database() "
                        "AND state = 'idle in transaction' AND pid <> pg_backend_pid()"
                    )
                )
            ).scalars()
            seen.append(list(idle))

    monkeypatch.setattr(run_mod, "run_read_through", probe_run_read_through)
    book_id = await _book(db_factory)

    resp = await app_client.post(f"/books/{book_id}/read-throughs", json=_body([("One", CHAPTER_ONE)]))

    assert resp.status_code == 200, resp.text
    assert seen == [[]], f"connections idle in transaction when the background run started: {seen}"
