"""Draft readiness queries — read-only diagnostics for contract-first drafting."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dominion.shared.enums import (
    BeatStatus,
    ChapterSequenceStatus,
    JobKind,
    JobStatus,
    PacketStatus,
    ScenePacketStatus,
    ScenePacketVerdict,
)
from dominion.shared.models import Beat, Chapter, ChapterPacket, ChapterSequence, Job, Scene, ScenePacket
from dominion.shared.schemas import DraftQueueBlockerOut, DraftReadinessOut, StructuralBlockerOut
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


# --- authoritative draft gate (recovery L8) --------------------------------------------------------
# Pure functions over counts — no ORM, no DB — unit-tested in tests/test_draft_readiness_gates.py.
# The gate order is FIXED pipeline order: packet → sequence/budget → scene packets (stale/QA) →
# beats → jobs → prose coverage → provider rate limit. `resolve_draft_gate` names the FIRST failing
# gate in one human sentence so the Desk never renders a disabled Draft button (or hides a "ready"
# claim behind one) without saying exactly why. Kept separable from the readiness query below —
# lanes 3/6 also touch this module.


def _norm(text: object) -> str:
    """Whitespace/case-insensitive comparison key for contract strings."""
    return " ".join(str(text).split()).casefold()


def _str_list(value: object) -> list[str]:
    return [str(v).strip() for v in value if str(v).strip()] if isinstance(value, list) else []


def _fmt_scenes(nums: tuple[int, ...] | list[int]) -> str:
    return ", ".join(str(n) for n in nums)


def sequence_budget_blockers(
    *,
    seed_count: int,
    sequence_scene_count: int | None,
    sequence_hard_max_words: int | None,
    scene_hard_max_total: int | None,
) -> list[StructuralBlockerOut]:
    """`sequence_budget_mismatch`: the approved sequence plan and the scene contracts disagree on
    arithmetic that no LLM call can fix (Ch1 failure §3: scene hard_max summed to 10,400 against a
    7,200-word chapter hard max — the overrun was guaranteed before drafting started)."""
    out: list[StructuralBlockerOut] = []
    if sequence_scene_count is not None and seed_count and sequence_scene_count != seed_count:
        out.append(
            StructuralBlockerOut(
                kind="sequence_budget_mismatch",
                message=(
                    f"The chapter sequence plans {sequence_scene_count} scenes but the chapter packet seeds "
                    f"{seed_count} — re-derive the sequence or fix the packet's scene seeds before drafting."
                ),
            )
        )
    if (
        sequence_hard_max_words is not None
        and scene_hard_max_total is not None
        and scene_hard_max_total > sequence_hard_max_words
    ):
        out.append(
            StructuralBlockerOut(
                kind="sequence_budget_mismatch",
                message=(
                    f"Scene word budgets sum to {scene_hard_max_total} hard-max words against a chapter hard max "
                    f"of {sequence_hard_max_words} — rebalance the scene budgets before drafting."
                ),
            )
        )
    return out


def scene_scope_bleed_blockers(links: list[tuple[int, int]]) -> list[StructuralBlockerOut]:
    """`scene_scope_bleed`: an approved beat is linked to another scene's contract, so its draft
    would be written against the wrong scope. `links` = (beat scene_no, linked packet scene_no)."""
    return [
        StructuralBlockerOut(
            kind="scene_scope_bleed",
            message=(
                f"The beat for scene {beat_no} is linked to the scene-{packet_no} contract — scene scope is "
                "bleeding across contracts; re-derive beats from the approved scene packets."
            ),
        )
        for beat_no, packet_no in links
        if beat_no != packet_no
    ]


def duplicate_irreversible_beat_blockers(
    *,
    beat_scene_nos: list[int],
    scene_seeds: list[dict[str, Any]],
) -> list[StructuralBlockerOut]:
    """`duplicate_irreversible_beat`: the same irreversible change is staged more than once (Ch1
    failure §2: recognition beats re-performed across scenes 2-4). Two cheap detections: (a) a scene
    with more than one approved beat (each would queue its own draft job), and (b) the same
    irreversible_state_change text seeded into two different scenes."""
    out: list[StructuralBlockerOut] = []
    counts: dict[int, int] = {}
    for n in beat_scene_nos:
        counts[n] = counts.get(n, 0) + 1
    for n in sorted(k for k, c in counts.items() if c > 1):
        out.append(
            StructuralBlockerOut(
                kind="duplicate_irreversible_beat",
                message=(
                    f"Scene {n} has {counts[n]} approved beats — duplicated beats re-perform the scene's "
                    "irreversible changes; prune the extra beats before drafting."
                ),
            )
        )
    seen: dict[str, tuple[str, list[int]]] = {}
    for i, seed in enumerate(scene_seeds):
        change = seed.get("irreversible_state_change")
        if not isinstance(change, str) or not change.strip():
            continue
        scene_no = seed.get("scene_no")
        display_no = scene_no if isinstance(scene_no, int) else i + 1
        seen.setdefault(_norm(change), (change.strip(), []))[1].append(display_no)
    for text, nos in seen.values():
        if len(nos) > 1:
            out.append(
                StructuralBlockerOut(
                    kind="duplicate_irreversible_beat",
                    message=(
                        f"The irreversible change '{text}' is seeded in scenes {_fmt_scenes(nos)} — an "
                        "irreversible beat must happen exactly once; fix the packet's scene seeds."
                    ),
                )
            )
    return out


def canon_contract_leak_blockers(
    *,
    packets: list[tuple[int, dict[str, Any]]],
    chapter_forbidden: list[str],
) -> list[StructuralBlockerOut]:
    """`canon_contract_leak`: a scene contract lets the reader learn something the chapter packet
    forbids (or that the same contract must keep hidden) — the drafter would obey the leak, and QA
    missed exactly this in Ch1 (§4). `packets` = (scene_no, scene packet body)."""
    out: list[StructuralBlockerOut] = []
    forbidden = {_norm(x) for x in chapter_forbidden}
    for scene_no, body in packets:
        learned = body.get("learned_during_scene") if isinstance(body, dict) else None
        learned = learned if isinstance(learned, dict) else {}
        reveals = _str_list(learned.get("reader_must_learn")) + _str_list(learned.get("reader_may_learn"))
        hidden_group = body.get("must_remain_hidden") if isinstance(body, dict) else None
        hidden_group = hidden_group if isinstance(hidden_group, dict) else {}
        own_hidden = {
            _norm(x) for x in _str_list(hidden_group.get("reader")) + _str_list(hidden_group.get("all_surface_prose"))
        }
        for reveal in reveals:
            key = _norm(reveal)
            if key in forbidden:
                out.append(
                    StructuralBlockerOut(
                        kind="canon_contract_leak",
                        message=(
                            f"Scene {scene_no}'s contract lets the reader learn '{reveal}', which the chapter "
                            "packet forbids — fix the scene contract or the chapter packet."
                        ),
                    )
                )
            elif key in own_hidden:
                out.append(
                    StructuralBlockerOut(
                        kind="canon_contract_leak",
                        message=(
                            f"Scene {scene_no}'s contract both reveals and hides '{reveal}' — resolve the "
                            "contradiction before drafting."
                        ),
                    )
                )
    return out


@dataclass(frozen=True)
class DraftGateInputs:
    """Everything the authoritative gate needs, as plain counts — cheap to assemble, pure to test."""

    chapter_packet_approved: bool = False
    structural_blockers: tuple[StructuralBlockerOut, ...] = ()
    scene_packets_derived: int = 0
    scene_packets_approved: int = 0
    missing_scene_packets: tuple[int, ...] = ()
    scene_packets_stale: int = 0
    scene_packet_qa_blocking: int = 0
    approved_beats: int = 0
    unlinked_beats: int = 0
    queue_blocker_messages: tuple[str, ...] = ()
    active_draft_jobs: int = 0
    draftable_scenes: int = 0
    missing_scene_drafts: tuple[int, ...] = ()
    provider_rate_limited: bool = False


def resolve_draft_gate(g: DraftGateInputs) -> tuple[bool, str | None]:
    """(can_draft, disabled_reason) — mutually consistent by construction: exactly one of
    `can_draft=True` / `disabled_reason is not None` holds. The reason names the FIRST failing gate
    in pipeline order: packet → sequence/budget → scene packets (stale/QA) → beats → jobs → prose
    coverage → provider rate limit."""
    # 1. Chapter packet — nothing downstream exists without the approved macro contract.
    if not g.chapter_packet_approved:
        return False, "Chapter packet is not approved yet — approve it first."
    # 2. Sequence/budget + structural contract faults — arithmetic/scoping errors that guarantee a
    # bad chapter before any prose is generated.
    if g.structural_blockers:
        return False, g.structural_blockers[0].message
    # 3. Scene packets — coverage, then staleness, then QA verdicts (contract axis before QA axis).
    if g.scene_packets_approved == 0:
        return False, (
            f"No approved scene packets ({g.scene_packets_derived} derived) — approve the scene packets first."
            if g.scene_packets_derived
            else "No scene packets derived yet — derive scene packets first."
        )
    if g.missing_scene_packets:
        return False, (
            f"Scene packet(s) for scene(s) {_fmt_scenes(g.missing_scene_packets)} are not approved yet — "
            "approve or re-derive them before drafting."
        )
    if g.scene_packets_stale:
        return False, (
            f"{g.scene_packets_stale} scene packet(s) are stale — re-derive or re-approve them before drafting."
        )
    if g.scene_packet_qa_blocking:
        return False, (
            f"Scene-packet QA blocks drafting on {g.scene_packet_qa_blocking} scene packet(s) — "
            "fix the contract and re-run QA, or re-derive."
        )
    # 4. Beats — the routing projection of the approved contracts.
    if g.approved_beats == 0:
        return False, "No approved beats yet — approving scene packets derives the chapter's beats."
    if g.unlinked_beats:
        return False, (
            f"{g.unlinked_beats} of {g.approved_beats} approved beats are not linked to an approved "
            "scene packet — approve (or re-derive) the scene packets for those scenes so beats re-link."
        )
    if g.queue_blocker_messages:
        return False, (f"{len(g.queue_blocker_messages)} draft-queue blocker(s): {g.queue_blocker_messages[0]}")
    # 5. Jobs — never double-queue while drafting is already running.
    if g.active_draft_jobs:
        return False, f"Scene drafting is already in progress ({g.active_draft_jobs} active draft job(s))."
    # 6. Prose coverage — nothing left for the Draft action to do.
    if g.draftable_scenes == 0:
        if g.missing_scene_drafts:
            return False, (
                f"Scene(s) {_fmt_scenes(g.missing_scene_drafts)} have draft rows but no prose — "
                "use redraft to regenerate them."
            )
        return False, "Every scene already has a draft — use redraft to regenerate a scene."
    # 7. Provider rate limit — transient infrastructure hold, checked last so a real contract fault
    # is never misreported as a 429 (Ch1 failure §6).
    if g.provider_rate_limited:
        return False, "The provider is rate-limiting scene generation — wait a moment and retry."
    return True, None


# --- readiness query -------------------------------------------------------------------------------


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
    cp_body: dict[str, Any] = (cp.body or {}) if cp else {}
    seeds_raw = cp_body.get("scene_seeds", [])
    seeds: list[dict[str, Any]] = [s for s in seeds_raw if isinstance(s, dict)] if isinstance(seeds_raw, list) else []
    seed_count = len(seeds_raw) if isinstance(seeds_raw, list) else 0
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

    # --- structural blockers (recovery L8) — deterministic contract faults, cheap over loaded rows.
    # One extra query total (the chapter sequence); everything else reuses rows already in memory.
    sequence = None
    if cp is not None:
        sequence = (
            (
                await session.execute(
                    select(ChapterSequence)
                    .where(
                        ChapterSequence.chapter_id == chapter_id,
                        ChapterSequence.chapter_packet_id == cp.id,
                        ChapterSequence.status != ChapterSequenceStatus.STALE,
                    )
                    .order_by(ChapterSequence.created_at.desc())
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
    hard_max_values = [
        (p.body or {}).get("word_budget", {}).get("hard_max")
        for p in approved_sp
        if isinstance((p.body or {}).get("word_budget"), dict)
    ]
    scene_hard_max_total = sum(v for v in hard_max_values if isinstance(v, int)) or None
    structural: list[StructuralBlockerOut] = []
    structural += sequence_budget_blockers(
        seed_count=seed_count,
        sequence_scene_count=sequence.target_scene_count if sequence else None,
        sequence_hard_max_words=sequence.hard_max_words if sequence else None,
        scene_hard_max_total=scene_hard_max_total,
    )
    structural += scene_scope_bleed_blockers(
        [
            (b.scene_no, packet_by_id[b.scene_packet_id].scene_no)
            for b in approved_beats
            if b.scene_no is not None and b.scene_packet_id is not None and b.scene_packet_id in packet_by_id
        ]
    )
    structural += duplicate_irreversible_beat_blockers(
        beat_scene_nos=[b.scene_no for b in approved_beats if b.scene_no is not None],
        scene_seeds=seeds,
    )
    structural += canon_contract_leak_blockers(
        packets=[(p.scene_no, p.body or {}) for p in approved_sp],
        chapter_forbidden=_str_list(cp_body.get("forbidden_reveals")) + _str_list(cp_body.get("forbidden_knowledge")),
    )

    qa_blocking = sum(
        1
        for p in sp_rows
        if p.status != ScenePacketStatus.RATE_LIMITED and (p.qa_verdict or "") == ScenePacketVerdict.BLOCK_DRAFTING
    )

    can_draft, disabled_reason = resolve_draft_gate(
        DraftGateInputs(
            chapter_packet_approved=chapter_packet_approved,
            structural_blockers=tuple(structural),
            scene_packets_derived=len(sp_rows),
            scene_packets_approved=len(approved_sp),
            missing_scene_packets=tuple(missing),
            scene_packets_stale=len(stale_sp),
            scene_packet_qa_blocking=qa_blocking,
            approved_beats=len(approved_beats),
            unlinked_beats=len(unlinked),
            queue_blocker_messages=tuple(b.message for b in blockers),
            active_draft_jobs=int(active_jobs),
            draftable_scenes=draftable_scenes,
            missing_scene_drafts=tuple(missing_prose),
            provider_rate_limited=len(rate_limited_sp) > 0,
        )
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
        scene_packets_stale=len(stale_sp),
        scene_packet_qa_blocking=qa_blocking,
        active_draft_jobs=int(active_jobs),
        missing_scene_drafts=missing_prose,
        structural_blockers=structural,
        provider_rate_limited=len(rate_limited_sp) > 0,
        can_draft=can_draft,
    )
