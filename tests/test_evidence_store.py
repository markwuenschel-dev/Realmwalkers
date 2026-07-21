"""Oracles for the import-scene evidence persistence seam (ADR 0028, Slice 3a′). Direct-DB (needs
Postgres; skips locally, runs under `just test` / CI).

The six core oracles (identity reuse, chunk reconstruction, single-pass, race/atomic reuse, rollback/
no-partial-parent, one-hash-function) plus the four the reconcile added (immutable source audit after an
in-place Scene.prose edit, interval integrity, non-identity error propagation, migration shape).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from dominion.shared.enums import SceneStatus
from dominion.shared.models import Book, Chapter, ImportSceneEvidence, ImportSceneEvidenceChunk, Scene
from dominion.shared.prose_fingerprint import prose_sha256
from dominion.workers import evidence_store
from dominion.workers.evidence_store import ensure_scene_evidence
from dominion.workers.import_evidence import FakeImportEvidenceExtractor
from dominion.workers.revision import prose_hash


async def _scene(s, *, prose="Imported prologue prose.", version=1) -> Scene:
    book = Book(title="Evidence Store")
    s.add(book)
    await s.flush()
    ch = Chapter(book_id=book.id, chapter_no=1, pov="Marcus")
    s.add(ch)
    await s.flush()
    scene = Scene(chapter_id=ch.id, scene_no=1, prose=prose, version=version, status=SceneStatus.PENDING_REVIEW)
    s.add(scene)
    await s.flush()
    return scene


async def _children(s, evidence_id) -> list[ImportSceneEvidenceChunk]:
    return list(
        (
            await s.execute(
                select(ImportSceneEvidenceChunk)
                .where(ImportSceneEvidenceChunk.evidence_id == evidence_id)
                .order_by(ImportSceneEvidenceChunk.chunk_index)
            )
        )
        .scalars()
        .all()
    )


async def _count(s, model) -> int:
    return (await s.execute(select(func.count()).select_from(model))).scalar_one()


async def test_identity_reuse_extracts_once(db_factory):
    """An unchanged snapshot is a lookup, never a re-extraction — one parent, no duplicate children."""
    async with db_factory() as s:
        scene = await _scene(s)
        fake = FakeImportEvidenceExtractor()
        first = await ensure_scene_evidence(s, scene=scene, extractor=fake)
        second = await ensure_scene_evidence(s, scene=scene, extractor=fake)
        await s.commit()

        assert second.id == first.id
        assert fake.calls == [scene.id]  # extracted exactly once
        assert await _count(s, ImportSceneEvidence) == 1


async def test_chunk_reconstruction_children_authoritative(db_factory):
    async with db_factory() as s:
        scene = await _scene(s)
        fake = FakeImportEvidenceExtractor(
            chunk_ledgers={scene.id: [{"events": [{"span": [0, 1]}]}, {"pov": "P"}, {"setting": "docks"}]}
        )
        parent = await ensure_scene_evidence(s, scene=scene, extractor=fake)
        await s.commit()

        kids = await _children(s, parent.id)
        assert [k.chunk_index for k in kids] == [0, 1, 2]
        assert parent.merged_shard_ids == {"chunk_ids": [str(k.id) for k in kids]}  # derived from children
        assert kids[1].ledger["pov"] == "P"  # chunk-local ledger stored unshifted


async def test_single_pass_has_no_children(db_factory):
    async with db_factory() as s:
        scene = await _scene(s)
        parent = await ensure_scene_evidence(s, scene=scene, extractor=FakeImportEvidenceExtractor())
        await s.commit()

        assert parent.merged_shard_ids is None
        assert await _count(s, ImportSceneEvidenceChunk) == 0


async def test_race_loser_returns_the_winner_no_second_parent(db_factory, monkeypatch):
    """Two writers on one identity: the winner commits; the loser's insert hits the identity index,
    catches ONLY that violation, and re-reads the winner. Exactly one parent."""
    async with db_factory() as s1:
        scene = await _scene(s1)
        scene_id = scene.id
        winner = await ensure_scene_evidence(s1, scene=scene, extractor=FakeImportEvidenceExtractor())
        await s1.commit()
        winner_id = winner.id

    async with db_factory() as s2:
        scene2 = await s2.get(Scene, scene_id)
        # Simulate the race window: the loser's FIRST lookup missed (its snapshot predated the commit),
        # so it proceeds to insert; the re-lookup after the violation is real.
        real_lookup = evidence_store._lookup
        seen = {"n": 0}

        async def _patched(session, **kw):
            seen["n"] += 1
            return None if seen["n"] == 1 else await real_lookup(session, **kw)

        monkeypatch.setattr(evidence_store, "_lookup", _patched)
        result = await ensure_scene_evidence(s2, scene=scene2, extractor=FakeImportEvidenceExtractor())
        await s2.commit()

        assert result.id == winner_id
        assert await _count(s2, ImportSceneEvidence) == 1


async def test_rollback_leaves_no_partial_parent(db_factory):
    """Fail-closed: a checkpoint rolled back before commit persists NEITHER the parent NOR its children."""
    async with db_factory() as s:
        scene = await _scene(s)
        await s.commit()  # the scene is committed; only the evidence checkpoint is rolled back
        await ensure_scene_evidence(
            s,
            scene=scene,
            extractor=FakeImportEvidenceExtractor(chunk_ledgers={scene.id: [{"pov": "A"}, {"pov": "B"}]}),
        )
        await s.rollback()

        assert await _count(s, ImportSceneEvidence) == 0
        assert await _count(s, ImportSceneEvidenceChunk) == 0


async def test_prose_hash_is_the_one_shared_helper(db_factory):
    async with db_factory() as s:
        scene = await _scene(s)
        parent = await ensure_scene_evidence(s, scene=scene, extractor=FakeImportEvidenceExtractor())
        await s.commit()
        # The parent identity hash == the canonical prose_sha256 == Slice 2's revision.prose_hash (R4).
        assert parent.prose_hash == prose_sha256(scene.prose) == prose_hash(scene.prose)


async def test_snapshot_survives_an_in_place_scene_edit(db_factory):
    """R1: Scene.prose is the current manuscript and can be hand-edited in place; the evidence's snapshot
    is the immutable audit of the past identity and must not move with it."""
    async with db_factory() as s:
        scene = await _scene(s, prose="ORIGINAL prose bytes.")
        orig_hash = prose_sha256("ORIGINAL prose bytes.")
        ev = await ensure_scene_evidence(s, scene=scene, extractor=FakeImportEvidenceExtractor())
        await s.commit()
        ev_id = ev.id

        scene.prose = "EDITED in the inbox — different bytes."  # the in-place mutation ADR-0028 warns about
        await s.commit()

    async with db_factory() as s2:
        ev2 = await s2.get(ImportSceneEvidence, ev_id)
        assert ev2.snapshot_prose == "ORIGINAL prose bytes."  # audit intact
        assert ev2.prose_hash == orig_hash  # identity pinned to the snapshot, not the current scene
        assert (await s2.get(Scene, scene.id)).prose == "EDITED in the inbox — different bytes."


async def test_chunk_intervals_are_ordered_and_well_formed(db_factory):
    async with db_factory() as s:
        scene = await _scene(s)
        fake = FakeImportEvidenceExtractor(chunk_ledgers={scene.id: [{"pov": "A"}, {"pov": "B"}, {"pov": "C"}]})
        parent = await ensure_scene_evidence(s, scene=scene, extractor=fake)
        await s.commit()

        kids = await _children(s, parent.id)
        for k in kids:
            assert k.char_offset < k.char_end  # a non-empty window
        for a, b in zip(kids, kids[1:], strict=False):
            assert a.char_end <= b.char_offset  # ordered, non-overlapping


async def test_non_identity_integrity_error_propagates_not_reuse(db_factory):
    """A non-identity integrity error (here: a dangling scene/chapter FK) must RAISE — never be swallowed
    as a reuse — and persist nothing."""
    async with db_factory() as s:
        ghost = Scene(
            id=uuid.uuid4(),
            chapter_id=uuid.uuid4(),  # no such chapter → parent insert FK-fails
            scene_no=1,
            version=1,
            prose="x",
            status=SceneStatus.PENDING_REVIEW,
        )
        with pytest.raises(IntegrityError):
            await ensure_scene_evidence(s, scene=ghost, extractor=FakeImportEvidenceExtractor())
        await s.rollback()

        assert await _count(s, ImportSceneEvidence) == 0
        assert await _count(s, ImportSceneEvidenceChunk) == 0


async def test_migration_shape_snapshot_column_and_cascade(db_factory):
    """The parent carries snapshot_prose; the child unique is (evidence_id, chunk_index); the FK CASCADEs
    at the DB level (deleting a parent removes its children)."""
    # Model/DDL shape (what create_all builds):
    assert "snapshot_prose" in ImportSceneEvidence.__table__.columns
    uniques = {
        tuple(sorted(c.name for c in con.columns))
        for con in ImportSceneEvidenceChunk.__table__.constraints
        if con.__class__.__name__ == "UniqueConstraint"
    }
    assert ("chunk_index", "evidence_id") in uniques

    async with db_factory() as s:
        scene = await _scene(s)
        parent = await ensure_scene_evidence(
            s,
            scene=scene,
            extractor=FakeImportEvidenceExtractor(chunk_ledgers={scene.id: [{"pov": "A"}, {"pov": "B"}]}),
        )
        await s.commit()
        ev_id = parent.id
        assert await _count(s, ImportSceneEvidenceChunk) == 2

        await s.execute(delete(ImportSceneEvidence).where(ImportSceneEvidence.id == ev_id))  # DB cascade
        await s.commit()
        remaining = (
            await s.execute(
                select(func.count())
                .select_from(ImportSceneEvidenceChunk)
                .where(ImportSceneEvidenceChunk.evidence_id == ev_id)
            )
        ).scalar_one()
        assert remaining == 0
