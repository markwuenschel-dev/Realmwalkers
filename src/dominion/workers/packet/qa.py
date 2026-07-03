"""Packet QA agent (contract-first drafting, Phase 1).

A separate agent that ATTACKS the packet the author produced — the author must not validate its own
guardrails. It does not rewrite and does not draft; it returns a verdict plus the residual risks the
writer must still avoid. The orchestration fails closed: a malformed QA response blocks drafting.
"""

from __future__ import annotations

import json
from typing import Any

from dominion.shared.agent_policy import get_runtime_policy
from dominion.shared.config import settings
from dominion.shared.enums import PacketVerdict
from dominion.shared.grading import parse_score
from dominion.shared.risk_scorer import qa_result_preferred, score_qa_result, should_semantic_escalate
from dominion.shared.severity import normalize_llm_issue
from dominion.workers import llm
from dominion.workers.budget import TokenBudget
from dominion.workers.llm_escalation import attempt_with_escalation, policy_for_setting
from dominion.workers.packet.parse import extract_object, str_list

_QA_MAX_TOKENS = 3000

_SYSTEM = (
    "You are the Packet QA agent. Do NOT improve prose. Do NOT draft. Attack the packet.\n\n"
    "Check for: canon leaks, missing required beats, premature reveals, accidental roster changes, "
    "timeline contradictions, duplicate scene functions, later-chapter contamination, emotional beats "
    "assigned to the wrong character, and unresolved questions hidden as confident assumptions.\n\n"
    "Roster-bucket consistency: each character belongs in exactly one of characters_present / "
    "characters_absent / characters_mentioned_only / characters_forbidden. Cross-check that bucket "
    "against how the SAME character is described elsewhere in the packet (roster_locks, claims, "
    "scene_seeds) — if that prose hedges toward an on-page role ('may be mentioned', 'has dialogue', "
    "'present but minor', appears in a scene_seed's required_beats) while the character sits in "
    "characters_absent, flag it as a roster_bucket_mismatch: characters_absent means NO role in the "
    "chapter whatsoever, not merely 'minor' or 'background' — a character with even one line or beat "
    "belongs in characters_present instead.\n\n"
    "Return exactly one of these verdicts: APPROVE, APPROVE_WARN, REVISE_REQUIRED, BLOCK_DRAFTING. "
    "BLOCK_DRAFTING means you judge the packet unsafe to draft from — it is routed to the packet "
    "author as urgent repair work. If the packet passes, list the top five risks the writer must "
    "still avoid.\n\n"
    "For each issue set `severity`: 'repair' means it must be fixed before the chapter can ship "
    "(a wrong roster bucket, a leak, a contradiction you found); 'warn' means the writer should "
    "watch for it; 'info' is context. Set `field` to the packet field the issue points at when one "
    "applies (else null).\n\n"
    "Also grade the packet in `score` with integer scores 0-100 per dimension: overall, "
    "canon_consistency (agrees with locked canon), reader_clarity (a reader could follow what this "
    "chapter establishes), scene_utility (each scene seed has a clear draftable job), specificity "
    "(concrete, checkable constraints — not vague vibes), non_contradiction (internally consistent), "
    "actionability (a drafting agent could execute it without guessing). Scores are advisory "
    "calibration signals for the human — they never gate anything.\n\n"
    "Reply with ONE JSON object only — no prose, no code fences — of shape:\n"
    '{"verdict": "APPROVE|APPROVE_WARN|REVISE_REQUIRED|BLOCK_DRAFTING", '
    '"residual_risks": [str], '
    '"issues": [{"kind": str, "field": str|null, "detail": str, "severity": "info|warn|repair"}], '
    '"score": {"overall": int, "canon_consistency": int, "reader_clarity": int, "scene_utility": int, '
    '"specificity": int, "non_contradiction": int, "actionability": int}}'
)

_VERDICTS = {v.value.upper(): v for v in PacketVerdict}


def build_prompt(packet: dict[str, Any]) -> str:
    """The attack payload. Derived `_`-prefixed sections (`_surface_contract`) are excluded — QA attacks
    the authoritative content; projection safety is the deterministic surface validator's job, and the
    embedded duplicate roughly doubled the prompt. Compact dump, not pretty-printed, for the same reason
    (see scene_packet.qa._compact) — on a detailed chapter the indent-2 + duplicate version was ~26k
    tokens PER ATTEMPT, which alone blew the shared propose token budget on any QA retry."""
    attackable = {k: v for k, v in packet.items() if not str(k).startswith("_")}
    return "Attack this chapter knowledge packet and return your verdict.\n\nPACKET:\n" + json.dumps(
        attackable, ensure_ascii=False, separators=(",", ":")
    )


def parse_qa(raw: str) -> dict[str, Any] | None:
    """Parse the QA response into {verdict, residual_risks, issues, score}, or None if unusable (fail
    closed). A recognizable object with an UNKNOWN verdict is treated as None — we never guess a
    verdict. Issues are normalized to the machine-readable shape (guaranteed `severity`, capped at
    `repair`, plus derived `blocks_*` facts) — an LLM issue can never carry a drafting-blocking
    severity. `score` is the tolerantly-parsed per-dimension grade (missing scores -> None, never a
    gate — the Workstream-G object is advisory)."""
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
        "issues": [normalize_llm_issue(i) for i in issues if isinstance(i, dict)] if isinstance(issues, list) else [],
        "score": parse_score(obj.get("score")),
    }


async def qa_packet(packet: dict[str, Any], *, budget: TokenBudget) -> dict[str, Any] | None:
    """One bounded call -> {verdict, residual_risks, issues}, or None on a malformed response."""

    def _semantic_escalate(value: dict[str, Any] | None) -> bool:
        if value is None or not get_runtime_policy("packet_qa_model").semantic_escalation:
            return False
        return should_semantic_escalate(score_qa_result(value))

    async def _attempt(model: str, max_tokens: int) -> tuple[dict[str, Any] | None, Any]:
        raw, usage = await llm.complete(
            model=model,
            system=_SYSTEM,
            user=build_prompt(packet),
            max_tokens=max_tokens,
            budget=budget,
            expect_cache=False,
        )
        return parse_qa(raw), usage

    result, _model, _esc = await attempt_with_escalation(
        setting_key="packet_qa_model",
        primary_model=settings.packet_qa_model,
        primary_max_tokens=_QA_MAX_TOKENS,
        attempt_fn=_attempt,
        is_success=lambda v: v is not None,
        policy=policy_for_setting("packet_qa_model"),
        semantic_escalate=_semantic_escalate,
        pick_preferred=qa_result_preferred,
    )
    return result if isinstance(result, dict) else None
