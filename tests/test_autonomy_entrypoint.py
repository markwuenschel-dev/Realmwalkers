"""The AutonomyDriver finally has a production caller, and it tells the truth about what it did.

The driver, its four states, its funnel and its acceptance tests landed on 2026-08-23 and nothing
outside the test suite ever constructed it — the unattended loop was proven correct and never ran.
Wiring it exposed three defects that would each have made the loop lie:

  1. the executor claimed the GLOBALLY oldest job, so a per-chapter loop would draft a different
     chapter and report it as progress on this one;
  2. `queue_draft_jobs_for_missing_sequence_scenes` returns `[]` for "done" AND for every kind of
     "blocked", and the driver reads a `None` action as convergence;
  3. neither kill switch that gates every other autonomous path was consulted.

Each has a test here, and each test is written so the pre-fix code fails it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from dominion.shared.enums import JobKind, JobStatus
from dominion.shared.models import Book, Chapter, Job
from dominion.workers import autonomy_action, worker


async def _two_chapters(s) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    book = Book(title="Dominion Realm")
    s.add(book)
    await s.flush()
    a = Chapter(book_id=book.id, chapter_no=1, pov="Marcus")
    b = Chapter(book_id=book.id, chapter_no=2, pov="Serra")
    s.add_all([a, b])
    await s.flush()
    return book.id, a.id, b.id


def _job(*, book_id, chapter_id, status=JobStatus.QUEUED, claimed_at=None) -> Job:
    return Job(
        book_id=book_id,
        chapter_id=chapter_id,
        kind=JobKind.DRAFT,
        token_budget=1000,
        status=status.value if hasattr(status, "value") else status,
        claimed_at=claimed_at,
        claimed_by="someone" if claimed_at else None,
    )


# =================================================================================================
# Defect 1 — the executor was global
# =================================================================================================


async def test_a_scoped_claim_will_not_take_another_chapters_job(db_factory):
    """The whole reason the driver could not be wired honestly.

    Chapter B's job is OLDER, so an unscoped claim takes it — and `run_once` would return True, which
    the driver reports as progress on chapter A. The scope has to be in the query, not in the caller's
    interpretation of the result.
    """
    async with db_factory() as s:
        book_id, chapter_a, chapter_b = await _two_chapters(s)
        s.add(_job(book_id=book_id, chapter_id=chapter_b))  # older
        await s.flush()
        s.add(_job(book_id=book_id, chapter_id=chapter_a))
        await s.commit()

    async with db_factory() as s2:
        claimed = await worker.claim_one_job(s2, chapter_id=chapter_a)
        assert claimed is not None, "the scoped claim found nothing at all"
        assert claimed.chapter_id == chapter_a, "the scoped claim took another chapter's job"

    async with db_factory() as s3:
        unscoped = await worker.claim_one_job(s3)
        assert unscoped is not None
        assert unscoped.chapter_id == chapter_b, (
            "the UNSCOPED claim should still take the globally oldest job — the shared worker depends "
            "on that, and narrowing it for everyone would be a different change"
        )


async def test_a_scoped_claim_finds_nothing_when_the_chapter_is_idle(db_factory):
    """A chapter with no work must report no work, even while the queue is full of other chapters."""
    async with db_factory() as s:
        book_id, chapter_a, chapter_b = await _two_chapters(s)
        s.add(_job(book_id=book_id, chapter_id=chapter_b))
        await s.commit()

    async with db_factory() as s2:
        assert await worker.claim_one_job(s2, chapter_id=chapter_a) is None


async def test_stale_recovery_is_scoped_too(db_factory):
    """`run_once` returns True after recovering a corpse, and True is reported as progress.

    An unscoped sweep would let another chapter's expired lease count as work done on this one — the
    same lie as defect 1, arriving through a different door.
    """
    stale = datetime.now(UTC) - timedelta(days=1)
    async with db_factory() as s:
        book_id, chapter_a, chapter_b = await _two_chapters(s)
        s.add(_job(book_id=book_id, chapter_id=chapter_b, status=JobStatus.RUNNING, claimed_at=stale))
        await s.commit()

    async with db_factory() as s2:
        assert await worker.recover_stale_jobs(s2, chapter_id=chapter_a) == 0, (
            "recovered another chapter's stale job while scoped to this one"
        )
        assert await worker.recover_stale_jobs(s2, chapter_id=chapter_b) == 1


# =================================================================================================
# Defects 2 and 3 — blocked must not read as done, and the kill switches must be honoured
# =================================================================================================


async def test_a_paused_queue_is_an_operational_block_not_silence(db_factory, monkeypatch):
    """A paused queue used to be invisible to the driver.

    That is worse than it sounds: the drain no-ops while the action keeps minting jobs, so the loop
    burns its whole tick ceiling, bills nothing, and reports nothing wrong. The probe must name it.
    """

    async def paused(_session):
        return True

    monkeypatch.setattr(autonomy_action.background_work, "load_queue_paused", paused)
    async with db_factory() as s:
        _book_id, chapter_a, _b = await _two_chapters(s)
        await s.commit()

    probe = autonomy_action.make_operational_probe(chapter_a, session_factory=db_factory)
    failure = await probe()
    assert failure is not None, "a paused queue was not reported as an operational block"
    assert "PAUSED" in failure


async def test_a_chapter_that_cannot_draft_blocks_rather_than_reporting_nothing_to_do(db_factory, monkeypatch):
    """Defect 2, the one that would have been hardest to notice.

    `queue_draft_jobs_for_missing_sequence_scenes` returns `[]` for a finished chapter and for a
    missing sequence, a refused structural gate, a missing ScenePacket and a parked run. The driver
    maps a None action to STOP_NOTHING_TO_DO, which an operator reads as "the machine finished". A
    block has to arrive as a REASON, and the probe is the only channel that carries one.
    """

    class _Blocked:
        can_draft = False
        disabled_reason = "no approved chapter sequence"
        provider_rate_limited = False
        missing_scene_drafts: list[int] = []

    async def blocked(_session, _chapter_id):
        return _Blocked()

    monkeypatch.setattr(autonomy_action.draft_readiness, "compute_draft_readiness", blocked)
    async with db_factory() as s:
        _book_id, chapter_a, _b = await _two_chapters(s)
        await s.commit()

    probe = autonomy_action.make_operational_probe(chapter_a, session_factory=db_factory)
    failure = await probe()
    assert failure is not None, "a chapter that cannot draft reported no operational problem"
    assert "no approved chapter sequence" in failure
    assert "not a converged chapter" in failure, "the block must say it is not convergence"


async def test_a_healthy_chapter_reports_no_operational_failure(db_factory, monkeypatch):
    """The other direction. A probe that always fails would pass every test above and stop the loop
    forever, so the permissive case is pinned too."""

    class _Ready:
        can_draft = True
        disabled_reason = None
        provider_rate_limited = False
        missing_scene_drafts = [3]

    async def ready(_session, _chapter_id):
        return _Ready()

    async def not_paused(_session):
        return False

    monkeypatch.setattr(autonomy_action.draft_readiness, "compute_draft_readiness", ready)
    monkeypatch.setattr(autonomy_action.background_work, "load_queue_paused", not_paused)
    async with db_factory() as s:
        _book_id, chapter_a, _b = await _two_chapters(s)
        await s.commit()

    probe = autonomy_action.make_operational_probe(chapter_a, session_factory=db_factory)
    assert await probe() is None


async def test_planning_is_read_only(db_factory, monkeypatch):
    """The dry run's promise: it reports what it would draft without minting a job.

    Pinned by making the minting selector explode. If the plan path ever starts calling it, this fails
    instead of quietly billing an operator who typed a command documented as free.
    """

    def explode(*_a, **_k):
        raise AssertionError("the dry-run plan called the job-minting selector")

    monkeypatch.setattr(autonomy_action.production, "queue_draft_jobs_for_missing_sequence_scenes", explode)

    class _Ready:
        can_draft = True
        disabled_reason = None
        provider_rate_limited = False
        missing_scene_drafts = [4, 5]

    async def ready(_session, _chapter_id):
        return _Ready()

    monkeypatch.setattr(autonomy_action.draft_readiness, "compute_draft_readiness", ready)
    async with db_factory() as s:
        _book_id, chapter_a, _b = await _two_chapters(s)
        await s.commit()
        plan = await autonomy_action.plan_chapter_work(s, chapter_a)

    assert plan.has_work
    assert plan.scene_nos == [4, 5]
    assert "would draft scene 4" in plan.describe()

    async with db_factory() as s2:
        remaining = await worker.claim_one_job(s2, chapter_id=chapter_a)
        assert remaining is None, "the dry-run plan left a job queued"


# =================================================================================================
# The regression guard: the driver must keep having a caller
# =================================================================================================


def test_the_driver_has_a_production_caller():
    """The defect this whole change exists to close.

    `AutonomyDriver` was constructed in exactly two test files and nowhere else, so the unattended
    loop was dead code that read as a delivered feature. Asserting on the entrypoint's own module is
    what stops that recurring silently — a `grep` in a docket is not a gate.
    """
    import tomllib
    from pathlib import Path

    from dominion.workers import autonomy_cli

    src = Path(autonomy_cli.__file__).read_text(encoding="utf-8")
    assert "AutonomyDriver(" in src, "the entrypoint no longer constructs the driver"
    assert "ChapterDraftAction()" in src, "the entrypoint no longer injects a real action"

    pyproject = tomllib.loads(Path(__file__).resolve().parents[1].joinpath("pyproject.toml").read_text("utf-8"))
    scripts = pyproject["project"]["scripts"]
    assert scripts.get("dominion-autonomy") == "dominion.workers.autonomy_cli:main", (
        "the console entrypoint is gone — the driver would be unreachable again"
    )


def test_the_live_path_is_opt_in():
    """Spend is a decision the operator types, not a default they discover afterwards."""
    import inspect

    from dominion.workers import autonomy_cli

    src = inspect.getsource(autonomy_cli.main)
    assert '"--live"' in src and 'action="store_true"' in src, "--live is no longer an opt-in boolean flag"
    assert "if args.live else _dry_run" in src, "the default path is no longer the dry run"
