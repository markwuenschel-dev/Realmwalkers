"""The persisted repair-attempt budget — one central policy, both entry points (ADR-0031 D7 / T2 #230).

**The defect this replaces.** Nothing bounded automatic repair. `RepairAttempt.attempt_no` is written but
was never compared against a cap at any site; the only cap was `sweeper._attempts`, an **in-process** dict
(reset on redeploy) keyed by **run_id** rather than task, which `drain_queued_repair_tasks` never
consulted. Since a `NEEDS_ANOTHER_REPAIR` verdict re-queues the task and the drain re-applies it, the
drain path was unbounded: a task could loop apply → revise → verify → apply forever.

**The four properties D7 demands, and where each lives:**

* *Counted per repair CYCLE, not per attempt row.* The counter is `RepairTask.repair_cycle_attempts`. A
  chapter-scoped repair mints one `RepairAttempt` per member scene, so counting rows would exhaust a cap
  of three on a three-scene chapter's first action. One apply = one attempt, whatever it fans out to.
* *Persisted, idempotent across restart.* It is a column, and the terminal reason is a column. A redeploy
  cannot un-park a parked cycle or refund a spent budget.
* *Central — both the sweeper and the drain reserve through ONE seam.* Both reach
  `production_repair.apply_repair_task`, and the reservation happens inside it. A cap either worker could
  bypass is not a cap (that was exactly the drain's bug).
* *Deterministic park with an exposed terminal reason.* At the cap the task parks WAITING_FOR_HUMAN with
  `terminal_reason` set, and only an explicit human grant reopens it.

**What does NOT consume budget:** a manual apply. `autonomous=False` neither increments the counter nor is
blocked by it, so a human is never told "this repair is out of attempts" — that limit exists to bound
*unattended* work, and a human deciding to apply something is the thing the limit defers to.
"""

from __future__ import annotations

from dataclasses import dataclass

from dominion.shared.enums import RepairTerminalReason

#: One persisted maximum of three automatic attempts per repair cycle (ADR-0031 D7, verbatim: "One
#: persisted maximum of three repair attempts per repair cycle"). A module constant, not a KV setting:
#: D7 fixes the number, and the sweeper's separate `sweeper_max_attempts` governs a DIFFERENT unit (how
#: many applies one *run* may drive in one process) which is left alone.
MAX_REPAIR_CYCLE_ATTEMPTS = 3


@dataclass(frozen=True)
class Reservation:
    """The outcome of asking for one automatic attempt. `granted=False` means the caller must park the
    task rather than apply it; `attempts` is the budget consumed so far (after a granted reservation)."""

    granted: bool
    attempts: int
    terminal_reason: str = ""

    @property
    def human_message(self) -> str:
        return (
            f"Repair cycle reached its {MAX_REPAIR_CYCLE_ATTEMPTS}-attempt limit without resolving. "
            "Parked for review — Approve & apply reopens the cycle."
        )


def reserve_automatic_attempt(task, *, autonomous: bool) -> Reservation:
    """Consume one automatic attempt for `task`, or refuse. **Mutates the task** — the caller must already
    hold the row lock and must commit the increment in the SAME transaction as the state change it
    authorizes, or the budget can be double-spent by two workers racing on attempt 3.
    (`apply_repair_task` satisfies both: it holds `SELECT ... FOR UPDATE` on the row and the caller owns
    one transaction around the whole apply.)

    A manual caller (`autonomous=False`) is passed through untouched: no increment, no refusal.
    """
    if not autonomous:
        return Reservation(True, task.repair_cycle_attempts or 0)
    if task.terminal_reason:
        # Already parked. Re-reserving would silently resurrect a cycle a human has not reopened — and
        # this is the branch that makes the cap survive a restart, since the reason is persisted.
        return Reservation(False, task.repair_cycle_attempts or 0, terminal_reason=task.terminal_reason)
    spent = task.repair_cycle_attempts or 0
    if spent >= MAX_REPAIR_CYCLE_ATTEMPTS:
        return Reservation(False, spent, terminal_reason=RepairTerminalReason.ATTEMPT_CAP_REACHED.value)
    task.repair_cycle_attempts = spent + 1
    return Reservation(True, task.repair_cycle_attempts)


def reopen_cycle(task) -> None:
    """The explicit human action D7 requires to reopen a parked cycle: clear the terminal reason and
    refund the budget. Called only on a real human grant (Approve & apply) — never by a worker, and never
    as a side effect of a retry."""
    task.repair_cycle_attempts = 0
    task.terminal_reason = None
