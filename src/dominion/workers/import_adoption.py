"""The import-adoption worker: turn one uncontracted, evidence-only imported chapter into a proposed
ChapterPacket, on demand (ADR 0028, Slice 3b — Lane A4).

Why its own worker (not the Job worker): an adoption cannot hold a single transaction across its long
model calls. It commits per-scene EVIDENCE checkpoints between extractions (workers/evidence_store.
ensure_scene_evidence), so a crash resumes from the last committed shard instead of re-extracting the
whole chapter. It is therefore a durable, LEASED claim loop over `import_adoptions`
(`FOR UPDATE SKIP LOCKED`, mirroring the Job worker's claim), with a lease that a crashed worker's row
outlives only until it expires — then boot recovery / the next claim re-queues it (Q3/Q12).

Lifecycle of one claimed adoption:
  1. CLAIM (short leased txn): mark RUNNING + stamp the lease, and CAPTURE the source fingerprint the
     author pass will run against — a hash over the chapter's non-superseded scenes (Q10). The SAME
     membership query is re-run at publish; a mismatch means the source drifted mid-pass.
  2. EVIDENCE (per-scene checkpoints): ensure_scene_evidence for each imported scene, committing each
     shard + the incrementally-filled `evidence_manifest` together. Unchanged snapshots are reused, so a
     resumed or repeated pass is cheap and deterministic. NO per-scene cursor — the manifest IS the
     progress record.
  3. PLAN (tiered idempotency, Q11): if the chapter already carries a proposed/approved ChapterPacket
     produced by a matching pass — same source fingerprint AND evidence set AND author-input fingerprint
     — REUSE it with NO model call (tier A/B). Otherwise author fresh (tier C).
  4. AUTHOR (tier C only, OUTSIDE the chapter lock): propose_packet_from_evidence authors + QA's + persists
     a proposed (or fail-closed blocked) ChapterPacket. This is the expensive model work and MUST NOT run
     under the per-chapter workflow lock.
  5. PUBLISH (short locked txn, compare-and-set): under run_under_chapter_workflow, RE-compute the source
     fingerprint and CAS it against the claim-time value. Match -> finalize the adoption to
     `contract_proposed`, link the packet, and write `seed_bindings` (Q8) + `author_input_fingerprint`
     (Q11). Drift -> INVALIDATED: the author pass is discarded (the proposed packet deleted) but the
     immutable evidence shards survive (Q13). `invalidated`/`cancelled` set by another path wins over a
     late worker completion.

Lock discipline (Q15/Q16): the per-chapter workflow lock is taken ONLY in the publish txn, never across
an evidence/author model call, and always before the adoption row lock. If it is busy, the publish raises
ChapterWorkflowBusy; the worker ROLLS BACK, re-queues the adoption, and does NOT spin in-process — the
drain re-enters it later (the 4s lock-acquire timeout is the backoff).

Non-goals fenced here (ADR 0028 later slices): `mode=amendment` is refused closed (never partially
implemented); this worker ends at `contract_proposed` and NEVER advances a RevisionRequest, mints a
revision Job, or reconciles on-revise (Slice 3c).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import structlog
from sqlalchemy import CursorResult, and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dominion.shared.chapter_lock import (
    DEFAULT_LOCK_TIMEOUT_MS,
    ChapterWorkflowBusy,
    run_under_chapter_workflow,
)
from dominion.shared.db import SessionFactory
from dominion.shared.enums import (
    ImportAdoptionMode,
    ImportAdoptionStatus,
    PacketStatus,
    SceneStatus,
)
from dominion.shared.models import Chapter, ChapterPacket, ImportAdoption, ImportSceneEvidence, Scene
from dominion.shared.prose_fingerprint import chapter_source_fingerprint, prose_sha256
from dominion.workers import packet as packet_pipeline
from dominion.workers.evidence_store import ensure_scene_evidence
from dominion.workers.import_evidence import (
    EvidenceExtractionError,
    ImportEvidenceExtractor,
    LlmImportEvidenceExtractor,
)
from dominion.workers.packet import canon_conflict
from dominion.workers.packet import evidence as evidence_mod

log = structlog.get_logger()

WORKER_ID = f"adoption-{os.getpid()}"

# A claimed adoption's lease: long enough to comfortably exceed the author-pass model budget
# (settings.packet_time_budget_s == 300s), so a live worker (which renews the lease at every evidence
# checkpoint) is never mistaken for a crashed one. A row RUNNING past this without renewal is treated as
# abandoned and re-claimable — the durable-lease half of crash recovery.
LEASE_TTL_S = 1800

_AMENDMENT_REFUSAL = (
    "amendment adoption mode is a Slice 3b non-goal and is refused closed; it is never partially "
    "implemented (ADR 0028)."
)


class AmendmentModeUnsupported(Exception):
    """`mode=amendment` reached the worker. Slice 3b refuses it closed rather than run a partial
    copy-on-write adoption; the adoption is failed with this typed reason (never a silent proceed)."""


@dataclass(frozen=True)
class _Claim:
    """The primitives captured from a freshly-claimed adoption, read BEFORE the claim txn commits so no
    expired-ORM attribute is touched afterwards."""

    adoption_id: uuid.UUID
    chapter_id: uuid.UUID
    book_id: uuid.UUID
    mode: str
    source_fingerprint: str
    # Q11 tier-C: the operator Re-author override. When set, run_one_adoption BYPASSES the reuse gate and
    # authors fresh (one deliberate additional author call). NULL for an ordinary claim.
    force_author_token: uuid.UUID | None


# ------------------------------------------------------------------------------------------------ #
# Fingerprints (Q10/Q11). One hash helper (R4): prose_sha256 over a deterministic, order-stable join.  #
# ------------------------------------------------------------------------------------------------ #


def _sha(parts: Iterable[str]) -> str:
    """Stable sha256 over the newline-join of `parts` (parts never contain newlines)."""
    return prose_sha256("\n".join(parts))


async def _chapter_scene_rows(
    session: AsyncSession, chapter_id: uuid.UUID
) -> list[tuple[int, uuid.UUID, int, str | None]]:
    """The chapter's non-superseded scenes as `(scene_no, scene_id, version, prose)` — the SINGLE
    membership query used at BOTH claim and publish (Q10), so the fingerprints are comparable."""
    rows = (
        await session.execute(
            select(Scene.scene_no, Scene.id, Scene.version, Scene.prose).where(
                Scene.chapter_id == chapter_id, Scene.status != SceneStatus.SUPERSEDED
            )
        )
    ).all()
    return [(int(r[0]), r[1], int(r[2]), r[3]) for r in rows]


def _manifest_entries(manifest: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The shard list out of the stored `{"shards": [...]}` envelope (empty for a missing/blank manifest)."""
    if not isinstance(manifest, dict):
        return []
    shards = manifest.get("shards")
    return [s for s in shards if isinstance(s, dict)] if isinstance(shards, list) else []


def _evidence_fingerprint(entries: Sequence[dict[str, Any]]) -> str:
    """Order-independent fingerprint of the evidence-shard IDENTITIES consumed — the evidence-equivalence
    key (Q11). Two passes over the same scenes at the same prose produce the same value."""
    ids = sorted(
        f"{e.get('scene_id')}:{e.get('scene_version')}:{e.get('prose_hash')}:{e.get('extractor_schema_version')}"
        for e in entries
    )
    return _sha(ids)


async def _canon_snapshot_fingerprint(
    retrieve: canon_conflict.CanonRetriever, bundle: Sequence[evidence_mod.SceneEvidence]
) -> str:
    """Fingerprint the locked-canon snapshot the author would see: the same evidence-built retrieval query
    propose_packet_from_evidence runs, hashed by each hit's identity + body. Sensitive to a canon edit, so
    an author pass over changed canon re-authors rather than silently reusing (Q11)."""
    query = evidence_mod.evidence_query(bundle)
    hits = list(await retrieve(query)) if query.strip() else []
    return _sha(sorted(f"{h.get('id')}:{prose_sha256(str(h.get('body') or ''))}" for h in hits))


def _seed_bindings(packet_body: dict[str, Any], scene_map: dict[int, uuid.UUID]) -> dict[str, Any] | None:
    """The seed->imported-scene lineage written once at publish (Q8): `{seed_id: {scene_no, scene_id}}`.
    Maps each authored scene_seed (by its display `scene_no`) back to the imported Scene it was adopted
    from, so a later derive/resume can bind an approved packet's seed to its source scene. Seeds whose
    scene_no has no non-superseded scene are skipped (never a dangling binding)."""
    bindings: dict[str, Any] = {}
    for seed in packet_body.get("scene_seeds") or []:
        if not isinstance(seed, dict):
            continue
        seed_id = str(seed.get("seed_id") or "").strip()
        scene_no = seed.get("scene_no")
        if not seed_id or scene_no is None:
            continue
        scene_id = scene_map.get(int(scene_no))
        if scene_id is None:
            continue
        bindings[seed_id] = {"scene_no": int(scene_no), "scene_id": str(scene_id)}
    return bindings or None


def _retriever(
    session: AsyncSession, book_id: uuid.UUID, retrieve: canon_conflict.CanonRetriever | None
) -> canon_conflict.CanonRetriever:
    """The injected retriever (tests) or the production session-bound one at the author's broad-canon k —
    the SAME `k` propose_packet_from_evidence uses internally, so the fingerprinted snapshot matches what
    the author is actually shown."""
    return retrieve or canon_conflict.session_retriever(session, book_id, k=packet_pipeline._CANON_K)


# ------------------------------------------------------------------------------------------------ #
# Phase 1 — claim (leased) + small helpers for the terminal states.                                  #
# ------------------------------------------------------------------------------------------------ #


async def _claim_one(session_factory: async_sessionmaker[AsyncSession], lease_ttl_s: int) -> _Claim | None:
    """Atomically claim the oldest claimable adoption (`FOR UPDATE SKIP LOCKED`, parallel-worker safe) and
    capture its source fingerprint (Q10). Claimable == QUEUED, or RUNNING with an expired lease (a crashed
    worker's abandoned row). `book_id IS NOT NULL` mirrors the Job worker's ownership guard."""
    async with session_factory() as session:
        cutoff = datetime.now(UTC) - timedelta(seconds=lease_ttl_s)
        stmt = (
            select(ImportAdoption)
            .where(
                ImportAdoption.book_id.is_not(None),
                or_(
                    ImportAdoption.status == ImportAdoptionStatus.QUEUED.value,
                    and_(
                        ImportAdoption.status == ImportAdoptionStatus.RUNNING.value,
                        ImportAdoption.claimed_at.is_not(None),
                        ImportAdoption.claimed_at < cutoff,
                    ),
                ),
            )
            .order_by(ImportAdoption.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        adoption = (await session.execute(stmt)).scalar_one_or_none()
        if adoption is None:
            await session.commit()
            return None
        adoption.status = ImportAdoptionStatus.RUNNING.value
        adoption.claimed_by = WORKER_ID
        adoption.claimed_at = datetime.now(UTC)
        adoption.error = None
        rows = await _chapter_scene_rows(session, adoption.chapter_id)
        adoption.source_fingerprint = chapter_source_fingerprint(rows)
        claim = _Claim(
            adoption_id=adoption.id,
            chapter_id=adoption.chapter_id,
            book_id=adoption.book_id,
            mode=adoption.mode,
            source_fingerprint=adoption.source_fingerprint,
            # The claim SELECT loads the full ImportAdoption entity, so the tier-C override column rides
            # along; surface it here before the ORM row expires at commit (Q11 tier-C).
            force_author_token=adoption.force_author_token,
        )
        await session.commit()
        return claim


async def _fail_adoption(session_factory: async_sessionmaker[AsyncSession], adoption_id: uuid.UUID, error: str) -> None:
    """Mark an adoption terminally FAILED with a diagnosable reason. Used for a refused amendment and for a
    non-resumable evidence-extraction failure; the already-committed evidence shards survive for reuse."""
    async with session_factory() as session:
        adoption = await session.get(ImportAdoption, adoption_id, with_for_update=True)
        if adoption is None:
            await session.commit()
            return
        adoption.status = ImportAdoptionStatus.FAILED.value
        adoption.error = error[:2000]
        adoption.finished_at = datetime.now(UTC)
        await session.commit()


async def _requeue(session_factory: async_sessionmaker[AsyncSession], adoption_id: uuid.UUID) -> None:
    """Release the lease and return the adoption to QUEUED so the drain re-enters it (the ChapterWorkflowBusy
    path — the worker never spins on the lock in-process, Q16)."""
    async with session_factory() as session:
        adoption = await session.get(ImportAdoption, adoption_id, with_for_update=True)
        if adoption is None:
            await session.commit()
            return
        # Only a still-RUNNING (this-pass) row is re-queued; never resurrect a row another path finalized.
        if adoption.status == ImportAdoptionStatus.RUNNING.value:
            adoption.status = ImportAdoptionStatus.QUEUED.value
            adoption.claimed_by = None
            adoption.claimed_at = None
        await session.commit()


# ------------------------------------------------------------------------------------------------ #
# Phase 2 — evidence checkpoints (resumable; manifest fills incrementally).                           #
# ------------------------------------------------------------------------------------------------ #


async def _run_evidence(
    session_factory: async_sessionmaker[AsyncSession],
    adoption_id: uuid.UUID,
    chapter_id: uuid.UUID,
    extractor: ImportEvidenceExtractor,
) -> list[dict[str, Any]]:
    """Ensure evidence for every imported (non-superseded, non-empty) scene, one CLEAN checkpoint txn per
    scene: each commits the shard (parent + chunk children) together with the grown `evidence_manifest`
    and a renewed lease. An unchanged snapshot is reused, never re-extracted. Raises EvidenceExtractionError
    (the caller fails the pass closed); prior committed checkpoints survive for a resumed pass."""
    async with session_factory() as session:
        scenes = (
            await session.execute(
                select(Scene.id, Scene.prose)
                .where(Scene.chapter_id == chapter_id, Scene.status != SceneStatus.SUPERSEDED)
                .order_by(Scene.scene_no)
            )
        ).all()
    scene_ids = [r[0] for r in scenes if (r[1] or "").strip()]

    manifest: list[dict[str, Any]] = []
    for scene_id in scene_ids:
        async with session_factory() as cp:
            scene = await cp.get(Scene, scene_id)
            if scene is None:  # deleted between the scan and the checkpoint — skip, drift is caught at publish
                await cp.commit()
                continue
            evidence = await ensure_scene_evidence(cp, scene=scene, extractor=extractor)
            manifest.append(
                {
                    "scene_id": str(evidence.scene_id),
                    "scene_version": evidence.scene_version,
                    "prose_hash": evidence.prose_hash,
                    "extractor_schema_version": evidence.extractor_schema_version,
                    "evidence_id": str(evidence.id),
                }
            )
            adoption = await cp.get(ImportAdoption, adoption_id)
            if adoption is not None:
                adoption.evidence_manifest = {"shards": list(manifest)}
                adoption.claimed_at = datetime.now(UTC)  # lease renewal — a live worker keeps its claim
            await cp.commit()
    return manifest


async def _build_bundle(
    session: AsyncSession, manifest: Sequence[dict[str, Any]], chapter: Chapter
) -> list[evidence_mod.SceneEvidence]:
    """Load the manifest's evidence shards into the pure `SceneEvidence` bundle the author consumes (no ORM
    leaks: plain value objects). Skips a shard/scene that vanished."""
    bundle: list[evidence_mod.SceneEvidence] = []
    for entry in manifest:
        evidence = await session.get(ImportSceneEvidence, uuid.UUID(str(entry["evidence_id"])))
        scene = await session.get(Scene, uuid.UUID(str(entry["scene_id"])))
        if evidence is None or scene is None:
            continue
        bundle.append(
            evidence_mod.SceneEvidence(
                scene_id=evidence.scene_id,
                scene_no=scene.scene_no,
                scene_version=evidence.scene_version,
                prose_hash=evidence.prose_hash,
                ledger=evidence.ledger,
                snapshot_prose_len=len(evidence.snapshot_prose or ""),
                pov=chapter.pov,
            )
        )
    return bundle


# ------------------------------------------------------------------------------------------------ #
# Phase 3 — tiered idempotency: reuse a matching existing packet with no model call (Q11).            #
# ------------------------------------------------------------------------------------------------ #


async def _find_reuse(
    session: AsyncSession,
    chapter_id: uuid.UUID,
    source_fingerprint: str,
    evidence_fingerprint: str,
    author_input_fingerprint: str,
) -> tuple[uuid.UUID, str, dict[str, Any]] | None:
    """The reuse gate (tiers A/B): if the chapter already carries a proposed/approved ChapterPacket whose
    PRODUCING adoption matched this pass on ALL THREE — source fingerprint, evidence set, author-input
    fingerprint — return `(packet_id, status, body)` to reuse without authoring. Any break -> None (author
    fresh, tier C). An approved packet + full match is the no-op case (the adoption just links + completes)."""
    packet = (
        await session.execute(
            select(ChapterPacket)
            .where(
                ChapterPacket.chapter_id == chapter_id,
                ChapterPacket.status.in_([PacketStatus.PROPOSED.value, PacketStatus.APPROVED.value]),
            )
            .order_by(ChapterPacket.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if packet is None:
        return None
    producer = (
        await session.execute(
            select(ImportAdoption)
            .where(
                ImportAdoption.chapter_packet_id == packet.id,
                ImportAdoption.status == ImportAdoptionStatus.CONTRACT_PROPOSED.value,
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if producer is None:
        return None
    matches = (
        producer.source_fingerprint == source_fingerprint
        and _evidence_fingerprint(_manifest_entries(producer.evidence_manifest)) == evidence_fingerprint
        and producer.author_input_fingerprint == author_input_fingerprint
    )
    if not matches:
        return None
    return packet.id, str(packet.status), dict(packet.body)


# ------------------------------------------------------------------------------------------------ #
# Phase 5 — publish under the per-chapter workflow lock, compare-and-set on the fingerprint (Q10/Q13). #
# ------------------------------------------------------------------------------------------------ #


async def _delete_pass_packet(session: AsyncSession, packet_id: uuid.UUID) -> None:
    """Discard THIS pass's ChapterPacket (proposed or blocked). Never deletes an approved packet — a human
    approval, even of a since-stale contract, is theirs to keep, not ours to revoke."""
    packet = await session.get(ChapterPacket, packet_id)
    if packet is not None and str(packet.status) in (PacketStatus.PROPOSED.value, PacketStatus.BLOCKED.value):
        await session.delete(packet)


async def publish_adoption(
    session_factory: async_sessionmaker[AsyncSession] = SessionFactory,
    *,
    adoption_id: uuid.UUID,
    chapter_id: uuid.UUID,
    packet_id: uuid.UUID,
    packet_status: str,
    packet_body: dict[str, Any],
    manifest_entries: Sequence[dict[str, Any]],
    author_input_fingerprint: str,
    created_packet: bool,
    timeout_ms: int | None = DEFAULT_LOCK_TIMEOUT_MS,
) -> str:
    """Finalize one adoption under the per-chapter workflow lock, compare-and-set on the source fingerprint.

    Returns the outcome: `contract_proposed` (usable packet, no drift — links the packet, writes
    seed_bindings + author_input_fingerprint), `failed` (a blocked packet, linked as diagnostic, Q14),
    `invalidated` (fingerprint drift — the author pass is discarded; a packet WE created this pass is
    deleted, evidence shards survive, Q13), or `skipped` (another path already finalized/cancelled this
    adoption — invalidated wins over a late worker completion).

    Raises ChapterWorkflowBusy if the lock can't be acquired within `timeout_ms`; nothing is written and
    the caller re-queues (Q16). The chapter lock is taken FIRST, then the adoption row lock (order-safe).
    """
    async with session_factory() as session:

        async def _body() -> str:
            adoption = await session.get(ImportAdoption, adoption_id, with_for_update=True)
            if adoption is None:
                return "skipped"
            if adoption.status != ImportAdoptionStatus.RUNNING.value:
                # invalidated / cancelled by another path since we claimed — it wins; drop our orphan packet.
                if created_packet:
                    await _delete_pass_packet(session, packet_id)
                return "skipped"

            rows = await _chapter_scene_rows(session, chapter_id)
            now = datetime.now(UTC)
            if chapter_source_fingerprint(rows) != adoption.source_fingerprint:
                adoption.status = ImportAdoptionStatus.INVALIDATED.value
                adoption.error = (
                    "source fingerprint drifted during the author pass; the pass is invalidated "
                    "(evidence shards retained)"
                )
                adoption.finished_at = now
                if created_packet:
                    await _delete_pass_packet(session, packet_id)
                return "invalidated"

            adoption.evidence_manifest = {"shards": list(manifest_entries)}
            adoption.finished_at = now
            if packet_status == PacketStatus.BLOCKED.value:
                adoption.status = ImportAdoptionStatus.FAILED.value
                adoption.chapter_packet_id = packet_id
                adoption.error = "packet authoring failed closed; the blocked packet is linked as diagnostic (Q14)"
                return "failed"

            adoption.status = ImportAdoptionStatus.CONTRACT_PROPOSED.value
            adoption.chapter_packet_id = packet_id
            adoption.author_input_fingerprint = author_input_fingerprint
            adoption.seed_bindings = _seed_bindings(packet_body, {r[0]: r[1] for r in rows})
            adoption.error = None
            return "contract_proposed"

        return await run_under_chapter_workflow(session, chapter_id, _body, timeout_ms=timeout_ms)


# ------------------------------------------------------------------------------------------------ #
# The claim loop entry points.                                                                       #
# ------------------------------------------------------------------------------------------------ #


async def run_one_adoption(
    session_factory: async_sessionmaker[AsyncSession] = SessionFactory,
    *,
    extractor: ImportEvidenceExtractor | None = None,
    retrieve: canon_conflict.CanonRetriever | None = None,
    lease_ttl_s: int = LEASE_TTL_S,
    publish_timeout_ms: int | None = DEFAULT_LOCK_TIMEOUT_MS,
) -> bool:
    """Claim and process ONE adoption end to end. Returns False when nothing is claimable (the drain stops),
    True otherwise (claimed — whether it published, reused, invalidated, or failed closed).

    `extractor`/`retrieve` are injectable seams for tests; production defaults to the real LLM extractor
    and the session-bound canon retriever.
    """
    extractor = extractor or LlmImportEvidenceExtractor()
    claim = await _claim_one(session_factory, lease_ttl_s)
    if claim is None:
        return False

    if claim.mode == ImportAdoptionMode.AMENDMENT.value:
        await _fail_adoption(
            session_factory, claim.adoption_id, f"{AmendmentModeUnsupported.__name__}: {_AMENDMENT_REFUSAL}"
        )
        log.info("adoption.amendment_refused", adoption=str(claim.adoption_id))
        return True

    # Phase 2: evidence checkpoints (resumable). A non-resumable extraction error fails the pass closed;
    # committed shards survive for a re-started adoption to reuse.
    try:
        manifest = await _run_evidence(session_factory, claim.adoption_id, claim.chapter_id, extractor)
    except EvidenceExtractionError as exc:
        await _fail_adoption(session_factory, claim.adoption_id, f"evidence extraction failed: {exc}")
        log.error("adoption.evidence_failed", adoption=str(claim.adoption_id), error=str(exc))
        return True

    # Phase 3: build the author bundle + input fingerprints, then the reuse plan.
    async with session_factory() as session:
        chapter = await session.get(Chapter, claim.chapter_id)
        if chapter is None:
            await session.commit()
            await _fail_adoption(session_factory, claim.adoption_id, "chapter vanished before authoring")
            return True
        bundle = await _build_bundle(session, manifest, chapter)
        evidence_fingerprint = _evidence_fingerprint(manifest)
        canon_fingerprint = await _canon_snapshot_fingerprint(_retriever(session, claim.book_id, retrieve), bundle)
        author_input_fingerprint = _sha([evidence_fingerprint, canon_fingerprint])
        # Q11 tier-C (operator Re-author): a force token BYPASSES the reuse gate entirely — author fresh, a
        # new proposed packet, one deliberate additional author call. The token is an execution command,
        # NOT author-input identity: author_input_fingerprint is still computed above and written at publish
        # unchanged, so a LATER ordinary Start reuses this force-generated packet via _find_reuse.
        reuse = (
            None
            if claim.force_author_token is not None
            else await _find_reuse(
                session, claim.chapter_id, claim.source_fingerprint, evidence_fingerprint, author_input_fingerprint
            )
        )

    if reuse is not None:
        packet_id, packet_status, packet_body = reuse
        created_packet = False
        log.info("adoption.reuse", adoption=str(claim.adoption_id), packet=str(packet_id))
    else:
        # Phase 4: author OUTSIDE the chapter lock (the expensive model work). propose_* flushes the packet;
        # we own the commit. A blocked packet is authored too — it is finalized to `failed` at publish.
        async with session_factory() as session:
            chapter = await session.get(Chapter, claim.chapter_id)
            if chapter is None:
                await session.commit()
                await _fail_adoption(session_factory, claim.adoption_id, "chapter vanished before authoring")
                return True
            packet = await packet_pipeline.propose_packet_from_evidence(
                session, chapter=chapter, evidence=bundle, retrieve=_retriever(session, claim.book_id, retrieve)
            )
            await session.commit()
            packet_id = packet.id
            packet_status = str(packet.status)
            packet_body = dict(packet.body)
        created_packet = True
        log.info("adoption.authored", adoption=str(claim.adoption_id), packet=str(packet_id), status=packet_status)

    # Phase 5: CAS publish under the chapter workflow lock. Busy -> roll back + re-queue (no in-process spin).
    try:
        outcome = await publish_adoption(
            session_factory,
            adoption_id=claim.adoption_id,
            chapter_id=claim.chapter_id,
            packet_id=packet_id,
            packet_status=packet_status,
            packet_body=packet_body,
            manifest_entries=manifest,
            author_input_fingerprint=author_input_fingerprint,
            created_packet=created_packet,
            timeout_ms=publish_timeout_ms,
        )
    except ChapterWorkflowBusy:
        await _requeue(session_factory, claim.adoption_id)
        log.info("adoption.chapter_busy_requeued", adoption=str(claim.adoption_id), chapter=str(claim.chapter_id))
        return True
    log.info("adoption.finalized", adoption=str(claim.adoption_id), outcome=outcome)
    return True


async def recover_stale_adoptions(session: AsyncSession, *, lease_ttl_s: int = LEASE_TTL_S) -> int:
    """Boot recovery: re-queue every RUNNING adoption whose lease has expired (a worker a redeploy killed
    mid-pass), so the drain drafts them instead of leaving them stuck RUNNING. The caller commits. Returns
    how many were re-queued. Idempotent — a fresh lease excludes a live worker's row."""
    cutoff = datetime.now(UTC) - timedelta(seconds=lease_ttl_s)
    result = cast(
        CursorResult[Any],
        await session.execute(
            update(ImportAdoption)
            .where(
                ImportAdoption.status == ImportAdoptionStatus.RUNNING.value,
                ImportAdoption.claimed_at.is_not(None),
                ImportAdoption.claimed_at < cutoff,
            )
            .values(status=ImportAdoptionStatus.QUEUED.value, claimed_by=None, claimed_at=None)
        ),
    )
    return result.rowcount or 0


# At most one adoption drain per process (FastAPI background tasks share the API event loop) — mirrors
# background_work._drain_lock for the Job queue.
_drain_lock = asyncio.Lock()


def drain_locked() -> bool:
    return _drain_lock.locked()


async def drain_adoptions(
    session_factory: async_sessionmaker[AsyncSession] = SessionFactory,
    *,
    extractor: ImportEvidenceExtractor | None = None,
    retrieve: canon_conflict.CanonRetriever | None = None,
    lease_ttl_s: int = LEASE_TTL_S,
    publish_timeout_ms: int | None = DEFAULT_LOCK_TIMEOUT_MS,
) -> None:
    """Process claimable adoptions one at a time until none remain. Re-queues crashed leases first, then
    drains. Single-flight per process. An unexpected error stops the pass (the lease governs a later
    retry); it never hot-loops."""
    if _drain_lock.locked():
        return
    async with _drain_lock:
        async with session_factory() as session:
            requeued = await recover_stale_adoptions(session, lease_ttl_s=lease_ttl_s)
            await session.commit()
        if requeued:
            log.info("adoption.recovered_stale", requeued=requeued)
        while True:
            try:
                did = await run_one_adoption(
                    session_factory,
                    extractor=extractor,
                    retrieve=retrieve,
                    lease_ttl_s=lease_ttl_s,
                    publish_timeout_ms=publish_timeout_ms,
                )
            except Exception as exc:  # noqa: BLE001 — one bad adoption must not strand the process; lease retries
                log.error("adoption.drain_error", error=str(exc))
                break
            if not did:
                break


async def _loop(interval: float) -> None:
    while True:
        try:
            did = await run_one_adoption()
        except Exception:  # noqa: BLE001 — logged/persisted inside; keep the loop alive
            did = True
        if not did:
            await asyncio.sleep(interval)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Dominion import-adoption worker: adopt one imported chapter into a proposed contract, then exit."
    )
    parser.add_argument("--once", action="store_true", help="process a single adoption and exit")
    parser.add_argument("--loop", action="store_true", help="poll for claimable adoptions continuously")
    parser.add_argument("--interval", type=float, default=2.0, help="poll interval for --loop")
    args = parser.parse_args()

    if args.loop:
        asyncio.run(_loop(args.interval))
    else:
        did = asyncio.run(run_one_adoption())
        if not did:
            log.info("adoption.idle", msg="no claimable adoptions")


if __name__ == "__main__":
    main()
