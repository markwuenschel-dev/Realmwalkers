"""Bootstrap + enqueue a scene job, with the beat content inline (Phase 1 helper).

One command: create book -> chapter -> beat -> run -> queued job. Pass the beat prose with
--beat-text or --beat-file (so you don't author it in a second psql round-trip); re-running with new
text upserts the existing beat. Add --draft to draft the scene immediately after enqueueing. In the
full flow, beats come from the gate-1 plan call (DESIGN §4); this is the manual path for early scenes.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dominion.shared.config import settings
from dominion.shared.db import SessionFactory
from dominion.shared.enums import (
    BeatStatus,
    GateMode,
    JobKind,
    JobStatus,
    PacketStatus,
    ScenePacketStatus,
)
from dominion.shared.models import Beat, Book, Chapter, ChapterPacket, Job, Run, ScenePacket

_PLACEHOLDER = "TODO: write this beat (gate 1)."


async def _ensure_scene_packet(
    s: AsyncSession, *, book: Book, chapter: Chapter, beat: Beat
) -> ScenePacket:
    """Drafting is fail-closed on an approved ScenePacket. The manual enqueue path has no chapter
    packet / scene-packet flow, so mint a minimal APPROVED ChapterPacket + ScenePacket for this scene
    and link the beat to it. Idempotent per (chapter, scene_no): reuse an existing one."""
    existing: ScenePacket | None = (await s.execute(
        select(ScenePacket).where(
            ScenePacket.chapter_id == chapter.id, ScenePacket.scene_no == beat.scene_no
        ).order_by(ScenePacket.created_at.desc())
    )).scalars().first()
    if existing is not None:
        existing.status = ScenePacketStatus.APPROVED
        beat.scene_packet_id = existing.id
        return existing

    cp = (await s.execute(
        select(ChapterPacket).where(ChapterPacket.chapter_id == chapter.id).limit(1)
    )).scalar_one_or_none()
    if cp is None:
        cp = ChapterPacket(
            book_id=book.id, chapter_id=chapter.id, status=PacketStatus.APPROVED,
            confidence="green", body={"scene_seeds": [], "manual": True},
            open_questions={"items": []},
        )
        s.add(cp)
        await s.flush()

    target = beat.target_words or 1500
    sp = ScenePacket(
        book_id=book.id, chapter_id=chapter.id, chapter_packet_id=cp.id, scene_no=beat.scene_no,
        status=ScenePacketStatus.APPROVED, qa_verdict="approve",
        body={
            "scene_no": beat.scene_no,
            "scene_job": beat.beat_text or "",
            "word_budget": {
                "target": target, "min": round(target * 0.7),
                "max": round(target * 1.35), "hard_max": round(target * 1.6),
            },
            "known_before_scene": {"reader": [], "pov": [], "omniscient_author": []},
            "learned_during_scene": {"reader_must_learn": [], "reader_may_learn": [], "reader_may_infer_only": []},
            "must_remain_hidden": {"reader": [], "pov": [], "all_surface_prose": []},
            "manual": True,
        },
        source_hash="manual",
    )
    s.add(sp)
    await s.flush()
    beat.scene_packet_id = sp.id
    return sp


async def enqueue_scene(
    book_title: str,
    chapter_no: int,
    scene_no: int,
    pov: str,
    *,
    beat_text: str | None = None,
    characters: list[str] | None = None,
    tags: list[str] | None = None,
    expected_state_changes: dict[str, Any] | None = None,
) -> uuid.UUID | None:
    """Upsert the beat and queue a draft job. Returns the job id (or None if one was already queued)."""
    async with SessionFactory() as s:
        book = (await s.execute(select(Book).where(Book.title == book_title))).scalar_one_or_none()
        if book is None:
            book = Book(title=book_title)
            s.add(book)
            await s.flush()

        chapter = (await s.execute(
            select(Chapter).where(Chapter.book_id == book.id, Chapter.chapter_no == chapter_no)
        )).scalar_one_or_none()
        if chapter is None:
            chapter = Chapter(book_id=book.id, chapter_no=chapter_no, pov=pov)
            s.add(chapter)
            await s.flush()

        # Upsert the beat: create it, or update the fields you actually supplied.
        beat = (await s.execute(
            select(Beat).where(Beat.chapter_id == chapter.id, Beat.scene_no == scene_no)
        )).scalar_one_or_none()
        if beat is None:
            beat = Beat(
                chapter_id=chapter.id,
                scene_no=scene_no,
                tags=tags or [],
                characters_present=characters,
                expected_state_changes=expected_state_changes,
                status=BeatStatus.APPROVED,
                beat_text=beat_text or _PLACEHOLDER,
            )
            s.add(beat)
        else:
            if beat_text is not None:
                beat.beat_text = beat_text
            if characters is not None:
                beat.characters_present = characters
            if tags is not None:
                beat.tags = tags
            if expected_state_changes is not None:
                beat.expected_state_changes = expected_state_changes
            beat.status = BeatStatus.APPROVED
        await s.flush()

        # Drafting is fail-closed on an approved ScenePacket — mint/link a minimal one for this scene.
        await _ensure_scene_packet(s, book=book, chapter=chapter, beat=beat)
        await s.flush()

        # Don't stack un-drafted jobs: if one is already queued for this scene, just keep the
        # beat edit and reuse it (so "edit beat -> re-run enqueue" is idempotent).
        existing = (await s.execute(
            select(Job).join(Run, Job.run_id == Run.id).where(
                Run.book_id == book.id,
                Job.chapter_no == chapter_no,
                Job.scene_no == scene_no,
                Job.status == JobStatus.QUEUED,
            )
        )).scalars().first()
        if existing is not None:
            await s.commit()
            note = "beat updated" if beat_text is not None else "beat unchanged"
            print(f"{note}; reusing queued job {existing.id} for ch{chapter_no} sc{scene_no}")
            return existing.id

        run = Run(
            book_id=book.id,
            scope_json={"chapter": chapter_no, "scene": scene_no},
            gate_mode=GateMode.PAUSE_EACH,
            token_budget=settings.scene_token_budget,
        )
        s.add(run)
        await s.flush()

        job = Job(
            run_id=run.id, kind=JobKind.DRAFT, chapter_no=chapter_no, scene_no=scene_no,
            book_id=book.id, chapter_id=chapter.id, beat_id=beat.id,
            scene_packet_id=beat.scene_packet_id,
            token_budget=settings.scene_token_budget, status=JobStatus.QUEUED,
        )
        s.add(job)
        await s.commit()
        has_text = beat_text is not None and beat_text != _PLACEHOLDER
        beat_note = "real beat" if has_text else "PLACEHOLDER beat (pass --beat-text/--beat-file)"
        print(f"queued job {job.id} for ch{chapter_no} sc{scene_no} "
              f"(book='{book.title}', pov={pov}, {beat_note})")
        return job.id


def _split(value: str | None) -> list[str] | None:
    if not value:
        return None
    return [item.strip() for item in value.split(",") if item.strip()]


def _parse_esc(raw: str | None) -> dict[str, Any] | None:
    """Parse --expected-state-changes JSON, e.g. '{"Marcus": {"level": "+1"}}'."""
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"--expected-state-changes is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit("--expected-state-changes must be a JSON object, e.g. '{\"Marcus\": {\"level\": \"+1\"}}'")
    return data


async def _run(args: argparse.Namespace) -> None:
    beat_text: str | None = None
    if args.beat_file:
        beat_text = Path(args.beat_file).read_text(encoding="utf-8").strip()
    elif args.beat_text:
        beat_text = args.beat_text

    await enqueue_scene(
        args.book, args.chapter, args.scene, args.pov,
        beat_text=beat_text, characters=_split(args.characters), tags=_split(args.tags),
        expected_state_changes=_parse_esc(args.expected_state_changes),
    )

    if args.draft:
        from dominion.workers.worker import run_once  # local import: avoids LLM deps unless drafting
        drafted = await run_once()
        print("drafted; check the inbox" if drafted else "nothing drafted (no queued job)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Enqueue a scene job with its beat, then optionally draft it.")
    parser.add_argument("--book", required=True)
    parser.add_argument("--chapter", type=int, required=True)
    parser.add_argument("--scene", type=int, required=True)
    parser.add_argument("--pov", default="Marcus")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--beat-text", help="the beat prose, inline")
    group.add_argument("--beat-file", help="path to a file containing the beat prose")
    parser.add_argument("--characters", help="comma-separated, e.g. 'Marcus,Mara'")
    parser.add_argument("--tags", help="comma-separated enrichment tags (Phase 3), e.g. 'combat,dialogue'")
    parser.add_argument(
        "--expected-state-changes",
        help="JSON stat deltas committed to the ledger on approval, "
             "e.g. '{\"Marcus\": {\"level\": \"+1\", \"hp\": 100}}'",
    )
    parser.add_argument("--draft", action="store_true", help="draft the scene immediately after enqueueing")
    asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    main()
