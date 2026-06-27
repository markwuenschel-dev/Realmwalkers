"""Semantic canon retrieval over pgvector (DESIGN §7).

Stores canon passages as CanonEntity rows (body + embedding) and returns the beat-scoped top-k by
cosine distance. `ingest_path` (re)builds the index from text/markdown under series/canon.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import re
import uuid
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from dominion.shared.config import settings
from dominion.shared.db import SessionFactory
from dominion.shared.models import Book, CanonEntity
from dominion.workers.memory.embedding import embed
from dominion.workers.memory.owner_router import _RULES

_PASSAGE_KIND = "passage"
_TARGET_CHARS = 1000

# Filename → owner topic / source priority. Owner files (relationship invariants, cast, mechanics, …)
# get a high source_priority so the reranker keeps them above generic passages. Built from the owner
# routing rules so the two never drift.
_OWNER_BY_FILE: dict[str, str] = {
    path: rule.owner_topic for rule in _RULES for path in rule.doc_paths
}
_OWNER_PRIORITY = 100
_EMBEDDING_VERSION = "v1"  # bump when the embed() implementation changes, to force a re-embed


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")


def _chunk_by_heading(text: str) -> list[tuple[str, str]]:
    """Split markdown into (heading_path, chunk_text), preserving the heading trail. Long sections are
    further split to ~target size; the heading path is repeated so each chunk keeps its provenance."""
    sections: list[tuple[str, str]] = []
    stack: list[tuple[int, str]] = []
    buf: list[str] = []
    heading_path = ""

    def flush() -> None:
        nonlocal buf
        body = "\n".join(buf).strip()
        if body:
            for piece in _chunk(body):
                sections.append((heading_path, piece))
        buf = []

    for line in text.splitlines():
        m = _HEADING_RE.match(line)
        if m:
            flush()
            level = len(m.group(1))
            title = m.group(2).strip()
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
            heading_path = " > ".join(t for _, t in stack)
        else:
            buf.append(line)
    flush()
    return sections


def _owner_meta(path: Path) -> tuple[str | None, int]:
    """(owner_topic, source_priority) for a file, from its name."""
    topic = _OWNER_BY_FILE.get(path.name)
    return topic, (_OWNER_PRIORITY if topic else 0)


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


async def retrieve_with_meta(
    session: AsyncSession, *, book_id: uuid.UUID, query: str, k: int = 6
) -> list[dict[str, object]]:
    """Like `retrieve`, but returns `{id, name, body}` per snippet so callers can attribute claims to
    their source (provenance). Used by the Packet Author — every packet claim must trace to a real
    canon row, not just an unsourced model assertion (DESIGN: contract-first drafting)."""
    if not query.strip():
        return []
    qvec = embed(query)
    stmt = (
        select(CanonEntity.id, CanonEntity.name, CanonEntity.body)
        .where(
            CanonEntity.book_id == book_id,
            CanonEntity.body.isnot(None),
            CanonEntity.embedding.isnot(None),
        )
        .order_by(CanonEntity.embedding.cosine_distance(qvec))
        .limit(k)
    )
    return [
        {"id": cid, "name": name, "body": body}
        for cid, name, body in (await session.execute(stmt)).all()
        if body
    ]


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


async def ingest_incremental(
    session: AsyncSession, *, book_id: uuid.UUID, root: str | Path, kind: str = _PASSAGE_KIND
) -> dict[str, int]:
    """Incrementally (re)build canon chunks with provenance + content hashing (RAG upgrade).

    Walks .md/.txt under root, chunks by heading, computes a content_hash per chunk, and:
      * skips a chunk whose (doc_path, heading_path, content_hash, embedding_version) already exists;
      * embeds + inserts changed/new chunks with doc_path/heading_path/owner_topic/source_priority;
      * retires chunks whose (doc_path, heading_path) no longer appears on disk.
    Returns {indexed, skipped, retired}. The caller commits.
    """
    root = Path(root)
    existing = {
        (r.doc_path, r.heading_path, r.content_hash): r
        for r in (await session.execute(
            select(CanonEntity).where(CanonEntity.book_id == book_id, CanonEntity.kind == kind)
        )).scalars()
    }
    seen_keys: set[tuple[str | None, str | None]] = set()
    indexed = skipped = 0

    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {".md", ".markdown", ".txt"}:
            continue
        doc_path = str(path.relative_to(root)).replace("\\", "/")
        owner_topic, priority = _owner_meta(path)
        for heading_path, chunk in _chunk_by_heading(path.read_text(encoding="utf-8")):
            chash = _content_hash(chunk)
            seen_keys.add((doc_path, heading_path))
            row = existing.get((doc_path, heading_path, chash))
            if row is not None and row.embedding_version == _EMBEDDING_VERSION:
                skipped += 1
                continue
            session.add(CanonEntity(
                book_id=book_id, kind=kind, name=path.stem, body=chunk, embedding=embed(chunk),
                doc_path=doc_path, heading_path=heading_path or None, owner_topic=owner_topic,
                source_priority=priority, content_hash=chash,
                embedding_model=settings.embedding_model, embedding_version=_EMBEDDING_VERSION,
            ))
            indexed += 1

    # retire chunks whose (doc_path, heading_path) vanished from disk
    retired = 0
    for (doc_path, heading_path, _chash), row in existing.items():
        if (doc_path, heading_path) not in seen_keys:
            await session.delete(row)
            retired += 1

    await session.flush()
    return {"indexed": indexed, "skipped": skipped, "retired": retired}


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
