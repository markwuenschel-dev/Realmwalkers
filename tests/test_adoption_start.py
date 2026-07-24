"""Oracles for the operator "Start contract adoption" endpoint (ADR 0028, Slice 3b — Lane A5).

Inbound ASGI (`app_client`) + direct-DB (`db_factory`), so each test exercises real routing, `SessionDep`
injection, the per-chapter workflow lock, and response serialization against the truncated test database.
Needs Postgres (skips locally, runs under CI / `just test`).

Coverage:
  * Start on an evidence-only chapter creates a `queued` INITIAL adoption;
  * a second Start is idempotent — the in-flight `queued` row is returned, never duplicated;
  * Start promotes an existing `awaiting_start` adoption to `queued` (Q17) without creating a new row;
  * Start on a chapter with any contracted scene is a typed `chapter_has_contracted_scenes` refuse (Q6);
  * Start on an unknown chapter is a 404;
  * a held per-chapter workflow lock maps to `409 chapter_workflow_busy` (Q16), writes nothing, and the
    retry after the lock frees succeeds.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select

from dominion.api.routers import adoption as adoption_router
from dominion.shared.chapter_lock import acquire_chapter_workflow_lock
from dominion.shared.enums import SceneStatus
from dominion.shared.models import Book, Chapter, ChapterPacket, ImportAdoption, Scene, ScenePacket

# ----------------------------------------- seed helpers ------------------------------------------ #


async def _seed_evidence_only(s, *, n_scenes: int = 2, pov: str = "Marcus") -> tuple[Book, Chapter, list[Scene]]:
    """A chapter of purely imported, uncontracted scenes (prose, no `scene_packet_id`) — the eligible
    Start case."""
    book = Book(title="Adoption Start")
    s.add(book)
    await s.flush()
    ch = Chapter(book_id=book.id, chapter_no=1, pov=pov, outline=None)
    s.add(ch)
    await s.flush()
    scenes = []
    for i in range(1, n_scenes + 1):
        sc = Scene(
            chapter_id=ch.id,
            scene_no=i,
            version=1,
            prose=f"Imported prose for scene {i}. The gate at the vault stands open.",
            status=SceneStatus.PENDING_REVIEW,
        )
        s.add(sc)
        scenes.append(sc)
    await s.flush()
    return book, ch, scenes


async def _contract_scene(s, book: Book, ch: Chapter, scene: Scene) -> None:
    """Bind `scene` to an approved ScenePacket of record — making the chapter no longer evidence-only."""
    cp = ChapterPacket(
        book_id=book.id, chapter_id=ch.id, status="approved", body={"scene_seeds": []}, open_questions={"items": []}
    )
    s.add(cp)
    await s.flush()
    sp = ScenePacket(
        book_id=book.id,
        chapter_id=ch.id,
        chapter_packet_id=cp.id,
        scene_no=scene.scene_no,
        status="approved",
        body={"scene_no": scene.scene_no},
        source_hash="test",
    )
    s.add(sp)
    await s.flush()
    scene.scene_packet_id = sp.id
    await s.flush()


async def _count(s, model) -> int:
    return (await s.execute(select(func.count()).select_from(model))).scalar_one()


# --------------------------------------------- oracles ------------------------------------------- #


async def test_start_on_evidence_only_chapter_queues_adoption(app_client, db_factory):
    """Start on an evidence-only chapter creates a `queued` INITIAL adoption with a captured source
    fingerprint — the durable spend-consent record the worker drains."""
    async with db_factory() as s:
        _, ch, _ = await _seed_evidence_only(s, n_scenes=2)
        await s.commit()
        chapter_id = ch.id

    resp = await app_client.post(f"/chapters/{chapter_id}/adoption/start")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "queued"
    assert data["mode"] == "initial"
    assert data["chapter_id"] == str(chapter_id)
    assert data["chapter_packet_id"] is None

    async with db_factory() as s:
        adoption = (await s.execute(select(ImportAdoption))).scalar_one()
        assert str(adoption.id) == data["id"]
        assert adoption.status == "queued"
        assert adoption.mode == "initial"
        assert adoption.source_fingerprint  # captured, non-empty (Q10)


async def test_start_is_idempotent_for_an_already_queued_chapter(app_client, db_factory):
    """A second Start over a chapter already being adopted returns the SAME in-flight row — no duplicate
    spend."""
    async with db_factory() as s:
        _, ch, _ = await _seed_evidence_only(s)
        await s.commit()
        chapter_id = ch.id

    first = await app_client.post(f"/chapters/{chapter_id}/adoption/start")
    second = await app_client.post(f"/chapters/{chapter_id}/adoption/start")
    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert second.json()["status"] == "queued"

    async with db_factory() as s:
        assert await _count(s, ImportAdoption) == 1  # no duplicate adoption


async def test_start_promotes_awaiting_start_to_queued(app_client, db_factory):
    """Q17: Start promotes an existing `awaiting_start` adoption to `queued` in place, rather than
    creating a second row."""
    async with db_factory() as s:
        book, ch, _ = await _seed_evidence_only(s)
        adoption = ImportAdoption(
            book_id=book.id,
            chapter_id=ch.id,
            mode="initial",
            status="awaiting_start",
            source_fingerprint="seed",
            liveness_basis="operator_independent",
            error="was awaiting an explicit start",
        )
        s.add(adoption)
        await s.commit()
        chapter_id, adoption_id = ch.id, adoption.id

    resp = await app_client.post(f"/chapters/{chapter_id}/adoption/start")
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == str(adoption_id)
    assert resp.json()["status"] == "queued"

    async with db_factory() as s:
        assert await _count(s, ImportAdoption) == 1  # promoted, not duplicated
        promoted = await s.get(ImportAdoption, adoption_id)
        assert promoted.status == "queued"
        assert promoted.error is None


async def test_start_on_mixed_chapter_is_refused(app_client, db_factory):
    """Q6: a chapter with any contracted scene is not evidence-only — Start is a typed
    `chapter_has_contracted_scenes` refuse (409) and writes no adoption."""
    async with db_factory() as s:
        book, ch, scenes = await _seed_evidence_only(s, n_scenes=2)
        await _contract_scene(s, book, ch, scenes[0])  # one contracted scene ⇒ mixed
        await s.commit()
        chapter_id = ch.id

    resp = await app_client.post(f"/chapters/{chapter_id}/adoption/start")
    assert resp.status_code == 409
    assert resp.json()["detail"]["reason"] == "chapter_has_contracted_scenes"

    async with db_factory() as s:
        assert await _count(s, ImportAdoption) == 0  # nothing queued


async def test_start_on_unknown_chapter_is_404(app_client, db_factory):
    resp = await app_client.post(f"/chapters/{uuid.uuid4()}/adoption/start")
    assert resp.status_code == 404


async def test_start_maps_chapter_workflow_busy_to_409(app_client, db_factory, monkeypatch):
    """Q16: when the per-chapter workflow lock is held, Start maps `ChapterWorkflowBusy` to
    `409 chapter_workflow_busy`, writes nothing, and the retry after the lock frees succeeds."""
    monkeypatch.setattr(adoption_router, "LOCK_TIMEOUT_MS", 250)  # keep the busy wait short
    async with db_factory() as s:
        _, ch, _ = await _seed_evidence_only(s)
        await s.commit()
        chapter_id = ch.id

    async with db_factory() as holder:
        await acquire_chapter_workflow_lock(holder, chapter_id, timeout_ms=None)  # hold the lock

        resp = await app_client.post(f"/chapters/{chapter_id}/adoption/start")
        assert resp.status_code == 409
        assert resp.json()["detail"]["reason"] == "chapter_workflow_busy"

        async with db_factory() as probe:  # nothing was written on the busy path
            assert await _count(probe, ImportAdoption) == 0

        await holder.rollback()  # release the advisory lock

    retry = await app_client.post(f"/chapters/{chapter_id}/adoption/start")
    assert retry.status_code == 200, retry.text
    assert retry.json()["status"] == "queued"
