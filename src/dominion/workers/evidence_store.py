"""Persist import-scene evidence: a parent ImportSceneEvidence + its retained chunk children (ADR 0028,
Slice 3a′). The extractor (workers/import_evidence) is DB-free; this is the thin, atomic write seam the
adoption worker will call once per scene.

Identity reuse: an unchanged snapshot — same (scene_id, scene_version, prose_sha256, extractor schema) —
is a lookup, never a re-extraction, so re-adoption is cheap and deterministic. The parent's unique
identity index makes a concurrent first-writer the single owner; a loser re-reads the winner (R5).

Atomicity (fail-closed, R5): the caller runs this inside a CLEAN per-scene checkpoint transaction and
commits it, so a parent and ALL of its chunk children land in one commit or none — there is no partial
parent. The parent-identity SAVEPOINT wraps ONLY the parent insert (the sole place a race can occur);
a child/FK/other integrity error is never mistaken for a reuse — it propagates and the checkpoint rolls
back.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from dominion.shared.models import ImportSceneEvidence, ImportSceneEvidenceChunk, Scene
from dominion.shared.prose_fingerprint import prose_sha256
from dominion.workers.import_evidence import (
    EVIDENCE_SCHEMA_VERSION,
    ExtractionBudget,
    ImportEvidenceExtractor,
    SceneSource,
    ValidatedEvidence,
)

# The parent identity unique index (migrations.py). A violation on THIS constraint — and only this one —
# is the benign "someone else adopted this snapshot first" race that resolves to reuse.
_IDENTITY_CONSTRAINT = "uq_import_scene_evidence_identity"


def _is_identity_violation(exc: IntegrityError) -> bool:
    """True only for a unique violation on the parent identity index (R5). A child-unique / FK / any
    other integrity error is NOT this — it must propagate, never be swallowed as a reuse."""
    return _IDENTITY_CONSTRAINT in str(getattr(exc, "orig", exc))


async def _lookup(
    session: AsyncSession, *, scene_id: uuid.UUID, scene_version: int, prose_hash: str
) -> ImportSceneEvidence | None:
    return (
        await session.execute(
            select(ImportSceneEvidence).where(
                ImportSceneEvidence.scene_id == scene_id,
                ImportSceneEvidence.scene_version == scene_version,
                ImportSceneEvidence.prose_hash == prose_hash,
                ImportSceneEvidence.extractor_schema_version == EVIDENCE_SCHEMA_VERSION,
            )
        )
    ).scalar_one_or_none()


def _source(scene: Scene, prose_hash: str) -> SceneSource:
    return SceneSource(
        scene_id=scene.id,
        scene_version=scene.version,
        prose_hash=prose_hash,
        chapter_id=scene.chapter_id,
        scene_no=scene.scene_no,
        prose=scene.prose or "",
    )


async def ensure_scene_evidence(
    session: AsyncSession,
    *,
    scene: Scene,
    extractor: ImportEvidenceExtractor,
    budget: ExtractionBudget | None = None,
) -> ImportSceneEvidence:
    """Return the ImportSceneEvidence for this scene's CURRENT snapshot, extracting + persisting it (with
    its chunk children) only if it does not already exist. The CALLER commits — parent + children are one
    atomic checkpoint (R5). Raises EvidenceExtractionError if the extractor fails (nothing persists)."""
    # Capture primitives BEFORE the savepoint: a savepoint rollback in the except below expires flushed
    # ORM state, so the except must read locals only — never scene.<attr> (post-savepoint lazy-load /
    # MissingGreenlet, the class tests/test_sweeper_greenlet_guard.py enforces).
    scene_id = scene.id
    scene_version = scene.version
    snapshot = scene.prose or ""
    prose_hash = prose_sha256(snapshot)  # ONE hash helper (R4); also the parent identity's prose_hash

    existing = await _lookup(session, scene_id=scene_id, scene_version=scene_version, prose_hash=prose_hash)
    if existing is not None:
        return existing  # unchanged snapshot — no extraction (identity reuse)

    ve: ValidatedEvidence = await extractor.extract_scene(_source(scene, prose_hash), budget or ExtractionBudget())

    parent = ImportSceneEvidence(
        scene_id=scene_id,
        scene_version=scene_version,
        prose_hash=prose_hash,
        snapshot_prose=snapshot,  # R1: the immutable audit snapshot, copied before we trust it
        extractor_schema_version=EVIDENCE_SCHEMA_VERSION,
        chapter_id=scene.chapter_id,
        ledger=ve.ledger,
    )
    try:
        # SAVEPOINT scopes ONLY the parent-identity race (R5). A concurrent first-writer owns the identity;
        # this insert then fails the unique index. Children are added OUTSIDE the savepoint, so a child /
        # FK failure can never be swallowed as reuse.
        async with session.begin_nested():
            session.add(parent)
            await session.flush()
    except IntegrityError as exc:
        if not _is_identity_violation(exc):
            raise  # child / FK / other — propagate; the checkpoint rolls back (never a partial parent)
        # Locals only — no scene.<attr> read after the savepoint rollback (greenlet-safe).
        winner = await _lookup(session, scene_id=scene_id, scene_version=scene_version, prose_hash=prose_hash)
        if winner is None:
            # The unique index says a winner exists; if we cannot read it back, do NOT invent a reuse.
            raise
        return winner

    children = [
        ImportSceneEvidenceChunk(
            evidence_id=parent.id,
            chunk_index=c.chunk_index,
            char_offset=c.char_offset,
            char_end=c.char_end,
            ledger=c.ledger,
        )
        for c in ve.chunks
    ]
    for child in children:
        session.add(child)
    await session.flush()
    # Write-once, derived from the children we just wrote (children are authoritative for membership/order).
    parent.merged_shard_ids = {"chunk_ids": [str(c.id) for c in children]} if children else None
    return parent
