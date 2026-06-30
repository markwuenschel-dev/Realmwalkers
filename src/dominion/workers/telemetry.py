"""Per-LLM-call telemetry capture (cache/usage/truncation), persisted for cross-run analysis.

`workers/progress.py` already surfaces *live* cache stats for the browser-driven drafting path, but
that state is ephemeral (an in-process dict, cleared when the job ends) and only covers drafting — so
the scene-packet derive path's cache/usage work was invisible, and nothing could compare cost or
cache efficiency across chapters/scenes/models after the fact.

This module is the missing seam. `llm.complete` records one `CallRecord` per successful call into a
context-scoped `TelemetrySink`, tagged with the dimensions (stage, book/chapter/scene/model) set by
the orchestrator via `call_context`. The orchestrator owns persistence: it creates the sink, runs its
calls inside `call_context`, then writes the collected records to `llm_calls` (the DB has the session;
this module deliberately stays DB-free and import-light to avoid cycles).

Async-safe: each asyncio task copies the current context at creation, so concurrent scenes can each
set their own per-scene tag (`call_context`) while sharing one sink object — appends interleave
safely under the single-threaded event loop.
"""

from __future__ import annotations

import contextvars
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field


@dataclass
class CallRecord:
    """One model call's observable cost + outcome. The dimensions (stage/book/chapter/scene) come from
    the active `CallContext`; the measures (tokens/cache/truncated/latency) from the call itself."""

    stage: str
    model: str
    input_tokens: int
    output_tokens: int
    cache_creation_tokens: int
    cache_read_tokens: int
    truncated: bool
    latency_ms: int
    book_id: str | None = None
    chapter_id: str | None = None
    scene_no: int | None = None
    seed_id: str | None = None
    error: str | None = None


@dataclass
class TelemetrySink:
    """Collects CallRecords for one orchestrated unit of work (e.g. one chapter's derive run)."""

    records: list[CallRecord] = field(default_factory=list)

    def add(self, rec: CallRecord) -> None:
        self.records.append(rec)


@dataclass
class CallContext:
    """The tag applied to every `llm.complete` made within its `call_context` block. The sink is shared
    across a run; the per-call dimensions (stage/scene/seed) are set fresh per scope."""

    sink: TelemetrySink
    stage: str
    book_id: str | None = None
    chapter_id: str | None = None
    scene_no: int | None = None
    seed_id: str | None = None


_ctx: contextvars.ContextVar[CallContext | None] = contextvars.ContextVar("llm_call_ctx", default=None)


@contextmanager
def call_context(ctx: CallContext) -> Iterator[CallContext]:
    """Tag every LLM call made inside this block with `ctx`. Restores the prior context on exit, so
    nested/sequential scopes (author then QA) don't leak their tag into each other."""
    token = _ctx.set(ctx)
    try:
        yield ctx
    finally:
        _ctx.reset(token)


def record(
    *,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_creation_tokens: int,
    cache_read_tokens: int,
    truncated: bool,
    latency_ms: int,
    error: str | None = None,
) -> None:
    """Append a CallRecord to the active sink, tagged from the current CallContext. A no-op when no
    context is active (every non-instrumented caller of `llm.complete` is unaffected)."""
    ctx = _ctx.get()
    if ctx is None:
        return
    ctx.sink.add(
        CallRecord(
            stage=ctx.stage,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_creation_tokens=cache_creation_tokens,
            cache_read_tokens=cache_read_tokens,
            truncated=truncated,
            latency_ms=latency_ms,
            book_id=ctx.book_id,
            chapter_id=ctx.chapter_id,
            scene_no=ctx.scene_no,
            seed_id=ctx.seed_id,
            error=error,
        )
    )
