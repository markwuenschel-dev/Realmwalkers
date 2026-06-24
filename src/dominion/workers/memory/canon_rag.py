"""Semantic canon retrieval over pgvector (DESIGN §7).

Stores canon passages as CanonEntity rows (body + embedding) and returns the beat-scoped top-k by
cosine distance. `ingest_path` (re)builds the index from text/markdown under series/canon.
"""
from __future__ import annotations

import argparse
import asyncio
import uuid
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from dominion.shared.db import SessionFactory
from dominion.shared.models import Book, CanonEntity
from dominion.workers.memory.embedding import embed

_PASSAGE_KIND = "passage"
_TARGET_CHARS = 1000


def _chunk(text: str) -> list[str]:
    """Group paragraphs into ~1000-char chunks so retrieval units aren't trivially small."""
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: list[str] = []
    buf = ""
    for para in paras:
        if buf and len(buf) + len(para) + 2 > _TARGET_CHARS:
            chunks.append(buf)
            buf = para
        else:
            buf = f"{buf}\n\n{para}" if buf else para
    if buf:
        chunks.append(buf)
    return chunks


async def retrieve(session: AsyncSession, *, book_id: uuid.UUID, query: str, k: int = 6) -> list[str]:
    """Beat-scoped top-k canon snippets by cosine distance. Empty corpus/query -> []."""
    if not query.strip():
        return []
    qvec = embed(query)
    stmt = (
        select(CanonEntity.body)
        .where(
            CanonEntity.book_id == book_id,
            CanonEntity.body.isnot(None),
            CanonEntity.embedding.isnot(None),
        )
        .order_by(CanonEntity.embedding.cosine_distance(qvec))
        .limit(k)
    )
    return [body for body in (await session.execute(stmt)).scalars().all() if body]


async def ingest_path(
    session: AsyncSession, *, book_id: uuid.UUID, root: str | Path, kind: str = _PASSAGE_KIND
) -> int:
    """Rebuild canon passages for a book from .md/.txt files under root. Returns chunks indexed."""
    await session.execute(
        delete(CanonEntity).where(CanonEntity.book_id == book_id, CanonEntity.kind == kind)
    )
    count = 0
    for path in sorted(Path(root).rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {".md", ".markdown", ".txt"}:
            continue
        for chunk in _chunk(path.read_text(encoding="utf-8")):
            session.add(CanonEntity(
                book_id=book_id, kind=kind, name=path.stem, body=chunk, embedding=embed(chunk)
            ))
            count += 1
    await session.flush()
    return count


async def _build(book_title: str, root: str) -> None:
    async with SessionFactory() as session:
        book = (await session.execute(select(Book).where(Book.title == book_title))).scalar_one_or_none()
        if book is None:
            raise SystemExit(f"no book titled {book_title!r} yet — enqueue a scene first to create it")
        n = await ingest_path(session, book_id=book.id, root=root)
        await session.commit()
        print(f"indexed {n} canon passages from {root!r} for '{book_title}'")


def main() -> None:
    parser = argparse.ArgumentParser(description="(Re)build the canon RAG index from text/markdown files.")
    parser.add_argument("--book", required=True)
    parser.add_argument("--path", default="series/canon")
    args = parser.parse_args()
    asyncio.run(_build(args.book, args.path))


if __name__ == "__main__":
    main()
