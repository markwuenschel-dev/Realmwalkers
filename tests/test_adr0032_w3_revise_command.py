"""ADR-0032 W3 — the revise command coordinator, driven over the REAL HTTP API.

W3 closes the gap ADR-0028 left open: an author revising an imported, uncontracted scene got a durable
`RevisionRequest` that nothing could ever advance, because no adoption was minted to build the contract
it waits for. W3 routes both Revise surfaces through one chapter-locked coordinator that commits the
request AND its adoption entry atomically.

What this suite pins:
  * D4  — one atomic transaction over two single owners: an injected mid-sequence failure leaves
          NEITHER the request nor the adoption behind;
  * D1  — the adoption the sync path mints is `queued` + `request_bound` (spend consent, request-owned
          survival), never `operator_independent`;
  * D5  — an exact request REPLAY still reconciles adoption entry (promotes `awaiting_start`→`queued`),
          so a fresh Revise click is never stuck behind operator Start;
  * D11 — the typed envelope, and 200 ONLY for a genuinely inert replay; 202 otherwise;
  * D6  — an ineligible (contracted / already-approved) chapter fails closed and persists nothing;
  * D9  — the reverse-cancel wired dormant in W2 is now LIVE against a real request_bound adoption;
  * chapter-shared adoption — two scenes in one chapter share ONE adoption row (D4).

Every acceptance assertion here goes through `app_client` (ASGI routing, `Depends`, response-model
serialization, real status codes), not a router coroutine, so the wire contract is what is proven.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from dominion.shared.enums import (
    ForwardEffect,
    ImportAdoptionStatus,
    LivenessBasis,
    RequestDisposition,
    RevisionRequestOrigin,
    RevisionRequestStatus,
    SceneStatus,
)
from dominion.shared.models import (
    Approval,
    Book,
    Chapter,
    ChapterPacket,
    Critique,
    ImportAdoption,
    RevisionRequest,
    Scene,
    ScenePacket,
)
from dominion.workers import import_adoption as import_adoption_worker
from dominion.workers.revision import prose_hash


@pytest.fixture(autouse=True)
def captured_drains(monkeypatch):
    """W3 kicks the adoption drain from a FastAPI BackgroundTask, and `app_client` runs background
    tasks for real — which would claim the adoption (`queued`→`running`) and then reach for a live
    model. Record the kicks instead of running them; `test_revise_kicks_the_adoption_drain` asserts on
    this list, so the wiring is proven exactly once rather than re-run in every case."""
    kicks: list[str] = []

    async def _record() -> None:
        kicks.append("drain_adoptions")

    monkeypatch.setattr(import_adoption_worker, "drain_adoptions", _record)
    return kicks


async def _seed(s, *, scenes: int = 1, prose: str = "Imported prose.") -> tuple[Book, Chapter, list[Scene]]:
    """An evidence-only imported chapter: real prose, no ScenePacket of record anywhere."""
    book = Book(title="ADR-0032 W3")
    s.add(book)
    await s.flush()
    ch = Chapter(book_id=book.id, chapter_no=1, pov="Marcus")
    s.add(ch)
    await s.flush()
    made = []
    for n in range(1, scenes + 1):
        sc = Scene(chapter_id=ch.id, scene_no=n, prose=f"{prose} ({n})", version=1, status=SceneStatus.PENDING_REVIEW)
        s.add(sc)
        made.append(sc)
    await s.flush()
    return book, ch, made


def _revise_body(scene: Scene, *, feedback: str = "tighten the open") -> dict:
    return {
        "decision": "revise",
        "feedback": feedback,
        "expected_prose_hash": prose_hash(scene.prose),
    }


# --------------------------------------------------------------------------- D1/D4: the forward path


async def test_revise_on_imported_scene_creates_request_and_adoption_atomically(app_client, db_factory):
    """THE ADR-0028 motivating case, end to end over HTTP: revising an imported scene now leaves a
    durable request AND the queued adoption that will build its contract, linked to each other."""
    async with db_factory() as s:
        _, ch, (scene,) = await _seed(s)
        await s.commit()
        scene_id, chapter_id, body = scene.id, ch.id, _revise_body(scene)

    resp = await app_client.post(f"/scenes/{scene_id}/decision", json=body)
    assert resp.status_code == 202
    payload = resp.json()
    assert payload["request_disposition"] == RequestDisposition.CREATED.value
    assert payload["forward_effect"] == ForwardEffect.ADOPTION_CREATED.value
    assert payload["request"]["status"] == RevisionRequestStatus.AWAITING_CONTRACT.value
    assert payload["request"]["display_phase"] == "Preparing contract"  # the durable resource is unchanged

    async with db_factory() as s:
        req = (await s.execute(select(RevisionRequest))).scalar_one()
        adoption = (await s.execute(select(ImportAdoption))).scalar_one()
        assert req.import_adoption_id == adoption.id  # serving/provenance link (D4)
        assert req.origin == RevisionRequestOrigin.REVIEW.value
        # D1: a Revise IS spend consent -> queued (worker-claimable), but the REQUEST is why it lives.
        assert adoption.status == ImportAdoptionStatus.QUEUED.value
        assert adoption.liveness_basis == LivenessBasis.REQUEST_BOUND.value
        assert adoption.chapter_id == chapter_id
        assert (await s.get(Scene, scene_id)).status == SceneStatus.REVISION_REQUESTED
        # The source Approval is recorded in the same transaction (ADR-0028 rollback guarantee).
        assert (await s.execute(select(func.count()).select_from(Approval))).scalar_one() == 1


async def test_mid_sequence_failure_rolls_back_both_owners(app_client, db_factory, monkeypatch):
    """D14: an imported/uncontracted RevisionRequest must NEVER commit without its adoption entry. Fail
    the adoption owner AFTER the revision owner has already written, and prove both vanish."""
    from dominion.api.routers import reviews

    async with db_factory() as s:
        _, _, (scene,) = await _seed(s)
        await s.commit()
        scene_id, body = scene.id, _revise_body(scene)

    async def _boom(*_a, **_k):
        raise RuntimeError("adoption owner exploded mid-transaction")

    monkeypatch.setattr(reviews, "ensure_import_adoption_locked", _boom)
    with pytest.raises(RuntimeError):
        await app_client.post(f"/scenes/{scene_id}/decision", json=body)

    async with db_factory() as s:
        assert (await s.execute(select(func.count()).select_from(RevisionRequest))).scalar_one() == 0
        assert (await s.execute(select(func.count()).select_from(ImportAdoption))).scalar_one() == 0
        assert (await s.execute(select(func.count()).select_from(Approval))).scalar_one() == 0
        assert (await s.get(Scene, scene_id)).status == SceneStatus.PENDING_REVIEW  # not even the status moved


async def test_two_scenes_in_one_chapter_share_one_adoption(app_client, db_factory):
    """Adoption is CHAPTER-shared (D4): the second scene's request JOINS the first's adoption rather
    than minting a rival active row, and both requests link to it."""
    async with db_factory() as s:
        _, ch, scenes = await _seed(s, scenes=2)
        await s.commit()
        ids = [(sc.id, _revise_body(sc)) for sc in scenes]

    first = await app_client.post(f"/scenes/{ids[0][0]}/decision", json=ids[0][1])
    second = await app_client.post(f"/scenes/{ids[1][0]}/decision", json=ids[1][1])
    assert first.status_code == 202 and second.status_code == 202
    assert first.json()["forward_effect"] == ForwardEffect.ADOPTION_CREATED.value
    # Already `queued`: nothing to create or promote, but THIS request newly attached to it.
    assert second.json()["forward_effect"] == ForwardEffect.ADOPTION_JOINED.value

    async with db_factory() as s:
        adoption = (await s.execute(select(ImportAdoption))).scalar_one()  # exactly ONE row
        reqs = (await s.execute(select(RevisionRequest))).scalars().all()
        assert len(reqs) == 2
        assert {r.import_adoption_id for r in reqs} == {adoption.id}


# --------------------------------------------------------------------------- D5 / D11: replay semantics


async def test_replay_against_awaiting_start_promotes_it_and_answers_202(app_client, db_factory):
    """D5: the request replays, but adoption entry still reconciles. A fresh explicit Revise must never
    be stuck behind operator Start merely because the REQUEST was already there."""
    async with db_factory() as s:
        book, ch, (scene,) = await _seed(s)
        s.add(
            RevisionRequest(
                book_id=book.id,
                chapter_id=ch.id,
                target_scene_id=scene.id,
                scene_no=scene.scene_no,
                target_scene_version=scene.version,
                target_prose_hash=prose_hash(scene.prose),
                feedback="tighten the open",
                origin=RevisionRequestOrigin.REVIEW.value,
                status=RevisionRequestStatus.AWAITING_CONTRACT.value,
            )
        )
        # A reconciliation-minted adoption: recorded intent, NOT worker-claimable.
        s.add(
            ImportAdoption(
                book_id=book.id,
                chapter_id=ch.id,
                status=ImportAdoptionStatus.AWAITING_START.value,
                liveness_basis=LivenessBasis.REQUEST_BOUND.value,
                source_fingerprint="w3",
            )
        )
        await s.commit()
        scene_id, body = scene.id, _revise_body(scene)

    resp = await app_client.post(f"/scenes/{scene_id}/decision", json=body)
    assert resp.status_code == 202  # NOT 200: the request replayed but adoption moved
    payload = resp.json()
    assert payload["request_disposition"] == RequestDisposition.REPLAYED.value
    assert payload["forward_effect"] == ForwardEffect.ADOPTION_PROMOTED.value

    async with db_factory() as s:
        adoption = (await s.execute(select(ImportAdoption))).scalar_one()
        assert adoption.status == ImportAdoptionStatus.QUEUED.value  # promoted in place, never duplicated
        assert adoption.liveness_basis == LivenessBasis.REQUEST_BOUND.value
        assert (await s.execute(select(func.count()).select_from(RevisionRequest))).scalar_one() == 1


async def test_inert_replay_is_the_only_200(app_client, db_factory):
    """D11: 200 iff `replayed` AND `forward_effect == none`. The second identical click changes nothing."""
    async with db_factory() as s:
        _, _, (scene,) = await _seed(s)
        await s.commit()
        scene_id, body = scene.id, _revise_body(scene)

    first = await app_client.post(f"/scenes/{scene_id}/decision", json=body)
    assert first.status_code == 202

    second = await app_client.post(f"/scenes/{scene_id}/decision", json=body)
    assert second.status_code == 200
    payload = second.json()
    assert payload["request_disposition"] == RequestDisposition.REPLAYED.value
    assert payload["forward_effect"] == ForwardEffect.NONE.value
    assert payload["request"]["id"] == first.json()["request"]["id"]

    async with db_factory() as s:
        assert (await s.execute(select(func.count()).select_from(RevisionRequest))).scalar_one() == 1
        assert (await s.execute(select(func.count()).select_from(ImportAdoption))).scalar_one() == 1


async def test_different_feedback_replaces_the_request_and_reuses_the_adoption(app_client, db_factory):
    """A genuinely different Revise supersedes the old request. The chapter's adoption is already
    serving it, so nothing moves forward on the adoption side — and the new request re-links to it."""
    async with db_factory() as s:
        _, _, (scene,) = await _seed(s)
        await s.commit()
        scene_id = scene.id
        first_body = _revise_body(scene, feedback="v1")
        second_body = _revise_body(scene, feedback="v2 — different intent")

    await app_client.post(f"/scenes/{scene_id}/decision", json=first_body)
    resp = await app_client.post(f"/scenes/{scene_id}/decision", json=second_body)
    assert resp.status_code == 202
    assert resp.json()["request_disposition"] == RequestDisposition.REPLACED.value
    assert resp.json()["forward_effect"] == ForwardEffect.ADOPTION_JOINED.value

    async with db_factory() as s:
        adoption = (await s.execute(select(ImportAdoption))).scalar_one()
        statuses = {r.status: r.import_adoption_id for r in (await s.execute(select(RevisionRequest))).scalars().all()}
        assert statuses[RevisionRequestStatus.SUPERSEDED.value] == adoption.id
        assert statuses[RevisionRequestStatus.AWAITING_CONTRACT.value] == adoption.id


# --------------------------------------------------------------------------- D6: the eligibility envelope


async def test_contracted_chapter_fails_closed_and_persists_nothing(app_client, db_factory):
    """D6: a mixed chapter needs AMENDMENT mode (#261). Revise fails closed rather than silently
    escalate into a supersession; the 409 points at amendment/start."""
    async with db_factory() as s:
        book, ch, (scene,) = await _seed(s)
        contracted = Scene(
            chapter_id=ch.id, scene_no=2, prose="Contracted prose.", version=1, status=SceneStatus.APPROVED
        )
        s.add(contracted)
        cp = ChapterPacket(book_id=book.id, chapter_id=ch.id, status="approved", body={}, open_questions={"items": []})
        s.add(cp)
        await s.flush()
        packet = ScenePacket(
            book_id=book.id, chapter_id=ch.id, chapter_packet_id=cp.id, scene_no=2, status="approved", body={}
        )
        s.add(packet)
        await s.flush()
        contracted.scene_packet_id = packet.id  # a scene of record -> the chapter is not evidence-only
        await s.commit()
        scene_id, body = scene.id, _revise_body(scene)

    resp = await app_client.post(f"/scenes/{scene_id}/decision", json=body)
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["reason"] == "chapter_has_contracted_scenes"
    assert "not available yet" not in detail["message"]
    assert f"/chapters/{ch.id}/amendment/start" in detail["message"]

    async with db_factory() as s:
        assert (await s.execute(select(func.count()).select_from(RevisionRequest))).scalar_one() == 0
        assert (await s.execute(select(func.count()).select_from(ImportAdoption))).scalar_one() == 0
        assert (await s.execute(select(func.count()).select_from(Approval))).scalar_one() == 0
        assert (await s.get(Scene, scene_id)).status == SceneStatus.PENDING_REVIEW


# --------------------------------------------------------------------------- D9: reverse-cancel goes live


async def test_approving_the_scene_reverse_cancels_the_request_bound_adoption(app_client, db_factory):
    """W2 wired the reverse path against data that could not exist yet. With W3's minter live, the full
    loop runs on REAL rows: revise -> request_bound adoption; approve -> demand gone -> adoption cancelled."""
    async with db_factory() as s:
        _, _, (scene,) = await _seed(s)
        await s.commit()
        scene_id, body = scene.id, _revise_body(scene)

    assert (await app_client.post(f"/scenes/{scene_id}/decision", json=body)).status_code == 202
    async with db_factory() as s:
        assert (await s.execute(select(ImportAdoption))).scalar_one().status == ImportAdoptionStatus.QUEUED.value

    approve = await app_client.post(f"/scenes/{scene_id}/decision", json={"decision": "approve"})
    assert approve.status_code == 200
    # Approve's wire shape is unchanged by W3's typing (SceneDecisionOut serializes identically).
    assert set(approve.json()) == {"scene", "status", "next_job"}

    async with db_factory() as s:
        assert (await s.execute(select(RevisionRequest))).scalar_one().status == RevisionRequestStatus.CANCELLED.value
        assert (await s.execute(select(ImportAdoption))).scalar_one().status == ImportAdoptionStatus.CANCELLED.value


async def test_operator_started_adoption_survives_the_same_approval(app_client, db_factory):
    """The `liveness_basis` guard is load-bearing (D9): an operator Start UPGRADES the row the revise
    minted, and that upgraded row must outlive the request that originally justified it."""
    async with db_factory() as s:
        _, ch, (scene,) = await _seed(s)
        await s.commit()
        scene_id, chapter_id, body = scene.id, ch.id, _revise_body(scene)

    await app_client.post(f"/scenes/{scene_id}/decision", json=body)
    start = await app_client.post(f"/chapters/{chapter_id}/adoption/start")
    assert start.status_code == 200

    async with db_factory() as s:
        adoption = (await s.execute(select(ImportAdoption))).scalar_one()  # still ONE row (D2)
        assert adoption.liveness_basis == LivenessBasis.OPERATOR_INDEPENDENT.value  # monotonic upgrade

    await app_client.post(f"/scenes/{scene_id}/decision", json={"decision": "approve"})
    async with db_factory() as s:
        adoption = (await s.execute(select(ImportAdoption))).scalar_one()
        assert adoption.status == ImportAdoptionStatus.QUEUED.value  # NOT cancelled — operator demand stands


# --------------------------------------------------------------------------- the second revise surface


async def test_continuity_use_ledger_uses_the_same_command(app_client, db_factory):
    """D4: no endpoint sequences the two mutations independently. The continuity panel's `use_ledger`
    returns the SAME typed envelope, mints the SAME adoption entry, and clears its critique atomically."""
    async with db_factory() as s:
        _, _, (scene,) = await _seed(s)
        crit = Critique(
            scene_id=scene.id,
            version=1,
            reviewer="continuity",
            severity="hard",
            payload={"character": "Marcus", "attribute": "level", "prose_value": "9", "ledger_value": "5"},
        )
        s.add(crit)
        await s.commit()
        scene_id, critique_id = scene.id, crit.id

    resp = await app_client.post(
        f"/scenes/{scene_id}/continuity/resolve",
        json={"critique_id": str(critique_id), "choice": "use_ledger"},
    )
    assert resp.status_code == 202
    payload = resp.json()
    assert payload["forward_effect"] == ForwardEffect.ADOPTION_CREATED.value
    assert payload["request"]["origin"] == RevisionRequestOrigin.CONTINUITY.value

    async with db_factory() as s:
        adoption = (await s.execute(select(ImportAdoption))).scalar_one()
        assert adoption.liveness_basis == LivenessBasis.REQUEST_BOUND.value
        assert (await s.execute(select(RevisionRequest))).scalar_one().import_adoption_id == adoption.id
        assert (await s.get(Critique, critique_id)) is None  # cleared in the same transaction


async def test_coordinator_reloads_under_the_lock_even_when_the_caller_preloaded_the_scene(db_factory):
    """Regression (found in review, ADR-0032 D4): the coordinator's "reload UNDER the lock" is only
    real if it bypasses the identity map.

    `resolve_continuity` reads the Scene as a full ORM entity BEFORE calling the coordinator — it needs
    the prose to build its feedback string. A bare `session.get` inside the locked body would then hand
    back that PRE-LOCK instance with no SQL at all, so classification would compare the client's hash
    against prose the coordinator never actually re-read. Because the expected hash is derived from the
    same stale object, the two agree and a concurrent prose change is silently revised over.

    Reproduce exactly that: preload the scene in session A, commit a prose change from session B, then
    invoke the coordinator on session A. It must see B's prose and REFUSE (409 scene_changed). Without
    `populate_existing=True` this accepts and writes a request pinned to prose that no longer exists.
    """
    from dominion.api.routers.reviews import accept_revision_intent

    async with db_factory() as s:
        _, _, (scene,) = await _seed(s, prose="Original prose.")
        await s.commit()
        scene_id = scene.id

    async with db_factory() as session_a:
        preloaded = await session_a.get(Scene, scene_id)  # now in session A's identity map
        assert preloaded is not None
        stale_hash = prose_hash(preloaded.prose)

        async with db_factory() as session_b:  # a concurrent, committed prose edit
            other = await session_b.get(Scene, scene_id)
            assert other is not None
            other.prose = "Rewritten by someone else."
            await session_b.commit()

        with pytest.raises(HTTPException) as exc:
            await accept_revision_intent(
                session_a,
                scene_id=scene_id,
                feedback="revise against what I was looking at",
                target_pass=None,
                expected_prose_hash=stale_hash,
                origin=RevisionRequestOrigin.CONTINUITY,
            )
        assert exc.value.status_code == 409

    async with db_factory() as s:
        assert (await s.execute(select(func.count()).select_from(RevisionRequest))).scalar_one() == 0
        assert (await s.execute(select(func.count()).select_from(ImportAdoption))).scalar_one() == 0
        assert (await s.get(Scene, scene_id)).prose == "Rewritten by someone else."


async def test_revise_kicks_the_adoption_drain(app_client, db_factory, captured_drains):
    """`queued` is spend consent the ADOPTION WORKER claims from — so the command must hand the row to
    a drain, or it is consent nothing ever acts on. Proven here on the real ASGI background-task path.
    A revise that mints NO adoption (nothing to claim) must not kick it."""
    async with db_factory() as s:
        _, _, (scene,) = await _seed(s)
        await s.commit()
        scene_id, body = scene.id, _revise_body(scene)

    await app_client.post(f"/scenes/{scene_id}/decision", json=body)
    assert captured_drains == ["drain_adoptions"]

    # An inert replay creates no adoption entry, so there is nothing new to drain.
    captured_drains.clear()
    await app_client.post(f"/scenes/{scene_id}/decision", json={"decision": "approve"})
    assert captured_drains == []


async def test_missing_scene_is_404_on_both_surfaces(app_client):
    ghost = uuid.uuid4()
    resp = await app_client.post(
        f"/scenes/{ghost}/decision",
        json={"decision": "revise", "feedback": "x", "expected_prose_hash": "abc"},
    )
    assert resp.status_code == 404
