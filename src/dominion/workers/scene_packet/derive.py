"""Derive one ScenePacket per scene seed from an approved ChapterPacket (scene-packet contract system).

Flow (DESIGN target architecture):

    ChapterPacket approved → Length Planner → ScenePacket Builder (one per seed) → ScenePacket QA → persist

Each ScenePacket is keyed by its seed's stable `seed_id` so re-deriving after a chapter-packet edit
updates in place instead of duplicating. The build is FAIL-CLOSED per scene: a malformed author body
or an unusable QA verdict persists a `blocked` ScenePacket (visible to the human) rather than a partial
contract. The word budget comes from the deterministic Length Planner, never the model.

`source_hash` records every input the packet was derived from, so staleness is detectable later.
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dominion.shared.config import settings
from dominion.shared.enums import ScenePacketStatus, ScenePacketVerdict, SceneStatus
from dominion.shared.models import Chapter, ChapterPacket, Scene, ScenePacket, Summary
from dominion.workers.budget import TokenBudget
from dominion.workers.length import planner as length_planner
from dominion.workers.memory import owner_router, retrieval
from dominion.workers.scene_packet import author as author_mod
from dominion.workers.scene_packet import hash as hash_mod
from dominion.workers.scene_packet import qa as qa_mod
from dominion.workers.scene_packet.parse import valid_scene_packet_body

log = structlog.get_logger()

_DEFAULT_SCENE_TARGET = 1500
_CANON_K = 6


@dataclass
class _SceneWork:
    """One scene's fully-assembled inputs, gathered serially (Phase 1) so the concurrent Author+QA
    fan-out (Phase 2) never touches the shared AsyncSession."""
    seed: dict[str, Any]
    seed_id: uuid.UUID
    scene_no: int
    word_budget: dict[str, Any]
    src_hash: str
    row: ScenePacket | None
    owner_snippets: list[str]
    canon_snippets: list[str]
    budget: TokenBudget


async def _author_then_qa(
    item: _SceneWork,
    *,
    chapter_packet_body: dict[str, Any],
    pov: str,
    pov_summary: str | None,
    omniscient_summary: str | None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """One scene's Author then QA. QA reads the author's output, so the two stay ordered; only
    different scenes run concurrently. Fails CLOSED: any author/QA error returns a None in the slot
    that makes `_status_for` block the packet, never raising into the gather."""
    try:
        scene_body = await author_mod.author_scene_packet(
            pov=pov, chapter_packet_body=chapter_packet_body, scene_seed=item.seed,
            word_budget=item.word_budget, pov_summary=pov_summary,
            omniscient_summary=omniscient_summary,
            owner_snippets=item.owner_snippets or None, canon_snippets=item.canon_snippets or None,
            budget=item.budget,
        )
    except Exception as exc:  # noqa: BLE001 — any author failure fails this scene closed
        log.error("scene_packet.author_failed", seed=str(item.seed_id), error=str(exc))
        scene_body = None

    qa: dict[str, Any] | None = None
    if isinstance(scene_body, dict) and valid_scene_packet_body(scene_body):
        try:
            qa = await qa_mod.qa_scene_packet(
                scene_body, chapter_packet_body=chapter_packet_body, budget=item.budget
            )
        except Exception as exc:  # noqa: BLE001
            log.error("scene_packet.qa_failed", seed=str(item.seed_id), error=str(exc))
            qa = None
    return scene_body, qa


def _as_str_list(value: Any) -> list[str]:
    return [str(v).strip() for v in value if str(v).strip()] if isinstance(value, list) else []


def _chapter_targets(body: dict[str, Any], seeds: list[dict[str, Any]]) -> tuple[int, int | None]:
    """Chapter target + optional hard cap. Prefer an explicit chapter figure, else sum the seeds'
    own targets, else a per-scene default."""
    target = body.get("chapter_target_words")
    if isinstance(target, int) and target > 0:
        return target, body.get("chapter_max_words") if isinstance(body.get("chapter_max_words"), int) else None
    seed_targets = [
        t for s in seeds
        if isinstance((wb := s.get("word_budget")), dict) and isinstance((t := wb.get("target")), int)
    ]
    chapter_target = sum(seed_targets) if seed_targets else _DEFAULT_SCENE_TARGET * len(seeds)
    return chapter_target, None


async def _prior_scene_keys(
    session: AsyncSession, *, chapter_id: uuid.UUID, scene_no: int
) -> list[list[Any]]:
    """Stable keys for the approved scenes before this one — feeds the source hash so a prior-scene
    change marks downstream packets stale."""
    rows = (await session.execute(
        select(Scene.id, Scene.version, Scene.word_count)
        .where(
            Scene.chapter_id == chapter_id,
            Scene.scene_no < scene_no,
            Scene.status == SceneStatus.APPROVED,
        )
        .order_by(Scene.scene_no)
    )).all()
    return [[str(sid), ver, wc] for sid, ver, wc in rows]


async def _omniscient_summary(session: AsyncSession, book_id: uuid.UUID) -> str | None:
    return (await session.execute(
        select(Summary.rolling_summary).where(
            Summary.book_id == book_id, Summary.scope == "omniscient", Summary.pov.is_(None)
        )
    )).scalar_one_or_none()


async def _pov_summary(session: AsyncSession, *, book_id: uuid.UUID, pov: str) -> str | None:
    return (await session.execute(
        select(Summary.rolling_summary)
        .where(Summary.book_id == book_id, Summary.scope == "pov", Summary.pov == pov)
        .order_by(Summary.up_to_scene_id.is_(None))
        .limit(1)
    )).scalar_one_or_none()


def _status_for(body: dict[str, Any] | None, qa: dict[str, Any] | None) -> tuple[str, str | None]:
    """(status, blocked_reason). Fail closed: a thin body or unusable QA blocks the packet; a
    BLOCK_DRAFTING verdict blocks it; otherwise it lands proposed for the human to approve."""
    if not valid_scene_packet_body(body):
        return ScenePacketStatus.BLOCKED, "scene packet author returned an incomplete body"
    if qa is None:
        return ScenePacketStatus.BLOCKED, "scene packet QA returned no usable verdict"
    if qa["verdict"] == ScenePacketVerdict.BLOCK_DRAFTING:
        return ScenePacketStatus.BLOCKED, "scene packet QA blocked drafting"
    return ScenePacketStatus.PROPOSED, None


async def derive_scene_packets(
    session: AsyncSession, *, packet: ChapterPacket, budget: TokenBudget | None = None
) -> dict[str, int]:
    """Build/refresh a ScenePacket per scene seed of the approved chapter `packet`. Returns counts
    {created, updated, blocked, stale}. The caller commits.

    Idempotent per seed_id: re-deriving updates in place. A derived ScenePacket whose body the human
    already approved is refreshed but flips back to `proposed` (re-approval required) only when its
    source_hash changes; an unchanged input leaves an approved packet untouched.
    """
    body: dict[str, Any] = packet.body or {}
    seeds = [s for s in (body.get("scene_seeds") or []) if isinstance(s, dict) and s.get("seed_id")]
    counts = {"created": 0, "updated": 0, "blocked": 0, "stale": 0}
    if not seeds:
        return counts

    # Each scene's Author+QA pair gets its own token budget so deriving a whole chapter doesn't share
    # one per-scene ceiling across N scenes (which exhausted after the first scene or two: the QA call
    # tipped it over, then every later author call started already-over-budget and failed closed —
    # surfacing as "QA returned no usable verdict" on scene 1 and "incomplete body" on the rest). A
    # caller that passes an explicit budget keeps the shared semantics (it's bounding the whole run).
    external_budget = budget
    chapter_target, chapter_max = _chapter_targets(body, seeds)
    budgets = length_planner.plan_word_budgets(
        chapter_target_words=chapter_target, chapter_max_words=chapter_max,
        scene_seeds=seeds, chapter_packet_body=body,
    )

    existing: dict[uuid.UUID, ScenePacket] = {
        sp.scene_seed_id: sp
        for sp in (await session.execute(
            select(ScenePacket).where(
                ScenePacket.chapter_id == packet.chapter_id, ScenePacket.scene_seed_id.isnot(None)
            )
        )).scalars()
        if sp.scene_seed_id is not None
    }

    omniscient = await _omniscient_summary(session, packet.book_id)
    pov = await _chapter_pov(session, packet.chapter_id)
    pov_summary = await _pov_summary(session, book_id=packet.book_id, pov=pov) if pov else None

    # ---- Phase 1 (serial, DB): assemble each scene's inputs. The shared AsyncSession is not safe for
    # concurrent use, so every DB read (prior-scene keys, hybrid retrieval) is done here, up front.
    work: list[_SceneWork] = []
    for seed in seeds:
        try:
            seed_id = uuid.UUID(str(seed["seed_id"]))
        except (ValueError, AttributeError, TypeError):
            continue
        scene_no = int(seed["scene_no"]) if isinstance(seed.get("scene_no"), int) else 0
        word_budget = budgets.get(str(seed_id), {})

        prior_keys = await _prior_scene_keys(session, chapter_id=packet.chapter_id, scene_no=scene_no)
        src_hash = hash_mod.source_hash(
            chapter_packet_id=packet.id, chapter_packet_body=body, scene_seed=seed,
            chapter_word_budget=word_budget, prior_scene_keys=prior_keys,
        )

        row = existing.get(seed_id)
        # An approved packet whose inputs are unchanged needs no rebuild.
        if row is not None and row.status == ScenePacketStatus.APPROVED and row.source_hash == src_hash:
            continue

        # Owner files win over semantic hits: force-inject the relevant dossiers/invariants, then add
        # supporting hybrid retrieval (keyword + semantic), deterministically split for the author.
        query = " ".join([str(seed.get("scene_job") or ""), *_as_str_list(seed.get("required_beats"))])
        routing = owner_router.route(query, characters=_as_str_list(body.get("characters_present")))
        snippets = await retrieval.retrieve_hybrid(
            session, book_id=packet.book_id, query=query,
            owner_topics=routing.owner_topics, required_doc_paths=routing.doc_paths, k=_CANON_K,
        )
        work.append(_SceneWork(
            seed=seed, seed_id=seed_id, scene_no=scene_no, word_budget=word_budget,
            src_hash=src_hash, row=row,
            owner_snippets=[s["body"] for s in snippets if s["retrieval_reason"] == "owner_forced"],
            canon_snippets=[s["body"] for s in snippets if s["retrieval_reason"] != "owner_forced"],
            # Fresh per-scene budget unless the caller bounded the whole run with one explicit budget.
            budget=external_budget or TokenBudget(max_tokens=settings.scene_token_budget),
        ))

    # ---- Phase 2 (concurrent, LLM only): the scenes are independent, so their Author+QA pairs fan out
    # under a concurrency cap. No DB access happens here (each task only touches its own _SceneWork).
    sem = asyncio.Semaphore(max(1, settings.scene_packet_concurrency))

    async def _run(item: _SceneWork) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        async with sem:
            return await _author_then_qa(
                item, chapter_packet_body=body, pov=pov or "",
                pov_summary=pov_summary, omniscient_summary=omniscient,
            )

    results = await asyncio.gather(*(_run(item) for item in work))

    # ---- Phase 3 (serial, DB): persist each scene's verdict. Order-independent — no task read another
    # scene's freshly-derived packet, so the write order doesn't change any result.
    for item, (scene_body, qa) in zip(work, results, strict=True):
        status, blocked_reason = _status_for(scene_body, qa)
        persisted_body = scene_body if isinstance(scene_body, dict) else {"blocked_reason": blocked_reason}
        qa_warnings = (
            {"residual_risks": qa["residual_risks"], "issues": qa["issues"]} if qa
            else {"residual_risks": [], "blocked_reason": blocked_reason}
        )

        row = item.row
        if row is None:
            row = ScenePacket(
                book_id=packet.book_id, chapter_id=packet.chapter_id,
                chapter_packet_id=packet.id, scene_seed_id=item.seed_id, scene_no=item.scene_no,
            )
            session.add(row)
            counts["created"] += 1
        else:
            counts["updated"] += 1
        row.chapter_packet_id = packet.id
        row.scene_no = item.scene_no
        row.status = status
        row.qa_verdict = qa["verdict"] if qa else ScenePacketVerdict.BLOCK_DRAFTING
        row.qa_warnings = qa_warnings
        row.body = persisted_body
        row.source_hash = item.src_hash
        row.stale_reason = None
        if status == ScenePacketStatus.BLOCKED:
            counts["blocked"] += 1

    return counts


async def _chapter_pov(session: AsyncSession, chapter_id: uuid.UUID) -> str | None:
    return (await session.execute(
        select(Chapter.pov).where(Chapter.id == chapter_id)
    )).scalar_one_or_none()
