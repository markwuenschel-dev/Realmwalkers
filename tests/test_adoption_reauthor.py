"""Oracles for the operator Re-author (Q11 tier-C force override) — ADR 0028, Slice 3b, Lane A8 —
together with the `chapter_packet_id ON DELETE SET NULL` change that lets a re-author REPLACE the
chapter's current proposed packet without the prior adoption's FK blocking the delete.

Six required proofs (the first two were the blocker Option A resolves):
  1. an ordinary CHANGED-INPUT re-Start authors and REPLACES the prior packet successfully — this closes
     the latent A4 collision that Option A also fixes;
  2. an identical forced Re-author authors exactly once AND leaves the PRIOR adoption's chapter_packet_id
     NULL after the replace (the FK SET NULL clears the superseded link);
  3. a retried identical Re-author (same token) does not double-spend;
  4. the reuse/derive/resume readers resolve the producer FROM THE LIVE PACKET id, so a superseded
     adoption whose former link is NULL is never matched;
  5. a contracted/approved chapter still REFUSES re-authoring;
  6. the ACTUALLY DEPLOYED FK on the migrated test DB is ON DELETE SET NULL (catalog query).
Plus retained coverage: tier-B reuse control (no force), two-active serialize, and source-drift invalidation.

Two harnesses: worker oracles drive `run_one_adoption` with the injected `FakeImportEvidenceExtractor` +
fixed retriever and a mocked author/QA (as `test_import_adoption.py`); endpoint oracles drive the real
ASGI app via `app_client` (as `test_adoption_start.py`). Needs Postgres (skips locally, runs under CI).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select, text

from dominion.shared.enums import PacketVerdict, SceneStatus
from dominion.shared.models import (
    Book,
    Chapter,
    ChapterPacket,
    ImportAdoption,
    ImportSceneEvidence,
    Scene,
    ScenePacket,
)
from dominion.workers import import_adoption as import_adoption_worker
from dominion.workers.import_adoption import run_one_adoption
from dominion.workers.import_evidence import FakeImportEvidenceExtractor
from dominion.workers.packet import author as author_mod
from dominion.workers.packet import qa as qa_mod


@pytest.fixture(autouse=True)
def captured_drains(monkeypatch):
    """HTTP Re-author now kicks drain_adoptions. Worker oracles call run_one_adoption directly
    and are unaffected; ASGI oracles must not claim a real author pass."""
    kicks: list[str] = []

    async def _record() -> None:
        kicks.append("drain_adoptions")

    monkeypatch.setattr(import_adoption_worker, "drain_adoptions", _record)
    return kicks


# ----------------------------------------- seed helpers ------------------------------------------ #


async def _seed(s, *, n_scenes: int = 2, pov: str = "Marcus") -> tuple[Book, Chapter, list[Scene]]:
    """A chapter of purely imported, uncontracted scenes — the eligible Re-author case."""
    book = Book(title="Reauthor Work")
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


def _queued(book_id: uuid.UUID, ch_id: uuid.UUID, **kw) -> ImportAdoption:
    """A fresh queued adoption for `(book_id, ch_id)` (worker seeds ids directly across sessions)."""
    return ImportAdoption(
        book_id=book_id,
        chapter_id=ch_id,
        mode="initial",
        status="queued",
        source_fingerprint="pending",
        liveness_basis=kw.pop("liveness_basis", "operator_independent"),
        **kw,
    )


def _author_packet(n_scenes: int = 2) -> dict:
    return {
        "confidence": "green",
        "chapter_job": "Reconstruct the chapter contract from the imported prose",
        "exit_state": "the gate stands open",
        "scene_seeds": [
            {"scene_no": i, "scene_job": f"Establish beat {i} at the vault gate."} for i in range(1, n_scenes + 1)
        ],
        "claims": [
            {"claim": "the gate exists", "source_strength": "LOCKED_CANON", "source_id": "C1"},
            {"claim": "the gate was breached", "source_strength": "DERIVED_FROM_MANUSCRIPT", "source_id": "M1"},
        ],
        "open_questions": [],
    }


def _qa() -> dict:
    return {"verdict": PacketVerdict.APPROVE, "residual_risks": ["keep the antagonist unnamed"], "issues": []}


def _fixed_retriever(hits):
    async def _retrieve(_query):
        return list(hits)

    return _retrieve


def _patch_author(monkeypatch, *, packet: dict, counter: dict | None = None) -> None:
    async def fake_author(**kwargs):
        if counter is not None:
            counter["n"] += 1
        return packet

    async def fake_qa(_packet, **kwargs):
        return _qa()

    monkeypatch.setattr(author_mod, "author_packet_from_evidence", fake_author)
    monkeypatch.setattr(qa_mod, "qa_packet", fake_qa)


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


async def _run(db_factory, extractor) -> bool:
    return await run_one_adoption(db_factory, extractor=extractor, retrieve=_fixed_retriever([]))


# ------------------------------------- the six required proofs ----------------------------------- #


async def test_ordinary_changed_input_restart_authors_and_replaces(db_factory, monkeypatch):
    """Proof 1 — closes the latent A4 collision: an ordinary re-Start over CHANGED inputs must author
    fresh (the reuse gate breaks) and REPLACE the prior packet. Under the old NO-ACTION FK the pipeline's
    replace-delete was blocked by the first adoption's link; ON DELETE SET NULL lets it succeed and nulls
    the superseded link."""
    counter = {"n": 0}
    _patch_author(monkeypatch, packet=_author_packet(2), counter=counter)
    extractor = FakeImportEvidenceExtractor()

    async with db_factory() as s:
        book, ch, scenes = await _seed(s, n_scenes=2)
        s.add(_queued(book.id, ch.id))
        await s.commit()
        book_id, ch_id, scene0_id = book.id, ch.id, scenes[0].id

    assert await _run(db_factory, extractor) is True
    assert counter["n"] == 1
    async with db_factory() as s:
        a1 = (await s.execute(select(ImportAdoption))).scalar_one()
        a1_id, p1 = a1.id, a1.chapter_packet_id

    # A real manuscript edit changes the inputs, so the reuse gate can no longer reuse P1.
    async with db_factory() as s:
        sc = await s.get(Scene, scene0_id)
        sc.prose = "Rewritten prose for scene 1. The vault gate is sealed once more, its lock reforged."
        await s.commit()

    async with db_factory() as s:
        s.add(_queued(book_id, ch_id))
        await s.commit()

    assert await _run(db_factory, extractor) is True
    assert counter["n"] == 2  # authored fresh (changed inputs) — NOT reused

    async with db_factory() as s:
        a2 = (await s.execute(select(ImportAdoption).order_by(ImportAdoption.created_at.desc()).limit(1))).scalar_one()
        assert a2.status == "contract_proposed"
        assert a2.chapter_packet_id is not None and a2.chapter_packet_id != p1  # replaced with P2
        assert await _count(s, ChapterPacket) == 1  # replace SUCCEEDED — one current packet (latent A4 fix)
        a1_after = await s.get(ImportAdoption, a1_id)
        assert a1_after.chapter_packet_id is None  # ON DELETE SET NULL cleared the superseded link
        assert a1_after.status == "contract_proposed"  # still a successful historical proposal


async def test_reauthor_authors_fresh_once_and_nulls_prior_link(db_factory, monkeypatch):
    """Proof 2 — the core force behavior: after an ordinary adoption proposes P1, an explicit Re-author
    (identical inputs) authors FRESH exactly once, replaces P1 with a new proposed P2, and the FK SET NULL
    leaves the PRIOR adoption's chapter_packet_id NULL. Evidence is reused, not re-extracted."""
    counter = {"n": 0}
    _patch_author(monkeypatch, packet=_author_packet(2), counter=counter)
    extractor = FakeImportEvidenceExtractor()

    async with db_factory() as s:
        book, ch, _ = await _seed(s, n_scenes=2)
        s.add(_queued(book.id, ch.id))
        await s.commit()
        book_id, ch_id = book.id, ch.id

    assert await _run(db_factory, extractor) is True
    assert counter["n"] == 1
    assert len(extractor.calls) == 2
    async with db_factory() as s:
        a1 = (await s.execute(select(ImportAdoption))).scalar_one()
        a1_id, p1 = a1.id, a1.chapter_packet_id

    # Operator Re-author: a fresh queued, force-flagged adoption for the SAME unchanged chapter.
    token = uuid.uuid4()
    async with db_factory() as s:
        s.add(_queued(book_id, ch_id, force_author_token=token, reauthor_of_adoption_id=a1_id))
        await s.commit()

    assert await _run(db_factory, extractor) is True
    assert counter["n"] == 2  # FORCE — exactly one additional author call
    assert len(extractor.calls) == 2  # identical inputs → evidence reused, NOT re-extracted

    async with db_factory() as s:
        a2 = (await s.execute(select(ImportAdoption).where(ImportAdoption.force_author_token == token))).scalar_one()
        assert a2.status == "contract_proposed"
        assert a2.chapter_packet_id is not None and a2.chapter_packet_id != p1  # NEW packet P2
        assert a2.reauthor_of_adoption_id == a1_id  # audit link preserved through the worker
        assert await _count(s, ChapterPacket) == 1  # P1 REPLACED — exactly one current packet
        a1_after = await s.get(ImportAdoption, a1_id)
        assert a1_after.chapter_packet_id is None  # ON DELETE SET NULL cleared the prior link
        assert a1_after.status == "contract_proposed"  # a superseded-but-successful historical proposal


async def test_reauthor_retry_same_token_no_double_spend(app_client, db_factory):
    """Proof 3 — idempotency: a retried Re-author with the SAME force_author_token returns the SAME
    adoption; no second spend, no second row."""
    async with db_factory() as s:
        _, ch, _ = await _seed(s, n_scenes=2)
        await s.commit()
        chapter_id = ch.id

    token = str(uuid.uuid4())
    first = await app_client.post(f"/chapters/{chapter_id}/adoption/reauthor", json={"force_author_token": token})
    second = await app_client.post(f"/chapters/{chapter_id}/adoption/reauthor", json={"force_author_token": token})
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["id"] == second.json()["id"]  # same adoption
    assert first.json()["status"] == "queued"
    assert first.json()["force_author_token"] == token

    async with db_factory() as s:
        assert await _count(s, ImportAdoption) == 1  # no second spend


async def test_reauthor_kicks_the_adoption_drain(app_client, db_factory, captured_drains):
    async with db_factory() as s:
        _, ch, _ = await _seed(s, n_scenes=1)
        await s.commit()
        chapter_id = ch.id

    resp = await app_client.post(
        f"/chapters/{chapter_id}/adoption/reauthor", json={"force_author_token": str(uuid.uuid4())}
    )
    assert resp.status_code == 200, resp.text
    assert captured_drains == ["drain_adoptions"]


async def test_readers_resolve_producer_from_live_packet(db_factory, monkeypatch):
    """Proof 4 — the reuse/derive/resume readers all resolve the producing adoption FROM THE LIVE PACKET id
    (`WHERE chapter_packet_id == <current packet>.id AND status=contract_proposed`). After a Re-author
    replaces P1 with P2 and nulls the prior adoption's link, a later ordinary Start REUSES P2 via that
    predicate — the null former link is never matched. (Exercising the reuse reader proves the predicate
    shared verbatim by revision.py and scene_packet/derive.py.)"""
    counter = {"n": 0}
    _patch_author(monkeypatch, packet=_author_packet(2), counter=counter)
    extractor = FakeImportEvidenceExtractor()

    async with db_factory() as s:
        book, ch, _ = await _seed(s, n_scenes=2)
        s.add(_queued(book.id, ch.id))
        await s.commit()
        book_id, ch_id = book.id, ch.id

    assert await _run(db_factory, extractor) is True  # ordinary → P1
    async with db_factory() as s:
        a1_id = (await s.execute(select(ImportAdoption.id))).scalar_one()

    token = uuid.uuid4()
    async with db_factory() as s:
        s.add(_queued(book_id, ch_id, force_author_token=token, reauthor_of_adoption_id=a1_id))
        await s.commit()
    assert await _run(db_factory, extractor) is True  # forced Re-author → P2, A1 link nulled
    assert counter["n"] == 2
    async with db_factory() as s:
        p2 = (
            (await s.execute(select(ImportAdoption).where(ImportAdoption.force_author_token == token)))
            .scalar_one()
            .chapter_packet_id
        )
        assert (await s.get(ImportAdoption, a1_id)).chapter_packet_id is None  # former link is NULL

    # A later ordinary Start must REUSE P2 via the live-packet producer lookup — the null A1 link is ignored.
    async with db_factory() as s:
        s.add(_queued(book_id, ch_id))
        await s.commit()
    assert await _run(db_factory, extractor) is True
    assert counter["n"] == 2  # REUSE of P2 — no third author call
    async with db_factory() as s:
        latest = (
            await s.execute(select(ImportAdoption).order_by(ImportAdoption.created_at.desc()).limit(1))
        ).scalar_one()
        assert latest.force_author_token is None
        assert latest.chapter_packet_id == p2  # resolved the LIVE producer (A2 → P2), not the null A1 link
        assert await _count(s, ChapterPacket) == 1


async def test_reauthor_refuses_approved_or_contracted_chapter(app_client, db_factory):
    """Proof 5 — no hidden overwrite: the force route REFUSES a chapter with an approved contract
    (`chapter_contract_already_approved`) or any contracted scene (`chapter_has_contracted_scenes`),
    writing no adoption on either refuse. Downstream-owned packets never become deletable via re-author."""
    async with db_factory() as s:
        book, ch, _ = await _seed(s, n_scenes=2)
        s.add(
            ChapterPacket(
                book_id=book.id,
                chapter_id=ch.id,
                status="approved",
                body={"scene_seeds": []},
                open_questions={"items": []},
            )
        )
        await s.commit()
        approved_chapter = ch.id

    resp = await app_client.post(
        f"/chapters/{approved_chapter}/adoption/reauthor", json={"force_author_token": str(uuid.uuid4())}
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["reason"] == "chapter_contract_already_approved"

    async with db_factory() as s:
        book2, ch2, scenes2 = await _seed(s, n_scenes=2)
        await _contract_scene(s, book2, ch2, scenes2[0])
        await s.commit()
        contracted_chapter = ch2.id

    resp2 = await app_client.post(
        f"/chapters/{contracted_chapter}/adoption/reauthor", json={"force_author_token": str(uuid.uuid4())}
    )
    assert resp2.status_code == 409, resp2.text
    assert resp2.json()["detail"]["reason"] == "chapter_has_contracted_scenes"

    async with db_factory() as s:
        assert await _count(s, ImportAdoption) == 0  # nothing queued on either refuse


async def test_deployed_fk_is_on_delete_set_null(db_factory):
    """Proof 6 — the ACTUALLY DEPLOYED constraint (not just the declarative model) is ON DELETE SET NULL on
    the migrated test DB. Catalog-level: pg_constraint.confdeltype = 'n' (SET NULL) for the FK, and the
    information_schema view agrees ('SET NULL'). This is what the migration's guarded ALTER guarantees on a
    persistent prod DB whose constraint predated the change."""
    async with db_factory() as s:
        confdeltype = (
            await s.execute(
                text("SELECT confdeltype FROM pg_constraint WHERE conname = 'import_adoptions_chapter_packet_id_fkey'")
            )
        ).scalar_one()
        # pg's internal "char" type comes back from asyncpg as a single byte; normalize to str.
        if isinstance(confdeltype, bytes):
            confdeltype = confdeltype.decode()
        assert confdeltype == "n"  # 'n' == ON DELETE SET NULL

        delete_rule = (
            await s.execute(
                text(
                    "SELECT delete_rule FROM information_schema.referential_constraints "
                    "WHERE constraint_name = 'import_adoptions_chapter_packet_id_fkey'"
                )
            )
        ).scalar_one()
        assert delete_rule == "SET NULL"


# ------------------------------------- retained extra coverage ----------------------------------- #


async def test_ordinary_rerun_still_reuses_no_force(db_factory, monkeypatch):
    """Control: a second ORDINARY adoption (no force, identical inputs) still REUSES the first pass's
    proposed packet — tier-B reuse is not regressed by the tier-C bypass."""
    counter = {"n": 0}
    _patch_author(monkeypatch, packet=_author_packet(2), counter=counter)
    extractor = FakeImportEvidenceExtractor()

    async with db_factory() as s:
        book, ch, _ = await _seed(s, n_scenes=2)
        s.add(_queued(book.id, ch.id))
        await s.commit()
        book_id, ch_id = book.id, ch.id

    assert await _run(db_factory, extractor) is True
    assert counter["n"] == 1
    assert len(extractor.calls) == 2
    async with db_factory() as s:
        first_packet_id = (await s.execute(select(ImportAdoption))).scalar_one().chapter_packet_id
        s.add(_queued(book_id, ch_id))
        await s.commit()

    assert await _run(db_factory, extractor) is True
    assert counter["n"] == 1  # REUSE — no second author call
    assert len(extractor.calls) == 2  # REUSE — no re-extraction

    async with db_factory() as s:
        adoptions = (await s.execute(select(ImportAdoption).order_by(ImportAdoption.created_at))).scalars().all()
        assert len(adoptions) == 2
        assert all(a.status == "contract_proposed" for a in adoptions)
        assert adoptions[1].chapter_packet_id == first_packet_id  # same packet
        assert adoptions[1].force_author_token is None
        assert await _count(s, ChapterPacket) == 1  # no duplicate packet


async def test_reauthor_two_active_requests_serialize(app_client, db_factory):
    """A second Re-author (a DIFFERENT token) while one adoption is already in-flight returns the active
    run rather than creating a parallel authoring pass; the second token never spends."""
    async with db_factory() as s:
        _, ch, _ = await _seed(s, n_scenes=2)
        await s.commit()
        chapter_id = ch.id

    t1, t2 = str(uuid.uuid4()), str(uuid.uuid4())
    first = await app_client.post(f"/chapters/{chapter_id}/adoption/reauthor", json={"force_author_token": t1})
    second = await app_client.post(f"/chapters/{chapter_id}/adoption/reauthor", json={"force_author_token": t2})
    assert first.status_code == 200 and second.status_code == 200, (first.text, second.text)
    assert first.json()["id"] == second.json()["id"]  # serialized to the in-flight run
    assert second.json()["force_author_token"] == t1  # the second token was NOT spent

    async with db_factory() as s:
        assert await _count(s, ImportAdoption) == 1  # no parallel authoring


async def test_reauthor_source_drift_invalidates_and_discards_packet(db_factory, monkeypatch):
    """The publish CAS is unchanged on the force path: when the chapter's source prose drifts DURING the
    forced author pass, the publish compare-and-set fails, the pass is INVALIDATED and its freshly authored
    packet discarded, while the immutable evidence shards survive (Q13)."""
    token = uuid.uuid4()

    async def drifting_author(**kwargs):
        async with db_factory() as s2:
            scene = (await s2.execute(select(Scene).limit(1))).scalar_one()
            scene.prose = (scene.prose or "") + " (edited mid-author)"
            await s2.commit()
        return _author_packet(1)

    async def fake_qa(_packet, **kwargs):
        return _qa()

    monkeypatch.setattr(author_mod, "author_packet_from_evidence", drifting_author)
    monkeypatch.setattr(qa_mod, "qa_packet", fake_qa)

    async with db_factory() as s:
        book, ch, _ = await _seed(s, n_scenes=1)
        s.add(_queued(book.id, ch.id, force_author_token=token))
        await s.commit()

    assert await _run(db_factory, FakeImportEvidenceExtractor()) is True

    async with db_factory() as s:
        adoption = (await s.execute(select(ImportAdoption))).scalar_one()
        assert adoption.status == "invalidated"
        assert "drift" in (adoption.error or "")
        assert adoption.force_author_token == token  # the force path went through publish unchanged
        assert await _count(s, ChapterPacket) == 0  # the freshly authored packet was discarded
        assert await _count(s, ImportSceneEvidence) == 1  # evidence shards survive


# ------------- ADR-0032 W1: Re-author endpoint characterization gaps (pre-extraction) ------------- #
# Lock in Re-author ENDPOINT behaviors not previously asserted at the HTTP layer, so the W1 extraction of
# the adoption seam stays behavior-preserving: chapter-not-found -> 404, workflow-lock busy -> 409, and
# fresh-create lineage (reauthor_of_adoption_id -> the prior contract_proposed adoption). Green on the
# pre-W1 code; they must remain green through the extraction.


async def test_reauthor_on_unknown_chapter_is_404(app_client, db_factory):
    """A Re-author on a chapter that does not exist is a 404 (parity with Start)."""
    resp = await app_client.post(
        f"/chapters/{uuid.uuid4()}/adoption/reauthor", json={"force_author_token": str(uuid.uuid4())}
    )
    assert resp.status_code == 404


async def test_reauthor_maps_chapter_workflow_busy_to_409(app_client, db_factory, monkeypatch):
    """A held per-chapter workflow lock maps Re-author to `409 chapter_workflow_busy`, writes nothing, and
    the retry after the lock frees succeeds (parity with Start's Q16 oracle)."""
    from dominion.api.routers import adoption as adoption_router
    from dominion.shared.chapter_lock import acquire_chapter_workflow_lock

    monkeypatch.setattr(adoption_router, "LOCK_TIMEOUT_MS", 250)
    async with db_factory() as s:
        _, ch, _ = await _seed(s, n_scenes=2)
        await s.commit()
        chapter_id = ch.id

    token = str(uuid.uuid4())
    async with db_factory() as holder:
        await acquire_chapter_workflow_lock(holder, chapter_id, timeout_ms=None)
        resp = await app_client.post(f"/chapters/{chapter_id}/adoption/reauthor", json={"force_author_token": token})
        assert resp.status_code == 409
        assert resp.json()["detail"]["reason"] == "chapter_workflow_busy"
        async with db_factory() as probe:
            assert await _count(probe, ImportAdoption) == 0  # nothing written on the busy path
        await holder.rollback()

    retry = await app_client.post(f"/chapters/{chapter_id}/adoption/reauthor", json={"force_author_token": token})
    assert retry.status_code == 200, retry.text
    assert retry.json()["status"] == "queued"


async def test_reauthor_fresh_create_links_prior_proposed_lineage(app_client, db_factory):
    """The fresh-create path stamps the force token and links `reauthor_of_adoption_id` to the chapter's
    prior `contract_proposed` adoption (audit lineage); mode stays `initial`."""
    async with db_factory() as s:
        book, ch, _ = await _seed(s, n_scenes=2)
        prior = ImportAdoption(
            book_id=book.id,
            chapter_id=ch.id,
            mode="initial",
            status="contract_proposed",
            source_fingerprint="prior-proposed",
            liveness_basis="operator_independent",
        )
        s.add(prior)
        await s.commit()
        chapter_id, prior_id = ch.id, prior.id

    token = str(uuid.uuid4())
    resp = await app_client.post(f"/chapters/{chapter_id}/adoption/reauthor", json={"force_author_token": token})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "queued"
    assert data["mode"] == "initial"
    assert data["force_author_token"] == token
    assert data["reauthor_of_adoption_id"] == str(prior_id)  # lineage to the prior proposed adoption

    async with db_factory() as s:
        assert await _count(s, ImportAdoption) == 2  # prior untouched + the new force adoption
        created = (
            await s.execute(select(ImportAdoption).where(ImportAdoption.force_author_token == uuid.UUID(token)))
        ).scalar_one()
        assert created.reauthor_of_adoption_id == prior_id
