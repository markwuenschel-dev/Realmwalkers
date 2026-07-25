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
import hashlib
import uuid
from dataclasses import dataclass
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dominion.shared.config import settings
from dominion.shared.enums import ImportAdoptionStatus, ScenePacketStatus
from dominion.shared.grading import build_grade
from dominion.shared.models import (
    Beat,
    Chapter,
    ChapterPacket,
    ImportAdoption,
    Scene,
    ScenePacket,
    Summary,
)
from dominion.shared.text_match import as_str_list, binding_replacements, project_drafter_fields
from dominion.workers import telemetry, telemetry_db
from dominion.workers.budget import TokenBudget
from dominion.workers.length import planner as length_planner
from dominion.workers.llm import LlmRateLimited, PromptBudgetExceeded, estimate_tokens
from dominion.workers.memory import owner_router, retrieval
from dominion.workers.packet import master
from dominion.workers.pov import effective_pov
from dominion.workers.scene_packet import approval_policy
from dominion.workers.scene_packet import author as author_mod
from dominion.workers.scene_packet import author_sections as author_sections_mod
from dominion.workers.scene_packet import blockers as blockers_mod
from dominion.workers.scene_packet import hash as hash_mod
from dominion.workers.scene_packet import inputs as sp_inputs
from dominion.workers.scene_packet import qa as qa_mod
from dominion.workers.scene_packet.parse import valid_scene_packet_body
from dominion.workers.scene_packet.validation import evaluate_scene_packet

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
    # ADR-0028 Slice 3b (Q9): the imported Scene this seed was adopted from, resolved from the producing
    # ImportAdoption.seed_bindings. Set ONLY for adoption-derived packets (NULL for ordinary ones) — it is
    # the JOIN key a waiting RevisionRequest's resume uses to find its target-scene contract.
    source_scene_id: uuid.UUID | None
    # True iff this packet is adoption-derived but its seed has NO binding — source_scene_id would be NULL.
    # Such a seed fails the packet CLOSED (blocked, no author call) rather than derive with no lineage and
    # NO scene_no fallback (Q9).
    adoption_unbound: bool
    owner_snippets: list[str]
    canon_snippets: list[str]
    # Resolved provenance legend persisted on the packet: one entry per retrieved snippet,
    # {handle, id, doc_path, heading_path, owner_topic, retrieval_reason, score}.
    sources: list[dict[str, Any]]
    budget: TokenBudget
    # This scene's EFFECTIVE POV (beat override, else chapter POV) and that POV's rolling summary —
    # resolved per scene in Phase 1 (DB-bound) so the LLM fan-out in Phase 2 needs no further reads.
    pov: str
    pov_summary: str | None


async def _author_then_qa(
    item: _SceneWork,
    *,
    chapter_packet_body: dict[str, Any],
    chapter_open_questions: dict[str, Any] | None,
    pov: str,
    pov_summary: str | None,
    omniscient_summary: str | None,
    sink: telemetry.TelemetrySink,
    book_id: str,
    chapter_id: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, str | None, list[dict[str, Any]], str | None]:
    """One scene's Author -> deterministic evaluation -> QA. QA reads the author's output, so they stay
    ordered; only different scenes run concurrently. Fails CLOSED: any author/QA error (or a deterministic
    draft-safety blocker) returns a None in the QA slot so `status_after_author_qa` blocks the packet,
    never raising into the gather — the failure's real cause is captured (3rd return value) so the blocked
    packet names *why*, the violations (4th value, block + warn) are persisted for the editor, and the
    blocker's SOURCE (5th value: "author" | "validation" | "qa" | None) is persisted so the UI stops
    mislabeling a deterministic block as a QA block.

    Deterministic evaluation runs BEFORE QA and is WRITER-FIRST: it normalizes optional provenance (an
    invalid claim source_id is nulled + warned, never blocked) and hard-blocks only true blockers — a
    malformed body, an unrecoverable budget/scene_no. Fixable defects (an absent character on-page, a
    reader/POV leak) become repair tasks that ride along in the persisted violations and gate final
    export only. Only a hard blocker skips QA (no point attacking a packet that can't be drafted); a
    repair/warn-only packet still runs QA and stays PROPOSED.

    Each call runs inside a telemetry `call_context` tagged with this scene's dimensions, so its cache/
    usage/truncation lands in the shared `sink` (persisted later) under the right stage + scene."""
    error_detail: str | None = None
    violations: list[dict[str, Any]] = []
    blocker_source: str | None = None

    def _ctx(stage: str) -> telemetry.CallContext:
        return telemetry.CallContext(
            sink=sink,
            stage=stage,
            book_id=book_id,
            chapter_id=chapter_id,
            scene_no=item.scene_no,
            seed_id=str(item.seed_id),
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
                pov=pov,
                chapter_packet_body=chapter_packet_body,
                scene_seed=item.seed,
                word_budget=item.word_budget,
                pov_summary=pov_summary,
                omniscient_summary=omniscient_summary,
                chapter_open_questions=chapter_open_questions,
                owner_snippets=item.owner_snippets or None,
                canon_snippets=item.canon_snippets or None,
                budget=item.budget,
            )
    except LlmRateLimited as exc:
        # NOT an author failure: the provider refused the call (429/TPM) past its automatic retries.
        # The scene contract is not invalid — it was never produced. Lands as RATE_LIMITED so the UI
        # offers a retry instead of blaming the author stage.
        log.warning("scene_packet.author_rate_limited", seed=str(item.seed_id), error=str(exc))
        scene_body = None
        error_detail = (
            f"Rate limited by provider during scene author ({exc}). "
            "Transient infrastructure failure — retry derive for this scene."
        )
        blocker_source = "rate_limit"
    except PromptBudgetExceeded as exc:
        # Local policy gate, no provider call was made — a deterministic validation failure, not an
        # author quality failure. Trim the packet/context or raise the stage budget.
        log.warning("scene_packet.author_prompt_budget", seed=str(item.seed_id), error=str(exc))
        scene_body = None
        error_detail = str(exc)
        blocker_source = "validation"
    except Exception as exc:  # noqa: BLE001 — any author failure fails this scene closed
        log.error("scene_packet.author_failed", seed=str(item.seed_id), error=str(exc))
        scene_body = None
        error_detail = str(exc)
        blocker_source = "author"

    qa: dict[str, Any] | None = None
    if isinstance(scene_body, dict) and valid_scene_packet_body(scene_body):
        # Writer-first deterministic evaluation: stamp the facts the planner/seed own (word_budget,
        # scene_no) server-side, normalize optional provenance (invalid source ids -> null + one warning),
        # then run the contract checks — all in one place. Only a true blocker hard-blocks; a repair/
        # warn-only packet still runs QA and stays PROPOSED (repairs gate final export, not drafting).
        # `normalized_body` is what we persist and QA attacks, so a sloppy model echo of a deterministic
        # field can never block on its own.
        result = evaluate_scene_packet(
            body=scene_body,
            chapter_packet_body=chapter_packet_body,
            scene_seed=item.seed,
            word_budget=item.word_budget,
            scene_no=item.scene_no,
            sources=item.sources,
            block_on_provenance=settings.scene_packet_block_on_provenance,
        )
        scene_body = result.normalized_body
        violations = [v.as_dict() for v in result.violations]
        draft_blockers = result.draft_blockers
        if draft_blockers:
            error_detail = "deterministic validation failed: " + "; ".join(v.detail for v in draft_blockers)
            blocker_source = "validation"
            log.warning(
                "scene_packet.validation_blocked",
                seed=str(item.seed_id),
                count=len(draft_blockers),
                kinds=sorted({v.kind for v in draft_blockers}),
            )
        else:
            try:
                with telemetry.call_context(_ctx("scene_packet_qa")):
                    qa = await qa_mod.qa_scene_packet(
                        scene_body,
                        chapter_packet_body=chapter_packet_body,
                        chapter_open_questions=chapter_open_questions,
                        budget=item.budget,
                    )
            except LlmRateLimited as exc:
                # The author's contract body is VALID — only the QA call was refused by the provider.
                # Persist the body and land as RATE_LIMITED so "re-run QA" stays available; never
                # report this as a missing/invalid scene contract.
                log.warning("scene_packet.qa_rate_limited", seed=str(item.seed_id), error=str(exc))
                qa = None
                error_detail = (
                    f"Rate limited by provider during scene QA ({exc}). "
                    "The scene contract is valid — re-run QA once the limit resets."
                )
                blocker_source = "rate_limit"
            except Exception as exc:  # noqa: BLE001
                log.error("scene_packet.qa_failed", seed=str(item.seed_id), error=str(exc))
                qa = None
                error_detail = str(exc)
                blocker_source = "qa"
    elif scene_body is not None:
        # Author returned a body, but it is thin/incomplete (missing required contract sections) — the
        # author gate blocks it, so attribute the block to the author, not to QA (which never ran).
        blocker_source = "author"
    return scene_body, qa, error_detail, violations, blocker_source


def _label_canon_sources(
    snippets: list[dict[str, Any]],
) -> tuple[list[str], list[str], list[dict[str, Any]], list[str]]:
    """Turn retrieved canon snippets into (owner_labeled, canon_labeled, sources, chunk_hashes).

    Each snippet gets a stable bracket handle (C1, C2, …) in rerank order, so the author can cite it in
    `claim_sources` and the resolved `sources` legend maps that handle back to a real file + heading. The
    derive previously kept only `s["body"]`, discarding doc_path/heading/score — which is exactly why a
    wrong claim in a packet was untraceable. `chunk_hashes` folds each snippet's identity+content into the
    packet's source_hash, so editing the canon a packet was built from marks it stale.
    """
    owner_labeled: list[str] = []
    canon_labeled: list[str] = []
    sources: list[dict[str, Any]] = []
    chunk_hashes: list[str] = []
    for i, s in enumerate(snippets, start=1):
        handle = f"C{i}"
        body = str(s.get("body") or "")
        doc_path = s.get("doc_path") or ""
        heading = s.get("heading_path") or ""
        reason = s.get("retrieval_reason") or "semantic"
        loc = f"{doc_path} › {heading}" if heading else doc_path
        labeled = f"[{handle}] ({loc})\n{body}" if loc else f"[{handle}]\n{body}"
        (owner_labeled if reason == "owner_forced" else canon_labeled).append(labeled)
        sources.append(
            {
                "handle": handle,
                "id": s.get("id"),
                "doc_path": doc_path,
                "heading_path": heading,
                "owner_topic": s.get("owner_topic"),
                "retrieval_reason": reason,
                "score": s.get("score"),
            }
        )
        chunk_hashes.append(hashlib.sha256(f"{s.get('id')}:{body}".encode()).hexdigest())
    return owner_labeled, canon_labeled, sources, chunk_hashes


async def _chronology_safe_summary(
    session: AsyncSession, row: Summary | None, *, before_chapter_no: int | None
) -> str | None:
    """A rolling summary is ONE ever-forward-mutated row per (book, scope, pov) — `refresh_on_approval`
    overwrites it on every scene approval, so it always reflects whichever scene was approved MOST
    RECENTLY, not necessarily anything chronologically before the chapter currently being derived.
    Re-deriving an earlier chapter after a later one was drafted/approved (revision, backfill,
    out-of-order work) would otherwise hand that later chapter's events to the author as if they preceded
    this one — the exact "Book 1 ending facts leak into Book 1 Chapter 1" contamination class. Suppress
    (return None) rather than hand back a summary that runs chronologically at-or-after the target
    chapter: no prior-summary context is safer than a spoiler-contaminated one.

    `before_chapter_no=None` means the target chapter's position couldn't be determined (defensive-only —
    every ScenePacket derive has a real Chapter row); fail open rather than block on missing chronology
    data, since suppressing here is only ever a mitigation, not the primary contract."""
    if row is None or not row.rolling_summary:
        return None
    if before_chapter_no is not None and row.up_to_scene_id is not None:
        folded_chapter_no = (
            await session.execute(
                select(Chapter.chapter_no)
                .join(Scene, Scene.chapter_id == Chapter.id)
                .where(Scene.id == row.up_to_scene_id)
            )
        ).scalar_one_or_none()
        if folded_chapter_no is not None and folded_chapter_no > before_chapter_no:
            log.warning(
                "scene_packet.summary_chronology_suppressed",
                scope=row.scope,
                pov=row.pov,
                folded_chapter_no=folded_chapter_no,
                target_chapter_no=before_chapter_no,
            )
            return None
    return row.rolling_summary


async def _omniscient_summary(
    session: AsyncSession, book_id: uuid.UUID, *, before_chapter_no: int | None
) -> str | None:
    row = (
        await session.execute(
            select(Summary).where(Summary.book_id == book_id, Summary.scope == "omniscient", Summary.pov.is_(None))
        )
    ).scalar_one_or_none()
    return await _chronology_safe_summary(session, row, before_chapter_no=before_chapter_no)


async def _pov_summary(
    session: AsyncSession, *, book_id: uuid.UUID, pov: str, before_chapter_no: int | None
) -> str | None:
    row = (
        await session.execute(
            select(Summary)
            .where(Summary.book_id == book_id, Summary.scope == "pov", Summary.pov == pov)
            .order_by(Summary.id)
            .limit(1)
        )
    ).scalar_one_or_none()
    return await _chronology_safe_summary(session, row, before_chapter_no=before_chapter_no)


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
        report["scenes"].append(
            {
                "scene_no": item.scene_no,
                "pov": item.pov,
                "pov_summary": pov_summary,
                "scene_seed": scene_seed,
                "word_budget": word_budget,
                "owner_snippets": owner,
                "canon_snippets": canon,
                "scene_context_total": scene_total,
            }
        )
    return report


async def _prime_shared_prefixes(
    *,
    work: list[_SceneWork],
    chapter_packet_body: dict[str, Any],
    chapter_open_questions: dict[str, Any] | None,
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
        with telemetry.call_context(
            telemetry.CallContext(
                sink=sink,
                stage="scene_packet_author_prefix_prime",
                book_id=book_id,
                chapter_id=chapter_id,
                scene_no=None,
                seed_id=None,
            )
        ):
            await author_sections_mod.prime_author_shared_prefix(
                chapter_packet_body=chapter_packet_body,
                pov_summary=item.pov_summary,
                omniscient_summary=omniscient_summary,
                chapter_open_questions=chapter_open_questions,
                budget=TokenBudget(
                    max_tokens=settings.scene_packet_prefix_prime_token_budget,
                    hard_max_tokens=settings.scene_packet_prefix_prime_hard_token_budget,
                ),
            )

    with telemetry.call_context(
        telemetry.CallContext(
            sink=sink,
            stage="scene_packet_qa_prefix_prime",
            book_id=book_id,
            chapter_id=chapter_id,
            scene_no=None,
            seed_id=None,
        )
    ):
        await qa_mod.prime_qa_shared_prefix(
            chapter_packet_body,
            chapter_open_questions=chapter_open_questions,
            budget=TokenBudget(
                max_tokens=settings.scene_packet_prefix_prime_token_budget,
                hard_max_tokens=settings.scene_packet_prefix_prime_hard_token_budget,
            ),
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
    # Prefer the SurfaceContract (drafter-safe projected seeds + fields) when present.
    # This is the contract-first rule: ScenePacket derivation consumes the DERIVED drafter view of the
    # master packet (`_surface_contract`), never the raw internal top-level scene seeds. Scene packets
    # are derived views of the chapter master packet, not an independent source of chapter truth.
    effective_body: dict[str, Any] = master.drafter_view(body)
    seeds = [s for s in (effective_body.get("scene_seeds") or []) if isinstance(s, dict) and s.get("seed_id")]
    counts: dict[str, Any] = {
        "created": 0,
        "updated": 0,
        "blocked": 0,
        "stale": 0,
        "rate_limited": 0,
        "skipped": 0,
        # A1c slice 2: how many packets this derive put on an automatic canon-conflict hold.
        "held_for_question": 0,
    }
    if not seeds:
        return counts

    # Each scene's Author+QA pair normally gets its own scene-local work budget. A caller-provided
    # budget intentionally preserves the older shared scene-work override for tests/special callers, but
    # it does NOT include chapter-prefix prime calls; those always use scene_packet_prefix_prime_token_budget
    # so cache initialization never consumes Scene 1's scene-local budget.
    external_scene_budget = budget
    chapter_target, chapter_max = sp_inputs.chapter_targets(effective_body, seeds)
    budgets = length_planner.plan_word_budgets(
        chapter_target_words=chapter_target,
        chapter_max_words=chapter_max,
        scene_seeds=seeds,
        chapter_packet_body=effective_body,
    )

    existing: dict[uuid.UUID, ScenePacket] = {
        sp.scene_seed_id: sp
        for sp in (
            await session.execute(
                select(ScenePacket).where(
                    ScenePacket.chapter_id == packet.chapter_id, ScenePacket.scene_seed_id.isnot(None)
                )
            )
        ).scalars()
        if sp.scene_seed_id is not None
    }

    # ADR-0028 Slice 3b (Q8/Q9): if THIS chapter packet was produced by an import adoption, every scene
    # packet derived from it must bind back to the imported Scene the adoption recorded for its seed — the
    # JOIN key a waiting RevisionRequest's resume uses. `seed_bindings` is written once at adoption publish
    # ({seed_id: {scene_no, scene_id}}). An adoption-derived seed with NO binding fails CLOSED below (never
    # a scene_no fallback). An ordinary (planning-path) packet has no producing adoption → source_scene_id
    # stays NULL for all its seeds.
    adoption = (
        await session.execute(
            select(ImportAdoption)
            .where(
                ImportAdoption.chapter_packet_id == packet.id,
                ImportAdoption.status == ImportAdoptionStatus.CONTRACT_PROPOSED.value,
            )
            .order_by(ImportAdoption.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    is_adoption_derived = adoption is not None
    adoption_bindings: dict[str, Any] = (adoption.seed_bindings or {}) if adoption is not None else {}

    # Each scene drafts in its EFFECTIVE POV: the beat's per-scene override (Beat.pov) when set, else the
    # chapter POV. Load the chapter + its beats keyed by scene_no so an overridden scene gets that POV's
    # actual rolling summary, not just a label. When a scene_no has multiple beat rows, prefer one that
    # carries an override so a per-scene POV isn't lost to a sibling row. Fetched BEFORE the omniscient
    # summary so the summary lookup can suppress anything chronologically ahead of this chapter.
    chapter = await session.get(Chapter, packet.chapter_id)
    target_chapter_no = chapter.chapter_no if chapter is not None else None
    omniscient = await _omniscient_summary(session, packet.book_id, before_chapter_no=target_chapter_no)
    # Canonical read: chapter_contract.open_questions with the sibling column folded in (the column is
    # the adjudicated state for legacy rows; writers keep it in sync for canonical rows).
    chapter_open_questions = master.master_open_questions(body, packet.open_questions)
    beats_by_scene: dict[int, Beat] = {}
    for b in (await session.execute(select(Beat).where(Beat.chapter_id == packet.chapter_id))).scalars():
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

        # Owner files win over semantic hits: force-inject the relevant dossiers/invariants, then add
        # supporting hybrid retrieval (keyword + semantic). Retrieve BEFORE the hash so the canon a packet
        # was built from is folded into its source_hash — editing that canon now correctly marks the packet
        # stale (previously it didn't, so a fixed canon fact left the wrong value sitting in the packet).
        # Cost: an unchanged approved scene now pays retrieval before the skip below, but still skips the
        # expensive Author+QA LLM fan-out; retrieval is DB-only.
        query = " ".join([str(seed.get("scene_job") or ""), *as_str_list(seed.get("required_beats"))])
        routing = owner_router.route(query, characters=as_str_list(effective_body.get("characters_present")))
        snippets = await retrieval.retrieve_hybrid(
            session,
            book_id=packet.book_id,
            query=query,
            owner_topics=routing.owner_topics,
            required_doc_paths=routing.doc_paths,
            k=_CANON_K,
        )
        # Keep full provenance (handle + file + heading + score), not just the snippet text: the author
        # cites handles in claim_sources, the Desk shows the source list, and the chunk hashes feed staleness.
        owner_labeled, canon_labeled, sources, canon_chunk_hashes = _label_canon_sources(snippets)

        prior_keys = await sp_inputs.prior_scene_keys(session, chapter_id=packet.chapter_id, scene_no=scene_no)
        src_hash = hash_mod.source_hash(
            chapter_packet_id=packet.id,
            chapter_packet_body=effective_body,
            scene_seed=seed,
            chapter_word_budget=word_budget,
            prior_scene_keys=prior_keys,
            canon_chunk_hashes=canon_chunk_hashes,
            scene_pov=pov_override or None,
        )

        # Resolve this seed's adoption binding (Q9). For an adoption-derived packet the imported Scene id
        # comes from seed_bindings[seed_id]["scene_id"]; a missing/invalid binding marks the seed unbound
        # so it fails CLOSED (a blocked packet, no author call) instead of deriving with no lineage.
        source_scene_id: uuid.UUID | None = None
        adoption_unbound = False
        if is_adoption_derived:
            binding = adoption_bindings.get(str(seed_id))
            raw_scene_id = binding.get("scene_id") if isinstance(binding, dict) else None
            if raw_scene_id:
                try:
                    source_scene_id = uuid.UUID(str(raw_scene_id))
                except (ValueError, TypeError):
                    source_scene_id = None
            adoption_unbound = source_scene_id is None

        row = existing.get(seed_id)
        # An approved packet whose inputs are unchanged needs no rebuild. Counted so the Desk can say
        # "4 skipped (approved, unchanged)" instead of a re-derive that looks like it did nothing.
        if row is not None and row.status == ScenePacketStatus.APPROVED and row.source_hash == src_hash:
            counts["skipped"] += 1
            continue

        # That POV's rolling summary, fetched once per DISTINCT effective POV (cache by pov string); most
        # chapters have a single POV, so this is one fetch. (scene_pov is resolved above, before the hash.)
        if scene_pov not in pov_summary_cache:
            pov_summary_cache[scene_pov] = (
                await _pov_summary(session, book_id=packet.book_id, pov=scene_pov, before_chapter_no=target_chapter_no)
                if scene_pov
                else None
            )

        work.append(
            _SceneWork(
                seed=seed,
                seed_id=seed_id,
                scene_no=scene_no,
                word_budget=word_budget,
                src_hash=src_hash,
                row=row,
                owner_snippets=owner_labeled,
                canon_snippets=canon_labeled,
                sources=sources,
                # Fresh per-scene budget unless the caller explicitly supplied a shared scene-work budget.
                # Soft target = scene_token_budget (a tiny overage only warns); hard ceiling =
                # scene_token_hard_budget (the real block). Prefix-prime calls use the separate prime budget.
                budget=external_scene_budget
                or TokenBudget(
                    max_tokens=settings.scene_token_budget,
                    hard_max_tokens=settings.scene_token_hard_budget,
                ),
                pov=scene_pov,
                pov_summary=pov_summary_cache[scene_pov],
                source_scene_id=source_scene_id,
                adoption_unbound=adoption_unbound,
            )
        )

    # ---- Phase 2 (concurrent, LLM only): the scenes are independent, so their Author+QA pairs fan out
    # under a concurrency cap. No DB access happens here (each task only touches its own _SceneWork).
    # One shared telemetry sink collects every call's cache/usage/truncation (tagged per scene+stage
    # inside _author_then_qa); it is flushed to the DB in Phase 3.
    sem = asyncio.Semaphore(max(1, settings.scene_packet_concurrency))
    sink = telemetry.TelemetrySink()
    derive_run_id = uuid.uuid4()
    book_id_str, chapter_id_str = str(packet.book_id), str(packet.chapter_id)
    counts["context_budget_report"] = _derive_context_budget_report(
        chapter_packet_body=effective_body,
        work=work,
        omniscient_summary=omniscient,
    )

    await _prime_shared_prefixes(
        work=work,
        chapter_packet_body=effective_body,
        chapter_open_questions=chapter_open_questions,
        omniscient_summary=omniscient,
        sink=sink,
        book_id=book_id_str,
        chapter_id=chapter_id_str,
    )

    async def _run(
        item: _SceneWork,
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None, str | None, list[dict[str, Any]], str | None]:
        # Fail CLOSED before spending an author call: an adoption-derived seed with no source-scene
        # binding cannot produce a lineage-complete contract (Q9). Persist it as a deterministic
        # (validation-source) block so the human sees WHY, never a silently-unbound approved packet.
        if item.adoption_unbound:
            return (
                None,
                None,
                (
                    "adoption-derived scene packet has no source-scene binding "
                    f"(seed {item.seed_id} is absent from the adoption's seed_bindings) — cannot bind "
                    "source_scene_id; failing closed with no scene_no fallback (ADR-0028 Q9)"
                ),
                [],
                "validation",
            )
        async with sem:
            return await _author_then_qa(
                item,
                chapter_packet_body=effective_body,
                chapter_open_questions=chapter_open_questions,
                pov=item.pov,
                pov_summary=item.pov_summary,
                omniscient_summary=omniscient,
                sink=sink,
                book_id=book_id_str,
                chapter_id=chapter_id_str,
            )

    # Shared chapter prefixes are already primed under a prefix budget, so Scene 1 no longer needs to
    # run first or pay chapter-level cache creation under its scene-local work budget.
    results = await asyncio.gather(*(_run(item) for item in work))

    # Surface-safe projection inputs. Prefer surface_terms (new generic) but fall back to legacy
    # entity_bindings for transition. The SurfaceContract already performed the main projection on
    # the seeds; this is a final safety net for Beat bodies.
    chapter_bindings = effective_body.get("entity_bindings") or body.get("entity_bindings")
    chapter_reps = binding_replacements(chapter_bindings)

    # ---- Phase 3 (serial, DB): persist each scene's verdict. Order-independent — no task read another
    # scene's freshly-derived packet, so the write order doesn't change any result.
    holds: list[tuple[uuid.UUID, str]] = []  # (scene_packet_id, question) — raised after the loop
    for item, (scene_body, qa, error_detail, violations, blocker_source) in zip(work, results, strict=True):
        status, blocked_reason = approval_policy.status_after_author_qa(
            scene_body, qa, error_detail, blocker_source=blocker_source
        )
        persisted_body = scene_body if isinstance(scene_body, dict) else {"blocked_reason": blocked_reason}
        if chapter_bindings and isinstance(persisted_body, dict):
            persisted_body = {**persisted_body, "entity_bindings": chapter_bindings}
            persisted_body, _ = project_drafter_fields(persisted_body, chapter_reps)
        qa_warnings = (
            {"residual_risks": qa["residual_risks"], "issues": qa["issues"]}
            if qa
            else {"residual_risks": [], "blocked_reason": blocked_reason}
        )
        held = status in (ScenePacketStatus.BLOCKED, ScenePacketStatus.RATE_LIMITED)
        if held and blocked_reason:
            qa_warnings = {**qa_warnings, "blocked_reason": blocked_reason}
        # Surface deterministic contract violations (block + repair + warn) on the packet so the editor
        # sees the concrete repair task ("absent character on-page") or advisory ("12 source ids
        # normalized") instead of only a generic verdict.
        if violations:
            qa_warnings = {**qa_warnings, "violations": violations}
        # Persist WHICH gate blocked (author | validation | qa | rate_limit) so the UI stops mislabeling
        # a deterministic block as a QA block. QA verdicts are advisory now, so the only "qa" blocks left
        # are QA calls that failed to return a usable verdict (fail closed on infrastructure).
        if held:
            qa_warnings = {**qa_warnings, "blocker_source": blocker_source or "qa"}

        row = item.row
        if row is None:
            row = ScenePacket(
                id=uuid.uuid4(),  # minted here so the grade below can self-reference the artifact
                book_id=packet.book_id,
                chapter_id=packet.chapter_id,
                chapter_packet_id=packet.id,
                scene_seed_id=item.seed_id,
                scene_no=item.scene_no,
            )
            session.add(row)
            counts["created"] += 1
        else:
            counts["updated"] += 1
        if qa is not None:
            # Workstream-G advisory grade: LLM per-dimension scores + deterministic repair/warn
            # violations in one machine-readable object. It never gates drafting or approval.
            qa_warnings = {
                **qa_warnings,
                "grade": build_grade(
                    artifact_id=row.id,
                    artifact_type="scene_packet",
                    grader=settings.scene_packet_qa_model,
                    qa=qa,
                    violations=violations,
                ),
            }
        row.chapter_packet_id = packet.id
        row.scene_no = item.scene_no
        row.status = status
        # Only record a QA verdict when QA actually ran. A deterministic/author block skips QA, so
        # forcing BLOCK_DRAFTING here is what made every such block look like a QA block downstream —
        # leave it None and let the persisted blocker_source name the real gate.
        row.qa_verdict = qa["verdict"] if qa else None
        row.qa_warnings = qa_warnings
        row.body = persisted_body
        # Persist the provenance legend even on a blocked packet — retrieval succeeded, so the human can
        # still see what canon the (failed) author was working from while diagnosing the block.
        row.sources = item.sources
        row.source_hash = item.src_hash
        # ADR-0028 Slice 3b (Q9): bind the imported source Scene on an adoption-derived packet (NULL for
        # ordinary packets). A fail-closed unbound seed persists as BLOCKED above with source_scene_id NULL.
        row.source_scene_id = item.source_scene_id
        row.stale_reason = None
        if status == ScenePacketStatus.BLOCKED:
            counts["blocked"] += 1
        elif status == ScenePacketStatus.RATE_LIMITED:
            counts["rate_limited"] += 1
        # A1c slice 2: the AUTOMATIC escalation source. A held packet (BLOCKED/RATE_LIMITED) already
        # refuses approval on its own status, so a hold there would be noise; the case this exists for is
        # the packet that LOOKS approvable but whose QA reported a canon conflict. Collected here and
        # raised after the loop, because raise_blocker locks the owning row and the freshly-added rows
        # must be flushed first.
        elif (question := blockers_mod.automatic_hold_for_qa(qa)) is not None:
            holds.append((row.id, question))

    if holds:
        await session.flush()  # the new ScenePacket rows must exist before a blocker references them
        for scene_packet_id, question in holds:
            await blockers_mod.raise_blocker(
                session,
                scene_packet_id=scene_packet_id,
                source=blockers_mod.CANON_CONFLICT,
                source_key=blockers_mod.CANON_CONFLICT_KEY,
                question=question,
            )
        counts["held_for_question"] = len(holds)

    # One run_id for every call this derive made, so the Desk can isolate this run (Packets panel) and
    # build a per-run history (Telemetry tab) instead of reading one ever-growing cumulative total.
    from dominion.workers.telemetry_settings import telemetry_settings_snapshot

    telemetry_db.persist_sink(
        session,
        sink,
        run_id=derive_run_id,
        book_id=packet.book_id,
        chapter_id=packet.chapter_id,
        settings_snapshot=telemetry_settings_snapshot(),
    )
    return counts
