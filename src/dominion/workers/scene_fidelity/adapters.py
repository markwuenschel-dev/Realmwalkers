"""Bounded, mode-owned evaluation adapters (Lane 3B).

An adapter evaluates the clauses ONE mode owns and returns raw per-clause findings — never a merged
report, never a verdict with authority. The facade (evaluator.py) fans these out under a bounded
semaphore, catches failures per mode, and merges deterministically. The real adapter rides the same
``attempt_with_escalation`` + ``llm.complete`` path (and the same ``scene_fidelity_model`` fallback chain)
as every other agent; tests inject a fake ``AdapterRunner`` so no live model is called.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from dominion.shared.config import settings
from dominion.workers import llm
from dominion.workers.budget import TokenBudget
from dominion.workers.llm_escalation import attempt_with_escalation, policy_for_setting
from dominion.workers.scene_fidelity import prompts
from dominion.workers.scene_fidelity.models import EvidenceAnchor, FidelityMode

ADAPTER_MAX_TOKENS = 2000

# The subset of results a model may REPORT. The merger owns blocked_by_dependency / not_evaluated /
# adapter_failed — a model never assigns those.
ReportedResult = Literal["satisfied", "lost", "indeterminate"]


class RawFinding(BaseModel):
    """One clause finding as reported by an adapter. Anchors are schema-valid EvidenceAnchors; whether an
    anchor is SEMANTICALLY valid (span in range, quote matches prose) is checked later by policy."""

    model_config = ConfigDict(extra="ignore")

    clause_id: str
    result: ReportedResult
    evidence_anchors: list[EvidenceAnchor] = Field(default_factory=list)
    explanation: str = ""


@dataclass
class AdapterOutcome:
    """The result of running one mode's adapter. ``status='failed'`` means the adapter could not produce a
    usable response (unparseable / no approved model) — the facade turns that into ``adapter_failed``
    evaluations for every clause the mode owns, never a silent omission (ADR 0013/0022)."""

    mode: str
    findings: list[RawFinding]
    model_requested: str
    model_used: str
    escalated: bool
    status: Literal["ok", "failed"]
    error: str | None = None


# An AdapterRunner evaluates one mode's clauses. Injectable so the facade is testable without a live model.
AdapterRunner = Callable[..., Awaitable[AdapterOutcome]]


def _parse(raw_text: str, owned_ids: set[str]) -> list[RawFinding] | None:
    """Parse the model's JSON into findings for OWNED clauses only. Returns None on unparseable output
    (so escalation triggers); drops findings for unknown clause_ids and schema-invalid anchors."""
    try:
        data = json.loads(raw_text)
    except (json.JSONDecodeError, TypeError):
        return None
    findings_raw = data.get("findings") if isinstance(data, dict) else None
    if not isinstance(findings_raw, list):
        return None
    out: list[RawFinding] = []
    for item in findings_raw:
        if not isinstance(item, dict) or item.get("clause_id") not in owned_ids:
            continue
        anchors: list[EvidenceAnchor] = []
        for a in item.get("evidence_anchors") or []:
            try:
                anchors.append(EvidenceAnchor(**a))
            except (ValidationError, TypeError):
                continue  # keep the finding; a bad anchor is a policy diagnostic, not a parse failure
        try:
            out.append(
                RawFinding.model_validate(
                    {
                        "clause_id": item["clause_id"],
                        "result": item.get("result"),
                        "evidence_anchors": anchors,
                        "explanation": str(item.get("explanation") or ""),
                    }
                )
            )
        except ValidationError:
            continue  # a bad result value (not satisfied/lost/indeterminate) drops just this finding
    return out


async def run_mode_adapter(
    mode: FidelityMode,
    clauses: list[dict[str, Any]],
    *,
    prose: str,
    scene_context: dict[str, Any] | None,
    budget: TokenBudget,
) -> AdapterOutcome:
    """The real adapter: one bounded LLM call for a mode, escalating on unparseable output through the
    approved fallback chain. Never raises for a bad model response — returns status='failed' so the facade
    records complete coverage."""
    owned_ids = {c["clause_id"] for c in clauses if isinstance(c.get("clause_id"), str)}
    system = prompts.system_prompt(mode)
    user = prompts.user_prompt(clauses, prose=prose, scene_context=scene_context)
    requested = settings.scene_fidelity_model

    async def _attempt(model: str, max_tokens: int) -> tuple[list[RawFinding] | None, Any]:
        text, usage = await llm.complete(
            model=model,
            system=system,
            user=user,
            max_tokens=max_tokens,
            budget=budget,
            setting_key="scene_fidelity_model",
        )
        return _parse(text, owned_ids), usage

    findings, model_used, escalated = await attempt_with_escalation(
        setting_key="scene_fidelity_model",
        primary_model=requested,
        primary_max_tokens=ADAPTER_MAX_TOKENS,
        attempt_fn=_attempt,
        is_success=lambda v: v is not None,
        policy=policy_for_setting("scene_fidelity_model"),
    )
    if findings is None:
        return AdapterOutcome(
            mode.value, [], requested, model_used, escalated, "failed", "unparseable adapter response"
        )
    return AdapterOutcome(mode.value, findings, requested, model_used, escalated, "ok")
