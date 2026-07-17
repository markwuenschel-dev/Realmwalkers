"""Regression coverage for the GLOBALS integrity-audit finding.

`agent_ops_state.globals_json` was added to an already-existing table via a bare
`ALTER TABLE ... ADD COLUMN IF NOT EXISTS globals_json JSONB` (see migrations.py) with no DEFAULT
and no backfill UPDATE, so a pre-migration ("legacy") row can carry `globals_json = NULL` in the
database even though the ORM model types the attribute as a non-Optional `dict[str, Any]`
(models.py's AgentOpsState.globals_json). `apply_globals`'s merge line read
`dict(row.globals_json if row else {})`, which evaluates to `dict(None)` for exactly that legacy
row and raises TypeError, 500-ing `PUT /settings/agents/globals`. Two sibling call sites
(`_globals_out` and the globals branch of `apply_model_overrides`) already guarded the same field
against None; this pins the third (apply_globals's merge) now that it does too.
"""

from __future__ import annotations

import pytest

from dominion.shared import agent_ops
from dominion.shared.config import settings
from dominion.shared.models import AgentOpsState
from dominion.shared.schemas import AgentGlobalsUpdateIn


@pytest.fixture(autouse=True)
def _restore_globals_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """apply_globals mutates the process-global `settings` singleton on success; restore it after
    each test so this file can't leak scene budget values into tests that run later in the same
    pytest process."""
    monkeypatch.setattr(settings, "scene_token_budget", settings.scene_token_budget)
    monkeypatch.setattr(settings, "scene_time_budget_s", settings.scene_time_budget_s)


async def test_apply_globals_survives_legacy_null_globals_json(db_factory):
    """A pre-backfill row (globals_json NULL) must not TypeError `dict(None)` in apply_globals."""
    async with db_factory() as session:
        session.add(AgentOpsState(id=agent_ops._OPS_STATE_ID, globals_json=None))  # type: ignore[arg-type]
        await session.flush()

        out = await agent_ops.apply_globals(session, AgentGlobalsUpdateIn(scene_token_budget=12_000))

    assert out.globals.scene_token_budget == 12_000
    # scene_time_budget_s was never set (row's globals_json was NULL, body didn't touch it) -- must
    # still resolve to a real int via the settings fallback in _globals_out, not blow up upstream.
    assert isinstance(out.globals.scene_time_budget_s, int)


async def test_apply_globals_merges_into_existing_globals_json(db_factory):
    """Sanity check: the normal (non-null) path still merges rather than clobbering the other key."""
    async with db_factory() as session:
        session.add(
            AgentOpsState(
                id=agent_ops._OPS_STATE_ID,
                globals_json={"scene_token_budget": 40_000, "scene_time_budget_s": 900},
            )
        )
        await session.flush()

        out = await agent_ops.apply_globals(session, AgentGlobalsUpdateIn(scene_time_budget_s=1_200))

    assert out.globals.scene_time_budget_s == 1_200
    assert out.globals.scene_token_budget == 40_000  # untouched key survives the merge
