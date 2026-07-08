"""N1 red-capable coverage: ProductionRun mutation endpoints must return a well-formed ProductionRunOut
on the success path, not a `MissingGreenlet` 500.

cancel / resume / approve-final each mutate the run (status/current_stage), commit, then serialize via
`_run_out(run)` → `ProductionRunOut.model_validate`. Without a post-commit `session.refresh(run)` the
server-side `updated_at` (onupdate) is expired at flush, and the serialize triggers a sync lazy-load on
the async session → MissingGreenlet. These tests are red on the unpatched routers and green once the
refresh is added. See docs/plans/n1-greenlet-enrich-after-commit-contract.md (candidate N1).
"""

from __future__ import annotations

# Reuse the existing seeders — no parallel harness.
from test_approve_final_chapter import _run_with_final_chapter  # noqa: E402
from test_repair_tasks import _seed  # noqa: E402

from dominion.api.routers import production as production_router
from dominion.shared.schemas import ProductionRunOut


def _assert_run_out(out) -> None:
    assert isinstance(out, ProductionRunOut)
    assert out.updated_at is not None  # the column that greenlet-500s when unrefreshed


async def test_cancel_production_run_returns_well_formed_out(db_factory):
    async with db_factory() as s:
        _book, _chapter, run, _scenes = await _seed(s)
        out = await production_router.cancel_production_run(run.id, s)
        _assert_run_out(out)
        assert out.status == "cancelled"


async def test_resume_production_run_returns_well_formed_out(db_factory):
    async with db_factory() as s:
        _book, _chapter, run, _scenes = await _seed(s)
        out = await production_router.resume_production_run(run.id, s)
        _assert_run_out(out)


async def test_approve_final_chapter_returns_well_formed_out(db_factory):
    async with db_factory() as s:
        run, _art = await _run_with_final_chapter(s)
        out = await production_router.approve_final_chapter(run.id, s)
        _assert_run_out(out)
        assert out.status == "completed"
