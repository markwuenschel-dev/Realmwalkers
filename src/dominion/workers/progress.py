"""In-process drafting-phase registry — runtime exhaust, never persisted (DESIGN §3).

The browser-driven drain (`jobs._drain` -> `worker.run_once` -> `pipeline.generate_one_scene`) runs
inside the API process, so a module-level dict is enough to surface *what* a job is doing right now
("drafting prose", "enriching · combat", "reviewing") to `GET /jobs/status` — with no DB write on the
hot path and no schema column for what is pure runtime state.

Contract: best-effort and fully defensive. A progress call must NEVER break drafting, so every public
function swallows its own errors; a missing entry just means the UI shows a generic "drafting…". The
terminal worker (`dominion-worker`) is a separate process, so its phases aren't visible here — that's
fine, since the Desk is the path a human actually watches.
"""
from __future__ import annotations

import time

# job_id (str) -> (phase, started_at_epoch_seconds). Cleared when the job finishes.
_phases: dict[str, tuple[str, float]] = {}


def set_phase(job_id: str | None, phase: str) -> None:
    """Record the current phase for a job, preserving the original start time across phase changes."""
    if not job_id:
        return
    try:
        existing = _phases.get(job_id)
        started = existing[1] if existing else time.time()
        _phases[job_id] = (phase, started)
    except Exception:  # noqa: BLE001 — progress reporting must never break the draft
        pass


def get(job_id: str | None) -> tuple[str | None, int | None]:
    """Current (phase, elapsed_seconds) for a job, or (None, None) if untracked."""
    if not job_id:
        return None, None
    try:
        entry = _phases.get(job_id)
        if not entry:
            return None, None
        phase, started = entry
        return phase, int(time.time() - started)
    except Exception:  # noqa: BLE001
        return None, None


def clear(job_id: str | None) -> None:
    """Forget a job once it's done/failed, so the registry doesn't grow without bound."""
    if not job_id:
        return
    try:
        _phases.pop(job_id, None)
    except Exception:  # noqa: BLE001
        pass
