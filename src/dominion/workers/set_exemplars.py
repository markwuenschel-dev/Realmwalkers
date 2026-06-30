"""Curate a per-POV voice exemplar list into PovProfile — the companion to set_voice.

The drafter few-shots on the author's own approved prose for a POV: `context.assemble_context` loads
`PovProfile.exemplar_scene_ids` into `ctx.exemplars`, and `specialists/drafter.py: _voice_system`
injects them ("Match the voice of these passages:"). This CLI is the authoring path for that list —
the scene-editor "use as voice exemplar" action is the eventual UI for the same field. Mirrors
set_voice: find-or-create the book by title, idempotent upsert (re-running REPLACES the list, never a
second profile row). Scope is `exemplar_scene_ids` only; `voice_spec` is left untouched.

Runnable as:
    uv run python -m dominion.workers.set_exemplars --book "..." --character Marcus \
        --scene-ids 1f2e... ,  9a8b...
"""

from __future__ import annotations

import argparse
import asyncio
import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dominion.shared.db import SessionFactory
from dominion.shared.models import Book, PovProfile, Scene


async def set_exemplars(
    session: AsyncSession,
    *,
    book_title: str,
    character: str,
    scene_ids: Sequence[uuid.UUID],
) -> uuid.UUID:
    """Resolve (or create) the book by title and upsert one PovProfile per (book_id, character).

    Sets only `exemplar_scene_ids` (stored as ARRAY(Text), so ids are persisted as strings); leaves
    `voice_spec` alone. An empty list clears the exemplars. Flushes and returns the profile id; the
    caller owns the commit (so this stays unit-testable on any session).
    """
    book = (await session.execute(select(Book).where(Book.title == book_title))).scalar_one_or_none()
    if book is None:
        book = Book(title=book_title)
        session.add(book)
        await session.flush()

    stored = [str(sid) for sid in scene_ids] or None
    profile = (
        await session.execute(
            select(PovProfile).where(PovProfile.book_id == book.id, PovProfile.character == character)
        )
    ).scalar_one_or_none()
    if profile is None:
        profile = PovProfile(book_id=book.id, character=character, exemplar_scene_ids=stored)
        session.add(profile)
    else:
        profile.exemplar_scene_ids = stored
    await session.flush()
    return profile.id


def _parse_ids(raw: str) -> list[uuid.UUID]:
    """Comma/space-separated scene UUIDs -> list[UUID]; a malformed id fails loudly (don't silently drop)."""
    out: list[uuid.UUID] = []
    for token in raw.replace(",", " ").split():
        try:
            out.append(uuid.UUID(token))
        except ValueError as exc:
            raise SystemExit(f"not a valid scene UUID: {token!r}") from exc
    return out


async def _run(args: argparse.Namespace) -> None:
    scene_ids = _parse_ids(args.scene_ids)
    async with SessionFactory() as session:
        if scene_ids:  # friendly check: warn on ids that don't exist, but still store what was asked
            found = set((await session.execute(select(Scene.id).where(Scene.id.in_(scene_ids)))).scalars().all())
            for sid in scene_ids:
                if sid not in found:
                    print(f"warning: scene {sid} not found (storing anyway)")
        profile_id = await set_exemplars(session, book_title=args.book, character=args.character, scene_ids=scene_ids)
        await session.commit()

    print(
        f"exemplars set for character='{args.character}' in book='{args.book}' "
        f"(profile {profile_id}, {len(scene_ids)} scene(s))"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Curate a per-POV voice exemplar list (read by the drafter) into PovProfile."
    )
    parser.add_argument("--book", required=True)
    parser.add_argument(
        "--character", required=True, help="must EXACTLY match the chapter's pov, case-sensitive (e.g. 'Marcus')"
    )
    parser.add_argument(
        "--scene-ids",
        required=True,
        help="comma/space-separated scene UUIDs to use as voice exemplars (empty string clears the list)",
    )
    asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    main()
