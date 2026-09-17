"""The read-through worker (ADR 0035) against a real test database, with only the model faked.

Assertions are on ROWS — read_throughs, read_through_chapters, read_through_notes, llm_calls — never on
in-process state, because every failure mode here (a paid response discarded, a stop that still pays, an
expired owner that still writes, telemetry that never committed) is invisible from inside the process.

The fake stands in for `llm.complete` and records to the ambient telemetry sink exactly as llm.py does: a
metered success records its Usage (llm.py:973), a provider failure records a zero-token row (:840), and
`PromptBudgetExceeded` raises before any record (:632-639). All prose is synthetic.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select, update

from dominion.shared.config import settings
from dominion.shared.models import Book, LlmCall, ReadThrough, ReadThroughChapter, ReadThroughNote
from dominion.workers import llm, telemetry, telemetry_db
from dominion.workers.budget import Usage
from dominion.workers.read_through import prompts
from dominion.workers.read_through import run as rt_run

PRIMARY = "rt-test-primary"
FALLBACK = "rt-test-fallback"

CH1 = (
    "The lighthouse keeper counted the gulls every morning before the kettle boiled. There were always "
    "eleven, except on the day the supply boat failed to come.\n\n"
    "Mara walked the causeway at low tide, her boots sinking into grey sand that smelled of iron and rope. "
    "She did not look back at the tower, though she could feel its lamp turning behind her."
)
CH2 = (
    "The harbour office kept its ledgers in a tin trunk under the stairs. Nobody had opened it since the "
    "storm, and the clerk swore the lock had rusted shut.\n\n"
    "When Mara finally lifted the lid, the pages had fused into a single brown brick of paper. She carried "
    "it outside and set it on the sea wall to dry, weighting it with a stone."
)
CH3 = (
    "By the third week the supply boat still had not come, and the keeper began rationing lamp oil. He "
    "trimmed the wick by a finger's width each night and wrote the date on the wall.\n\n"
    "Mara found the missing boat's name scratched into the underside of the causeway rail, fresh enough "
    "that the splinters were still pale."
)
# Enough extra synthetic text that a chapter's full text is clearly larger than its digest — the digest-mode
# prompt carries a longer notice and task, so short chapters would never fall back to digests.
LONG = "\n\n" + "\n\n".join(
    f"On day {day} the keeper wrote the tide table in pencil, and the ink bottle stayed shut on the sill."
    for day in range(1, 41)
)
Q1 = "counted the gulls every morning before the kettle boiled"
Q2 = "the pages had fused into a single brown brick of paper"
Q3 = "began rationing lamp oil"

DIGEST: dict[str, Any] = {
    "summary": "A keeper counts gulls and a supply boat fails to arrive.",
    "characters": [{"name": "Mara", "state": "uneasy and searching"}],
    "threads_opened": ["the missing supply boat"],
    "threads_resolved": [],
    "setups": ["eleven gulls every morning"],
    "timeline": ["morning of the missing boat"],
}


def note(quote: str, *, title: str = "The opening stalls", category: str = "pacing") -> dict[str, Any]:
    return {
        "category": category,
        "priority": "high",
        "title": title,
        "observation": "The reader waits a long time before anything changes.",
        "recommendation": "Bring the missing boat forward so the routine is broken sooner.",
        "anchor_role": "evidence",
        "quotes": [quote],
    }


def chapter_reply(*notes: dict[str, Any], capped: bool = False) -> str:
    return json.dumps({"notes": list(notes), "capped": capped, "digest": DIGEST})


def book_reply(*positions_lists: list[int], quotes: tuple[str, ...] = ()) -> str:
    notes = [
        {
            "category": "continuity",
            "priority": "medium",
            "title": f"Track the boat across chapters {positions}",
            "observation": "The missing boat is raised and then set aside.",
            "recommendation": "Decide when the reader learns what happened to the boat.",
            "anchor_role": "location",
            "positions": positions,
            "quotes": list(quotes),
        }
        for positions in positions_lists
    ]
    return json.dumps({"notes": notes, "capped": False})


USAGE = Usage(input_tokens=1000, output_tokens=200)
TRUNCATED = Usage(input_tokens=1000, output_tokens=16000, truncated=True)

Step = str | tuple[str, Usage] | BaseException | Callable[[dict[str, Any]], Awaitable[Any]]


class FakeModel:
    """Scripted `llm.complete`. Each call consumes the next step: a reply string, a (reply, Usage) pair, an
    exception to raise, or an async callable receiving the call kwargs and returning one of those."""

    def __init__(self, steps: list[Step], *, recorded_model: str | None = None) -> None:
        self.steps = list(steps)
        self.calls: list[dict[str, Any]] = []
        # What llm.complete RECORDS as the model when it remaps the requested one (gateway, override).
        self.recorded_model = recorded_model

    async def __call__(self, **kwargs: Any) -> tuple[str, Usage]:
        self.calls.append(kwargs)
        if not self.steps:
            raise AssertionError("unexpected extra model call")
        step: Any = self.steps.pop(0)
        while callable(step) and not isinstance(step, BaseException):
            step = await step(kwargs)
        if isinstance(step, llm.PromptBudgetExceeded):
            raise step  # raised locally before any provider traffic: llm.py records nothing
        if isinstance(step, BaseException):
            telemetry.record(
                model=self.recorded_model or kwargs["model"],
                input_tokens=0,
                output_tokens=0,
                cache_creation_tokens=0,
                cache_read_tokens=0,
                truncated=False,
                latency_ms=3,
                error=f"{type(step).__name__}: {step}",
            )
            raise step
        raw, usage = step if isinstance(step, tuple) else (step, USAGE)
        telemetry.record(
            model=self.recorded_model or kwargs["model"],
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_creation_tokens=usage.cache_creation_tokens,
            cache_read_tokens=usage.cache_read_tokens,
            truncated=usage.truncated,
            latency_ms=5,
            metadata={"max_tokens": kwargs["max_tokens"]},
        )
        return raw, usage


@pytest.fixture(autouse=True)
def _pinned_models(monkeypatch: pytest.MonkeyPatch) -> None:
    # The escalation helper reads the FALLBACK from live settings (llm_escalation.py:31-36); pin it so a
    # developer .env can never change which attempts these tests see.
    monkeypatch.setattr(settings, "read_through_fallback_model", FALLBACK)


def install(monkeypatch: pytest.MonkeyPatch, steps: list[Step], *, recorded_model: str | None = None) -> FakeModel:
    fake = FakeModel(steps, recorded_model=recorded_model)
    monkeypatch.setattr(llm, "complete", fake)
    return fake


def snapshot_with(**overrides: Any) -> dict[str, Any]:
    snapshot = prompts.snapshot_settings()
    snapshot["model"] = PRIMARY
    snapshot["fallback_model"] = FALLBACK
    snapshot.update(overrides)
    return snapshot


async def seed_run(
    db_factory: Any,
    chapters: list[tuple[str, str]],
    *,
    allowance: int | None = None,
    **overrides: Any,
) -> uuid.UUID:
    snapshot = snapshot_with(**overrides)
    async with db_factory() as s:
        book = Book(title="Synthetic Book")
        s.add(book)
        await s.flush()
        rt = ReadThrough(
            book_id=book.id,
            title="Synthetic read-through",
            client_request_id=str(uuid.uuid4()),
            payload_sha256="0" * 64,
            status="queued",
            deadline_at=datetime.now(UTC) + timedelta(hours=3),
            settings_snapshot=snapshot,
            voice_guide_snapshot=None,
            attempt_allowance=(
                allowance if allowance is not None else rt_run.attempt_allowance_for(len(chapters), snapshot)
            ),
        )
        s.add(rt)
        await s.flush()
        for position, (label, text) in enumerate(chapters, start=1):
            s.add(
                ReadThroughChapter(
                    read_through_id=rt.id, position=position, label=label, text=text, word_count=len(text.split())
                )
            )
        await s.commit()
        return rt.id


async def request_stop(db_factory: Any, rid: uuid.UUID) -> None:
    """What POST /read-throughs/{id}/stop does to a running run."""
    async with db_factory() as s:
        await s.execute(
            update(ReadThrough)
            .where(ReadThrough.id == rid)
            .values(status="stopping", stop_requested_at=func.now())
            .execution_options(synchronize_session=False)
        )
        await s.commit()


@dataclass
class Rows:
    run: ReadThrough
    chapters: list[ReadThroughChapter]
    chapter_notes: list[ReadThroughNote]
    book_notes: list[ReadThroughNote]
    calls: list[LlmCall]


async def load(db_factory: Any, rid: uuid.UUID) -> Rows:
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
        notes = list(
            (
                await s.execute(
                    select(ReadThroughNote)
                    .where(ReadThroughNote.read_through_id == rid)
                    .order_by(ReadThroughNote.position)
                )
            ).scalars()
        )
        calls = list((await s.execute(select(LlmCall).where(LlmCall.run_id == rid))).scalars())
    calls.sort(
        key=lambda c: (c.stage, (c.metadata_ or {}).get("chapter_position") or 0, (c.metadata_ or {})["attempt"])
    )
    return Rows(
        run=run,
        chapters=chapters,
        chapter_notes=[n for n in notes if n.chapter_id is not None],
        book_notes=[n for n in notes if n.chapter_id is None],
        calls=calls,
    )


async def run(db_factory: Any, rid: uuid.UUID) -> Rows:
    await rt_run.run_read_through(rid, session_factory=db_factory)
    return await load(db_factory, rid)


# ------------------------------------------------------------------------------------------------ #


async def test_saves_notes_and_digest_per_chapter(db_factory, monkeypatch):
    rid = await seed_run(db_factory, [("Chapter One", CH1), ("Chapter Two", CH2)])
    fake = install(
        monkeypatch,
        [chapter_reply(note(Q1)), chapter_reply(note(Q2, title="The ledger")), book_reply([1, 2])],
    )

    rows = await run(db_factory, rid)

    assert rows.run.status == "succeeded", rows.run.error
    assert rows.run.error is None
    assert rows.run.finished_at is not None and rows.run.lease_expires_at is None
    assert len(fake.calls) == 3
    assert rows.run.attempts_used == 3
    assert rows.run.tokens_charged == 3 * USAGE.budget_cost
    for chapter, quote in zip(rows.chapters, (Q1, Q2), strict=True):
        assert chapter.status == "done"
        assert chapter.digest == DIGEST
        assert chapter.model_used == PRIMARY
        assert chapter.attempts == 1
        assert chapter.notes_dropped == 0 and chapter.notes_capped is False
        saved = [n for n in rows.chapter_notes if n.chapter_id == chapter.id]
        assert len(saved) == 1
        anchor = saved[0].anchors[0]
        assert anchor["state"] == "located"
        assert anchor["chapter_id"] == str(chapter.id)
        segment = anchor["segments"][0]
        assert chapter.text[segment["start"] : segment["end"]] == segment["text"] == quote
        assert saved[0].status == "open"

    assert rows.run.book_pass_status == "done"
    assert rows.run.book_input_mode == "full_text"
    assert rows.run.book_chapter_ids == [str(c.id) for c in rows.chapters]
    assert rows.run.book_model_used == PRIMARY
    assert len(rows.book_notes) == 1
    assert rows.book_notes[0].scope_chapter_ids == [str(c.id) for c in rows.chapters]
    # The book pass read the manuscript: both chapters' text is in the prompt, with their note titles.
    assert CH1 in fake.calls[2]["user"] and CH2 in fake.calls[2]["user"]
    assert "The ledger" in fake.calls[2]["user"]


async def test_truncated_but_parseable_fallback_rejected(db_factory, monkeypatch):
    rid = await seed_run(db_factory, [("Chapter One", CH1)])
    fake = install(monkeypatch, [(chapter_reply(note(Q1)), TRUNCATED), (chapter_reply(note(Q1)), TRUNCATED)])

    rows = await run(db_factory, rid)

    assert [c["model"] for c in fake.calls] == [PRIMARY, FALLBACK]
    assert fake.calls[1]["max_tokens"] == 24_000  # the escalation floor for this role
    chapter = rows.chapters[0]
    # The fallback's JSON parsed and validated — but it was cut off, so it is not trusted as complete.
    assert chapter.status == "failed"
    assert chapter.error == rt_run.TRUNCATED_MESSAGE
    assert chapter.digest is None and rows.chapter_notes == []
    assert rows.run.status == "failed"


async def test_final_attempt_revalidated_before_done(db_factory, monkeypatch):
    """`attempt_with_escalation` returns the fallback's value even when it failed (llm_escalation.py:108)."""
    rid = await seed_run(db_factory, [("Chapter One", CH1)])
    invalid_digest = json.dumps({"notes": [note(Q1)], "capped": False, "digest": {"summary": ""}})
    fake = install(monkeypatch, ["I could not read this chapter.", invalid_digest])

    rows = await run(db_factory, rid)

    assert len(fake.calls) == 2
    chapter = rows.chapters[0]
    assert chapter.status == "failed"
    assert chapter.error == rt_run.INVALID_MESSAGE
    assert chapter.model_used is None and chapter.digest is None
    assert rows.chapter_notes == []


async def test_note_without_located_anchor_dropped_and_counted(db_factory, monkeypatch):
    rid = await seed_run(db_factory, [("Chapter One", CH1)])
    unplaceable = note("the dragon unfolded its wings over the silent orchard", title="Nowhere")
    invalid = note(Q1, category="vibes")
    install(monkeypatch, [chapter_reply(note(Q1, title="Kept"), unplaceable, invalid, capped=True)])

    rows = await run(db_factory, rid)

    chapter = rows.chapters[0]
    assert chapter.status == "done"
    assert [n.title for n in rows.chapter_notes] == ["Kept"]
    assert chapter.notes_dropped == 2  # one invalid item + one note whose only quote is not in the text
    assert chapter.notes_capped is True


async def test_stop_prevents_next_paid_call(db_factory, monkeypatch):
    rid = await seed_run(db_factory, [("Chapter One", CH1), ("Chapter Two", CH2)])

    async def stop_then_answer(_kwargs: dict[str, Any]) -> str:
        await request_stop(db_factory, rid)
        return chapter_reply(note(Q1))

    fake = install(monkeypatch, [stop_then_answer])

    rows = await run(db_factory, rid)

    assert len(fake.calls) == 1  # no paid call after the stop
    assert rows.run.status == "stopped"
    assert rows.run.attempts_used == 1
    assert rows.chapters[0].status == "done"  # the reply already paid for is kept
    assert rows.chapters[1].status == "skipped"
    assert rows.run.book_pass_status == "not_run"
    assert rows.run.lease_expires_at is None


async def test_stop_cancels_inflight_call(db_factory, monkeypatch):
    rid = await seed_run(
        db_factory,
        [("Chapter One", CH1), ("Chapter Two", CH2)],
        read_through_lease_ttl_s=1.5,  # heartbeat every 0.5s
        read_through_call_deadline_s=20,
    )
    cancelled = asyncio.Event()

    async def hang_until_cancelled(_kwargs: dict[str, Any]) -> str:
        await request_stop(db_factory, rid)
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise
        raise AssertionError("unreachable")

    fake = install(monkeypatch, [hang_until_cancelled])

    started = asyncio.get_running_loop().time()
    rows = await run(db_factory, rid)

    assert cancelled.is_set()
    assert asyncio.get_running_loop().time() - started < 10  # cancelled by the heartbeat, not the deadline
    assert len(fake.calls) == 1
    assert rows.run.status == "stopped"
    assert rows.chapters[0].status == "failed"
    assert rows.chapters[0].error == rt_run.STOPPED_CHAPTER_MESSAGE
    assert rows.chapters[1].status == "skipped"
    assert rows.run.book_pass_status == "not_run"
    # The cancelled call left no record inside llm.complete; the worker records one so it stays attributable.
    assert len(rows.calls) == 1
    assert rows.calls[0].error == "call cancelled"


@pytest.mark.parametrize("case", ["malformed", "rate_limited", "truncated", "prompt_budget"])
async def test_failed_attempt_telemetry_persisted(db_factory, monkeypatch, case):
    rid = await seed_run(db_factory, [("Chapter One", CH1)])
    steps: dict[str, list[Step]] = {
        "malformed": ["no json here", "{not json either"],
        "rate_limited": [llm.LlmRateLimited("provider rate limit (429) persisted after 3 automatic retries")],
        "truncated": [(chapter_reply(note(Q1))[:40], TRUNCATED), (chapter_reply(note(Q1))[:40], TRUNCATED)],
        "prompt_budget": [llm.PromptBudgetExceeded("prompt_budget_exceeded: estimated_input_tokens=90000")],
    }
    install(monkeypatch, steps[case])

    rows = await run(db_factory, rid)

    chapter = rows.chapters[0]
    assert chapter.status == "failed"
    assert rows.run.status == "failed"
    if case == "malformed":
        assert chapter.error == rt_run.UNPARSEABLE_MESSAGE
        assert len(rows.calls) == 2
        assert rows.run.tokens_charged == 2 * USAGE.budget_cost
    elif case == "rate_limited":
        assert chapter.error == rt_run.RATE_LIMITED_MESSAGE
        assert len(rows.calls) == 1
        assert rows.calls[0].error is not None and rows.calls[0].error.startswith("LlmRateLimited")
    elif case == "truncated":
        assert chapter.error == rt_run.TRUNCATED_MESSAGE
        assert len(rows.calls) == 2
        assert all(c.truncated for c in rows.calls)
    else:
        # Refused locally before any provider traffic: nothing was sent, so there is no call to record.
        assert chapter.error is not None and chapter.error.startswith("prompt_budget_exceeded")
        assert rows.calls == []
        assert rows.run.tokens_charged == 0
    assert all(c.stage == "read_through_chapter" and c.run_id == rid for c in rows.calls)
    assert rows.run.attempts_used == chapter.attempts == max(len(rows.calls), 1)
    assert rows.run.accounting_gap is False


async def test_telemetry_failure_keeps_notes_and_sets_accounting_gap(db_factory, monkeypatch):
    rid = await seed_run(db_factory, [("Chapter One", CH1)])
    install(monkeypatch, [chapter_reply(note(Q1))])

    def broken_persist(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("llm_calls is unavailable")

    monkeypatch.setattr(telemetry_db, "persist_sink", broken_persist)

    rows = await run(db_factory, rid)

    assert rows.chapters[0].status == "done"
    assert len(rows.chapter_notes) == 1
    assert rows.run.accounting_gap is True
    assert rows.calls == []
    assert rows.run.status == "succeeded"
    assert rows.run.book_pass_status == "skipped"


async def test_expired_owner_write_rejected(db_factory, monkeypatch):
    rid = await seed_run(db_factory, [("Chapter One", CH1), ("Chapter Two", CH2)])

    async def expire_then_answer(_kwargs: dict[str, Any]) -> str:
        async with db_factory() as s:
            await s.execute(
                update(ReadThrough)
                .where(ReadThrough.id == rid)
                .values(lease_expires_at=func.now() - timedelta(seconds=5))
                .execution_options(synchronize_session=False)
            )
            await s.commit()
        return chapter_reply(note(Q1))

    fake = install(monkeypatch, [expire_then_answer])

    rows = await run(db_factory, rid)

    assert len(fake.calls) == 1  # never went on to chapter two
    assert rows.chapters[0].status == "running"  # the result write was refused, not merely skipped
    assert rows.chapters[0].digest is None
    assert rows.chapter_notes == []
    assert [c.status for c in rows.chapters[1:]] == ["pending"]
    assert rows.run.status == "running" and rows.run.finished_at is None
    assert rows.run.tokens_charged == 0
    assert rows.run.attempts_used == 1
    # Telemetry is exhaust, not a result: the paid call is still attributable.
    assert len(rows.calls) == 1


async def test_attempt_allowance_stops_run(db_factory, monkeypatch):
    # Allowance exhausted between chapters: chapter two is never attempted.
    rid = await seed_run(db_factory, [("Chapter One", CH1), ("Chapter Two", CH2)], allowance=1)
    fake = install(monkeypatch, [chapter_reply(note(Q1))])

    rows = await run(db_factory, rid)

    assert len(fake.calls) == 1
    assert [c.status for c in rows.chapters] == ["done", "skipped"]
    assert rows.run.status == "partial"
    assert rows.run.error is not None and "used all 1 of its model attempts" in rows.run.error
    assert rows.run.attempts_used == 1

    # Allowance exhausted inside a chapter: the FALLBACK attempt is refused before any call.
    rid = await seed_run(db_factory, [("Chapter One", CH1)], allowance=1)
    fake = install(monkeypatch, ["not json"])

    rows = await run(db_factory, rid)

    assert len(fake.calls) == 1
    assert rows.chapters[0].status == "failed"
    assert rows.chapters[0].error is not None and "used all 1" in rows.chapters[0].error
    assert rows.run.attempts_used == 1
    assert rows.run.status == "failed"


async def test_call_deadline_fails_chapter(db_factory, monkeypatch):
    rid = await seed_run(db_factory, [("Chapter One", CH1), ("Chapter Two", CH2)], read_through_call_deadline_s=0.3)

    async def slow(_kwargs: dict[str, Any]) -> str:
        await asyncio.sleep(10)
        return chapter_reply(note(Q1))

    fake = install(monkeypatch, [slow, chapter_reply(note(Q2))])

    rows = await run(db_factory, rid)

    assert len(fake.calls) == 2  # a timed-out chapter is not retried on the fallback; the run moves on
    assert rows.chapters[0].status == "failed"
    assert rows.chapters[0].error == "The model didn't finish within 0.3 seconds."
    assert rows.chapters[1].status == "done"
    assert rows.run.status == "partial"
    deadline_rows = [c for c in rows.calls if c.error == "call deadline exceeded"]
    assert len(deadline_rows) == 1


async def test_book_pass_skipped_under_two_chapters(db_factory, monkeypatch):
    rid = await seed_run(db_factory, [("Chapter One", CH1)])
    fake = install(monkeypatch, [chapter_reply(note(Q1))])

    rows = await run(db_factory, rid)

    assert len(fake.calls) == 1
    assert rows.run.book_pass_status == "skipped"
    assert rows.run.book_input_mode is None and rows.run.book_chapter_ids == []
    assert rows.run.status == "succeeded"
    assert rows.run.attempt_allowance == 2  # no book attempts reserved for a single chapter


async def test_book_pass_digest_mode_records_input_mode_and_chapter_ids(db_factory, monkeypatch):
    chapters = [("Chapter One", CH1 + LONG), ("Chapter Two", CH2 + LONG), ("Chapter Three", CH3 + LONG)]
    # Chapter two will fail, so the book pass reads chapters 1 and 3. Budget the book input at exactly what
    # their DIGEST prompt needs: the full-text prompt is larger, so the worker must fall back to digests.
    probe = snapshot_with()
    digest_inputs = [
        prompts.BookChapterInput(position=1, label="Chapter One", text=None, digest=DIGEST, note_titles=("One",)),
        prompts.BookChapterInput(position=3, label="Chapter Three", text=None, digest=DIGEST, note_titles=("Three",)),
    ]
    digest_estimate = prompts.estimated_input_tokens(prompts.build_book_prompt(probe, None, digest_inputs, "digests"))
    rid = await seed_run(db_factory, chapters, read_through_book_input_budget=digest_estimate)
    fake = install(
        monkeypatch,
        [
            chapter_reply(note(Q1, title="One")),
            "garbage",
            "more garbage",
            chapter_reply(note(Q3, title="Three")),
            book_reply([1, 2, 3], [2]),
        ],
    )

    rows = await run(db_factory, rid)

    ch1, ch2, ch3 = rows.chapters
    assert (ch1.status, ch2.status, ch3.status) == ("done", "failed", "done")
    assert rows.run.book_pass_status == "done"
    assert rows.run.book_input_mode == "digests"
    assert rows.run.book_chapter_ids == [str(ch1.id), str(ch3.id)]
    book_prompt = fake.calls[-1]["user"]
    assert "SUMMARY, not the manuscript" in book_prompt
    assert CH1 not in book_prompt and LONG not in book_prompt
    full_inputs = [
        prompts.BookChapterInput(position=p, label=label, text=text, digest=None, note_titles=(title,))
        for p, label, text, title in ((1, "Chapter One", CH1 + LONG, "One"), (3, "Chapter Three", CH3 + LONG, "Three"))
    ]
    full_estimate = prompts.estimated_input_tokens(prompts.build_book_prompt(probe, None, full_inputs, "full_text"))
    assert full_estimate > digest_estimate  # the precondition this test depends on
    # Position 2 was not read by the pass: dropped from the first note's scope; the second note had nothing else.
    assert len(rows.book_notes) == 1
    assert rows.book_notes[0].scope_chapter_ids == [str(ch1.id), str(ch3.id)]
    assert rows.run.status == "partial"


async def test_book_pass_not_run_after_stop(db_factory, monkeypatch):
    rid = await seed_run(db_factory, [("Chapter One", CH1), ("Chapter Two", CH2)])

    async def stop_then_answer(_kwargs: dict[str, Any]) -> str:
        await request_stop(db_factory, rid)
        return chapter_reply(note(Q2))

    fake = install(monkeypatch, [chapter_reply(note(Q1)), stop_then_answer])

    rows = await run(db_factory, rid)

    assert len(fake.calls) == 2  # both chapters, no book call
    assert [c.status for c in rows.chapters] == ["done", "done"]
    assert rows.run.book_pass_status == "not_run"
    assert rows.run.book_input_mode is None
    assert rows.run.status == "stopped"


async def test_one_llm_call_row_per_attempt_run_id_is_read_through_id(db_factory, monkeypatch):
    rid = await seed_run(db_factory, [("Chapter One", CH1), ("Chapter Two", CH2)])
    install(
        monkeypatch,
        ["not json", chapter_reply(note(Q1)), chapter_reply(note(Q2)), book_reply([1, 2], quotes=(Q2,))],
    )

    rows = await run(db_factory, rid)

    assert rows.run.status == "succeeded", rows.run.error
    assert rows.run.attempts_used == 4
    assert len(rows.calls) == 4
    ch1, ch2 = rows.chapters
    assert all(c.run_id == rid and c.book_id == rows.run.book_id and c.chapter_id is None for c in rows.calls)
    book_call, *chapter_calls = rows.calls  # sorted by stage: read_through_book < read_through_chapter
    got = [
        (
            c.stage,
            c.model,
            c.metadata_["snapshot_chapter_id"],
            c.metadata_["chapter_position"],
            c.metadata_["phase"],
            c.metadata_["attempt"],
            c.metadata_["attempt_role"],
        )
        for c in chapter_calls
    ]
    assert got == [
        ("read_through_chapter", PRIMARY, str(ch1.id), 1, "chapter", 1, "primary"),
        ("read_through_chapter", FALLBACK, str(ch1.id), 1, "chapter", 2, "fallback"),
        ("read_through_chapter", PRIMARY, str(ch2.id), 2, "chapter", 1, "primary"),
    ]
    assert all(c.metadata_["read_through_id"] == str(rid) for c in rows.calls)
    assert chapter_calls[1].metadata_.get("fallback_attempt") is True
    assert book_call.stage == "read_through_book"
    assert book_call.metadata_["phase"] == "book"
    assert book_call.metadata_["snapshot_chapter_id"] is None
    assert book_call.metadata_["attempt"] == 1
    assert rows.chapters[0].model_used == FALLBACK and rows.chapters[0].attempts == 2
    # A book-note quote found in exactly one scoped chapter is anchored to that chapter.
    anchor = rows.book_notes[0].anchors[0]
    assert anchor["state"] == "located" and anchor["chapter_id"] == str(ch2.id)


async def test_rate_limited_primary_fails_chapter_not_run(db_factory, monkeypatch):
    """A primary 429 escapes the escalation helper (llm_escalation.py:61): the chapter fails, the fallback is
    NOT run, and the run carries on to the next chapter."""
    rid = await seed_run(db_factory, [("Chapter One", CH1), ("Chapter Two", CH2)])
    fake = install(monkeypatch, [llm.LlmRateLimited("429"), chapter_reply(note(Q2))])

    rows = await run(db_factory, rid)

    assert [c["model"] for c in fake.calls] == [PRIMARY, PRIMARY]
    assert rows.chapters[0].status == "failed"
    assert rows.chapters[0].error == rt_run.RATE_LIMITED_MESSAGE
    assert rows.chapters[0].attempts == 1
    assert rows.chapters[1].status == "done"
    assert rows.run.status == "partial"
    assert rows.run.error is not None and "Chapter One" in rows.run.error


def utf16_slice(text: str, start: int, end: int) -> str:
    """What the Desk's `String.slice` returns: anchor offsets are UTF-16 code units, not Python indices."""
    return text.encode("utf-16-le")[2 * start : 2 * end].decode("utf-16-le")


async def test_book_quote_in_two_chapters_yields_ambiguous_anchor_per_chapter(db_factory, monkeypatch):
    shared = "the tide came in over the causeway stones"
    chapter_a = CH1 + f"\n\nBy evening {shared}, and the gulls went quiet."
    # An astral character ahead of the matches makes UTF-16 offsets differ from Python indices.
    chapter_b = "\U0001f30a " + CH2 + f"\n\nAt dawn {shared}. By noon, once more, {shared}."
    rid = await seed_run(db_factory, [("Chapter One", chapter_a), ("Chapter Two", chapter_b)])
    install(
        monkeypatch,
        [chapter_reply(note(Q1)), chapter_reply(note(Q2)), book_reply([1, 2], quotes=(shared,))],
    )

    rows = await run(db_factory, rid)

    assert rows.run.book_pass_status == "done", rows.run.book_pass_error
    ch1, ch2 = rows.chapters
    texts = {str(c.id): c.text for c in rows.chapters}
    book_anchors = rows.book_notes[0].anchors
    by_chapter = {a["chapter_id"]: a for a in book_anchors}
    assert len(book_anchors) == 2
    assert set(by_chapter) == {str(ch1.id), str(ch2.id)}  # every anchor names its chapter
    for anchor in book_anchors:
        assert anchor["state"] == "ambiguous"
        assert anchor["segments"] == []
        assert anchor["candidate_count"] == 3  # the TOTAL across both chapters: 1 + 2
        assert anchor["candidates"]
        for placement in anchor["candidates"]:
            assert placement
            for segment in placement:
                assert utf16_slice(texts[anchor["chapter_id"]], segment["start"], segment["end"]) == segment["text"]
                assert segment["text"] == shared
    assert len(by_chapter[str(ch1.id)]["candidates"]) == 1
    assert len(by_chapter[str(ch2.id)]["candidates"]) == 2


async def test_model_used_is_the_recorded_model(db_factory, monkeypatch):
    """With the LiteLLM gateway or a model override, llm.complete sends and records a different model than
    the one requested (llm.py:590-591). The saved results must credit the model that actually ran."""
    rid = await seed_run(db_factory, [("Chapter One", CH1), ("Chapter Two", CH2)])
    fake = install(
        monkeypatch,
        [chapter_reply(note(Q1)), chapter_reply(note(Q2)), book_reply([1, 2])],
        recorded_model="gateway/other-model",
    )

    rows = await run(db_factory, rid)

    assert rows.run.status == "succeeded", rows.run.error
    assert [c["model"] for c in fake.calls] == [PRIMARY, PRIMARY, PRIMARY]
    assert [c.model_used for c in rows.chapters] == ["gateway/other-model", "gateway/other-model"]
    assert rows.run.book_model_used == "gateway/other-model"
    assert len(rows.calls) == 3 and all(c.model == "gateway/other-model" for c in rows.calls)
    # attempt_role compares the REQUESTED model with the primary: a remapped primary is still the primary.
    assert all(c.metadata_["attempt_role"] == "primary" for c in rows.calls)


async def test_shutdown_cancellation_still_records_attempt(db_factory, monkeypatch):
    """Server shutdown cancels the worker task ITSELF mid-call. The cancellation must propagate, but the
    attempt — which the provider may already be billing — must still leave a telemetry row, or an accounting
    gap if even the flush could not run."""
    rid = await seed_run(db_factory, [("Chapter One", CH1)])
    entered = asyncio.Event()

    async def hang(_kwargs: dict[str, Any]) -> str:
        entered.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    install(monkeypatch, [hang])
    worker = asyncio.create_task(rt_run.run_read_through(rid, session_factory=db_factory))
    await asyncio.wait_for(entered.wait(), timeout=10)
    worker.cancel()
    with pytest.raises(asyncio.CancelledError):
        await worker

    rows = await load(db_factory, rid)
    shutdown_rows = [c for c in rows.calls if c.error == "call cancelled: the server was shutting down"]
    assert len(shutdown_rows) == 1 or rows.run.accounting_gap is True
    assert rows.run.attempts_used == 1
    # Not finalized by the dying worker: its lease is left to expire, and recovery marks the run interrupted.
    assert rows.run.status == "running" and rows.run.finished_at is None


async def test_shutdown_cancellation_propagates_even_when_ownership_is_lost(db_factory, monkeypatch):
    """The narrow race: shutdown cancels the worker after the provider has already recorded a billed call,
    while the lease has also expired. Charging that spend then finds ownership lost — and that must not
    replace the CancelledError, or the task would return normally instead of being cancelled."""
    rid = await seed_run(db_factory, [("Chapter One", CH1)])
    entered = asyncio.Event()

    async def billed_then_hang(_kwargs: dict[str, Any]) -> str:
        telemetry.record(
            model=PRIMARY,
            input_tokens=1000,
            output_tokens=200,
            cache_creation_tokens=0,
            cache_read_tokens=0,
            truncated=False,
            latency_ms=5,
        )
        entered.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    install(monkeypatch, [billed_then_hang])
    worker = asyncio.create_task(rt_run.run_read_through(rid, session_factory=db_factory))
    await asyncio.wait_for(entered.wait(), timeout=10)
    async with db_factory() as session:
        await session.execute(
            update(ReadThrough).where(ReadThrough.id == rid).values(lease_expires_at=func.now() - timedelta(minutes=5))
        )
        await session.commit()
    worker.cancel()
    with pytest.raises(asyncio.CancelledError):
        await worker

    rows = await load(db_factory, rid)
    assert [c.input_tokens for c in rows.calls] == [1000]  # the billed call is still attributable
    assert rows.run.tokens_charged == 0  # the charge was skipped, not written by a non-owner
