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
from typing import Any

import structlog
from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from dominion.shared.config import settings
from dominion.shared.db import SessionFactory
from dominion.shared.models import Book, CanonEntity
from dominion.workers.memory.embedding import embed_async, embed_many_async, embedding_version
from dominion.workers.memory.owner_router import _RULES

log = structlog.get_logger()

_PASSAGE_KIND = "passage"
_TARGET_CHARS = 1000


def _active_only():
    """Status-aware retrieval gate (Workstream H): only `active` canon reaches agent/prose context;
    stale/retired/superseded rows are excluded. NULL is treated as active so legacy rows written
    before the `status` column existed still surface."""
    return or_(CanonEntity.status.is_(None), CanonEntity.status == "active")


# Top-level folder under series/canon → the CanonEntity.kind ingested chunks get tagged with, so the
# ledger groups them into real categories instead of one "passage" pile. `kind` is display/organization
# only — retrieval (retrieve/retrieve_hybrid) never filters on it. Character docs route to "cast" (a
# browsable category) rather than "character", which is reserved for the hand-authored stat-description
# rows the Characters tab resolves by name. Unmapped folders / root-level docs fall back to "lore".
_KIND_BY_FOLDER: dict[str, str] = {
    "characters": "cast",
    "factions": "faction",
    "locations": "location",
    "litrpg_system": "system",
    "world": "lore",
    "continuity": "continuity",
}
_DEFAULT_INGEST_KIND = "lore"

# Top-level folders we've already warned about falling back for, so a rebuild that walks many files
# under the same unmapped folder logs the warning ONCE per folder instead of once per file (mirrors
# embedding.py's `_warned_no_key` dedupe). Process-lifetime state; a warning, never a gate.
_warned_unmapped_folders: set[str] = set()

# Extensions we treat as canon source text.
_INGEST_SUFFIXES = {".md", ".markdown", ".txt"}


def _is_ingestable(path: Path) -> bool:
    """True if `path` is a canon source file we should embed.

    Skips non-prose scaffolding that would otherwise be embedded and surface as retrievable
    "canon": underscore-prefixed templates (``_CHARACTER_TEMPLATE.md``, ``_FACTION_TEMPLATE.md``,
    ``_COHORT_TEMPLATE.md``) and the ``CHANGELOG``. Mirrors seed.py's convention so a rebuild
    never pollutes context with template placeholders. Real index files (canon_index.md) are kept.
    """
    if not path.is_file() or path.suffix.lower() not in _INGEST_SUFFIXES:
        return False
    if path.name.startswith("_"):
        return False
    if path.stem.upper() == "CHANGELOG":
        return False
    return True


def _kind_for(doc_path: str) -> str:
    """Map a chunk's relative doc_path to its display kind by top-level folder.

    A folder not in `_KIND_BY_FOLDER` (a new or renamed canon folder, or a root-level doc) falls back
    to `_DEFAULT_INGEST_KIND`. That fallback is intentionally NON-fatal so an evolving corpus keeps
    ingesting — but it can silently MIS-categorize an entire new folder as "lore", so we emit a loud,
    structured WARNING naming the unmapped folder and the kind it fell back to (deduped to once per
    folder per process). Fix by adding the folder to `_KIND_BY_FOLDER`.

    NOTE: a strict mode that HARD-FAILS on an unmapped folder was deliberately not enabled — it's a
    human/migration judgment call (it could break an existing corpus mid-rebuild). It could be layered
    on later behind a setting (e.g. `settings.canon_ingest_strict_kinds`), defaulting off.
    """
    top = doc_path.split("/", 1)[0] if "/" in doc_path else ""
    kind = _KIND_BY_FOLDER.get(top)
    if kind is not None:
        return kind
    if top not in _warned_unmapped_folders:
        _warned_unmapped_folders.add(top)
        log.warning(
            "canon_ingest.unmapped_folder",
            folder=top or "<root>",
            fell_back_to=_DEFAULT_INGEST_KIND,
            known_folders=sorted(_KIND_BY_FOLDER),
            hint="add this folder to canon_rag._KIND_BY_FOLDER to tag it with a real kind",
        )
    return _DEFAULT_INGEST_KIND


# Filename → owner topic / source priority. Owner files (relationship invariants, cast, mechanics, …)
# get a high source_priority so the reranker keeps them above generic passages. Built from the owner
# routing rules so the two never drift.
_OWNER_BY_FILE: dict[str, str] = {path: rule.owner_topic for rule in _RULES for path in rule.doc_paths}
_OWNER_PRIORITY = 100


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
    qvec = await embed_async(query)
    stmt = (
        select(CanonEntity.body)
        .where(
            CanonEntity.book_id == book_id,
            CanonEntity.body.isnot(None),
            CanonEntity.embedding.isnot(None),
            _active_only(),
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
    qvec = await embed_async(query)
    stmt = (
        select(CanonEntity.id, CanonEntity.name, CanonEntity.body)
        .where(
            CanonEntity.book_id == book_id,
            CanonEntity.body.isnot(None),
            CanonEntity.embedding.isnot(None),
            _active_only(),
        )
        .order_by(CanonEntity.embedding.cosine_distance(qvec))
        .limit(k)
    )
    return [{"id": cid, "name": name, "body": body} for cid, name, body in (await session.execute(stmt)).all() if body]


async def ingest_path(session: AsyncSession, *, book_id: uuid.UUID, root: str | Path, kind: str = _PASSAGE_KIND) -> int:
    """Rebuild canon passages for a book from .md/.txt files under root. Returns chunks indexed."""
    await session.execute(delete(CanonEntity).where(CanonEntity.book_id == book_id, CanonEntity.kind == kind))
    count = 0
    for path in sorted(Path(root).rglob("*")):
        if not _is_ingestable(path):
            continue
        for chunk in _chunk(path.read_text(encoding="utf-8")):
            session.add(
                CanonEntity(
                    book_id=book_id,
                    kind=kind,
                    name=path.stem,
                    body=chunk,
                    embedding=await embed_async(chunk),
                    source="repo_ingested",
                    status="active",
                )
            )
            count += 1
    await session.flush()
    return count


async def ingest_incremental(
    session: AsyncSession, *, book_id: uuid.UUID, root: str | Path, kind: str | None = None
) -> dict[str, int]:
    """Incrementally (re)build canon chunks with provenance + content hashing (RAG upgrade).

    Walks .md/.txt under root, chunks by heading, computes a content_hash per chunk, and:
      * tags each chunk's `kind` from its top-level folder (_kind_for) so the ledger groups them —
        unless `kind` is given, which forces a single kind (back-compat / a single-purpose corpus);
      * skips a chunk whose (doc_path, heading_path, content_hash, embedding_version) already exists;
      * embeds + inserts changed/new chunks with doc_path/heading_path/owner_topic/source_priority;
      * retires chunks whose (doc_path, heading_path) no longer appears on disk.

    Ingested rows are identified by a non-null `doc_path`, so a rebuild replaces/retires only
    previously-ingested chunks and NEVER touches hand-authored canon entities (which have doc_path
    NULL) — even now that ingested chunks carry real kinds like "faction"/"location".
    Returns {indexed, skipped, retired}. The caller commits.
    """
    root = Path(root)
    existing_rows = list(
        (
            await session.execute(
                select(CanonEntity).where(CanonEntity.book_id == book_id, CanonEntity.doc_path.isnot(None))
            )
        ).scalars()
    )
    existing_by_key = {(r.doc_path, r.heading_path, r.content_hash): r for r in existing_rows}
    kept_ids: set[uuid.UUID] = set()
    skipped = 0
    version = embedding_version()  # encodes the active embedding backend + model

    # Phase 1: walk the docs and split each chunk into "kept as-is" vs "needs (re)embedding". A chunk is
    # kept only when its content AND the embedding backend are unchanged; a content edit or a provider
    # switch (which changes `version`) makes it pending.
    pending: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if not _is_ingestable(path):
            continue
        doc_path = str(path.relative_to(root)).replace("\\", "/")
        row_kind = kind or _kind_for(doc_path)
        owner_topic, priority = _owner_meta(path)
        for heading_path, chunk in _chunk_by_heading(path.read_text(encoding="utf-8")):
            chash = _content_hash(chunk)
            row = existing_by_key.get((doc_path, heading_path, chash))
            if row is not None and row.embedding_version == version:
                kept_ids.add(row.id)
                skipped += 1
                continue
            pending.append(
                {
                    "doc_path": doc_path,
                    "heading_path": heading_path or None,
                    "chunk": chunk,
                    "chash": chash,
                    "kind": row_kind,
                    "name": path.stem,
                    "owner_topic": owner_topic,
                    "priority": priority,
                }
            )

    # Phase 2: embed ALL pending chunks in one batched pass (chunked internally), then insert. This is
    # the speedup that keeps a full re-index from timing out (was one blocking embed per chunk).
    vectors = await embed_many_async([p["chunk"] for p in pending])
    for p, vec in zip(pending, vectors, strict=True):
        session.add(
            CanonEntity(
                book_id=book_id,
                kind=p["kind"],
                name=p["name"],
                body=p["chunk"],
                embedding=vec,
                source="repo_ingested",
                status="active",
                doc_path=p["doc_path"],
                heading_path=p["heading_path"],
                owner_topic=p["owner_topic"],
                source_priority=p["priority"],
                content_hash=p["chash"],
                embedding_model=settings.embedding_model,
                embedding_version=version,
            )
        )
    indexed = len(pending)

    # Phase 3: retire every prior repo row we did NOT keep — vanished docs, changed content, AND
    # stale-version rows we just re-embedded. Retiring by ROW IDENTITY (not by doc/heading) is what stops
    # a content edit from leaving the old chunk behind as a duplicate.
    retired = 0
    for row in existing_rows:
        if row.id not in kept_ids:
            await session.delete(row)
            retired += 1

    await session.flush()
    return {"indexed": indexed, "skipped": skipped, "retired": retired}


async def ingest_rebuild(
    session: AsyncSession, *, book_id: uuid.UUID, root: str | Path, kind: str | None = None
) -> dict[str, int]:
    """Hard clean rebuild of repo-ingested canon chunks for a book from on-disk docs.

    Deletes *all* doc/seed-derived rows for the book first, then delegates to
    ingest_incremental to (re)index current files under root (folder-derived kinds,
    heading paths, content hashes, owner metadata, etc.).

    The purge targets any row that is NOT hand-authored canon:
      - doc_path IS NOT NULL      — chunks a prior doc-ingest wrote, and
      - source == 'repo_ingested' — repo-sourced rows regardless of doc_path, and
      - kind == 'passage'         — the legacy seed catch-all kind (ingest_path/seed
                                    wrote these with doc_path NULL, so the old
                                    doc_path-only predicate could never clean them —
                                    the "everything stuck under passage" defect).
    Hand-authored canon (source 'manual', doc_path NULL, a real kind like 'cast')
    is preserved. After the delete, ingest_incremental re-indexes every doc fresh
    (nothing left to skip), so the corpus comes back with real folder-derived kinds.

    Returns {indexed, skipped, retired} where retired includes the count of rows
    purged in the initial delete + any additional retired by the incremental pass.
    The caller commits.
    """
    root = Path(root)
    del_res = await session.execute(
        delete(CanonEntity).where(
            CanonEntity.book_id == book_id,
            or_(
                CanonEntity.doc_path.isnot(None),
                CanonEntity.source == "repo_ingested",
                CanonEntity.kind == _PASSAGE_KIND,
            ),
        )
    )
    deleted = int(getattr(del_res, "rowcount", 0) or 0)

    inc = await ingest_incremental(session, book_id=book_id, root=root, kind=kind)
    retired = deleted + inc.get("retired", 0)
    return {"indexed": inc["indexed"], "skipped": inc["skipped"], "retired": retired}


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
    parser.add_argument("--path", default=settings.canon_dir)
    args = parser.parse_args()
    asyncio.run(_build(args.book, args.path))


if __name__ == "__main__":
    main()
