"""ADR-0032 D7/D8 (W4) — boot reconciliation of stranded imported-prose revise intent.

The failure this repairs: a redeploy killed the process between "the author clicked Revise" and the
durable `RevisionRequest` that records it. The scene is left at `revision_requested` with nothing
active behind it — visibly mid-revision, actually inert, and invisible to every worker.

This module is a COORDINATOR, exactly like `accept_revision_intent` (ADR-0032 D4). It owns the
per-chapter workflow lock, the transaction boundary, and the order of two single-owner mutations —
and nothing else:

    reconstruct_revision_request_locked  (revision-owned: the sole RevisionRequest writer)
    ensure_import_adoption_locked        (adoption-owned: the sole ImportAdoption writer)

It is the fourth and last caller of the one adoption-entry seam, and the only one that supplies
`RECORD_WITHOUT_SPEND`: a boot must restore durable INTENT without buying anything. The reconstructed
adoption lands `awaiting_start` — not worker-claimable — because an unpaused queue is not consent for
historical spend. An operator's explicit Start is what converts it, upgrading the same row to `queued`
and `operator_independent` through the same seam.

Runs in the lifespan seam, deliberately NOT in `apply_lightweight_migrations` (D7) — that function
also provisions the test fixture, and reconciliation is production recovery, not schema provisioning.

Restart/concurrency: each candidate chapter is its own locked transaction, so a crash mid-scan keeps
every chapter already committed and the next boot picks up the rest. Every predicate is re-verified
UNDER the lock, so a second scanner (or a live Revise racing the boot) finds the request already there
and does nothing. Bounded growth: hold events dedup on (hold_code, scene_id, current prose hash), so
repeated boots over an unrepaired scene append no new rows.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dominion.shared.adoption_entry import AdoptionEntryError, ensure_import_adoption_locked
from dominion.shared.chapter_lock import ChapterWorkflowBusy, run_under_chapter_workflow
from dominion.shared.db import SessionFactory
from dominion.shared.enums import (
    AdoptionOperation,
    Decision,
    IntegrityHoldReason,
    RevisionRequestStatus,
    SceneStatus,
)
from dominion.shared.models import Activity, Approval, Chapter, RevisionRequest, Scene
from dominion.workers.activity import record_activity
from dominion.workers.revision import prose_hash, reconstruct_revision_request_locked

log = structlog.get_logger()

# The one hold this module can raise (D8). A distinct `hold_code` per diagnosable condition; the
# `reason_code` inside it says which way the condition failed.
HOLD_CODE = "legacy_revision_intent_missing"

# Boot waits LONGER than a request path (4s) but never forever. `timeout_ms=None` would set no
# `lock_timeout` at all, so a chapter lock held by an overlapping old container during a rolling deploy
# would stall the awaited lifespan and the new container would never start serving — and the
# `ChapterWorkflowBusy` skip below would be unreachable code. A bounded wait makes the skip real: the
# scene stays a candidate and the next boot retries it.
LOCK_TIMEOUT_MS = 10_000

# A runaway guard, not a routine truncation — recovery is meant to finish in one boot. Each candidate
# costs one locked transaction before the app serves traffic, so an absurd backlog (a corrupted import,
# a bad migration) must not hold boot open indefinitely. Hitting this is LOGGED loudly, never silent.
MAX_CANDIDATES_PER_BOOT = 500

_ACTIVE_REQUEST_STATUSES = (
    RevisionRequestStatus.AWAITING_CONTRACT.value,
    RevisionRequestStatus.QUEUED.value,
    RevisionRequestStatus.RUNNING.value,
)


@dataclass(frozen=True)
class ReconciliationReport:
    """What one boot pass did. `skipped` counts candidates a concurrent writer, a contended lock, or an
    ineligible chapter took off the table — expected, not an error; the next boot re-evaluates them.
    `deferred` counts candidates beyond `MAX_CANDIDATES_PER_BOOT` that this pass did not even look at,
    reported so a capped run can never be mistaken for a complete one."""

    scanned: int = 0
    reconstructed: int = 0
    holds_recorded: int = 0
    holds_deduped: int = 0
    skipped: int = 0
    deferred: int = 0


def hold_dedup_key(scene_id: uuid.UUID, current_prose_hash: str) -> str:
    """Deterministic dedup key for the D8 hold: one event per unresolved PROSE SNAPSHOT, not per boot.
    A hand-edit changes the prose hash, which is a genuinely new diagnostic state and gets its own
    event; the old one remains as history."""
    return hashlib.sha256(f"{HOLD_CODE}|{scene_id}|{current_prose_hash}".encode()).hexdigest()


async def _candidate_scene_ids(session: AsyncSession) -> list[tuple[uuid.UUID, uuid.UUID]]:
    """Locate candidates only — decide nothing from this read (chapter_lock protocol step 1). Every
    predicate is re-verified under the lock before anything is written.

    A candidate is a scene sitting at `revision_requested` with no active RevisionRequest behind it:
    the first two conjuncts of D8's derived-hold condition. Whether it is reconstructible or held
    depends on the third (its current-row Approval), evaluated per-scene under the lock."""
    rows = (
        await session.execute(
            select(Scene.id, Scene.chapter_id)
            .where(
                Scene.status == SceneStatus.REVISION_REQUESTED,
                ~select(RevisionRequest.id)
                .where(
                    RevisionRequest.target_scene_id == Scene.id,
                    RevisionRequest.status.in_(_ACTIVE_REQUEST_STATUSES),
                )
                .exists(),
            )
            .order_by(Scene.chapter_id, Scene.scene_no)
        )
    ).all()
    return [(r[0], r[1]) for r in rows]


class IndeterminateApprovalOrder(RuntimeError):
    """The scene's CURRENT approval row cannot be established (ADR-0032 D7). `Approval` has no
    monotonic sequence — only `decided_at`, which is the Postgres TRANSACTION timestamp and is
    therefore constant across rows written together. When the two newest approvals share it, "latest"
    is genuinely undecidable and the id tiebreak would be a coin flip between, say, a REVISE and the
    APPROVE that replaced it.

    Reconciliation refuses to guess: this skips the scene, leaving it exactly as the boot found it. It
    is NOT a D8 integrity hold — the hold means "no valid current intent", a conclusion this read is
    precisely unable to reach. (`Activity` solved the same ordering problem with an Identity `seq`
    column; giving `Approval` one would remove this case, and is the fix if it is ever observed.)"""

    def __init__(self, scene_id: uuid.UUID) -> None:
        self.scene_id = scene_id
        super().__init__(f"scene {scene_id} has two approvals sharing decided_at; current row undecidable")


async def _current_row_revise_approval(
    session: AsyncSession, scene: Scene
) -> tuple[Approval | None, IntegrityHoldReason | None]:
    """D7's identity test: take the LATEST Approval OVERALL for this scene, THEN test that it is a
    REVISE for this scene version. Never "the latest REVISE" — that query would happily skip past a
    later APPROVE or DENY and resurrect intent the author already replaced.

    Fails closed on IDENTITY drift (the approval's version vs the scene's), never on elapsed time — an
    old revise intent is still the author's intent; one raised against different prose is not.
    """
    newest = (
        (
            await session.execute(
                select(Approval)
                .where(Approval.scene_id == scene.id)
                .order_by(Approval.decided_at.desc(), Approval.id.desc())
                .limit(2)
            )
        )
        .scalars()
        .all()
    )
    if not newest:
        return None, IntegrityHoldReason.MISSING_APPROVAL
    if len(newest) == 2 and newest[0].decided_at == newest[1].decided_at:
        raise IndeterminateApprovalOrder(scene.id)

    latest = newest[0]
    if latest.decision != Decision.REVISE:
        return None, IntegrityHoldReason.LATEST_DECISION_NOT_REVISE
    if latest.version != scene.version:
        return None, IntegrityHoldReason.SCENE_VERSION_MISMATCH
    return latest, None


async def _record_hold_locked(
    session: AsyncSession, *, scene: Scene, chapter: Chapter, reason: IntegrityHoldReason
) -> bool:
    """Project the derived hold onto `Activity` (D8). Existence-check and insert in the SAME
    chapter-locked transaction, so two scanners cannot both append. Uses `record_activity`, NOT
    `safe_record_activity`: an integrity hold that silently failed to record is worse than a failed
    boot step, because the operator would never learn the scene needs attention. Returns True if a new
    event was appended, False if this snapshot was already reported."""
    current_hash = prose_hash(scene.prose)
    dedup_key = hold_dedup_key(scene.id, current_hash)
    # `integrity_hold` is a SHARED kind — the boot job-ownership probe (ADR 0027) emits it too — so the
    # dedup read is scoped by source as well. Matching on the key alone would work today only because
    # the other producer writes no `dedup_key`, which is an accident, not a contract.
    already = (
        await session.execute(
            select(func.count())
            .select_from(Activity)
            .where(
                Activity.kind == "integrity_hold",
                Activity.source == "reconciliation",
                Activity.payload_json["dedup_key"].astext == dedup_key,
            )
        )
    ).scalar_one()
    if already:
        return False

    await record_activity(
        session,
        kind="integrity_hold",
        title=f"Scene {scene.scene_no} is marked for revision but its revise intent is missing",
        source="reconciliation",
        severity="warn",
        book_id=chapter.book_id,
        chapter_id=chapter.id,
        detail=(
            "Boot reconciliation could not rebuild this scene's revision request from its approval "
            "history. The scene stays marked for revision until a human re-decides it."
        ),
        payload={
            "hold_code": HOLD_CODE,
            "reason_code": reason.value,
            "scene_id": str(scene.id),
            "prose_hash": current_hash,
            "dedup_key": dedup_key,
        },
    )
    return True


async def _reconcile_one_scene(
    session: AsyncSession, *, scene_id: uuid.UUID, chapter_id: uuid.UUID, report: dict[str, int]
) -> None:
    """One candidate, one locked transaction. Mutates `report` counters in place."""

    async def _body() -> None:
        scene = await session.get(Scene, scene_id)  # first materialization, UNDER the held lock
        chapter = await session.get(Chapter, chapter_id) if scene is not None else None
        if scene is None or chapter is None or scene.status != SceneStatus.REVISION_REQUESTED:
            report["skipped"] += 1  # raced: deleted, or re-decided between locate and lock
            return

        # Re-verify the "no active request" conjunct under the lock — a live Revise may have landed one
        # while the boot scan was running, in which case reconstruction would create a duplicate.
        active = (
            await session.execute(
                select(func.count())
                .select_from(RevisionRequest)
                .where(
                    RevisionRequest.target_scene_id == scene.id,
                    RevisionRequest.status.in_(_ACTIVE_REQUEST_STATUSES),
                )
            )
        ).scalar_one()
        if active:
            report["skipped"] += 1
            return

        approval, reason = await _current_row_revise_approval(session, scene)
        if approval is None:
            assert reason is not None  # the two are exclusive by construction
            if await _record_hold_locked(session, scene=scene, chapter=chapter, reason=reason):
                report["holds_recorded"] += 1
            else:
                report["holds_deduped"] += 1
            return

        # Valid current-row REVISE: rebuild the request, then enter adoption — in that order, so the
        # request exists to be linked, and both land in this one transaction or neither does.
        request = await reconstruct_revision_request_locked(session, scene=scene, chapter=chapter, approval=approval)
        entry = await ensure_import_adoption_locked(
            session, chapter_id=chapter_id, operation=AdoptionOperation.RECONCILIATION
        )
        request.import_adoption_id = entry.adoption.id
        report["reconstructed"] += 1
        log.info(
            "adoption_entry_transition",
            action=entry.effect.value,
            trigger=entry.trigger,
            entry_intent=entry.entry_intent.value,
            from_status=entry.from_status,
            to_status=entry.to_status,
            liveness_basis=entry.liveness_basis,
            adoption_id=str(entry.adoption.id),
            chapter_id=str(chapter_id),
            request_id=str(request.id),
            collided=entry.collided,
        )

    await run_under_chapter_workflow(session, chapter_id, _body, timeout_ms=LOCK_TIMEOUT_MS)


async def reconcile_legacy_revision_intent(
    session_factory: async_sessionmaker[AsyncSession] = SessionFactory,
) -> ReconciliationReport:
    """Rebuild every stranded scene's revise intent, or record why it could not be rebuilt.

    Commits per candidate, never in bulk, so one ineligible or contended chapter cannot strand the rest.
    An `AdoptionEntryError` (the chapter is no longer evidence-only, or already carries an approved
    contract — amendment territory, #261), a `ChapterWorkflowBusy` (bounded by `LOCK_TIMEOUT_MS`), and an
    `IndeterminateApprovalOrder` are all counted as `skipped` and re-evaluated on the next boot; none is
    allowed to fail the boot.

    ONE FRESH SESSION PER CANDIDATE (mirroring `background_work.drain_queued_repair_tasks`): a failure
    while ACQUIRING the lock happens outside `run_under_chapter_workflow`'s try/rollback, so on a shared
    session it would leave an aborted transaction that fails every remaining candidate — a single poison
    scene silently degrading the whole pass to `skipped`. A per-candidate session cannot poison its
    successors.

    NOTE the D12 telemetry above is emitted inside `_body`, i.e. pre-commit, unlike the request-path
    coordinators. That is deliberate: this runs per-chapter in a boot loop with no post-commit hook per
    chapter, and a rolled-back chapter is already reported by the surrounding `except` as `skipped`.
    """
    report = {"scanned": 0, "reconstructed": 0, "holds_recorded": 0, "holds_deduped": 0, "skipped": 0}
    async with session_factory() as scan_session:
        candidates = await _candidate_scene_ids(scan_session)

    deferred = 0
    if len(candidates) > MAX_CANDIDATES_PER_BOOT:
        deferred = len(candidates) - MAX_CANDIDATES_PER_BOOT
        candidates = candidates[:MAX_CANDIDATES_PER_BOOT]
        log.error(
            "adoption_reconciliation_capped",
            cap=MAX_CANDIDATES_PER_BOOT,
            deferred=deferred,
            note="candidate backlog exceeds one boot's budget; the remainder retries on the next boot",
        )
    report["scanned"] = len(candidates)

    for scene_id, chapter_id in candidates:
        try:
            async with session_factory() as session:
                await _reconcile_one_scene(session, scene_id=scene_id, chapter_id=chapter_id, report=report)
        except (AdoptionEntryError, ChapterWorkflowBusy, IndeterminateApprovalOrder) as exc:
            report["skipped"] += 1
            log.info(
                "adoption_reconciliation_skipped",
                scene_id=str(scene_id),
                chapter_id=str(chapter_id),
                reason=type(exc).__name__,
            )
        except Exception as exc:  # noqa: BLE001 — one poison scene must never fail the boot
            report["skipped"] += 1
            log.error("adoption_reconciliation_error", scene_id=str(scene_id), error=str(exc))

    result = ReconciliationReport(**report, deferred=deferred)
    if candidates:
        log.info(
            "adoption_reconciliation_pass",
            scanned=result.scanned,
            reconstructed=result.reconstructed,
            holds_recorded=result.holds_recorded,
            holds_deduped=result.holds_deduped,
            skipped=result.skipped,
            deferred=result.deferred,
        )
    return result
