"""Integration coverage for required_beats_preserved on RepairVerification (audit candidate D5).

The advisory flag now answers "did the repair drop a required beat that was present before it?" via the
deterministic ``beat_preservation`` delta, in BOTH verify paths. The old packet-binding proxy helper
``_required_beats_preserved`` is retired (its logic-level cases live in tests/test_beats_preserved.py).

  * single-scene: dropping a required beat flips the flag False + records the dropped beat, WITHOUT
    changing the verdict (the flag is advisory, not a gate).
  * chapter-scoped: a beat that RELOCATES between revised scenes stays preserved (concatenated region);
    a beat genuinely absent from the whole revised region is reported dropped.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select

from dominion.shared.enums import (
    IssueStatus,
    RepairAuthorityLevel,
    RepairTaskStatus,
    RepairVerificationVerdict,
)
from dominion.shared.models import (
    Book,
    Chapter,
    ChapterPacket,
    ChapterSequence,
    Issue,
    ProductionRun,
    RepairAttempt,
    RepairTask,
    RepairVerification,
    Scene,
)
from dominion.workers import production

BEAT_REACTOR = "Show the reactor core overheating beyond safe operating limits"
BEAT_BREACH = "Show the engineer sealing the hull breach with molten alloy"
PROSE_REACTOR = "The reactor core kept overheating past every safe operating limit as the sirens rose."
PROSE_BREACH = "The engineer knelt, sealing the ragged hull breach with a ribbon of molten alloy."
PROSE_CALM = "Rain fell on the quiet garden; a cat dozed on the warm windowsill."
QUOTE = "the flat gray corridor stretched on and on"


async def _seed_chapter(s, required_beats_by_scene: dict[int, list[str]]):
    book = Book(title="Realmwalkers")
    s.add(book)
    await s.flush()
    chapter = Chapter(book_id=book.id, chapter_no=7, pov="Mara", title="Signal Fire")
    s.add(chapter)
    await s.flush()
    run = ProductionRun(book_id=book.id, chapter_id=chapter.id, status="repairing")
    s.add(run)
    await s.flush()
    packet = ChapterPacket(
        book_id=book.id,
        chapter_id=chapter.id,
        status="approved",
        confidence="green",
        body={"scene_seeds": []},
        open_questions={"items": []},
    )
    s.add(packet)
    await s.flush()
    s.add(
        ChapterSequence(
            book_id=book.id,
            chapter_id=chapter.id,
            chapter_packet_id=packet.id,
            status="approved",
            body={
                "scenes": [
                    {"scene_no": scene_no, "required_beats": beats}
                    for scene_no, beats in sorted(required_beats_by_scene.items())
                ]
            },
        )
    )
    await s.flush()
    return book, chapter, run


async def _scene(s, chapter, *, scene_no: int, version: int, prose: str) -> Scene:
    scene = Scene(
        chapter_id=chapter.id,
        scene_no=scene_no,
        version=version,
        status="pending_review",
        word_count=len(prose.split()),
        prose=prose,
        prose_source="agent",
    )
    s.add(scene)
    await s.flush()
    return scene


# --- single-scene ---------------------------------------------------------------------------------


async def test_single_scene_dropped_required_beat_flags_not_preserved_without_gating(db_factory):
    async with db_factory() as s:
        _book, chapter, run = await _seed_chapter(s, {1: [BEAT_REACTOR]})
        base = await _scene(s, chapter, scene_no=1, version=1, prose=f"{PROSE_REACTOR} {QUOTE}.")

        issue = Issue(
            production_run_id=run.id,
            chapter_id=chapter.id,
            artifact_type="scene_review_report",
            artifact_id=uuid.uuid4(),
            scene_id=base.id,
            scene_no=1,
            validator="voice",
            issue_kind="flat_line",
            severity="repair",
            quote=QUOTE,
            claim="This corridor line reads flat.",
            recommended_action="Rework the flagged span.",
            status=IssueStatus.ACCEPTED,
            payload_json={"signature": "sig-1"},
        )
        s.add(issue)
        await s.flush()
        task = RepairTask(
            production_run_id=run.id,
            chapter_id=chapter.id,
            scene_id=base.id,
            scene_no=1,
            repair_kind="expand",
            authority_level=RepairAuthorityLevel.SPAN_ONLY,
            status=RepairTaskStatus.QUEUED,
            issue_ids=[str(issue.id)],
            target_spans={"items": [{"quote": QUOTE}]},
            instructions="Repair kind: expand. Rework the flagged span.",
            preserve=["Preserve scene outcome."],
            must_change=[QUOTE],
            allowed_operations=["replace_span", "rewrite_scene"],
            forbidden_operations=["change_canon"],
            requires_human_approval=False,
        )
        s.add(task)
        await s.flush()

        await production.apply_repair_task(s, task.id)
        # The revision "lands": a new scene version that fixes the flagged quote BUT drops the reactor
        # beat entirely (calm, unrelated prose).
        await _scene(
            s, chapter, scene_no=1, version=2, prose="The engineer steadied her breath; the panic finally eased."
        )

        verification = await production.verify_repair_task(s, task.id)

        assert verification.required_beats_preserved is False
        check = verification.payload_json["required_beats_check"]
        assert check["scope"] == "single_scene"
        assert check["status"] == "checked"
        assert check["dropped_beats"] == [BEAT_REACTOR]
        assert check["scene_no"] == 1
        # Advisory, not a gate: the beat dropped, yet the verdict rides on issue/critique state (the
        # flagged quote is gone, no new critiques) — so it still ACCEPTs.
        assert verification.verdict == RepairVerificationVerdict.ACCEPT


# --- chapter-scoped -------------------------------------------------------------------------------


async def _chapter_scoped_task(s, run, chapter) -> RepairTask:
    task = RepairTask(
        production_run_id=run.id,
        chapter_id=chapter.id,
        scene_id=None,  # chapter-scoped
        scene_no=None,
        repair_kind="continuity",
        authority_level=RepairAuthorityLevel.CHAPTER_STRUCTURAL,
        status=RepairTaskStatus.RUNNING,
        issue_ids=[],
        target_spans={"items": []},
        instructions="Structural repair across scenes.",
        preserve=[],
        must_change=[],
        allowed_operations=["rewrite_scene"],
        forbidden_operations=["change_canon"],
        requires_human_approval=True,
    )
    s.add(task)
    await s.flush()
    return task


async def _attempt_for(s, task, base_scene: Scene, offset: int) -> None:
    s.add(
        RepairAttempt(
            repair_task_id=task.id,
            attempt_no=offset,
            model="test",
            patch_json={
                "scene_id": str(base_scene.id),
                "scene_no": base_scene.scene_no,
                "base_version": base_scene.version,
                "applied_via": "revision_job",
            },
        )
    )
    await s.flush()


async def test_chapter_scoped_relocated_beat_stays_preserved(db_factory):
    # Beat moves from scene 3 -> scene 4 across the revision. The concatenated revised region still
    # contains it, so it must NOT be reported as dropped.
    async with db_factory() as s:
        _book, chapter, run = await _seed_chapter(s, {3: [BEAT_REACTOR], 4: []})
        base3 = await _scene(s, chapter, scene_no=3, version=1, prose=PROSE_REACTOR)
        base4 = await _scene(s, chapter, scene_no=4, version=1, prose=PROSE_CALM)
        await _scene(s, chapter, scene_no=3, version=2, prose=PROSE_CALM)  # beat leaves scene 3
        await _scene(s, chapter, scene_no=4, version=2, prose=PROSE_REACTOR)  # ...and lands in scene 4

        task = await _chapter_scoped_task(s, run, chapter)
        await _attempt_for(s, task, base3, offset=1)
        await _attempt_for(s, task, base4, offset=2)

        verification = await production.verify_repair_task(s, task.id)

        assert verification.required_beats_preserved is True
        check = verification.payload_json["required_beats_check"]
        assert check["scope"] == "chapter_scoped"
        assert check["status"] == "checked"
        assert check["dropped_beats"] == []
        assert sorted(check["scene_numbers"]) == [3, 4]


async def test_chapter_scoped_true_region_drop_is_reported(db_factory):
    # Reactor beat disappears from the whole revised region; breach beat is retained.
    async with db_factory() as s:
        _book, chapter, run = await _seed_chapter(s, {3: [BEAT_REACTOR], 4: [BEAT_BREACH]})
        base3 = await _scene(s, chapter, scene_no=3, version=1, prose=PROSE_REACTOR)
        base4 = await _scene(s, chapter, scene_no=4, version=1, prose=PROSE_BREACH)
        await _scene(s, chapter, scene_no=3, version=2, prose=PROSE_CALM)  # reactor beat gone
        await _scene(s, chapter, scene_no=4, version=2, prose=PROSE_BREACH)  # breach beat kept

        task = await _chapter_scoped_task(s, run, chapter)
        await _attempt_for(s, task, base3, offset=1)
        await _attempt_for(s, task, base4, offset=2)

        verification = await production.verify_repair_task(s, task.id)

        assert verification.required_beats_preserved is False
        check = verification.payload_json["required_beats_check"]
        assert check["scope"] == "chapter_scoped"
        assert check["dropped_beats"] == [BEAT_REACTOR]
        assert BEAT_BREACH not in check["dropped_beats"]


# --- guard: the retired proxy helper is gone ------------------------------------------------------


async def test_retired_proxy_helper_is_gone(db_factory):
    import dominion.workers.production_repair as pr

    assert not hasattr(pr, "_required_beats_preserved")
    # And the verification path also does not reconstruct the old packet-binding proxy for the flag.
    async with db_factory() as s:
        rows = (await s.execute(select(RepairVerification).limit(0))).scalars().all()
        assert rows == []
