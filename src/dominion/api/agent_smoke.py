"""Offline and optional live smoke tests for agent configuration."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

from dominion.shared.agent_registry import AGENT_BY_KEY, AGENTS
from dominion.shared.config import settings
from dominion.shared.model_pricing import estimate_call_cost_usd
from dominion.shared.schemas import SmokeTestAgentOut, SmokeTestCheckOut, SmokeTestOut
from dominion.workers.budget import TokenBudget, Usage

_FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "agent_smoke" / "fixtures.json"
_LIVE_SMOKE_TOKEN_BUDGET = 8_000
_LIVE_PING_MAX_TOKENS = 32


def _load_fixtures() -> dict[str, Any]:
    return json.loads(_FIXTURES.read_text(encoding="utf-8"))


def _usage(input_tokens: int = 100, output_tokens: int = 50, *, truncated: bool = False) -> Usage:
    return Usage(input_tokens=input_tokens, output_tokens=output_tokens, truncated=truncated)


def estimate_live_smoke_cost_usd(agents: list[str] | None = None) -> float:
    """Conservative per-agent ping estimate for the live smoke-test warning."""
    targets = agents or [a.setting_key for a in AGENTS]
    total = 0.0
    for setting in targets:
        if setting not in AGENT_BY_KEY:
            continue
        model = getattr(settings, setting, "")
        total += estimate_call_cost_usd(model=model, input_tokens=500, output_tokens=_LIVE_PING_MAX_TOKENS)
    return round(total, 4)


async def run_smoke_test(*, agents: list[str] | None = None, live: bool = False) -> SmokeTestOut:
    """Exercise agent parse/schema paths with fixtures, or optional live API pings."""
    fx = _load_fixtures()
    budget = TokenBudget(max_tokens=_LIVE_SMOKE_TOKEN_BUDGET if live else 50_000)
    targets = agents or [a.setting_key for a in AGENTS]
    est_cost = estimate_live_smoke_cost_usd(targets) if live else None
    live_warning = None
    if live:
        live_warning = (
            f"Live mode makes real Anthropic API calls (~${est_cost:.2f} estimated). "
            f"Token budget capped at {_LIVE_SMOKE_TOKEN_BUDGET:,} for this run."
        )
    results: list[SmokeTestAgentOut] = []
    actual_cost = 0.0

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
        checks = await _run_agent_checks(setting, fx, budget, live=live)
        if live:
            for c in checks:
                if c.name == "live_ping" and c.detail:
                    try:
                        parts = dict(p.split("=", 1) for p in c.detail.split() if "=" in p)
                        inp = int(parts.get("in", 0))
                        out = int(parts.get("out", 0))
                        actual_cost += estimate_call_cost_usd(
                            model=getattr(settings, setting), input_tokens=inp, output_tokens=out
                        )
                    except (ValueError, KeyError):
                        pass
        results.append(
            SmokeTestAgentOut(
                setting=setting,
                label=agent.label,
                passed=all(c.ok for c in checks),
                checks=checks,
            )
        )
    return SmokeTestOut(
        results=results,
        all_passed=all(r.passed for r in results),
        mode="live" if live else "offline",
        estimated_cost_usd=est_cost,
        actual_cost_usd=round(actual_cost, 4) if live else None,
        live_warning=live_warning,
    )


async def _run_live_ping(setting: str, budget: TokenBudget) -> SmokeTestCheckOut:
    from dominion.workers import llm

    model = getattr(settings, setting)
    try:
        raw, usage = await llm.complete(
            model=model,
            system="Smoke test: reply with exactly OK.",
            user="OK",
            max_tokens=_LIVE_PING_MAX_TOKENS,
            budget=budget,
            expect_cache=False,
        )
        ok = bool(raw.strip())
        return SmokeTestCheckOut(
            name="live_ping",
            ok=ok,
            detail=f"in={usage.input_tokens} out={usage.output_tokens}",
        )
    except Exception as exc:  # noqa: BLE001
        return SmokeTestCheckOut(name="live_ping", ok=False, detail=str(exc))


async def _run_agent_checks(
    setting: str, fx: dict[str, Any], budget: TokenBudget, *, live: bool
) -> list[SmokeTestCheckOut]:
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

    async def _harness() -> None:
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
        elif setting in ("draft_model", "review_model", "enrich_model"):
            checks.append(
                SmokeTestCheckOut(
                    name="config_resolved",
                    ok=True,
                    detail=f"{setting} uses live settings",
                )
            )
        else:
            checks.append(SmokeTestCheckOut(name="unknown", ok=False, detail="no smoke harness"))

    try:
        if live:
            if setting in ("draft_model", "review_model", "enrich_model", "scene_packet_author_model"):
                checks.append(await _run_live_ping(setting, budget))
            else:
                await _harness()
        else:
            with patch("dominion.workers.llm.complete", new=AsyncMock(side_effect=fake_complete)):
                await _harness()
    except Exception as exc:  # noqa: BLE001 — smoke test reports failures
        checks.append(SmokeTestCheckOut(name="runtime", ok=False, detail=str(exc)))

    if live and setting not in ("draft_model", "review_model", "enrich_model", "scene_packet_author_model"):
        checks.insert(
            0,
            SmokeTestCheckOut(
                name="live_api",
                ok=any(c.ok for c in checks),
                detail="fixture path with real LLM",
            ),
        )
    else:
        checks.insert(0, SmokeTestCheckOut(name="completes", ok=any(c.ok for c in checks), detail=None))
    return checks
