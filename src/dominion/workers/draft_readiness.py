"""Draft readiness queries — read-only diagnostics for contract-first drafting."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dominion.shared import job_policy
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
from dominion.shared.text_match import collect_strings, get_dotted
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
# The gate order is FIXED pipeline order: packet → sequence/budget/structural → scene packets
# (coverage/stale) → beats → jobs → prose coverage → provider rate limit. `resolve_draft_gate` names
# the FIRST failing gate in one human sentence so the Desk never renders a disabled Draft button (or
# hides a "ready" claim behind one) without saying exactly why. Kept separable from the readiness
# query below — lanes 3/6 also touch this module.
#
# EVERY input to this gate is deterministic (#278). Nothing an LLM returned may decide it.


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
    sequence_id: uuid.UUID | None = None,
) -> list[StructuralBlockerOut]:
    """Two deterministic contract-arithmetic gates:

    `sequence_scene_count_mismatch` — the sequence's PLANNING TARGET disagrees with the packet's
    authored seed list. The sequence's scenes[] is always one-per-seed, so this is target-vs-actual
    only; the blocker carries sequence_id/seed_count so the Desk can offer the one-click
    "Align plan to N seeded scenes" fix (re-derive just reproduces the same estimate).

    `sequence_budget_mismatch` — word arithmetic no LLM call can fix (Ch1 failure §3: scene
    hard_max summed to 10,400 against a 7,200-word chapter hard max)."""
    out: list[StructuralBlockerOut] = []
    if sequence_scene_count is not None and seed_count and sequence_scene_count != seed_count:
        out.append(
            StructuralBlockerOut(
                kind="sequence_scene_count_mismatch",
                message=(
                    f"The chapter sequence plans {sequence_scene_count} scenes but the chapter packet seeds "
                    f"{seed_count} — align the plan to the seeded scenes, or fix the packet's scene seeds."
                ),
                sequence_id=sequence_id,
                planned_scene_count=sequence_scene_count,
                seed_count=seed_count,
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


# --- the exposure matrix (#278) --------------------------------------------------------------------
# The scene-contract fields that EXPOSE a fact, split by WHO they expose it to. This is the field list
# `scene_packet/qa.py:_SYSTEM` already recites when it asks the model to look for "a hidden/author-only
# fact that has ALSO leaked into a reader-known or POV-known field ... or into an on-page field" — and
# `qa.py`'s own rule for when that is a defect ("Only flag when the SAME fact is found in BOTH an
# author-only field AND a reader/POV/on-page field"). Mechanized here so the finding no longer depends
# on the model choosing to report it: an LLM verdict is a nomination, never a gate (#278, ADR-0031 R3).
_READER_VISIBLE_PATHS: tuple[str, ...] = (
    "known_before_scene.reader",
    "learned_during_scene.reader_must_learn",
    "learned_during_scene.reader_may_learn",
    "learned_during_scene.reader_may_infer_only",
)
_POV_VISIBLE_PATHS: tuple[str, ...] = (
    "known_before_scene.pov",
    "pov_permissions.may_notice",
    "pov_permissions.may_infer",
)
#: Fields that put a fact into the drafted prose itself — visible to reader and POV alike.
_ON_PAGE_PATHS: tuple[str, ...] = ("required_beats", "exit_state")

# (hidden-declaration path -> the paths that contradict it). Only `must_remain_hidden.*` is a
# DECLARATION that a fact is withheld, so only it can be contradicted. `known_before_scene
# .omniscient_author` is deliberately NOT a source: it is what the AUTHOR knows, which legitimately
# includes everything the reader knows, so pairing it would fail the writer on correct contracts —
# and a structural blocker is unappealable. Exact normalized equality only (`_norm`), never fuzzy
# matching, the same discipline `scene_packet/validation.py:23-25` holds to.
_HIDDEN_EXPOSURE_MATRIX: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("must_remain_hidden.reader", _READER_VISIBLE_PATHS + _ON_PAGE_PATHS),
    ("must_remain_hidden.pov", _POV_VISIBLE_PATHS + _ON_PAGE_PATHS),
    ("must_remain_hidden.all_surface_prose", _READER_VISIBLE_PATHS + _POV_VISIBLE_PATHS + _ON_PAGE_PATHS),
)


def _at(body: dict[str, Any], dotted: str) -> list[str]:
    """Every string reachable at a dotted body path, whatever its shape (`exit_state` is a bare str,
    the rest are lists) — empty when the path is absent or the body is not a dict."""
    if not isinstance(body, dict):
        return []
    return [s for s in collect_strings(get_dotted(body, dotted)) if s.strip()]


def canon_contract_leak_blockers(
    *,
    packets: list[tuple[int, dict[str, Any]]],
    chapter_forbidden: list[str],
) -> list[StructuralBlockerOut]:
    """`canon_contract_leak`: a scene contract exposes something the chapter packet forbids, or that the
    same contract declares must stay hidden — the drafter would obey the leak, and QA missed exactly this
    in Ch1 (§4). `packets` = (scene_no, scene packet body).

    Two deterministic arms, both over the full exposure matrix above:

    * **chapter-forbidden exposure** — a `forbidden_reveals` / `forbidden_knowledge` entry appearing in a
      reader-visible or on-page field. POV-visible fields are excluded: the chapter's forbidden list is
      about what the READER may learn, and a POV who knows a secret the reader must not is ordinary
      craft, not a leak.
    * **self-contradiction** — the contract declares a fact hidden from an audience and then hands it to
      that same audience. This is the arm that replaces the retired raw-verdict gate (#278): it is the
      one draft-unsafe class the LLM verdict uniquely covered, now decided from the contract itself.

    Recall is narrower than a model's (verbatim restatements only, no paraphrase), and that is the
    deliberate trade: the model's paraphrase-catching survives as `repair` issues that gate final export
    and as the automatic canon-conflict `ApprovalBlocker` (`scene_packet/blockers.py:52`), neither of
    which a prompt sentence can silently flip to permissive.
    """
    out: list[StructuralBlockerOut] = []
    forbidden = {_norm(x) for x in chapter_forbidden}
    for scene_no, body in packets:
        seen: set[tuple[str, str]] = set()  # (exposure path, normalized text) — report each pair once
        for path in _READER_VISIBLE_PATHS + _ON_PAGE_PATHS:
            for text in _at(body, path):
                key = _norm(text)
                if key in forbidden and (path, key) not in seen:
                    seen.add((path, key))
                    out.append(
                        StructuralBlockerOut(
                            kind="canon_contract_leak",
                            message=(
                                f"Scene {scene_no}'s contract lets the reader learn '{text}', which the chapter "
                                "packet forbids — fix the scene contract or the chapter packet."
                            ),
                        )
                    )
        for hidden_path, exposed_paths in _HIDDEN_EXPOSURE_MATRIX:
            hidden = {_norm(x) for x in _at(body, hidden_path)}
            if not hidden:
                continue
            for path in exposed_paths:
                for text in _at(body, path):
                    key = _norm(text)
                    if key in hidden and (path, key) not in seen:
                        seen.add((path, key))
                        out.append(
                            StructuralBlockerOut(
                                kind="canon_contract_leak",
                                message=(
                                    f"Scene {scene_no}'s contract both reveals and hides '{text}' — "
                                    f"{path} exposes what {hidden_path} protects; resolve the "
                                    "contradiction before drafting."
                                ),
                            )
                        )
    return out


@dataclass(frozen=True)
class DraftGateInputs:
    """Everything the authoritative gate needs, as plain counts — cheap to assemble, pure to test.

    **EVERY field here is deterministic, and that is an invariant, not an accident (#278).** No field
    may be derived from LLM output. `scene_packet_qa_blocking` used to be: it counted rows whose raw
    `ScenePacket.qa_verdict` equalled `BLOCK_DRAFTING`, so a drafting gate was decided by a model that
    the author/QA prompts had already told what to think (`scene_packet/author.py:format_chapter_rulings`
    injects a human's ruling with "do NOT re-litigate", `scene_packet/qa.py:_SYSTEM` adds "do NOT flag
    it as an unresolved open question"). One sentence of prose was the only enforcement, and the failure
    direction was permissive. ADR-0031 R3 Fork 2 ruled (d): a model may nominate, never mint — so the
    field is gone and the draft-unsafe class it covered is decided by `canon_contract_leak_blockers`
    instead. The verdict still rides out on `DraftReadinessOut.scene_packet_qa_blocking` as an ADVISORY
    count for the Desk's checklist; it is not an input to this struct. Pinned by
    `tests/test_issue278_prompt_gate_authority.py::test_draft_gate_inputs_carry_no_model_derived_field`.
    """

    chapter_packet_approved: bool = False
    structural_blockers: tuple[StructuralBlockerOut, ...] = ()
    scene_packets_derived: int = 0
    scene_packets_approved: int = 0
    missing_scene_packets: tuple[int, ...] = ()
    scene_packets_stale: int = 0
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
    in pipeline order: packet → sequence/budget/structural → scene packets (coverage/stale) → beats →
    jobs → prose coverage → provider rate limit.

    Deterministic end to end (#278): every branch below reads a count derived from persisted contract
    state, never from a model's judgement."""
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
    # (A QA-verdict gate stood here. It was decided by raw model output that the prompts had already
    # coached — #278. Its one unique class of coverage, a self-contradictory contract, is now a
    # deterministic `canon_contract_leak` structural blocker resolved at gate 2 above.)
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


@dataclass(frozen=True)
class ReadinessRows:
    """Everything `derive_draft_readiness` reads, fetched up front. Splitting fetch from derive lets
    the per-chapter endpoint keep its shape while the book-scoped overview batches ONE set of flat
    queries for every chapter instead of ~8 sequential awaits × N chapters."""

    chapter_id: uuid.UUID
    cp: ChapterPacket | None  # the APPROVED chapter packet (readiness never reads other statuses)
    sp_rows: list[ScenePacket]
    beats: list[Beat]
    # Latest Scene row per scene_no → does it carry non-empty prose? (latest-row-wins by created_at;
    # a key existing at all == "drafted", True == prose coverage — same two facts as before.)
    latest_has_prose: dict[int, bool]
    active_jobs: int
    malformed_jobs: int
    sp_required_failed: int
    budget_sequence: ChapterSequence | None  # latest by updated_at, any status (lane-3 budget check)
    cp_sequence: ChapterSequence | None  # latest non-stale sequence scoped to cp (structural gates)


def derive_draft_readiness(rows: ReadinessRows) -> DraftReadinessOut:
    """Pure derivation — no ORM, no DB. Verbatim move of the readiness logic; the parity test in
    tests/test_chapters_overview.py pins its output against the pre-split behavior."""
    chapter_id = rows.chapter_id
    cp = rows.cp
    chapter_packet_approved = cp is not None

    sp_rows = rows.sp_rows
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

    approved_beats = [b for b in rows.beats if b.status == BeatStatus.APPROVED]
    linked = [b for b in approved_beats if b.scene_packet_id is not None]
    unlinked = [b.id for b in approved_beats if b.scene_packet_id is None]

    # Mirror schedule_undrafted_beats(skip_drafted=True): a beat whose scene already has prose is not
    # queueable — redraft is the path for those. Excluding already-drafted scenes here keeps `draftable`
    # honest, so a fully-drafted chapter reports draftable=False instead of enabling a "Draft chapter"
    # click that skips every beat and 409s. "Drafted" matches the scheduler exactly: any Scene row for
    # that scene_no (draft_queue.py:244-247). Prose coverage is stricter — the latest row per scene_no
    # must carry non-empty prose (matching production's `(scene.prose or "").strip()` test) — and gates
    # chapter ASSEMBLY, which concatenates prose and hard-fails `missing_scene` on every gap.
    drafted_scene_nos = set(rows.latest_has_prose)
    prose_scene_nos = sorted(n for n, has in rows.latest_has_prose.items() if has)
    expected_scenes = seed_count or len({p.scene_no for p in sp_rows})
    missing_prose = sorted(set(range(1, expected_scenes + 1)) - set(prose_scene_nos)) if expected_scenes else []

    active_jobs = rows.active_jobs
    malformed = rows.malformed_jobs
    sp_required_failed = rows.sp_required_failed

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
    sequence = rows.budget_sequence
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
    sequence = rows.cp_sequence if cp is not None else None
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
        sequence_id=sequence.id if sequence else None,
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

    # ADVISORY ONLY (#278): how many contracts the LLM QA agent NOMINATED as unsafe. It is reported so
    # the Desk can show the nomination, and it is deliberately NOT passed to `resolve_draft_gate` — see
    # `DraftGateInputs`. The draft-safety facts are decided by `structural` above.
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

    sp_rows = list(
        (await session.execute(select(ScenePacket).where(ScenePacket.chapter_id == chapter_id))).scalars().all()
    )

    beats = list(
        (await session.execute(select(Beat).where(Beat.chapter_id == chapter_id).order_by(Beat.scene_no)))
        .scalars()
        .all()
    )

    scene_rows = (
        await session.execute(
            select(Scene.scene_no, Scene.prose)
            .where(Scene.chapter_id == chapter_id)
            .order_by(Scene.scene_no, Scene.created_at)
        )
    ).all()
    latest_has_prose: dict[int, bool] = {}
    for scene_no, prose in scene_rows:  # ordered by created_at, so the latest row per scene_no wins
        latest_has_prose[scene_no] = bool((prose or "").strip())

    active_jobs = (
        await session.execute(
            select(func.count())
            .select_from(Job)
            .where(
                Job.chapter_id == chapter_id,
                Job.kind == JobKind.DRAFT,
                job_policy.in_flight_clause(),
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

    budget_sequence = (
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

    cp_sequence = None
    if cp is not None:
        cp_sequence = (
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

    return derive_draft_readiness(
        ReadinessRows(
            chapter_id=chapter_id,
            cp=cp,
            sp_rows=sp_rows,
            beats=beats,
            latest_has_prose=latest_has_prose,
            active_jobs=int(active_jobs),
            malformed_jobs=int(malformed),
            sp_required_failed=int(sp_required_failed),
            budget_sequence=budget_sequence,
            cp_sequence=cp_sequence,
        )
    )


async def fetch_book_readiness_rows(session: AsyncSession, book_id: uuid.UUID) -> dict[uuid.UUID, ReadinessRows]:
    """Book-scoped fetch for the Chapters overview: the same facts `compute_draft_readiness` gathers,
    in a handful of flat `chapter_id IN (…)` queries instead of ~8 sequential awaits per chapter.
    Scenes are projected to a prose BOOLEAN so the whole book's prose never crosses the wire."""
    chapter_ids = list((await session.execute(select(Chapter.id).where(Chapter.book_id == book_id))).scalars().all())
    if not chapter_ids:
        return {}

    cp_by_chapter: dict[uuid.UUID, ChapterPacket] = {}
    cp_rows = (
        (
            await session.execute(
                select(ChapterPacket)
                .where(
                    ChapterPacket.chapter_id.in_(chapter_ids),
                    ChapterPacket.status == PacketStatus.APPROVED,
                )
                .order_by(ChapterPacket.chapter_id, ChapterPacket.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    for row in cp_rows:  # first row per chapter is the newest approved packet
        cp_by_chapter.setdefault(row.chapter_id, row)

    sp_by_chapter: dict[uuid.UUID, list[ScenePacket]] = {cid: [] for cid in chapter_ids}
    for row in (
        (await session.execute(select(ScenePacket).where(ScenePacket.chapter_id.in_(chapter_ids)))).scalars().all()
    ):
        sp_by_chapter[row.chapter_id].append(row)

    beats_by_chapter: dict[uuid.UUID, list[Beat]] = {cid: [] for cid in chapter_ids}
    for row in (
        (
            await session.execute(
                select(Beat).where(Beat.chapter_id.in_(chapter_ids)).order_by(Beat.chapter_id, Beat.scene_no)
            )
        )
        .scalars()
        .all()
    ):
        beats_by_chapter[row.chapter_id].append(row)

    # Boolean prose projection; trim covers the same whitespace Python's .strip() removes.
    prose_by_chapter: dict[uuid.UUID, dict[int, bool]] = {cid: {} for cid in chapter_ids}
    scene_rows = (
        await session.execute(
            select(
                Scene.chapter_id,
                Scene.scene_no,
                func.btrim(func.coalesce(Scene.prose, ""), " \t\r\n") != "",
            )
            .where(Scene.chapter_id.in_(chapter_ids))
            .order_by(Scene.chapter_id, Scene.scene_no, Scene.created_at)
        )
    ).all()
    for cid, scene_no, has_prose in scene_rows:  # latest row per (chapter, scene_no) wins
        prose_by_chapter[cid][scene_no] = bool(has_prose)

    active: dict[uuid.UUID, int] = {cid: 0 for cid in chapter_ids}
    malformed: dict[uuid.UUID, int] = {cid: 0 for cid in chapter_ids}
    sp_required_failed: dict[uuid.UUID, int] = {cid: 0 for cid in chapter_ids}
    job_rows = (
        await session.execute(
            select(Job.chapter_id, Job.status, Job.scene_packet_id, Job.last_error, Job.claimed_at).where(
                Job.chapter_id.in_(chapter_ids),
                Job.kind == JobKind.DRAFT,
                Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.FAILED]),
            )
        )
    ).all()
    for cid, status, scene_packet_id, last_error, claimed_at in job_rows:
        if status == JobStatus.QUEUED or job_policy.is_live_running_status(status, claimed_at):
            active[cid] += 1
        if scene_packet_id is None:
            malformed[cid] += 1
        if status == JobStatus.FAILED and "scenepacket" in (last_error or "").lower():
            sp_required_failed[cid] += 1

    seq_by_chapter: dict[uuid.UUID, list[ChapterSequence]] = {cid: [] for cid in chapter_ids}
    for row in (
        (await session.execute(select(ChapterSequence).where(ChapterSequence.chapter_id.in_(chapter_ids))))
        .scalars()
        .all()
    ):
        seq_by_chapter[row.chapter_id].append(row)

    out: dict[uuid.UUID, ReadinessRows] = {}
    for cid in chapter_ids:
        cp = cp_by_chapter.get(cid)
        sequences = seq_by_chapter[cid]
        budget_sequence = max(sequences, key=lambda s: s.updated_at, default=None)
        cp_sequence = None
        if cp is not None:
            scoped = [s for s in sequences if s.chapter_packet_id == cp.id and s.status != ChapterSequenceStatus.STALE]
            cp_sequence = max(scoped, key=lambda s: s.created_at, default=None)
        out[cid] = ReadinessRows(
            chapter_id=cid,
            cp=cp,
            sp_rows=sp_by_chapter[cid],
            beats=beats_by_chapter[cid],
            latest_has_prose=prose_by_chapter[cid],
            active_jobs=active[cid],
            malformed_jobs=malformed[cid],
            sp_required_failed=sp_required_failed[cid],
            budget_sequence=budget_sequence,
            cp_sequence=cp_sequence,
        )
    return out


async def compute_book_readiness(session: AsyncSession, book_id: uuid.UUID) -> dict[uuid.UUID, DraftReadinessOut]:
    rows = await fetch_book_readiness_rows(session, book_id)
    return {cid: derive_draft_readiness(r) for cid, r in rows.items()}
