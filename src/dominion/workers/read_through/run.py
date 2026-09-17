"""Read-through worker (ADR 0035): claim → lease + heartbeat → chapters in order → book pass → final status.

Ownership. A run is owned by one in-process task, identified by a fresh `owner_token` (a uuid4 per claim —
not a pid, which collides across containers) plus an expiring `lease_expires_at`. Every timestamp comes
from the database's `now()`, so app-server clock skew never decides who owns a run. A heartbeat task renews
the lease every ttl/3 while the owner awaits the provider, and doubles as the stop watcher: it cancels the
in-flight call when the author asks to stop or when the lease can no longer be renewed.

Writes. Every result write is a SHORT transaction whose first statement re-asserts ownership (token, status
running|stopping, unexpired lease) with a conditional UPDATE. No row back means the run is no longer ours —
recovered as interrupted, or reclaimed after expiry — and the transaction writes nothing. No transaction or
row lock is ever held across a model call.

Attempts. Each attempt runs through `attempt_with_escalation`, which hands back the LAST value even when that
value failed (llm_escalation.py:108), so the caller re-validates before marking anything done. Telemetry for
an attempt is flushed in `finally`, in its own transaction, whatever happened — a failed flush flags
`accounting_gap` and never touches notes that were already saved.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import structlog
from sqlalchemy import and_, case, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from dominion.shared.agent_policy import quality_effort
from dominion.shared.config import settings
from dominion.shared.db import SessionFactory
from dominion.shared.enums import (
    ReadThroughBookInputMode,
    ReadThroughBookPassStatus,
    ReadThroughChapterStatus,
    ReadThroughNoteStatus,
    ReadThroughStatus,
)
from dominion.shared.models import ReadThrough, ReadThroughChapter, ReadThroughNote
from dominion.workers import llm, telemetry, telemetry_db
from dominion.workers.budget import TokenBudget, Usage
from dominion.workers.llm_escalation import attempt_with_escalation, policy_for_setting
from dominion.workers.packet.parse import extract_object
from dominion.workers.read_through import anchors, prompts, validate

log = structlog.get_logger()

SETTING_KEY = "read_through_model"
CHAPTER_STAGE = "read_through_chapter"
BOOK_STAGE = "read_through_book"

# Headroom over input budget + output allowance for the per-attempt TokenBudget, so a response the provider
# already billed is never discarded by a hard-budget raise (llm.py:1014) just because the estimate ran low.
_BUDGET_HEADROOM_TOKENS = 20_000
_ERROR_LIMIT = 500
_RUN_ERROR_LIMIT = 2_000

RATE_LIMITED_MESSAGE = "The model provider is rate-limiting requests; try again later."
STOPPED_CHAPTER_MESSAGE = "Stopped while this chapter was being read."
STOPPED_BOOK_MESSAGE = "Stopped while the cross-chapter pass was running."
STOP_REQUESTED_MESSAGE = "Stopped at the author's request."
INTERRUPTED_MESSAGE = "Interrupted: the server stopped working on this read-through. Finished chapters are kept."
INTERRUPTED_CHAPTER_MESSAGE = "Interrupted while this chapter was being read."
TRUNCATED_MESSAGE = "The model's reply was cut off before it finished."
UNPARSEABLE_MESSAGE = "The model's reply was not a JSON object."
INVALID_MESSAGE = "The model's reply did not match the notes format."

_OWNED_STATUSES = (ReadThroughStatus.RUNNING.value, ReadThroughStatus.STOPPING.value)
_DONE = ReadThroughChapterStatus.DONE.value
_FAILED = ReadThroughChapterStatus.FAILED.value
_SKIPPED = ReadThroughChapterStatus.SKIPPED.value
_PENDING = ReadThroughChapterStatus.PENDING.value
_RUNNING = ReadThroughChapterStatus.RUNNING.value


def attempt_allowance_for(chapter_count: int, snapshot: Mapping[str, Any]) -> int:
    """How many model attempts a run may make: every chapter gets its primary + fallback, and the book pass
    gets its own only when there can be one (two or more chapters)."""
    per_chapter = int(snapshot["read_through_attempts_per_chapter"])
    book = int(snapshot["read_through_book_attempts"]) if chapter_count >= 2 else 0
    return chapter_count * per_chapter + book


# ------------------------------------------------------------------------------------------------ #
# Value objects                                                                                     #
# ------------------------------------------------------------------------------------------------ #


@dataclass(frozen=True)
class _Block:
    """A pre-check that refused the next paid attempt. `kind`: stop | deadline | allowance | ceiling."""

    kind: str
    message: str


@dataclass(frozen=True)
class Attempt:
    """One attempt's outcome, as `attempt_with_escalation` hands it back. `value` is the validated result or
    None; `truncated` is the provider's own flag; `blocked` names the pre-check that refused the attempt
    before any call was made; `error` says why `value` is None when a call did return."""

    value: Any
    truncated: bool
    model: str  # the model REQUESTED (decides attempt_role)
    usage: Usage | None = None
    blocked: _Block | None = None
    error: str | None = None
    # The model that actually ran, as llm.complete recorded it — it may remap the requested one (gateway
    # aliases, llm.py:590-591). None when no call was made. This is what model_used columns store.
    model_used: str | None = None


@dataclass
class _Chapter:
    id: uuid.UUID
    position: int
    label: str
    text: str
    status: str
    digest: dict[str, Any] | None
    error: str | None = None
    note_titles: tuple[str, ...] = ()


class _OwnershipLost(Exception):
    """This worker no longer owns the run (lease expired, recovered, or reclaimed): write nothing more."""


class _CallCancelled(Exception):
    """The heartbeat cancelled the in-flight model call — a stop request, or ownership that could not be
    renewed. Raised in place of the CancelledError so a genuine cancellation of the worker task itself
    (server shutdown) is never mistaken for one."""


def _is_success(value: Any) -> bool:
    return isinstance(value, Attempt) and value.value is not None and not value.truncated


def _unfinished_call_error(exc: BaseException) -> str:
    if isinstance(exc, TimeoutError):
        return "call deadline exceeded"
    if isinstance(exc, _CallCancelled):
        return "call cancelled"
    return "call cancelled: the server was shutting down"


def _short(exc: BaseException, limit: int = _ERROR_LIMIT) -> str:
    text = str(exc).strip() or type(exc).__name__
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _join(*pieces: str | None) -> str | None:
    text = " ".join(p for p in pieces if p)
    if not text:
        return None
    return text if len(text) <= _RUN_ERROR_LIMIT else text[: _RUN_ERROR_LIMIT - 1] + "…"


def _snap_float(snapshot: Mapping[str, Any], key: str) -> float:
    """A limit from the admitted snapshot. A key the snapshot lacks falls back to the live setting of the same
    name so an older snapshot still runs, but a present key always wins."""
    value = snapshot.get(key)
    return float(value if value is not None else getattr(settings, key))


def _snap_int(snapshot: Mapping[str, Any], key: str) -> int:
    return int(_snap_float(snapshot, key))


def _sink_cost(sink: telemetry.TelemetrySink) -> int:
    """Weighted work recorded in a sink — the charge when the call raised after the provider billed it (a
    hard-budget raise loses the Usage but llm.py has already recorded it)."""
    return sum(
        Usage(r.input_tokens, r.output_tokens, r.cache_creation_tokens, r.cache_read_tokens).budget_cost
        for r in sink.records
    )


def _placements(anchor: Any) -> int:
    if anchor.state == "located":
        return 1
    if anchor.state == "ambiguous":
        return max(int(anchor.candidate_count), 1)
    return 0


def _book_anchors(quote: str, scope: Sequence[_Chapter]) -> list[dict[str, Any]]:
    """Place one book-note quote within its scoped chapters, as anchors the Desk can highlight.

    - One chapter has placements → that chapter's anchor, unchanged.
    - Several chapters have placements → one AMBIGUOUS anchor PER matching chapter, each carrying that
      chapter's own placements as candidates (none chosen) and the TOTAL placement count across all of them.
      Every anchor names its chapter: the Desk selects anchors by `chapter_id`, so a chapter-less anchor
      would highlight nothing while claiming every placement is shown.
    - No placements anywhere → a single unlocated anchor."""
    hits: list[tuple[Any, int]] = []
    for chapter in scope:
        anchor = anchors.locate(quote, chapter.text, chapter_id=str(chapter.id))
        count = _placements(anchor)
        if count:
            hits.append((anchor, count))
    if len(hits) == 1:
        return [hits[0][0].to_json()]
    if len(hits) > 1:
        total = sum(count for _, count in hits)
        spread: list[dict[str, Any]] = []
        for anchor, _count in hits:
            placements = anchor.candidates if anchor.state == anchors.AMBIGUOUS else (anchor.segments,)
            spread.append(
                {
                    "chapter_id": anchor.chapter_id,
                    "state": anchors.AMBIGUOUS,
                    "text_quoted": anchor.text_quoted,
                    "segments": [],
                    "candidates": [
                        [segment.to_json() for segment in placement]
                        for placement in placements[: anchors.MAX_CANDIDATES]
                    ],
                    "candidate_count": total,
                }
            )
        return spread
    if len(scope) == 1:
        return [anchors.locate(quote, scope[0].text, chapter_id=str(scope[0].id)).to_json()]
    return [
        {
            "chapter_id": None,
            "state": anchors.UNLOCATED,
            "text_quoted": quote,
            "segments": [],
            "candidates": [],
            "candidate_count": 0,
        }
    ]


# ------------------------------------------------------------------------------------------------ #
# The owned run                                                                                     #
# ------------------------------------------------------------------------------------------------ #


class _Run:
    def __init__(
        self,
        *,
        session_factory: Callable[[], AsyncSession],
        run_id: uuid.UUID,
        token: uuid.UUID,
        book_id: uuid.UUID,
        snapshot: Mapping[str, Any],
        voice_guide: str | None,
        chapters: list[_Chapter],
    ) -> None:
        self.session_factory = session_factory
        self.id = run_id
        self.token = token
        self.book_id = book_id
        self.snapshot = dict(snapshot)
        self.voice_guide = voice_guide
        self.chapters = chapters
        self.primary_model = str(self.snapshot.get("model") or settings.read_through_model)
        self.ttl_s = _snap_float(self.snapshot, "read_through_lease_ttl_s")
        self.call_deadline_s = _snap_float(self.snapshot, "read_through_call_deadline_s")
        self.run_deadline_s = _snap_float(self.snapshot, "read_through_run_deadline_s")
        self.token_ceiling = _snap_int(self.snapshot, "read_through_run_token_ceiling")
        # Set by the heartbeat (or a pre-check) and read by the loop; the heartbeat also cancels `call_task`.
        self.stop = False
        self.lost = False
        self.call_task: asyncio.Task[tuple[str, Usage]] | None = None
        self.loop_end: _Block | None = None
        self.deadline_hit = False
        self.chapter_attempts = 0
        self.book_attempts = 0
        self.book_status = ReadThroughBookPassStatus.PENDING.value
        self.book_error: str | None = None

    # ---- ownership -------------------------------------------------------------------------------

    def _owned_clause(self) -> tuple[Any, ...]:
        return (
            ReadThrough.id == self.id,
            ReadThrough.owner_token == self.token,
            ReadThrough.status.in_(_OWNED_STATUSES),
            ReadThrough.lease_expires_at > func.now(),
        )

    @asynccontextmanager
    async def _owned(self) -> AsyncIterator[AsyncSession]:
        """A short transaction that first proves this worker still owns the run. Commits on success; an
        exception in the body rolls everything back (the session closes without commit)."""
        if self.lost:
            raise _OwnershipLost
        async with self.session_factory() as session:
            still_ours = (
                await session.execute(
                    update(ReadThrough)
                    .where(*self._owned_clause())
                    .values(updated_at=func.now())
                    .returning(ReadThrough.id)
                    .execution_options(synchronize_session=False)
                )
            ).first()
            if still_ours is None:
                await session.rollback()
                self.lost = True
                raise _OwnershipLost
            yield session
            await session.commit()

    def _cancel_call(self) -> None:
        task = self.call_task
        if task is not None and not task.done():
            task.cancel()

    async def heartbeat(self) -> None:
        """Renew the lease every ttl/3 and watch for a stop. A renewal that matches no row means ownership is
        gone: cancel the call and stop renewing. A DB error is logged and retried on the next tick — the lease
        has two more ticks of slack before it expires."""
        interval = max(self.ttl_s / 3, 0.01)
        while not self.lost:
            await asyncio.sleep(interval)
            try:
                async with self.session_factory() as session:
                    row = (
                        await session.execute(
                            update(ReadThrough)
                            .where(*self._owned_clause())
                            .values(lease_expires_at=func.now() + timedelta(seconds=self.ttl_s))
                            .returning(ReadThrough.status, ReadThrough.stop_requested_at)
                            .execution_options(synchronize_session=False)
                        )
                    ).first()
                    await session.commit()
            except Exception:
                log.warning("read_through.heartbeat_failed", read_through_id=str(self.id), exc_info=True)
                continue
            if row is None:
                self.lost = True
                self._cancel_call()
                return
            if row.stop_requested_at is not None or row.status == ReadThroughStatus.STOPPING.value:
                self.stop = True
                self._cancel_call()

    # ---- pre-checks and accounting ---------------------------------------------------------------

    async def _precheck(self, parts: Any, max_tokens: int) -> _Block | None:
        """Everything that must hold before a PAID attempt, read fresh from the run row. Raises
        `_OwnershipLost` when the run is no longer ours."""
        if self.lost:
            raise _OwnershipLost
        async with self.session_factory() as session:
            row = (
                await session.execute(
                    select(
                        ReadThrough.owner_token,
                        ReadThrough.status,
                        ReadThrough.stop_requested_at,
                        (ReadThrough.lease_expires_at > func.now()).label("lease_ok"),
                        (func.now() >= ReadThrough.deadline_at).label("deadline_passed"),
                        ReadThrough.attempts_used,
                        ReadThrough.attempt_allowance,
                        ReadThrough.tokens_charged,
                    ).where(ReadThrough.id == self.id)
                )
            ).first()
        if row is None or row.owner_token != self.token or row.status not in _OWNED_STATUSES or not row.lease_ok:
            self.lost = True
            raise _OwnershipLost
        if self.stop or row.stop_requested_at is not None or row.status == ReadThroughStatus.STOPPING.value:
            self.stop = True
            return _Block("stop", STOP_REQUESTED_MESSAGE)
        if row.deadline_passed:
            self.deadline_hit = True
            return _Block("deadline", f"The read-through ran past its {self.run_deadline_s:g}-second time limit.")
        if row.attempts_used >= row.attempt_allowance:
            return _Block("allowance", f"The read-through used all {row.attempt_allowance} of its model attempts.")
        projected = int(row.tokens_charged) + prompts.estimated_input_tokens(parts) + max_tokens
        if projected > self.token_ceiling:
            return _Block(
                "ceiling", f"The next model call could pass the read-through's {self.token_ceiling:,}-token ceiling."
            )
        return None

    async def _begin_attempt(self, chapter: _Chapter | None) -> int:
        """Count the attempt BEFORE the call, so a crash mid-call still shows it against the allowance."""
        chapter_attempts: int | None = None
        async with self._owned() as session:
            await session.execute(
                update(ReadThrough)
                .where(ReadThrough.id == self.id)
                .values(attempts_used=ReadThrough.attempts_used + 1)
                .execution_options(synchronize_session=False)
            )
            if chapter is not None:
                chapter_attempts = (
                    await session.execute(
                        update(ReadThroughChapter)
                        .where(ReadThroughChapter.id == chapter.id)
                        .values(attempts=ReadThroughChapter.attempts + 1)
                        .returning(ReadThroughChapter.attempts)
                        .execution_options(synchronize_session=False)
                    )
                ).scalar_one()
        if chapter_attempts is None:
            self.book_attempts += 1
            return self.book_attempts
        self.chapter_attempts += 1
        return int(chapter_attempts)

    async def _mark_accounting_gap(self) -> None:
        """Flag that this run's spend is not fully in llm_calls. Deliberately NOT owner-conditional: it is a
        fact about money already spent, true whoever owns the run now, and it touches no result."""
        try:
            async with self.session_factory() as session:
                await session.execute(
                    update(ReadThrough)
                    .where(ReadThrough.id == self.id)
                    .values(accounting_gap=True)
                    .execution_options(synchronize_session=False)
                )
                await session.commit()
        except Exception:
            log.exception("read_through.accounting_gap_write_failed", read_through_id=str(self.id))

    async def _persist_telemetry(self, sink: telemetry.TelemetrySink) -> None:
        if not sink.records:
            return
        try:
            async with self.session_factory() as session:
                try:
                    telemetry_db.persist_sink(session, sink, run_id=self.id, book_id=self.book_id, chapter_id=None)
                    await session.commit()
                except Exception:
                    await session.rollback()
                    raise
        except Exception:
            log.exception(
                "read_through.telemetry_persist_failed", read_through_id=str(self.id), records=len(sink.records)
            )
            await self._mark_accounting_gap()

    async def _charge(self, cost: int) -> None:
        if cost <= 0:
            return
        try:
            async with self._owned() as session:
                await session.execute(
                    update(ReadThrough)
                    .where(ReadThrough.id == self.id)
                    .values(tokens_charged=ReadThrough.tokens_charged + cost)
                    .execution_options(synchronize_session=False)
                )
        except _OwnershipLost:
            raise
        except Exception:
            log.exception("read_through.charge_failed", read_through_id=str(self.id), cost=cost)
            await self._mark_accounting_gap()

    # ---- one attempt -----------------------------------------------------------------------------

    async def _call(self, **kwargs: Any) -> tuple[str, Usage]:
        """Run `llm.complete` as a Task the heartbeat can cancel, under the per-attempt deadline. The Task
        copies the current context, so the telemetry tags set by the caller reach `telemetry.record`."""
        task = asyncio.create_task(llm.complete(**kwargs))
        self.call_task = task
        if self.stop or self.lost:  # the heartbeat saw it between the pre-check and here
            task.cancel()
        try:
            async with asyncio.timeout(self.call_deadline_s):
                return await task
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if current is not None and current.cancelling():
                raise  # the worker task itself is being cancelled (shutdown): not ours to absorb
            raise _CallCancelled from None
        finally:
            self.call_task = None
            if not task.done():
                task.cancel()

    def _attempt_fn(
        self, *, parts: Any, chapter: _Chapter | None, validator: Callable[[dict[str, Any]], Any]
    ) -> Callable[[str, int], Awaitable[tuple[Attempt, Usage]]]:
        stage = CHAPTER_STAGE if chapter is not None else BOOK_STAGE
        phase = "chapter" if chapter is not None else "book"

        async def attempt_fn(model: str, max_tokens: int) -> tuple[Attempt, Usage]:
            # Re-checked here as well as before the chapter, so a FALLBACK attempt also respects stop,
            # deadline, allowance and ceiling. A blocked attempt makes no call and counts nothing.
            block = await self._precheck(parts, max_tokens)
            if block is not None:
                return Attempt(value=None, truncated=False, model=model, blocked=block), Usage(0, 0)
            attempt_no = await self._begin_attempt(chapter)
            sink = telemetry.TelemetrySink()
            usage: Usage | None = None
            try:
                hard = int(parts.input_budget) + max_tokens + _BUDGET_HEADROOM_TOKENS
                context = telemetry.CallContext(sink=sink, stage=stage, book_id=str(self.book_id))
                with (
                    telemetry.call_context(context),
                    telemetry.call_metadata(
                        read_through_id=str(self.id),
                        snapshot_chapter_id=str(chapter.id) if chapter is not None else None,
                        chapter_position=chapter.position if chapter is not None else None,
                        phase=phase,
                        attempt=attempt_no,
                        attempt_role="primary" if model == self.primary_model else "fallback",
                    ),
                ):
                    started = time.monotonic()
                    try:
                        raw, usage = await self._call(
                            model=model,
                            system=parts.system,
                            user=parts.user,
                            max_tokens=max_tokens,
                            budget=TokenBudget(max_tokens=hard, hard_max_tokens=hard),
                            input_budget=parts.input_budget,
                            effort=quality_effort(SETTING_KEY),
                            setting_key=SETTING_KEY,
                        )
                    except (TimeoutError, _CallCancelled, asyncio.CancelledError) as exc:
                        # llm.complete records failures only for `Exception`; a cancelled or timed-out call
                        # raises CancelledError inside it and leaves no row. Record one so the attempt stays
                        # attributable (zero measured tokens: the provider reported none to us). That includes
                        # the worker task's OWN cancellation (server shutdown): the attempt may be billed, so
                        # it is recorded, re-raised, and still flushed best-effort by the `finally` below.
                        if not sink.records:
                            telemetry.record(
                                model=model,
                                input_tokens=0,
                                output_tokens=0,
                                cache_creation_tokens=0,
                                cache_read_tokens=0,
                                truncated=False,
                                latency_ms=int((time.monotonic() - started) * 1000),
                                error=_unfinished_call_error(exc),
                                metadata={"max_tokens": max_tokens, "input_budget": parts.input_budget},
                            )
                        raise
                obj = extract_object(raw)
                value = validator(obj) if obj is not None else None
                error = None if value is not None else (UNPARSEABLE_MESSAGE if obj is None else INVALID_MESSAGE)
                ran = sink.records[-1].model if sink.records else model
                attempt = Attempt(
                    value=value, truncated=usage.truncated, model=model, usage=usage, error=error, model_used=ran
                )
                return attempt, usage
            finally:
                await self._persist_telemetry(sink)
                try:
                    await self._charge(usage.budget_cost if usage is not None else _sink_cost(sink))
                except _OwnershipLost:
                    # A lost lease must not REPLACE the worker task's own cancellation (server shutdown):
                    # raising here would turn a CancelledError into an ordinary exception that
                    # run_read_through logs and returns from. The spend is already in llm_calls via the
                    # flush above; recovery interrupts the run once the lease is seen expired.
                    current = asyncio.current_task()
                    if current is None or not current.cancelling():
                        raise
                    log.warning("read_through.charge_skipped_during_shutdown", read_through_id=str(self.id))

        return attempt_fn

    async def _escalate(self, *, parts: Any, chapter: _Chapter | None, validator: Callable[[dict[str, Any]], Any]):
        attempt, _model, _escalated = await attempt_with_escalation(
            setting_key=SETTING_KEY,
            primary_model=self.primary_model,
            primary_max_tokens=int(parts.max_tokens),
            attempt_fn=self._attempt_fn(parts=parts, chapter=chapter, validator=validator),
            is_success=_is_success,
            policy=policy_for_setting(SETTING_KEY),
        )
        if not isinstance(attempt, Attempt):  # the helper only ever returns what attempt_fn returned
            raise TypeError(f"unexpected attempt value: {type(attempt).__name__}")
        return attempt

    # ---- chapters --------------------------------------------------------------------------------

    async def _set_chapter(self, chapter: _Chapter, status: str, error: str | None = None) -> None:
        async with self._owned() as session:
            await session.execute(
                update(ReadThroughChapter)
                .where(ReadThroughChapter.id == chapter.id)
                .values(status=status, error=error)
                .execution_options(synchronize_session=False)
            )
        chapter.status = status
        chapter.error = error

    async def _read_chapter(self, chapter: _Chapter) -> bool:
        """Read one chapter. Returns False when the chapter loop must end (stop, deadline, allowance,
        ceiling); raises `_OwnershipLost` when nothing more may be written."""
        try:
            parts = prompts.build_chapter_prompt(
                self.snapshot,
                self.voice_guide,
                prompts.ChapterInput(position=chapter.position, label=chapter.label, text=chapter.text),
            )
        except Exception as exc:
            await self._set_chapter(chapter, _FAILED, _short(exc))
            return True

        block = await self._precheck(parts, int(parts.max_tokens))
        if block is not None:
            self.loop_end = block
            return False

        await self._set_chapter(chapter, _RUNNING)
        self.chapter_attempts = 0
        try:
            attempt = await self._escalate(parts=parts, chapter=chapter, validator=validate.validate_chapter_output)
        except _OwnershipLost:
            raise
        except llm.LlmRateLimited:
            await self._set_chapter(chapter, _FAILED, RATE_LIMITED_MESSAGE)
            return True
        except _CallCancelled:
            if self.lost:
                raise _OwnershipLost from None
            self.stop = True
            self.loop_end = _Block("stop", STOP_REQUESTED_MESSAGE)
            await self._set_chapter(chapter, _FAILED, STOPPED_CHAPTER_MESSAGE)
            return False
        except TimeoutError:
            await self._set_chapter(
                chapter, _FAILED, f"The model didn't finish within {self.call_deadline_s:g} seconds."
            )
            return True
        except Exception as exc:
            log.warning("read_through.chapter_failed", read_through_id=str(self.id), chapter_id=str(chapter.id))
            await self._set_chapter(chapter, _FAILED, _short(exc))
            return True

        if attempt.blocked is not None:
            self.loop_end = attempt.blocked
            if self.chapter_attempts == 0:
                await self._set_chapter(chapter, _SKIPPED)
            else:
                message = STOPPED_CHAPTER_MESSAGE if attempt.blocked.kind == "stop" else attempt.blocked.message
                await self._set_chapter(chapter, _FAILED, message)
            return False
        # Re-validate what the helper returned: it returns the fallback's value even when that failed too.
        if attempt.value is None or attempt.truncated:
            await self._set_chapter(chapter, _FAILED, TRUNCATED_MESSAGE if attempt.truncated else attempt.error)
            return True

        try:
            await self._save_chapter(chapter, attempt)
        except _OwnershipLost:
            raise
        except Exception as exc:
            log.exception("read_through.chapter_save_failed", read_through_id=str(self.id), chapter_id=str(chapter.id))
            await self._set_chapter(chapter, _FAILED, _short(exc))
        return True

    async def _save_chapter(self, chapter: _Chapter, attempt: Attempt) -> None:
        result = attempt.value
        kept: list[tuple[Any, list[dict[str, Any]]]] = []
        unlocatable = 0
        for note in result.notes:
            located = [anchors.locate(quote, chapter.text, chapter_id=str(chapter.id)) for quote in note.quotes]
            if not located or all(a.state == "unlocated" for a in located):
                unlocatable += 1  # nothing on the page to point at: the note cannot be checked, so it is dropped
                continue
            kept.append((note, [a.to_json() for a in located]))
        digest = result.digest.model_dump()
        async with self._owned() as session:
            for position, (note, anchor_json) in enumerate(kept):
                session.add(
                    ReadThroughNote(
                        read_through_id=self.id,
                        chapter_id=chapter.id,
                        position=position,
                        category=note.category,
                        priority=note.priority,
                        title=note.title,
                        observation=note.observation,
                        recommendation=note.recommendation,
                        anchor_role=note.anchor_role,
                        anchors=anchor_json,
                        scope_chapter_ids=[],
                        status=ReadThroughNoteStatus.OPEN.value,
                    )
                )
            await session.execute(
                update(ReadThroughChapter)
                .where(ReadThroughChapter.id == chapter.id)
                .values(
                    status=_DONE,
                    digest=digest,
                    notes_dropped=int(result.dropped_invalid) + unlocatable,
                    notes_capped=bool(result.capped),
                    model_used=attempt.model_used or attempt.model,
                    error=None,
                )
                .execution_options(synchronize_session=False)
            )
        chapter.status = _DONE
        chapter.error = None
        chapter.digest = digest
        chapter.note_titles = tuple(note.title for note, _ in kept)

    # ---- book pass -------------------------------------------------------------------------------

    async def _set_book(self, status: str, error: str | None = None, **extra: Any) -> None:
        async with self._owned() as session:
            await session.execute(
                update(ReadThrough)
                .where(ReadThrough.id == self.id)
                .values(book_pass_status=status, book_pass_error=error, **extra)
                .execution_options(synchronize_session=False)
            )
        self.book_status = status
        self.book_error = error

    async def _book_pass(self) -> None:
        if self.lost:
            raise _OwnershipLost
        if self.stop or (self.loop_end is not None and self.loop_end.kind in ("stop", "deadline")):
            return  # not_run, written with the final status
        done = [c for c in self.chapters if c.status == _DONE]
        if len(done) < 2:
            await self._set_book(ReadThroughBookPassStatus.SKIPPED.value)
            return
        try:
            mode = ReadThroughBookInputMode.FULL_TEXT.value
            inputs = [
                prompts.BookChapterInput(
                    position=c.position, label=c.label, text=c.text, digest=None, note_titles=c.note_titles
                )
                for c in done
            ]
            parts = prompts.build_book_prompt(self.snapshot, self.voice_guide, inputs, mode)
            if prompts.estimated_input_tokens(parts) > int(parts.input_budget):
                mode = ReadThroughBookInputMode.DIGESTS.value
                inputs = [
                    prompts.BookChapterInput(
                        position=c.position, label=c.label, text=None, digest=c.digest, note_titles=c.note_titles
                    )
                    for c in done
                ]
                parts = prompts.build_book_prompt(self.snapshot, self.voice_guide, inputs, mode)
        except Exception as exc:
            await self._set_book(ReadThroughBookPassStatus.FAILED.value, _short(exc))
            return

        block = await self._precheck(parts, int(parts.max_tokens))
        if block is not None:
            await self._set_book(ReadThroughBookPassStatus.NOT_RUN.value, block.message)
            return
        await self._set_book(
            ReadThroughBookPassStatus.PENDING.value,
            book_input_mode=mode,
            book_chapter_ids=[str(c.id) for c in done],
        )

        failed = ReadThroughBookPassStatus.FAILED.value
        try:
            attempt = await self._escalate(parts=parts, chapter=None, validator=validate.validate_book_output)
        except _OwnershipLost:
            raise
        except llm.LlmRateLimited:
            await self._set_book(failed, RATE_LIMITED_MESSAGE)
            return
        except _CallCancelled:
            if self.lost:
                raise _OwnershipLost from None
            self.stop = True
            await self._set_book(failed, STOPPED_BOOK_MESSAGE)
            return
        except TimeoutError:
            await self._set_book(failed, f"The model didn't finish within {self.call_deadline_s:g} seconds.")
            return
        except Exception as exc:
            await self._set_book(failed, _short(exc))
            return

        if attempt.blocked is not None:
            if self.book_attempts == 0:
                await self._set_book(ReadThroughBookPassStatus.NOT_RUN.value, attempt.blocked.message)
            else:
                message = STOPPED_BOOK_MESSAGE if attempt.blocked.kind == "stop" else attempt.blocked.message
                await self._set_book(failed, message)
            return
        if attempt.value is None or attempt.truncated:
            await self._set_book(failed, TRUNCATED_MESSAGE if attempt.truncated else attempt.error)
            return
        try:
            await self._save_book(done, attempt)
        except _OwnershipLost:
            raise
        except Exception as exc:
            log.exception("read_through.book_save_failed", read_through_id=str(self.id))
            await self._set_book(failed, _short(exc))

    async def _save_book(self, done: Sequence[_Chapter], attempt: Attempt) -> None:
        by_position = {c.position: c for c in done}
        kept: list[tuple[Any, list[_Chapter], list[dict[str, Any]]]] = []
        for note in attempt.value.notes:
            scope = [by_position[p] for p in dict.fromkeys(note.positions) if p in by_position]
            if not scope:
                continue  # every position it named is outside what the pass read
            kept.append((note, scope, [a for quote in note.quotes for a in _book_anchors(quote, scope)]))
        async with self._owned() as session:
            for position, (note, scope, anchor_json) in enumerate(kept):
                session.add(
                    ReadThroughNote(
                        read_through_id=self.id,
                        chapter_id=None,
                        position=position,
                        category=note.category,
                        priority=note.priority,
                        title=note.title,
                        observation=note.observation,
                        recommendation=note.recommendation,
                        anchor_role=note.anchor_role,
                        anchors=anchor_json,
                        scope_chapter_ids=[str(c.id) for c in scope],
                        status=ReadThroughNoteStatus.OPEN.value,
                    )
                )
            await session.execute(
                update(ReadThrough)
                .where(ReadThrough.id == self.id)
                .values(
                    book_pass_status=ReadThroughBookPassStatus.DONE.value,
                    book_pass_error=None,
                    book_model_used=attempt.model_used or attempt.model,
                )
                .execution_options(synchronize_session=False)
            )
        self.book_status = ReadThroughBookPassStatus.DONE.value
        self.book_error = None

    # ---- run -------------------------------------------------------------------------------------

    async def execute(self) -> None:
        for chapter in self.chapters:
            if chapter.status in (_DONE, _FAILED, _SKIPPED):
                continue  # a reclaimed run keeps what the previous owner finished
            if not await self._read_chapter(chapter):
                break
        await self._book_pass()
        await self.finish()

    def _coverage_summary(self) -> str | None:
        pieces: list[str] = []
        failed = [f"{c.label} ({c.error})" if c.error else c.label for c in self.chapters if c.status == _FAILED]
        skipped = [c.label for c in self.chapters if c.status == _SKIPPED]
        if failed:
            pieces.append("Failed: " + "; ".join(failed) + ".")
        if skipped:
            pieces.append("Skipped: " + ", ".join(skipped) + ".")
        if self.book_status == ReadThroughBookPassStatus.FAILED.value:
            pieces.append(f"Cross-chapter pass failed: {self.book_error}")
        elif self.book_status == ReadThroughBookPassStatus.NOT_RUN.value and self.book_error:
            pieces.append(f"Cross-chapter pass not run: {self.book_error}")
        return " ".join(pieces) or None

    def _final_status(self, *, stopped: bool, crash: str | None) -> tuple[str, str | None]:
        done = sum(1 for c in self.chapters if c.status == _DONE)
        loop_message = self.loop_end.message if self.loop_end is not None else None
        if crash is not None:
            return ReadThroughStatus.FAILED.value, _join(
                f"The read-through stopped unexpectedly: {crash}", self._coverage_summary()
            )
        if stopped:
            return ReadThroughStatus.STOPPED.value, _join(STOP_REQUESTED_MESSAGE, self._coverage_summary())
        if self.deadline_hit:
            deadline = f"The read-through ran past its {self.run_deadline_s:g}-second time limit."
            return ReadThroughStatus.FAILED.value, _join(deadline, self._coverage_summary())
        if done == 0:
            return ReadThroughStatus.FAILED.value, _join(
                "No chapter could be read.", loop_message, self._coverage_summary()
            )
        book_ok = self.book_status in (ReadThroughBookPassStatus.DONE.value, ReadThroughBookPassStatus.SKIPPED.value)
        if done == len(self.chapters) and book_ok:
            return ReadThroughStatus.SUCCEEDED.value, None
        return ReadThroughStatus.PARTIAL.value, _join(loop_message, self._coverage_summary())

    async def finish(self, *, crash: str | None = None) -> None:
        """The final, owner-conditional write: close out every chapter and the book pass, then the run."""
        async with self._owned() as session:
            stop_requested_at = (
                await session.execute(select(ReadThrough.stop_requested_at).where(ReadThrough.id == self.id))
            ).scalar_one()
            await session.execute(
                update(ReadThroughChapter)
                .where(ReadThroughChapter.read_through_id == self.id, ReadThroughChapter.status == _PENDING)
                .values(status=_SKIPPED)
                .execution_options(synchronize_session=False)
            )
            await session.execute(
                update(ReadThroughChapter)
                .where(ReadThroughChapter.read_through_id == self.id, ReadThroughChapter.status == _RUNNING)
                .values(status=_FAILED, error=INTERRUPTED_CHAPTER_MESSAGE)
                .execution_options(synchronize_session=False)
            )
            for chapter in self.chapters:
                if chapter.status == _PENDING:
                    chapter.status = _SKIPPED
                elif chapter.status == _RUNNING:
                    chapter.status, chapter.error = _FAILED, INTERRUPTED_CHAPTER_MESSAGE
            if self.book_status == ReadThroughBookPassStatus.PENDING.value:
                self.book_status = ReadThroughBookPassStatus.NOT_RUN.value
            status, error = self._final_status(stopped=self.stop or stop_requested_at is not None, crash=crash)
            await session.execute(
                update(ReadThrough)
                .where(ReadThrough.id == self.id)
                .values(
                    status=status,
                    error=error,
                    finished_at=func.now(),
                    lease_expires_at=None,
                    book_pass_status=self.book_status,
                    book_pass_error=self.book_error,
                )
                .execution_options(synchronize_session=False)
            )
        log.info("read_through.finished", read_through_id=str(self.id), status=status)


async def _claim(session_factory: Callable[[], AsyncSession], run_id: uuid.UUID) -> _Run | None:
    """Take ownership with one conditional UPDATE: a queued run, or a running one whose lease has expired.
    No row back → someone else owns it, or it is terminal, or it does not exist."""
    token = uuid.uuid4()
    async with session_factory() as session:
        snapshot = (
            await session.execute(select(ReadThrough.settings_snapshot).where(ReadThrough.id == run_id))
        ).scalar_one_or_none()
        if snapshot is None:
            return None
        ttl_s = _snap_float(snapshot, "read_through_lease_ttl_s")
        claimed = (
            await session.execute(
                update(ReadThrough)
                .where(
                    ReadThrough.id == run_id,
                    or_(
                        ReadThrough.status == ReadThroughStatus.QUEUED.value,
                        and_(
                            ReadThrough.status == ReadThroughStatus.RUNNING.value,
                            ReadThrough.lease_expires_at < func.now(),
                        ),
                    ),
                )
                .values(
                    status=ReadThroughStatus.RUNNING.value,
                    owner_token=token,
                    lease_expires_at=func.now() + timedelta(seconds=ttl_s),
                    started_at=func.coalesce(ReadThrough.started_at, func.now()),
                )
                .returning(ReadThrough.book_id, ReadThrough.voice_guide_snapshot)
                .execution_options(synchronize_session=False)
            )
        ).first()
        if claimed is None:
            await session.rollback()
            return None
        await session.commit()

        chapter_rows = (
            await session.execute(
                select(
                    ReadThroughChapter.id,
                    ReadThroughChapter.position,
                    ReadThroughChapter.label,
                    ReadThroughChapter.text,
                    ReadThroughChapter.status,
                    ReadThroughChapter.digest,
                    ReadThroughChapter.error,
                )
                .where(ReadThroughChapter.read_through_id == run_id)
                .order_by(ReadThroughChapter.position)
            )
        ).all()
        titles: dict[uuid.UUID, list[str]] = {}
        done_ids = [r.id for r in chapter_rows if r.status == _DONE]
        if done_ids:
            for chapter_id, title in (
                await session.execute(
                    select(ReadThroughNote.chapter_id, ReadThroughNote.title)
                    .where(ReadThroughNote.chapter_id.in_(done_ids))
                    .order_by(ReadThroughNote.position)
                )
            ).all():
                titles.setdefault(chapter_id, []).append(title)
        await session.rollback()

    chapters = [
        _Chapter(
            id=r.id,
            position=r.position,
            label=r.label,
            text=r.text,
            status=r.status,
            digest=r.digest,
            error=r.error,
            note_titles=tuple(titles.get(r.id, ())),
        )
        for r in chapter_rows
    ]
    return _Run(
        session_factory=session_factory,
        run_id=run_id,
        token=token,
        book_id=claimed.book_id,
        snapshot=snapshot,
        voice_guide=claimed.voice_guide_snapshot,
        chapters=chapters,
    )


async def run_read_through(
    read_through_id: uuid.UUID, *, session_factory: Callable[[], AsyncSession] | None = None
) -> None:
    """Claim and run one read-through to a terminal status. Never raises an `Exception`: a failure after the
    claim is written as status failed when the run is still ours. (A cancellation of THIS task — server
    shutdown — still propagates as asyncio requires; the lease then expires and recovery interrupts the run.)"""
    factory = session_factory or SessionFactory
    try:
        run = await _claim(factory, read_through_id)
    except Exception:
        log.exception("read_through.claim_failed", read_through_id=str(read_through_id))
        return
    if run is None:
        log.info("read_through.not_claimed", read_through_id=str(read_through_id))
        return

    heartbeat = asyncio.create_task(run.heartbeat(), name=f"read-through-heartbeat-{read_through_id}")
    try:
        await run.execute()
    except _OwnershipLost:
        log.warning("read_through.ownership_lost", read_through_id=str(read_through_id))
    except Exception as exc:
        log.exception("read_through.crashed", read_through_id=str(read_through_id))
        try:
            await run.finish(crash=_short(exc))
        except _OwnershipLost:
            log.warning("read_through.ownership_lost", read_through_id=str(read_through_id))
        except Exception:
            log.exception("read_through.crash_finalize_failed", read_through_id=str(read_through_id))
    finally:
        heartbeat.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat


async def recover_read_throughs(session: AsyncSession) -> int:
    """Interrupt runs nobody is working on: owned runs whose lease has EXPIRED, and queued runs nobody claimed
    within the admission deadline. Conditional UPDATEs on the database clock only — a live lease is never
    touched, and a late write from the old owner fails its ownership check. The caller commits."""
    expired_ids = list(
        (
            await session.execute(
                update(ReadThrough)
                .where(
                    ReadThrough.status.in_(_OWNED_STATUSES),
                    or_(ReadThrough.lease_expires_at.is_(None), ReadThrough.lease_expires_at < func.now()),
                )
                .values(
                    status=ReadThroughStatus.INTERRUPTED.value,
                    finished_at=func.now(),
                    error=INTERRUPTED_MESSAGE,
                    book_pass_status=case(
                        (
                            ReadThrough.book_pass_status == ReadThroughBookPassStatus.PENDING.value,
                            ReadThroughBookPassStatus.NOT_RUN.value,
                        ),
                        else_=ReadThrough.book_pass_status,
                    ),
                )
                .returning(ReadThrough.id)
                .execution_options(synchronize_session=False)
            )
        ).scalars()
    )
    if expired_ids:
        await session.execute(
            update(ReadThroughChapter)
            .where(ReadThroughChapter.read_through_id.in_(expired_ids), ReadThroughChapter.status == _RUNNING)
            .values(status=_FAILED, error=INTERRUPTED_CHAPTER_MESSAGE)
            .execution_options(synchronize_session=False)
        )
        await session.execute(
            update(ReadThroughChapter)
            .where(ReadThroughChapter.read_through_id.in_(expired_ids), ReadThroughChapter.status == _PENDING)
            .values(status=_SKIPPED)
            .execution_options(synchronize_session=False)
        )

    admission_s = int(settings.read_through_admission_deadline_s)
    unstarted_ids = list(
        (
            await session.execute(
                update(ReadThrough)
                .where(
                    ReadThrough.status == ReadThroughStatus.QUEUED.value,
                    ReadThrough.owner_token.is_(None),
                    ReadThrough.created_at < func.now() - timedelta(seconds=admission_s),
                )
                .values(
                    status=ReadThroughStatus.INTERRUPTED.value,
                    finished_at=func.now(),
                    error=f"Didn't start within {admission_s} seconds — nothing was spent.",
                    book_pass_status=ReadThroughBookPassStatus.NOT_RUN.value,
                )
                .returning(ReadThrough.id)
                .execution_options(synchronize_session=False)
            )
        ).scalars()
    )
    if unstarted_ids:
        await session.execute(
            update(ReadThroughChapter)
            .where(ReadThroughChapter.read_through_id.in_(unstarted_ids), ReadThroughChapter.status == _PENDING)
            .values(status=_SKIPPED)
            .execution_options(synchronize_session=False)
        )
    recovered = len(expired_ids) + len(unstarted_ids)
    if recovered:
        log.warning("read_through.recovered", expired=len(expired_ids), unstarted=len(unstarted_ids))
    return recovered
