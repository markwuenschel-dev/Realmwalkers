"""Offline agent smoke test harness."""

from __future__ import annotations

from dominion.api.agent_smoke import run_smoke_test


async def test_smoke_test_all_agents():
    out = await run_smoke_test(agents=None)
    assert len(out.results) == 7
    for row in out.results:
        assert row.checks
        assert row.setting


async def test_smoke_test_subset():
    out = await run_smoke_test(agents=["packet_qa_model"])
    assert len(out.results) == 1
    assert out.results[0].setting == "packet_qa_model"
