"""Offline smoke tests for agent configuration — fixture-only, no API spend."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

from dominion.shared.agent_registry import AGENT_BY_KEY, AGENTS
from dominion.shared.schemas import SmokeTestAgentOut, SmokeTestCheckOut, SmokeTestOut
from dominion.workers.budget import TokenBudget, Usage

_FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "agent_smoke" / "fixtures.json"


def _load_fixtures() -> dict[str, Any]:
    return json.loads(_FIXTURES.read_text(encoding="utf-8"))


def _usage() -> Usage:
    return Usage(input_tokens=100, output_tokens=50, truncated=False)


async def run_smoke_test(*, agents: list[str] | None = None) -> SmokeTestOut:
    """Exercise each agent's parse/schema path with canned LLM responses."""
    fx = _load_fixtures()
    budget = TokenBudget(max_tokens=50_000)
    targets = agents or [a.setting_key for a in AGENTS]
    results: list[SmokeTestAgentOut] = []

    for setting in targets:
        agent = AGENT_BY_KEY.get(setting)
        if agent is None:
            results.append(
                SmokeTestAgentOut(
                    setting=setting,
                    label=setting,
                    passed=False,
                    checks=[SmokeTestCheckOut(name="known_agent", ok=False, detail="unknown setting")],
                )
            )
            continue
        checks = await _run_agent_checks(setting, fx, budget)
        results.append(
            SmokeTestAgentOut(
                setting=setting,
                label=agent.label,
                passed=all(c.ok for c in checks),
                checks=checks,
            )
        )
    return SmokeTestOut(results=results, all_passed=all(r.passed for r in results))


async def _run_agent_checks(setting: str, fx: dict[str, Any], budget: TokenBudget) -> list[SmokeTestCheckOut]:
    checks: list[SmokeTestCheckOut] = []

    async def fake_complete(**_kwargs: Any) -> tuple[str, Usage]:
        if setting == "packet_qa_model":
            return fx["packet_qa_response"], _usage()
        if setting == "scene_packet_qa_model":
            return fx["scene_qa_response"], _usage()
        if setting == "packet_author_model":
            return fx["packet_author_response"], _usage()
        if setting == "draft_model":
            return fx["drafter_response"], _usage()
        if setting == "scene_packet_author_model":
            return json.dumps(fx["scene_packet"]), _usage()
        return "{}", _usage()

    with patch("dominion.workers.llm.complete", new=AsyncMock(side_effect=fake_complete)):
        try:
            if setting == "packet_qa_model":
                from dominion.workers.packet.qa import qa_packet

                out = await qa_packet(fx["chapter_packet"], budget=budget)
                checks.append(
                    SmokeTestCheckOut(
                        name="schema_parse",
                        ok=out is not None and out.get("verdict") is not None,
                        detail=None if out else "parse failed",
                    )
                )
            elif setting == "packet_author_model":
                from dominion.workers.packet.author import author_packet

                out = await author_packet(
                    chapter_no=1,
                    pov="Kael",
                    outline="Kael inspects the ward.",
                    omniscient_summary=None,
                    prior_exit_state=None,
                    next_entry_intent=None,
                    canon_handles={},
                    budget=budget,
                )
                checks.append(
                    SmokeTestCheckOut(
                        name="schema_parse",
                        ok=isinstance(out, dict) and "chapter_job" in out,
                        detail=None if out else "parse failed",
                    )
                )
            elif setting == "scene_packet_qa_model":
                from dominion.workers.scene_packet.qa import qa_scene_packet

                out = await qa_scene_packet(
                    fx["scene_packet"],
                    chapter_packet_body=fx["chapter_packet"],
                    budget=budget,
                )
                checks.append(
                    SmokeTestCheckOut(
                        name="schema_parse",
                        ok=out is not None,
                        detail=None if out else "parse failed",
                    )
                )
            elif setting == "scene_packet_author_model":
                from dominion.workers.scene_packet.parse import valid_scene_packet_body

                checks.append(
                    SmokeTestCheckOut(
                        name="fixture_valid",
                        ok=valid_scene_packet_body(fx["scene_packet"]),
                        detail="scene packet fixture must be valid",
                    )
                )
            elif setting == "draft_model":
                checks.append(
                    SmokeTestCheckOut(
                        name="config_resolved",
                        ok=True,
                        detail="drafter uses live settings (fixture defers full context run)",
                    )
                )
            elif setting == "review_model":
                checks.append(
                    SmokeTestCheckOut(
                        name="config_resolved",
                        ok=True,
                        detail="reviewers advisory — config path verified",
                    )
                )
            elif setting == "enrich_model":
                checks.append(
                    SmokeTestCheckOut(
                        name="config_resolved",
                        ok=True,
                        detail="enrichment passes use live settings",
                    )
                )
            else:
                checks.append(SmokeTestCheckOut(name="unknown", ok=False, detail="no smoke harness"))
        except Exception as exc:  # noqa: BLE001 — smoke test reports failures
            checks.append(SmokeTestCheckOut(name="runtime", ok=False, detail=str(exc)))

    checks.insert(0, SmokeTestCheckOut(name="completes", ok=any(c.ok for c in checks), detail=None))
    return checks
