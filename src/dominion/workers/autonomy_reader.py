"""Read a chapter's real state into `ChapterAutonomyInputs`. The DB half of the autonomy projection.

Kept separate from `autonomy_status` on purpose: that module is a pure total function over a typed
struct, which is what makes its precedence exhaustively testable without a database. This module is the
only place that decides WHICH rows answer each question, and it decides nothing about what the answers
mean.

Every input is read from a seam that already owns it, never re-derived:

* unresolved open questions come from `approval_policy.open_question_items` — the ONE module permitted
  to turn that column into a gate decision (#277, enforced by the fork-3b seam guard);
* holds awaiting a human grant come from `verification_authority` — the predicate that reads
  `authorization_requirement` alone and fails closed on anything it does not recognize (#285).

Re-deriving either here would create the second reader that both of those tickets exist to prevent.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dominion.shared.enums import IssueStatus, PacketStatus, SceneStatus
from dominion.shared.models import ChapterPacket, Issue, ProductionRun, Scene
from dominion.shared.verification_authority import demands_human_verification, manual_grant_task_ids_for_issues
from dominion.workers.autonomy_status import ChapterAutonomyInputs, ChapterAutonomyStatus, project_chapter_autonomy

#: Issue statuses that still count as OPEN. Mirrors `production_sequence`'s readiness set exactly — the
#: set that gates publication — so this projection and that gate cannot drift into disagreeing about
#: whether a chapter is finished.
OPEN_ISSUE_STATUSES = frozenset(
    {
        IssueStatus.PROPOSED.value,
        IssueStatus.ACCEPTED.value,
        IssueStatus.REPAIR_QUEUED.value,
        IssueStatus.REPAIRED.value,
        IssueStatus.ESCALATED.value,
    }
)


async def read_chapter_autonomy_inputs(
    session: AsyncSession, chapter_id: uuid.UUID, *, operational_failure: str | None = None
) -> ChapterAutonomyInputs:
    """Gather the facts. `operational_failure` is supplied by the caller because provider/infrastructure
    health is not a row in this database — the driver knows it, and passing it in keeps this function
    honest about what it can and cannot observe."""
    # The chapter's AUTHORITY packet — the approved one, not merely the newest. `_latest` semantics
    # (newest by created_at) would return a proposed amendment while the approved predecessor still
    # governs, and asking the amendment about open questions answers a different question.
    authority = (
        await session.execute(
            select(ChapterPacket)
            .where(ChapterPacket.chapter_id == chapter_id, ChapterPacket.status == PacketStatus.APPROVED.value)
            .order_by(ChapterPacket.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    unresolved_open_questions = 0
    if authority is not None:
        from dominion.workers.packet import approval_policy  # local: avoids a heavy import at module load

        unresolved_open_questions = len(approval_policy.open_question_items(authority))

    issues = (
        (
            await session.execute(
                select(Issue)
                .join(ProductionRun, Issue.production_run_id == ProductionRun.id)
                .where(ProductionRun.chapter_id == chapter_id)
            )
        )
        .scalars()
        .all()
    )
    open_issues = [issue for issue in issues if str(issue.status) in OPEN_ISSUE_STATUSES]

    # Of the OPEN issues, how many need an explicit human grant to clear? One query for the whole set.
    holds = 0
    if open_issues:
        tasks_by_issue = await manual_grant_task_ids_for_issues(session, [issue.id for issue in open_issues])
        holds = sum(1 for issue in open_issues if demands_human_verification(tasks_by_issue.get(str(issue.id), [])))

    scenes = (await session.execute(select(Scene).where(Scene.chapter_id == chapter_id))).scalars().all()
    # Newest version per scene_no — an older version lingering must not read as a second scene.
    newest: dict[int, Scene] = {}
    for scene in scenes:
        current = newest.get(scene.scene_no)
        if current is None or (scene.version or 0) > (current.version or 0):
            newest[scene.scene_no] = scene
    drafted = [s for s in newest.values() if (s.prose or "").strip()]
    draft_complete = bool(drafted) and len(drafted) == len(newest)
    work_remaining = any(str(s.status) == SceneStatus.DRAFT.value for s in newest.values()) or not draft_complete
    missing = tuple(sorted(no for no, scene in newest.items() if not (scene.prose or "").strip()))

    return ChapterAutonomyInputs(
        operational_failure=operational_failure,
        unresolved_open_questions=unresolved_open_questions,
        holds_awaiting_human_verification=holds,
        open_issues=len(open_issues),
        missing_scene_nos=missing,
        qa_blocked=False,
        draft_complete=draft_complete,
        work_remaining=work_remaining,
    )


async def chapter_autonomy_status(
    session: AsyncSession, chapter_id: uuid.UUID, *, operational_failure: str | None = None
) -> ChapterAutonomyStatus:
    """THE runtime answer for one chapter. Every consumer — the API, the driver, the Desk — reads this,
    so there is exactly one notion of whether a chapter may proceed."""
    inputs = await read_chapter_autonomy_inputs(session, chapter_id, operational_failure=operational_failure)
    return project_chapter_autonomy(inputs)
