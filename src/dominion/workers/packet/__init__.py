"""Chapter packet orchestration (contract-first drafting, Phase 1).

`propose_packet` runs the Packet Author then the Packet QA, derives a confidence + status, and
persists a ChapterPacket. It is FAIL-CLOSED: any malformed/empty/timed-out agent output yields a
`blocked` packet rather than partial drafting constraints — a weak packet must never quietly become
the gate. A failed re-propose never wipes an already-approved packet (mirrors the beats path).

Two safety jobs live here, not in the agents:
  * provenance — claim `source_id` handles (C1, C2, …) are resolved back to real canon ids + titles;
  * stable ids — every scene seed gets a server-minted `seed_id` (the sync key for later phases),
    never a model-supplied one.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import structlog
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from dominion.shared.config import settings
from dominion.shared.enums import PacketConfidence, PacketStatus, PacketVerdict
from dominion.shared.models import Chapter, ChapterPacket, Summary
from dominion.workers import progress, telemetry, telemetry_db
from dominion.workers.budget import TokenBudget
from dominion.workers.memory import canon_rag
from dominion.workers.packet import approval_policy
from dominion.workers.packet import author as author_mod
from dominion.workers.packet import qa as qa_mod
from dominion.workers.packet.validation import validate_chapter_packet_contract

log = structlog.get_logger()

_CANON_K = 16  # the author gets broad canon (scoping protects the writer, not the planner)
_EXCERPT_CHARS = 240


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
) -> ChapterPacket:
    qa_warnings: dict[str, Any] = {"residual_risks": [], "blocked_reason": reason}
    if violations:
        # Persist WHICH gate blocked (deterministic validation, not QA) so the UI doesn't mislabel a
        # decidable roster contradiction as an LLM QA verdict — same distinction the scene-packet UI
        # already makes for its own deterministic-vs-QA blocks.
        qa_warnings["violations"] = violations
        qa_warnings["blocker_source"] = "validation"
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
            ),
        )

    if not outline:
        return await fail_closed("chapter has no outline to plan from")

    omniscient = await _omniscient_summary(session, book_id)
    prior_exit = await _prior_exit_state(session, book_id=book_id, chapter_no=chapter.chapter_no)
    canon_meta = await canon_rag.retrieve_with_meta(session, book_id=book_id, query=outline, k=_CANON_K)
    handles = {f"C{i}": meta for i, meta in enumerate(canon_meta, start=1)}

    # Three distinct fail-closed paths, kept distinguishable in both the stored reason and the logs so a
    # block is debuggable after the fact (they used to collapse into one generic, unlogged reason):
    #   1. the author *call* raised — timeout / budget / API error (logged below);
    #   2. it returned text we couldn't parse to an object — usually truncation (see llm.truncated);
    #   3. it parsed but the packet was too thin (no scene seeds or no claims list).
    author_error: str | None = None
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
        log.error("packet.author_failed", chapter=str(chapter.id), error=str(exc))
        author_error = type(exc).__name__
        packet = None

    if author_error is not None:
        return await fail_closed(f"packet author call failed ({author_error})")
    if packet is None:
        log.warning("packet.author_unparsable", chapter=str(chapter.id))
        return await fail_closed("packet author response could not be parsed (possibly truncated)")
    if not _valid_packet(packet):
        log.warning("packet.author_thin", chapter=str(chapter.id))
        return await fail_closed("packet author returned an incomplete packet (no scene seeds or claims)")

    _mint_seed_ids(packet)
    _resolve_provenance(packet, handles)

    # Deterministic roster-consistency check runs BEFORE QA: a character double-bucketed across
    # present/absent/mentioned-only/forbidden, or a forbidden name bled into the chapter's own scene
    # seeds, is a decidable self-contradiction QA should not need to guess at. A hard blocker skips QA
    # entirely (no point attacking a packet already known internally inconsistent).
    violations = validate_chapter_packet_contract(packet)
    block_violations = [v for v in violations if v.severity == "block"]
    if block_violations:
        log.warning(
            "packet.validation_blocked",
            chapter=str(chapter.id),
            count=len(block_violations),
            kinds=sorted({v.kind for v in block_violations}),
        )
        return await fail_closed(
            "deterministic validation failed: " + "; ".join(v.detail for v in block_violations),
            body=packet,
            violations=[v.as_dict() for v in violations],
            open_questions={"items": _open_questions(packet)},
        )

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
        return await fail_closed("packet QA returned no usable verdict", body=packet)

    confidence, status = approval_policy.status_from_qa(packet, qa)
    qa_warnings: dict[str, Any] = {"residual_risks": qa["residual_risks"], "issues": qa["issues"]}
    if violations:
        # Only warn-severity violations reach here (any block already returned above), surfaced
        # alongside QA's own findings so the editor sees both channels.
        qa_warnings["violations"] = [v.as_dict() for v in violations]
    row = ChapterPacket(
        book_id=book_id,
        chapter_id=chapter.id,
        status=status,
        confidence=confidence,
        qa_verdict=qa["verdict"],
        qa_warnings=qa_warnings,
        body=packet,
        open_questions={"items": _open_questions(packet)},
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
