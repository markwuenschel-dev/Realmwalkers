"""Packet QA agent (contract-first drafting, Phase 1).

A separate agent that ATTACKS the packet the author produced — the author must not validate its own
guardrails. It does not rewrite and does not draft; it returns a verdict plus the residual risks the
writer must still avoid. The orchestration fails closed: a malformed QA response blocks drafting.
"""
from __future__ import annotations

import json
from typing import Any

from dominion.shared.config import settings
from dominion.shared.enums import PacketVerdict
from dominion.workers import llm
from dominion.workers.budget import TokenBudget
from dominion.workers.packet.parse import extract_object, str_list

_QA_MAX_TOKENS = 3000

_SYSTEM = (
    "You are the Packet QA agent. Do NOT improve prose. Do NOT draft. Attack the packet.\n\n"
    "Check for: canon leaks, missing required beats, premature reveals, accidental roster changes, "
    "timeline contradictions, duplicate scene functions, later-chapter contamination, emotional beats "
    "assigned to the wrong character, and unresolved questions hidden as confident assumptions.\n\n"
    "Return exactly one of these verdicts: APPROVE, APPROVE_WARN, REVISE_REQUIRED, BLOCK_DRAFTING. "
    "BLOCK_DRAFTING means the packet is unsafe and no prose may be written from it. If the packet "
    "passes, list the top five risks the writer must still avoid.\n\n"
    "Reply with ONE JSON object only — no prose, no code fences — of shape:\n"
    '{"verdict": "APPROVE|APPROVE_WARN|REVISE_REQUIRED|BLOCK_DRAFTING", '
    '"residual_risks": [str], '
    '"issues": [{"kind": str, "detail": str}]}'
)

_VERDICTS = {v.value.upper(): v for v in PacketVerdict}


def build_prompt(packet: dict[str, Any]) -> str:
    return (
        "Attack this chapter knowledge packet and return your verdict.\n\nPACKET:\n"
        + json.dumps(packet, ensure_ascii=False, indent=2)
    )


def parse_qa(raw: str) -> dict[str, Any] | None:
    """Parse the QA response into {verdict, residual_risks, issues}, or None if unusable (fail
    closed). A recognizable object with an UNKNOWN verdict is treated as None — we never guess a
    verdict for a gate."""
    obj = extract_object(raw)
    if obj is None:
        return None
    verdict = _VERDICTS.get(str(obj.get("verdict", "")).strip().upper())
    if verdict is None:
        return None
    issues = obj.get("issues")
    return {
        "verdict": verdict,
        "residual_risks": str_list(obj.get("residual_risks")),
        "issues": issues if isinstance(issues, list) else [],
    }


async def qa_packet(packet: dict[str, Any], *, budget: TokenBudget) -> dict[str, Any] | None:
    """One bounded call -> {verdict, residual_risks, issues}, or None on a malformed response."""
    raw, _usage = await llm.complete(
        model=settings.packet_qa_model,
        system=_SYSTEM,
        user=build_prompt(packet),
        max_tokens=_QA_MAX_TOKENS,
        budget=budget,
    )
    return parse_qa(raw)
