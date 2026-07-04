"""Draft readiness queries — read-only diagnostics for contract-first drafting."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dominion.shared.enums import BeatStatus, JobKind, JobStatus, PacketStatus, ScenePacketStatus
from dominion.shared.models import Beat, Chapter, ChapterPacket, ChapterSequence, Job, Scene, ScenePacket
from dominion.shared.schemas import DraftQueueBlockerOut, DraftReadinessOut
from dominion.workers.budget_reconciliation import check_sequence_budget_consistency
from dominion.workers.draft_queue import DraftQueueBlocker, resolve_approved_scene_packet_for_beat_prefetched


def blocker_out(b: DraftQueueBlocker) -> DraftQueueBlockerOut:
    return DraftQueueBlockerOut(
        chapter_id=b.chapter_id,
        scene_no=b.scene_no,
        beat_id=b.beat_id,
        scene_packet_id=b.scene_packet_id,
        reason=b.reason,
        message=b.message,
        required_action=b.required_action,
    )


async def compute_draft_readiness(session: AsyncSession, chapter_id: uuid.UUID) -> DraftReadinessOut:
    chapter = await session.get(Chapter, chapter_id)
    if chapter is None:
        raise ValueError("chapter not found")

    cp = (
        await session.execute(
            select(ChapterPacket)
            .where(
                ChapterPacket.chapter_id == chapter_id,
                ChapterPacket.status == PacketStatus.APPROVED,
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    chapter_packet_approved = cp is not None

    sp_rows = list(
        (await session.execute(select(ScenePacket).where(ScenePacket.chapter_id == chapter_id))).scalars().all()
    )
    approved_sp = [p for p in sp_rows if p.status == ScenePacketStatus.APPROVED]
    stale_sp = [p for p in sp_rows if p.status == ScenePacketStatus.STALE]
    blocked_sp = [p for p in sp_rows if p.status == ScenePacketStatus.BLOCKED]
    rate_limited_sp = [p for p in sp_rows if p.status == ScenePacketStatus.RATE_LIMITED]
    seed_count = len((cp.body or {}).get("scene_seeds", [])) if cp else 0
    approved_nos = {p.scene_no for p in approved_sp}
    missing = sorted(set(range(1, seed_count + 1)) - approved_nos) if seed_count else []

    beats = list(
        (await session.execute(select(Beat).where(Beat.chapter_id == chapter_id).order_by(Beat.scene_no)))
        .scalars()
        .all()
    )
    approved_beats = [b for b in beats if b.status == BeatStatus.APPROVED]
    linked = [b for b in approved_beats if b.scene_packet_id is not None]
    unlinked = [b.id for b in approved_beats if b.scene_packet_id is None]

    # Mirror schedule_undrafted_beats(skip_drafted=True): a beat whose scene already has prose is not
    # queueable — redraft is the path for those. Excluding already-drafted scenes here keeps `draftable`
    # honest, so a fully-drafted chapter reports draftable=False instead of enabling a "Draft chapter"
    # click that skips every beat and 409s. "Drafted" matches the scheduler exactly: any Scene row for
    # that scene_no (draft_queue.py:244-247). Prose coverage is stricter — the latest row per scene_no
    # must carry non-empty prose (matching production's `(scene.prose or "").strip()` test) — and gates
    # chapter ASSEMBLY, which concatenates prose and hard-fails `missing_scene` on every gap.
    scene_rows = (
        await session.execute(
            select(Scene.scene_no, Scene.prose)
            .where(Scene.chapter_id == chapter_id)
            .order_by(Scene.scene_no, Scene.created_at)
        )
    ).all()
    latest_prose: dict[int, str] = {}
    for scene_no, prose in scene_rows:  # ordered by created_at, so the latest row per scene_no wins
        latest_prose[scene_no] = prose or ""
    drafted_scene_nos = set(latest_prose)
    prose_scene_nos = sorted(n for n, p in latest_prose.items() if p.strip())
    expected_scenes = seed_count or len({p.scene_no for p in sp_rows})
    missing_prose = sorted(set(range(1, expected_scenes + 1)) - set(prose_scene_nos)) if expected_scenes else []

    active_jobs = (
        await session.execute(
            select(func.count())
            .select_from(Job)
            .where(
                Job.chapter_id == chapter_id,
                Job.kind == JobKind.DRAFT,
                Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]),
            )
        )
    ).scalar_one() or 0

    malformed = (
        await session.execute(
            select(func.count())
            .select_from(Job)
            .where(
                Job.chapter_id == chapter_id,
                Job.kind == JobKind.DRAFT,
                Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.FAILED]),
                Job.scene_packet_id.is_(None),
            )
        )
    ).scalar_one() or 0

    sp_required_failed = (
        await session.execute(
            select(func.count())
            .select_from(Job)
            .where(
                Job.chapter_id == chapter_id,
                Job.kind == JobKind.DRAFT,
                Job.status == JobStatus.FAILED,
                Job.last_error.ilike("%ScenePacket%"),
            )
        )
    ).scalar_one() or 0

    # Resolve every beat against the already-loaded packet rows (read-only twin of the queue
    # resolver) — the old per-beat DB resolution was an N+1 that alone put multiple seconds on
    # GET /draft/readiness over a networked Postgres.
    packet_by_id = {p.id: p for p in sp_rows}
    packets_by_scene_no: dict[int, list[ScenePacket]] = {}
    for p in sp_rows:
        packets_by_scene_no.setdefault(p.scene_no, []).append(p)

    blockers: list[DraftQueueBlockerOut] = []
    draftable_scenes = 0
    for beat in approved_beats:
        resolved = resolve_approved_scene_packet_for_beat_prefetched(
            beat, packet_by_id=packet_by_id, packets_by_scene_no=packets_by_scene_no
        )
        if isinstance(resolved, DraftQueueBlocker):
            blockers.append(blocker_out(resolved))
        elif beat.scene_no not in drafted_scene_nos:
            draftable_scenes += 1

    # ── Sequence budget envelope (lane 3) — structural pre-draft blocker ─────────────────────────
    # The persisted sequence must be arithmetically self-consistent — scene word_budget.hard_max
    # values must SUM within the chapter's hard_max_words — before any LLM spend (the ch1 bad run
    # drafted 9,630 words against a 7,200 envelope because nothing compared the two). At most ONE
    # chapter-level `sequence_budget_mismatch` blocker, prepended so it names the root cause first.
    sequence = (
        (
            await session.execute(
                select(ChapterSequence)
                .where(ChapterSequence.chapter_id == chapter_id)
                .order_by(ChapterSequence.updated_at.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )
    if sequence is not None:
        seq_scenes = (sequence.body or {}).get("scenes") or []
        for issue in check_sequence_budget_consistency(
            sequence.hard_max_words,
            [s.get("word_budget") or {} for s in seq_scenes if isinstance(s, dict)],
        ):
            blockers.insert(
                0,
                DraftQueueBlockerOut(
                    chapter_id=chapter_id,
                    reason=issue.kind,
                    message=issue.detail,
                    required_action=(
                        "Re-derive the chapter sequence and scene packets (freshly derived budgets "
                        "now reconcile against the chapter envelope), or fix the sequence word "
                        "budgets, before drafting."
                    ),
                ),
            )
    # ── end lane 3 ────────────────────────────────────────────────────────────────────────────────

    draftable = (
        chapter_packet_approved
        and len(approved_sp) > 0
        and len(unlinked) == 0
        and len(blockers) == 0
        and draftable_scenes > 0
    )

    # Name the FIRST failing gate in plain language, in the same priority order `draftable` checks
    # them — so the Desk never shows a disabled Draft button without saying exactly why.
    disabled_reason: str | None = None
    if not chapter_packet_approved:
        disabled_reason = "Chapter packet is not approved yet — approve it first."
    elif len(approved_sp) == 0:
        disabled_reason = (
            f"No approved scene packets ({len(sp_rows)} derived) — approve the scene packets first."
            if sp_rows
            else "No scene packets derived yet — derive scene packets first."
        )
    elif unlinked:
        disabled_reason = (
            f"{len(unlinked)} of {len(approved_beats)} approved beats are not linked to an approved "
            "scene packet — approve (or re-derive) the scene packets for those scenes so beats re-link."
        )
    elif blockers:
        disabled_reason = f"{len(blockers)} draft-queue blocker(s): {blockers[0].message}"
    elif draftable_scenes == 0:
        disabled_reason = (
            f"Scene drafting is already in progress ({int(active_jobs)} active draft job(s))."
            if active_jobs
            else "Every scene already has a draft — use redraft to regenerate a scene."
        )

    return DraftReadinessOut(
        chapter_id=chapter_id,
        chapter_packet_approved=chapter_packet_approved,
        scene_packets={
            "approved": len(approved_sp),
            "blocked": len(blocked_sp),
            "stale": len(stale_sp),
            "rate_limited": len(rate_limited_sp),
            "expected": expected_scenes,
            "missing_scene_numbers": missing,
        },
        beats={
            "approved": len(approved_beats),
            "linked": len(linked),
            "unlinked": [str(i) for i in unlinked],
        },
        jobs={
            "active": int(active_jobs),
            "malformed": int(malformed),
            "failed_scene_packet_required": int(sp_required_failed),
        },
        prose={
            "scenes_with_prose": len(prose_scene_nos),
            "expected_scenes": expected_scenes,
            "missing_scene_numbers": missing_prose,
            # The production-assembly gate: assembling with gaps hard-fails one missing_scene per gap.
            "assembly_ready": expected_scenes > 0 and not missing_prose,
        },
        draftable=draftable,
        disabled_reason=disabled_reason,
        blockers=blockers,
    )
