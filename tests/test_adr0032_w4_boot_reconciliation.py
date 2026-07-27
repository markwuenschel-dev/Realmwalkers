"""ADR-0032 W4 — boot reconciliation, driven through the REAL application lifespan.

The failure this repairs: a redeploy killed the process between the author's Revise click and the
durable `RevisionRequest` recording it. The scene is left at `revision_requested` with nothing active
behind it — visibly mid-revision, actually inert.

The acceptance tests here enter `app.router.lifespan_context(app)` — the exact async context manager
`FastAPI(lifespan=...)` installs and the container's `hypercorn dominion.api.main:app` runs — AFTER
seeding the stranded state. Nothing calls the reconciler directly at that level, so what is proven is
that BOOT does it. (`app_client` boots the lifespan on entry, before a test can seed, so these drive
the context manager themselves.)

What this suite pins:
  * D7 — the scan predicate: LATEST Approval OVERALL must BE a REVISE for the scene's CURRENT version;
         never "latest REVISE", which would skip past a later APPROVE and resurrect replaced intent;
  * D7 — reconstruction records intent WITHOUT spend: `awaiting_start` (not worker-claimable),
         `origin=legacy_reconciliation`, the SOURCE approval preserved, the CURRENT prose hash pinned;
  * D8 — the hold is a DERIVED condition projected onto `Activity`, deduped per prose snapshot, so
         repeated boots over an unrepaired scene append nothing new (bounded growth);
  * idempotency/restart — a second boot over reconciled data reconstructs nothing;
  * ordering — reconciliation commits BEFORE the drains are kicked (D13 W4).
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from dominion.shared.enums import (
    Decision,
    ImportAdoptionStatus,
    IntegrityHoldReason,
    LivenessBasis,
    RevisionRequestOrigin,
    RevisionRequestStatus,
    SceneStatus,
)
from dominion.shared.models import (
    Activity,
    Approval,
    Book,
    Chapter,
    ImportAdoption,
    RevisionRequest,
    Scene,
)
from dominion.workers.boot_reconciliation import HOLD_CODE, hold_dedup_key
from dominion.workers.revision import prose_hash

# `integrity_hold` is a SHARED Activity kind — the lifespan's ADR-0027 job-ownership probe emits it on
# every boot too. Scope every assertion to THIS producer, or the probe's rows contaminate the counts.
_IS_RECONCILIATION_HOLD = (Activity.kind == "integrity_hold") & (Activity.source == "reconciliation")


async def _boot():
    """Run the REAL lifespan once (startup + shutdown), exactly as the production ASGI server does."""
    from dominion.api.main import app

    async with app.router.lifespan_context(app):
        pass


async def _stranded(s, *, decision=Decision.REVISE, version: int = 1, approval_version: int | None = 1, scenes=1):
    """A scene the redeploy stranded: status `revision_requested`, NO active RevisionRequest, and an
    approval history whose current row decides whether it is reconstructible or held."""
    book = Book(title="ADR-0032 W4")
    s.add(book)
    await s.flush()
    ch = Chapter(book_id=book.id, chapter_no=1, pov="Marcus")
    s.add(ch)
    await s.flush()
    made = []
    for n in range(1, scenes + 1):
        sc = Scene(
            chapter_id=ch.id,
            scene_no=n,
            prose=f"Imported prose {n}.",
            version=version,
            status=SceneStatus.REVISION_REQUESTED,
        )
        s.add(sc)
        await s.flush()
        if decision is not None:
            s.add(
                Approval(
                    scene_id=sc.id,
                    version=approval_version,
                    decision=decision,
                    feedback="tighten the open",
                    target_pass=None,
                )
            )
        made.append(sc)
    await s.flush()
    return book, ch, made


# --------------------------------------------------------------------------- D7: reconstruction


async def test_boot_reconstructs_stranded_revise_intent(db_factory):
    """The motivating recovery, through the real boot path: the lost request comes back, linked to a
    RECORDED-not-bought adoption."""
    async with db_factory() as s:
        _, ch, (scene,) = await _stranded(s)
        await s.commit()
        scene_id, chapter_id, source_prose = scene.id, ch.id, scene.prose

    await _boot()

    async with db_factory() as s:
        req = (await s.execute(select(RevisionRequest))).scalar_one()
        adoption = (await s.execute(select(ImportAdoption))).scalar_one()
        approval = (await s.execute(select(Approval))).scalar_one()

        assert req.target_scene_id == scene_id
        assert req.status == RevisionRequestStatus.AWAITING_CONTRACT.value
        assert req.origin == RevisionRequestOrigin.LEGACY_RECONCILIATION.value
        assert req.approval_id == approval.id  # the SOURCE approval is preserved, not re-created
        assert (await s.execute(select(func.count()).select_from(Approval))).scalar_one() == 1
        # Legacy Approval carries no prose hash, so intent is re-anchored to the CURRENT prose (D7).
        assert req.target_prose_hash == prose_hash(source_prose)
        assert req.feedback == "tighten the open"

        # RECORD_WITHOUT_SPEND: durable intent, NOT worker-claimable. An unpaused queue is not consent
        # for historical spend — an operator Start is what promotes this.
        assert adoption.status == ImportAdoptionStatus.AWAITING_START.value
        assert adoption.liveness_basis == LivenessBasis.REQUEST_BOUND.value
        assert adoption.chapter_id == chapter_id
        assert req.import_adoption_id == adoption.id
        # A reconstructible scene raises NO integrity hold.
        assert (
            await s.execute(select(func.count()).select_from(Activity).where(_IS_RECONCILIATION_HOLD))
        ).scalar_one() == 0


async def test_two_stranded_scenes_in_one_chapter_share_one_adoption(db_factory):
    """Adoption is chapter-shared: reconciling a second scene in the same chapter JOINS the row the
    first created rather than colliding with the active-chapter unique index."""
    async with db_factory() as s:
        await _stranded(s, scenes=2)
        await s.commit()

    await _boot()

    async with db_factory() as s:
        adoption = (await s.execute(select(ImportAdoption))).scalar_one()  # exactly ONE
        reqs = (await s.execute(select(RevisionRequest))).scalars().all()
        assert len(reqs) == 2
        assert {r.import_adoption_id for r in reqs} == {adoption.id}


async def test_stranded_scenes_in_different_chapters_get_their_own_adoptions(db_factory):
    """Identity isolation: adoption is CHAPTER-scoped. Two stranded scenes in different chapters (and
    different books) must produce two independent adoptions, each linked only to its own chapter's
    request — the partial-unique index is per chapter, and reconciliation must not conflate them."""
    async with db_factory() as s:
        _, ch_a, (scene_a,) = await _stranded(s)
        _, ch_b, (scene_b,) = await _stranded(s)  # a second Book + Chapter entirely
        await s.commit()
        a_chapter, b_chapter = ch_a.id, ch_b.id
        a_scene, b_scene = scene_a.id, scene_b.id
        assert a_chapter != b_chapter

    await _boot()

    async with db_factory() as s:
        adoptions = (await s.execute(select(ImportAdoption))).scalars().all()
        assert {a.chapter_id for a in adoptions} == {a_chapter, b_chapter}
        assert len(adoptions) == 2
        by_chapter = {a.chapter_id: a.id for a in adoptions}
        reqs = {r.target_scene_id: r for r in (await s.execute(select(RevisionRequest))).scalars().all()}
        assert reqs[a_scene].import_adoption_id == by_chapter[a_chapter]
        assert reqs[b_scene].import_adoption_id == by_chapter[b_chapter]
        assert reqs[a_scene].chapter_id == a_chapter and reqs[b_scene].chapter_id == b_chapter


async def test_second_boot_is_idempotent(db_factory):
    """Restart recovery must converge: the reconstructed request is now ACTIVE, so the scene is no
    longer a candidate and a second boot writes nothing."""
    async with db_factory() as s:
        await _stranded(s)
        await s.commit()

    await _boot()
    await _boot()

    async with db_factory() as s:
        assert (await s.execute(select(func.count()).select_from(RevisionRequest))).scalar_one() == 1
        assert (await s.execute(select(func.count()).select_from(ImportAdoption))).scalar_one() == 1


async def test_a_scene_with_an_active_request_is_not_a_candidate(db_factory):
    """Only STRANDED scenes are reconciled — a scene whose durable request survived is left alone."""
    async with db_factory() as s:
        book, ch, (scene,) = await _stranded(s)
        s.add(
            RevisionRequest(
                book_id=book.id,
                chapter_id=ch.id,
                target_scene_id=scene.id,
                scene_no=scene.scene_no,
                target_scene_version=scene.version,
                target_prose_hash=prose_hash(scene.prose),
                origin=RevisionRequestOrigin.REVIEW.value,
                status=RevisionRequestStatus.AWAITING_CONTRACT.value,
            )
        )
        await s.commit()

    await _boot()

    async with db_factory() as s:
        req = (await s.execute(select(RevisionRequest))).scalar_one()  # still exactly one
        assert req.origin == RevisionRequestOrigin.REVIEW.value  # the original, not a reconstruction
        assert (await s.execute(select(func.count()).select_from(ImportAdoption))).scalar_one() == 0


# --------------------------------------------------------------------------- D8: the integrity hold


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"decision": None}, IntegrityHoldReason.MISSING_APPROVAL),
        ({"decision": Decision.APPROVE}, IntegrityHoldReason.LATEST_DECISION_NOT_REVISE),
        ({"approval_version": 1, "version": 3}, IntegrityHoldReason.SCENE_VERSION_MISMATCH),
    ],
)
async def test_unreconstructible_scene_raises_a_typed_hold(db_factory, kwargs, reason):
    """D8: missing, stale, or mismatched evidence is NEVER treated as current intent. Each failure mode
    records WHY, and nothing is fabricated."""
    async with db_factory() as s:
        _, _, (scene,) = await _stranded(s, **kwargs)
        await s.commit()
        scene_id, prose = scene.id, scene.prose

    await _boot()

    async with db_factory() as s:
        assert (await s.execute(select(func.count()).select_from(RevisionRequest))).scalar_one() == 0
        assert (await s.execute(select(func.count()).select_from(ImportAdoption))).scalar_one() == 0
        hold = (await s.execute(select(Activity).where(_IS_RECONCILIATION_HOLD))).scalar_one()
        assert hold.source == "reconciliation"
        assert hold.payload_json["hold_code"] == HOLD_CODE
        assert hold.payload_json["reason_code"] == reason.value
        assert hold.payload_json["scene_id"] == str(scene_id)
        assert hold.payload_json["dedup_key"] == hold_dedup_key(scene_id, prose_hash(prose))
        # The scene is left visibly held, not silently "fixed".
        assert (await s.get(Scene, scene_id)).status == SceneStatus.REVISION_REQUESTED


async def test_latest_approve_wins_over_an_earlier_revise(db_factory):
    """D7's ordering rule, stated as a defect guard: querying the latest REVISE (rather than the latest
    approval OVERALL) would skip this APPROVE and resurrect intent the author already replaced."""
    async with db_factory() as s:
        _, _, (scene,) = await _stranded(s, decision=Decision.REVISE)
        await s.commit()
        scene_id, version = scene.id, scene.version

    # A SEPARATE transaction, as the second HTTP decision genuinely is — `decided_at` is the
    # TRANSACTION timestamp, so this is what gives the APPROVE a strictly later one.
    async with db_factory() as s:
        s.add(Approval(scene_id=scene_id, version=version, decision=Decision.APPROVE))
        await s.commit()

    await _boot()

    async with db_factory() as s:
        assert (await s.execute(select(func.count()).select_from(RevisionRequest))).scalar_one() == 0
        hold = (await s.execute(select(Activity).where(_IS_RECONCILIATION_HOLD))).scalar_one()
        assert hold.payload_json["reason_code"] == IntegrityHoldReason.LATEST_DECISION_NOT_REVISE.value


async def test_tied_approval_timestamps_skip_rather_than_guess(db_factory):
    """`Approval` has no monotonic sequence, and `decided_at` is the TRANSACTION timestamp — so two
    approvals written together are genuinely unordered. Reconciliation must not coin-flip between a
    REVISE and the APPROVE that replaced it: it leaves the scene exactly as it found it, and records NO
    hold (a hold asserts 'no valid current intent', a conclusion this read cannot reach)."""
    async with db_factory() as s:
        _, _, (scene,) = await _stranded(s, decision=Decision.REVISE)
        # SAME transaction as the REVISE above -> identical decided_at.
        s.add(Approval(scene_id=scene.id, version=scene.version, decision=Decision.APPROVE))
        await s.commit()
        scene_id = scene.id

    await _boot()

    async with db_factory() as s:
        assert (await s.execute(select(func.count()).select_from(RevisionRequest))).scalar_one() == 0
        assert (await s.execute(select(func.count()).select_from(ImportAdoption))).scalar_one() == 0
        holds = (
            await s.execute(select(func.count()).select_from(Activity).where(_IS_RECONCILIATION_HOLD))
        ).scalar_one()
        assert holds == 0
        assert (await s.get(Scene, scene_id)).status == SceneStatus.REVISION_REQUESTED


async def test_hold_dedupes_per_prose_snapshot_across_boots(db_factory):
    """Bounded resource growth (D8): the hold is one event per unresolved SNAPSHOT, not per boot. A
    hand-edit changes the prose hash — a genuinely new diagnostic state — and gets its own event."""
    async with db_factory() as s:
        _, _, (scene,) = await _stranded(s, decision=None)
        await s.commit()
        scene_id = scene.id

    await _boot()
    await _boot()
    await _boot()

    async with db_factory() as s:
        holds = (await s.execute(select(Activity).where(_IS_RECONCILIATION_HOLD))).scalars().all()
        assert len(holds) == 1  # three boots, one event

    async with db_factory() as s:  # the author hand-edits the prose: a new snapshot
        scene = await s.get(Scene, scene_id)
        scene.prose = "Reworked prose."
        await s.commit()

    await _boot()

    async with db_factory() as s:
        holds = (await s.execute(select(Activity).where(_IS_RECONCILIATION_HOLD))).scalars().all()
        assert len(holds) == 2
        assert {h.payload_json["prose_hash"] for h in holds} == {
            prose_hash("Imported prose 1."),
            prose_hash("Reworked prose."),
        }


# --------------------------------------------------------------------------- D13 W4: boot ordering


async def test_reconciliation_commits_before_the_drains_are_kicked(db_factory, monkeypatch):
    """The ordering is load-bearing (D13 W4): if the drains ran first, this boot's pass would run
    against a queue still missing the work reconciliation is about to restore, so recovery would wait a
    whole extra deploy cycle. Assert on ORDER + committed visibility, not on source position."""
    from dominion.api import main as main_mod
    from dominion.workers import background_work
    from dominion.workers import import_adoption as import_adoption_worker

    async with db_factory() as s:
        book, ch, (scene,) = await _stranded(s)
        # Give the job drain something to resume, so its kick is genuinely reachable this boot.
        from dominion.shared.enums import JobStatus
        from dominion.shared.models import Job

        s.add(
            Job(
                kind="draft",
                token_budget=100,
                status=JobStatus.QUEUED.value,
                book_id=book.id,
                chapter_id=ch.id,
                scene_no=1,
            )
        )
        await s.commit()

    order: list[str] = []
    real_reconcile = main_mod.__dict__.get("reconcile_legacy_revision_intent")
    assert real_reconcile is None  # imported lazily inside the lifespan, so patch the source module

    from dominion.workers import boot_reconciliation

    real = boot_reconciliation.reconcile_legacy_revision_intent

    async def _traced_reconcile(*a, **k):
        order.append("reconcile")
        return await real(*a, **k)

    async def _traced_jobs():
        order.append("drain_jobs")

    async def _traced_repairs():
        order.append("drain_repairs")

    async def _traced_adoptions():
        # By the time any drain runs, reconciliation's work must already be COMMITTED and visible.
        async with db_factory() as s:
            assert (await s.execute(select(func.count()).select_from(RevisionRequest))).scalar_one() == 1
        order.append("drain_adoptions")

    monkeypatch.setattr(boot_reconciliation, "reconcile_legacy_revision_intent", _traced_reconcile)
    monkeypatch.setattr(background_work, "drain_queued_jobs", _traced_jobs)
    monkeypatch.setattr(background_work, "drain_queued_repair_tasks", _traced_repairs)
    monkeypatch.setattr(import_adoption_worker, "drain_adoptions", _traced_adoptions)

    await _boot()

    assert order and order[0] == "reconcile"
    assert "drain_jobs" in order  # the queued job proves a drain really was kicked after it
