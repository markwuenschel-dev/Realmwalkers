"""Lane 3B — report telemetry/provenance (ADR 0014).

Every report records the requested model, prompt/facade/schema versions, and per-mode adapter status,
as provenance — never authority. DB-backed with the fake adapters from the evaluator test.
"""

from __future__ import annotations

from test_scene_fidelity_evaluator import _body, _clause, _req, _runner, _seed

from dominion.shared.config import settings
from dominion.workers.scene_fidelity.evaluator import evaluate_scene_fidelity


async def test_report_records_model_and_version_provenance(db_factory) -> None:
    body = _body([_req("req-1", "relationship_turn", [_clause("cl-1")])])
    async with db_factory() as s:
        scene, da, sp = await _seed(s, body)
        art = await evaluate_scene_fidelity(
            s,
            scene=scene,
            draft_attempt=da,
            packet=sp,
            trigger="manual",
            adapter_runner=_runner({"cl-1": ("lost", [])}),
        )
        tele = art.body["evaluation_telemetry"]
    assert tele["trigger"] == "manual"
    assert tele["requested_model"] == settings.scene_fidelity_model
    assert tele["prompt_version"] == settings.scene_fidelity_prompt_version
    assert tele["facade_version"] == settings.scene_fidelity_facade_version
    assert tele["report_schema_version"] == settings.scene_fidelity_report_schema_version
    assert art.body["report_schema_version"] == settings.scene_fidelity_report_schema_version


async def test_report_records_per_mode_adapter_status(db_factory) -> None:
    body = _body(
        [
            _req("req-1", "relationship_turn", [_clause("cl-1")]),
            _req("req-2", "combat_blocking", [_clause("cl-2")]),
        ]
    )
    async with db_factory() as s:
        scene, da, sp = await _seed(s, body)
        art = await evaluate_scene_fidelity(
            s,
            scene=scene,
            draft_attempt=da,
            packet=sp,
            trigger="manual",
            adapter_runner=_runner({"cl-1": ("satisfied", [])}, failed_modes=["combat_blocking"]),
        )
        tele = art.body["evaluation_telemetry"]
    statuses = {a["mode"]: a["status"] for a in tele["adapters"]}
    assert statuses == {"relationship_turn": "ok", "combat_blocking": "failed"}
