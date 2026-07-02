"""Editorial production run endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException

from dominion.api.deps import SessionDep
from dominion.shared.models import RepairTask
from dominion.shared.schemas import (
    AgentEventOut,
    AgentRunOut,
    ArtifactDependencyOut,
    ArtifactOut,
    ChapterSequenceOut,
    ChapterSequenceQaOut,
    ChapterSequenceUpdateIn,
    IssueDecisionIn,
    IssueDecisionOut,
    IssueOut,
    ProductionRunActionOut,
    ProductionRunCreateIn,
    ProductionRunDetailOut,
    ProductionRunOut,
    ProductionRunStartIn,
    RepairAttemptOut,
    RepairTaskOut,
    RepairVerificationOut,
)
from dominion.workers import production

router = APIRouter(tags=["production"])


def _run_out(run) -> ProductionRunOut:
    return ProductionRunOut.model_validate(run)


def _raise_for_value_error(exc: ValueError) -> HTTPException:
    detail = str(exc)
    status = 404 if "not found" in detail else 409
    return HTTPException(status_code=status, detail=detail)


async def _action_out(session: SessionDep, run_id: uuid.UUID) -> ProductionRunActionOut:
    detail = await production.production_run_detail(session, run_id)
    await session.refresh(detail["run"])
    latest_verification = detail["repair_verifications"][-1] if detail["repair_verifications"] else None
    return ProductionRunActionOut(
        run=_run_out(detail["run"]),
        issue_count=len(detail["issues"]),
        repair_task_count=len(detail["repair_tasks"]),
        latest_verification=RepairVerificationOut.model_validate(latest_verification) if latest_verification else None,
    )


@router.post("/production-runs", response_model=ProductionRunActionOut)
async def start_production_run(body: ProductionRunCreateIn, session: SessionDep) -> ProductionRunActionOut:
    try:
        run = await production.create_production_run(
            session,
            chapter_id=body.chapter_id,
            mode=body.mode,
            target_words=body.target_words,
            hard_max_words=body.hard_max_words,
            auto_triage=body.auto_triage,
        )
    except ValueError as exc:
        raise _raise_for_value_error(exc) from exc
    await session.commit()
    return await _action_out(session, run.id)


@router.post("/chapters/{chapter_id}/production-runs", response_model=ProductionRunActionOut)
async def start_chapter_production_run(
    chapter_id: uuid.UUID, body: ProductionRunStartIn, session: SessionDep
) -> ProductionRunActionOut:
    try:
        run = await production.create_production_run(
            session,
            chapter_id=chapter_id,
            mode=body.mode,
            target_words=body.target_words,
            hard_max_words=body.hard_max_words,
            auto_triage=body.auto_triage,
        )
    except ValueError as exc:
        raise _raise_for_value_error(exc) from exc
    await session.commit()
    return await _action_out(session, run.id)


@router.get("/chapters/{chapter_id}/production-runs", response_model=list[ProductionRunOut])
async def list_production_runs(chapter_id: uuid.UUID, session: SessionDep) -> list[ProductionRunOut]:
    rows = await production.list_production_runs(session, chapter_id)
    return [_run_out(row) for row in rows]


@router.get("/production-runs/{run_id}", response_model=ProductionRunDetailOut)
async def get_production_run(run_id: uuid.UUID, session: SessionDep) -> ProductionRunDetailOut:
    try:
        detail = await production.production_run_detail(session, run_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ProductionRunDetailOut(
        run=_run_out(detail["run"]),
        chapter_sequence=ChapterSequenceOut.model_validate(detail["chapter_sequence"])
        if detail["chapter_sequence"] is not None
        else None,
        artifacts=[ArtifactOut.model_validate(row) for row in detail["artifacts"]],
        dependencies=[ArtifactDependencyOut.model_validate(row) for row in detail["dependencies"]],
        agent_runs=[AgentRunOut.model_validate(row) for row in detail["agent_runs"]],
        events=[AgentEventOut.model_validate(row) for row in detail["events"]],
        issues=[IssueOut.model_validate(row) for row in detail["issues"]],
        issue_decisions=[IssueDecisionOut.model_validate(row) for row in detail["issue_decisions"]],
        repair_tasks=[RepairTaskOut.model_validate(row) for row in detail["repair_tasks"]],
        repair_attempts=[RepairAttemptOut.model_validate(row) for row in detail["repair_attempts"]],
        repair_verifications=[RepairVerificationOut.model_validate(row) for row in detail["repair_verifications"]],
    )


@router.get("/production-runs/{run_id}/events", response_model=list[AgentEventOut])
async def get_production_run_events(run_id: uuid.UUID, session: SessionDep) -> list[AgentEventOut]:
    rows = await production.production_run_events(session, run_id)
    return [AgentEventOut.model_validate(row) for row in rows]


@router.post("/production-runs/{run_id}/cancel", response_model=ProductionRunOut)
async def cancel_production_run(run_id: uuid.UUID, session: SessionDep) -> ProductionRunOut:
    try:
        run = await production.cancel_production_run(session, run_id)
    except ValueError as exc:
        raise _raise_for_value_error(exc) from exc
    await session.commit()
    return _run_out(run)


@router.post("/production-runs/{run_id}/resume", response_model=ProductionRunOut)
async def resume_production_run(run_id: uuid.UUID, session: SessionDep) -> ProductionRunOut:
    try:
        run = await production.resume_production_run(session, run_id)
    except ValueError as exc:
        raise _raise_for_value_error(exc) from exc
    await session.commit()
    return _run_out(run)


@router.get("/production-runs/{run_id}/artifacts", response_model=list[ArtifactOut])
async def get_production_run_artifacts(run_id: uuid.UUID, session: SessionDep) -> list[ArtifactOut]:
    rows = await production.production_run_artifacts(session, run_id)
    return [ArtifactOut.model_validate(row) for row in rows]


@router.get("/artifacts/{artifact_id}", response_model=ArtifactOut)
async def get_artifact(artifact_id: uuid.UUID, session: SessionDep) -> ArtifactOut:
    try:
        artifact = await production.artifact_detail(session, artifact_id)
    except ValueError as exc:
        raise _raise_for_value_error(exc) from exc
    return ArtifactOut.model_validate(artifact)


@router.get("/artifacts/{artifact_id}/dependencies", response_model=list[ArtifactDependencyOut])
async def get_artifact_dependencies(artifact_id: uuid.UUID, session: SessionDep) -> list[ArtifactDependencyOut]:
    rows = await production.artifact_dependency_list(session, artifact_id)
    return [ArtifactDependencyOut.model_validate(row) for row in rows]


@router.get("/production-runs/{run_id}/issues", response_model=list[IssueOut])
async def get_production_run_issues(run_id: uuid.UUID, session: SessionDep) -> list[IssueOut]:
    rows = await production.production_run_issues(session, run_id)
    return [IssueOut.model_validate(row) for row in rows]


async def _decide_issue(
    issue_id: uuid.UUID,
    decision: str,
    body: IssueDecisionIn | None,
    session: SessionDep,
) -> IssueOut:
    try:
        issue = await production.decide_issue(
            session,
            issue_id,
            decision=decision,
            reason=body.reason if body else None,
            merged_into_issue_id=body.merged_into_issue_id if body else None,
        )
    except ValueError as exc:
        raise _raise_for_value_error(exc) from exc
    await session.commit()
    return IssueOut.model_validate(issue)


@router.post("/issues/{issue_id}/accept", response_model=IssueOut)
async def accept_issue(issue_id: uuid.UUID, body: IssueDecisionIn | None, session: SessionDep) -> IssueOut:
    return await _decide_issue(issue_id, "accept", body, session)


@router.post("/issues/{issue_id}/reject", response_model=IssueOut)
async def reject_issue(issue_id: uuid.UUID, body: IssueDecisionIn | None, session: SessionDep) -> IssueOut:
    return await _decide_issue(issue_id, "reject", body, session)


@router.post("/issues/{issue_id}/merge", response_model=IssueOut)
async def merge_issue(issue_id: uuid.UUID, body: IssueDecisionIn, session: SessionDep) -> IssueOut:
    return await _decide_issue(issue_id, "merge", body, session)


@router.post("/issues/{issue_id}/escalate", response_model=IssueOut)
async def escalate_issue(issue_id: uuid.UUID, body: IssueDecisionIn | None, session: SessionDep) -> IssueOut:
    return await _decide_issue(issue_id, "escalate", body, session)


@router.post("/issues/{issue_id}/mark-false-positive", response_model=IssueOut)
async def mark_issue_false_positive(issue_id: uuid.UUID, body: IssueDecisionIn | None, session: SessionDep) -> IssueOut:
    return await _decide_issue(issue_id, "mark_false_positive", body, session)


@router.post("/production-runs/{run_id}/triage", response_model=ProductionRunActionOut)
async def triage_production_run(run_id: uuid.UUID, session: SessionDep) -> ProductionRunActionOut:
    try:
        run = await production.triage_production_run(session, run_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await session.commit()
    return await _action_out(session, run.id)


@router.post("/production-runs/{run_id}/assemble", response_model=ProductionRunActionOut)
async def assemble_production_run(run_id: uuid.UUID, session: SessionDep) -> ProductionRunActionOut:
    detail = await production.production_run_detail(session, run_id)
    run = detail["run"]
    await production.assemble_run(session, run)
    await production._update_run_summary(session, run)
    await session.commit()
    return await _action_out(session, run.id)


@router.post("/production-runs/{run_id}/draft-missing-scenes", response_model=ProductionRunActionOut)
async def draft_missing_scenes(run_id: uuid.UUID, session: SessionDep) -> ProductionRunActionOut:
    """Queue draft jobs for any ChapterSequence scenes that lack prose but have approved contracts.

    This lets the production run drive generation of the full chapter rather than only reacting to
    pre-existing scenes and escalating missing ones.
    """
    detail = await production.production_run_detail(session, run_id)
    run = detail["run"]
    await production.queue_draft_jobs_for_missing_sequence_scenes(session, run)
    await production._update_run_summary(session, run)
    await session.commit()
    return await _action_out(session, run.id)


@router.get("/production-runs/{run_id}/repair-tasks", response_model=list[RepairTaskOut])
async def get_production_run_repair_tasks(run_id: uuid.UUID, session: SessionDep) -> list[RepairTaskOut]:
    rows = await production.production_run_repair_tasks(session, run_id)
    return [RepairTaskOut.model_validate(row) for row in rows]


@router.post("/repair-tasks/{task_id}/apply", response_model=RepairTaskOut)
async def apply_repair_task(task_id: uuid.UUID, session: SessionDep) -> RepairTaskOut:
    try:
        task = await production.apply_repair_task(session, task_id)
    except ValueError as exc:
        raise _raise_for_value_error(exc) from exc
    await session.commit()
    return RepairTaskOut.model_validate(task)


@router.post("/repair-tasks/{task_id}/run", response_model=RepairTaskOut)
async def run_repair_task(task_id: uuid.UUID, session: SessionDep) -> RepairTaskOut:
    return await apply_repair_task(task_id, session)


@router.post("/repair-tasks/{task_id}/verify", response_model=RepairVerificationOut)
async def verify_repair_task(task_id: uuid.UUID, session: SessionDep) -> RepairVerificationOut:
    try:
        verification = await production.verify_repair_task(session, task_id)
    except ValueError as exc:
        raise _raise_for_value_error(exc) from exc
    await session.commit()
    return RepairVerificationOut.model_validate(verification)


@router.post("/repair-tasks/{task_id}/reject", response_model=RepairTaskOut)
async def reject_repair_task(task_id: uuid.UUID, body: IssueDecisionIn | None, session: SessionDep) -> RepairTaskOut:
    try:
        task = await production.reject_repair_task(session, task_id, reason=body.reason if body else None)
    except ValueError as exc:
        raise _raise_for_value_error(exc) from exc
    await session.commit()
    return RepairTaskOut.model_validate(task)


@router.post("/repair-tasks/{task_id}/rollback", response_model=RepairTaskOut)
async def rollback_repair_task(task_id: uuid.UUID, body: IssueDecisionIn | None, session: SessionDep) -> RepairTaskOut:
    try:
        task = await production.rollback_repair_task(session, task_id, reason=body.reason if body else None)
    except ValueError as exc:
        raise _raise_for_value_error(exc) from exc
    await session.commit()
    return RepairTaskOut.model_validate(task)


@router.get("/repair-tasks/{task_id}", response_model=RepairTaskOut)
async def get_repair_task(task_id: uuid.UUID, session: SessionDep) -> RepairTaskOut:
    row = await session.get(RepairTask, task_id)
    if row is None:
        raise HTTPException(status_code=404, detail="repair task not found")
    return RepairTaskOut.model_validate(row)


@router.get("/chapters/{chapter_id}/chapter-sequence", response_model=ChapterSequenceOut)
async def get_chapter_sequence(chapter_id: uuid.UUID, session: SessionDep) -> ChapterSequenceOut:
    sequence = await production.latest_chapter_sequence(session, chapter_id)
    if sequence is None:
        raise HTTPException(status_code=404, detail="chapter sequence not found")
    return ChapterSequenceOut.model_validate(sequence)


@router.post("/chapters/{chapter_id}/chapter-sequence/derive", response_model=ChapterSequenceOut)
async def derive_chapter_sequence(chapter_id: uuid.UUID, session: SessionDep) -> ChapterSequenceOut:
    try:
        sequence = await production.derive_chapter_sequence_for_chapter(session, chapter_id)
    except ValueError as exc:
        raise _raise_for_value_error(exc) from exc
    await session.commit()
    return ChapterSequenceOut.model_validate(sequence)


@router.put("/chapter-sequences/{sequence_id}", response_model=ChapterSequenceOut)
async def update_chapter_sequence(
    sequence_id: uuid.UUID, body: ChapterSequenceUpdateIn, session: SessionDep
) -> ChapterSequenceOut:
    try:
        sequence = await production.update_chapter_sequence(session, sequence_id, body.body, reason=body.reason)
    except ValueError as exc:
        raise _raise_for_value_error(exc) from exc
    await session.commit()
    return ChapterSequenceOut.model_validate(sequence)


@router.post("/chapter-sequences/{sequence_id}/qa", response_model=ChapterSequenceQaOut)
async def qa_chapter_sequence(sequence_id: uuid.UUID, session: SessionDep) -> ChapterSequenceQaOut:
    try:
        evaluation = await production.chapter_sequence_qa(session, sequence_id)
    except ValueError as exc:
        raise _raise_for_value_error(exc) from exc
    await session.commit()
    return ChapterSequenceQaOut.model_validate(evaluation)


@router.post("/chapter-sequences/{sequence_id}/approve", response_model=ChapterSequenceOut)
async def approve_chapter_sequence(sequence_id: uuid.UUID, session: SessionDep) -> ChapterSequenceOut:
    try:
        sequence = await production.approve_chapter_sequence(session, sequence_id)
    except ValueError as exc:
        raise _raise_for_value_error(exc) from exc
    await session.commit()
    return ChapterSequenceOut.model_validate(sequence)


@router.post("/chapter-sequences/{sequence_id}/revise", response_model=ChapterSequenceOut)
async def revise_chapter_sequence(
    sequence_id: uuid.UUID, body: ChapterSequenceUpdateIn, session: SessionDep
) -> ChapterSequenceOut:
    try:
        sequence = await production.update_chapter_sequence(session, sequence_id, body.body, reason=body.reason)
    except ValueError as exc:
        raise _raise_for_value_error(exc) from exc
    await session.commit()
    return ChapterSequenceOut.model_validate(sequence)


@router.get("/production-runs/{run_id}/final-chapter", response_model=ArtifactOut)
async def get_final_chapter(run_id: uuid.UUID, session: SessionDep) -> ArtifactOut:
    artifact = await production.latest_final_chapter(session, run_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="final chapter not found")
    return ArtifactOut.model_validate(artifact)


@router.post("/production-runs/{run_id}/final-qa", response_model=ArtifactOut)
async def run_final_qa(run_id: uuid.UUID, session: SessionDep) -> ArtifactOut:
    try:
        artifact = await production.run_final_qa(session, run_id)
    except ValueError as exc:
        raise _raise_for_value_error(exc) from exc
    await session.commit()
    return ArtifactOut.model_validate(artifact)


@router.post("/production-runs/{run_id}/approve-final", response_model=ProductionRunOut)
async def approve_final_chapter(run_id: uuid.UUID, session: SessionDep) -> ProductionRunOut:
    try:
        run = await production.approve_final_chapter(session, run_id)
    except ValueError as exc:
        raise _raise_for_value_error(exc) from exc
    await session.commit()
    return _run_out(run)
