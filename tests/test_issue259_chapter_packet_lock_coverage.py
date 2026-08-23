"""#259 residual 1 — chapter-tier ChapterPacket transitions run under the chapter workflow lock.

The failure this pins: `api/routers/packets.py` never imported `chapter_lock`, so the four
authority-changing ChapterPacket transitions mutated and committed with no serialization at all —
contradicting `shared/chapter_lock.py:5-6` ("ChapterPacket propose/replace/approve/supersede") and
ADR-0028's "All authority-changing operations acquire a per-chapter transaction-level advisory lock".
Amendment mode (#261) depends on this coverage, because an atomic supersede cannot be built on a
transition that a concurrent writer can interleave with.

What this suite pins:
  * ADR-0028 — approve / update / delete refuse with `409 chapter_workflow_busy` while the chapter
    lock is held, write NOTHING, and succeed on retry once it frees. Four properties per transition.
  * ADR-0028 mandatory order — the lock is taken BEFORE the row is read, so a busy chapter never
    reaches its rows. Proven by asserting the row is untouched, not merely that a 409 came back.
  * chapter_lock.py:20-22 — the authoring path must NOT hold the lock across the model calls. The
    propose path therefore locks only its short `_persist` write; the LLM work stays outside.
  * chapter_lock.py:55-61 — the 409 body is the shared `BUSY_DETAIL`, not a re-typed dict.

Every acceptance assertion here goes through `app_client` — the real ASGI app and the real routes,
not a router coroutine — so what is proven is the wire contract. No existing test in this repo drives
the `/chapters/{id}/packet*` routes over HTTP at all; that gap is why the drift went unnoticed.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from dominion.api.routers import packets as packets_router
from dominion.shared.chapter_lock import ChapterWorkflowBusy, acquire_chapter_workflow_lock
from dominion.shared.enums import PacketStatus, PacketVerdict
from dominion.shared.models import Book, Chapter, ChapterPacket, ImportAdoption
from dominion.workers import packet as packet_pipeline
from dominion.workers.import_adoption import run_one_adoption
from dominion.workers.packet import author as author_mod
from dominion.workers.packet import qa as qa_mod


def _seed_body(seed_id: str) -> dict:
    return {"scene_seeds": [{"seed_id": seed_id, "scene_no": 1, "scene_job": "do the thing"}]}


async def _seed_chapter_packet(s, *, status: str = "proposed", open_questions: list | None = None):
    """One book + chapter + a single ChapterPacket. Returns (book, chapter, packet)."""
    book = Book(title="Lock259")
    s.add(book)
    await s.flush()
    ch = Chapter(book_id=book.id, chapter_no=1, pov="Marcus")
    s.add(ch)
    await s.flush()
    cp = ChapterPacket(
        book_id=book.id,
        chapter_id=ch.id,
        status=status,
        confidence="green",
        body=_seed_body(str(uuid.uuid4())),
        open_questions={"items": open_questions or []},
    )
    s.add(cp)
    await s.flush()
    return book, ch, cp


async def _packet_status(db_factory, chapter_id: uuid.UUID) -> str | None:
    async with db_factory() as probe:
        return (
            await probe.execute(select(ChapterPacket.status).where(ChapterPacket.chapter_id == chapter_id))
        ).scalar_one_or_none()


async def _packet_count(db_factory, chapter_id: uuid.UUID) -> int:
    async with db_factory() as probe:
        return (
            await probe.execute(
                select(func.count()).select_from(ChapterPacket).where(ChapterPacket.chapter_id == chapter_id)
            )
        ).scalar_one()


# ---------- approve: the authority transition (#259 residual 1, the named site)


async def test_approve_under_held_chapter_lock_is_409_and_writes_nothing(app_client, db_factory, monkeypatch):
    """A held chapter lock must stop approval BEFORE it reads the row — the packet stays `proposed`."""
    monkeypatch.setattr(packets_router, "LOCK_TIMEOUT_MS", 250)
    async with db_factory() as s:
        _, ch, _ = await _seed_chapter_packet(s)
        await s.commit()
        chapter_id = ch.id

    async with db_factory() as holder:
        await acquire_chapter_workflow_lock(holder, chapter_id, timeout_ms=None)

        resp = await app_client.post(f"/chapters/{chapter_id}/packet/approve")
        assert resp.status_code == 409, resp.text
        assert resp.json()["detail"]["reason"] == "chapter_workflow_busy"

        # The row was never reached, not merely "not approved".
        assert await _packet_status(db_factory, chapter_id) == PacketStatus.PROPOSED.value

        await holder.rollback()

    retry = await app_client.post(f"/chapters/{chapter_id}/packet/approve")
    assert retry.status_code == 200, retry.text
    assert await _packet_status(db_factory, chapter_id) == PacketStatus.APPROVED.value


# ---------- update: open questions and confidence are approval-gating inputs


async def test_update_under_held_chapter_lock_is_409_and_writes_nothing(app_client, db_factory, monkeypatch):
    """`update_packet` writes open_questions/confidence — approval-gating state — so it serializes too."""
    monkeypatch.setattr(packets_router, "LOCK_TIMEOUT_MS", 250)
    async with db_factory() as s:
        _, ch, _ = await _seed_chapter_packet(s, open_questions=["who holds the blade?"])
        await s.commit()
        chapter_id = ch.id

    async with db_factory() as holder:
        await acquire_chapter_workflow_lock(holder, chapter_id, timeout_ms=None)

        resp = await app_client.put(f"/chapters/{chapter_id}/packet", json={"open_questions": {"items": []}})
        assert resp.status_code == 409, resp.text
        assert resp.json()["detail"]["reason"] == "chapter_workflow_busy"

        async with db_factory() as probe:
            oq = (
                await probe.execute(select(ChapterPacket.open_questions).where(ChapterPacket.chapter_id == chapter_id))
            ).scalar_one()
        assert oq["items"] == ["who holds the blade?"]  # untouched

        await holder.rollback()

    # #277 clause B: a write that changes open questions must echo the token it read (absent -> 422).
    # Fetch it first, then retry — which is also the real Desk flow now.
    token = (await app_client.get(f"/chapters/{chapter_id}/packet")).json()["open_questions_token"]
    retry = await app_client.put(
        f"/chapters/{chapter_id}/packet",
        json={"open_questions": {"items": [], "resolved": []}, "expected_open_questions_token": token},
    )
    assert retry.status_code == 200, retry.text


# ---------- delete: destroys authority state and cascades


async def test_delete_under_held_chapter_lock_is_409_and_writes_nothing(app_client, db_factory, monkeypatch):
    """Delete destroys the packet and cascades to ScenePackets/jobs — the widest blast radius here."""
    monkeypatch.setattr(packets_router, "LOCK_TIMEOUT_MS", 250)
    async with db_factory() as s:
        _, ch, _ = await _seed_chapter_packet(s)
        await s.commit()
        chapter_id = ch.id

    async with db_factory() as holder:
        await acquire_chapter_workflow_lock(holder, chapter_id, timeout_ms=None)

        resp = await app_client.delete(f"/chapters/{chapter_id}/packet")
        assert resp.status_code == 409, resp.text
        assert resp.json()["detail"]["reason"] == "chapter_workflow_busy"
        assert await _packet_count(db_factory, chapter_id) == 1  # nothing destroyed

        await holder.rollback()

    retry = await app_client.delete(f"/chapters/{chapter_id}/packet")
    assert retry.status_code == 200, retry.text
    assert await _packet_count(db_factory, chapter_id) == 0


# ---------- the shared 409 body, not a re-typed dict


async def test_busy_body_is_the_shared_constant(app_client, db_factory, monkeypatch):
    """chapter_lock.BUSY_DETAIL exists so one condition cannot grow two messages. Pin both keys."""
    from dominion.shared.chapter_lock import BUSY_DETAIL

    monkeypatch.setattr(packets_router, "LOCK_TIMEOUT_MS", 250)
    async with db_factory() as s:
        _, ch, _ = await _seed_chapter_packet(s)
        await s.commit()
        chapter_id = ch.id

    async with db_factory() as holder:
        await acquire_chapter_workflow_lock(holder, chapter_id, timeout_ms=None)
        resp = await app_client.post(f"/chapters/{chapter_id}/packet/approve")
        assert resp.json()["detail"] == BUSY_DETAIL
        await holder.rollback()


# ---------- the approved-packet guard is decided UNDER the lock, not before it


async def test_persist_preserves_an_approved_packet_decided_under_the_lock(db_factory):
    """ADR-0028 protocol steps 3-4: reload and revalidate INSIDE the lock, then write.

    `_make_fail_closed` checks `latest_approved` before `_persist` acquires the lock, so an
    `approve_packet` committing in that window would have been destroyed by the `replace=True` delete —
    the check was not serialized against the thing it guards against. `_persist(preserve_approved=True)`
    now re-decides under the lock. Simulated by approving AFTER the caller's fast-path read would have
    run, which is exactly the race: the approved row must survive and be returned."""
    async with db_factory() as s:
        book, ch, cp = await _seed_chapter_packet(s, status=PacketStatus.APPROVED.value)
        await s.commit()
        chapter_id, book_id, approved_id = ch.id, book.id, cp.id

    async with db_factory() as s:
        replacement = ChapterPacket(
            book_id=book_id,
            chapter_id=chapter_id,
            status="blocked",
            confidence="red",
            body=_seed_body(str(uuid.uuid4())),
            open_questions={"items": []},
        )
        returned = await packet_pipeline._persist(
            s, chapter_id=chapter_id, row=replacement, replace=True, preserve_approved=True
        )
        await s.commit()
        assert returned.id == approved_id, "the approved packet must be returned, not replaced"

    assert await _packet_count(db_factory, chapter_id) == 1
    assert await _packet_status(db_factory, chapter_id) == PacketStatus.APPROVED.value


# ---------- unknown chapter still 404s, and the lock never invents a row


async def test_approve_on_unknown_chapter_is_still_404(app_client, db_factory):
    """Taking the lock before the read must not turn a missing packet into a 200 or a 409."""
    resp = await app_client.post(f"/chapters/{uuid.uuid4()}/packet/approve")
    assert resp.status_code == 404, resp.text


# ---------- the authoring path: the replace write is locked, the model calls are NOT


async def test_persist_serializes_the_replace_write(db_factory, monkeypatch):
    """`_persist` is the only ChapterPacket INSERT/replace and is reached by both propose paths, so it
    must serialize — while the 1-2min authoring above it stays outside the lock."""
    monkeypatch.setattr(packet_pipeline, "PERSIST_LOCK_TIMEOUT_MS", 250)
    async with db_factory() as s:
        book, ch, _ = await _seed_chapter_packet(s)
        await s.commit()
        chapter_id, book_id = ch.id, book.id

    async with db_factory() as holder:
        await acquire_chapter_workflow_lock(holder, chapter_id, timeout_ms=None)
        async with db_factory() as writer:
            row = ChapterPacket(
                book_id=book_id,
                chapter_id=chapter_id,
                status="proposed",
                confidence="green",
                body=_seed_body(str(uuid.uuid4())),
                open_questions={"items": []},
            )
            with pytest.raises(ChapterWorkflowBusy):
                await packet_pipeline._persist(writer, chapter_id=chapter_id, row=row, replace=True)
            await writer.rollback()

        # The replace never ran: the pre-existing packet is still there.
        assert await _packet_count(db_factory, chapter_id) == 1
        await holder.rollback()


async def test_adoption_author_phase_requeues_on_busy_instead_of_breaking_the_drain(db_factory, monkeypatch):
    """Putting the lock in `_persist` gave the adoption worker's AUTHOR phase a new way to fail busy.

    Phase 5 already requeued on `ChapterWorkflowBusy`; phase 4 did not, so a transient contention would
    have escaped to `drain_adoptions`' blanket `except Exception`, logged `adoption.drain_error`, and
    stopped the whole drain pass — with retry left to lease expiry. Driven through the real
    `run_one_adoption`, with a genuinely held advisory lock; nothing about the lock is faked."""
    # Reuse the harness that already drives the real worker end to end, rather than inventing one.
    from test_import_adoption import _adoption, _author_packet, _fixed_retriever, _patch_author, _seed

    from dominion.workers.import_evidence import FakeImportEvidenceExtractor

    monkeypatch.setattr(packet_pipeline, "PERSIST_LOCK_TIMEOUT_MS", 250)
    _patch_author(monkeypatch, packet=_author_packet(1))
    async with db_factory() as s:
        book, ch, _scenes = await _seed(s, n_scenes=1)
        s.add(_adoption(book, ch))
        await s.commit()
        chapter_id = ch.id

    async with db_factory() as holder:
        await acquire_chapter_workflow_lock(holder, chapter_id, timeout_ms=None)

        did = await run_one_adoption(db_factory, extractor=FakeImportEvidenceExtractor(), retrieve=_fixed_retriever([]))
        # True == "I handled one; keep draining" — NOT an exception that stops the pass.
        assert did is True

        async with db_factory() as probe:
            adoption = (await probe.execute(select(ImportAdoption))).scalar_one()
            assert adoption.status == "queued", "busy at the author persist must requeue, not strand"
            assert adoption.chapter_packet_id is None
        assert await _packet_count(db_factory, chapter_id) == 0, (
            "no packet may be written when the chapter lock was never granted"
        )

        await holder.rollback()


async def test_api_propose_surfaces_a_busy_chapter_instead_of_swallowing_it(app_client, db_factory, monkeypatch):
    """Regression for the hole #259's own change opened on the API side.

    `_persist` now takes the chapter lock, so a background propose can lose it. `_run_propose`'s
    blanket `except Exception` would have logged `packet.propose_bg_failed` and stopped — 1-2 min of
    paid model work gone, NO packet (not even the fail-closed blocked one, which also persists through
    `_persist`), no retry, and nothing distinguishing it from a crash.

    Two things are asserted, because the fix has two halves and an earlier version of this test only
    covered one: (a) the acquire is RETRIED a bounded number of times rather than abandoned on the
    first busy, and (b) the outcome is reported through the DISTINCT `packet.propose_chapter_busy`
    event, not the generic `packet.propose_bg_failed`. Deleting either half fails this test.

    NB there is deliberately no assertion about `GET .../packet/status`: `background_work.schedule`
    clears the phase in its `finally`, so no phase survives. That limit is documented at the catch
    site rather than papered over here."""
    monkeypatch.setattr(packet_pipeline, "PERSIST_LOCK_TIMEOUT_MS", 250)
    monkeypatch.setattr(packet_pipeline, "PERSIST_LOCK_RETRY_S", 0.01)

    attempts = {"n": 0}
    real_acquire = packet_pipeline.acquire_chapter_workflow_lock

    async def counting_acquire(session, chapter_id_, *, timeout_ms=None):
        attempts["n"] += 1
        return await real_acquire(session, chapter_id_, timeout_ms=timeout_ms)

    monkeypatch.setattr(packet_pipeline, "acquire_chapter_workflow_lock", counting_acquire)

    events: list[str] = []
    real_warning = packets_router.log.warning

    def capture_warning(event, **kw):
        events.append(event)
        return real_warning(event, **kw)

    monkeypatch.setattr(packets_router.log, "warning", capture_warning)

    async def fake_author(**kwargs):
        return {
            "confidence": "green",
            "chapter_job": "j",
            "exit_state": "e",
            "scene_seeds": [{"scene_no": 1, "scene_job": "do it"}],
            "claims": [],
            "open_questions": [],
        }

    async def fake_qa(_packet, **kwargs):
        return {"verdict": PacketVerdict.APPROVE, "residual_risks": [], "issues": []}

    monkeypatch.setattr(author_mod, "author_packet", fake_author)
    monkeypatch.setattr(qa_mod, "qa_packet", fake_qa)

    async with db_factory() as s:
        book = Book(title="Lock259Busy")
        s.add(book)
        await s.flush()
        ch = Chapter(book_id=book.id, chapter_no=1, pov="Marcus", outline="Marcus intercepts.")
        s.add(ch)
        await s.commit()
        chapter_id = ch.id

    async with db_factory() as holder:
        await acquire_chapter_workflow_lock(holder, chapter_id, timeout_ms=None)

        # app_client runs BackgroundTasks for real, so the propose actually executes and loses the race.
        resp = await app_client.post(f"/chapters/{chapter_id}/packet")
        assert resp.status_code == 200, resp.text

        # It must not give up on the first busy: losing the acquire throws away the whole authored
        # pass, so the write retries a bounded number of times before surfacing.
        assert attempts["n"] == packet_pipeline.PERSIST_LOCK_ATTEMPTS, (
            f"expected {packet_pipeline.PERSIST_LOCK_ATTEMPTS} bounded acquire attempts, got {attempts['n']}"
        )
        # (b) reported distinctly, not swallowed as a generic background crash.
        assert "packet.propose_chapter_busy" in events, (
            f"a lost chapter lock must be reported distinctly by its own handler (warnings seen: {events})"
        )
        assert await _packet_count(db_factory, chapter_id) == 0  # nothing half-written

        await holder.rollback()

    # And once the lock frees, a re-triggered propose succeeds — the busy was transient, not terminal.
    retry = await app_client.post(f"/chapters/{chapter_id}/packet")
    assert retry.status_code == 200, retry.text
    assert await _packet_count(db_factory, chapter_id) == 1


async def test_propose_does_not_hold_the_lock_across_the_model_calls(db_factory, monkeypatch):
    """chapter_lock.py:20-22 — the lock is taken inside the short write, NOT around the model work.

    Proven from inside the real `propose_packet` run: the patched author probes the chapter lock at
    author time from a separate session. If the lock were held around authoring, that probe would
    raise `ChapterWorkflowBusy`. A regression that wrapped the whole propose would fail here, and
    would otherwise be invisible until an operator's PUT started 409-ing for two minutes."""
    chapter_id = uuid.uuid4()  # rebound below, before propose runs
    seen: dict[str, bool] = {}

    async def probing_author(**kwargs):
        async with db_factory() as probe:
            try:
                await acquire_chapter_workflow_lock(probe, chapter_id, timeout_ms=250)
                seen["lock_free_during_authoring"] = True
            except ChapterWorkflowBusy:
                seen["lock_free_during_authoring"] = False
            finally:
                await probe.rollback()
        return {
            "confidence": "green",
            "chapter_job": "Marcus intercepts the rogue courier",
            "exit_state": "the duel begins",
            "scene_seeds": [{"scene_no": 1, "scene_job": "Marcus reads the route and intercepts."}],
            "claims": [{"claim": "Realm is real", "source_strength": "LOCKED_CANON", "source_id": "C1"}],
            "open_questions": [],
        }

    async def fake_qa(_packet, **kwargs):
        return {"verdict": PacketVerdict.APPROVE, "residual_risks": [], "issues": []}

    monkeypatch.setattr(author_mod, "author_packet", probing_author)
    monkeypatch.setattr(qa_mod, "qa_packet", fake_qa)

    async with db_factory() as s:
        book = Book(title="Lock259Author")
        s.add(book)
        await s.flush()
        ch = Chapter(book_id=book.id, chapter_no=1, pov="Marcus", outline="Marcus intercepts.")
        s.add(ch)
        await s.commit()
        chapter_id = ch.id

    async with db_factory() as s:
        chapter = await s.get(Chapter, chapter_id)
        await packet_pipeline.propose_packet(s, chapter=chapter)
        await s.commit()

    assert seen["lock_free_during_authoring"] is True
    assert await _packet_count(db_factory, chapter_id) == 1  # and the write still landed
