"""GET /books/{book_id}/chapters/overview — the Chapters command-center aggregate.

The endpoint batches per-chapter pipeline facts (packet approval state, scene-contract counts,
violation fold, the authoritative draft gate, latest production run) in one request. The parity
test pins the fetch/derive split of draft_readiness against the per-chapter path: same chapter,
same gate verdict, byte-identical reason.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from fastapi import HTTPException

from dominion.api.routers import books as books_router
from dominion.shared.models import (
    Beat,
    Book,
    Chapter,
    ChapterPacket,
    ProductionRun,
    Scene,
    ScenePacket,
)
from dominion.workers.draft_readiness import compute_draft_readiness


async def _seed_book(s):
    """ch1: approved packet (2 seeds) + approved scene-1 contract with violations + linked beat +
    drafted scene + two production runs. ch2: bare — no packet, no contracts, nothing."""
    book = Book(title="Realmwalkers")
    s.add(book)
    await s.flush()

    ch1 = Chapter(book_id=book.id, chapter_no=1, pov="Mara", title="Signal Fire")
    ch2 = Chapter(book_id=book.id, chapter_no=2, pov="Seb", title="Bare Bones")
    s.add_all([ch1, ch2])
    await s.flush()

    seeds = [
        {"seed_id": str(uuid.uuid4()), "scene_no": 1, "scene_job": "Open the breach"},
        {"seed_id": str(uuid.uuid4()), "scene_no": 2, "scene_job": "Reach the relay"},
    ]
    packet = ChapterPacket(
        book_id=book.id,
        chapter_id=ch1.id,
        status="approved",
        confidence="green",
        body={"chapter_no": 1, "scene_seeds": seeds},
        open_questions={"items": []},
    )
    s.add(packet)
    await s.flush()

    sp = ScenePacket(
        book_id=book.id,
        chapter_id=ch1.id,
        chapter_packet_id=packet.id,
        scene_no=1,
        status="approved",
        qa_verdict="approve",
        body={"scene_no": 1},
        qa_warnings={
            "residual_risks": [],
            "issues": [],
            "violations": [
                {"kind": "roster_double_bucketed", "detail": "present and absent", "severity": "repair"},
                {"kind": "legacy_row", "detail": "old snapshot", "severity": "hard"},
            ],
        },
        source_hash="seed-1",
    )
    s.add(sp)
    await s.flush()

    beat = Beat(
        chapter_id=ch1.id,
        scene_packet_id=sp.id,
        scene_no=1,
        beat_text="Mara cuts through the breach.",
        characters_present=["Mara"],
        status="approved",
    )
    s.add(beat)

    scene = Scene(
        chapter_id=ch1.id,
        scene_no=1,
        version=1,
        status="approved",
        word_count=8,
        prose="Mara vaulted the breach and kept moving.",
        prose_source="agent",
    )
    s.add(scene)

    run_old = ProductionRun(
        book_id=book.id,
        chapter_id=ch1.id,
        status="completed",
        current_stage="final",
        summary_json={"issue_count": 9, "repair_task_count": 9},
        created_at=datetime(2026, 7, 1, tzinfo=UTC),
    )
    run_new = ProductionRun(
        book_id=book.id,
        chapter_id=ch1.id,
        status="repairing",
        current_stage="repair_queue",
        summary_json={"issue_count": 2, "repair_task_count": 1},
        created_at=datetime(2026, 7, 4, tzinfo=UTC),
    )
    s.add_all([run_old, run_new])
    await s.flush()
    return book, ch1, ch2


async def test_overview_reports_the_full_pipeline_per_chapter(db_factory):
    async with db_factory() as s:
        book, ch1, ch2 = await _seed_book(s)

        rows = await books_router.chapters_overview(book.id, s)

        assert [r.chapter_no for r in rows] == [1, 2]
        rich, bare = rows

        # Packet axis: an approved packet reads as already_approved with its guaranteed reason.
        assert rich.packet_status == "approved"
        assert rich.packet_approval_state == "already_approved"
        assert rich.packet_approval_blockers
        # Contract + prose axes.
        assert rich.scene_packets_total == 1
        assert rich.scene_packets_approved == 1
        assert rich.expected_scenes == 2
        assert rich.scenes_with_prose == 1
        assert rich.assembly_ready is False  # scene 2 has no prose yet
        # Violation fold sums across the chapter's contracts; raw tokens pass through (the UI folds
        # legacy "hard" into block).
        assert rich.violation_counts == {"repair": 1, "hard": 1}
        # Production axis: the newest run wins, counts from summary_json.
        assert rich.latest_run is not None
        assert rich.latest_run.status == "repairing"
        assert rich.latest_run.issue_count == 2
        assert rich.latest_run.repair_task_count == 1

        # A bare chapter is honest, not empty-crashy.
        assert bare.packet_status is None
        assert bare.packet_approval_state is None
        assert bare.scene_packets_total == 0
        assert bare.latest_run is None
        assert bare.can_draft is False
        assert bare.disabled_reason == "Chapter packet is not approved yet — approve it first."


async def test_overview_gate_matches_the_per_chapter_readiness_endpoint(db_factory):
    # Parity guard for the fetch/derive split: the overview's gate fields must equal what
    # compute_draft_readiness (the per-chapter path every existing consumer uses) reports.
    async with db_factory() as s:
        book, ch1, ch2 = await _seed_book(s)

        rows = {r.chapter_id: r for r in await books_router.chapters_overview(book.id, s)}
        for chapter in (ch1, ch2):
            readiness = await compute_draft_readiness(s, chapter.id)
            row = rows[chapter.id]
            assert row.can_draft == readiness.can_draft
            assert row.disabled_reason == readiness.disabled_reason
            assert row.assembly_ready == readiness.prose["assembly_ready"]
            assert row.active_draft_jobs == readiness.active_draft_jobs
            assert row.provider_rate_limited == readiness.provider_rate_limited


async def test_overview_404s_on_a_missing_book(db_factory):
    async with db_factory() as s:
        with pytest.raises(HTTPException) as err:
            await books_router.chapters_overview(uuid.uuid4(), s)
        assert err.value.status_code == 404
