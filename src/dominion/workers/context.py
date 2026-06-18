"""Assemble the small, scoped context one scene needs (DESIGN §4, §7).

Phase 2 fills it out: the Oracle's hard state for characters present, beat-scoped canon RAG, the
per-POV rolling summary, and the in-chapter prior-scene tail. For a revision job it also loads the
prior draft and the author's feedback. Context stays a few KB by design.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dominion.shared.config import settings
from dominion.shared.enums import Decision, SceneStatus
from dominion.shared.models import Approval, Beat, Chapter, Job, PovProfile, Run, Scene
from dominion.workers.budget import TokenBudget
from dominion.workers.memory import canon_rag, summaries
from dominion.workers.oracle import Oracle

_PRIOR_TAIL_CHARS = 800
_PROJECT_ROOT = Path(__file__).resolve().parents[3]  # …/src/dominion/workers/context.py -> repo root
_dialogue_rules_warned = False


def _load_dialogue_rules() -> str | None:
    """Read the authoritative dialogue rules fresh for each draft, so edits to the file take effect
    on the next scene. Relative paths resolve from the project root, then the CWD."""
    global _dialogue_rules_warned
    configured = Path(settings.dialogue_rules_path)
    candidates = [configured] if configured.is_absolute() else [
        _PROJECT_ROOT / configured,
        Path.cwd() / configured,
    ]
    for path in candidates:
        try:
            text = path.read_text(encoding="utf-8").strip()
        except (FileNotFoundError, NotADirectoryError):
            continue
        return text or None
    if not _dialogue_rules_warned:  # surface a misconfigured source-of-truth once, don't spam per scene
        print(f"[context] dialogue rules not found at {settings.dialogue_rules_path!r}; "
              "drafts will run without them", flush=True)
        _dialogue_rules_warned = True
    return None


@dataclass
class SceneContext:
    book_id: uuid.UUID
    chapter_id: uuid.UUID
    pov: str
    scene_no: int
    tags: list[str]
    characters_present: list[str]
    beat_text: str | None
    expected_state_changes: dict[str, Any] | None
    knowledge_injections: list[str]
    voice_spec: str | None
    budget: TokenBudget
    exemplars: list[str] = field(default_factory=list)
    dialogue_rules: str | None = None                           # authoritative dialogue source of truth
    canon: list[str] = field(default_factory=list)              # beat-scoped RAG over canon
    pov_summary: str | None = None                              # what this POV knows
    ledger: dict[str, dict[str, Any]] = field(default_factory=dict)  # Oracle read of hard stats
    prior_scene_tail: str | None = None                         # in-chapter continuity
    prior_prose: str | None = None                              # revise: the draft being revised
    revise_feedback: str | None = None                          # revise: the author's notes


async def assemble_context(session: AsyncSession, job: Job) -> SceneContext:
    """Load everything a draft (or revision) needs for this job's (chapter_no, scene_no)."""
    if job.run_id is None or job.chapter_no is None or job.scene_no is None:
        raise ValueError("job is missing run_id / chapter_no / scene_no")

    book_id = (await session.execute(
        select(Run.book_id).where(Run.id == job.run_id)
    )).scalar_one()

    chapter = (await session.execute(
        select(Chapter).where(Chapter.book_id == book_id, Chapter.chapter_no == job.chapter_no)
    )).scalar_one()

    beat = (await session.execute(
        select(Beat).where(Beat.chapter_id == chapter.id, Beat.scene_no == job.scene_no)
    )).scalar_one_or_none()
    if beat is None:
        raise ValueError(
            f"no beat for ch{job.chapter_no} sc{job.scene_no} — propose/approve beats first (gate 1)"
        )

    profile = (await session.execute(
        select(PovProfile).where(PovProfile.book_id == book_id, PovProfile.character == chapter.pov)
    )).scalar_one_or_none()

    ctx = SceneContext(
        book_id=book_id,
        chapter_id=chapter.id,
        pov=chapter.pov,
        scene_no=job.scene_no,
        tags=list(beat.tags or []),
        characters_present=list(beat.characters_present or []),
        beat_text=beat.beat_text,
        expected_state_changes=beat.expected_state_changes,
        knowledge_injections=list(beat.knowledge_injections or []),
        voice_spec=profile.voice_spec if profile else None,
        budget=TokenBudget(max_tokens=job.token_budget),
        dialogue_rules=_load_dialogue_rules(),
    )

    # Oracle: current hard state for each character present (drives the continuity reviewer).
    oracle = Oracle(session)
    ledger: dict[str, dict[str, Any]] = {}
    for character in ctx.characters_present:
        stats = await oracle.current(book_id=book_id, character=character)
        if stats:
            ledger[character] = stats
    ctx.ledger = ledger

    # Memory: beat-scoped canon, the POV's rolling summary, the previous approved scene's tail.
    retrieval_query = " ".join(p for p in [beat.beat_text or "", *ctx.characters_present] if p)
    ctx.canon = await canon_rag.retrieve(session, book_id=book_id, query=retrieval_query, k=6)
    ctx.pov_summary = await summaries.pov_summary(session, book_id=book_id, pov=chapter.pov)
    ctx.prior_scene_tail = await _prior_tail(session, chapter_id=chapter.id, scene_no=job.scene_no)

    # Revision: pull the prior draft + the latest revision feedback for the target scene.
    if job.target_scene_id is not None:
        prior = await session.get(Scene, job.target_scene_id)
        ctx.prior_prose = prior.prose if prior else None
        ctx.revise_feedback = (await session.execute(
            select(Approval.feedback)
            .where(Approval.scene_id == job.target_scene_id, Approval.decision == Decision.REVISE)
            .order_by(Approval.decided_at.desc())
            .limit(1)
        )).scalar_one_or_none()

    return ctx


async def _prior_tail(
    session: AsyncSession, *, chapter_id: uuid.UUID, scene_no: int
) -> str | None:
    prose = (await session.execute(
        select(Scene.prose)
        .where(
            Scene.chapter_id == chapter_id,
            Scene.scene_no < scene_no,
            Scene.status == SceneStatus.APPROVED,
        )
        .order_by(Scene.scene_no.desc())
        .limit(1)
    )).scalar_one_or_none()
    return prose[-_PRIOR_TAIL_CHARS:] if prose else None
