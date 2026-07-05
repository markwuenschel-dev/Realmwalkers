"""The deterministic editorial agents are surfaced read-only in the Agent Ops response.

These agents (from workers/production.py, all agent_role="deterministic") have no model and cost $0.
They must appear in `AgentOpsOut.editorial_agents` for the Agents tab roster, but must NEVER leak
into the model-resolution registry (AGENTS / ROLE_KEYS). The pure-metadata assertions run without a
DB; the build_agent_ops parity test uses real Postgres and skips when it's unreachable (see conftest).
"""

from __future__ import annotations

from dominion.shared import agent_ops
from dominion.shared.agent_registry import EDITORIAL_AGENTS, ROLE_KEYS
from dominion.shared.schemas import EditorialAgentOut

_EXPECTED_NAMES = {
    "contract_classifier",
    "chapter_sequence_planner",
    "issue_normalizer",
    "issue_triage_evaluator",
    "repair_scheduler",
    "repair_verifier",
}


def test_editorial_agents_roster_is_complete_and_described():
    names = [ea.name for ea in EDITORIAL_AGENTS]
    assert set(names) == _EXPECTED_NAMES
    assert len(names) == len(set(names)) == 6
    for ea in EDITORIAL_AGENTS:
        assert ea.label.strip(), ea.name
        assert ea.description.strip(), ea.name
        assert ea.stage.strip(), ea.name


def test_editorial_agents_never_enter_model_resolution():
    # They are metadata only -- keeping them out of ROLE_KEYS is what protects apply_model_overrides
    # (which iterates AGENTS expecting a setting_key + tiers) from ever trying to resolve a model.
    assert _EXPECTED_NAMES.isdisjoint(ROLE_KEYS)


def test_editorial_agents_out_projection():
    out = agent_ops._editorial_agents_out()
    assert [ea.name for ea in out] == [ea.name for ea in EDITORIAL_AGENTS]
    for row in out:
        assert isinstance(row, EditorialAgentOut)
        assert row.deterministic is True
        assert row.description.strip()


async def test_build_agent_ops_includes_editorial_agents(db_factory):
    async with db_factory() as session:
        ops = await agent_ops.build_agent_ops(session)

    by_name = {ea.name: ea for ea in ops.editorial_agents}
    assert set(by_name) == _EXPECTED_NAMES
    for ea in ops.editorial_agents:
        assert ea.deterministic is True
        assert ea.description.strip()
        assert ea.stage.strip()

    # The read-only roster must not collide with the configurable, model-resolved agents.
    configurable = {a.setting for a in ops.agents}
    assert set(by_name).isdisjoint(configurable)
