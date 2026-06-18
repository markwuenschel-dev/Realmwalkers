"""Upsert a per-POV voice spec into PovProfile — the field the drafter reads but nothing wrote.

The drafter injects `PovProfile.voice_spec` into its system prompt per narrating character
(`specialists/drafter.py: _voice_system`), looked up by `(book_id, character)` where `character`
must equal the chapter's `pov`, case-sensitive (`context.py`). This CLI is the authoring path for
that field: write the spec in a file, then point this at it. Mirrors the `enqueue` tool — find-or-
create the book by title, idempotent upsert (re-running with new text updates the same row, never a
second one). Scope is `voice_spec` only; exemplars are left untouched.

Runnable as:
    uv run python -m dominion.workers.set_voice --book "..." --character Soren --voice-file novel/voice/soren.md
"""
from __future__ import annotations

import argparse
import asyncio
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dominion.shared.db import SessionFactory
from dominion.shared.models import Book, PovProfile


async def set_voice(
    session: AsyncSession,
    *,
    book_title: str,
    character: str,
    voice_spec: str,
) -> uuid.UUID:
    """Resolve (or create) the book by title and upsert one PovProfile per (book_id, character).

    Sets only `voice_spec` (what the drafter reads); leaves exemplars alone. Flushes and returns
    the profile id. The caller owns the commit (so this stays unit-testable on any session).
    """
    book = (await session.execute(select(Book).where(Book.title == book_title))).scalar_one_or_none()
    if book is None:
        book = Book(title=book_title)
        session.add(book)
        await session.flush()

    profile = (await session.execute(
        select(PovProfile).where(PovProfile.book_id == book.id, PovProfile.character == character)
    )).scalar_one_or_none()
    if profile is None:
        profile = PovProfile(book_id=book.id, character=character, voice_spec=voice_spec)
        session.add(profile)
    else:
        profile.voice_spec = voice_spec
    await session.flush()
    return profile.id


async def _run(args: argparse.Namespace) -> None:
    voice_spec: str
    if args.voice_file:
        voice_spec = Path(args.voice_file).read_text(encoding="utf-8").strip()
    else:
        voice_spec = args.voice_text

    async with SessionFactory() as session:
        profile_id = await set_voice(
            session, book_title=args.book, character=args.character, voice_spec=voice_spec
        )
        await session.commit()

    print(
        f"voice spec set for character='{args.character}' in book='{args.book}' "
        f"(profile {profile_id}, {len(voice_spec)} chars)"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Upsert a per-POV voice spec (read by the drafter) into PovProfile."
    )
    parser.add_argument("--book", required=True)
    parser.add_argument(
        "--character", required=True, help="must EXACTLY match the chapter's pov, case-sensitive (e.g. 'Soren')"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--voice-file", help="path to a utf-8 file holding the voice spec")
    group.add_argument("--voice-text", help="the voice spec, inline")
    asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    main()
