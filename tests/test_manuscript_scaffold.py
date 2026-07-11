"""Production-skeleton scaffold: creates the standard front/back-matter + prologue/epilogue slots in
canonical reading order, idempotently. Router function called directly with a session (see conftest)."""

from __future__ import annotations

from fastapi import BackgroundTasks
from sqlalchemy import select

from dominion.api.routers import manuscript
from dominion.shared.models import Book, Chapter
from dominion.shared.schemas import ManuscriptImportChapterIn, ManuscriptImportIn, ManuscriptImportSceneIn


async def _book(s):
    book = Book(title="X")
    s.add(book)
    await s.flush()
    return book


async def test_scaffold_creates_skeleton_in_canonical_order(db_factory):
    async with db_factory() as s:
        book = await _book(s)
        report = await manuscript.scaffold_production(book.id, s)
        assert report.skipped == []
        # Every authored production slot is created (generated pages — title/copyright page, TOC — are not).
        assert "Copyright" in report.created and "Author Bio" in report.created

        chapters = (
            (await s.execute(select(Chapter).where(Chapter.book_id == book.id).order_by(Chapter.position)))
            .scalars()
            .all()
        )
        seq = [(c.kind, c.section_type) for c in chapters]
        # Front matter (in publishing order) → prologue → epilogue → back matter (in order).
        assert seq == [
            ("front_matter", "copyright"),
            ("front_matter", "dedication"),
            ("front_matter", "preface"),
            ("prologue", None),
            ("epilogue", None),
            ("back_matter", "afterword"),
            ("back_matter", "acknowledgments"),
            ("back_matter", "appendix"),
            ("back_matter", "glossary"),
            ("back_matter", "author_bio"),
        ]
        assert all(c.chapter_no is None for c in chapters)  # every scaffolded slot is numberless


async def test_scaffold_is_idempotent(db_factory):
    async with db_factory() as s:
        book = await _book(s)
        await manuscript.scaffold_production(book.id, s)
        report2 = await manuscript.scaffold_production(book.id, s)
        assert report2.created == []  # second run adds nothing
        assert "Copyright" in report2.skipped
        count = len(
            (await s.execute(select(Chapter).where(Chapter.book_id == book.id))).scalars().all()
        )
        assert count == 10  # no duplicates


async def test_scaffold_slots_sort_around_existing_body_chapters(db_factory):
    """A prologue slot leads the body and an epilogue trails it, even with real chapters already imported."""
    async with db_factory() as s:
        book = await _book(s)
        await manuscript.import_manuscript(
            book.id,
            ManuscriptImportIn(
                chapters=[
                    ManuscriptImportChapterIn(
                        chapter_no=n, scenes=[ManuscriptImportSceneIn(scene_no=1, prose=f"ch{n}")]
                    )
                    for n in (1, 2)
                ]
            ),
            s,
            BackgroundTasks(),
        )
        await manuscript.scaffold_production(book.id, s)
        chapters = (
            (await s.execute(select(Chapter).where(Chapter.book_id == book.id).order_by(Chapter.position)))
            .scalars()
            .all()
        )
        kinds = [c.kind for c in chapters]
        # front matter first, prologue before chapter 1, epilogue after the last chapter, back matter last.
        assert kinds.index("prologue") < kinds.index("chapter")
        assert kinds.index("chapter") < kinds.index("epilogue")
        assert kinds.index("epilogue") < kinds.index("back_matter")
        assert kinds[0] == "front_matter"