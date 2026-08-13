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

SECOND SWEEP — CHAPTER-PACKET AUTHORITY (#261, invariant 6 second half). `reconcile_chapter_packet_
authority` verifies that the approve+supersede lineage is coherent and REPAIRS NOTHING. The two sweeps
live together because they share the seam (the lifespan), the lock discipline, and the `Activity`
integrity-hold projection — but they are opposites in intent: the D7/D8 sweep RECONSTRUCTS lost intent
(the crash happened BEFORE a commit, so nothing durable is at stake), whereas the authority sweep only
OBSERVES. "A crash before commit changes nothing; a crash after commit leaves a complete state that boot
reconciliation VERIFIES without guessing" is the whole invariant, and the second half is why the
authority sweep must never write a ChapterPacket row: the transition is one chapter-locked transaction,
so a torn half-state is unreachable, and observing one means a CONSTRAINT was bypassed. Guessing which
contract a book is written against would destroy the evidence and could pick the wrong one — that call
belongs to a human. See `reconcile_chapter_packet_authority` for the five states and their predicates.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dominion.shared.adoption_entry import AdoptionEntryError, ensure_import_adoption_locked
from dominion.shared.chapter_lock import ChapterWorkflowBusy, run_under_chapter_workflow
from dominion.shared.db import SessionFactory
from dominion.shared.enums import (
    AdoptionOperation,
    Decision,
    ImportAdoptionMode,
    IntegrityHoldReason,
    PacketStatus,
    RevisionRequestStatus,
    ScenePacketStatus,
    SceneStatus,
)
from dominion.shared.models import (
    Activity,
    Approval,
    Chapter,
    ChapterPacket,
    RevisionRequest,
    Scene,
    ScenePacket,
)
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


async def _hold_already_recorded(session: AsyncSession, dedup_key: str) -> bool:
    """Has this exact hold snapshot already been projected onto `Activity`?

    `integrity_hold` is a SHARED kind — the boot job-ownership probe (ADR 0027) emits it too — so the
    dedup read is scoped by source as well. Matching on the key alone would work today only because the
    other producer writes no `dedup_key`, which is an accident, not a contract.

    Every sweep in this module funnels through here so they cannot drift apart on the scoping predicate;
    their keys can never collide because each hashes its own `hold_code` into the digest.
    """
    return bool(
        (
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
    )


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
    if await _hold_already_recorded(session, dedup_key):
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


# ======================= CHAPTER-PACKET AUTHORITY: VERIFY, NEVER REPAIR ========================== #
#
# Invariant 6, second half (#261). Everything below OBSERVES. It reads `chapter_packets` /
# `scene_packets`, projects what it finds onto `Activity`, and changes no ChapterPacket row — ever.
#
# WHY VERIFICATION AND NOT REPAIR. The ONE locked authority transition in `packet/amendment.py` performs
# the whole approve+supersede move inside ONE `run_under_chapter_workflow` transaction: the predecessor
# leaves `approved` naming its successor, the successor takes the freed slot, the orphaned children are
# staled, and `run_under_chapter_workflow` owns the single commit. A crash anywhere before that commit
# rolls the lot back, so a torn half-state is not merely unlikely — it is unreachable. Four of the five
# states below are additionally forbidden by a DB constraint (`shared/migrations.py:326-387`).
#
# Which means: observing one of them is evidence that a constraint was BYPASSED — a hand-dropped index, a
# direct UPDATE, a writer that skipped the seam. A "repair" would then have to choose which packet holds
# authority, i.e. which contract a book is written against. That is a human's decision, and guessing it
# would also erase the only evidence of how the state arose. So the sweep's whole product is a durable,
# deduped, operator-visible report.

#: The one hold code this sweep can raise, mirroring `HOLD_CODE`'s role for D7/D8: a distinct code per
#: diagnosable CONDITION, with `reason_code` inside saying which way the condition failed.
AUTHORITY_HOLD_CODE = "chapter_packet_authority_violation"

#: Operator-facing title per reason. Written as the one line a human reads in the Desk feed, so it names
#: the state, not the check — "this chapter has two contracts" is actionable, "invariant 2 violated" is not.
_AUTHORITY_TITLES: dict[IntegrityHoldReason, str] = {
    IntegrityHoldReason.MULTIPLE_APPROVED_CHAPTER_PACKETS: (
        "This chapter has more than one approved contract, so drafting may obey either one"
    ),
    IntegrityHoldReason.SUPERSESSION_SUCCESSOR_MISSING: (
        "A superseded chapter contract does not name a contract that replaced it"
    ),
    IntegrityHoldReason.APPROVED_AMENDMENT_WITHOUT_PREDECESSOR: (
        "An approved amendment does not name the contract it replaced"
    ),
    IntegrityHoldReason.SUPERSEDED_PACKET_HAS_LIVE_CHILDREN: (
        "Scene contracts still claim authority from a superseded chapter contract"
    ),
    IntegrityHoldReason.CHAPTER_AUTHORITY_VACATED: (
        "This chapter gave up its approved contract and never took a new one"
    ),
}

#: Secondary line per reason: what a human should DO. Every one of them ends at a human decision, because
#: the sweep is forbidden from making it.
_AUTHORITY_DETAILS: dict[IntegrityHoldReason, str] = {
    IntegrityHoldReason.MULTIPLE_APPROVED_CHAPTER_PACKETS: (
        "Only one chapter contract may be approved at a time, and the readiness query picks an arbitrary "
        "one when there are two. Nothing was changed — decide which contract governs this chapter and "
        "supersede the other."
    ),
    IntegrityHoldReason.SUPERSESSION_SUCCESSOR_MISSING: (
        "The supersession record is incomplete: it points at no successor, or at a contract that no "
        "longer exists. Nothing was changed — the lineage needs a human to reconstruct it."
    ),
    IntegrityHoldReason.APPROVED_AMENDMENT_WITHOUT_PREDECESSOR: (
        "An amendment is copy-on-write FROM an approved contract, so an approved one must record which "
        "contract it replaced. Nothing was changed — the lineage needs a human to reconstruct it."
    ),
    IntegrityHoldReason.SUPERSEDED_PACKET_HAS_LIVE_CHILDREN: (
        "These scene contracts were derived from a chapter contract that no longer governs, so drafting "
        "against them would obey a retired contract. Nothing was changed — re-derive them against the "
        "current chapter contract."
    ),
    IntegrityHoldReason.CHAPTER_AUTHORITY_VACATED: (
        "The chapter has a superseded contract but no approved one, so no contract governs it and "
        "drafting cannot proceed. Nothing was changed — approve a replacement contract."
    ),
}


@dataclass(frozen=True)
class AuthorityFinding:
    """One observed impossible state, with the rows that evidence it.

    `dedup_subjects` — not `packet_ids` — is what the dedup key hashes, because the two differ for the
    findings whose identity includes something other than a ChapterPacket id (a dangling successor id, the
    set of live scene-packet children). Keeping them separate is what makes "the condition CHANGED" a new
    event while "the condition PERSISTS" stays one event: the subjects move, the packet ids may not.
    """

    chapter_id: uuid.UUID
    reason: IntegrityHoldReason
    packet_ids: tuple[uuid.UUID, ...]
    dedup_subjects: tuple[str, ...]
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def dedup_key(self) -> str:
        return authority_hold_dedup_key(self.chapter_id, self.reason, self.dedup_subjects)


@dataclass(frozen=True)
class ChapterAuthorityReport:
    """What one authority sweep observed. Per-category counts so the caller can log ONE line.

    The five category counters count FINDINGS, not chapters: a single chapter can be broken in several
    ways at once and each way is separately diagnosable. `holds_recorded + holds_deduped` therefore equals
    `findings_total` on a completed pass, and a persistent condition shows up as `deduped` on every boot
    after the first — that is the bounded-growth guarantee, visible in the log line rather than asserted.

    `skipped` counts chapters a contended lock or a concurrent writer took off the table (the next boot
    re-evaluates them); `deferred` counts chapters beyond `MAX_CANDIDATES_PER_BOOT` this pass never looked
    at, so a capped run can never be mistaken for a complete one.
    """

    scanned_chapters: int = 0
    multiple_approved: int = 0
    supersession_successor_missing: int = 0
    amendment_without_predecessor: int = 0
    superseded_with_live_children: int = 0
    authority_vacated: int = 0
    holds_recorded: int = 0
    holds_deduped: int = 0
    skipped: int = 0
    deferred: int = 0

    @property
    def findings_total(self) -> int:
        return (
            self.multiple_approved
            + self.supersession_successor_missing
            + self.amendment_without_predecessor
            + self.superseded_with_live_children
            + self.authority_vacated
        )


#: Report field name per reason, so the tally cannot silently drop a category when a reason is added.
_AUTHORITY_COUNTERS: dict[IntegrityHoldReason, str] = {
    IntegrityHoldReason.MULTIPLE_APPROVED_CHAPTER_PACKETS: "multiple_approved",
    IntegrityHoldReason.SUPERSESSION_SUCCESSOR_MISSING: "supersession_successor_missing",
    IntegrityHoldReason.APPROVED_AMENDMENT_WITHOUT_PREDECESSOR: "amendment_without_predecessor",
    IntegrityHoldReason.SUPERSEDED_PACKET_HAS_LIVE_CHILDREN: "superseded_with_live_children",
    IntegrityHoldReason.CHAPTER_AUTHORITY_VACATED: "authority_vacated",
}


def authority_hold_dedup_key(chapter_id: uuid.UUID, reason: IntegrityHoldReason, subjects: tuple[str, ...]) -> str:
    """Deterministic dedup key: one event per unresolved CONDITION SNAPSHOT, not per boot.

    Mirrors `hold_dedup_key` exactly, including hashing the hold code into the digest so the two sweeps'
    keys can never collide in the shared `integrity_hold` Activity kind. `subjects` is SORTED before
    hashing so a set of offending rows keys the same regardless of the read order that produced it —
    otherwise a plan change in Postgres would silently re-report an unchanged condition.
    """
    material = f"{AUTHORITY_HOLD_CODE}|{chapter_id}|{reason.value}|{'|'.join(sorted(subjects))}"
    return hashlib.sha256(material.encode()).hexdigest()


async def chapter_authority_findings(session: AsyncSession, *, chapter_id: uuid.UUID) -> list[AuthorityFinding]:
    """Evaluate all five authority states for ONE chapter. Reads only, writes nothing.

    This is the AUTHORITATIVE evaluation — the boot loop calls it a second time under the chapter lock
    rather than trusting the lock-free locator pass (chapter_lock protocol step 1). It is also the unit
    the tests drive, because a category is proven by the predicate that detects it, not by the loop.

    The five predicates, each with the constraint that should make it unreachable:

      1. MULTIPLE_APPROVED_CHAPTER_PACKETS
           COUNT(*) FILTER (WHERE status = 'approved') > 1
         `uq_chapter_packets_active_chapter` (partial unique on chapter_id WHERE status='approved').
         Checked anyway because a partial index can be dropped by hand, and this is precisely the
         split-brain that makes `draft_readiness.py`'s no-ORDER-BY approved-packet query resolve
         arbitrarily — two boots could disagree about which contract governs the same chapter.

      2. SUPERSESSION_SUCCESSOR_MISSING
           status = 'superseded'
             AND (superseded_by_packet_id IS NULL
                  OR NOT EXISTS (SELECT 1 FROM chapter_packets q WHERE q.id = superseded_by_packet_id))
         The NULL half is `ck_chapter_packets_superseded_names_successor`. The DANGLING half is guarded by
         NOTHING: `migrations.py:399-406` deliberately declines a self-referential FK on the lineage
         columns (a whole-chapter contract delete would make per-row delete order load-bearing) and names
         this sweep as the compensating control. So the second disjunct is the reachable one.

      3. APPROVED_AMENDMENT_WITHOUT_PREDECESSOR
           status = 'approved' AND origin_mode = 'amendment' AND supersedes_packet_id IS NULL
         `ck_chapter_packets_amendment_names_predecessor`.

      4. SUPERSEDED_PACKET_HAS_LIVE_CHILDREN
           p.status = 'superseded'
             AND EXISTS (SELECT 1 FROM scene_packets sp
                          WHERE sp.chapter_packet_id = p.id AND sp.status = 'approved')
         No constraint at all — this is invariant 3's third clause ("never a superseded packet with
         authoritative live children"), upheld by `_stale_children_of` at supersession time and therefore
         genuinely reachable afterwards: any route that approves a ScenePacket bound to the retired packet
         re-creates it. The one case here a healthy system can actually produce.

      5. CHAPTER_AUTHORITY_VACATED
           COUNT(*) FILTER (WHERE status = 'approved') = 0
             AND COUNT(*) FILTER (WHERE status = 'superseded') > 0
         No single-row constraint can express it. Reachable in combination — e.g. a supersession whose
         named successor exists but was never promoted past `proposed`, which satisfies every CHECK while
         leaving the chapter governed by nothing.
    """
    rows = (
        await session.execute(
            select(
                ChapterPacket.id,
                ChapterPacket.status,
                ChapterPacket.origin_mode,
                ChapterPacket.supersedes_packet_id,
                ChapterPacket.superseded_by_packet_id,
            )
            .where(ChapterPacket.chapter_id == chapter_id)
            .order_by(ChapterPacket.created_at, ChapterPacket.id)
        )
    ).all()

    approved = [r for r in rows if str(r.status) == PacketStatus.APPROVED.value]
    superseded = [r for r in rows if str(r.status) == PacketStatus.SUPERSEDED.value]
    findings: list[AuthorityFinding] = []

    # (1) The split-brain. Subjects are ALL the approved ids, so the finding re-reports if the set changes
    # (a third packet appears, or one is resolved away) but stays one event while it persists unchanged.
    if len(approved) > 1:
        ids = tuple(r.id for r in approved)
        findings.append(
            AuthorityFinding(
                chapter_id=chapter_id,
                reason=IntegrityHoldReason.MULTIPLE_APPROVED_CHAPTER_PACKETS,
                packet_ids=ids,
                dedup_subjects=tuple(str(x) for x in ids),
                evidence={"approved_packet_ids": [str(x) for x in ids], "approved_count": len(ids)},
            )
        )

    # (5) Authority vacated. Evaluated from the same aggregate so the two cannot disagree about the chapter.
    if not approved and superseded:
        ids = tuple(r.id for r in superseded)
        findings.append(
            AuthorityFinding(
                chapter_id=chapter_id,
                reason=IntegrityHoldReason.CHAPTER_AUTHORITY_VACATED,
                packet_ids=ids,
                dedup_subjects=tuple(str(x) for x in ids),
                evidence={"superseded_packet_ids": [str(x) for x in ids], "approved_count": 0},
            )
        )

    # (2) Existence of every NAMED successor, resolved GLOBALLY rather than within this chapter: a
    # successor that landed under a different chapter_id is still a broken lineage, and scoping the lookup
    # to this chapter would misreport it as "does not exist" for the wrong reason.
    named = {r.superseded_by_packet_id for r in superseded if r.superseded_by_packet_id is not None}
    live_ids: set[uuid.UUID] = set()
    if named:
        live_ids = set(
            (await session.execute(select(ChapterPacket.id).where(ChapterPacket.id.in_(named)))).scalars().all()
        )
    for r in superseded:
        successor = r.superseded_by_packet_id
        if successor is not None and successor in live_ids:
            continue
        findings.append(
            AuthorityFinding(
                chapter_id=chapter_id,
                reason=IntegrityHoldReason.SUPERSESSION_SUCCESSOR_MISSING,
                packet_ids=(r.id,),
                # The named id is part of the identity: a NULL link repaired into a DANGLING one is a
                # different diagnostic state and earns its own event.
                dedup_subjects=(str(r.id), f"successor={successor}"),
                evidence={
                    "packet_id": str(r.id),
                    "superseded_by_packet_id": str(successor) if successor else None,
                    "successor_exists": False,
                },
            )
        )

    # (3) An approved amendment that superseded nothing.
    for r in approved:
        if str(r.origin_mode) != ImportAdoptionMode.AMENDMENT.value or r.supersedes_packet_id is not None:
            continue
        findings.append(
            AuthorityFinding(
                chapter_id=chapter_id,
                reason=IntegrityHoldReason.APPROVED_AMENDMENT_WITHOUT_PREDECESSOR,
                packet_ids=(r.id,),
                dedup_subjects=(str(r.id),),
                evidence={"packet_id": str(r.id), "origin_mode": str(r.origin_mode)},
            )
        )

    # (4) Live authoritative children of a retired contract. One query for every superseded packet, then
    # one finding per PARENT — an operator resolves this per retired contract, not per orphaned child.
    if superseded:
        child_rows = (
            await session.execute(
                select(ScenePacket.chapter_packet_id, ScenePacket.id, ScenePacket.scene_no)
                .where(
                    ScenePacket.chapter_packet_id.in_([r.id for r in superseded]),
                    ScenePacket.status == ScenePacketStatus.APPROVED,
                )
                .order_by(ScenePacket.scene_no, ScenePacket.id)
            )
        ).all()
        by_parent: dict[uuid.UUID, list[tuple[uuid.UUID, int]]] = {}
        for parent_id, sp_id, scene_no in child_rows:
            by_parent.setdefault(parent_id, []).append((sp_id, scene_no))
        for r in superseded:
            children = by_parent.get(r.id)
            if not children:
                continue
            findings.append(
                AuthorityFinding(
                    chapter_id=chapter_id,
                    reason=IntegrityHoldReason.SUPERSEDED_PACKET_HAS_LIVE_CHILDREN,
                    packet_ids=(r.id,),
                    # The child set is part of the identity: staling three of four children is genuine
                    # progress, and the remaining one is a new snapshot worth its own event.
                    dedup_subjects=(str(r.id), *(str(sp_id) for sp_id, _no in children)),
                    evidence={
                        "packet_id": str(r.id),
                        "live_scene_packet_ids": [str(sp_id) for sp_id, _no in children],
                        "live_scene_nos": [no for _sp, no in children],
                    },
                )
            )

    return findings


async def _authority_candidate_chapter_ids(session: AsyncSession) -> list[uuid.UUID]:
    """Locate candidate chapters only — decide nothing from this read (chapter_lock protocol step 1).

    Four cheap lock-free reads whose union is a SUPERSET of the broken chapters; `chapter_authority_findings`
    re-derives the actual verdict under the lock. A superset is the safe direction: a chapter that healed
    between locate and lock simply yields no findings and is not counted.
    """
    per_chapter = (
        await session.execute(
            select(
                ChapterPacket.chapter_id,
                func.count().filter(ChapterPacket.status == PacketStatus.APPROVED).label("approved_n"),
                func.count().filter(ChapterPacket.status == PacketStatus.SUPERSEDED).label("superseded_n"),
            ).group_by(ChapterPacket.chapter_id)
        )
    ).all()
    found: set[uuid.UUID] = {
        chapter_id
        for chapter_id, approved_n, superseded_n in per_chapter
        if approved_n > 1 or (approved_n == 0 and superseded_n > 0)
    }

    # Lineage: a superseded packet whose successor link is NULL or dangling. The dangling test is an
    # anti-join against the same table (there is no FK to lean on).
    successor = ChapterPacket.__table__.alias("successor")
    found.update(
        (
            await session.execute(
                select(ChapterPacket.chapter_id)
                .where(
                    ChapterPacket.status == PacketStatus.SUPERSEDED,
                    ChapterPacket.superseded_by_packet_id.is_(None)
                    | ~select(successor.c.id).where(successor.c.id == ChapterPacket.superseded_by_packet_id).exists(),
                )
                .distinct()
            )
        )
        .scalars()
        .all()
    )

    found.update(
        (
            await session.execute(
                select(ChapterPacket.chapter_id)
                .where(
                    ChapterPacket.status == PacketStatus.APPROVED,
                    ChapterPacket.origin_mode == ImportAdoptionMode.AMENDMENT,
                    ChapterPacket.supersedes_packet_id.is_(None),
                )
                .distinct()
            )
        )
        .scalars()
        .all()
    )

    found.update(
        (
            await session.execute(
                select(ChapterPacket.chapter_id)
                .where(
                    ChapterPacket.status == PacketStatus.SUPERSEDED,
                    select(ScenePacket.id)
                    .where(
                        ScenePacket.chapter_packet_id == ChapterPacket.id,
                        ScenePacket.status == ScenePacketStatus.APPROVED,
                    )
                    .exists(),
                )
                .distinct()
            )
        )
        .scalars()
        .all()
    )
    # Sorted for a stable, reproducible pass order (and therefore a reproducible cap).
    return sorted(found)


async def _record_authority_hold_locked(session: AsyncSession, *, finding: AuthorityFinding, chapter: Chapter) -> bool:
    """Project one authority finding onto `Activity`. Existence-check and insert in the SAME
    chapter-locked transaction, so two boots cannot both append. `record_activity`, NOT
    `safe_record_activity`: a swallowed integrity hold is worse than a failed boot step, because the
    operator would never learn the chapter needs attention. Returns True if a new event was appended,
    False if this exact snapshot was already reported.

    Writes nothing but the Activity row — no ChapterPacket, no ScenePacket. That is the contract.
    """
    dedup_key = finding.dedup_key
    if await _hold_already_recorded(session, dedup_key):
        return False
    await record_activity(
        session,
        kind="integrity_hold",
        title=_AUTHORITY_TITLES[finding.reason],
        source="reconciliation",
        severity="error",  # error, not warn: an impossible state means a constraint was bypassed
        book_id=chapter.book_id,
        chapter_id=chapter.id,
        detail=_AUTHORITY_DETAILS[finding.reason],
        payload={
            "hold_code": AUTHORITY_HOLD_CODE,
            "reason_code": finding.reason.value,
            "chapter_id": str(finding.chapter_id),
            "packet_ids": [str(x) for x in finding.packet_ids],
            "evidence": finding.evidence,
            "repaired": False,  # stated explicitly: this sweep never changes a packet row
            "dedup_key": dedup_key,
        },
    )
    return True


async def _verify_one_chapter(session: AsyncSession, *, chapter_id: uuid.UUID, report: dict[str, int]) -> None:
    """One candidate chapter, one locked transaction. Mutates `report` counters in place.

    The lock is held for a READ-ONLY verification, which is not the usual reason to take it. Two reasons it
    is still right: the findings must be derived from a state no concurrent approve+supersede is halfway
    through (an unlocked read could observe the predecessor demoted before the successor is promoted and
    report a phantom CHAPTER_AUTHORITY_VACATED), and the dedup existence-check plus the insert must be
    atomic against a second boot. Only chapters the locator already flagged pay the cost.
    """

    async def _body() -> None:
        chapter = await session.get(Chapter, chapter_id)
        if chapter is None:
            report["skipped"] += 1  # raced: the chapter was deleted between locate and lock
            return
        for finding in await chapter_authority_findings(session, chapter_id=chapter_id):
            report[_AUTHORITY_COUNTERS[finding.reason]] += 1
            if await _record_authority_hold_locked(session, finding=finding, chapter=chapter):
                report["holds_recorded"] += 1
            else:
                report["holds_deduped"] += 1
            log.error(
                "chapter_packet_authority_violation",
                chapter_id=str(chapter_id),
                hold_code=AUTHORITY_HOLD_CODE,
                reason_code=finding.reason.value,
                packet_ids=[str(x) for x in finding.packet_ids],
                evidence=finding.evidence,
                repaired=False,
            )

    await run_under_chapter_workflow(session, chapter_id, _body, timeout_ms=LOCK_TIMEOUT_MS)


async def reconcile_chapter_packet_authority(
    session_factory: async_sessionmaker[AsyncSession] = SessionFactory,
) -> ChapterAuthorityReport:
    """VERIFY the chapter-packet authority lineage and durably report every impossible state. Repairs
    nothing (see the section header above for why, and `chapter_authority_findings` for the five
    predicates and the constraint that should make each unreachable).

    Idempotent and read-mostly: the only rows it can create are `Activity` integrity holds, and those
    dedup on (hold_code, chapter_id, reason, offending-row snapshot), so a condition that persists across a
    hundred boots produces exactly one Desk event — while a condition that CHANGES produces a new one,
    because the change is itself diagnostic.

    Commits per chapter, never in bulk, and one FRESH SESSION per chapter for the reason
    `reconcile_legacy_revision_intent` spells out: a failure while ACQUIRING the lock happens outside
    `run_under_chapter_workflow`'s try/rollback, so on a shared session it would leave an aborted
    transaction that fails every remaining chapter — one poison chapter silently degrading the whole pass.
    A `ChapterWorkflowBusy` (bounded by `LOCK_TIMEOUT_MS`) is counted as `skipped` and re-evaluated on the
    next boot; nothing here is allowed to fail the boot.
    """
    report = {name: 0 for name in _AUTHORITY_COUNTERS.values()}
    report.update({"holds_recorded": 0, "holds_deduped": 0, "skipped": 0})

    async with session_factory() as scan_session:
        candidates = await _authority_candidate_chapter_ids(scan_session)

    deferred = 0
    if len(candidates) > MAX_CANDIDATES_PER_BOOT:
        deferred = len(candidates) - MAX_CANDIDATES_PER_BOOT
        candidates = candidates[:MAX_CANDIDATES_PER_BOOT]
        log.error(
            "chapter_packet_authority_capped",
            cap=MAX_CANDIDATES_PER_BOOT,
            deferred=deferred,
            note="flagged-chapter backlog exceeds one boot's budget; the remainder is verified next boot",
        )

    for chapter_id in candidates:
        try:
            async with session_factory() as session:
                await _verify_one_chapter(session, chapter_id=chapter_id, report=report)
        except ChapterWorkflowBusy:
            report["skipped"] += 1
            log.info("chapter_packet_authority_skipped", chapter_id=str(chapter_id), reason="ChapterWorkflowBusy")
        except Exception as exc:  # noqa: BLE001 — one poison chapter must never fail the boot
            report["skipped"] += 1
            log.error("chapter_packet_authority_error", chapter_id=str(chapter_id), error=str(exc))

    result = ChapterAuthorityReport(scanned_chapters=len(candidates), deferred=deferred, **report)
    if candidates:
        log.info(
            "chapter_packet_authority_pass",
            scanned_chapters=result.scanned_chapters,
            findings=result.findings_total,
            holds_recorded=result.holds_recorded,
            holds_deduped=result.holds_deduped,
            skipped=result.skipped,
            deferred=result.deferred,
        )
    return result
