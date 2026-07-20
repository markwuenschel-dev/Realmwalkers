"""REPAIR-GOD guard: the SceneFidelity production lifecycle (Lane 5 triage/materialization + Lane 6
author-controlled repair previews) lives in ``production_fidelity``, not ``production_repair``.

Mirrors the existing 'retired proxy is gone' hasattr pattern: the moved public callables must be ABSENT
from production_repair, PRESENT on production_fidelity (and be its entire owned public surface), and the
production API router must resolve its author-preview surface through production_fidelity.
"""

from __future__ import annotations

import inspect

# The public callables that moved out of production_repair into production_fidelity.
MOVED_PUBLIC = frozenset(
    {
        "create_repair_preview",
        "accept_repair_preview",
        "reject_repair_preview",
        "override_fidelity_issue",
        "triage_scene_fidelity_for_production",
    }
)


def test_moved_callables_left_production_repair():
    import dominion.workers.production_repair as pr

    for name in MOVED_PUBLIC:
        assert not hasattr(pr, name), f"{name!r} should have moved out of production_repair"


def test_production_fidelity_owns_moved_callables():
    import dominion.workers.production_fidelity as pf

    for name in MOVED_PUBLIC:
        assert inspect.iscoroutinefunction(getattr(pf, name, None)), f"production_fidelity is missing {name!r}"


def test_production_fidelity_public_surface_is_exactly_the_moved_callables():
    """production_fidelity OWNS exactly the five moved callables as public surface. The imported queue
    seam (``queue_repair_task_from_issues``) is excluded — it belongs to production_repair (its
    __module__) — so this can't silently accept a re-exported repair helper."""
    import dominion.workers.production_fidelity as pf

    owned_public = {
        name
        for name, obj in inspect.getmembers(pf, inspect.iscoroutinefunction)
        if not name.startswith("_") and obj.__module__ == pf.__name__
    }
    assert owned_public == set(MOVED_PUBLIC)


def test_router_fidelity_surface_is_production_fidelity():
    import dominion.workers.production_fidelity as pf
    from dominion.api.routers import production as prod_router

    # The router binds production_fidelity for its author-preview surface and no longer imports
    # production_repair for fidelity work.
    assert prod_router.production_fidelity is pf
    assert not hasattr(prod_router, "production_repair")
