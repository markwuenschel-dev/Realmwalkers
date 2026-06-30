"""DB persistence for collected telemetry sinks.

Kept separate from `workers/telemetry.py`, which is deliberately DB-free (import-light, no model
imports) so any worker can `record()` without dragging in SQLAlchemy or risking an import cycle. An
orchestrator collects a `TelemetrySink` during a run (calls made inside `call_context`), then calls
`persist_sink()` once to flush the collected records to `llm_calls`. The caller commits.

Every instrumented unit of work (one chapter derive, one scene draft, one beat proposal, one summary
regeneration) shares a single book/chapter, so those ids are passed once for the whole sink; the
per-call dimensions (stage, scene_no, seed_id, model, tokens) ride on each CallRecord.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from dominion.shared.models import LlmCall
from dominion.workers.telemetry import TelemetrySink


def persist_sink(
    session: AsyncSession,
    sink: TelemetrySink,
    *,
    run_id: uuid.UUID | None,
    book_id: uuid.UUID,
    chapter_id: uuid.UUID | None = None,
) -> None:
    """Flush a run's collected per-call telemetry to `llm_calls` (the caller commits). Pure exhaust:
    a bad row is dropped rather than failing the work that produced real output. Rows roll up under
    `run_id` in the Desk's per-run history; pass a fresh uuid4 for work that has no standing run id
    (e.g. summary regeneration) so each invocation is its own run row, or None to land in the legacy
    bucket."""
    for rec in sink.records:
        try:
            seed_id = uuid.UUID(rec.seed_id) if rec.seed_id else None
        except (ValueError, TypeError):
            seed_id = None
        session.add(
            LlmCall(
                run_id=run_id,
                book_id=book_id,
                chapter_id=chapter_id,
                scene_no=rec.scene_no,
                scene_seed_id=seed_id,
                stage=rec.stage,
                model=rec.model,
                input_tokens=rec.input_tokens,
                output_tokens=rec.output_tokens,
                cache_creation_tokens=rec.cache_creation_tokens,
                cache_read_tokens=rec.cache_read_tokens,
                truncated=rec.truncated,
                latency_ms=rec.latency_ms,
                error=rec.error,
            )
        )
