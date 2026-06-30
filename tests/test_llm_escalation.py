"""Tests for llm_escalation helper."""

from __future__ import annotations

import pytest

from dominion.shared.config import settings as cfg
from dominion.workers.budget import Usage
from dominion.workers.llm_escalation import attempt_with_escalation, policy_for_setting


@pytest.mark.asyncio
async def test_escalates_on_unparseable(monkeypatch):
    monkeypatch.setattr(cfg, "packet_qa_fallback_model", "claude-sonnet-5")
    calls: list[str] = []

    async def attempt_fn(model: str, max_tokens: int) -> tuple[dict | None, Usage]:
        calls.append(model)
        if model == cfg.packet_qa_model:
            return None, Usage(input_tokens=10, output_tokens=5, truncated=False)
        return {"verdict": "ok"}, Usage(input_tokens=10, output_tokens=5, truncated=False)

    result, model, escalated = await attempt_with_escalation(
        setting_key="packet_qa_model",
        primary_model=cfg.packet_qa_model,
        primary_max_tokens=1000,
        attempt_fn=attempt_fn,
        is_success=lambda v: v is not None,
        policy=policy_for_setting("packet_qa_model"),
    )
    assert escalated is True
    assert model == "claude-sonnet-5"
    assert result == {"verdict": "ok"}
    assert calls == [cfg.packet_qa_model, "claude-sonnet-5"]


@pytest.mark.asyncio
async def test_no_escalation_when_primary_succeeds(monkeypatch):
    monkeypatch.setattr(cfg, "packet_qa_fallback_model", "claude-sonnet-5")

    async def attempt_fn(model: str, max_tokens: int) -> tuple[dict | None, Usage]:
        return {"ok": True}, Usage(input_tokens=1, output_tokens=1, truncated=False)

    _result, model, escalated = await attempt_with_escalation(
        setting_key="packet_qa_model",
        primary_model=cfg.packet_qa_model,
        primary_max_tokens=1000,
        attempt_fn=attempt_fn,
        is_success=lambda v: v is not None,
        policy=policy_for_setting("packet_qa_model"),
    )
    assert escalated is False
    assert model == cfg.packet_qa_model


@pytest.mark.asyncio
async def test_escalates_on_truncation_even_when_value_ok(monkeypatch):
    monkeypatch.setattr(cfg, "draft_fallback_model", "claude-sonnet-5")
    n = 0

    async def attempt_fn(model: str, max_tokens: int) -> tuple[str, Usage]:
        nonlocal n
        n += 1
        if n == 1:
            return "partial prose", Usage(input_tokens=1, output_tokens=1, truncated=True)
        return "full prose from fallback", Usage(input_tokens=1, output_tokens=1, truncated=False)

    text, model, escalated = await attempt_with_escalation(
        setting_key="draft_model",
        primary_model=cfg.draft_model,
        primary_max_tokens=4000,
        attempt_fn=attempt_fn,
        is_success=lambda t: bool(t),
        policy=policy_for_setting("draft_model"),
    )
    assert escalated is True
    assert text == "full prose from fallback"


@pytest.mark.asyncio
async def test_escalates_on_semantic_qa_risk(monkeypatch):
    monkeypatch.setattr(cfg, "packet_qa_fallback_model", "claude-sonnet-5")
    from dominion.shared.agent_policy import load_runtime_policies
    from dominion.shared.risk_scorer import qa_result_preferred, score_qa_result, should_semantic_escalate

    load_runtime_policies({"packet_qa_model": {"semantic_escalation": True}})

    async def attempt_fn(model: str, max_tokens: int) -> tuple[dict | None, Usage]:
        if model == cfg.packet_qa_model:
            return (
                {"verdict": "revise_required", "issues": [{"kind": "canon_leak"}]},
                Usage(input_tokens=10, output_tokens=5, truncated=False),
            )
        return (
            {"verdict": "approve_warn", "issues": []},
            Usage(input_tokens=10, output_tokens=5, truncated=False),
        )

    def _semantic(v):
        return should_semantic_escalate(score_qa_result(v))

    result, model, escalated = await attempt_with_escalation(
        setting_key="packet_qa_model",
        primary_model=cfg.packet_qa_model,
        primary_max_tokens=1000,
        attempt_fn=attempt_fn,
        is_success=lambda v: v is not None,
        policy=policy_for_setting("packet_qa_model"),
        semantic_escalate=_semantic,
        pick_preferred=qa_result_preferred,
    )
    assert escalated is True
    assert model == "claude-sonnet-5"
    assert result["verdict"] == "approve_warn"
