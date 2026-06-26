"""Assemble the small, scoped context one scene needs (DESIGN §4, §7).

Phase 2 fills it out: the Oracle's hard state for characters present, beat-scoped canon RAG, the
per-POV rolling summary, and the in-chapter prior-scene tail. For a revision job it also loads the
prior draft and the author's feedback. Context stays a few KB by design.
"""
from __future__ import annotations

import re
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dominion.shared.config import settings
from dominion.shared.enums import BeatStatus, Decision, PacketStatus, SceneStatus
from dominion.shared.models import (
    Approval,
    Beat,
    Chapter,
    ChapterPacket,
    Job,
    PovProfile,
    Run,
    Scene,
)
from dominion.workers.budget import TokenBudget
from dominion.workers.memory import canon_rag, summaries
from dominion.workers.oracle import Oracle

_PRIOR_TAIL_CHARS = 800
_PROJECT_ROOT = Path(__file__).resolve().parents[3]  # …/src/dominion/workers/context.py -> repo root
_dialogue_rules_warned = False

# In dialogue_rules.md the general craft is always-on, but each character's idiolect lives in a
# Tool-2 profile headed by "### <Name>" (e.g. "### Marcus"). We load the general rules
# unconditionally and keep a character's profile only when they're in the scene — this caps the
# always-on cost and stops an absent character's voice leaking into a POV that isn't theirs.
_CHAR_BLOCK_RE = re.compile(
    r"^### (?P<header>[^\n]+)\n.*?(?=^### |^## |\Z)", re.MULTILINE | re.DOTALL
)
# Ayla is Marcus's in-head companion; her dialogue rules ride along whenever Marcus is on the page.
_BLOCK_ALIASES: dict[str, set[str]] = {"ayla": {"ayla", "marcus"}}


def _header_names(header: str) -> set[str]:
    """Names that pull in a profile block, parsed from its header. 'Marcus (Marc)' -> {marcus, marc}."""
    names = {n.strip().lower() for n in re.split(r"[(),/]| and ", header) if n.strip()}
    for name in list(names):
        names |= _BLOCK_ALIASES.get(name, set())
    return names


def _scope_dialogue_rules(text: str, present: Iterable[str]) -> str:
    """Keep the general dialogue craft; drop per-character profiles for characters not in the scene."""
    present_l = {p.strip().lower() for p in present if p and p.strip()}
    if not present_l:  # unknown cast — don't strip anything
        return text

    def _keep(match: re.Match[str]) -> str:
        return match.group(0) if _header_names(match.group("header")) & present_l else ""

    scoped = _CHAR_BLOCK_RE.sub(_keep, text)
    return re.sub(r"\n{3,}", "\n\n", scoped).strip()


def _load_dialogue_rules(present: Iterable[str]) -> str | None:
    """Read the authoritative dialogue rules fresh for each draft (so edits take effect on the next
    scene) and scope per-character profiles to the characters present. Relative paths resolve from
    the project root, then the CWD."""
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
        return _scope_dialogue_rules(text, present) or None
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
    target_words: int | None = None                             # per-scene length guide for the drafter
    exemplars: list[str] = field(default_factory=list)
    dialogue_rules: str | None = None                           # authoritative dialogue source of truth
    canon: list[str] = field(default_factory=list)              # beat-scoped RAG over canon
    pov_summary: str | None = None                              # what this POV knows
    ledger: dict[str, dict[str, Any]] = field(default_factory=dict)  # Oracle read of hard stats
    contract: dict[str, Any] | None = None                      # packet constraints the writer obeys
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

    # Tolerate duplicate rows: a re-run plan-call / re-enqueue can leave more than one Chapter for a
    # (book, chapter_no) or more than one Beat for a (chapter, scene_no). scalar_one[_or_none]() raises
    # MultipleResultsFound on those, which used to fail the draft before it began — so pick a canonical
    # row deterministically (lowest id) instead of crashing the scene.
    chapter = (await session.execute(
        select(Chapter)
        .where(Chapter.book_id == book_id, Chapter.chapter_no == job.chapter_no)
        .order_by(Chapter.id)
    )).scalars().first()
    if chapter is None:
        raise ValueError(f"no chapter {job.chapter_no} for this book")

    beats = (await session.execute(
        select(Beat)
        .where(Beat.chapter_id == chapter.id, Beat.scene_no == job.scene_no)
        .order_by(Beat.id)
    )).scalars().all()
    # Prefer an approved beat when duplicates exist (it's the one the human signed off / a packet derived).
    beat = next((b for b in beats if b.status == BeatStatus.APPROVED), beats[0] if beats else None)
    if beat is None:
        raise ValueError(
            f"no beat for ch{job.chapter_no} sc{job.scene_no} — propose/approve beats first (gate 1)"
        )

    # limit(1): tolerate a duplicate POV profile for (book, character) — a re-seed can leave two, and
    # scalar_one_or_none() would raise MultipleResultsFound and fail every scene of this POV.
    profile = (await session.execute(
        select(PovProfile)
        .where(PovProfile.book_id == book_id, PovProfile.character == chapter.pov)
        .order_by(PovProfile.id)
        .limit(1)
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
        target_words=beat.target_words,
        # General craft is always-on; per-character profiles are scoped to the POV + cast on the page.
        dialogue_rules=_load_dialogue_rules([chapter.pov, *(beat.characters_present or [])]),
    )

    # Oracle: current hard state for each character present (drives the continuity reviewer).
    oracle = Oracle(session)
    ledger: dict[str, dict[str, Any]] = {}
    for character in ctx.characters_present:
        stats = await oracle.current(book_id=book_id, character=character)
        if stats:
            ledger[character] = stats
    ctx.ledger = ledger

    # Voice: the author's curated few-shot exemplars for this POV (LEARNING_FROM_EDITS Tier 2). The
    # drafter consumes ctx.exemplars (_voice_system); this is the wire that loads them.
    ctx.exemplars = await _load_exemplars(session, profile, exclude_scene_id=job.target_scene_id)

    # Memory: beat-scoped canon, the POV's rolling summary, the previous approved scene's tail.
    retrieval_query = " ".join(p for p in [beat.beat_text or "", *ctx.characters_present] if p)
    ctx.canon = await canon_rag.retrieve(session, book_id=book_id, query=retrieval_query, k=6)
    ctx.pov_summary = await summaries.pov_summary(session, book_id=book_id, pov=chapter.pov)
    ctx.prior_scene_tail = await _prior_tail(session, chapter_id=chapter.id, scene_no=job.scene_no)

    # Contract-first (Phase 2): a beat derived from a packet scene_seed is bound by that packet's
    # constraints. Read them live from the approved packet so a packet edit takes effect next draft.
    ctx.contract = await _load_contract(
        session, chapter_id=chapter.id, scene_seed_id=beat.scene_seed_id
    )

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


async def _load_exemplars(
    session: AsyncSession, profile: PovProfile | None, *, exclude_scene_id: uuid.UUID | None
) -> list[str]:
    """Load the POV's curated voice exemplars — the wire that was cut (LEARNING_FROM_EDITS Tier 2).

    Fetch the prose of `profile.exemplar_scene_ids` so the drafter few-shots on the author's own approved
    prose for this POV. Stored as ARRAY(Text), so ids arrive as strings — parse defensively, skip the
    scene being revised, preserve the author's curated order, and cap count + per-passage length so the
    exemplars can't crowd the token budget.
    """
    if profile is None or not profile.exemplar_scene_ids:
        return []
    ids: list[uuid.UUID] = []
    for raw in profile.exemplar_scene_ids:  # stored as text (ARRAY(Text)) — parse back to UUID
        try:
            sid = uuid.UUID(raw)
        except (ValueError, AttributeError, TypeError):
            continue  # a malformed id is skipped, not fatal
        if sid != exclude_scene_id and sid not in ids:
            ids.append(sid)
    if not ids:
        return []

    rows = (await session.execute(
        select(Scene.id, Scene.prose).where(Scene.id.in_(ids))
    )).all()
    prose_by_id = {sid: prose for sid, prose in rows if prose}

    exemplars: list[str] = []
    for sid in ids:  # author's curated order, not the IN-query's order
        prose = prose_by_id.get(sid)
        if not prose:
            continue
        exemplars.append(prose[: settings.exemplar_max_chars])
        if len(exemplars) >= settings.exemplar_max_count:
            break
    return exemplars


# Chapter-wide constraints (apply to every scene) + the per-seed scene constraints, lifted from the
# approved packet body into the drafter's contract. Kept as a flat dict the drafter formats verbatim.
_CONTRACT_CHAPTER_KEYS: tuple[str, ...] = (
    "allowed_knowledge", "forbidden_knowledge", "required_reveals", "forbidden_reveals",
    "canon_locks", "roster_locks", "relationship_locks", "timeline_locks",
    "allowed_ui_concepts", "forbidden_ui_concepts",
)
_CONTRACT_SCENE_KEYS: tuple[str, ...] = ("required_beats", "forbidden_beats", "exit_state", "scene_type")


async def _load_contract(
    session: AsyncSession, *, chapter_id: uuid.UUID, scene_seed_id: uuid.UUID | None
) -> dict[str, Any] | None:
    """Assemble the drafting contract for a seed-linked beat from the chapter's APPROVED packet:
    chapter-wide knowledge/reveal/lock rules plus this seed's scene-level constraints. Returns None for
    a plan-call beat (no scene_seed_id) or when no approved packet exists."""
    if scene_seed_id is None:
        return None
    body = (await session.execute(
        select(ChapterPacket.body)
        .where(ChapterPacket.chapter_id == chapter_id, ChapterPacket.status == PacketStatus.APPROVED)
        .order_by(ChapterPacket.created_at.desc())
        .limit(1)
    )).scalar_one_or_none()
    if not isinstance(body, dict):
        return None

    contract: dict[str, Any] = {}
    for key in _CONTRACT_CHAPTER_KEYS:
        value = body.get(key)
        if isinstance(value, list) and any(str(v).strip() for v in value):
            contract[key] = [str(v).strip() for v in value if str(v).strip()]

    seed = next(
        (s for s in (body.get("scene_seeds") or [])
         if isinstance(s, dict) and str(s.get("seed_id")) == str(scene_seed_id)),
        None,
    )
    if isinstance(seed, dict):
        for key in _CONTRACT_SCENE_KEYS:
            value = seed.get(key)
            if isinstance(value, list) and any(str(v).strip() for v in value):
                contract[key] = [str(v).strip() for v in value if str(v).strip()]
            elif isinstance(value, str) and value.strip():
                contract[key] = value.strip()

    return contract or None


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
