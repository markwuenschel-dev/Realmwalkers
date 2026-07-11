"""Manuscript ingest — parse dropped files into a chapter/scene preview.

Slice 1 is **parse-only and read-only**: `/parse` runs the best-effort splitter and reports the
detected structure plus which chapter numbers already exist in the book, so the uploader can flag
collisions. Nothing is written here — importing the preview into scenes is a later slice.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from dominion.api.deps import SessionDep
from dominion.shared.models import Book, Chapter
from dominion.shared.schemas import (
    ManuscriptParseIn,
    ParsedChapterOut,
    ParsedManuscriptOut,
    ParsedSceneOut,
)
from dominion.workers.memory.manuscript_split import split_files

router = APIRouter(prefix="/books", tags=["manuscript"])


@router.post("/{book_id}/manuscript/parse", response_model=ParsedManuscriptOut)
async def parse_manuscript(book_id: uuid.UUID, body: ManuscriptParseIn, session: SessionDep) -> ParsedManuscriptOut:
    """Split the dropped files into a chapter/scene preview and flag chapter numbers that already
    exist in this book. Pure read — no DB writes."""
    book = await session.get(Book, book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="book not found")
    if not body.files:
        raise HTTPException(status_code=400, detail="no files provided")

    parsed = split_files([(f.filename, f.text) for f in body.files])

    existing = set(
        (await session.execute(select(Chapter.chapter_no).where(Chapter.book_id == book_id))).scalars().all()
    )

    return ParsedManuscriptOut(
        warnings=parsed.warnings,
        existing_chapter_nos=sorted(existing),
        chapters=[
            ParsedChapterOut(
                chapter_no=c.chapter_no,
                title=c.title,
                detected=c.detected,
                conflict=c.chapter_no in existing,
                warnings=c.warnings,
                scenes=[ParsedSceneOut(scene_no=s.scene_no, prose=s.prose, word_count=s.word_count) for s in c.scenes],
            )
            for c in parsed.chapters
        ],
    )
