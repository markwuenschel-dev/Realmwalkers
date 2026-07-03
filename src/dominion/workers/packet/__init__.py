"""Chapter packet orchestration (contract-first drafting, Phase 1).

`propose_packet` runs the Packet Author then the Packet QA, derives a confidence + status, and
persists a ChapterPacket. It is FAIL-CLOSED: any malformed/empty/timed-out agent output yields a
`blocked` packet rather than partial drafting constraints — a weak packet must never quietly become
the gate. A failed re-propose never wipes an already-approved packet (mirrors the beats path).

The persisted body of a successful proposal is the canonical `chapter_master_packet` (see
`packet/master.py` + `chapter_master_packet.schema.json`): raw internal truth stays authoritative at
the top level (scene_seeds exist exactly ONCE, un-projected), the drafter-safe projection lives only
under the derived `_surface_contract` key, and the chapter's open questions live in
`chapter_contract.open_questions` (the sibling column is written as a derived sync for API back-compat).

Two safety jobs live here, not in the agents:
  * provenance — claim `source_id` handles (C1, C2, …) are resolved back to real canon ids + titles;
  * stable ids — every scene seed gets a server-minted `seed_id` (the sync key for later phases),
    never a model-supplied one.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from dominion.shared.config import settings
from dominion.shared.enums import PacketConfidence, PacketStatus, PacketVerdict
from dominion.shared.grading import build_grade
from dominion.shared.models import Chapter, ChapterPacket, Summary
from dominion.workers import progress, telemetry, telemetry_db
from dominion.workers.budget import TokenBudget
from dominion.workers.memory import canon_rag
from dominion.workers.packet import approval_policy, master
from dominion.workers.packet import author as author_mod
from dominion.workers.packet import qa as qa_mod
from dominion.workers.packet.surface_contract import build_surface_contract
from dominion.workers.packet.validation import evaluate_chapter_packet_internal

log = structlog.get_logger()

_CANON_K = 16  # the author gets broad canon (scoping protects the writer, not the planner)
_EXCERPT_CHARS = 240

_AUTHOR_TIMEOUT_ACTIONS = [
    "Reduce or split the chapter outline/context, then re-propose.",
    "Choose a faster packet author model in Settings, then re-propose.",
    "Increase DOMINION_PACKET_TIME_BUDGET_S and restart the API, then re-propose.",
]
_AUTHOR_FAILURE_ACTIONS = [
    "Check packet-author telemetry/logs for the exact provider error.",
    "Change the packet author model or reduce the chapter outline/context, then re-propose.",
]
_AUTHOR_BODY_ACTIONS = [
    "Tighten the chapter outline so the packet author can emit clear scene seeds and claims, then re-propose.",
    "If the chapter is very large, split it or reduce context before re-proposing.",
]
_QA_FAILURE_ACTIONS = [
    "Re-propose after changing the chapter outline/canon inputs, or check packet-QA telemetry for provider errors.",
]
_VALIDATION_ACTIONS = [
    "Fix the roster fields or forbidden names shown below, then re-propose.",
]


def _valid_packet(packet: dict[str, Any]) -> bool:
    """A usable packet must carry at least one scene seed and a claims list (provenance). Anything
    thinner is treated as malformed -> blocked."""
    seeds = packet.get("scene_seeds")
    if not isinstance(seeds, list) or not any(isinstance(s, dict) for s in seeds):
        return False
    return isinstance(packet.get("claims"), list)


def mint_seed_ids(packet: dict[str, Any]) -> None:
    """Stamp a stable server-side seed_id on each scene seed that lacks one (the sync key for contract
    derivation). Existing ids are PRESERVED so a human edit that adds/reorders seeds keeps already-
    derived beats linked; only brand-new seeds get a fresh id. Mutates in place."""
    for seed in packet.get("scene_seeds", []):
        if isinstance(seed, dict) and not str(seed.get("seed_id") or "").strip():
            seed["seed_id"] = str(uuid.uuid4())


# Back-compat alias for the propose path (which always mints from scratch — the author never supplies ids).
_mint_seed_ids = mint_seed_ids


def _resolve_provenance(packet: dict[str, Any], handles: dict[str, dict[str, Any]]) -> None:
    """Turn each claim's source handle into real provenance: a canon id + title + excerpt, or the
    outline, or nothing for inference. Done server-side so 'LOCKED_CANON' is always traceable."""
    for claim in packet.get("claims", []):
        if not isinstance(claim, dict):
            continue
        handle = str(claim.get("source_id") or "").strip()
        meta = handles.get(handle)
        if meta is not None:
            body = str(meta.get("body") or "")
            claim["source_id"] = str(meta.get("id"))
            claim["source_title_or_file"] = meta.get("name")
            claim["excerpt"] = body[:_EXCERPT_CHARS]
        elif handle.upper() == "OUTLINE":
            claim["source_id"] = "OUTLINE"
            claim["source_title_or_file"] = "chapter outline"
            claim["excerpt"] = None
        else:  # PLAUSIBLE_INFERENCE / UNRESOLVED / unknown handle — no canonical source
            claim["source_id"] = None
            claim["source_title_or_file"] = None
            claim["excerpt"] = None


def _open_questions(packet: dict[str, Any]) -> list[str]:
    oq = packet.get("open_questions")
    return [str(q).strip() for q in oq if str(q).strip()] if isinstance(oq, list) else []


async def _prior_exit_state(session: AsyncSession, *, book_id: uuid.UUID, chapter_no: int) -> str | None:
    """The previous chapter's approved exit state = this chapter's entry state, if we have it."""
    prior_chapter = (
        await session.execute(
            select(Chapter.id).where(Chapter.book_id == book_id, Chapter.chapter_no == chapter_no - 1)
        )
    ).scalar_one_or_none()
    if prior_chapter is None:
        return None
    body = (
        await session.execute(
            select(ChapterPacket.body)
            .where(ChapterPacket.chapter_id == prior_chapter, ChapterPacket.status == PacketStatus.APPROVED)
            .order_by(ChapterPacket.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return str(body.get("exit_state")) if isinstance(body, dict) and body.get("exit_state") else None


async def _omniscient_summary(session: AsyncSession, book_id: uuid.UUID) -> str | None:
    return (
        await session.execute(
            select(Summary.rolling_summary).where(
                Summary.book_id == book_id, Summary.scope == "omniscient", Summary.pov.is_(None)
            )
        )
    ).scalar_one_or_none()


async def latest_approved(session: AsyncSession, chapter_id: uuid.UUID) -> ChapterPacket | None:
    return (
        await session.execute(
            select(ChapterPacket)
            .where(ChapterPacket.chapter_id == chapter_id, ChapterPacket.status == PacketStatus.APPROVED)
            .order_by(ChapterPacket.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


def _blocked_row(
    *,
    book_id: uuid.UUID,
    chapter_id: uuid.UUID,
    reason: str,
    body: dict[str, Any] | None = None,
    violations: list[dict[str, Any]] | None = None,
    open_questions: dict[str, Any] | None = None,
    blocker_source: str | None = None,
    blocker_kind: str | None = None,
    recovery_actions: list[str] | None = None,
    blocker_diagnostics: dict[str, Any] | None = None,
) -> ChapterPacket:
    qa_warnings: dict[str, Any] = {"residual_risks": [], "blocked_reason": reason}
    if blocker_source:
        qa_warnings["blocker_source"] = blocker_source
    if blocker_kind:
        qa_warnings["blocker_kind"] = blocker_kind
    if recovery_actions:
        qa_warnings["recovery_actions"] = recovery_actions
    if blocker_diagnostics:
        qa_warnings["blocker_diagnostics"] = blocker_diagnostics
    if violations:
        # Persist WHICH gate blocked (deterministic validation, not QA) so the UI doesn't mislabel a
        # decidable roster contradiction as an LLM QA verdict — same distinction the scene-packet UI
        # already makes for its own deterministic-vs-QA blocks.
        qa_warnings["violations"] = violations
        qa_warnings.setdefault("blocker_source", "validation")
        qa_warnings.setdefault("blocker_kind", "contract_validation")
        qa_warnings.setdefault("recovery_actions", _VALIDATION_ACTIONS)
    return ChapterPacket(
        book_id=book_id,
        chapter_id=chapter_id,
        status=PacketStatus.BLOCKED,
        confidence=PacketConfidence.RED,
        qa_verdict=PacketVerdict.BLOCK_DRAFTING,
        qa_warnings=qa_warnings,
        body=body or {"blocked_reason": reason},
        open_questions=open_questions if open_questions is not None else {"items": []},
    )


async def propose_packet(session: AsyncSession, *, chapter: Chapter, progress_key: str | None = None) -> ChapterPacket:
    """Author -> QA -> persist a ChapterPacket for this chapter (proposed/blocked). Fail-closed.

    The row is added to the session (and existing packets for the chapter replaced on success) but the
    caller commits. On a malformed/timed-out agent, an already-approved packet is preserved untouched.

    `progress_key` (when run in the background) surfaces the live phase to `GET .../packet/status` so
    the Desk can show 'authoring' -> 'qa' instead of a frozen spinner. Best-effort, never required.
    """
    book_id = chapter.book_id
    outline = (chapter.outline or "").strip()
    budget = TokenBudget(max_tokens=settings.scene_token_budget)

    # Telemetry: the Packet Author + QA calls roll up under one run row (one propose = one run) so the
    # chapter-packet stage shows in the Desk telemetry like the scene-packet derive. Persisted on EVERY
    # exit (incl. fail-closed) since the author/QA calls may have charged before a failure path.
    sink = telemetry.TelemetrySink()
    run_id = uuid.uuid4()

    def _persist_telemetry() -> None:
        telemetry_db.persist_sink(session, sink, run_id=run_id, book_id=book_id, chapter_id=chapter.id)

    async def fail_closed(
        reason: str,
        body: dict[str, Any] | None = None,
        violations: list[dict[str, Any]] | None = None,
        open_questions: dict[str, Any] | None = None,
        blocker_source: str | None = None,
        blocker_kind: str | None = None,
        recovery_actions: list[str] | None = None,
        blocker_diagnostics: dict[str, Any] | None = None,
    ) -> ChapterPacket:
        # A failed (re)propose must never wipe an already-approved packet; otherwise persist a
        # visible blocked packet so the human sees the failure (never silent partial constraints).
        _persist_telemetry()
        existing = await latest_approved(session, chapter.id)
        if existing is not None:
            return existing
        return await _persist(
            session,
            chapter_id=chapter.id,
            replace=True,
            row=_blocked_row(
                book_id=book_id,
                chapter_id=chapter.id,
                reason=reason,
                body=body,
                violations=violations,
                open_questions=open_questions,
                blocker_source=blocker_source,
                blocker_kind=blocker_kind,
                recovery_actions=recovery_actions,
                blocker_diagnostics=blocker_diagnostics,
            ),
        )

    if not outline:
        return await fail_closed(
            "Chapter has no outline to plan from. Add a chapter outline, then re-propose the packet.",
            blocker_source="input",
            blocker_kind="no_outline",
            recovery_actions=["Add a chapter outline, then re-propose."],
        )

    omniscient = await _omniscient_summary(session, book_id)
    prior_exit = await _prior_exit_state(session, book_id=book_id, chapter_no=chapter.chapter_no)
    canon_meta = await canon_rag.retrieve_with_meta(session, book_id=book_id, query=outline, k=_CANON_K)
    handles = {f"C{i}": meta for i, meta in enumerate(canon_meta, start=1)}

    # Three distinct fail-closed paths, kept distinguishable in both the stored reason and the logs so a
    # block is debuggable after the fact (they used to collapse into one generic, unlogged reason):
    #   1. the author *call* raised — timeout / budget / API error (logged below);
    #   2. it returned text we couldn't parse to an object — usually truncation (see llm.truncated);
    #   3. it parsed but the packet was too thin (no scene seeds or no claims list).
    author_exc: Exception | None = None
    progress.set_phase(progress_key, "authoring")
    try:
        with telemetry.call_context(
            telemetry.CallContext(
                sink=sink,
                stage="packet_author",
                book_id=str(book_id),
                chapter_id=str(chapter.id),
            )
        ):
            packet = await asyncio.wait_for(
                author_mod.author_packet(
                    chapter_no=chapter.chapter_no,
                    pov=chapter.pov,
                    outline=outline,
                    omniscient_summary=omniscient,
                    prior_exit_state=prior_exit,
                    next_entry_intent=None,
                    canon_handles=handles,
                    budget=budget,
                ),
                timeout=settings.packet_time_budget_s,
            )
    except Exception as exc:  # noqa: BLE001 — any author failure (timeout/budget/API) must fail closed
        log.error("packet.author_failed", chapter=str(chapter.id), error=str(exc), error_type=type(exc).__name__)
        author_exc = exc
        packet = None

    if author_exc is not None:
        diagnostics: dict[str, Any] = {
            "stage": "packet_author",
            "exception_type": type(author_exc).__name__,
            "timeout_s": settings.packet_time_budget_s,
            "model": settings.packet_author_model,
            "fallback_model": settings.packet_author_fallback_model,
        }
        if str(author_exc):
            diagnostics["message"] = str(author_exc)
        if isinstance(author_exc, TimeoutError):
            return await fail_closed(
                "Packet Author timed out after "
                f"{settings.packet_time_budget_s}s while authoring the chapter packet. "
                "Re-propose will likely time out again unless the input/model/budget changes.",
                blocker_source="author",
                blocker_kind="timeout",
                recovery_actions=_AUTHOR_TIMEOUT_ACTIONS,
                blocker_diagnostics=diagnostics,
            )
        label = type(author_exc).__name__
        detail = f": {author_exc}" if str(author_exc) else ""
        return await fail_closed(
            f"Packet Author call failed ({label}{detail}).",
            blocker_source="author",
            blocker_kind="call_failed",
            recovery_actions=_AUTHOR_FAILURE_ACTIONS,
            blocker_diagnostics=diagnostics,
        )
    if packet is None:
        log.warning("packet.author_unparsable", chapter=str(chapter.id))
        return await fail_closed(
            "Packet Author response could not be parsed, possibly because the JSON was truncated.",
            blocker_source="author",
            blocker_kind="unparsable",
            recovery_actions=_AUTHOR_BODY_ACTIONS,
            blocker_diagnostics={"stage": "packet_author", "model": settings.packet_author_model},
        )
    if not _valid_packet(packet):
        log.warning("packet.author_thin", chapter=str(chapter.id))
        return await fail_closed(
            "Packet Author returned an incomplete packet with no scene seeds or no claims list.",
            body=packet,
            blocker_source="author",
            blocker_kind="thin_packet",
            recovery_actions=_AUTHOR_BODY_ACTIONS,
            blocker_diagnostics={"stage": "packet_author", "model": settings.packet_author_model},
        )

    _mint_seed_ids(packet)
    _resolve_provenance(packet, handles)

    # === Scope-aware contract pipeline (internal -> surface) ===
    # 1. Internal validation: structure + roster contradictions only. Raw packet may contain hidden
    #    canonical truth in INTERNAL_PLANNING / AUTHOR_ONLY_CANON fields (including raw scene seeds).
    internal_result = evaluate_chapter_packet_internal(packet)
    packet_internal = internal_result.normalized_body
    violations = internal_result.violations
    if internal_result.draft_blockers:
        log.warning(
            "packet.validation_blocked",
            chapter=str(chapter.id),
            count=len(internal_result.draft_blockers),
            kinds=sorted({v.kind for v in internal_result.draft_blockers}),
            stage="internal",
        )
        return await fail_closed(
            "deterministic validation failed: " + "; ".join(v.detail for v in internal_result.draft_blockers),
            body=packet_internal,
            violations=[v.as_dict() for v in violations],
            open_questions={"items": _open_questions(packet_internal)},
            blocker_source="validation",
            blocker_kind="contract_validation",
            recovery_actions=_VALIDATION_ACTIONS,
            blocker_diagnostics={
                "stage": "internal_validation",
                "violation_count": len(internal_result.draft_blockers),
                "violation_kinds": sorted({v.kind for v in internal_result.draft_blockers}),
            },
        )

    # 2. Build SurfaceContract (drafter-facing projection). This is the contract that must be handed
    #    to ScenePacket derivation and all drafter-facing consumers.
    surface_result = build_surface_contract(packet_internal)
    packet_surface = surface_result.surface_body
    violations.extend(surface_result.violations)
    if surface_result.blockers:
        log.warning(
            "packet.validation_blocked",
            chapter=str(chapter.id),
            count=len(surface_result.blockers),
            kinds=sorted({v.kind for v in surface_result.blockers}),
            stage="surface",
        )
        return await fail_closed(
            "deterministic validation failed (surface): " + "; ".join(v.detail for v in surface_result.blockers),
            body=packet_internal,  # persist internal truth for audit
            violations=[v.as_dict() for v in violations],
            open_questions={"items": _open_questions(packet_internal)},
            blocker_source="validation",
            blocker_kind="contract_validation",
            recovery_actions=_VALIDATION_ACTIONS,
            blocker_diagnostics={
                "stage": "surface_validation",
                "violation_count": len(surface_result.blockers),
                "violation_kinds": sorted({v.kind for v in surface_result.blockers}),
            },
        )

    # === One canonical artifact (chapter_master_packet, schema_version 1) ===
    # Internal truth remains authoritative at the top level: scene_seeds exist exactly ONCE as raw
    # planning data (the old double-write that overwrote them with the projected copy is gone) and the
    # drafter-safe projection lives ONLY under the derived `_surface_contract` key — scene-packet
    # derivation and other drafter-facing consumers read it via `master.drafter_view`.
    packet_id = uuid.uuid4()
    packet = master.to_master_packet(
        packet_internal,
        book_id=book_id,
        chapter_id=chapter.id,
        chapter_no=chapter.chapter_no,
        pov=chapter.pov,
        status=PacketStatus.PROPOSED,
    )
    packet["source_inputs"] = {
        "outline_chars": len(outline),
        "prior_exit_state": bool(prior_exit),
        "omniscient_summary": bool(omniscient),
        "canon_handles": [
            {"handle": handle, "id": str(meta.get("id")), "name": meta.get("name")} for handle, meta in handles.items()
        ],
    }
    packet["lineage"] = {"source": "packet_author", "packet_id": str(packet_id)}
    packet["_surface_contract"] = packet_surface  # DERIVED projection, never authoritative

    # Structural canary on the canonical body. Blockers here are the true-blocker list only (e.g. no
    # scene seed carries a usable scene_job); fixable gaps ride along as repair tasks.
    master_violations = master.validate_master_packet(packet)
    master_blockers = [v for v in master_violations if v["severity"] == "block"]
    if master_blockers:
        log.warning(
            "packet.validation_blocked",
            chapter=str(chapter.id),
            count=len(master_blockers),
            kinds=sorted({v["kind"] for v in master_blockers}),
            stage="master",
        )
        return await fail_closed(
            "canonical packet validation failed: " + "; ".join(v["detail"] for v in master_blockers),
            body=packet,
            violations=[*(v.as_dict() for v in violations), *master_violations],
            open_questions=packet["chapter_contract"]["open_questions"],
            blocker_source="validation",
            blocker_kind="contract_validation",
            recovery_actions=_VALIDATION_ACTIONS,
            blocker_diagnostics={
                "stage": "master_validation",
                "violation_count": len(master_blockers),
                "violation_kinds": sorted({v["kind"] for v in master_blockers}),
            },
        )

    # All blockers (internal + surface + master) have already been checked. Repairs/warnings may remain.

    progress.set_phase(progress_key, "qa")
    try:
        with telemetry.call_context(
            telemetry.CallContext(
                sink=sink,
                stage="packet_qa",
                book_id=str(book_id),
                chapter_id=str(chapter.id),
            )
        ):
            qa = await asyncio.wait_for(qa_mod.qa_packet(packet, budget=budget), timeout=settings.packet_time_budget_s)
    except Exception as exc:  # noqa: BLE001 — any QA failure (timeout/budget/API) must fail closed
        log.error("packet.qa_failed", chapter=str(chapter.id), error=str(exc))
        qa = None

    if qa is None:
        return await fail_closed(
            "Packet QA returned no usable verdict.",
            body=packet,
            blocker_source="qa",
            blocker_kind="no_usable_verdict",
            recovery_actions=_QA_FAILURE_ACTIONS,
            blocker_diagnostics={
                "stage": "packet_qa",
                "timeout_s": settings.packet_time_budget_s,
                "model": settings.packet_qa_model,
                "fallback_model": settings.packet_qa_fallback_model,
            },
        )

    # QA is advisory: even a BLOCK_DRAFTING verdict yields a proposed packet (red confidence, issues
    # persisted as repair tasks). Only the deterministic fail-closed paths above may block.
    confidence, status = approval_policy.status_from_qa(packet, qa)
    violation_dicts = [*(v.as_dict() for v in violations), *master_violations]
    # The Workstream-G grade: one advisory score object folding the LLM's per-dimension scores with the
    # deterministic repair/warn violations. It NEVER gates drafting (persisted for humans + agents).
    grade = build_grade(
        artifact_id=packet_id,
        artifact_type="chapter_packet",
        grader=settings.packet_qa_model,
        qa=qa,
        violations=violation_dicts,
    )
    packet["qa"] = {
        "verdict": str(getattr(qa["verdict"], "value", qa["verdict"])),
        "blocking_issues": grade["blocking_issues"],
        "warnings": grade["warnings"],
        "repair_tasks": grade["repair_tasks"],
        "graded_by": settings.packet_qa_model,
        "last_checked_at": datetime.now(UTC).isoformat(),
    }
    qa_warnings: dict[str, Any] = {"residual_risks": qa["residual_risks"], "issues": qa["issues"], "grade": grade}
    if violation_dicts:
        # Only repair- and warn-severity violations reach here (any block already returned above),
        # surfaced alongside QA's own findings so the editor sees both channels.
        qa_warnings["violations"] = violation_dicts
    row = ChapterPacket(
        id=packet_id,
        book_id=book_id,
        chapter_id=chapter.id,
        status=status,
        confidence=confidence,
        qa_verdict=qa["verdict"],
        qa_warnings=qa_warnings,
        body=packet,
        # Derived sync of body.chapter_contract.open_questions — kept for API/UI back-compat; the body
        # section is the source of truth.
        open_questions=packet["chapter_contract"]["open_questions"],
    )
    log.info(
        "packet.proposed",
        chapter=str(chapter.id),
        status=str(status),
        confidence=str(confidence),
        verdict=str(qa["verdict"]),
    )
    _persist_telemetry()
    return await _persist(session, chapter_id=chapter.id, row=row, replace=True)


async def _persist(
    session: AsyncSession, *, chapter_id: uuid.UUID, row: ChapterPacket, replace: bool = False
) -> ChapterPacket:
    """Add the new packet; with replace, clear prior packets for the chapter first so GET returns
    exactly one current packet. Callers only replace after confirming no approved packet would be
    lost (a failed re-propose returns the existing approved packet instead of reaching here)."""
    if replace:
        await session.execute(delete(ChapterPacket).where(ChapterPacket.chapter_id == chapter_id))
        await session.flush()
    session.add(row)
    await session.flush()
    return row
