"""Hard scene delete must remove the scene AND everything that references it (critiques, annotations,
approvals, suggestions, edit-pairs are NOT NULL FKs; summaries/ledger/jobs/child versions/draft
attempts/knowledge facts are soft refs that get detached). Otherwise the FK constraints block the
delete — a nullable FK still blocks it. Backs the inbox's bulk 'delete selected'."""
from __future__ import annotations

from sqlalchemy import select

from dominion.api.routers import scenes as scenes_router
from dominion.shared.enums import Decision, Severity
from dominion.shared.models import (
    Approval,
    Book,
    Chapter,
    Critique,
    DraftAttempt,
    EditPair,
    KnowledgeFact,
    Scene,
    Summary,
)


async def test_delete_scene_removes_scene_and_all_references(db_factory):
    async with db_factory() as s:
        book = Book(title="X")
        s.add(book)
        await s.flush()
        ch = Chapter(book_id=book.id, chapter_no=1, pov="Marcus")
        s.add(ch)
        await s.flush()
        scene = Scene(chapter_id=ch.id, scene_no=1, prose="prose", version=1)
        s.add(scene)
        await s.flush()
        # hard dependents (NOT NULL scene_id) that would block the delete, + soft refs to detach
        s.add(Critique(scene_id=scene.id, version=1, reviewer="continuity",
                       severity=Severity.WARN, note="flag"))
        s.add(Approval(scene_id=scene.id, version=1, decision=Decision.APPROVE))
        s.add(EditPair(scene_id=scene.id, version=1, pov="Marcus",
                       agent_text="a", human_text="b"))
        s.add(Summary(book_id=book.id, scope="pov", pov="Marcus", rolling_summary="s",
                      up_to_scene_id=scene.id))
        # nullable scene FKs that ALSO block the delete unless cleared first (the 13/13 bug)
        s.add(DraftAttempt(scene_id=scene.id, stage="raw", prose="p"))
        s.add(KnowledgeFact(book_id=book.id, fact="f", source_scene_id=scene.id,
                            known_by_reader_after_scene_id=scene.id))
        await s.commit()

        out = await scenes_router.delete_scene(scene.id, s)
        assert out["deleted"] == str(scene.id)

        async def gone(model, **where):
            stmt = select(model)
            for k, v in where.items():
                stmt = stmt.where(getattr(model, k) == v)
            return (await s.execute(stmt)).first() is None

        assert await gone(Scene, id=scene.id)             # the scene itself
        assert await gone(Critique, scene_id=scene.id)    # hard dependents removed
        assert await gone(Approval, scene_id=scene.id)
        assert await gone(EditPair, scene_id=scene.id)
        # the summary survives but is detached from the deleted scene
        summ = (await s.execute(select(Summary).where(Summary.book_id == book.id))).scalar_one()
        assert summ.up_to_scene_id is None
        # append-only provenance survives, detached from the scene
        da = (await s.execute(select(DraftAttempt).where(DraftAttempt.stage == "raw"))).scalar_one()
        assert da.scene_id is None
        # book-level knowledge fact survives, all scene refs detached
        kf = (await s.execute(select(KnowledgeFact).where(KnowledgeFact.book_id == book.id))).scalar_one()
        assert kf.source_scene_id is None
        assert kf.known_by_reader_after_scene_id is None
