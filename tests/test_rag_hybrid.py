"""RAG upgrade: owner-file routing, incremental ingest with metadata + content hashing, and hybrid
retrieval with owner-file precedence. DB-backed (skips if Postgres unreachable)."""

from __future__ import annotations

from sqlalchemy import select

from dominion.shared.models import Book, CanonEntity
from dominion.workers.memory import canon_rag, owner_router
from dominion.workers.memory.retrieval import retrieve_hybrid

# --- owner router (pure) --------------------------------------------------------------------------


def test_owner_router_forces_relationship_docs_for_marcus_and_serra():
    r = owner_router.route("the duel between them", characters=["Marcus", "Serra"])
    assert "relationship_invariants.md" in r.doc_paths
    assert "relationship_invariants" in r.owner_topics


def test_owner_router_routes_roster_query_to_cast_index():
    r = owner_router.route("who appears in this scene")
    assert "cast_index.md" in r.doc_paths


def test_owner_router_silent_without_a_match():
    r = owner_router.route("a quiet walk", characters=["Marcus"])
    assert r.doc_paths == [] and r.owner_topics == []


# --- incremental ingest ---------------------------------------------------------------------------


async def _book(s) -> Book:
    b = Book(title="X")
    s.add(b)
    await s.flush()
    return b


async def test_ingest_stores_metadata_and_skips_unchanged(db_factory, tmp_path):
    root = tmp_path / "canon"
    root.mkdir()
    (root / "mechanics.md").write_text(
        "# Mechanics\n\nTiers and soul rules.\n\n## Tier\n\nTier is the spell strength axis.\n",
        encoding="utf-8",
    )
    async with db_factory() as s:
        book = await _book(s)
        first = await canon_rag.ingest_incremental(s, book_id=book.id, root=root)
        await s.commit()
        assert first["indexed"] >= 1 and first["skipped"] == 0
        rows = (await s.execute(select(CanonEntity).where(CanonEntity.book_id == book.id))).scalars().all()
        # owner metadata persisted from the filename
        assert any(r.owner_topic == "mechanics" and (r.source_priority or 0) > 0 for r in rows)
        assert all(r.content_hash and r.heading_path is not None for r in rows)

        # re-ingest unchanged content -> everything is skipped by content_hash, nothing re-indexed
        second = await canon_rag.ingest_incremental(s, book_id=book.id, root=root)
        await s.commit()
        assert second["indexed"] == 0 and second["skipped"] >= 1


async def test_ingest_tags_kind_by_folder(db_factory, tmp_path):
    root = tmp_path / "canon"
    (root / "characters" / "major").mkdir(parents=True)
    (root / "factions").mkdir()
    (root / "characters" / "major" / "mc.md").write_text("# MC\n\nThe protagonist dossier.\n", encoding="utf-8")
    (root / "factions" / "iron_vultures.md").write_text("# Iron Vultures\n\nA mercenary company.\n", encoding="utf-8")
    (root / "story_bible.md").write_text("# Bible\n\nRoot-level lore.\n", encoding="utf-8")
    async with db_factory() as s:
        book = await _book(s)
        await canon_rag.ingest_incremental(s, book_id=book.id, root=root)
        await s.commit()
        rows = (await s.execute(select(CanonEntity).where(CanonEntity.book_id == book.id))).scalars().all()
        kinds = {r.doc_path: r.kind for r in rows}
        assert kinds["characters/major/mc.md"] == "cast"  # not "character" (reserved for stat rows)
        assert kinds["factions/iron_vultures.md"] == "faction"
        assert kinds["story_bible.md"] == "lore"  # root-level fallback


async def test_ingest_leaves_hand_authored_entities_untouched(db_factory, tmp_path):
    root = tmp_path / "canon"
    (root / "factions").mkdir(parents=True)
    (root / "factions" / "nightbound.md").write_text("# Nightbound\n\nA cult.\n", encoding="utf-8")
    async with db_factory() as s:
        book = await _book(s)
        # a hand-authored entity (no doc_path) — must survive a rebuild
        hand = CanonEntity(book_id=book.id, kind="location", name="Eriadne", body="A city.")
        s.add(hand)
        await s.flush()
        await canon_rag.ingest_incremental(s, book_id=book.id, root=root)
        await s.commit()
        survivor = (
            (await s.execute(select(CanonEntity).where(CanonEntity.book_id == book.id, CanonEntity.doc_path.is_(None))))
            .scalars()
            .all()
        )
        assert [r.name for r in survivor] == ["Eriadne"]


async def test_ingest_retires_deleted_chunks(db_factory, tmp_path):
    root = tmp_path / "canon"
    root.mkdir()
    f = root / "lore.md"
    f.write_text("# A\n\nalpha content here.\n\n# B\n\nbeta content here.\n", encoding="utf-8")
    async with db_factory() as s:
        book = await _book(s)
        await canon_rag.ingest_incremental(s, book_id=book.id, root=root)
        await s.commit()
        # remove section B
        f.write_text("# A\n\nalpha content here.\n", encoding="utf-8")
        out = await canon_rag.ingest_incremental(s, book_id=book.id, root=root)
        await s.commit()
        assert out["retired"] >= 1
        bodies = [b for (b,) in (await s.execute(select(CanonEntity.body).where(CanonEntity.book_id == book.id))).all()]
        assert not any("beta" in (b or "") for b in bodies)


async def test_ingest_rebuild_purges_stale_same_path_content_and_preserves_hand_authored(db_factory, tmp_path):
    """Targeted repro: content change under same (doc_path, heading_path) must not leave stale rows.

    Uses the hard rebuild path (as invoked by the Ledger webpage button): delete all
    repo-ingested (doc_path IS NOT NULL), then re-ingest. Old bodies under same key gone.
    Hand-authored (doc_path IS NULL) survive.
    """
    from sqlalchemy import select

    root = tmp_path / "canon"
    root.mkdir(parents=True)
    f = root / "characters" / "hero.md"
    f.parent.mkdir()
    f.write_text("# Protagonist\n\nAyla was the chosen one.\n", encoding="utf-8")
    async with db_factory() as s:
        book = await _book(s)
        # hand-authored row (must survive)
        hand = CanonEntity(book_id=book.id, kind="lore", name="Note", body="Hand note about Illyri.")
        s.add(hand)
        await s.flush()

        # first ingest creates repo row with Ayla
        r1 = await canon_rag.ingest_rebuild(s, book_id=book.id, root=root)
        await s.commit()
        assert r1["indexed"] >= 1
        assert r1["retired"] >= 0

        # edit same doc+heading
        f.write_text("# Protagonist\n\nIllyri was the chosen one.\n", encoding="utf-8")

        # hard rebuild (as Ledger does)
        r2 = await canon_rag.ingest_rebuild(s, book_id=book.id, root=root)
        await s.commit()
        assert r2["retired"] >= 1  # at least the prior Ayla row

        rows = (await s.execute(select(CanonEntity).where(CanonEntity.book_id == book.id))).scalars().all()
        bodies = [r.body for r in rows]
        doc_rows = [r for r in rows if r.doc_path is not None]
        null_rows = [r for r in rows if r.doc_path is None]

        assert not any("Ayla" in (b or "") for b in bodies), "stale Ayla content must be purged"
        assert any("Illyri" in (b or "") for b in bodies)
        assert len(doc_rows) >= 1
        assert any("Illyri" in (b or "") for b in [r.body for r in doc_rows])
        assert len(null_rows) == 1 and "Hand note" in (null_rows[0].body or "")


# --- hybrid retrieval -----------------------------------------------------------------------------


async def test_hybrid_owner_forced_outranks_semantic(db_factory):
    async with db_factory() as s:
        book = await _book(s)
        # an owner file (high priority) + a generic passage that lexically matches the query better
        s.add(
            CanonEntity(
                book_id=book.id,
                kind="passage",
                name="relationship_invariants",
                body="Marcus and Serra share a guarded history.",
                embedding=canon_rag.embed("marcus serra"),
                doc_path="relationship_invariants.md",
                owner_topic="relationship_invariants",
                source_priority=100,
                content_hash="a",
            )
        )
        s.add(
            CanonEntity(
                book_id=book.id,
                kind="passage",
                name="misc",
                body="duel duel duel tactics and footwork",
                embedding=canon_rag.embed("duel tactics"),
                doc_path="misc.md",
                owner_topic=None,
                source_priority=0,
                content_hash="b",
            )
        )
        await s.flush()

        results = await retrieve_hybrid(
            s,
            book_id=book.id,
            query="the duel between Marcus and Serra",
            owner_topics=["relationship_invariants"],
            required_doc_paths=["relationship_invariants.md"],
        )
        assert results
        assert results[0]["retrieval_reason"] == "owner_forced"
        assert results[0]["doc_path"] == "relationship_invariants.md"


async def test_hybrid_forces_owner_doc_in_nested_folder(db_factory):
    """Owner rules name docs by bare filename ("mc.md") but ingest stores the folder-relative path
    ("characters/major/mc.md"); force-inclusion must still match across the folder."""
    async with db_factory() as s:
        book = await _book(s)
        s.add(
            CanonEntity(
                book_id=book.id,
                kind="cast",
                name="mc",
                body="The protagonist's dossier.",
                embedding=canon_rag.embed("protagonist"),
                doc_path="characters/major/mc.md",
                owner_topic="relationship_invariants",
                source_priority=100,
                content_hash="z",
            )
        )
        await s.flush()
        results = await retrieve_hybrid(
            s,
            book_id=book.id,
            query="who is the protagonist",
            required_doc_paths=["mc.md"],  # bare filename, as owner_router emits
        )
        assert results
        assert results[0]["retrieval_reason"] == "owner_forced"
        assert results[0]["doc_path"] == "characters/major/mc.md"


async def test_hybrid_dedupes_and_respects_forbidden_topic(db_factory):
    async with db_factory() as s:
        book = await _book(s)
        s.add(
            CanonEntity(
                book_id=book.id,
                kind="passage",
                name="secret",
                body="the hidden cohort plan",
                embedding=canon_rag.embed("hidden cohort"),
                doc_path="secret.md",
                owner_topic="forbidden_topic",
                source_priority=0,
                content_hash="c",
            )
        )
        await s.flush()
        results = await retrieve_hybrid(
            s, book_id=book.id, query="hidden cohort plan", forbidden_topics=["forbidden_topic"]
        )
        assert all(r["owner_topic"] != "forbidden_topic" for r in results)
