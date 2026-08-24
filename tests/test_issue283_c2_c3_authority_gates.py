"""#283 C2 and C3 — the two remaining enumerated ungated paths to a protected write.

**C2, `POST /scenes/{id}/approve`.** This is the one place a human blesses prose as canonical. It held
the chapter lock but consulted nothing: the `first_approval` check above the write is an idempotence
guard (don't re-run one-shot side effects), not an authorization. So prose drafted under a contract
whose open questions were never ruled could be made canon, which is exactly what #277's gate exists to
prevent one layer up.

**C3, `dominion-seed`.** The CLI lands scenes at APPROVED as an assignment AND a constructor, and took
no lock at all. The status itself is not the defect — the command imports prose the author already
wrote, so the operator IS the authority for it. The defect is that an APPROVED scene is a prior-scene
input to the scene-packet staleness hash, so an unlocked write could interleave with a concurrent
approval on the same chapter and shift staleness underneath it.

WHAT THESE TESTS REFUSE TO ACCEPT AS EVIDENCE. A 409 alone proves nothing about whether the write
happened: a refusal raised *after* a flush passes a response-code-only test while having already
mutated. Every refusal case here asserts the database too.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from dominion.api.routers.reviews import accept_scene_approval
from dominion.shared.enums import PacketStatus, SceneStatus
from dominion.shared.models import Approval, Book, Chapter, ChapterPacket, Scene
from dominion.workers.memory import seed as seed_mod
from dominion.workers.packet import contract_permit
from dominion.workers.packet import open_questions as oq


async def _seed_scene(s, *, open_questions: dict | None, scene_status=SceneStatus.PENDING_REVIEW):
    book = Book(title="Dominion Realm")
    s.add(book)
    await s.flush()
    ch = Chapter(book_id=book.id, chapter_no=1, pov="Marcus")
    s.add(ch)
    await s.flush()
    if open_questions is not None:
        s.add(
            ChapterPacket(
                book_id=book.id,
                chapter_id=ch.id,
                status=PacketStatus.APPROVED,
                confidence="green",
                body={"scene_seeds": []},
                open_questions=open_questions,
            )
        )
    scene = Scene(
        chapter_id=ch.id,
        scene_no=1,
        version=1,
        status=scene_status,
        prose="The courier crossed at dusk.",
        prose_source="agent",
    )
    s.add(scene)
    await s.flush()
    return ch.id, scene.id


async def _scene_state(db_factory, scene_id) -> tuple[str, int]:
    async with db_factory() as s:
        scene = await s.get(Scene, scene_id)
        approvals = (
            await s.execute(select(func.count()).select_from(Approval).where(Approval.scene_id == scene_id))
        ).scalar_one()
        return str(scene.status), approvals


# =================================================================================================
# C2 — editorial scene approval
# =================================================================================================


async def test_scene_approval_is_refused_while_the_contract_has_open_questions(db_factory):
    """1. The gate itself. A 409 AND an unchanged database — the refusal must precede every mutation."""
    async with db_factory() as s:
        chapter_id, scene_id = await _seed_scene(s, open_questions=oq.normalize({"items": ["who paid?"]}, mint=True))
        await s.commit()

    async with db_factory() as s2:
        with pytest.raises(HTTPException) as exc:
            await accept_scene_approval(s2, scene_id=scene_id, edited_prose=None, target_pass=None, feedback=None)
        assert exc.value.status_code == 409
        assert exc.value.detail["reason"] == contract_permit.REASON_OPEN_QUESTIONS

    status, approvals = await _scene_state(db_factory, scene_id)
    assert status != str(SceneStatus.APPROVED), "the scene was approved despite the refusal"
    assert approvals == 0, "an Approval row was written despite the refusal"


async def test_scene_approval_is_permitted_once_every_question_is_ruled(db_factory):
    """2. The gate opens. Same contract, same scene — only the rulings differ, so a test that passes
    both this and case 1 cannot be passing for an unrelated reason."""
    ruled = oq.normalize({"items": ["who paid?"]}, mint=True)
    item_id = ruled["items"][0]["item_id"]
    ruled = oq.normalize(
        {
            "items": ruled["items"],
            "resolved": [{"item_id": item_id, "resolution": "Mara paid.", "source": "author"}],
        },
        mint=True,
    )
    async with db_factory() as s:
        _chapter_id, scene_id = await _seed_scene(s, open_questions=ruled)
        await s.commit()

    async with db_factory() as s2:
        await accept_scene_approval(s2, scene_id=scene_id, edited_prose=None, target_pass=None, feedback=None)
        await s2.commit()

    status, approvals = await _scene_state(db_factory, scene_id)
    assert status == str(SceneStatus.APPROVED)
    assert approvals == 1


async def test_scene_approval_is_permitted_with_no_approved_contract(db_factory):
    """3. The ONE deliberate fail-open. Contract-free authoring is a supported flow; refusing it would
    make this permit break drafting that #283 was never about."""
    async with db_factory() as s:
        _chapter_id, scene_id = await _seed_scene(s, open_questions=None)
        await s.commit()

    async with db_factory() as s2:
        await accept_scene_approval(s2, scene_id=scene_id, edited_prose=None, target_pass=None, feedback=None)
        await s2.commit()

    status, _ = await _scene_state(db_factory, scene_id)
    assert status == str(SceneStatus.APPROVED)


async def test_re_approval_is_refused_too(db_factory):
    """4. The idempotence check is not a gate, and the gate is not an idempotence check.

    An already-APPROVED scene re-approved under an unresolved contract must still refuse: blessing the
    prose is the authority act, not the status transition. A permit wired to `first_approval` would
    pass every other test here and silently allow this.
    """
    async with db_factory() as s:
        _chapter_id, scene_id = await _seed_scene(
            s,
            open_questions=oq.normalize({"items": ["who paid?"]}, mint=True),
            scene_status=SceneStatus.APPROVED,
        )
        await s.commit()

    async with db_factory() as s2:
        with pytest.raises(HTTPException) as exc:
            await accept_scene_approval(s2, scene_id=scene_id, edited_prose=None, target_pass=None, feedback=None)
        assert exc.value.status_code == 409
        assert exc.value.detail["reason"] == contract_permit.REASON_OPEN_QUESTIONS

    _status, approvals = await _scene_state(db_factory, scene_id)
    assert approvals == 0, "a re-approval wrote an Approval row despite the refusal"


async def test_only_an_APPROVED_contract_gates_the_scene(db_factory):
    """5. Row selection, not just the predicate.

    `uq_chapter_packets_active_chapter` admits only one approved packet per chapter, so "newest
    approved" can never be ambiguous — the real risk is the opposite one: reading a packet that does not
    hold authority yet. A PROPOSED packet full of unresolved questions is a draft contract. Gating on it
    would block scene approval on a document nobody has adopted, which is a different bug wearing this
    fix's clothes.
    """
    async with db_factory() as s:
        book = Book(title="Dominion Realm")
        s.add(book)
        await s.flush()
        ch = Chapter(book_id=book.id, chapter_no=1, pov="Marcus")
        s.add(ch)
        await s.flush()
        s.add(
            ChapterPacket(
                book_id=book.id,
                chapter_id=ch.id,
                status=PacketStatus.PROPOSED,
                confidence="green",
                body={"scene_seeds": []},
                open_questions=oq.normalize({"items": ["who paid?"]}, mint=True),
            )
        )
        scene = Scene(chapter_id=ch.id, scene_no=1, version=1, status=SceneStatus.PENDING_REVIEW, prose="x")
        s.add(scene)
        await s.flush()
        scene_id = scene.id
        await s.commit()

    async with db_factory() as s2:
        await accept_scene_approval(s2, scene_id=scene_id, edited_prose=None, target_pass=None, feedback=None)
        await s2.commit()

    status, _ = await _scene_state(db_factory, scene_id)
    assert status == str(SceneStatus.APPROVED), "a merely PROPOSED contract blocked scene approval"


# =================================================================================================
# C3 — the seed CLI
# =================================================================================================


async def test_seed_takes_the_chapter_lock_before_the_approved_write(db_factory, monkeypatch, tmp_path):
    """6. The C3 defect was the missing lock, so the test is about ORDER, not about the status.

    Recording the call sequence is what makes this non-hollow: asserting only that the lock function ran
    would still pass if it ran *after* the write, which is the same defect with extra steps.
    """
    events: list[str] = []
    real_lock = seed_mod.acquire_chapter_workflow_lock
    real_upsert = seed_mod._upsert_seed_scene

    async def spy_lock(session, chapter_id, **kw):
        events.append(f"lock:{chapter_id}")
        return await real_lock(session, chapter_id, **kw)

    async def spy_upsert(session, **kw):
        events.append(f"write:{kw['chapter_id']}")
        return await real_upsert(session, **kw)

    monkeypatch.setattr(seed_mod, "acquire_chapter_workflow_lock", spy_lock)
    monkeypatch.setattr(seed_mod, "_upsert_seed_scene", spy_upsert)

    scenes_dir = tmp_path / "scenes"
    scenes_dir.mkdir()
    (scenes_dir / "ch01_s01.md").write_text(
        "---\nchapter: 1\nscene: 1\npov: Marcus\ntitle: Crossing\n---\n\nThe courier crossed at dusk.\n",
        encoding="utf-8",
    )

    async with db_factory() as s:
        await seed_mod.seed_manuscript(
            s,
            book_title="Dominion Realm",
            scenes_dir=scenes_dir,
            canon_dir=None,
            build_summaries=False,
        )
        await s.commit()

    assert events, "the seed never reached the scene write"
    assert events[0].startswith("lock:"), f"the APPROVED write was not preceded by the chapter lock: {events}"
    lock_chapter = events[0].split(":", 1)[1]
    write_chapter = events[1].split(":", 1)[1]
    assert lock_chapter == write_chapter, f"locked a different chapter than it wrote: {events}"


async def test_seeded_scenes_are_still_approved(db_factory, tmp_path):
    """7. The scope boundary, pinned. C3 was closed as HUMAN, not GATED — the operator running the CLI
    is the authority for prose they already wrote. If someone later gates this on a contract, this test
    fails and forces the ruling to be explicit rather than incidental.
    """
    scenes_dir = tmp_path / "scenes"
    scenes_dir.mkdir()
    (scenes_dir / "ch01_s01.md").write_text(
        "---\nchapter: 1\nscene: 1\npov: Marcus\ntitle: Crossing\n---\n\nThe courier crossed at dusk.\n",
        encoding="utf-8",
    )
    async with db_factory() as s:
        await seed_mod.seed_manuscript(
            s,
            book_title="Dominion Realm",
            scenes_dir=scenes_dir,
            canon_dir=None,
            build_summaries=False,
        )
        await s.commit()

    async with db_factory() as s2:
        scene = (await s2.execute(select(Scene).where(Scene.prose_source == seed_mod._SEED_SOURCE))).scalar_one()
        assert str(scene.status) == str(SceneStatus.APPROVED)
