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
from dominion.shared.enums import (
    BeatStatus,
    Decision,
    ScenePacketStatus,
    SceneStatus,
)
from dominion.shared.models import (
    Approval,
    Beat,
    Chapter,
    ChapterPacket,
    Job,
    PovProfile,
    Run,
    Scene,
    ScenePacket,
)
from dominion.workers.budget import TokenBudget
from dominion.workers.memory import owner_router, retrieval, summaries
from dominion.workers.oracle import Oracle


class ScenePacketRequiredError(RuntimeError):
    """A draft job referenced a ScenePacket that is missing, not approved, or stale. Drafting fails
    closed rather than silently falling back to the chapter packet (scene-packet contract system)."""


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
    # Scene-packet contract system: the scene-local contract and its projections. `contract` stays as
    # the drafter's flat MUST/MUST-NOT view; these named fields are the structured source.
    scene_packet_id: uuid.UUID | None = None
    chapter_contract: dict[str, Any] | None = None              # the approved chapter packet body
    scene_contract: dict[str, Any] | None = None               # the full approved ScenePacket body
    reader_state_contract: dict[str, Any] | None = None        # reader/POV knowledge + reveals + mysteries
    word_budget: dict[str, Any] | None = None                  # planned per-scene word budget
    reviewer_contract: dict[str, Any] | None = None            # per-lane reviewer instructions + traps
    prior_scene_tail: str | None = None                         # in-chapter continuity
    prior_prose: str | None = None                              # revise: the draft being revised
    revise_feedback: str | None = None                          # revise: the author's notes


async def assemble_context(session: AsyncSession, job: Job) -> SceneContext:
    """Load everything a draft (or revision) needs, routing by the job's DIRECT ids (book/chapter/beat/
    scene_packet). `run_id` is provenance only; the legacy (run_id, chapter_no, scene_no) lookup is a
    fallback for jobs created before direct-ID routing. A job that names a ScenePacket must reference an
    approved, non-stale one — otherwise drafting fails closed (no silent chapter-packet fallback)."""
    # book id: direct, else via the run.
    book_id = job.book_id
    if book_id is None:
        if job.run_id is None:
            raise ValueError("job is missing book_id and run_id — cannot resolve the book")
        book_id = (await session.execute(
            select(Run.book_id).where(Run.id == job.run_id)
        )).scalar_one()

    # chapter: direct id, else legacy (book, chapter_no). Tolerate duplicate rows by picking the
    # lowest-id canonical row instead of crashing the scene.
    chapter: Chapter | None = None
    if job.chapter_id is not None:
        chapter = await session.get(Chapter, job.chapter_id)
    if chapter is None and job.chapter_no is not None:
        chapter = (await session.execute(
            select(Chapter)
            .where(Chapter.book_id == book_id, Chapter.chapter_no == job.chapter_no)
            .order_by(Chapter.id)
        )).scalars().first()
    if chapter is None:
        raise ValueError("no chapter for this job (missing chapter_id / chapter_no)")

    # beat: direct id, else legacy (chapter, scene_no).
    beat: Beat | None = None
    if job.beat_id is not None:
        beat = await session.get(Beat, job.beat_id)
    if beat is None:
        scene_no = job.scene_no
        if scene_no is None:
            raise ValueError("job is missing beat_id and scene_no — cannot resolve the beat")
        beats = (await session.execute(
            select(Beat)
            .where(Beat.chapter_id == chapter.id, Beat.scene_no == scene_no)
            .order_by(Beat.id)
        )).scalars().all()
        beat = next((b for b in beats if b.status == BeatStatus.APPROVED), beats[0] if beats else None)
    if beat is None:
        raise ValueError(
            f"no beat for ch{chapter.chapter_no} sc{job.scene_no} — derive/approve a scene packet first"
        )

    # limit(1): tolerate a duplicate POV profile for (book, character) — a re-seed can leave two, and
    # scalar_one_or_none() would raise MultipleResultsFound and fail every scene of this POV.
    profile = (await session.execute(
        select(PovProfile)
        .where(PovProfile.book_id == book_id, PovProfile.character == chapter.pov)
        .order_by(PovProfile.id)
        .limit(1)
    )).scalar_one_or_none()

    scene_no = beat.scene_no if beat.scene_no is not None else job.scene_no
    ctx = SceneContext(
        book_id=book_id,
        chapter_id=chapter.id,
        pov=chapter.pov,
        scene_no=scene_no,
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
    # Hybrid retrieval with owner-file precedence (relationship invariants, cast, mechanics dossiers
    # win over a semantic guess) — the same authority path the scene-packet builder uses. Owner-forced
    # snippets lead so the drafter sees canon before supporting context. Falls back to plain semantic
    # bodies if nothing is owner-routed.
    retrieval_query = " ".join(p for p in [beat.beat_text or "", *ctx.characters_present] if p)
    routing = owner_router.route(retrieval_query, characters=ctx.characters_present)
    snippets = await retrieval.retrieve_hybrid(
        session, book_id=book_id, query=retrieval_query,
        owner_topics=routing.owner_topics, required_doc_paths=routing.doc_paths, k=6,
    )
    owner_first = [s for s in snippets if s["retrieval_reason"] == "owner_forced"]
    rest = [s for s in snippets if s["retrieval_reason"] != "owner_forced"]
    ctx.canon = [s["body"] for s in [*owner_first, *rest] if s["body"]]
    ctx.pov_summary = await summaries.pov_summary(session, book_id=book_id, pov=chapter.pov)
    ctx.prior_scene_tail = await _prior_tail(session, chapter_id=chapter.id, scene_no=scene_no)

    # Scene-packet contract system: drafting is fail-closed on the scene-local contract. The job's (or
    # beat's) ScenePacket must be approved + non-stale; it is the authority for reader/POV knowledge,
    # reveals, mysteries, word budget, and reviewer instructions. There is no chapter-packet fallback —
    # a beat with no scene packet cannot be drafted (derive + approve one first).
    sp_id = job.scene_packet_id or beat.scene_packet_id
    if sp_id is None:
        raise ScenePacketRequiredError(
            f"no scene packet for ch{chapter.chapter_no} sc{scene_no} — derive and approve a scene "
            "packet before drafting"
        )
    await _load_scene_packet(session, ctx, scene_packet_id=sp_id)

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


# Chapter-level locks that still bind every scene; lifted from the chapter packet into the flat
# drafter contract alongside the scene-local constraints.
_CHAPTER_LOCK_KEYS: tuple[str, ...] = (
    "canon_locks", "roster_locks", "relationship_locks", "timeline_locks",
    "allowed_ui_concepts", "forbidden_ui_concepts",
)


def _str_list(value: Any) -> list[str]:
    return [str(v).strip() for v in value if str(v).strip()] if isinstance(value, list) else []


def _flat_contract(scene_body: dict[str, Any], chapter_body: dict[str, Any]) -> dict[str, Any]:
    """Translate the structured ScenePacket (+ chapter locks) into the flat MUST/MUST-NOT view the
    drafter's _contract_block already formats. Scene-local reveal/hidden rules become the reveal
    constraints; chapter locks remain immutable."""
    hidden = scene_body.get("must_remain_hidden") or {}
    learned = scene_body.get("learned_during_scene") or {}
    pov_perms = scene_body.get("pov_permissions") or {}
    contract: dict[str, Any] = {}
    forbidden_reveals = _str_list(hidden.get("reader")) + _str_list(hidden.get("all_surface_prose"))
    if forbidden_reveals:
        contract["forbidden_reveals"] = forbidden_reveals
    forbidden_knowledge = _str_list(hidden.get("pov")) + _str_list(pov_perms.get("must_not_know"))
    if forbidden_knowledge:
        contract["forbidden_knowledge"] = forbidden_knowledge
    required_reveals = _str_list(learned.get("reader_must_learn"))
    if required_reveals:
        contract["required_reveals"] = required_reveals
    for key in ("required_beats", "forbidden_beats"):
        if vals := _str_list(scene_body.get(key)):
            contract[key] = vals
    if isinstance(scene_body.get("exit_state"), str) and scene_body["exit_state"].strip():
        contract["exit_state"] = scene_body["exit_state"].strip()
    for key in _CHAPTER_LOCK_KEYS:
        if vals := _str_list(chapter_body.get(key)):
            contract[key] = vals
    return contract


async def _load_scene_packet(
    session: AsyncSession, ctx: SceneContext, *, scene_packet_id: uuid.UUID
) -> None:
    """Load the approved, non-stale ScenePacket and populate the scene-local contract fields. Fails
    closed (ScenePacketRequiredError) when the packet is missing, unapproved, or stale."""
    sp = await session.get(ScenePacket, scene_packet_id)
    if sp is None:
        raise ScenePacketRequiredError(f"no scene packet {scene_packet_id} for this draft job")
    if sp.status == ScenePacketStatus.STALE:
        raise ScenePacketRequiredError(
            f"scene packet {scene_packet_id} is stale ({sp.stale_reason or 'inputs changed'}) — "
            "re-derive or re-approve it before drafting"
        )
    if sp.status != ScenePacketStatus.APPROVED:
        raise ScenePacketRequiredError(
            f"scene packet {scene_packet_id} is {sp.status}, not approved — approve it before drafting"
        )

    body = sp.body or {}
    chapter_body = (await session.execute(
        select(ChapterPacket.body).where(ChapterPacket.id == sp.chapter_packet_id)
    )).scalar_one_or_none()
    chapter_body = chapter_body if isinstance(chapter_body, dict) else {}

    ctx.scene_packet_id = sp.id
    ctx.scene_contract = body
    ctx.chapter_contract = chapter_body
    ctx.word_budget = body.get("word_budget") if isinstance(body.get("word_budget"), dict) else None
    ctx.reader_state_contract = {
        "known_before_scene": body.get("known_before_scene") or {},
        "learned_during_scene": body.get("learned_during_scene") or {},
        "must_remain_hidden": body.get("must_remain_hidden") or {},
        "pov_permissions": body.get("pov_permissions") or {},
        "intentional_mysteries": body.get("intentional_mysteries") or [],
        "reviewer_false_positive_traps": body.get("reviewer_false_positive_traps") or [],
    }
    ctx.reviewer_contract = {
        "scene_job": body.get("scene_job"),
        "scene_type": body.get("scene_type"),
        "required_beats": _str_list(body.get("required_beats")),
        "forbidden_beats": _str_list(body.get("forbidden_beats")),
        "reviewer_false_positive_traps": body.get("reviewer_false_positive_traps") or [],
        "reviewer_instructions": body.get("reviewer_instructions") or {},
        "word_budget": ctx.word_budget,
    }
    ctx.contract = _flat_contract(body, chapter_body) or None


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
