"""RED-FIRST: a Job claimed by a worker that then dies must become re-claimable.

The autonomy engine's acceptance standard is "survives process death at every transition". This
file pins the drafting queue's claim->execute transition, which today does NOT survive it.

The defect, verified at HEAD:

  `claim_one_job` (workers/worker.py:62-89) selects `WHERE Job.status IN job_policy.CLAIMABLE`, and
  `CLAIMABLE = frozenset({JobStatus.QUEUED})` (shared/job_policy.py:21). The claim then writes
  `claimed_by` (worker.py:81) and `claimed_at` (worker.py:82) and flips the row to RUNNING.

  Those two columns have the exact shape of a lease and none of its enforcement. Every reader of
  `Job.claimed_at` in `src/` is display or ordering -- api/routers/jobs.py:241 (ORDER BY), :304,
  :329 (response projection), workers/pipeline_status.py:149,:153,:171. There is no
  `Job.claimed_at < cutoff` anywhere, so no predicate ever treats the claim as expirable.

  Contrast the adoption worker, which got the real treatment: `LEASE_TTL_S` at
  workers/import_adoption.py:94, expiry inside the claim predicate at :224-225, renewal at :332,
  release at :282-283, and boot recovery `recover_stale_adoptions` at :614 whose UPDATE resets
  `status`/`claimed_by`/`claimed_at` (:625-628).

  Boot recovery does not cover this either: api/main.py:83 resumes stranded **QUEUED** jobs only.

Net effect: a Job that is RUNNING when the process dies is stranded permanently -- invisible to
every claim path, never retried, never failed, and reported to the operator as an active draft.

These tests assert OBSERVABLE recovery (the job becomes claimable again), deliberately not the
existence of any particular named helper. A fix may add a TTL term to the claim predicate, a boot
reconciliation pass, a fencing token, or an Operational Hold transition -- any of those satisfies
this file, and a rename of the fix does not silently re-inert the guard.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from dominion.shared.enums import GateMode, JobKind, JobStatus
from dominion.shared.models import Book, Chapter, Job, Run
from dominion.workers import worker

# Far beyond any plausible lease TTL. The adoption worker's is 1800s
# (workers/import_adoption.py:94); a fix that picks any sane bound must consider this expired.
_LONG_DEAD = timedelta(hours=48)


async def _seed_queued_job(factory) -> int:
    """Minimal book -> chapter -> run -> QUEUED job. No beat/packet: nothing here drafts, it only
    claims, so the drafting preconditions are irrelevant to what these tests assert."""
    async with factory() as s:
        book = Book(title="Lease Recovery Book")
        s.add(book)
        await s.flush()
        chapter = Chapter(book_id=book.id, chapter_no=1, pov="Marcus")
        s.add(chapter)
        await s.flush()
        run = Run(
            book_id=book.id,
            scope_json={"chapter": 1, "scene": 1},
            gate_mode=GateMode.PAUSE_EACH,
            token_budget=40_000,
        )
        s.add(run)
        await s.flush()
        job = Job(
            run_id=run.id,
            book_id=book.id,
            kind=JobKind.DRAFT,
            chapter_no=1,
            scene_no=1,
            token_budget=40_000,
            status=JobStatus.QUEUED,
        )
        s.add(job)
        await s.commit()
        return job.id


async def test_positive_control_a_queued_job_is_claimable(db_factory):
    """Guard-is-not-inert control. If this fails, the seeding is broken and the RED tests below
    prove nothing about lease recovery."""
    job_id = await _seed_queued_job(db_factory)

    async with db_factory() as s:
        claimed = await worker.claim_one_job(s)
        assert claimed is not None, "seeded QUEUED job was not claimable -- fixture is broken"
        assert claimed.id == job_id
        assert claimed.status == JobStatus.RUNNING
        assert claimed.claimed_at is not None, "claim did not stamp claimed_at"
        await s.commit()


async def test_claim_stamps_a_lease_that_nothing_enforces(db_factory):
    """Documents the precondition for the two RED tests: the claim writes lease-shaped columns.

    This passes at HEAD. It exists so the RED failures below cannot be dismissed as "the columns
    were never written" -- they are written, they just bind nothing.
    """
    await _seed_queued_job(db_factory)

    async with db_factory() as s:
        claimed = await worker.claim_one_job(s)
        assert claimed is not None
        assert claimed.claimed_by, "claimed_by not stamped -- worker.py:81"
        assert claimed.claimed_at is not None, "claimed_at not stamped -- worker.py:82"
        await s.commit()


async def test_RED_stranded_running_job_is_reclaimable_after_lease_expiry(db_factory):
    """RED at HEAD. Process death between claim and completion must not strand the job forever.

    Sequence: claim a job (-> RUNNING, lease stamped), then simulate the worker process dying by
    never finishing it and backdating the lease well past expiry. A queue that survives process
    death must offer that job to the next worker.

    Fails at HEAD because `claim_one_job` filters on CLAIMABLE == {QUEUED} (job_policy.py:21) and
    consults `claimed_at` nowhere, so the RUNNING row is invisible to every claim path.
    """
    job_id = await _seed_queued_job(db_factory)

    # A worker claims it.
    async with db_factory() as s:
        claimed = await worker.claim_one_job(s)
        assert claimed is not None and claimed.id == job_id
        await s.commit()

    # That worker dies mid-generation: the row stays RUNNING and its lease goes stale.
    async with db_factory() as s:
        job = await s.get(Job, job_id)
        assert job is not None
        assert job.status == JobStatus.RUNNING
        job.claimed_at = datetime.now(UTC) - _LONG_DEAD
        await s.commit()

    # A replacement worker starts. The stranded job must be recoverable.
    async with db_factory() as s:
        recovered = await worker.claim_one_job(s)
        await s.commit()

    assert recovered is not None and recovered.id == job_id, (
        "stranded RUNNING job with a 48h-stale lease was not re-claimable. "
        "claim_one_job (worker.py:62) filters on CLAIMABLE == {QUEUED} (job_policy.py:21) and "
        "never reads claimed_at, so a worker that dies mid-draft strands the job permanently. "
        "Compare import_adoption.py:224-225, which does expire its lease in the claim predicate."
    )


async def test_RED_stranded_running_job_does_not_masquerade_as_active(db_factory):
    """RED at HEAD. A stranded job must not remain indistinguishable from a live draft.

    Whatever the fix, a job whose lease has expired must be observably NOT-RUNNING: requeued,
    failed, or held. Leaving it RUNNING is what makes the operator surface lie -- the Desk renders
    it from `claimed_at` ordering (api/routers/jobs.py:241, workers/pipeline_status.py:149-171) as
    an in-flight draft, with no signal that nothing is executing it.
    """
    job_id = await _seed_queued_job(db_factory)

    async with db_factory() as s:
        claimed = await worker.claim_one_job(s)
        assert claimed is not None
        await s.commit()

    async with db_factory() as s:
        job = await s.get(Job, job_id)
        assert job is not None
        job.claimed_at = datetime.now(UTC) - _LONG_DEAD
        await s.commit()

    # Give any recovery path the chance to run, exactly as a redeploy would.
    async with db_factory() as s:
        await worker.run_once(session_factory=db_factory)
        await s.commit()

    async with db_factory() as s:
        status = (await s.execute(select(Job.status).where(Job.id == job_id))).scalar_one()

    assert status != JobStatus.RUNNING, (
        f"job {job_id} is still RUNNING with a 48h-stale lease and no executing process. "
        "Nothing expires the claim (no `Job.claimed_at < cutoff` exists in src/) and boot "
        "recovery covers QUEUED only (api/main.py:83), so the operator surface reports a dead "
        "job as an active draft indefinitely."
    )
