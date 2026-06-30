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
from dominion.shared.enums import ScenePacketStatus, ScenePacketVerdict
from dominion.shared.models import Beat, Chapter, ChapterPacket, ScenePacket, Summary
from dominion.workers import telemetry, telemetry_db
from dominion.workers.budget import TokenBudget
from dominion.workers.length import planner as length_planner
from dominion.workers.llm import estimate_tokens
from dominion.workers.memory import owner_router, retrieval
from dominion.workers.pov import effective_pov
from dominion.workers.scene_packet import approval_policy
from dominion.workers.scene_packet import author as author_mod
from dominion.workers.scene_packet import author_sections as author_sections_mod
from dominion.workers.scene_packet import hash as hash_mod
from dominion.workers.scene_packet import inputs as sp_inputs
from dominion.workers.scene_packet import qa as qa_mod
from dominion.workers.scene_packet.parse import valid_scene_packet_body

log = structlog.get_logger()

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
    # This scene's EFFECTIVE POV (beat override, else chapter POV) and that POV's rolling summary —
    # resolved per scene in Phase 1 (DB-bound) so the LLM fan-out in Phase 2 needs no further reads.
    pov: str
    pov_summary: str | None


async def _author_then_qa(
    item: _SceneWork,
    *,
    chapter_packet_body: dict[str, Any],
    pov: str,
    pov_summary: str | None,
    omniscient_summary: str | None,
    sink: telemetry.TelemetrySink,
    book_id: str,
    chapter_id: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, str | None]:
    """One scene's Author then QA. QA reads the author's output, so the two stay ordered; only
    different scenes run concurrently. Fails CLOSED: any author/QA error returns a None in the slot
    that makes `_status_for` block the packet, never raising into the gather — but the failure's real
    cause is captured (3rd return value) so the blocked packet names *why*, not just "incomplete body".

    Each call runs inside a telemetry `call_context` tagged with this scene's dimensions, so its cache/
    usage/truncation lands in the shared `sink` (persisted later) under the right stage + scene."""
    error_detail: str | None = None

    def _ctx(stage: str) -> telemetry.CallContext:
        return telemetry.CallContext(
            sink=sink, stage=stage, book_id=book_id, chapter_id=chapter_id,
            scene_no=item.scene_no, seed_id=str(item.seed_id),
        )

    # Sectioned author fans the contract into concurrent section calls (the default — fixes the
    # output-bound >50s latency); the monolithic author is the fallback path. Both share a signature and
    # the same fail-closed body contract, so the rest of the flow is identical. Every section call still
    # records under this one `scene_packet_author` telemetry context (stage unchanged), so the Desk
    # panels just see more, shorter author rows per scene.
    author_fn = (
        author_sections_mod.author_scene_packet_sectioned
        if settings.scene_packet_author_sectioned
        else author_mod.author_scene_packet
    )
    try:
        with telemetry.call_context(_ctx("scene_packet_author")):
            scene_body: dict[str, Any] | None = await author_fn(
                pov=pov, chapter_packet_body=chapter_packet_body, scene_seed=item.seed,
                word_budget=item.word_budget, pov_summary=pov_summary,
                omniscient_summary=omniscient_summary,
                owner_snippets=item.owner_snippets or None, canon_snippets=item.canon_snippets or None,
                budget=item.budget,
            )
    except Exception as exc:  # noqa: BLE001 — any author failure fails this scene closed
        log.error("scene_packet.author_failed", seed=str(item.seed_id), error=str(exc))
        scene_body = None
        error_detail = str(exc)

    qa: dict[str, Any] | None = None
    if isinstance(scene_body, dict) and valid_scene_packet_body(scene_body):
        try:
            with telemetry.call_context(_ctx("scene_packet_qa")):
                qa = await qa_mod.qa_scene_packet(
                    scene_body, chapter_packet_body=chapter_packet_body, budget=item.budget
                )
        except Exception as exc:  # noqa: BLE001
            log.error("scene_packet.qa_failed", seed=str(item.seed_id), error=str(exc))
            qa = None
            error_detail = str(exc)
    return scene_body, qa, error_detail


def _as_str_list(value: Any) -> list[str]:
    return [str(v).strip() for v in value if str(v).strip()] if isinstance(value, list) else []


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



def _derive_context_budget_report(
    *,
    chapter_packet_body: dict[str, Any],
    work: list[_SceneWork],
    omniscient_summary: str | None,
) -> dict[str, Any]:
    author_sections = [author_sections_mod._section_directive(sec) for sec in author_sections_mod._SECTIONS]
    report: dict[str, Any] = {
        "context_window_budget": settings.scene_packet_context_window_budget,
        "chapter_packet": estimate_tokens(author_mod._compact(chapter_packet_body)),
        "omniscient_summary": estimate_tokens(omniscient_summary or ""),
        "max_section_directive": max((estimate_tokens(s) for s in author_sections), default=0),
        "author_output_allowance": max((sec.max_tokens for sec in author_sections_mod._SECTIONS), default=0),
        "qa_output_allowance": settings.scene_packet_qa_max_tokens,
        "scenes": [],
    }
    for item in work:
        scene_seed = estimate_tokens(author_mod._compact(item.seed))
        word_budget = estimate_tokens(author_mod._compact(item.word_budget))
        owner = sum(estimate_tokens(s) for s in item.owner_snippets)
        canon = sum(estimate_tokens(s) for s in item.canon_snippets)
        pov_summary = estimate_tokens(item.pov_summary or "")
        scene_total = scene_seed + word_budget + owner + canon + pov_summary
        report["scenes"].append({
            "scene_no": item.scene_no,
            "pov": item.pov,
            "pov_summary": pov_summary,
            "scene_seed": scene_seed,
            "word_budget": word_budget,
            "owner_snippets": owner,
            "canon_snippets": canon,
            "scene_context_total": scene_total,
        })
    return report


async def _prime_shared_prefixes(
    *,
    work: list[_SceneWork],
    chapter_packet_body: dict[str, Any],
    omniscient_summary: str | None,
    sink: telemetry.TelemetrySink,
    book_id: str,
    chapter_id: str,
) -> None:
    seen_author_prefixes: set[tuple[str | None, str | None]] = set()
    for item in work:
        key = (item.pov_summary, omniscient_summary)
        if key in seen_author_prefixes:
            continue
        seen_author_prefixes.add(key)
        with telemetry.call_context(telemetry.CallContext(
            sink=sink, stage="scene_packet_author_prefix_prime", book_id=book_id, chapter_id=chapter_id,
            scene_no=None, seed_id=None,
        )):
            await author_sections_mod.prime_author_shared_prefix(
                chapter_packet_body=chapter_packet_body, pov_summary=item.pov_summary,
                omniscient_summary=omniscient_summary,
                budget=TokenBudget(max_tokens=settings.scene_packet_prefix_prime_token_budget),
            )

    with telemetry.call_context(telemetry.CallContext(
        sink=sink, stage="scene_packet_qa_prefix_prime", book_id=book_id, chapter_id=chapter_id,
        scene_no=None, seed_id=None,
    )):
        await qa_mod.prime_qa_shared_prefix(
            chapter_packet_body,
            budget=TokenBudget(max_tokens=settings.scene_packet_prefix_prime_token_budget),
        )


async def derive_scene_packets(
    session: AsyncSession, *, packet: ChapterPacket, budget: TokenBudget | None = None
) -> dict[str, Any]:
    """Build/refresh a ScenePacket per scene seed of the approved chapter `packet`. Returns counts
    {created, updated, blocked, stale}. The caller commits.

    Idempotent per seed_id: re-deriving updates in place. A derived ScenePacket whose body the human
    already approved is refreshed but flips back to `proposed` (re-approval required) only when its
    source_hash changes; an unchanged input leaves an approved packet untouched.
    """
    body: dict[str, Any] = packet.body or {}
    seeds = [s for s in (body.get("scene_seeds") or []) if isinstance(s, dict) and s.get("seed_id")]
    counts: dict[str, Any] = {"created": 0, "updated": 0, "blocked": 0, "stale": 0}
    if not seeds:
        return counts

    # Each scene's Author+QA pair normally gets its own scene-local work budget. A caller-provided
    # budget intentionally preserves the older shared scene-work override for tests/special callers, but
    # it does NOT include chapter-prefix prime calls; those always use scene_packet_prefix_prime_token_budget
    # so cache initialization never consumes Scene 1's scene-local budget.
    external_scene_budget = budget
    chapter_target, chapter_max = sp_inputs.chapter_targets(body, seeds)
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
    # Each scene drafts in its EFFECTIVE POV: the beat's per-scene override (Beat.pov) when set, else the
    # chapter POV. Load the chapter + its beats keyed by scene_no so an overridden scene gets that POV's
    # actual rolling summary, not just a label. When a scene_no has multiple beat rows, prefer one that
    # carries an override so a per-scene POV isn't lost to a sibling row.
    chapter = await session.get(Chapter, packet.chapter_id)
    beats_by_scene: dict[int, Beat] = {}
    for b in (await session.execute(
        select(Beat).where(Beat.chapter_id == packet.chapter_id)
    )).scalars():
        prev = beats_by_scene.get(b.scene_no)
        if prev is None or ((b.pov or "").strip() and not (prev.pov or "").strip()):
            beats_by_scene[b.scene_no] = b
    # pov_summary is fetched once per DISTINCT effective POV (cache by pov string); most chapters have a
    # single POV, so this is one fetch — overrides add at most one more per extra POV.
    pov_summary_cache: dict[str, str | None] = {}

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

        # This scene's EFFECTIVE POV (beat override, else chapter POV) feeds the author + summary; the raw
        # override is also a derivation input (folded into source_hash) so changing a beat's POV re-opens an
        # already-approved packet. No beat / blank override => inherits chapter POV, and the hash is
        # unchanged from before this input existed (so the upgrade doesn't mass-invalidate packets).
        beat = beats_by_scene.get(scene_no)
        scene_pov = effective_pov(beat, chapter) if chapter is not None else ""
        pov_override = (beat.pov or "").strip() if beat is not None else ""

        prior_keys = await sp_inputs.prior_scene_keys(session, chapter_id=packet.chapter_id, scene_no=scene_no)
        src_hash = hash_mod.source_hash(
            chapter_packet_id=packet.id, chapter_packet_body=body, scene_seed=seed,
            chapter_word_budget=word_budget, prior_scene_keys=prior_keys,
            scene_pov=pov_override or None,
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

        # That POV's rolling summary, fetched once per DISTINCT effective POV (cache by pov string); most
        # chapters have a single POV, so this is one fetch. (scene_pov is resolved above, before the hash.)
        if scene_pov not in pov_summary_cache:
            pov_summary_cache[scene_pov] = (
                await _pov_summary(session, book_id=packet.book_id, pov=scene_pov) if scene_pov else None
            )

        work.append(_SceneWork(
            seed=seed, seed_id=seed_id, scene_no=scene_no, word_budget=word_budget,
            src_hash=src_hash, row=row,
            owner_snippets=[s["body"] for s in snippets if s["retrieval_reason"] == "owner_forced"],
            canon_snippets=[s["body"] for s in snippets if s["retrieval_reason"] != "owner_forced"],
            # Fresh per-scene budget unless the caller explicitly supplied a shared scene-work budget.
            # Prefix-prime calls are always charged to the separate prefix-prime budget.
            budget=external_scene_budget or TokenBudget(max_tokens=settings.scene_token_budget),
            pov=scene_pov, pov_summary=pov_summary_cache[scene_pov],
        ))

    # ---- Phase 2 (concurrent, LLM only): the scenes are independent, so their Author+QA pairs fan out
    # under a concurrency cap. No DB access happens here (each task only touches its own _SceneWork).
    # One shared telemetry sink collects every call's cache/usage/truncation (tagged per scene+stage
    # inside _author_then_qa); it is flushed to the DB in Phase 3.
    sem = asyncio.Semaphore(max(1, settings.scene_packet_concurrency))
    sink = telemetry.TelemetrySink()
    book_id_str, chapter_id_str = str(packet.book_id), str(packet.chapter_id)
    counts["context_budget_report"] = _derive_context_budget_report(
        chapter_packet_body=body, work=work, omniscient_summary=omniscient,
    )

    await _prime_shared_prefixes(
        work=work, chapter_packet_body=body, omniscient_summary=omniscient, sink=sink,
        book_id=book_id_str, chapter_id=chapter_id_str,
    )

    async def _run(item: _SceneWork) -> tuple[dict[str, Any] | None, dict[str, Any] | None, str | None]:
        async with sem:
            return await _author_then_qa(
                item, chapter_packet_body=body, pov=item.pov,
                pov_summary=item.pov_summary, omniscient_summary=omniscient,
                sink=sink, book_id=book_id_str, chapter_id=chapter_id_str,
            )

    # Shared chapter prefixes are already primed under a prefix budget, so Scene 1 no longer needs to
    # run first or pay chapter-level cache creation under its scene-local work budget.
    results = await asyncio.gather(*(_run(item) for item in work))

    # ---- Phase 3 (serial, DB): persist each scene's verdict. Order-independent — no task read another
    # scene's freshly-derived packet, so the write order doesn't change any result.
    for item, (scene_body, qa, error_detail) in zip(work, results, strict=True):
        status, blocked_reason = approval_policy.status_after_author_qa(scene_body, qa, error_detail)
        persisted_body = scene_body if isinstance(scene_body, dict) else {"blocked_reason": blocked_reason}
        qa_warnings = (
            {"residual_risks": qa["residual_risks"], "issues": qa["issues"]} if qa
            else {"residual_risks": [], "blocked_reason": blocked_reason}
        )
        if status == ScenePacketStatus.BLOCKED and blocked_reason:
            qa_warnings = {**qa_warnings, "blocked_reason": blocked_reason}

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

    # One run_id for every call this derive made, so the Desk can isolate this run (Packets panel) and
    # build a per-run history (Telemetry tab) instead of reading one ever-growing cumulative total.
    telemetry_db.persist_sink(
        session, sink, run_id=uuid.uuid4(), book_id=packet.book_id, chapter_id=packet.chapter_id
    )
    return counts
