"""Decision endpoints (DESIGN §9). The human's verdict is the only gate.

On approve: record the verdict, commit the beat's declared deltas to the ledger, fold the approved
text into the rolling summaries, and (in pause_each) auto-enqueue the next scene. On revise: queue a
revision job that re-drafts against the feedback. The continuity panel resolves a mismatch by either
correcting the ledger (prose was right) or queuing a targeted prose fix (ledger was right).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import structlog
from fastapi import APIRouter, BackgroundTasks, HTTPException, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dominion.api.deps import SessionDep
from dominion.shared.adoption_entry import (
    AdoptionChapterNotFound,
    AdoptionEntryError,
    AdoptionEntryResult,
    ChapterContractAlreadyApproved,
    ChapterHasContractedScenes,
    ensure_import_adoption_locked,
    reconcile_adoption_demand_locked,
)
from dominion.shared.chapter_lock import (
    BUSY_DETAIL,
    DEFAULT_LOCK_TIMEOUT_MS,
    ChapterWorkflowBusy,
    run_under_chapter_workflow,
)
from dominion.shared.enums import (
    AdoptionOperation,
    Decision,
    EntryEffect,
    ForwardEffect,
    ReconcileDemandOutcome,
    RequestDisposition,
    RevisionRequestOrigin,
    RevisionRequestStatus,
    SceneStatus,
)
from dominion.shared.models import (
    Approval,
    Chapter,
    CharacterState,
    Critique,
    EditPair,
    RevisionRequest,
    Scene,
)
from dominion.shared.schemas import (
    ContinuityResolveIn,
    ContinuityResolveOut,
    DecisionIn,
    RevisionAcceptanceOut,
    RevisionRequestOut,
    SceneDecisionOut,
)
from dominion.workers import activity
from dominion.workers.job_scheduler import schedule_next_after_approval
from dominion.workers.memory import knowledge, ledger, summaries
from dominion.workers.revision import (
    _accept_revision_request_locked,
    _cancel_active_requests_for_scene_locked,
    prose_hash,
    revision_request_out,
)
from dominion.workers.stat_render import render_stat_blocks

log = structlog.get_logger()
router = APIRouter(prefix="/scenes", tags=["reviews"])


async def _refresh_summaries_bg(scene_id: uuid.UUID) -> None:
    """Fold the approved scene into the rolling summaries OFF the request path — that's two
    review-model calls (several seconds), so Approve can respond instantly. Advisory: a failure just
    leaves summaries briefly stale; the next approval folds again."""
    from dominion.shared.db import SessionFactory

    async with SessionFactory() as session:
        try:
            await summaries.refresh_on_approval(session, scene_id=scene_id)
            await session.commit()
        except Exception as exc:  # noqa: BLE001 — advisory; never surfaces to the user
            log.error("summary.refresh_failed", scene=str(scene_id), error=str(exc))


async def _capture_edit_pair(session: SessionDep, scene: Scene, human_text: str) -> None:
    """Snapshot the model's rendered draft next to the author's edit (LEARNING_FROM_EDITS Tier 1).

    `agent_text` is the RENDERED agent draft — we render the stored marker-form `agent_original` so the
    pair isn't noisy with ```stat``` markers (falling back to the current, pre-overwrite prose for older
    scenes with no `agent_original`). Upsert per `(scene, version)`: a re-edit refreshes only `human_text`,
    so we keep the true agent draft and never record a human→human diff. Advisory capture; never gates.
    """
    agent_text = render_stat_blocks(scene.agent_original) if scene.agent_original is not None else scene.prose
    pair = (
        await session.execute(select(EditPair).where(EditPair.scene_id == scene.id, EditPair.version == scene.version))
    ).scalar_one_or_none()
    if pair is None:
        pov = (await session.execute(select(Chapter.pov).where(Chapter.id == scene.chapter_id))).scalar_one_or_none()
        session.add(
            EditPair(
                scene_id=scene.id,
                version=scene.version,
                pov=pov,
                agent_text=agent_text,
                human_text=human_text,
            )
        )
    else:
        pair.human_text = human_text  # keep the original agent draft; only the human side moved


@dataclass(frozen=True)
class ApprovalResult:
    """What the chapter-locked scene-approval command committed (ADR-0032 W2). `reconcile` records the
    reverse-reconciliation outcome for the chapter's adoption (D9); `next_job`, `status`, and the response
    body are byte-identical to the pre-W2 APPROVE response."""

    scene_id: uuid.UUID
    status: str
    next_job: uuid.UUID | None
    first_approval: bool
    reconcile: ReconcileDemandOutcome


async def accept_scene_approval(
    session: AsyncSession,
    *,
    scene_id: uuid.UUID,
    edited_prose: str | None,
    target_pass: str | None,
    feedback: str | None,
) -> ApprovalResult:
    """The chapter-locked scene-APPROVAL command (ADR-0032 W2, D4/D9). ONE clean unit of work: acquire the
    per-chapter workflow lock FIRST (subsuming the old per-scene DECIDE-LOCK), reload the scene UNDER the
    lock, apply the approval + any inbox hand-edit, cancel the scene's active revision requests,
    reverse-reconcile the chapter's adoption demand, run the first-approval effects, record Activity, and
    commit — atomically, or not at all. Takes identifiers + immutable command data, NOT a preloaded mutable
    Scene (the reload happens under the lock). OWNS the commit boundary; callers must NOT commit around it.

    DENY/REVISE stay on the legacy decide path in W2; W3 moves the forward Revise path onto its own
    coordinator (`accept_revision_intent`). Reverse cancellation is DORMANT on current data (no request_bound
    adoption exists before W3's minter) — wired now so the defense lands before the first live minter (D13)."""
    # Locate the chapter only — make no decision from this read (chapter_lock protocol step 1).
    chapter_id = (await session.execute(select(Scene.chapter_id).where(Scene.id == scene_id))).scalar_one_or_none()
    if chapter_id is None:
        raise HTTPException(status_code=404, detail="scene not found")

    async def _body() -> ApprovalResult:
        scene = await session.get(Scene, scene_id)  # first materialization, UNDER the held lock
        if scene is None:  # raced delete between locate and lock
            raise HTTPException(status_code=404, detail="scene not found")

        # Re-approval must NOT re-run the one-shot side effects (relative ledger deltas would double-count;
        # auto-advance could re-enqueue). They fire only on the first pending -> approved cross.
        first_approval = scene.status != SceneStatus.APPROVED

        # Hand-edit becomes canonical; capture the agent→human pair BEFORE overwriting prose (DESIGN §9).
        if edited_prose is not None:
            await _capture_edit_pair(session, scene, edited_prose)
            scene.prose = edited_prose
            scene.prose_source = "agent+human_edit"

        session.add(
            Approval(
                scene_id=scene.id,
                version=scene.version,
                decision=Decision.APPROVE,
                target_pass=target_pass,
                feedback=feedback,
            )
        )
        scene.status = SceneStatus.APPROVED

        # Demand removal (D9): cancel the scene's active requests (revision-owned), then let the adoption
        # owner decide the consequence for the chapter's adoption — BOTH inside this one locked transaction,
        # so a failure in either rolls the whole approval back with them.
        await _cancel_active_requests_for_scene_locked(session, scene.id)
        reconcile = await reconcile_adoption_demand_locked(session, chapter_id)

        next_job: uuid.UUID | None = None
        if first_approval:
            await ledger.commit_declared_deltas(session, scene_id=scene.id)  # fast (DB) — keep inline
            try:  # record the scene's reveals into the knowledge ledger (advisory, never gates)
                await knowledge.record_scene_reveals(session, scene_id=scene.id)
            except Exception as exc:  # noqa: BLE001
                log.warning("knowledge.record_failed", scene=str(scene.id), error=str(exc))
            next_job = await schedule_next_after_approval(session, scene)

        chapter_row = (
            await session.execute(select(Chapter.book_id, Chapter.chapter_no).where(Chapter.id == scene.chapter_id))
        ).first()
        place = (
            f"Ch {chapter_row[1]} · " if chapter_row and chapter_row[1] is not None else ""
        ) + f"Scene {scene.scene_no}"
        await activity.safe_record_activity(
            session,
            kind="scene_decision",
            title=f"{place} approved",
            source="reviews",
            severity="success",
            book_id=chapter_row[0] if chapter_row else None,
            chapter_id=scene.chapter_id,
            job_id=next_job,
            payload={"decision": str(Decision.APPROVE)},
        )
        return ApprovalResult(
            scene_id=scene.id,
            status=str(scene.status),
            next_job=next_job,
            first_approval=first_approval,
            reconcile=reconcile,
        )

    return await run_under_chapter_workflow(session, chapter_id, _body)


@dataclass(frozen=True)
class RevisionAcceptance:
    """What the chapter-locked revise command committed (ADR-0032 W3, D11). Plain values + the two ORM
    rows the caller serializes; `adoption_entry` is carried out so the coordinator can emit the D12
    transition telemetry POST-COMMIT, never for a movement that rolled back."""

    request: RevisionRequest
    disposition: RequestDisposition
    forward_effect: ForwardEffect
    adoption_entry: AdoptionEntryResult | None


def _forward_effect(accepted_job_minted: bool, entry: AdoptionEntryResult | None, newly_linked: bool) -> ForwardEffect:
    """Derive D11's `forward_effect` — did this invocation move the chapter's work forward, and how?

    Exactly one applies: a contract-backed scene mints its Job and never reaches the adoption branch,
    so `revision_job_queued` and the `adoption_*` effects are mutually exclusive by construction. An
    adoption the seam left UNCHANGED still counts as `adoption_joined` when THIS request newly attached
    to it (D11's 'a second scene's request attaches to already-queued/running adoption'); if the link
    was already there, nothing moved and the honest answer is `none`."""
    if accepted_job_minted:
        return ForwardEffect.REVISION_JOB_QUEUED
    if entry is None:
        return ForwardEffect.NONE
    if entry.effect is EntryEffect.CREATED:
        return ForwardEffect.ADOPTION_CREATED
    if entry.effect is EntryEffect.PROMOTED:
        return ForwardEffect.ADOPTION_PROMOTED
    return ForwardEffect.ADOPTION_JOINED if newly_linked else ForwardEffect.NONE


async def accept_revision_intent(
    session: AsyncSession,
    *,
    scene_id: uuid.UUID,
    feedback: str | None,
    target_pass: str | None,
    expected_prose_hash: str | None,
    origin: RevisionRequestOrigin,
    edited_prose: str | None = None,
    resolves_critique_id: uuid.UUID | None = None,
    timeout_ms: int | None = DEFAULT_LOCK_TIMEOUT_MS,
) -> RevisionAcceptance:
    """The chapter-locked revise COMMAND coordinator (ADR-0032 W3, D4). It owns the per-chapter workflow
    lock, the transaction boundary, and the ORDER of two single-owner mutations — and nothing else:

        _accept_revision_request_locked  (revision-owned: the sole RevisionRequest writer)
        ensure_import_adoption_locked    (adoption-owned: the sole ImportAdoption writer)

    Neither owner touches the other's state and no endpoint sequences them independently, so there is one
    atomic outcome: the request and its adoption entry commit together, or neither persists (D14).

    Takes identifiers + immutable command data, NOT a preloaded mutable Scene — the reload happens under
    the lock, because classification compares the client's `expected_prose_hash` against prose that only
    the lock makes stable. OWNS the commit boundary; callers must NOT commit around it.

    Adoption entry runs only when the request remains `awaiting_contract` (an imported/uncontracted
    scene). A contract-backed scene mints its revise Job instead and needs no adoption. Per D5 this
    reconciliation happens on an exact REPLAY too — otherwise a reconciliation-restored request plus an
    `awaiting_start` adoption would leave a fresh explicit Revise click stuck behind operator Start.

    An ineligible chapter (contracted scenes, or an already-approved contract needing amendment mode,
    #261) raises `AdoptionEntryError`, which rolls the WHOLE command back — deliberately, per D14: an
    imported/uncontracted request must never commit without its adoption entry.
    """
    # Protocol step 1: locate the chapter only; decide nothing from this read.
    chapter_id = (await session.execute(select(Scene.chapter_id).where(Scene.id == scene_id))).scalar_one_or_none()
    if chapter_id is None:
        raise HTTPException(status_code=404, detail="scene not found")

    async def _body() -> RevisionAcceptance:
        # `populate_existing=True` is LOAD-BEARING, not defensive noise. A caller may already hold this
        # Scene in the session's identity map — `resolve_continuity` reads it to build its feedback
        # string — and a bare `session.get` would then return that PRE-LOCK instance with no SQL at all,
        # silently defeating the reload and classifying against prose a concurrent commit has moved.
        # Forcing the refresh here keeps the guarantee in the coordinator, where it cannot depend on
        # each caller remembering not to touch the row first.
        scene = await session.get(Scene, scene_id, populate_existing=True)
        if scene is None:  # raced delete between locate and lock
            raise HTTPException(status_code=404, detail="scene not found")

        # A hand-edit in the inbox becomes the canonical text BEFORE classification, so the prose hash
        # the taxonomy compares is the text the author is actually revising (DESIGN §9).
        if edited_prose is not None:
            await _capture_edit_pair(session, scene, edited_prose)
            scene.prose = edited_prose
            scene.prose_source = "agent+human_edit"

        accepted = await _accept_revision_request_locked(
            session,
            scene=scene,
            feedback=feedback,
            target_pass=target_pass,
            expected_prose_hash=expected_prose_hash,
            origin=origin,
        )
        request = accepted.request
        scene.status = SceneStatus.REVISION_REQUESTED

        # The diagnostic this intent supersedes, cleared in the SAME transaction (continuity resolve).
        # Ordering + atomicity is exactly the coordinator's job; it decides nothing about the critique.
        if resolves_critique_id is not None:
            critique = await session.get(Critique, resolves_critique_id)
            if critique is not None:
                await session.delete(critique)

        entry: AdoptionEntryResult | None = None
        newly_linked = False
        if request.status == RevisionRequestStatus.AWAITING_CONTRACT.value:
            entry = await ensure_import_adoption_locked(
                session, chapter_id=chapter_id, operation=AdoptionOperation.REVISION
            )
            newly_linked = request.import_adoption_id != entry.adoption.id
            request.import_adoption_id = entry.adoption.id  # serving/provenance link (adoption is chapter-shared)

        chapter_row = (
            await session.execute(select(Chapter.book_id, Chapter.chapter_no).where(Chapter.id == chapter_id))
        ).first()
        place = (
            f"Ch {chapter_row[1]} · " if chapter_row and chapter_row[1] is not None else ""
        ) + f"Scene {scene.scene_no}"
        await activity.safe_record_activity(
            session,
            kind="scene_decision",
            title=f"{place} sent for revision",
            source="reviews",
            severity="info",
            book_id=chapter_row[0] if chapter_row else None,
            chapter_id=chapter_id,
            job_id=request.job_id,
            payload={"decision": str(Decision.REVISE)},
        )
        return RevisionAcceptance(
            request=request,
            disposition=accepted.disposition,
            forward_effect=_forward_effect(accepted.job_minted, entry, newly_linked),
            adoption_entry=entry,
        )

    result = await run_under_chapter_workflow(session, chapter_id, _body, timeout_ms=timeout_ms)

    # POST-COMMIT (D12): a lifecycle movement is only real once it committed. The seam's own wrapper
    # emits this for Start/Re-author; the coordinator calls the `_locked` primitive, so it inherits the
    # obligation. Nothing is emitted for a completely inert reuse — the command response reports it.
    entry = result.adoption_entry
    if entry is not None and entry.effect is not EntryEffect.UNCHANGED:
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
            request_id=str(result.request.id),
            collided=entry.collided,
        )
    return result


async def _revision_acceptance_response(
    session: AsyncSession, accepted: RevisionAcceptance, response: Response
) -> RevisionAcceptanceOut:
    """Serialize an accepted revise command and set its HTTP status (D11): 200 iff the request replayed
    AND nothing moved forward; 202 for every other accepted result. `refresh` loads the server-side
    defaults (`created_at`) and the `updated_at` onupdate that the commit wrote, rather than lazy-loading
    them on an async session."""
    await session.refresh(accepted.request)
    inert = accepted.disposition is RequestDisposition.REPLAYED and accepted.forward_effect is ForwardEffect.NONE
    response.status_code = 200 if inert else 202
    return RevisionAcceptanceOut(
        request=revision_request_out(accepted.request),
        request_disposition=accepted.disposition,
        forward_effect=accepted.forward_effect,
    )


def _kick_adoption_drain(background: BackgroundTasks, accepted: RevisionAcceptance) -> None:
    """Hand a freshly-entered adoption to the worker that claims it.

    A REVISION entry always leaves the chapter's active adoption `queued` — SPEND creates `queued` or
    promotes `awaiting_start` to it — and `queued` IS the durable spend consent the adoption worker
    claims from. Without this kick the row is spend consent nothing ever acts on. `drain_adoptions` is
    single-flight per process, so a second scene joining the same adoption costs nothing. Imported
    lazily so the API process loads the adoption/LLM stack only when it actually has adoption work."""
    if accepted.adoption_entry is None:
        return
    from dominion.workers.import_adoption import drain_adoptions

    background.add_task(drain_adoptions)


def _revise_http_error(exc: Exception) -> HTTPException:
    """Map the adoption owner's DOMAIN refusals onto this transport (the seam is HTTP-agnostic). These
    mirror the operator Start/Re-author mappings in `routers/adoption.py`, because they are the same
    eligibility envelope reached through a different entry path."""
    if isinstance(exc, AdoptionChapterNotFound):
        return HTTPException(status_code=404, detail="chapter not found")
    if isinstance(exc, ChapterHasContractedScenes):
        return HTTPException(
            status_code=409,
            detail={
                "reason": "chapter_has_contracted_scenes",
                "message": (
                    "This chapter has at least one contracted scene, so it is not evidence-only. "
                    "Revising an imported scene here needs amendment mode, which is not available yet."
                ),
            },
        )
    if isinstance(exc, ChapterContractAlreadyApproved):
        return HTTPException(
            status_code=409,
            detail={
                "reason": "chapter_contract_already_approved",
                "message": (
                    "This chapter already has an approved contract. Revising an imported scene here "
                    "needs amendment mode, which is not available yet."
                ),
            },
        )
    return HTTPException(status_code=409, detail=BUSY_DETAIL)


@router.post("/{scene_id}/decision")
async def decide(
    scene_id: uuid.UUID, body: DecisionIn, session: SessionDep, background: BackgroundTasks, response: Response
) -> SceneDecisionOut | RevisionAcceptanceOut:
    # APPROVE and REVISE are both chapter-locked atomic commands (ADR-0032 D4): each acquires the
    # per-chapter workflow lock FIRST — subsuming the old per-scene DECIDE-LOCK — so each must be
    # dispatched BEFORE this handler takes any row lock or mutates prose. APPROVE removes demand (W2,
    # D9); REVISE creates it (W3, D1/D5). Only DENY remains on the legacy per-scene path below.
    if body.decision == Decision.APPROVE:
        result = await accept_scene_approval(
            session,
            scene_id=scene_id,
            edited_prose=body.edited_prose,
            target_pass=body.target_pass,
            feedback=body.feedback,
        )
        # Rolling-summary fold is two LLM calls — defer so the inbox responds instantly (post-commit; a
        # re-approval re-folds the edited text, which is correct and idempotent).
        background.add_task(_refresh_summaries_bg, scene_id)
        return SceneDecisionOut(scene=scene_id, status=result.status, next_job=result.next_job)

    if body.decision == Decision.REVISE:
        # The revise COMMAND coordinator (W3): one locked transaction over the revision owner and the
        # adoption owner. An imported/uncontracted scene now leaves with a durable request AND its
        # adoption entry; a contracted one mints the linked revise Job; an ineligible chapter fails
        # closed and persists nothing.
        try:
            accepted = await accept_revision_intent(
                session,
                scene_id=scene_id,
                feedback=body.feedback,
                target_pass=body.target_pass,
                expected_prose_hash=body.expected_prose_hash,
                origin=RevisionRequestOrigin.REVIEW,
                edited_prose=body.edited_prose,
            )
        except (AdoptionEntryError, ChapterWorkflowBusy) as exc:
            raise _revise_http_error(exc) from exc
        _kick_adoption_drain(background, accepted)
        return await _revision_acceptance_response(session, accepted, response)

    # ---- DENY: the legacy per-scene path ----
    # DECIDE-LOCK: serialize concurrent decisions on this scene (SELECT ... FOR UPDATE). APPROVE and
    # REVISE no longer reach here — both serialize on the broader per-chapter workflow lock.
    scene = (await session.execute(select(Scene).where(Scene.id == scene_id).with_for_update())).scalar_one_or_none()
    if scene is None:
        raise HTTPException(status_code=404, detail="scene not found")

    # Hand-edit in the inbox becomes the canonical text; all derivation reads this (DESIGN §9). Capture
    # the agent→human pair BEFORE overwriting prose — the pre-edit prose is the model's rendered draft.
    if body.edited_prose is not None:
        await _capture_edit_pair(session, scene, body.edited_prose)
        scene.prose = body.edited_prose
        scene.prose_source = "agent+human_edit"

    # Only DENY reaches here now (APPROVE and REVISE returned above), so the verdict and the status
    # move are unconditional.
    session.add(
        Approval(
            scene_id=scene.id,
            version=scene.version,
            decision=body.decision,
            target_pass=body.target_pass,
            feedback=body.feedback,
        )
    )
    scene.status = SceneStatus.SUPERSEDED

    # Land this review action in the central Activity feed too (best-effort; never blocks the verdict).
    chapter_row = (
        await session.execute(select(Chapter.book_id, Chapter.chapter_no).where(Chapter.id == scene.chapter_id))
    ).first()
    verb = "denied"
    place = (
        f"Ch {chapter_row[1]} · " if chapter_row and chapter_row[1] is not None else ""
    ) + f"Scene {scene.scene_no}"
    await activity.safe_record_activity(
        session,
        kind="scene_decision",
        title=f"{place} {verb}",
        source="reviews",
        severity="info",
        book_id=chapter_row[0] if chapter_row else None,
        chapter_id=scene.chapter_id,
        payload={"decision": str(body.decision)},
    )

    await session.commit()  # land the verdict before responding
    return SceneDecisionOut(scene=scene.id, status=str(scene.status), next_job=None)


@router.get("/{scene_id}/revision-request", response_model=RevisionRequestOut)
async def get_revision_request(scene_id: uuid.UUID, session: SessionDep) -> RevisionRequestOut:
    """The scene's current (most-recent) durable revision request, with its server-derived phase — the
    Desk banner reads this to show an imported / awaiting-contract scene its next action (ADR 0028)."""
    request = (
        await session.execute(
            select(RevisionRequest)
            .where(RevisionRequest.target_scene_id == scene_id)
            .order_by(RevisionRequest.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if request is None:
        raise HTTPException(status_code=404, detail="no revision request for this scene")
    return revision_request_out(request)


@router.post("/{scene_id}/continuity/resolve")
async def resolve_continuity(
    scene_id: uuid.UUID,
    body: ContinuityResolveIn,
    session: SessionDep,
    background: BackgroundTasks,
    response: Response,
) -> ContinuityResolveOut | RevisionAcceptanceOut:
    critique = await session.get(Critique, body.critique_id)
    if critique is None or critique.scene_id != scene_id:
        raise HTTPException(status_code=404, detail="critique not found for this scene")
    scene = await session.get(Scene, scene_id)
    chapter = await session.get(Chapter, scene.chapter_id) if scene else None
    if scene is None or chapter is None:
        raise HTTPException(status_code=404, detail="scene not found")

    payload = critique.payload or {}
    character = str(payload.get("character", ""))
    attribute = str(payload.get("attribute", ""))
    prose_value = payload.get("prose_value")
    ledger_value = payload.get("ledger_value")

    if body.choice == "use_prose":
        # Prose is right -> correct the Oracle's ledger.
        row = (
            await session.execute(
                select(CharacterState).where(
                    CharacterState.book_id == chapter.book_id,
                    func.lower(CharacterState.character) == character.lower(),
                )
            )
        ).scalar_one_or_none()
        if row is None:
            row = CharacterState(book_id=chapter.book_id, character=character, stats_json={})
            session.add(row)
        stats = dict(row.stats_json or {})
        stats[attribute] = _coerce(prose_value)
        row.stats_json = stats
        await session.delete(critique)  # mismatch handled — clear it from the panel
        await session.commit()
        return ContinuityResolveOut(resolved="ledger_updated")

    if body.choice == "use_ledger":
        # Ledger is right -> record durable revise intent through the ONE revise command (ADR-0032 D4),
        # exactly as the inbox Revise does: same lock, same ordering, same atomic request+adoption entry.
        # The expected hash is taken from the prose the panel just read. The coordinator re-reads the
        # scene UNDER the lock (`populate_existing`), so if a concurrent commit moved the prose in
        # between, this correctly refuses with 409 rather than revising text nobody looked at. The
        # critique this supersedes is cleared inside that same transaction.
        feedback = (
            f"Continuity fix: {character}'s {attribute} must read {ledger_value!r}, "
            f"not {prose_value!r}. Correct the prose accordingly."
        )
        try:
            accepted = await accept_revision_intent(
                session,
                scene_id=scene_id,
                feedback=feedback,
                target_pass=None,
                expected_prose_hash=prose_hash(scene.prose),
                origin=RevisionRequestOrigin.CONTINUITY,
                resolves_critique_id=critique.id,
            )
        except (AdoptionEntryError, ChapterWorkflowBusy) as exc:
            raise _revise_http_error(exc) from exc
        _kick_adoption_drain(background, accepted)
        return await _revision_acceptance_response(session, accepted, response)

    if body.choice == "edit":
        return ContinuityResolveOut(resolved="edit_in_inbox")

    raise HTTPException(status_code=422, detail="choice must be use_prose | use_ledger | edit")


def _coerce(value: object) -> object:
    if isinstance(value, str) and value.lstrip("-").isdigit():
        return int(value)
    return value
