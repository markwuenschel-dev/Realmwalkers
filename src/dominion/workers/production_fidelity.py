"""Production-facing SceneFidelity reconciliation lane.

This module is imported by name from both ``dominion.workers.production`` (production triage)
and the ``api.routers.production`` author surface (repair previews); it is not re-exported
through the former. It owns the SceneFidelity *production*
lifecycle: materializing CURRENT, repair-eligible fidelity findings into run-owned Issues + HUMAN_REQUIRED
RepairTasks (Lane 5), and the author-controlled repair-preview lifecycle (Lane 6) that turns an accepted
preview into a new author-visible Scene revision. The pure evaluation package (``scene_fidelity/``) stays
free of run/Issue/RepairTask/Artifact wiring; that wiring lives here.

Import graph is one-way: this module imports ``production_repair`` for the public
``queue_repair_task_from_issues`` seam; ``production_repair`` never imports this module.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dominion.shared import verification_authority
from dominion.shared.enums import (
    ArtifactType,
    IssueStatus,
    RepairAuthorityLevel,
    RepairTaskStatus,
    SceneStatus,
)
from dominion.shared.models import (
    Artifact,
    Critique,
    DraftAttempt,
    Issue,
    ProductionRun,
    RepairTask,
    Scene,
    ScenePacket,
)
from dominion.workers import production_support as support
from dominion.workers.production_repair import queue_repair_task_from_issues
from dominion.workers.scene_fidelity.contract import fidelity_contract_fingerprint
from dominion.workers.scene_fidelity.models import ClauseResult, SceneFidelityReport, is_fidelity_active
from dominion.workers.scene_fidelity.payloads import CritiqueProjection, TriageResult
from dominion.workers.scene_fidelity.policy import (
    policy_outcome_for_clause_evaluation,
    project_report_to_critiques,
    report_is_current,
)
from dominion.workers.scene_fidelity.repair_preview import REPAIR_PREVIEW_ARTIFACT_TYPE, build_preview_body

# The SceneFidelity report Artifact type, kept as a literal here to avoid importing the evaluator (which
# pulls the LLM stack into this early-imported production module).
_FIDELITY_REPORT_TYPE = ArtifactType.SCENE_FIDELITY_REPORT.value


async def _latest_scene_map(session: AsyncSession, chapter_id: uuid.UUID) -> dict[int, Scene]:
    from dominion.workers import production_sequence

    return await production_sequence._latest_scene_map(session, chapter_id)


# --------------------------------------------------------------------------------------------------
# SceneFidelity production triage (Lane 5). Before a run completes, materialize CURRENT, unresolved,
# repair-eligible fidelity Critiques into run-owned HUMAN_REQUIRED Issues; treat missing / stale /
# incomplete evaluation as operational holds, never prose failures (ADR 0010/0018/0019/0020).
# --------------------------------------------------------------------------------------------------

_OPEN_FIDELITY_STATUSES: frozenset[str] = frozenset(
    {
        IssueStatus.PROPOSED.value,
        IssueStatus.ACCEPTED.value,
        IssueStatus.MERGED.value,
        IssueStatus.REPAIR_QUEUED.value,
        IssueStatus.ESCALATED.value,
    }
)


async def _latest_final_draft_attempt(session: AsyncSession, scene_id: uuid.UUID) -> DraftAttempt | None:
    return (
        (
            await session.execute(
                select(DraftAttempt)
                .where(DraftAttempt.scene_id == scene_id, DraftAttempt.stage == "final_rendered")
                .order_by(DraftAttempt.created_at.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )


async def _latest_fidelity_report(session: AsyncSession, draft_attempt_id: uuid.UUID) -> Artifact | None:
    return (
        (
            await session.execute(
                select(Artifact)
                .where(Artifact.artifact_type == _FIDELITY_REPORT_TYPE, Artifact.domain_id == draft_attempt_id)
                .order_by(Artifact.version.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )


async def _open_fidelity_issues_for_clause(
    session: AsyncSession, *, scene_id: uuid.UUID, clause_id: str
) -> list[Issue]:
    rows = (
        (await session.execute(select(Issue).where(Issue.validator == "scene_fidelity", Issue.scene_id == scene_id)))
        .scalars()
        .all()
    )
    return [
        issue
        for issue in rows
        if (issue.payload_json or {}).get("clause_id") == clause_id and issue.status in _OPEN_FIDELITY_STATUSES
    ]


async def _fidelity_issue_exists(session: AsyncSession, *, run: ProductionRun, critique_id: uuid.UUID) -> bool:
    rows = (
        (
            await session.execute(
                select(Issue).where(Issue.production_run_id == run.id, Issue.validator == "scene_fidelity")
            )
        )
        .scalars()
        .all()
    )
    return any((issue.payload_json or {}).get("fidelity_critique_id") == str(critique_id) for issue in rows)


async def _persist_fidelity_critique(
    session: AsyncSession, *, scene: Scene, report_artifact: Artifact, projection: CritiqueProjection
) -> tuple[Critique, bool]:
    """Persist one projected fidelity Critique, idempotent by the report-projection unique index
    (reviewer, source_artifact_id, finding_signature). Returns (critique, created)."""
    existing = (
        (
            await session.execute(
                select(Critique).where(
                    Critique.reviewer == "scene_fidelity",
                    Critique.source_artifact_id == report_artifact.id,
                    Critique.finding_signature == projection.finding_signature,
                )
            )
        )
        .scalars()
        .first()
    )
    if existing is not None:
        return existing, False
    critique = Critique(
        scene_id=scene.id,
        scene_packet_id=scene.scene_packet_id,
        reviewer="scene_fidelity",
        severity=projection.severity,
        note=projection.note,
        payload=projection.payload,
        draft_attempt_id=uuid.UUID(projection.payload["draft_attempt_id"]),
        source_artifact_id=report_artifact.id,
        finding_signature=projection.finding_signature,
    )
    session.add(critique)
    await session.flush()
    return critique, True


async def _materialize_fidelity_issue(
    session: AsyncSession,
    *,
    run: ProductionRun,
    scene: Scene,
    scene_no: int,
    report_artifact: Artifact,
    critique: Critique,
    projection: CritiqueProjection,
) -> Issue:
    """Create a run-owned Issue + a HUMAN_REQUIRED RepairTask for one repair-eligible fidelity Critique,
    and supersede any prior open Issue for the same scene+clause (ADR 0018/0020). Fidelity repair is
    ALWAYS human-required — the scheduler must never turn an export hold into an autonomous rewrite."""
    payload = projection.payload
    clause_id = payload["clause_id"]
    prior_open = await _open_fidelity_issues_for_clause(session, scene_id=scene.id, clause_id=clause_id)

    issue = await support.create_issue(
        session,
        run=run,
        artifact_type=_FIDELITY_REPORT_TYPE,
        artifact_id=report_artifact.id,
        scene_id=scene.id,
        scene_no=scene_no,
        validator="scene_fidelity",
        issue_kind=str(payload["mode"]),
        severity="repair",
        quote=None,
        span_start=None,
        span_end=None,
        claim=projection.note,
        contract_reference=clause_id,
        recommended_action="Author-required fidelity repair: resolve the lost clause via an author-controlled preview.",
        confidence=None,
        auto_repair_allowed=False,
        payload={
            "fidelity_critique_id": str(critique.id),
            "finding_signature": projection.finding_signature,
            "requirement_id": payload["requirement_id"],
            "clause_id": clause_id,
            "mode": payload["mode"],
            "source_artifact_id": str(report_artifact.id),
        },
    )
    await queue_repair_task_from_issues(
        session,
        run=run,
        issues=[issue],
        authority_level=RepairAuthorityLevel.HUMAN_REQUIRED,
        repair_kind="fidelity",
        instruction_preamble="SceneFidelity export hold — author-controlled repair required.",
    )
    for prior in prior_open:
        prior.status = IssueStatus.SUPERSEDED.value
        prior.payload_json = {**(prior.payload_json or {}), "successor_issue_id": str(issue.id)}
    return issue


async def _verify_satisfied_clauses(
    session: AsyncSession, *, scene: Scene, report: SceneFidelityReport, report_artifact_id: uuid.UUID
) -> None:
    """Record what a CURRENT satisfied evaluation claims about each open fidelity Issue (#285).

    This function used to set `IssueStatus.VERIFIED` outright, under a docstring saying verification was
    "never by the mere absence of a complaint". That was literally true and materially false: it was
    verification by the mere PRESENCE of a model claim. `ev.result` is copied verbatim from the adapter's
    model output (`scene_fidelity/evaluator.py:183`), and while `ev.evidence_valid` beside it IS
    deterministic — span-in-range plus exact quote match — it only proves the quote the model chose really
    occurs at the offsets it named. It does not prove the quote SATISFIES the clause. So a model returning
    `satisfied` plus any exact substring of the prose closed a human-required hold.

    What the model may still do is unchanged and complete: it nominates, with its evidence recorded as an
    append-only `IssueDecision` naming the immutable report artifact and clause the claim rests on. The
    issue's status is untouched, so the hold stays active and keeps counting toward the readiness gate at
    `production_sequence.py:904-913` — which is what stops both children shortening the count that gates
    publication.

    Ceiling-gated work still auto-verifies. Only work whose Authorization Requirement demands an explicit
    human grant is withheld, and an issue whose requirement cannot be determined is treated as demanding
    one (unknown provenance is not human provenance).
    """
    satisfied = {
        ev.clause_id for ev in report.clause_evaluations if ev.result == ClauseResult.SATISFIED and ev.evidence_valid
    }
    if not satisfied:
        return

    by_clause: dict[str, list[Issue]] = {}
    for clause_id in sorted(satisfied):
        by_clause[clause_id] = await _open_fidelity_issues_for_clause(session, scene_id=scene.id, clause_id=clause_id)
    every_issue = [issue for issues in by_clause.values() for issue in issues]
    if not every_issue:
        return

    # ONE query for the whole set: the linked repair tasks whose Authorization Requirement decides whether
    # a human must clear each issue. Per issue would be an N+1 over every scene of every chapter.
    tasks_by_issue = await verification_authority.manual_grant_task_ids_for_issues(
        session, [issue.id for issue in every_issue]
    )

    for clause_id, issues in by_clause.items():
        for issue in issues:
            linked = tasks_by_issue.get(str(issue.id), [])
            if verification_authority.demands_human_verification(linked):
                await verification_authority.nominate_verification(
                    session,
                    issue_id=issue.id,
                    decided_by="scene_fidelity_evaluator",
                    evidence_kind=verification_authority.EVIDENCE_KIND_FIDELITY_CLAUSE,
                    # DIRECT evidence identity: the immutable report artifact plus the clause. A
                    # re-evaluation of the same report collides on the uniqueness key rather than
                    # appending a second identical nomination.
                    evidence_id=f"{report_artifact_id}:{clause_id}",
                    reason=(
                        f"evaluator reported clause {clause_id} SATISFIED with a quote that validated "
                        "against the prose. This is a nomination, not a verification: a human grant is "
                        "required to clear this hold."
                    ),
                )
                continue
            issue.status = IssueStatus.VERIFIED.value


async def triage_scene_fidelity_for_production(session: AsyncSession, *, run: ProductionRun) -> TriageResult:
    """Materialize a run's CURRENT repair-eligible fidelity findings into run-owned Issues, verify clauses
    that now pass, and collect operational holds. Idempotent: keyed by (production_run_id,
    fidelity_critique_id), a re-run creates no duplicate Issue. Legacy/inert packets are skipped."""
    created_issue_ids: list[uuid.UUID] = []
    operational_holds: list[str] = []
    latest_scenes = await _latest_scene_map(session, run.chapter_id)

    for scene_no, scene in sorted(latest_scenes.items()):
        if scene.scene_packet_id is None:
            continue
        packet = await session.get(ScenePacket, scene.scene_packet_id)
        if packet is None or not is_fidelity_active(dict(packet.body or {})):
            continue  # no active fidelity contract — nothing to triage (forward-only, ADR 0025)

        final_attempt = await _latest_final_draft_attempt(session, scene.id)
        if final_attempt is None:
            operational_holds.append(f"scene {scene_no}: no draft attempt to evaluate")
            continue
        report_artifact = await _latest_fidelity_report(session, final_attempt.id)
        if report_artifact is None:
            operational_holds.append(f"scene {scene_no}: no fidelity evaluation report")
            continue

        current, reason = report_is_current(
            report_artifact.body or {},
            scene_packet_id=packet.id,
            packet_fingerprint=fidelity_contract_fingerprint(dict(packet.body or {})),
            draft_attempt_id=final_attempt.id,
            prose=final_attempt.prose or scene.prose or "",
        )
        if not current:
            operational_holds.append(f"scene {scene_no}: stale evaluation ({reason})")
            continue

        report = SceneFidelityReport.model_validate(report_artifact.body or {})
        for evaluation in report.clause_evaluations:
            if policy_outcome_for_clause_evaluation(evaluation).kind == "operational_hold":
                operational_holds.append(
                    f"scene {scene_no}: clause {evaluation.clause_id} incomplete ({evaluation.result.value})"
                )

        await _verify_satisfied_clauses(session, scene=scene, report=report, report_artifact_id=report_artifact.id)

        for projection in project_report_to_critiques(report, source_artifact_id=report_artifact.id):
            critique, _created = await _persist_fidelity_critique(
                session, scene=scene, report_artifact=report_artifact, projection=projection
            )
            if projection.severity != "repair":
                continue  # advisory warnings never become run Issues
            if await _fidelity_issue_exists(session, run=run, critique_id=critique.id):
                continue  # idempotent by (production_run_id, fidelity_critique_id)
            issue = await _materialize_fidelity_issue(
                session,
                run=run,
                scene=scene,
                scene_no=scene_no,
                report_artifact=report_artifact,
                critique=critique,
                projection=projection,
            )
            created_issue_ids.append(issue.id)

    await session.flush()  # persist lifecycle transitions (VERIFIED/SUPERSEDED) within the run txn
    return TriageResult(created_issue_ids=created_issue_ids, operational_holds=operational_holds)


# --------------------------------------------------------------------------------------------------
# SceneFidelity repair previews (Lane 6). A preview is an immutable, bounded proposal tied to one
# actionable fidelity Issue; it never changes the current Scene. Only the author, by accepting or
# editing it, materializes a NEW author-visible revision (ADR 0017). Its Artifact BODY is immutable —
# lifecycle rides the Artifact.status column, not a body edit.
# --------------------------------------------------------------------------------------------------


async def create_repair_preview(
    session: AsyncSession, *, issue: Issue, candidate_prose: str, rationale: str, edited: bool = False
) -> Artifact:
    """Create an immutable RepairPreview Artifact for one repair-eligible fidelity Issue, carrying the
    diff, evidence window, and preservation boundary. Does NOT touch the current Scene (ADR 0017)."""
    ipayload = issue.payload_json or {}
    critique = None
    if ipayload.get("fidelity_critique_id"):
        critique = await session.get(Critique, uuid.UUID(ipayload["fidelity_critique_id"]))
    cpayload = (critique.payload if critique else {}) or {}
    scene = await session.get(Scene, issue.scene_id) if issue.scene_id else None
    old_prose = (scene.prose if scene else "") or ""

    body = build_preview_body(
        source_issue_id=str(issue.id),
        source_critique_id=str(critique.id) if critique else "",
        source_report_artifact_id=str(ipayload.get("source_artifact_id") or ""),
        source_draft_attempt_id=str(cpayload.get("draft_attempt_id") or ""),
        scene_id=str(issue.scene_id or ""),
        prose_hash=str(cpayload.get("prose_hash") or ""),
        packet_fingerprint=str(cpayload.get("packet_contract_fingerprint") or ""),
        clause_ids=[ipayload["clause_id"]] if ipayload.get("clause_id") else [],
        anchors=cpayload.get("evidence_anchors") or [],
        old_prose=old_prose,
        candidate_prose=candidate_prose,
        rationale=rationale,
        edited=edited,
    )
    run = await session.get(ProductionRun, issue.production_run_id)
    assert run is not None  # an Issue always belongs to a run
    return await support.create_artifact(
        session,
        run=run,
        artifact_type=REPAIR_PREVIEW_ARTIFACT_TYPE,
        body=body,
        domain_table="issues",
        domain_id=issue.id,
    )


async def accept_repair_preview(
    session: AsyncSession, *, preview_artifact_id: uuid.UUID, edited_prose: str | None = None
) -> Scene:
    """Materialize an accepted (or edited) preview into a NEW author-visible Scene revision, supersede the
    old revision, stale the source evidence, and schedule fresh evaluation. Accept and edit both create a
    NORMAL new revision — only the prose_source differs (ADR 0017)."""
    preview = await session.get(Artifact, preview_artifact_id)
    assert preview is not None
    body = preview.body or {}
    scene = await session.get(Scene, uuid.UUID(body["scene_id"]))
    assert scene is not None
    new_prose = edited_prose if edited_prose is not None else (body.get("candidate_prose") or "")

    new_scene = Scene(
        chapter_id=scene.chapter_id,
        scene_no=scene.scene_no,
        version=scene.version + 1,
        parent_scene_id=scene.id,
        status=SceneStatus.PENDING_REVIEW,
        scene_packet_id=scene.scene_packet_id,
        prose=new_prose,
        prose_source="agent+human_edit" if edited_prose is not None else "agent",
        word_count=len((new_prose or "").split()),
    )
    session.add(new_scene)
    scene.status = SceneStatus.SUPERSEDED
    await session.flush()

    # A final DraftAttempt so fresh fidelity evaluation of the new revision has a target (the actual
    # re-evaluation is scheduled/deferred — never inline here).
    session.add(
        DraftAttempt(
            scene_id=new_scene.id,
            scene_packet_id=new_scene.scene_packet_id,
            stage="final_rendered",
            prose=new_prose,
            model="human_repair_preview",
        )
    )
    # The source Issue's evidence is now stale; mark it REPAIRED (fresh evaluation VERIFIES or re-opens it).
    if body.get("source_issue_id"):
        source_issue = await session.get(Issue, uuid.UUID(body["source_issue_id"]))
        if source_issue is not None:
            source_issue.status = IssueStatus.REPAIRED.value
    preview.status = "materialized"  # body stays immutable; status marks the lifecycle
    await session.flush()

    run = await session.get(ProductionRun, preview.production_run_id) if preview.production_run_id else None
    if run is not None:
        await support.record_event(
            session,
            run_id=run.id,
            event_type="scene_fidelity_repair_accepted",
            stage=run.current_stage,
            message="Author accepted a fidelity repair preview; new revision scheduled for re-evaluation",
            payload={
                "scene_id": str(new_scene.id),
                "preview_artifact_id": str(preview.id),
                "edited": edited_prose is not None,
            },
        )
    return new_scene


async def override_fidelity_issue(
    session: AsyncSession, *, issue: Issue, reason: str, overridden_by: str = "author"
) -> Issue:
    """Author override of a fidelity repair Issue (ADR 0009): record the reason + affected clause, mark
    the Issue OVERRIDDEN, and CANCEL its human-required RepairTask. The override does not mutate the
    report and never inherits to later drafts — a fresh loss on a new draft materializes a NEW Issue."""
    issue.status = IssueStatus.OVERRIDDEN.value
    ipayload = issue.payload_json or {}
    issue.payload_json = {
        **ipayload,
        "override": {"reason": reason, "by": overridden_by, "clause_id": ipayload.get("clause_id")},
    }
    tasks = (
        (await session.execute(select(RepairTask).where(RepairTask.production_run_id == issue.production_run_id)))
        .scalars()
        .all()
    )
    for task in tasks:
        if str(issue.id) in (task.issue_ids or []) and task.status not in (
            RepairTaskStatus.VERIFIED,
            RepairTaskStatus.CANCELLED,
        ):
            task.status = RepairTaskStatus.CANCELLED
    await session.flush()
    run = await session.get(ProductionRun, issue.production_run_id)
    if run is not None:
        await support.record_event(
            session,
            run_id=run.id,
            event_type="scene_fidelity_issue_overridden",
            stage=run.current_stage,
            message="Author overrode a fidelity repair Issue",
            payload={"issue_id": str(issue.id), "reason": reason},
        )
    return issue


async def reject_repair_preview(
    session: AsyncSession, *, preview_artifact_id: uuid.UUID, reason: str | None = None
) -> Artifact:
    """Reject a preview. The Critique and Issue stay intact and the current Scene is untouched (ADR 0017)."""
    preview = await session.get(Artifact, preview_artifact_id)
    assert preview is not None
    preview.status = "rejected"
    await session.flush()
    run = await session.get(ProductionRun, preview.production_run_id) if preview.production_run_id else None
    if run is not None:
        await support.record_event(
            session,
            run_id=run.id,
            event_type="scene_fidelity_repair_rejected",
            stage=run.current_stage,
            message="Author rejected a fidelity repair preview",
            payload={"preview_artifact_id": str(preview.id), "reason": reason},
        )
    return preview
