"""Inject endpoint tests — lane selection, chaining, and partial failure. No DB, no network.

The endpoint's own job is narrow: turn a lane SELECTION into a chain of passes, run them in the
pipeline's canonical order, and report honestly on what ran. So these stub each lane's `run()` and
assert that wiring; the passes' own behaviour is covered in test_enrichment_passes.py.

The contract being pinned:
  - no selection => every lane (an empty multi-select must not make Enrich a no-op button)
  - canonical order (combat, sensory, dialogue) regardless of the order lanes were requested in
  - each lane transforms the PREVIOUS lane's output, as the drafting pipeline does
  - a failed lane never discards the lanes that already succeeded — 502 only when NOTHING ran
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from dominion.api.routers.enrich import EnrichIn, enrich
from dominion.workers.budget import BudgetExceeded
from dominion.workers.router import DRAFT_PASSES
from dominion.workers.specialists.base import PassError


def _stub_lanes(monkeypatch, failures: dict[str, BaseException] | None = None) -> list[str]:
    """Replace every lane's run() with a stub that appends its own name to the prose.

    Returns the list each stub records itself into, so a test can assert both WHICH lanes ran and in
    what order. Appending to the prose (rather than replacing it) is what makes chaining visible: a
    lane that read the original instead of its predecessor's output loses the earlier lane's mark.
    """
    failures = failures or {}
    calls: list[str] = []

    def make_stub(name: str):
        async def fake_run(prose, ctx):
            calls.append(name)
            if name in failures:
                raise failures[name]
            return f"{prose} +{name}"

        return fake_run

    for name, specialist in DRAFT_PASSES.items():
        monkeypatch.setattr(specialist, "run", make_stub(name))
    return calls


async def test_no_selection_runs_every_lane_chained_in_canonical_order(monkeypatch):
    calls = _stub_lanes(monkeypatch)
    out = await enrich(EnrichIn(prose="scene", lanes=None))

    assert calls == ["combat", "sensory", "dialogue"]
    assert out.lanes_run == ["combat", "sensory", "dialogue"]
    assert out.lanes_failed == []
    # Every mark present and in order => each lane transformed its predecessor's output, not the
    # author's original.
    assert out.enriched == "scene +combat +sensory +dialogue"


async def test_empty_list_is_the_same_as_no_selection(monkeypatch):
    """The panel sends [] for "nothing picked" — it must mean all lanes, not zero lanes."""
    _stub_lanes(monkeypatch)
    out = await enrich(EnrichIn(prose="scene", lanes=[]))
    assert out.lanes_run == ["combat", "sensory", "dialogue"]


async def test_requested_order_does_not_change_run_order(monkeypatch):
    """Selection is a SET. Clicking dialogue first must not run it against un-sharpened choreography."""
    calls = _stub_lanes(monkeypatch)
    out = await enrich(EnrichIn(prose="scene", lanes=["dialogue", "combat"]))

    assert calls == ["combat", "dialogue"]
    assert out.lanes_run == ["combat", "dialogue"]
    assert out.enriched == "scene +combat +dialogue"


async def test_a_subset_runs_only_what_was_asked_for(monkeypatch):
    calls = _stub_lanes(monkeypatch)
    out = await enrich(EnrichIn(prose="scene", lanes=["sensory"]))

    assert calls == ["sensory"]
    assert out.lanes_run == ["sensory"]
    assert out.enriched == "scene +sensory"


async def test_duplicate_lanes_run_once(monkeypatch):
    calls = _stub_lanes(monkeypatch)
    await enrich(EnrichIn(prose="scene", lanes=["combat", "combat"]))
    assert calls == ["combat"]


async def test_failed_lane_keeps_the_lanes_that_succeeded(monkeypatch):
    """The decision: a mid-chain failure never throws away real prose the author already paid for."""
    calls = _stub_lanes(monkeypatch, failures={"sensory": PassError("sensory pass returned empty output")})
    out = await enrich(EnrichIn(prose="scene", lanes=None))

    assert calls == ["combat", "sensory", "dialogue"]  # a soft failure does not abort the chain
    assert out.lanes_run == ["combat", "dialogue"]
    assert [f.lane for f in out.lanes_failed] == ["sensory"]
    assert "empty output" in out.lanes_failed[0].reason
    # dialogue picked up combat's output — the failed lane is skipped, not fatal to what follows.
    assert out.enriched == "scene +combat +dialogue"


async def test_every_lane_failing_is_a_502_not_your_own_prose_back(monkeypatch):
    """With nothing enriched there is no result — returning the source as one would be a lie."""
    _stub_lanes(
        monkeypatch,
        failures={name: PassError(f"{name} boom") for name in DRAFT_PASSES},
    )
    with pytest.raises(HTTPException) as exc:
        await enrich(EnrichIn(prose="scene", lanes=None))

    assert exc.value.status_code == 502
    assert "combat boom" in exc.value.detail


async def test_budget_exhaustion_stops_the_chain(monkeypatch):
    """The budget is shared across the chain: once gone, later lanes cannot succeed — don't bill for them."""
    calls = _stub_lanes(monkeypatch, failures={"sensory": BudgetExceeded("out of tokens")})
    out = await enrich(EnrichIn(prose="scene", lanes=None))

    assert calls == ["combat", "sensory"]  # dialogue never attempted
    assert out.lanes_run == ["combat"]
    assert [f.lane for f in out.lanes_failed] == ["sensory"]
    assert out.enriched == "scene +combat"


async def test_unknown_lane_is_rejected(monkeypatch):
    _stub_lanes(monkeypatch)
    with pytest.raises(HTTPException) as exc:
        await enrich(EnrichIn(prose="scene", lanes=["combat", "telepathy"]))

    assert exc.value.status_code == 422
    assert "telepathy" in exc.value.detail
