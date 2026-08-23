"""Funnel instrumentation for one chapter: did the machine get anywhere, and at what cost?

The five figures the owner asked for, and what each is actually counting:

* **approved-scene rate** — approved scenes over scenes that exist. The headline: what fraction of the
  machine's output the author accepted as canonical.
* **interventions** — human acts that changed machine state. Every `Approval` the author recorded plus
  every human `VERIFY` on a held issue (#285). This is the number that says how unattended the loop
  really was; a high approved-scene rate bought with an intervention per scene is not autonomy.
* **revisions** — `RevisionRequest` rows plus scenes carrying a version above 1. Rework, as distinct
  from rejection.
* **provider cost** — from `llm_calls`, the durable per-call exhaust. Reported as CALLS AND TOKENS, not
  dollars: no price table exists in this repo, and inventing one would produce a confident number
  nobody could reconcile against a provider invoice. `cost_usd` is therefore None, deliberately and
  visibly, rather than absent or guessed.
* **failure reasons** — a histogram of what actually went wrong, from `llm_calls.error` and failed
  `Job` rows. A count with no reasons tells an operator that the chapter failed and not why.

PURE COUNTS OVER REAL ROWS. Nothing here mutates, and nothing here decides. It reports what the run
did; `autonomy_status` decides what may happen next. Keeping the two apart is what stops a metric from
quietly becoming a gate.
"""

from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dominion.shared.enums import IssueDecisionKind, JobStatus, SceneStatus
from dominion.shared.models import (
    Approval,
    Chapter,
    Issue,
    IssueDecision,
    Job,
    LlmCall,
    ProductionRun,
    RevisionRequest,
    Scene,
)

__all__ = ["ChapterFunnel", "read_chapter_funnel"]


@dataclass(frozen=True)
class ChapterFunnel:
    """One chapter's funnel. Every field is a count over rows that exist — never an estimate."""

    chapter_id: uuid.UUID
    scenes_total: int = 0
    scenes_approved: int = 0
    interventions: int = 0
    revisions: int = 0
    provider_calls: int = 0
    provider_input_tokens: int = 0
    provider_output_tokens: int = 0
    #: Deliberately None. There is no price table in this repo; a dollar figure would be invented.
    #: Present-and-null says "not measured here" where an absent field would read as "zero".
    cost_usd: float | None = None
    failure_reasons: dict[str, int] = field(default_factory=dict)

    @property
    def approved_scene_rate(self) -> float:
        """Approved scenes over scenes that exist. 0.0 for an empty chapter — NOT 1.0: "nothing to
        approve" must never read as "everything was approved", which is the shape of a metric that
        flatters a run that did nothing."""
        return (self.scenes_approved / self.scenes_total) if self.scenes_total else 0.0

    @property
    def interventions_per_approved_scene(self) -> float | None:
        """How unattended the loop actually was. None when nothing was approved — a ratio over zero is
        not 'perfect', it is undefined, and reporting 0.0 would claim autonomy the run never showed."""
        return (self.interventions / self.scenes_approved) if self.scenes_approved else None

    def as_dict(self) -> dict[str, Any]:
        return {
            "chapter_id": str(self.chapter_id),
            "scenes_total": self.scenes_total,
            "scenes_approved": self.scenes_approved,
            "approved_scene_rate": round(self.approved_scene_rate, 4),
            "interventions": self.interventions,
            "interventions_per_approved_scene": (
                round(self.interventions_per_approved_scene, 4)
                if self.interventions_per_approved_scene is not None
                else None
            ),
            "revisions": self.revisions,
            "provider_calls": self.provider_calls,
            "provider_input_tokens": self.provider_input_tokens,
            "provider_output_tokens": self.provider_output_tokens,
            "cost_usd": self.cost_usd,
            "failure_reasons": dict(sorted(self.failure_reasons.items())),
        }


async def read_chapter_funnel(session: AsyncSession, chapter_id: uuid.UUID) -> ChapterFunnel:
    """Compute one chapter's funnel from live rows."""
    scenes = (await session.execute(select(Scene).where(Scene.chapter_id == chapter_id))).scalars().all()
    # Newest version per scene_no: an older version lingering is not a second scene, and counting it
    # would inflate the denominator and understate the approved rate.
    newest: dict[int, Scene] = {}
    for scene in scenes:
        current = newest.get(scene.scene_no)
        if current is None or (scene.version or 0) > (current.version or 0):
            newest[scene.scene_no] = scene
    scenes_total = len(newest)
    scenes_approved = sum(1 for s in newest.values() if str(s.status) == SceneStatus.APPROVED.value)
    revisions_from_versions = sum(1 for s in newest.values() if (s.version or 1) > 1)

    scene_ids = [s.id for s in scenes]
    approvals = 0
    if scene_ids:
        approvals = (
            await session.execute(select(func.count()).select_from(Approval).where(Approval.scene_id.in_(scene_ids)))
        ).scalar_one()

    # Human VERIFY decisions (#285) are interventions too: clearing a held issue is an authoring act the
    # machine could not perform, and omitting them would understate how attended the loop was.
    human_verifies = (
        await session.execute(
            select(func.count())
            .select_from(IssueDecision)
            .join(Issue, IssueDecision.issue_id == Issue.id)
            .where(Issue.chapter_id == chapter_id, IssueDecision.decision == IssueDecisionKind.VERIFY.value)
        )
    ).scalar_one()

    revision_requests = (
        await session.execute(
            select(func.count()).select_from(RevisionRequest).where(RevisionRequest.chapter_id == chapter_id)
        )
    ).scalar_one()

    calls = (await session.execute(select(LlmCall).where(LlmCall.chapter_id == chapter_id))).scalars().all()
    failures: Counter[str] = Counter()
    for call in calls:
        if call.error:
            # First clause only: provider errors carry ids and timings that would fragment the histogram
            # into one bucket per occurrence, which is a log, not a metric.
            failures[str(call.error).split(":", 1)[0].strip()[:80]] += 1

    failed_jobs = (
        (await session.execute(select(Job).where(Job.chapter_id == chapter_id, Job.status == JobStatus.FAILED.value)))
        .scalars()
        .all()
    )
    for job in failed_jobs:
        failures[str(job.last_error or "job failed").split(":", 1)[0].strip()[:80]] += 1

    return ChapterFunnel(
        chapter_id=chapter_id,
        scenes_total=scenes_total,
        scenes_approved=scenes_approved,
        interventions=int(approvals) + int(human_verifies),
        revisions=int(revision_requests) + revisions_from_versions,
        provider_calls=len(calls),
        provider_input_tokens=sum(c.input_tokens or 0 for c in calls),
        provider_output_tokens=sum(c.output_tokens or 0 for c in calls),
        cost_usd=None,
        failure_reasons=dict(failures),
    )


async def chapter_exists(session: AsyncSession, chapter_id: uuid.UUID) -> bool:
    return (await session.get(Chapter, chapter_id)) is not None


async def production_runs_for(session: AsyncSession, chapter_id: uuid.UUID) -> list[ProductionRun]:
    return list(
        (await session.execute(select(ProductionRun).where(ProductionRun.chapter_id == chapter_id))).scalars().all()
    )
