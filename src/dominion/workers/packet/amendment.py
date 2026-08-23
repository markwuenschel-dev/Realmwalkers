"""ChapterPacket amendment mode — eligibility, and the ONE locked approve+supersede transition (#261).

Amendment mode exists for exactly one state that normal re-derivation cannot repair: imported prose
exists, the chapter has an APPROVED ChapterPacket, and an affected scene has NO seed in that packet's
body. A merely-stale seed is a normal re-derive (the seed is still there, so `derive.py`'s per-seed loop
still visits it); a valid seed needs nothing at all. Only the genuine no-seed case may enter here.

WHY ELIGIBILITY IS STRUCTURAL, NEVER `ScenePacket.status == STALE`. `source_hash` is computed from
DIFFERENT payloads at derive vs recompute — `scene_packet/derive.py:576-584` passes `canon_chunk_hashes`
and `scene_pov`, `scene_packet/staleness.py:111-117` passes neither — so any packet derived against
populated canon is marked STALE on the next recompute whether or not anything actually drifted. STALE
therefore cannot distinguish "stale seed" from "no seed". Seed PRESENCE in
`ChapterPacket.body["scene_seeds"]` can, and is immune to that defect. (That hash asymmetry is a real,
separate defect; amendment mode is built so as not to depend on it either way.)

THE TRANSITION. `apply_authority_locked` is the single domain operation that moves a ChapterPacket into
`approved`. Both callers funnel into it — `approve_amendment` (which takes the lock and owns the commit)
and the ordinary `POST /chapters/{id}/packet/approve` route from inside its own locked body. An ordinary
approve is the degenerate case: no predecessor to supersede, no children to stale. Keeping one writer is
the point; two is how "two approved packets per chapter" becomes reachable again, and the AST writer
guard (`tests/test_issue259_chapter_packet_writer_guard.py`) exists to make a second one visible.

Model work happens strictly OUTSIDE the lock (the adoption worker authors the amendment packet in
`amendment_author.py`); this op reacquires the chapter workflow lock, RELOADS the authoritative rows with
`populate_existing=True`, revalidates eligibility, re-checks the prose fingerprint, and FAILS CLOSED on
any drift. Nothing here calls a model.

On invariant 8, stated precisely rather than optimistically: `ChapterPacketApprovalSource` has no
autonomous member, and `ck_chapter_packets_approval_source` rejects any value outside the two permitted
ones — so an automated approver cannot NAME itself without a migration. That CHECK does NOT prevent a
writer from leaving `approval_source` NULL (an adversarial review found exactly that hole in an earlier,
stronger-sounding version of this claim). The residual guarantee is enforced one level up: every
production path into `approved` goes through this function, which always stamps provenance, and the AST
guard fails any new path that stores `status` without it. See migrations.py's note on the CHECK.

Why `populate_existing=True` is load-bearing and not defensive: `session.get` returns the
identity-mapped instance without emitting SQL, so a caller that already touched the row would have its
PRE-LOCK copy silently returned and "reload under the lock" would do nothing at all. The same discipline
is spelled out at `api/routers/reviews.py:295-300` and `workers/production_repair.py:638-643`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import select

from dominion.shared.chapter_lock import run_under_chapter_workflow
from dominion.shared.enums import (
    ChapterPacketApprovalSource,
    ImportAdoptionMode,
    PacketStatus,
    ScenePacketStatus,
)
from dominion.shared.models import Chapter, ChapterPacket, ImportAdoption, ScenePacket
from dominion.shared.prose_fingerprint import chapter_scene_rows, chapter_source_fingerprint
from dominion.workers.packet import approval_policy as packet_approval

log = structlog.get_logger()

#: `stale_reason` written onto a ScenePacket whose contract an approved amendment invalidated. Distinct
#: from `staleness.py`'s generic "upstream inputs changed since derivation" ON PURPOSE: that string
#: cannot tell an author whether a canon edit, a word-budget change, or a superseded chapter contract
#: caused it, and the recovery differs. This one names the cause and is queryable.
AMENDMENT_STALE_REASON = "superseded by an approved chapter-packet amendment — re-derive this scene"


# --------------------------------------- typed failures ------------------------------------------- #


class AmendmentError(Exception):
    """Base for every amendment-mode domain failure. Transport-agnostic on purpose — the route maps these
    to HTTP, and a worker or boot reconciliation can raise/catch the same types (the discipline
    `shared/adoption_entry.py`'s AdoptionEntryError family established)."""


class AmendmentChapterNotFound(AmendmentError):
    """The chapter vanished between the caller's read and the locked reload."""


class AmendmentNotEligible(AmendmentError):
    """The chapter is not in the genuine no-seed state. Carries the machine-readable `reason` from the
    eligibility verdict so the route can surface WHICH boundary condition refused it — "every scene is
    already seeded" and "there is no approved packet to amend" need different operator action."""

    def __init__(self, reason: str, message: str) -> None:
        self.reason = reason
        super().__init__(message)


class AmendmentPacketNotFound(AmendmentError):
    """The amendment packet named by the caller does not exist, or does not belong to this chapter."""


class AmendmentSourceDrifted(AmendmentError):
    """Invariant 4, fail-closed: the chapter's prose fingerprint changed between authoring the amendment
    and this locked commit, so the amendment was written against prose that no longer exists. NOTHING is
    written. The amendment stays `proposed` and reviewable; the operator re-runs it against current prose."""

    def __init__(self, expected: str | None, actual: str) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__(
            "the chapter's source prose changed after this amendment was authored "
            f"(authored against {expected or 'an uncaptured fingerprint'}, now {actual}); "
            "nothing was changed — re-run the amendment against the current prose"
        )


class AmendmentPredecessorMissing(AmendmentError):
    """Invariant 3: the amendment names no predecessor, or its predecessor is no longer the chapter's
    approved authority. Approving it would create an amendment with no superseded predecessor — the exact
    state `ck_chapter_packets_amendment_names_predecessor` forbids — so it fails closed here with a
    diagnosis rather than surfacing as an opaque IntegrityError."""


# ------------------------------------- eligibility verdict ---------------------------------------- #


@dataclass(frozen=True)
class AmendmentVerdict:
    """The eligibility answer, with the evidence that produced it.

    `reason` is a stable machine-readable token (it crosses the API boundary and the Desk switches on
    it), so it is deliberately not a prose message. `eligible` is True for exactly one reason token,
    `unseeded_scenes_present`; every other token is a refusal, and `ALREADY_OPEN` is the idempotent one —
    it carries `open_amendment_packet_id` so a duplicate request returns the existing branch instead of
    forking a second lineage.
    """

    chapter_id: uuid.UUID
    eligible: bool
    reason: str
    approved_packet_id: uuid.UUID | None = None
    open_amendment_packet_id: uuid.UUID | None = None
    unseeded_scene_ids: tuple[uuid.UUID, ...] = ()
    seeded_scene_ids: tuple[uuid.UUID, ...] = ()
    source_fingerprint: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)


#: Every `AmendmentVerdict.reason` token. Enumerated so the API schema, the Desk, and the tests share one
#: vocabulary and a typo cannot invent a silent new outcome.
REASON_NO_APPROVED_PACKET = "no_approved_packet"
REASON_NO_IMPORTED_SCENES = "no_imported_scenes"
REASON_ALL_SCENES_SEEDED = "all_scenes_seeded"
REASON_UNSEEDED_SCENES_PRESENT = "unseeded_scenes_present"
REASON_AMENDMENT_ALREADY_OPEN = "amendment_already_open"
#: Not an eligibility verdict token — a transition refusal. Raised when the approve target is not
#: `proposed` (a BLOCKED diagnostic row or a SUPERSEDED historical one).
REASON_NOT_PROPOSED = "packet_not_proposed"
#: Also a transition refusal, not an eligibility verdict (#277 clause A). Raised when the packet taking
#: authority still carries unresolved open questions. It lives HERE, at the shared seam, rather than at a
#: route: chapter authority can be granted through two routes and only `POST .../packet/approve` consulted
#: the gate, so an amendment — the very path that GENERATES open questions, at
#: `amendment_author.py:471-479` — could take authority with every question unresolved.
REASON_OPEN_QUESTIONS_UNRESOLVED = "open_questions_unresolved"

REFUSAL_MESSAGES: dict[str, str] = {
    REASON_OPEN_QUESTIONS_UNRESOLVED: (
        "This chapter contract still has unresolved open questions, so it cannot take authority. Rule "
        "every question — each ruling needs a non-empty resolution and source — then approve again. "
        "Nothing was changed."
    ),
    REASON_NO_APPROVED_PACKET: (
        "This chapter has no approved contract, so there is nothing to amend. Adopt an initial contract "
        "first (Start contract adoption) — amendment is copy-on-write FROM an approved packet."
    ),
    REASON_NO_IMPORTED_SCENES: (
        "This chapter has no imported prose, so no scene can be missing a seed. Amendment repairs "
        "imported prose that the approved contract does not cover."
    ),
    REASON_ALL_SCENES_SEEDED: (
        "Every scene in this chapter already resolves to a seed in the approved contract, so amendment "
        "is not the right repair. If a scene's contract is out of date, re-derive its scene packet."
    ),
    REASON_AMENDMENT_ALREADY_OPEN: (
        "An amendment for this chapter is already open and awaiting review. Review or discard that one "
        "rather than opening a second — a chapter may only have one amendment in flight."
    ),
}


# ------------------------------------------ eligibility ------------------------------------------- #


def _seed_index(body: dict[str, Any] | None) -> tuple[dict[str, dict[str, Any]], set[int]]:
    """`(seeds by seed_id, the scene_no values they claim)` from a ChapterPacket body.

    Mirrors `scene_packet/derive.py:456-457`'s own filter exactly — a seed counts only when it is a dict
    carrying a truthy `seed_id` — so eligibility and derivation agree on what a seed IS. If they
    disagreed, amendment could be refused for a chapter whose derive loop still skips the scene.
    """
    by_id: dict[str, dict[str, Any]] = {}
    scene_nos: set[int] = set()
    for seed in (body or {}).get("scene_seeds") or []:
        if not isinstance(seed, dict) or not seed.get("seed_id"):
            continue
        by_id[str(seed["seed_id"])] = seed
        raw = seed.get("scene_no")
        if isinstance(raw, int):
            scene_nos.add(int(raw))
    return by_id, scene_nos


async def assess_chapter(session, *, chapter_id: uuid.UUID) -> AmendmentVerdict:
    """Decide whether `chapter_id` is in the genuine no-seed state that amendment mode exists for.

    Pure assessment: it reads, it writes nothing, and it never calls a model. Safe to call outside the
    lock for display; the transition calls it AGAIN under the lock, because a verdict computed outside is
    advisory only (invariant 4).

    A scene counts as SEEDED when either the producing adoption's `seed_bindings` binds a seed to that
    exact scene id, OR some seed claims that scene's `scene_no`. Both linkages are honoured because both
    exist in the wild: `seed_bindings` is written only for adoption-derived packets
    (`import_adoption._seed_bindings`), while planning-path packets link by `scene_no` alone. The OR is
    the FAIL-CLOSED direction for invariant 1 — when either linkage can account for a scene we treat it
    as covered and refuse amendment, so an ambiguous chapter is never dragged into a supersession it did
    not need. Deliberately the opposite bias to `derive.py:670-684`, which fails a SCENE closed on a
    missing binding; there the risk is drafting against a contract that does not cover the prose, here
    the risk is superseding an approved contract that was fine.
    """
    chapter = await session.get(Chapter, chapter_id)
    if chapter is None:
        raise AmendmentChapterNotFound(f"chapter {chapter_id} not found")

    approved = (
        await session.execute(
            select(ChapterPacket)
            .where(ChapterPacket.chapter_id == chapter_id, ChapterPacket.status == PacketStatus.APPROVED)
            .limit(1)
        )
    ).scalar_one_or_none()

    rows = await chapter_scene_rows(session, chapter_id)
    fingerprint = chapter_source_fingerprint(rows)

    open_amendment = (
        await session.execute(
            select(ChapterPacket)
            .where(
                ChapterPacket.chapter_id == chapter_id,
                ChapterPacket.origin_mode == ImportAdoptionMode.AMENDMENT,
                ChapterPacket.status == PacketStatus.PROPOSED,
            )
            .limit(1)
        )
    ).scalar_one_or_none()

    if approved is None:
        return AmendmentVerdict(
            chapter_id=chapter_id,
            eligible=False,
            reason=REASON_NO_APPROVED_PACKET,
            open_amendment_packet_id=open_amendment.id if open_amendment else None,
            source_fingerprint=fingerprint,
        )

    # Only scenes that actually carry prose are "imported prose" for this purpose; an empty planned slot
    # has nothing for an amendment to reconstruct a contract from.
    imported = [(scene_no, scene_id) for scene_no, scene_id, _v, prose in rows if (prose or "").strip()]
    if not imported:
        return AmendmentVerdict(
            chapter_id=chapter_id,
            eligible=False,
            reason=REASON_NO_IMPORTED_SCENES,
            approved_packet_id=approved.id,
            open_amendment_packet_id=open_amendment.id if open_amendment else None,
            source_fingerprint=fingerprint,
        )

    seeds_by_id, claimed_scene_nos = _seed_index(approved.body)
    bound_scene_ids = await _bound_scene_ids(session, chapter_id=chapter_id, packet_id=approved.id, seeds=seeds_by_id)

    unseeded: list[uuid.UUID] = []
    seeded: list[uuid.UUID] = []
    for scene_no, scene_id in sorted(imported):
        if scene_id in bound_scene_ids or scene_no in claimed_scene_nos:
            seeded.append(scene_id)
        else:
            unseeded.append(scene_id)

    if not unseeded:
        return AmendmentVerdict(
            chapter_id=chapter_id,
            eligible=False,
            reason=REASON_ALL_SCENES_SEEDED,
            approved_packet_id=approved.id,
            open_amendment_packet_id=open_amendment.id if open_amendment else None,
            seeded_scene_ids=tuple(seeded),
            source_fingerprint=fingerprint,
        )

    if open_amendment is not None:
        # Idempotency (invariant 5): the chapter IS eligible on the merits, but a branch already exists.
        # Return it rather than forking a second lineage; `uq_chapter_packets_open_amendment` would reject
        # the fork anyway, and a typed answer beats an IntegrityError.
        return AmendmentVerdict(
            chapter_id=chapter_id,
            eligible=False,
            reason=REASON_AMENDMENT_ALREADY_OPEN,
            approved_packet_id=approved.id,
            open_amendment_packet_id=open_amendment.id,
            unseeded_scene_ids=tuple(unseeded),
            seeded_scene_ids=tuple(seeded),
            source_fingerprint=fingerprint,
        )

    return AmendmentVerdict(
        chapter_id=chapter_id,
        eligible=True,
        reason=REASON_UNSEEDED_SCENES_PRESENT,
        approved_packet_id=approved.id,
        unseeded_scene_ids=tuple(unseeded),
        seeded_scene_ids=tuple(seeded),
        source_fingerprint=fingerprint,
    )


async def _bound_scene_ids(
    session, *, chapter_id: uuid.UUID, packet_id: uuid.UUID, seeds: dict[str, dict[str, Any]]
) -> set[uuid.UUID]:
    """Scene ids that the producing adoption's `seed_bindings` binds to a seed STILL PRESENT in `seeds`.

    The "still present" filter is the whole point: `seed_bindings` is a historical record of what the
    packet looked like when it was published, so a binding whose seed has since been edited out of the
    body must NOT count as coverage — that scene is precisely the no-seed case. Reads the adoption by its
    forward link (`chapter_packet_id`); a NULL there is expected and simply yields no bindings (the FK is
    ON DELETE SET NULL, so it is not a durable origin record — which is why ChapterPacket carries its own
    `origin_adoption_id`).
    """
    adoption = (
        await session.execute(select(ImportAdoption).where(ImportAdoption.chapter_packet_id == packet_id).limit(1))
    ).scalar_one_or_none()
    if adoption is None or not isinstance(adoption.seed_bindings, dict):
        return set()
    out: set[uuid.UUID] = set()
    for seed_id, binding in adoption.seed_bindings.items():
        if str(seed_id) not in seeds or not isinstance(binding, dict):
            continue
        raw = binding.get("scene_id")
        if not raw:
            continue
        try:
            out.add(uuid.UUID(str(raw)))
        except (ValueError, TypeError):
            continue
    return out


# ----------------------------- the one locked authority transition -------------------------------- #


@dataclass(frozen=True)
class AuthorityOutcome:
    """What the locked transition actually did to persisted state.

    `superseded_packet_id` is None for an ordinary (non-amendment) approve — the degenerate case with no
    predecessor — and set for an amendment. `staled_scene_packet_ids` is the consequence set, recorded
    here AND in the approved packet's `amendment_scope` so it stays queryable after the fact.
    """

    packet_id: uuid.UUID
    chapter_id: uuid.UUID
    superseded_packet_id: uuid.UUID | None
    staled_scene_packet_ids: tuple[uuid.UUID, ...]
    approval_source: str
    was_already_approved: bool = False


async def _reload_packet_locked(session, packet_id: uuid.UUID) -> ChapterPacket | None:
    """Reload one ChapterPacket under the held chapter lock.

    `populate_existing=True` is LOAD-BEARING, not defensive: `session.get` alone returns an
    identity-mapped instance without emitting SQL, so if this session already read the row (the route
    does, to build its pre-flight response) the "reload" would hand back the stale pre-lock copy and
    every guard below would evaluate against it. See `api/routers/reviews.py:295-300`.
    """
    return await session.get(ChapterPacket, packet_id, populate_existing=True, with_for_update=True)


async def apply_authority_locked(
    session,
    *,
    chapter_id: uuid.UUID,
    packet_id: uuid.UUID,
    approval_source: ChapterPacketApprovalSource,
    expect_amendment: bool,
    unseeded_scene_ids: tuple[uuid.UUID, ...] = (),
) -> AuthorityOutcome:
    """THE authority transition. Assumes the chapter workflow lock is already held; performs no commit.

    Ordinary approve and amendment approve are the SAME operation — an ordinary approve is simply the case
    with no predecessor to supersede. Keeping one body is the point: a second seam is how "two approved
    packets" becomes reachable again, and the AST writer guard
    (`tests/test_issue259_chapter_packet_writer_guard.py`) exists to make that regression visible.

    Order inside the transaction matters and is deliberate:
      1. reload the target under the lock (`populate_existing`), so every guard reads authoritative state;
      2. re-check the prose fingerprint -> fail closed on drift (invariant 4);
      3. re-check the predecessor is STILL the chapter's approved authority (invariant 3);
      4. predecessor leaves `approved` naming its successor, THEN the successor takes the freed slot —
         `uq_chapter_packets_active_chapter` makes any other order fail, which is the guarantee that two
         approved packets are unreachable rather than merely unlikely;
      5. stale the ScenePackets the supersession invalidated, and record that consequence set.
    """
    packet = await _reload_packet_locked(session, packet_id)
    if packet is None or packet.chapter_id != chapter_id:
        raise AmendmentPacketNotFound(f"chapter packet {packet_id} not found for chapter {chapter_id}")

    is_amendment = str(packet.origin_mode) == ImportAdoptionMode.AMENDMENT.value
    if expect_amendment and not is_amendment:
        raise AmendmentNotEligible(
            REASON_NO_APPROVED_PACKET,
            f"chapter packet {packet_id} is not an amendment (origin_mode={packet.origin_mode!r})",
        )

    # Idempotent replay (invariant 5): an already-approved target is a terminal success, not an error and
    # not a second supersession. Returning the existing state is what makes a retried request safe.
    if str(packet.status) == PacketStatus.APPROVED.value:
        return AuthorityOutcome(
            packet_id=packet.id,
            chapter_id=chapter_id,
            superseded_packet_id=packet.supersedes_packet_id,
            staled_scene_packet_ids=(),
            approval_source=str(packet.approval_source or approval_source.value),
            was_already_approved=True,
        )

    # (1b) ONLY a `proposed` packet may take authority. Without this a `BLOCKED` packet is promotable —
    # confirmed by execution, not inference: a blocked amendment went `blocked -> approved` with its
    # predecessor `-> superseded`. `enums.PacketStatus` says BLOCKED is "authored fail-closed; retained as
    # diagnostic evidence, NEVER authoritative", and a fail-closed authoring result becoming the contract
    # every drafting agent obeys is the exact inversion that guarantee exists to prevent. SUPERSEDED is
    # rejected here too: it is terminal history and must never be re-approved.
    if str(packet.status) != PacketStatus.PROPOSED.value:
        raise AmendmentNotEligible(
            REASON_NOT_PROPOSED,
            f"chapter packet {packet_id} is {packet.status!r}, not proposed — only a proposed packet may "
            "become the chapter's authority. A blocked packet is retained as diagnostic evidence and a "
            "superseded one is history; neither may be approved.",
        )

    # (1c) THE open-questions gate (#277 clause A). Enforced HERE, at the shared authority seam, so both
    # approval routes are covered by construction. Ordinary approve pre-checks the same predicate at
    # `packets.py:323`; the amendment route had NO gated path at all — `packets.py:305-321` deliberately
    # refuses an amendment on the ordinary route and directs the caller to the ungated one. So the comment
    # at `amendment_author.py:471-479` claiming "any open-question item blocks APPROVAL" was false on the
    # exact route that mints those items.
    #
    # Placed BEFORE every mutation and before the predecessor is even loaded: a refusal here must leave the
    # amendment `proposed` and the predecessor `approved`, not merely return a 409 after a flush.
    #
    # Reached ONLY through the canonical predicate. This function must never inspect `open_questions`
    # itself — that is both the fork-3b seam guard's rule and the reason a second reader is how the two
    # routes drifted apart in the first place.
    if refusal := packet_approval.can_approve(packet):
        raise AmendmentNotEligible(
            REASON_OPEN_QUESTIONS_UNRESOLVED,
            f"chapter packet {packet_id} cannot take authority: {refusal.detail}",
        )

    # (2) The drift gate. Recomputed HERE, under the lock, from the same membership query the amendment
    # was fingerprinted with. A pre-lock check would be worthless: the whole point is that prose can move
    # while a model call is in flight.
    if is_amendment:
        actual = chapter_source_fingerprint(await chapter_scene_rows(session, chapter_id))
        if packet.source_fingerprint is None or packet.source_fingerprint != actual:
            raise AmendmentSourceDrifted(packet.source_fingerprint, actual)

    predecessor: ChapterPacket | None = None
    if is_amendment:
        # (3) The predecessor must still BE the authority. If a concurrent operation already superseded or
        # deleted it, this amendment was authored against a contract that no longer governs.
        if packet.supersedes_packet_id is None:
            raise AmendmentPredecessorMissing(
                f"amendment {packet_id} names no predecessor; it cannot be approved without one"
            )
        predecessor = await _reload_packet_locked(session, packet.supersedes_packet_id)
        if predecessor is None:
            raise AmendmentPredecessorMissing(
                f"amendment {packet_id}'s predecessor {packet.supersedes_packet_id} no longer exists"
            )
        # The predecessor MUST belong to this chapter. Nothing in the schema enforces it — a CHECK cannot
        # reference another row, and the lineage columns deliberately carry no FK — so without this check a
        # cross-chapter `supersedes_packet_id` is legal (an adversarial DB probe inserted one successfully)
        # and approving it would demote a DIFFERENT chapter's authority: the supersede below writes the
        # foreign row, while `_stale_children_of` filters on THIS chapter_id, so the foreign chapter's
        # approved ScenePackets keep pointing at a contract that has just been retired and that chapter is
        # left with no authority at all. The chapter workflow lock cannot help either — it is per-chapter, so
        # a foreign row is not even covered by the lock we hold.
        if predecessor.chapter_id != chapter_id:
            raise AmendmentPredecessorMissing(
                f"amendment {packet_id} names predecessor {predecessor.id}, which belongs to chapter "
                f"{predecessor.chapter_id}, not {chapter_id}. Superseding it would retire another chapter's "
                "contract and leave that chapter with no authority; nothing was changed."
            )
        if str(predecessor.status) != PacketStatus.APPROVED.value:
            raise AmendmentPredecessorMissing(
                f"amendment {packet_id}'s predecessor {predecessor.id} is no longer the chapter's approved "
                f"authority (status={predecessor.status!r}) — another operation changed it first"
            )

    now = datetime.now(UTC)

    # (4) Hand the slot over. Predecessor first: the partial unique index covers `approved` only, so the
    # successor cannot enter until the predecessor has left.
    if predecessor is not None:
        predecessor.status = PacketStatus.SUPERSEDED
        predecessor.superseded_by_packet_id = packet.id
        predecessor.superseded_at = now
        await session.flush()

    packet.status = PacketStatus.APPROVED
    packet.approval_source = approval_source
    packet.approved_at = now
    # Keep the embedded body status coherent with the row for schema-versioned bodies, exactly as the
    # ordinary approve route already does (`api/routers/packets.py:276-277`).
    if isinstance(packet.body, dict) and packet.body.get("schema_version"):
        packet.body = {**packet.body, "status": PacketStatus.APPROVED.value}
    await session.flush()

    # (5) The consequence set. A superseded contract invalidates the scene contracts derived from it —
    # they must be re-derived against the new authority before drafting. Staling (not deleting) preserves
    # the author's review history and the ApprovalBlocker rows that hang off them.
    staled: tuple[uuid.UUID, ...] = ()
    if predecessor is not None:
        staled = await _stale_children_of(session, chapter_id=chapter_id, superseded_packet_id=predecessor.id)
        # BOTH halves of the record: `unseeded_scene_ids` is the JUSTIFICATION (the scenes that had no seed,
        # which is the only reason amendment was permitted at all) and `staled_scene_packet_ids` is the
        # CONSEQUENCE. Keeping only the consequence would leave "why was this chapter amended?" answerable
        # only by re-deriving a diff against prose that has since moved on — which is exactly the question
        # provenance invariant 7 exists to keep answerable.
        packet.amendment_scope = {
            "predecessor_packet_id": str(predecessor.id),
            "unseeded_scene_ids": [str(x) for x in unseeded_scene_ids],
            "staled_scene_packet_ids": [str(x) for x in staled],
            "superseded_at": now.isoformat(),
        }
        await session.flush()

    log.info(
        "chapter_packet.authority_transition",
        chapter=str(chapter_id),
        packet=str(packet.id),
        superseded=str(predecessor.id) if predecessor else None,
        staled_scene_packets=len(staled),
        approval_source=approval_source.value,
        amendment=is_amendment,
    )
    return AuthorityOutcome(
        packet_id=packet.id,
        chapter_id=chapter_id,
        superseded_packet_id=predecessor.id if predecessor else None,
        staled_scene_packet_ids=staled,
        approval_source=approval_source.value,
    )


async def _stale_children_of(
    session, *, chapter_id: uuid.UUID, superseded_packet_id: uuid.UUID
) -> tuple[uuid.UUID, ...]:
    """Mark every live ScenePacket derived from the superseded packet STALE, with a reason that NAMES the
    cause. Returns the ids actually changed (so an idempotent replay reports an empty set, not a lie).

    Invariant 3's third clause — "never a superseded packet with authoritative live children" — is what
    this enforces: after it runs, no APPROVED ScenePacket still claims authority on the strength of a
    contract that no longer governs. Already-stale rows are left alone so a replay does not churn them.
    """
    rows = (
        (
            await session.execute(
                select(ScenePacket)
                .where(
                    ScenePacket.chapter_id == chapter_id,
                    ScenePacket.chapter_packet_id == superseded_packet_id,
                    ScenePacket.status != ScenePacketStatus.STALE,
                )
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    changed: list[uuid.UUID] = []
    for sp in rows:
        sp.status = ScenePacketStatus.STALE
        sp.stale_reason = AMENDMENT_STALE_REASON
        changed.append(sp.id)
    if changed:
        await session.flush()
    return tuple(changed)


async def approve_amendment(
    session,
    *,
    chapter_id: uuid.UUID,
    packet_id: uuid.UUID,
    approval_source: ChapterPacketApprovalSource = ChapterPacketApprovalSource.MANUAL_COMMAND,
    timeout_ms: int | None = None,
) -> AuthorityOutcome:
    """Approve an amendment and supersede its predecessor as ONE chapter-locked transaction.

    The public entry point for routes and workers alike. `run_under_chapter_workflow` acquires the
    per-chapter advisory lock BEFORE any row lock (the ordering discipline `chapter_lock.py:118-126`
    exists to enforce), runs the body, and owns the commit — so a crash anywhere before that commit
    changes nothing at all (invariant 6), and a `ChapterWorkflowBusy` means the body never ran.

    `approval_source` defaults to MANUAL_COMMAND because every wired caller is a deliberate command; the
    enum has no autonomous member, so no model-driven caller can supply one even deliberately.
    """
    from dominion.shared.chapter_lock import DEFAULT_LOCK_TIMEOUT_MS

    async def _body() -> AuthorityOutcome:
        # Revalidate eligibility from AUTHORITATIVE state, not the caller's pre-lock verdict. The verdict
        # that justified authoring this amendment was computed minutes and one model call ago.
        verdict = await assess_chapter(session, chapter_id=chapter_id)
        if verdict.approved_packet_id is None:
            raise AmendmentPredecessorMissing(
                f"chapter {chapter_id} has no approved packet to supersede; nothing was changed"
            )
        return await apply_authority_locked(
            session,
            chapter_id=chapter_id,
            packet_id=packet_id,
            approval_source=approval_source,
            expect_amendment=True,
            # The justification set, captured from the SAME under-lock verdict that authorised the
            # transition — so `amendment_scope` records the state the decision was actually made on, not a
            # re-read that could already have moved.
            unseeded_scene_ids=verdict.unseeded_scene_ids,
        )

    effective = DEFAULT_LOCK_TIMEOUT_MS if timeout_ms is None else timeout_ms
    return await run_under_chapter_workflow(session, chapter_id, _body, timeout_ms=effective)
