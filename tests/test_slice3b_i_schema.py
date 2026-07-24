"""Schema oracles for ADR-0028 Slice 3b-i: the three adoption-engine columns and the ScenePacket →
Scene binding FK.

Two layers, mirroring the rest of the migration suite:
  * STATIC (no DB) — every one of the three (table, col) pairs is in the migration's added-columns set,
    so a deleted `ADD COLUMN` line is caught before it can boot green on a fresh create_all DB and throw
    UndefinedColumn against the persistent production Postgres (the forward-drift class).
  * DB round-trip / FK (needs Postgres; skips locally, runs under `just test` / CI) — the columns
    persist and read back intact, the NOT-VALID FK enforces new writes, and a NULL binding is allowed.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from dominion.shared.models import Book, Chapter, ChapterPacket, ImportAdoption, Scene, ScenePacket

# The migration's "every (table, col) a migration explicitly ADDs" set — the single source of truth the
# forward-drift guard already builds from `_COLUMN_ADDS` + `_EXTRA_DDL`. Reused verbatim so this guard
# and that one cannot disagree on what counts as "migrated".
from tests.test_migration_forward_drift import _migration_added_columns

# The three columns this slice adds to tables that ALREADY exist in prod (create_all won't ALTER them).
_SLICE3B_COLUMNS = {
    ("scene_packets", "source_scene_id"),
    ("import_adoptions", "seed_bindings"),
    ("import_adoptions", "author_input_fingerprint"),
}


def test_all_three_columns_are_migration_added():
    """STATIC (no DB): each of the three columns has an `ADD COLUMN` in migrations — guards against a
    deleted ALTER that would drift ahead of the persistent prod DB."""
    added = _migration_added_columns()
    missing = _SLICE3B_COLUMNS - added
    assert not missing, (
        "Slice 3b column(s) missing an `ALTER TABLE ... ADD COLUMN` in migrations._COLUMN_ADDS — "
        "they would boot green on a fresh create_all DB and throw UndefinedColumn against prod: "
        + ", ".join(f"{t}.{c}" for t, c in sorted(missing))
    )


async def _seed(s) -> tuple[Book, Chapter, ChapterPacket, Scene]:
    """A book → chapter → (approved) chapter packet → one imported scene chain, the minimal FK context an
    ImportAdoption and a ScenePacket both need."""
    book = Book(title="Slice 3b Schema")
    s.add(book)
    await s.flush()
    ch = Chapter(book_id=book.id, chapter_no=1, pov="Marcus")
    s.add(ch)
    await s.flush()
    cp = ChapterPacket(book_id=book.id, chapter_id=ch.id, status="approved", body={"scene_seeds": []})
    s.add(cp)
    await s.flush()
    scene = Scene(chapter_id=ch.id, scene_no=1, version=1, prose="Imported prose.", status="pending_review")
    s.add(scene)
    await s.flush()
    return book, ch, cp, scene


async def test_columns_round_trip_intact(db_factory):
    """DB round-trip: an ImportAdoption's seed_bindings/author_input_fingerprint and a ScenePacket's
    source_scene_id persist and read back unchanged in a fresh session."""
    async with db_factory() as s:
        book, ch, cp, scene = await _seed(s)
        seed_id = uuid.uuid4()
        seed_bindings = {str(seed_id): {"scene_no": 1, "scene_id": str(scene.id)}}
        adoption = ImportAdoption(
            book_id=book.id,
            chapter_id=ch.id,
            source_fingerprint="fp-source",
            liveness_basis="operator_independent",
            seed_bindings=seed_bindings,
            author_input_fingerprint="fp-author-input",
        )
        s.add(adoption)
        packet = ScenePacket(
            book_id=book.id,
            chapter_id=ch.id,
            chapter_packet_id=cp.id,
            scene_no=1,
            body={"scene_no": 1},
            source_scene_id=scene.id,
        )
        s.add(packet)
        await s.commit()
        adoption_id, packet_id, scene_id = adoption.id, packet.id, scene.id

    async with db_factory() as s2:
        got_adoption = await s2.get(ImportAdoption, adoption_id)
        assert got_adoption.seed_bindings == seed_bindings
        assert got_adoption.author_input_fingerprint == "fp-author-input"
        got_packet = await s2.get(ScenePacket, packet_id)
        assert got_packet.source_scene_id == scene_id  # a real uuid.UUID, bound to the imported scene


async def test_source_scene_fk_rejects_a_dangling_binding(db_factory):
    """The NOT-VALID FK (fk_scene_packets_source_scene) still enforces every NEW write: a
    source_scene_id that names no Scene row raises IntegrityError."""
    async with db_factory() as s:
        book, ch, cp, _scene = await _seed(s)
        await s.commit()

        s.add(
            ScenePacket(
                book_id=book.id,
                chapter_id=ch.id,
                chapter_packet_id=cp.id,
                scene_no=2,
                body={"scene_no": 2},
                source_scene_id=uuid.uuid4(),  # no such scene → FK violation
            )
        )
        with pytest.raises(IntegrityError):
            await s.commit()
        await s.rollback()


async def test_null_source_scene_commits(db_factory):
    """An ordinary (planning-path) packet leaves source_scene_id NULL — the FK tolerates NULL, so it
    commits fine."""
    async with db_factory() as s:
        book, ch, cp, _scene = await _seed(s)
        packet = ScenePacket(
            book_id=book.id,
            chapter_id=ch.id,
            chapter_packet_id=cp.id,
            scene_no=3,
            body={"scene_no": 3},
        )
        s.add(packet)
        await s.commit()
        assert packet.source_scene_id is None
