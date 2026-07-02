from __future__ import annotations

import uuid

from sqlalchemy import select

from dominion.api.routers import production as production_router
from dominion.shared.models import (
    Approval,
    Beat,
    Book,
    Chapter,
    ChapterPacket,
    Critique,
    Issue,
    Job,
    Scene,
    ScenePacket,
)
from dominion.shared.schemas import ProductionRunCreateIn, ProductionRunStartIn


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


async def test_triage_creates_repair_task_for_scene_local_issue(db_factory):
    async with db_factory() as s:
        _book, chapter, _scene, _packet = await _seed_chapter(s, seed_count=2, add_critique=True)
        started = await production_router.start_production_run(
            ProductionRunCreateIn(chapter_id=chapter.id, auto_triage=False),
            s,
        )

        triaged = await production_router.triage_production_run(started.run.id, s)
        assert triaged.repair_task_count == 1
        detail = await production_router.get_production_run(started.run.id, s)

        task = detail.repair_tasks[0]
        assert task.repair_kind == "dialogue"
        assert task.authority_level == "span_only"
        statuses = {issue.issue_kind: issue.status for issue in detail.issues}
        assert statuses["dialogue"] == "repair_queued"
        assert statuses["missing_scene"] == "escalated"


async def test_apply_repair_task_queues_targeted_revision_job(db_factory):
    async with db_factory() as s:
        _book, chapter, scene, _packet = await _seed_chapter(s, seed_count=1, add_critique=True)
        started = await production_router.start_production_run(
            ProductionRunCreateIn(chapter_id=chapter.id, auto_triage=True),
            s,
        )
        detail = await production_router.get_production_run(started.run.id, s)
        task = detail.repair_tasks[0]

        out = await production_router.apply_repair_task(task.id, s)

        assert out.status == "running"
        jobs = (await s.execute(select(Job).where(Job.target_scene_id == scene.id))).scalars().all()
        assert len(jobs) == 1
        assert jobs[0].kind == "revise_pass"
        assert jobs[0].target_pass == "dialogue"
        approvals = (await s.execute(select(Approval).where(Approval.scene_id == scene.id))).scalars().all()
        assert approvals[-1].decision == "revise"
        assert "Dialogue reads flat" in (approvals[-1].feedback or "")


async def test_verify_repair_task_accepts_clean_revision_and_emits_final_artifact(db_factory):
    async with db_factory() as s:
        _book, chapter, scene, packet = await _seed_chapter(s, seed_count=1, add_critique=True)
        started = await production_router.start_production_run(
            ProductionRunCreateIn(chapter_id=chapter.id, auto_triage=True),
            s,
        )
        detail = await production_router.get_production_run(started.run.id, s)
        task = detail.repair_tasks[0]
        await production_router.apply_repair_task(task.id, s)

        revised = Scene(
            chapter_id=chapter.id,
            scene_no=scene.scene_no,
            version=2,
            parent_scene_id=scene.id,
            status="pending_review",
            scene_packet_id=packet.id,
            word_count=118,
            prose="Mara vaulted the breach and cut Seb off before he could panic.",
            prose_source="agent",
            agent_original="Mara vaulted the breach and cut Seb off before he could panic.",
            passes_run=["drafter", "dialogue"],
            token_count=380,
            model="test-model",
        )
        s.add(revised)
        await s.flush()

        verification = await production_router.verify_repair_task(task.id, s)

        assert verification.verdict == "accept"
        refreshed_detail = await production_router.get_production_run(started.run.id, s)
        refreshed_task = refreshed_detail.repair_tasks[0]
        assert refreshed_task.status == "verified"
        artifacts = [artifact for artifact in refreshed_detail.artifacts if artifact.artifact_type == "final_chapter"]
        assert artifacts, "expected a final chapter artifact after a clean repair verification"
        issues = (await s.execute(select(Issue).where(Issue.production_run_id == started.run.id))).scalars().all()
        assert {issue.status for issue in issues} == {"verified"}


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


async def test_issue_and_final_alias_endpoints_expose_production_surfaces(db_factory):
    async with db_factory() as s:
        _book, chapter, scene, packet = await _seed_chapter(s, seed_count=1, add_critique=True)
        started = await production_router.start_chapter_production_run(
            chapter.id,
            ProductionRunStartIn(auto_triage=False),
            s,
        )

        issues = await production_router.get_production_run_issues(started.run.id, s)
        assert len(issues) == 1
        accepted = await production_router.accept_issue(issues[0].id, None, s)
        assert accepted.status == "repair_queued"

        tasks = await production_router.get_production_run_repair_tasks(started.run.id, s)
        assert len(tasks) == 1
        await production_router.run_repair_task(tasks[0].id, s)

        revised = Scene(
            chapter_id=chapter.id,
            scene_no=scene.scene_no,
            version=2,
            parent_scene_id=scene.id,
            status="pending_review",
            scene_packet_id=packet.id,
            word_count=118,
            prose="Mara vaulted the breach and cut Seb off before he could panic.",
            prose_source="agent",
            agent_original="Mara vaulted the breach and cut Seb off before he could panic.",
            passes_run=["drafter", "dialogue"],
            token_count=380,
            model="test-model",
        )
        s.add(revised)
        await s.flush()
        await production_router.verify_repair_task(tasks[0].id, s)

        final_chapter = await production_router.get_final_chapter(started.run.id, s)
        assert final_chapter.artifact_type == "final_chapter"

        final_qa = await production_router.run_final_qa(started.run.id, s)
        assert final_qa.artifact_type == "chapter_draft_qa"

        events = await production_router.get_production_run_events(started.run.id, s)
        assert any(event.event_type == "final_ready" for event in events)

        artifacts = await production_router.get_production_run_artifacts(started.run.id, s)
        assert any(artifact.artifact_type == "final_chapter" for artifact in artifacts)
