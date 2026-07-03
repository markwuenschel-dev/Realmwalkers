"""Smoke coverage for the production-run pipeline (workers/production.py + routers/production.py).

Trimmed to two lifecycle smoke tests: a run can be created/started and produce its sequence artifacts
and structured issues, and the chapter-sequence QA can reach a blocked terminal state. production.py is
live code a later workstream builds on, so the file is kept (not deleted) at smoke depth.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select

from dominion.api.routers import production as production_router
from dominion.shared.models import (
    Beat,
    Book,
    Chapter,
    ChapterPacket,
    Critique,
    Scene,
    ScenePacket,
)
from dominion.shared.schemas import ProductionRunCreateIn


async def _seed_chapter(
    s,
    *,
    seed_count: int = 2,
    add_critique: bool = True,
) -> tuple[Book, Chapter, Scene, ScenePacket]:
    book = Book(title="Realmwalkers")
    s.add(book)
    await s.flush()

    chapter = Chapter(book_id=book.id, chapter_no=7, pov="Mara", title="Signal Fire")
    s.add(chapter)
    await s.flush()

    seeds = []
    for scene_no in range(1, seed_count + 1):
        seeds.append(
            {
                "seed_id": str(uuid.uuid4()),
                "scene_no": scene_no,
                "scene_job": f"Scene {scene_no} job",
                "required_beats": [f"Beat {scene_no} lands"],
                "forbidden_beats": [],
            }
        )
    packet = ChapterPacket(
        book_id=book.id,
        chapter_id=chapter.id,
        status="approved",
        confidence="green",
        body={
            "chapter_no": chapter.chapter_no,
            "chapter_job": "Push Mara through the breach.",
            "one_sentence_spine": "Mara chooses speed over certainty.",
            "entry_state": "Mara is pinned down.",
            "exit_state": "Mara reaches the relay tower.",
            "required_unanswered_questions": ["What is behind the relay tower signal?"],
            "forbidden_knowledge": ["The tower is bait."],
            "scene_seeds": seeds,
        },
        open_questions={"items": []},
    )
    s.add(packet)
    await s.flush()

    beat = Beat(
        chapter_id=chapter.id,
        scene_seed_id=uuid.UUID(seeds[0]["seed_id"]),
        scene_no=1,
        beat_text="Mara cuts through the breach.",
        characters_present=["Mara", "Seb"],
        tags=["dialogue"],
        status="approved",
    )
    s.add(beat)
    await s.flush()

    scene_packet = ScenePacket(
        book_id=book.id,
        chapter_id=chapter.id,
        chapter_packet_id=packet.id,
        scene_seed_id=uuid.UUID(seeds[0]["seed_id"]),
        scene_no=1,
        status="approved",
        qa_verdict="approve",
        body={
            "scene_no": 1,
            "known_before_scene": {"reader": [], "pov": [], "omniscient_author": []},
            "learned_during_scene": {"reader_must_learn": [], "reader_may_learn": [], "reader_may_infer_only": []},
            "must_remain_hidden": {"reader": [], "pov": [], "all_surface_prose": []},
        },
        source_hash="seed-1",
    )
    s.add(scene_packet)
    await s.flush()
    beat.scene_packet_id = scene_packet.id

    scene = Scene(
        chapter_id=chapter.id,
        scene_no=1,
        version=1,
        status="pending_review",
        scene_packet_id=scene_packet.id,
        word_count=120,
        prose="Hello there. Mara vaulted the breach and kept moving.",
        prose_source="agent",
        agent_original="Hello there. Mara vaulted the breach and kept moving.",
        passes_run=["drafter", "dialogue"],
        token_count=400,
        model="test-model",
    )
    s.add(scene)
    await s.flush()

    if add_critique:
        s.add(
            Critique(
                scene_id=scene.id,
                scene_packet_id=scene_packet.id,
                version=scene.version,
                reviewer="dialogue",
                severity="warn",
                note="Dialogue reads flat and generic.",
                payload={"quote": "Hello there.", "span": [0, 12]},
            )
        )
    await s.flush()
    return book, chapter, scene, scene_packet


async def test_start_production_run_creates_sequence_artifacts_and_structured_issues(db_factory):
    async with db_factory() as s:
        _book, chapter, _scene, _packet = await _seed_chapter(s, seed_count=2, add_critique=True)

        out = await production_router.start_production_run(
            ProductionRunCreateIn(chapter_id=chapter.id, auto_triage=False),
            s,
        )

        assert out.issue_count == 2  # dialogue critique + missing scene
        assert out.repair_task_count == 0
        detail = await production_router.get_production_run(out.run.id, s)
        artifact_types = {artifact.artifact_type for artifact in detail.artifacts}
        assert {
            "contract_classification",
            "chapter_sequence",
            "issue_set",
            "chapter_draft",
            "chapter_draft_qa",
        } <= artifact_types
        assert detail.chapter_sequence is not None
        assert detail.chapter_sequence.body["scenes"][0]["scene_no"] == 1
        assert any(issue.issue_kind == "missing_scene" for issue in detail.issues)
        assert any(issue.validator == "dialogue" for issue in detail.issues)


async def test_chapter_sequence_qa_blocks_duplicate_ownership_and_duplicate_functions(db_factory):
    async with db_factory() as s:
        _book, chapter, _scene, _packet = await _seed_chapter(s, seed_count=2, add_critique=False)
        packet = (
            (
                await s.execute(
                    select(ChapterPacket).where(ChapterPacket.chapter_id == chapter.id).order_by(ChapterPacket.id)
                )
            )
            .scalars()
            .one()
        )
        scene_seeds = list(packet.body["scene_seeds"])
        scene_seeds[1] = {
            **scene_seeds[1],
            "required_beats": list(scene_seeds[0]["required_beats"]),
            "scene_job": scene_seeds[0]["scene_job"],
        }
        packet.body = {**packet.body, "scene_seeds": scene_seeds}
        await s.flush()

        sequence = await production_router.derive_chapter_sequence(chapter.id, s)
        qa = await production_router.qa_chapter_sequence(sequence.id, s)

        assert qa.verdict == "block_drafting"
        assert qa.warnings is not None
        assert qa.warnings["duplicate_beat_ownership"]
        assert qa.warnings["duplicate_scene_functions"]
        assert any(action["kind"] == "merge_scenes" for action in qa.required_actions)


# --- run_chapter_draft_qa (pure, no DB): PRESENT_CHARACTER_NOT_VISIBLE + severity facts ------------


def _draft_rows(*prose: str) -> list[dict]:
    return [
        {"scene_no": i, "prose": p, "word_count": len(p.split()), "scene_function": f"fn{i}"}
        for i, p in enumerate(prose, start=1)
    ]


def test_present_character_not_visible_is_repair_finding():
    from dominion.workers.production import run_chapter_draft_qa

    packet_body = {"characters_present": ["Marcus Vye (POV)", "Serra Hawthorne (masked)", "The Broker"]}
    prose = "Marcus circled the scrim. Across the sand, Serra kept her visor down. Nobody mentioned brokers' guilds."
    qa = run_chapter_draft_qa(None, _draft_rows(prose), prose, packet_body=packet_body)

    missing = [f for f in qa["findings"] if f["kind"] == "PRESENT_CHARACTER_NOT_VISIBLE"]
    # Marcus and Serra are visible via first-name whole-word references; "The Broker" never appears
    # ("brokers'" is not a whole-word match on "Broker").
    assert [f["character"] for f in missing] == ["The Broker"]
    f = missing[0]
    assert f["severity"] == "repair"
    assert f["blocks_drafting"] is False and f["blocks_human_review"] is False
    assert f["blocks_final_export"] is True
    # Repair findings escalate the verdict to at most "warn" — never "block" (human review proceeds).
    assert qa["verdict"] == "warn"


def test_all_present_characters_visible_passes():
    from dominion.workers.production import run_chapter_draft_qa

    packet_body = {"characters_present": ["Marcus Vye"]}
    prose = "Marcus won."
    qa = run_chapter_draft_qa(None, _draft_rows(prose), prose, packet_body=packet_body)
    assert not [f for f in qa["findings"] if f["kind"] == "PRESENT_CHARACTER_NOT_VISIBLE"]
    assert qa["verdict"] == "pass"


def test_draft_qa_block_findings_carry_block_facts():
    from dominion.workers.production import run_chapter_draft_qa

    rows = [
        {"scene_no": 1, "prose": "One.", "word_count": 1, "scene_function": "dup"},
        {"scene_no": 2, "prose": "Two.", "word_count": 1, "scene_function": "dup"},
    ]
    qa = run_chapter_draft_qa(None, rows, "One.\n\nTwo.")
    dup = [f for f in qa["findings"] if f["kind"] == "duplicate_scene_function"]
    assert dup and dup[0]["severity"] == "block" and dup[0]["blocks_drafting"] is True
    assert qa["verdict"] == "block"
