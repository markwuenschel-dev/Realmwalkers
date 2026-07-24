"""ADR-0032 D1-D4: the ONE adoption-entry writer.

Every caller that mints or promotes an ImportAdoption's ACTIVE lifecycle (`awaiting_start`/`queued`/
`running`) routes through this adoption-owned primitive, supplying an *operation* — never implementing
transitions itself. The primitive owns: the chapter reload under the held workflow lock, the evidence-only
+ operation-specific eligibility envelope, the (existing-state x intent) transition table, the monotonic
liveness merge, the source fingerprint, force-token/lineage attachment, and the D3 collision recovery
against `uq_import_adoptions_active_chapter`.

HTTP-agnostic by design — it is reused by the revision coordinator (W2/W3) and boot reconciliation (W4),
neither of which is HTTP — so it raises DOMAIN errors that each caller maps to its own transport.

Telemetry split (correction: no false evidence on rollback):
  * `ensure_import_adoption_locked` ASSUMES `run_under_chapter_workflow` is held, NEVER commits, and emits
    NO success telemetry. A committed lifecycle movement is only real after the outer commit, so the
    committing wrapper/coordinator emits `adoption_entry_transition` POST-COMMIT.
  * A `uq_import_adoptions_active_chapter` collision is attempted-corruption pressure, not a verified
    transition, so `adoption_active_invariant_collision` is emitted IMMEDIATELY at high severity — even if
    the surrounding transaction later rolls back.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import structlog
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from dominion.shared.chapter_lock import DEFAULT_LOCK_TIMEOUT_MS, run_under_chapter_workflow
from dominion.shared.enums import (
    AdoptionOperation,
    EntryEffect,
    EntryIntent,
    ImportAdoptionMode,
    ImportAdoptionStatus,
    LivenessBasis,
    PacketStatus,
    SceneStatus,
)
from dominion.shared.models import Chapter, ChapterPacket, ImportAdoption, Scene
from dominion.shared.prose_fingerprint import chapter_source_fingerprint

log = structlog.get_logger()

# The active-adoption states the "<=1 per chapter" partial-unique index (W0) covers. Because the DB
# guarantees at most one row here per chapter, a single lookup returns THE active row (if any).
_ACTIVE_INDEX_STATUSES = (
    ImportAdoptionStatus.AWAITING_START.value,
    ImportAdoptionStatus.QUEUED.value,
    ImportAdoptionStatus.RUNNING.value,
)
_UQ_ACTIVE_CHAPTER = "uq_import_adoptions_active_chapter"


# --------------------------------------------------------------------------- domain errors


class AdoptionEntryError(Exception):
    """Base for adoption-entry domain refusals (an ineligible chapter state). Transport-agnostic — HTTP
    callers map subclasses to 404/409; workers/reconciliation handle them in their own terms."""


class AdoptionChapterNotFound(AdoptionEntryError):
    def __init__(self, chapter_id: uuid.UUID) -> None:
        self.chapter_id = chapter_id
        super().__init__(f"chapter {chapter_id} not found")


class ChapterHasContractedScenes(AdoptionEntryError):
    def __init__(self, chapter_id: uuid.UUID) -> None:
        self.chapter_id = chapter_id
        super().__init__(f"chapter {chapter_id} has contracted scenes; not evidence-only")


class ChapterContractAlreadyApproved(AdoptionEntryError):
    def __init__(self, chapter_id: uuid.UUID) -> None:
        self.chapter_id = chapter_id
        super().__init__(f"chapter {chapter_id} already has an approved contract")


class IncompatibleAdoptionEntry(AdoptionEntryError):
    """Fail-closed: the requested operation, or a reconciled collision winner, is in a state the seam
    will not silently guess through (unwired operation, force-token conflict, or a vanished winner). Never
    pick a survivor by inference (D3)."""


# --------------------------------------------------------------------------- operation policy (D1 table)


@dataclass(frozen=True)
class _Policy:
    """Per-operation contract (ADR-0032 D1 caller table) — the single source of truth so a caller cannot
    supply an inconsistent (operation, intent, liveness) combination."""

    entry_intent: EntryIntent
    liveness_basis: LivenessBasis
    requires_evidence_only: bool
    refuses_approved_packet: bool


_POLICY: dict[AdoptionOperation, _Policy] = {
    AdoptionOperation.OPERATOR_START: _Policy(
        EntryIntent.SPEND,
        LivenessBasis.OPERATOR_INDEPENDENT,
        requires_evidence_only=True,
        refuses_approved_packet=False,
    ),
    AdoptionOperation.REAUTHOR: _Policy(
        EntryIntent.SPEND, LivenessBasis.OPERATOR_INDEPENDENT, requires_evidence_only=True, refuses_approved_packet=True
    ),
    # REVISION (sync auto-start, W3) and RECONCILIATION (boot, W4) are wired in their waves; an operation
    # absent from this table fails closed in the seam.
}


@dataclass
class AdoptionEntryResult:
    """What the seam did, plus the facts a committing caller needs to emit `adoption_entry_transition`
    POST-COMMIT. The seam itself emits no success telemetry."""

    adoption: ImportAdoption
    effect: EntryEffect
    from_status: str | None  # None when created
    to_status: str
    entry_intent: EntryIntent
    liveness_basis: str
    trigger: str  # == operation.value (D12 trigger vocabulary)
    collided: bool = False


# --------------------------------------------------------------------------- adoption-domain queries


async def _contracted_scene_count(session: AsyncSession, chapter_id: uuid.UUID) -> int:
    """How many of the chapter's non-superseded scenes are CONTRACTED (bound to an approved ScenePacket of
    record). A nonzero count means the chapter is not evidence-only and adoption must refuse (Q6)."""
    return (
        await session.execute(
            select(func.count())
            .select_from(Scene)
            .where(
                Scene.chapter_id == chapter_id,
                Scene.status != SceneStatus.SUPERSEDED,
                Scene.scene_packet_id.is_not(None),
            )
        )
    ).scalar_one()


async def _source_fingerprint(session: AsyncSession, chapter_id: uuid.UUID) -> str:
    """The chapter's current prose-hash source fingerprint over its non-superseded scenes (Q10). Set at
    creation so the NOT-NULL column is populated; the worker re-captures it in its leased claim txn."""
    rows = (
        await session.execute(
            select(Scene.scene_no, Scene.id, Scene.version, Scene.prose).where(
                Scene.chapter_id == chapter_id, Scene.status != SceneStatus.SUPERSEDED
            )
        )
    ).all()
    return chapter_source_fingerprint((int(r[0]), r[1], int(r[2]), r[3]) for r in rows)


async def _existing_adoption(
    session: AsyncSession, chapter_id: uuid.UUID, statuses: tuple[str, ...]
) -> ImportAdoption | None:
    """The most recent adoption for this chapter in one of `statuses`, if any (newest first)."""
    return (
        await session.execute(
            select(ImportAdoption)
            .where(ImportAdoption.chapter_id == chapter_id, ImportAdoption.status.in_(statuses))
            .order_by(ImportAdoption.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _adoption_by_force_token(session: AsyncSession, token: uuid.UUID) -> ImportAdoption | None:
    """The adoption already created for this operator Re-author token, if any (the idempotency-key lookup).
    The token is globally unique (a partial UNIQUE index enforces it), so this is not chapter-scoped."""
    return (
        await session.execute(select(ImportAdoption).where(ImportAdoption.force_author_token == token).limit(1))
    ).scalar_one_or_none()


async def _has_approved_chapter_packet(session: AsyncSession, chapter_id: uuid.UUID) -> bool:
    """Whether the chapter already carries an APPROVED ChapterPacket (an amendment/revision concern the
    Re-author operation refuses — Q11 tier-C)."""
    return (
        await session.execute(
            select(func.count())
            .select_from(ChapterPacket)
            .where(ChapterPacket.chapter_id == chapter_id, ChapterPacket.status == PacketStatus.APPROVED.value)
        )
    ).scalar_one() > 0


# --------------------------------------------------------------------------- pure helpers


def _merge_liveness(current: str, incoming: LivenessBasis) -> tuple[str, bool]:
    """Monotonic liveness merge (D2): `operator_independent` never downgrades. Returns (merged, changed)."""
    if current == LivenessBasis.OPERATOR_INDEPENDENT.value:
        return current, False
    if incoming is LivenessBasis.OPERATOR_INDEPENDENT:
        return LivenessBasis.OPERATOR_INDEPENDENT.value, True
    return current, False  # request_bound + request_bound


def _is_active_chapter_collision(exc: IntegrityError) -> bool:
    """True iff `exc` is a unique violation PROVEN to name `uq_import_adoptions_active_chapter`. The
    force-token uniqueness index and every unrelated integrity error must return False and propagate."""
    orig = getattr(exc, "orig", None)
    for candidate in (orig, getattr(orig, "__cause__", None)):
        name = getattr(candidate, "constraint_name", None)
        if name is not None:
            return name == _UQ_ACTIVE_CHAPTER
    return _UQ_ACTIVE_CHAPTER in str(orig if orig is not None else exc)


def _reconcile_existing(
    existing: ImportAdoption,
    policy: _Policy,
    operation: AdoptionOperation,
) -> AdoptionEntryResult:
    """Reuse the chapter's single active row, applying ONLY monotonic, non-regressing mutations (D2/D3):
    promote `awaiting_start`→`queued` under SPEND, and upgrade liveness monotonically. It NEVER attaches a
    new force token, lineage, or mode to an already-active row — a second operator command (e.g. a Re-author
    with a different token) SERIALIZES to the in-flight run rather than spending again or mutating immutable
    identity. Used for both the ordinary active-reuse path and the collision-winner reconcile."""
    from_status = existing.status
    changed = False
    if existing.status == ImportAdoptionStatus.AWAITING_START.value and policy.entry_intent is EntryIntent.SPEND:
        existing.status = ImportAdoptionStatus.QUEUED.value
        existing.error = None
        changed = True
    merged_basis, basis_changed = _merge_liveness(existing.liveness_basis, policy.liveness_basis)
    if basis_changed:
        existing.liveness_basis = merged_basis
        changed = True

    return AdoptionEntryResult(
        adoption=existing,
        effect=EntryEffect.PROMOTED if changed else EntryEffect.UNCHANGED,
        from_status=from_status,
        to_status=existing.status,
        entry_intent=policy.entry_intent,
        liveness_basis=existing.liveness_basis,
        trigger=operation.value,
    )


# --------------------------------------------------------------------------- the one writer


async def ensure_import_adoption_locked(
    session: AsyncSession,
    *,
    chapter_id: uuid.UUID,
    operation: AdoptionOperation,
    mode: ImportAdoptionMode = ImportAdoptionMode.INITIAL,
    force_author_token: uuid.UUID | None = None,
    reauthor_of: uuid.UUID | None = None,
) -> AdoptionEntryResult:
    """The single adoption-entry writer. ASSUMES `run_under_chapter_workflow` is held; NEVER commits;
    emits NO success telemetry (its committing caller does, POST-COMMIT). Raises `AdoptionEntryError`
    subclasses on ineligibility. `entry_intent` and `liveness_basis` are derived from `operation` via the
    D1 policy table, so an inconsistent combination is unrepresentable."""
    policy = _POLICY.get(operation)
    if policy is None:
        raise IncompatibleAdoptionEntry(f"adoption operation {operation!r} is not wired for entry")

    # 1) Reload the chapter under the held lock — the mutation precondition (correction 1). A caller-side
    #    lookup cannot replace this under-lock reload.
    chapter = await session.get(Chapter, chapter_id)
    if chapter is None:
        raise AdoptionChapterNotFound(chapter_id)

    # 2) Eligibility envelope, keyed by the operation policy (correction 2 — never inferred from the token).
    if policy.requires_evidence_only and await _contracted_scene_count(session, chapter_id) > 0:
        raise ChapterHasContractedScenes(chapter_id)
    if policy.refuses_approved_packet and await _has_approved_chapter_packet(session, chapter_id):
        raise ChapterContractAlreadyApproved(chapter_id)

    # 3) Force-token idempotency (REAUTHOR): a token that already spent returns its row, inert.
    if force_author_token is not None:
        prior_token_row = await _adoption_by_force_token(session, force_author_token)
        if prior_token_row is not None:
            return AdoptionEntryResult(
                adoption=prior_token_row,
                effect=EntryEffect.UNCHANGED,
                from_status=prior_token_row.status,
                to_status=prior_token_row.status,
                entry_intent=policy.entry_intent,
                liveness_basis=prior_token_row.liveness_basis,
                trigger=operation.value,
            )

    # 4) The chapter's single active row (DB guarantees <=1 in the active states) — reuse/promote it.
    existing = await _existing_adoption(session, chapter_id, _ACTIVE_INDEX_STATUSES)
    if existing is not None:
        return _reconcile_existing(existing, policy, operation)

    # 5) Construct, guarded against a concurrent active insert (D3 collision recovery).
    return await _create_locked(session, chapter, policy, operation, mode, force_author_token, reauthor_of)


async def _create_locked(
    session: AsyncSession,
    chapter: Chapter,
    policy: _Policy,
    operation: AdoptionOperation,
    mode: ImportAdoptionMode,
    force_author_token: uuid.UUID | None,
    reauthor_of: uuid.UUID | None,
) -> AdoptionEntryResult:
    chapter_id = chapter.id
    fingerprint = await _source_fingerprint(session, chapter_id)
    # REAUTHOR lineage: link the prior proposed contract this supersedes (or NULL) when unspecified.
    if operation is AdoptionOperation.REAUTHOR and reauthor_of is None:
        prior = await _existing_adoption(session, chapter_id, (ImportAdoptionStatus.CONTRACT_PROPOSED.value,))
        reauthor_of = prior.id if prior is not None else None

    to_status = (
        ImportAdoptionStatus.QUEUED.value
        if policy.entry_intent is EntryIntent.SPEND
        else ImportAdoptionStatus.AWAITING_START.value
    )
    adoption = ImportAdoption(
        book_id=chapter.book_id,
        chapter_id=chapter_id,
        mode=mode.value,
        status=to_status,
        liveness_basis=policy.liveness_basis.value,
        source_fingerprint=fingerprint,
        force_author_token=force_author_token,
        reauthor_of_adoption_id=reauthor_of,
    )
    try:
        # The nested transaction (SAVEPOINT) encloses ONLY the insert + flush, so a collision rolls back
        # just this attempt, never the caller's outer transaction.
        async with session.begin_nested():
            session.add(adoption)
            await session.flush()
    except IntegrityError as exc:
        if not _is_active_chapter_collision(exc):
            raise  # force-token uniqueness and every unrelated integrity error propagate untouched
        # D3: a canonical caller under the lock should almost never reach this — a collision signals an
        # architectural bypass. Emit IMMEDIATELY at high severity (survives the savepoint rollback), then
        # reconcile against the winner rather than guessing.
        log.warning(
            "adoption_active_invariant_collision",
            chapter_id=str(chapter_id),
            operation=operation.value,
            attempted_intent=policy.entry_intent.value,
            attempted_mode=mode.value,
            attempted_basis=policy.liveness_basis.value,
            constraint=_UQ_ACTIVE_CHAPTER,
        )
        winner = await _existing_adoption(session, chapter_id, _ACTIVE_INDEX_STATUSES)
        if winner is None:
            # The colliding row went terminal between our failed insert and this reload — do NOT loop or
            # retry; fail closed (D3).
            raise IncompatibleAdoptionEntry(
                f"chapter {chapter_id} active-adoption collision but no active winner on reload"
            ) from exc
        # Fail closed on an incompatible contract type (D3): serving/promoting a winner of a DIFFERENT mode
        # would silently satisfy the wrong contract. (Force-token/lineage differences are NOT incompatible —
        # like the ordinary serialize path, the caller joins the authoritative winner and its own token
        # simply does not spend; only status + liveness are merged, both monotonically.)
        if winner.mode != mode.value:
            raise IncompatibleAdoptionEntry(
                f"chapter {chapter_id} active-adoption collision winner has mode {winner.mode!r}, "
                f"incompatible with requested {mode.value!r}"
            ) from exc
        result = _reconcile_existing(winner, policy, operation)
        result.collided = True
        return result

    return AdoptionEntryResult(
        adoption=adoption,
        effect=EntryEffect.CREATED,
        from_status=None,
        to_status=to_status,
        entry_intent=policy.entry_intent,
        liveness_basis=adoption.liveness_basis,
        trigger=operation.value,
    )


async def ensure_import_adoption(
    session: AsyncSession,
    *,
    chapter_id: uuid.UUID,
    operation: AdoptionOperation,
    mode: ImportAdoptionMode = ImportAdoptionMode.INITIAL,
    force_author_token: uuid.UUID | None = None,
    reauthor_of: uuid.UUID | None = None,
    timeout_ms: int | None = DEFAULT_LOCK_TIMEOUT_MS,
) -> AdoptionEntryResult:
    """Standalone wrapper for non-coordinator callers (operator Start/Re-author now; boot reconciliation in
    W4). Acquires `run_under_chapter_workflow`, runs the locked primitive, COMMITS, then emits the
    `adoption_entry_transition` telemetry POST-COMMIT — never for a rolled-back movement, and never for a
    completely inert reuse (D12). `ChapterWorkflowBusy` and `AdoptionEntryError` propagate to the caller."""

    async def _run() -> AdoptionEntryResult:
        return await ensure_import_adoption_locked(
            session,
            chapter_id=chapter_id,
            operation=operation,
            mode=mode,
            force_author_token=force_author_token,
            reauthor_of=reauthor_of,
        )

    result = await run_under_chapter_workflow(session, chapter_id, _run, timeout_ms=timeout_ms)

    if result.effect is not EntryEffect.UNCHANGED:
        log.info(
            "adoption_entry_transition",
            action=result.effect.value,
            trigger=result.trigger,
            entry_intent=result.entry_intent.value,
            from_status=result.from_status,
            to_status=result.to_status,
            liveness_basis=result.liveness_basis,
            adoption_id=str(result.adoption.id),
            chapter_id=str(chapter_id),
            collided=result.collided,
        )
    return result
