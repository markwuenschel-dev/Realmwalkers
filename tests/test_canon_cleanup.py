"""Workstream H: canon `source`/`status` provenance, status-aware retrieval, and the cleanup API.

DB-backed (skips if Postgres unreachable, like the rest of tests/). Router functions are called
directly (mirrors tests/test_desk_api.py); retrieval is exercised through the real query path so the
"retired canon never enters agent context" guarantee is tested end-to-end, not just at the column.
"""

from __future__ import annotations

from sqlalchemy import select

from dominion.api.routers import world as world_router
from dominion.shared.models import Book, CanonEntity
from dominion.shared.schemas import CanonCleanupIn
from dominion.workers.memory import canon_rag
from dominion.workers.memory.embedding import embed
from dominion.workers.memory.retrieval import retrieve_hybrid


async def _book(s) -> Book:
    b = Book(title="X")
    s.add(b)
    await s.flush()
    return b


def _canon(
    book_id, *, name: str, body: str, status: str = "active", source: str = "repo_ingested", **kw
) -> CanonEntity:
    """A retrievable canon row (body + embedding), with explicit provenance/lifecycle."""
    return CanonEntity(
        book_id=book_id,
        kind="lore",
        name=name,
        body=body,
        embedding=embed(body),
        status=status,
        source=source,
        **kw,
    )


# --- ingestion stamps provenance ------------------------------------------------------------------


async def test_ingest_incremental_stamps_provenance(db_factory, tmp_path):
    root = tmp_path / "canon"
    root.mkdir()
    (root / "lore.md").write_text("# A\n\nalpha content here.\n", encoding="utf-8")
    async with db_factory() as s:
        book = await _book(s)
        await canon_rag.ingest_incremental(s, book_id=book.id, root=root)
        await s.commit()
        rows = (await s.execute(select(CanonEntity).where(CanonEntity.book_id == book.id))).scalars().all()
        assert rows and all(r.source == "repo_ingested" and r.status == "active" for r in rows)


# --- status-aware retrieval (the key behavior) ----------------------------------------------------


async def test_retrieve_and_meta_exclude_retired_rows(db_factory):
    q = "warded glass towers of Eriadne guarding the harbor"
    async with db_factory() as s:
        book = await _book(s)
        s.add(_canon(book.id, name="live", body="Eriadne is a warded city of glass towers guarding the harbor."))
        s.add(
            _canon(
                book.id,
                name="dead",
                body="Eriadne is a warded city of glass towers guarding the harbor SENTINEL.",
                status="retired",
            )
        )
        await s.flush()

        hits = await canon_rag.retrieve(s, book_id=book.id, query=q, k=6)
        assert any("glass towers" in h for h in hits)  # the active row is returned
        assert not any("SENTINEL" in h for h in hits)  # the retired row is excluded

        meta = await canon_rag.retrieve_with_meta(s, book_id=book.id, query=q, k=6)
        names = {m["name"] for m in meta}
        assert "live" in names and "dead" not in names


async def test_retrieve_hybrid_excludes_retired_rows(db_factory):
    async with db_factory() as s:
        book = await _book(s)
        s.add(_canon(book.id, name="live", body="the hidden cohort plan alpha", doc_path="a.md", content_hash="a"))
        s.add(
            _canon(
                book.id,
                name="dead",
                body="the hidden cohort plan SENTINEL",
                status="retired",
                doc_path="b.md",
                content_hash="b",
            )
        )
        await s.flush()
        results = await retrieve_hybrid(s, book_id=book.id, query="hidden cohort plan")
        assert results  # the active row still surfaces (keyword + semantic)
        assert all("SENTINEL" not in r["body"] for r in results)


# --- retire (soft) ---------------------------------------------------------------------------------


async def test_retire_removes_row_from_retrieval_and_active_list(db_factory):
    async with db_factory() as s:
        book = await _book(s)
        row = _canon(book.id, name="doomed", body="the sunken vault of Kessar", source="repo_ingested")
        s.add(row)
        await s.flush()
        rid = row.id

        assert await canon_rag.retrieve(s, book_id=book.id, query="sunken vault Kessar")  # present before
        assert any(e.id == rid for e in await world_router.list_canon(book.id, s))  # default status=active

        out = await world_router.retire_canon(book.id, CanonCleanupIn(ids=[rid]), s)
        assert out.retired == 1 and out.protected_manual == 0

        assert not await canon_rag.retrieve(s, book_id=book.id, query="sunken vault Kessar")  # gone from RAG
        assert all(e.id != rid for e in await world_router.list_canon(book.id, s))  # gone from ?status=active
        retired = await world_router.list_canon(book.id, s, status="retired")
        assert any(e.id == rid for e in retired)  # still visible under ?status=retired


async def test_retire_protects_manual_under_filter_but_honors_explicit_id(db_factory):
    async with db_factory() as s:
        book = await _book(s)
        manual = _canon(book.id, name="hand", body="hand authored note", source="manual")
        repo = _canon(
            book.id, name="repo", body="repo ingested chunk", source="repo_ingested", doc_path="r.md", content_hash="r"
        )
        s.add(manual)
        s.add(repo)
        await s.flush()

        # a status filter retires the repo row but PROTECTS the manual row (no id named)
        out = await world_router.retire_canon(book.id, CanonCleanupIn(status_filter="active"), s)
        assert out.retired == 1 and out.protected_manual == 1
        await s.refresh(manual)
        await s.refresh(repo)
        assert manual.status == "active" and repo.status == "retired"

        # naming the manual id explicitly overrides the protection
        out2 = await world_router.retire_canon(book.id, CanonCleanupIn(ids=[manual.id]), s)
        assert out2.retired == 1 and out2.protected_manual == 0
        await s.refresh(manual)
        assert manual.status == "retired"


# --- cleanup-preview (dry run) ---------------------------------------------------------------------


async def test_cleanup_preview_counts_protects_and_does_not_mutate(db_factory):
    async with db_factory() as s:
        book = await _book(s)
        manual = _canon(book.id, name="hand", body="hand note", source="manual")
        repo = _canon(book.id, name="r1", body="repo one", source="repo_ingested", doc_path="1.md", content_hash="1")
        s.add(manual)
        s.add(repo)
        await s.flush()

        prev = await world_router.cleanup_preview(book.id, CanonCleanupIn(status_filter="active"), s)
        assert prev.dry_run is True
        assert prev.matched == 2
        assert prev.protected_manual == 1
        assert prev.would_retire == 1 and prev.would_delete == 1
        reasons = {i.name: i.reason for i in prev.items}
        assert "protected" in reasons["hand"] and reasons["r1"] == "eligible"

        # a dry run mutates nothing: both rows survive, unchanged
        await s.refresh(manual)
        await s.refresh(repo)
        assert manual.status == "active" and repo.status == "active"
        rows = (await s.execute(select(CanonEntity).where(CanonEntity.book_id == book.id))).scalars().all()
        assert len(rows) == 2


# --- bulk delete (hard) ----------------------------------------------------------------------------


async def test_bulk_delete_protects_manual_and_removes_the_rest(db_factory):
    async with db_factory() as s:
        book = await _book(s)
        manual = _canon(book.id, name="hand", body="hand note", source="manual")
        repo = _canon(book.id, name="r1", body="repo one", source="repo_ingested", doc_path="1.md", content_hash="1")
        s.add(manual)
        s.add(repo)
        await s.flush()

        out = await world_router.bulk_delete_canon(book.id, CanonCleanupIn(status_filter="active"), s)
        assert out.deleted == 1 and out.protected_manual == 1
        remaining = (await s.execute(select(CanonEntity).where(CanonEntity.book_id == book.id))).scalars().all()
        assert {r.name for r in remaining} == {"hand"}


# --- rebuild ---------------------------------------------------------------------------------------


async def test_rebuild_preserves_manual_rows(db_factory):
    async with db_factory() as s:
        book = await _book(s)
        manual = CanonEntity(book_id=book.id, kind="lore", name="hand", body="hand authored canon", source="manual")
        s.add(manual)
        await s.flush()
        mid = manual.id

        # The rebuild endpoint is now an async scheduler (returns 202, work runs in a background task);
        # exercise the work it schedules directly so this stays a synchronous assertion.
        await canon_rag.ingest_incremental(s, book_id=book.id, root=world_router._PROJECT_ROOT / "series" / "canon")

        survivor = await s.get(CanonEntity, mid)
        assert survivor is not None and survivor.source == "manual" and survivor.status == "active"
        # any repo-ingested rows the rebuild produced carry the right provenance
        repo_rows = (
            (
                await s.execute(
                    select(CanonEntity).where(CanonEntity.book_id == book.id, CanonEntity.doc_path.isnot(None))
                )
            )
            .scalars()
            .all()
        )
        assert all(r.source == "repo_ingested" and r.status == "active" for r in repo_rows)
