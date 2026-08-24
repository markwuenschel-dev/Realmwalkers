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
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from dominion.shared.chapter_lock import ChapterWorkflowBusy, acquire_chapter_workflow_lock
from dominion.shared.config import settings
from dominion.shared.enums import PacketConfidence, PacketStatus, PacketVerdict
from dominion.shared.grading import build_grade
from dominion.shared.models import Chapter, ChapterPacket, Summary
from dominion.workers import progress, telemetry, telemetry_db
from dominion.workers.budget import TokenBudget
from dominion.workers.memory import canon_rag
from dominion.workers.packet import approval_policy, canon_conflict, master
from dominion.workers.packet import author as author_mod
from dominion.workers.packet import evidence as evidence_mod
from dominion.workers.packet import open_questions as open_questions_policy
from dominion.workers.packet import qa as qa_mod
from dominion.workers.packet.surface_contract import build_surface_contract
from dominion.workers.packet.validation import evaluate_chapter_packet_internal

#: The author->QA->persist tail's fail-closed closure (built per-call by `_make_fail_closed`).
FailClosed = Callable[..., Awaitable[ChapterPacket]]

log = structlog.get_logger()

_CANON_K = 16  # the author gets broad canon (scoping protects the writer, not the planner)
_EXCERPT_CHARS = 240

#: Wait ceiling for the chapter workflow lock in `_persist` (#259). Longer than the 4s request-path
#: default because losing this acquisition discards 1-2 minutes of already-paid model work, and the
#: writes it contends with are all short. Bounded, never None: a stalled holder must surface as a
#: retryable busy rather than hang a background task forever.
PERSIST_LOCK_TIMEOUT_MS = 10_000
#: Bounded retries of that acquire, with linear backoff. Worst case ~2x10s waiting plus backoff, still
#: far cheaper than re-running the author+QA pass this write is the tail of.
PERSIST_LOCK_ATTEMPTS = 3
PERSIST_LOCK_RETRY_S = 0.25

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


async def _prior_exit_state(session: AsyncSession, *, chapter: Chapter) -> str | None:
    """The previous chapter's approved exit state = this chapter's entry state, if we have it.

    "Previous" is the chapter immediately before this one in READING ORDER (`position`), not
    `chapter_no - 1` — so a chapter that follows a prologue/interlude inherits from that section, and a
    numberless section resolves a prior at all. Falls back cleanly to None for the first chapter."""
    if chapter.position is None:
        return None
    prior_chapter = (
        await session.execute(
            select(Chapter.id)
            .where(Chapter.book_id == chapter.book_id, Chapter.position < chapter.position)
            .order_by(Chapter.position.desc())
            .limit(1)
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
    """Newest APPROVED packet for the chapter, or None.

    `populate_existing` because this IS the reload-under-the-lock of the chapter_lock protocol when
    called from `_persist(preserve_approved=True)`: a bare ORM SELECT returns the identity-mapped
    instance with its PRE-LOCK `status`, so a packet approved by another transaction could read as
    still-unapproved and be replaced — the exact race `preserve_approved` exists to close."""
    return (
        await session.execute(
            select(ChapterPacket)
            .where(ChapterPacket.chapter_id == chapter_id, ChapterPacket.status == PacketStatus.APPROVED)
            .order_by(ChapterPacket.created_at.desc())
            .limit(1)
            .execution_options(populate_existing=True)
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
    # A blocked row is newly authored state, not historical data. It must receive durable server-minted
    # bindings just like the successful proposal path; persisting model-produced raw strings here would
    # create a fresh ``legacy`` packet that could only be acted on by a later Prepare call. This factory
    # also feeds amendment fail-closed rows, keeping both creation paths on one rule.
    normalized_open_questions = open_questions_policy.normalize(
        open_questions if open_questions is not None else {"items": []}, mint=True
    )
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
        open_questions=normalized_open_questions,
    )


def _propose_budget() -> TokenBudget:
    """Soft/hard budget split shared by both propose paths, same policy as the scene derive: crossing the
    soft target only warns (telemetry); only the hard ceiling fails closed. Without the split, a QA
    escalation retry on a detailed chapter crossed the single 60k ceiling and the raising charge DISCARDED
    the retry's already-produced verdict (the observed `token budget exceeded: 74673 > 60000` block)."""
    return TokenBudget(max_tokens=settings.scene_token_budget, hard_max_tokens=settings.scene_token_hard_budget)


def _make_fail_closed(
    session: AsyncSession, *, chapter: Chapter, sink: telemetry.TelemetrySink, run_id: uuid.UUID
) -> FailClosed:
    """The fail-closed closure shared by both propose paths. Persists telemetry (author/QA may have
    charged before the failure), then — a failed (re)propose must NEVER wipe an already-approved packet —
    returns the existing approved packet if one exists, else persists a visible blocked packet so the
    human sees the failure (never silent partial constraints)."""
    book_id = chapter.book_id

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
        # Fast path only — the authoritative re-check happens under the chapter lock inside _persist
        # (`preserve_approved=True`), because this read is not serialized against `approve_packet`.
        existing = await latest_approved(session, chapter.id)
        if existing is not None:
            telemetry_db.persist_sink(session, sink, run_id=run_id, book_id=book_id, chapter_id=chapter.id)
            return existing
        # Telemetry AFTER the write for the same reason as the success path: `_persist` may roll back
        # to retry a busy chapter lock, which would otherwise discard this run's `llm_calls` rows.
        persisted = await _persist(
            session,
            chapter_id=chapter.id,
            replace=True,
            preserve_approved=True,
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
        telemetry_db.persist_sink(session, sink, run_id=run_id, book_id=book_id, chapter_id=chapter.id)
        return persisted

    return fail_closed


async def _qa_and_persist(
    session: AsyncSession,
    *,
    chapter: Chapter,
    packet: dict[str, Any],
    source_inputs: dict[str, Any],
    lineage: dict[str, Any],
    sink: telemetry.TelemetrySink,
    run_id: uuid.UUID,
    budget: TokenBudget,
    progress_key: str | None,
    fail_closed: FailClosed,
    extra_open_questions: Sequence[str] | None = None,
) -> ChapterPacket:
    """The shared Author->QA->persist tail for BOTH propose paths (outline + evidence).

    `packet` is the raw authored dict with per-claim provenance ALREADY resolved by the caller; the caller
    also supplies the `source_inputs`/`lineage` provenance stamps that differ between the two paths, and —
    the evidence path only — `extra_open_questions`, the adoption's manuscript-vs-canon conflict questions
    to fold into the packet (they block APPROVAL, never adoption, per Q14). Runs seed minting, the
    internal->surface->master validation pipeline (fail-closed on a hard blocker), advisory QA, and
    persists the proposed/blocked packet. QA is advisory: even BLOCK_DRAFTING yields a proposed packet;
    only the deterministic paths here may block.
    """
    book_id = chapter.book_id
    _mint_seed_ids(packet)

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
    packet["source_inputs"] = source_inputs
    packet["lineage"] = {**lineage, "packet_id": str(packet_id)}
    packet["_surface_contract"] = packet_surface  # DERIVED projection, never authoritative

    # Fold the adoption's manuscript-vs-canon conflict questions into the canonical open-questions section
    # (and its top-level mirror) BEFORE QA/persist. Any open-question item blocks ChapterPacket APPROVAL
    # (approval_policy), so a conflict-laden packet is still a proposed contract that a human must clear —
    # it does not fail the adoption closed (Q14). De-duplicated, append-only, order-preserving.
    if extra_open_questions:
        # Each appended question is MINTED an item_id (#277). Appending a bare string here would create a
        # question that can never be ruled, because the clearance predicate binds by id and nothing else.
        folded = open_questions_policy.append_open_questions(
            packet["chapter_contract"]["open_questions"], extra_open_questions
        )
        packet["chapter_contract"]["open_questions"] = folded
        packet["open_questions"] = master.open_question_texts(folded)

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
    # Telemetry AFTER the packet write, not before: `_persist` may roll back to retry a busy chapter
    # lock, and a rollback here would discard this run's `llm_calls` rows even on a pass that then
    # SUCCEEDS — silently zeroing cost attribution for a retried propose. Both land in the caller's
    # single commit either way.
    # `preserve_approved=True` (#261): a re-propose must NEVER destroy the chapter's approved authority.
    # Previously only the FAILED path asked for this (see the fail-closed caller above), so a SUCCESSFUL
    # re-propose deleted the approved packet — and, once amendment mode exists, its whole `superseded`
    # lineage with it. `_persist`'s own docstring already claimed callers "only replace after confirming no
    # approved packet would be lost"; this makes that true of the success path too. The re-check happens
    # UNDER the lock inside `_persist`, so it also closes the window where an approve commits between here
    # and the acquire.
    persisted = await _persist(session, chapter_id=chapter.id, row=row, replace=True, preserve_approved=True)
    telemetry_db.persist_sink(session, sink, run_id=run_id, book_id=book_id, chapter_id=chapter.id)
    return persisted


def _handle_author_failure(
    author_exc: Exception,
    *,
    chapter: Chapter,
    fail_closed: FailClosed,
    context: str,
) -> Awaitable[ChapterPacket]:
    """Map a raised author call (timeout / budget / API error) to the right fail-closed blocked packet.
    Shared by both propose paths; `context` names the input shape for the stored reason (e.g. 'the chapter
    packet' vs 'the chapter packet from imported evidence')."""
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
        return fail_closed(
            f"Packet Author timed out after {settings.packet_time_budget_s}s while authoring {context}. "
            "Re-propose will likely time out again unless the input/model/budget changes.",
            blocker_source="author",
            blocker_kind="timeout",
            recovery_actions=_AUTHOR_TIMEOUT_ACTIONS,
            blocker_diagnostics=diagnostics,
        )
    label = type(author_exc).__name__
    detail = f": {author_exc}" if str(author_exc) else ""
    return fail_closed(
        f"Packet Author call failed ({label}{detail}).",
        blocker_source="author",
        blocker_kind="call_failed",
        recovery_actions=_AUTHOR_FAILURE_ACTIONS,
        blocker_diagnostics=diagnostics,
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
    budget = _propose_budget()

    # Telemetry: the Packet Author + QA calls roll up under one run row (one propose = one run) so the
    # chapter-packet stage shows in the Desk telemetry like the scene-packet derive. Persisted on EVERY
    # exit (incl. fail-closed) since the author/QA calls may have charged before a failure path.
    sink = telemetry.TelemetrySink()
    run_id = uuid.uuid4()
    fail_closed = _make_fail_closed(session, chapter=chapter, sink=sink, run_id=run_id)

    if not outline:
        return await fail_closed(
            "Chapter has no outline to plan from. Add a chapter outline, then re-propose the packet.",
            blocker_source="input",
            blocker_kind="no_outline",
            recovery_actions=["Add a chapter outline, then re-propose."],
        )

    omniscient = await _omniscient_summary(session, book_id)
    prior_exit = await _prior_exit_state(session, chapter=chapter)
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
        return await _handle_author_failure(
            author_exc, chapter=chapter, fail_closed=fail_closed, context="the chapter packet"
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

    _resolve_provenance(packet, handles)
    source_inputs = {
        "outline_chars": len(outline),
        "prior_exit_state": bool(prior_exit),
        "omniscient_summary": bool(omniscient),
        "canon_handles": [
            {"handle": handle, "id": str(meta.get("id")), "name": meta.get("name")} for handle, meta in handles.items()
        ],
    }
    return await _qa_and_persist(
        session,
        chapter=chapter,
        packet=packet,
        source_inputs=source_inputs,
        lineage={"source": "packet_author"},
        sink=sink,
        run_id=run_id,
        budget=budget,
        progress_key=progress_key,
        fail_closed=fail_closed,
    )


async def propose_packet_from_evidence(
    session: AsyncSession,
    *,
    chapter: Chapter,
    evidence: Sequence[evidence_mod.SceneEvidence],
    retrieve: canon_conflict.CanonRetriever | None = None,
    progress_key: str | None = None,
) -> ChapterPacket:
    """Author -> QA -> persist a ChapterPacket for an imported chapter from its EVIDENCE ledgers (the M#
    bundle) instead of an outline (ADR 0028 Slice 3b import adoption). Fail-closed, and it SHARES
    `propose_packet`'s QA + persist tail — a proposed evidence packet is validated, QA'd, graded, and
    persisted identically to a proposed outline packet.

    The import-adoption worker (Lane A4) orchestrates this: it snapshots the chapter's scenes, ensures
    each `ImportSceneEvidence`, builds the `evidence` bundle, and maps the RESULT — a usable proposed
    packet -> adoption `contract_proposed`; a blocked packet -> adoption `failed` with the blocked packet
    linked as diagnostic (Q14). This function itself only ever returns a ChapterPacket.

    Fail-closed authoring (the author call raised / unparsable output / a thin packet / no evidence to
    plan from) yields a BLOCKED packet — never partial constraints. A USABLE packet is PROPOSED even when
    red or conflict-laden: manuscript-vs-canon conflicts are folded in as open questions that block
    APPROVAL, not adoption (Q14); QA stays advisory.

    `retrieve` is the live locked-canon retrieval seam (author C# handles AND Lane A3's conflict
    re-anchoring both flow through it); it defaults to `canon_conflict.session_retriever` and is injected
    by unit tests. `evidence` may be empty — that fails closed (nothing to reconstruct from).
    """
    book_id = chapter.book_id
    budget = _propose_budget()
    sink = telemetry.TelemetrySink()
    run_id = uuid.uuid4()
    fail_closed = _make_fail_closed(session, chapter=chapter, sink=sink, run_id=run_id)

    manuscript_handles = evidence_mod.build_manuscript_handles(evidence)
    if not manuscript_handles:
        return await fail_closed(
            "No imported-scene evidence to reconstruct this chapter from. Extract scene evidence, then "
            "re-run adoption.",
            blocker_source="input",
            blocker_kind="no_evidence",
            recovery_actions=["Ensure the chapter's imported scenes have extracted evidence, then re-adopt."],
        )

    retrieve = retrieve or canon_conflict.session_retriever(session, book_id, k=_CANON_K)
    omniscient = await _omniscient_summary(session, book_id)
    prior_exit = await _prior_exit_state(session, chapter=chapter)
    # No outline: the canon-retrieval query is built from what the manuscript actually asserts, so the
    # author still sees the locked canon relevant to the imported prose.
    canon_query = evidence_mod.evidence_query(evidence)
    canon_hits = list(await retrieve(canon_query)) if canon_query.strip() else []
    handles: dict[str, dict[str, Any]] = {f"C{i}": dict(hit) for i, hit in enumerate(canon_hits, start=1)}

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
                author_mod.author_packet_from_evidence(
                    chapter_no=chapter.chapter_no,
                    pov=chapter.pov,
                    omniscient_summary=omniscient,
                    prior_exit_state=prior_exit,
                    next_entry_intent=None,
                    canon_handles=handles,
                    manuscript_handles=evidence_mod.rendered_bundle(manuscript_handles),
                    budget=budget,
                ),
                timeout=settings.packet_time_budget_s,
            )
    except Exception as exc:  # noqa: BLE001 — any author failure (timeout/budget/API) must fail closed
        log.error("packet.author_failed", chapter=str(chapter.id), error=str(exc), error_type=type(exc).__name__)
        author_exc = exc
        packet = None

    if author_exc is not None:
        return await _handle_author_failure(
            author_exc, chapter=chapter, fail_closed=fail_closed, context="the chapter packet from imported evidence"
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

    evidence_mod.resolve_evidence_provenance(packet, canon_handles=handles, manuscript_handles=manuscript_handles)

    # Author-time manuscript-vs-canon conflict detection (Lane A3), gated by claim_precedence (Q4). Each
    # candidate re-anchors the CANON side against LIVE locked canon; a re-anchored conflict becomes an
    # encoded manuscript_canon_conflict question, a non-re-anchorable one a plain fail-closed question.
    # BOTH block APPROVAL only — the packet is still a proposed contract (Q14).
    candidates = evidence_mod.candidate_conflicts(manuscript_handles)
    canon_handle_by_id = {str(hit.get("id")): handle for handle, hit in handles.items() if hit.get("id") is not None}
    conflict_result = await canon_conflict.detect_manuscript_canon_conflicts(
        candidates, retrieve=retrieve, canon_handle_by_id=canon_handle_by_id
    )
    extra_open_questions = [
        *conflict_result.open_questions(),
        *(
            evidence_mod.fail_closed_question(fc.manuscript_handle, fc.scene_id, str(fc.reason), fc.detail)
            for fc in conflict_result.fail_closed
        ),
    ]
    if conflict_result.blocks_approval:
        log.info(
            "packet.evidence_conflicts",
            chapter=str(chapter.id),
            reanchored=len(conflict_result.reanchored),
            fail_closed=len(conflict_result.fail_closed),
        )

    source_inputs = {
        "prior_exit_state": bool(prior_exit),
        "omniscient_summary": bool(omniscient),
        "canon_handles": [
            {"handle": handle, "id": str(hit.get("id")), "name": hit.get("name")} for handle, hit in handles.items()
        ],
        "manuscript_handles": [
            {
                "handle": handle,
                "scene_id": str(se.scene_id),
                "scene_no": se.scene_no,
                "scene_version": se.scene_version,
                "prose_hash": se.prose_hash,
            }
            for handle, se in manuscript_handles.items()
        ],
        # The asserted-fact precedence audit (ADR 0029) the packet was authored under — advisory, never a
        # gate; see packet/evidence.precedence_adjudication.
        "precedence": evidence_mod.precedence_adjudication(packet.get("claims", [])),
    }
    return await _qa_and_persist(
        session,
        chapter=chapter,
        packet=packet,
        source_inputs=source_inputs,
        lineage={"source": "import_adoption"},
        sink=sink,
        run_id=run_id,
        budget=budget,
        progress_key=progress_key,
        fail_closed=fail_closed,
        extra_open_questions=extra_open_questions,
    )


async def _persist(
    session: AsyncSession,
    *,
    chapter_id: uuid.UUID,
    row: ChapterPacket,
    replace: bool = False,
    preserve_approved: bool = False,
) -> ChapterPacket:
    """Add the new packet; with replace, clear prior packets for the chapter first so GET returns
    exactly one current packet. Callers only replace after confirming no approved packet would be
    lost (a failed re-propose returns the existing approved packet instead of reaching here).

    This is the ONLY production INSERT/replace of a ChapterPacket, reached by both propose paths
    (the router's background author and the adoption worker's evidence author), so it is where the
    chapter workflow lock belongs (ADR-0028 "ChapterPacket propose/replace"; #259).

    It takes the LOCK PRIMITIVE, not `run_under_chapter_workflow`, deliberately:
      * the authoring that precedes this runs 1-2 minutes of model calls and MUST NOT hold the lock
        (`shared/chapter_lock.py:20-22`); only this short write is serialized;
      * the caller's session is already dirty here — `telemetry_db.persist_sink` writes `llm_calls`
        rows just above — which violates the wrapper's clean-transaction precondition, and the
        wrapper would take ownership of a commit boundary that belongs to the caller.
    The advisory lock is transaction-scoped, so it is held from here until the caller's commit
    (immediately after) and released by it. Acquired BEFORE the delete so the advisory lock is always
    taken ahead of row locks, the ordering `chapter_lock.py:110-112` requires.

    THIS FUNCTION MAY ROLL BACK THE CALLER'S TRANSACTION. A fired `lock_timeout` aborts the PG
    transaction, so a retry has to clear it before issuing anything else. Callers must therefore treat
    every ORM instance they hold as expired across this call (in async, a later plain attribute read on
    an expired instance raises `MissingGreenlet`). Today neither caller reads one — `_run_propose`
    commits immediately, and `run_one_adoption` only touches the returned `packet`.

    BOUNDED RETRY. Losing this acquisition throws away 1-2 minutes of already-paid model work, while
    the writes it contends with are all short — so a busy acquire is retried `PERSIST_LOCK_ATTEMPTS`
    times before giving up (worst case ≈ `PERSIST_LOCK_ATTEMPTS × PERSIST_LOCK_TIMEOUT_MS` ≈ 30s of a
    pooled connection, plus backoff). `row` is still TRANSIENT here — `session.add` happens below, after
    the loop — so the rollback cannot expunge it and it is added exactly once. Telemetry written by the
    caller before this call IS lost to the rollback; see the caller's note on ordering. Exhausting the
    attempts re-raises `ChapterWorkflowBusy`; both callers handle it.

    KNOWN LIMIT: `acquire_chapter_workflow_lock` issues `SET LOCAL lock_timeout`, which applies for the
    REST of the transaction, not just the acquire. This transaction continues through the delete, the
    insert, and the caller's commit — so a later row-lock wait beyond the ceiling aborts with a raw
    `OperationalError` (SQLSTATE 55P03), NOT `ChapterWorkflowBusy`, and is therefore not caught by the
    busy handlers in `_run_propose` / `run_one_adoption`. Pre-existing property of the primitive, newly
    applied to a longer-lived transaction here."""
    # max(1, ...): a misconfigured 0 would skip the loop body entirely and write with NO lock at all —
    # fail-open in the one function the whole design declares to be the single lock point.
    attempts_allowed = max(1, PERSIST_LOCK_ATTEMPTS)
    for attempt in range(1, attempts_allowed + 1):
        try:
            await acquire_chapter_workflow_lock(session, chapter_id, timeout_ms=PERSIST_LOCK_TIMEOUT_MS)
            break
        except ChapterWorkflowBusy:
            if attempt == attempts_allowed:
                log.warning("packet.persist_chapter_busy_giving_up", chapter=str(chapter_id), attempts=attempt)
                raise
            # The failed acquire aborted the transaction; clear it before issuing anything else.
            await session.rollback()
            log.info("packet.persist_chapter_busy_retry", chapter=str(chapter_id), attempt=attempt)
            await asyncio.sleep(PERSIST_LOCK_RETRY_S * attempt)
    if preserve_approved:
        # ADR-0028 protocol steps 3-4, re-done UNDER the lock. The fail-closed caller checks this too,
        # but that check is only a fast path: it runs before the lock, so an `approve_packet` that
        # commits between it and this acquire would otherwise be destroyed by the replace below. The
        # authoritative decision is this one.
        existing = await latest_approved(session, chapter_id)
        if existing is not None:
            log.info("packet.persist_preserved_approved", chapter=str(chapter_id), packet=str(existing.id))
            return existing
    if replace:
        # SCOPED to the replaceable states (#261). An unqualified chapter-wide delete would take the
        # APPROVED authority and every `SUPERSEDED` row with it — and a superseded packet is the immutable
        # record of which contract the book was written against before an amendment, so destroying it
        # silently erases provenance invariant 7 exists to keep. `preserve_approved` above already returns
        # early when an approved packet exists, so this predicate is the second, structural line of defence
        # rather than the only one: a future caller that forgets the flag still cannot delete authority or
        # history. PROPOSED and BLOCKED are the genuinely transient rows a re-propose is entitled to clear.
        await session.execute(
            delete(ChapterPacket).where(
                ChapterPacket.chapter_id == chapter_id,
                ChapterPacket.status.in_((PacketStatus.PROPOSED.value, PacketStatus.BLOCKED.value)),
            )
        )
        await session.flush()
    session.add(row)
    await session.flush()
    return row
