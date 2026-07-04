"""ScenePacket QA agent (scene-packet contract system).

A separate agent that ATTACKS each derived ScenePacket — the author must not validate its own
contract. It checks for the failure modes that make a scene-local contract unsafe: a hidden/author-only
fact that has ALSO leaked into a reader/POV-known or on-page field (NOT the mere presence of a fact in
known_before_scene.omniscient_author or must_remain_hidden — that is correct layering, not a leak),
missing required reveal, premature reveal permission, contradictions with the chapter packet /
relationship invariants / cast, implausible word budget, missing false-positive traps or
phrase-avoidance. A RESOLVED AUTHOR RULING (when the chapter packet's `open_questions` carries one) is
settled fact QA must not re-litigate or misread as still-unresolved. A malformed QA response blocks
packet approval (fail closed).
"""

from __future__ import annotations

import json
from typing import Any

from dominion.shared.agent_policy import get_runtime_policy
from dominion.shared.config import settings
from dominion.shared.risk_scorer import qa_result_preferred, score_qa_result, should_semantic_escalate
from dominion.workers import llm
from dominion.workers.budget import TokenBudget
from dominion.workers.llm import CachedPrefixBlock, estimate_tokens
from dominion.workers.llm_escalation import attempt_with_escalation, policy_for_setting
from dominion.workers.scene_packet.author import format_chapter_rulings
from dominion.workers.scene_packet.parse import parse_scene_qa

# Headroom for the fallback attempt when the first QA pass is cut off mid-verdict.
_QA_FALLBACK_MAX_TOKENS_FLOOR = 5000

_SYSTEM = (
    "You are the ScenePacket QA agent. Do NOT improve prose. Do NOT draft. Attack the scene packet.\n\n"
    "Check for: a hidden/author-only fact that has ALSO leaked into a reader-known or POV-known field "
    "(known_before_scene.reader, known_before_scene.pov, learned_during_scene.*, pov_permissions.may_notice, "
    "pov_permissions.may_infer) or into an on-page field (required_beats, exit_state) before its reveal "
    "timing; a missing required reveal; a premature reveal permission; contradiction with the chapter "
    "packet; contradiction with relationship invariants or cast/roster constraints; an implausible word "
    "budget; missing reviewer false-positive traps; and missing phrases_to_avoid_echoing for abstract "
    "packet language.\n\n"
    "known_before_scene.omniscient_author and must_remain_hidden.* are AUTHOR-ONLY fields BY DESIGN — a "
    "hidden truth belongs there. Naming a fact ONLY in those fields (never in reader/pov/on-page fields) is "
    "CORRECT, not a defect — do NOT flag it as 'collapse of reader-known vs POV-known' or as leaking "
    "author-only canon into a reader-known field. Only flag when the SAME fact is found in BOTH an "
    "author-only field AND a reader/POV/on-page field, or when must_remain_hidden fails to list a fact that "
    "known_before_scene.omniscient_author or a RESOLVED AUTHOR RULING says must stay hidden.\n\n"
    "If a RESOLVED AUTHOR RULING is supplied (in the chapter-packet prefix), it is settled fact — do NOT "
    "flag it as an unresolved open question, do NOT contradict it, and do NOT treat the packet as missing "
    "something just because it stays consistent with the ruling instead of re-deciding it. If an UNRESOLVED "
    "OPEN QUESTION is supplied, a packet that stays silent on it (does not invent an answer) is CORRECT — "
    "do not block or flag a packet merely for not resolving something genuinely still open.\n\n"
    "Return exactly one verdict: APPROVE, APPROVE_WARN, REVISE_REQUIRED, BLOCK_DRAFTING. "
    "BLOCK_DRAFTING means you judge the packet unsafe to draft from — it is routed to the packet "
    "author as urgent repair work.\n\n"
    "For each issue, set `field` to the dotted path of the offending scene-packet field when one applies "
    '(e.g. "known_before_scene.reader", "learned_during_scene.reader_must_learn", "must_remain_hidden.pov", '
    '"required_beats", "exit_state"), or null for a whole-packet problem. The exact key names matter — '
    "they let the editor point the human straight at the field to fix instead of making them hunt. "
    "Set `severity`: 'repair' means it must be fixed before the chapter can ship (a leak, a wrong "
    "bucket, a contradiction you found); 'warn' means the writer should watch for it; 'info' is "
    "context.\n\n"
    "Also grade the packet in `score` with integer scores 0-100 per dimension: overall, "
    "canon_consistency (agrees with the chapter packet and locked canon), reader_clarity (the "
    "reader/POV knowledge layering is coherent), scene_utility (the scene has a clear draftable job), "
    "specificity (concrete, checkable constraints — not vague vibes), non_contradiction (internally "
    "consistent), actionability (a drafting agent could execute it without guessing). Scores are "
    "advisory calibration signals for the human — they never gate anything.\n\n"
    "Reply with ONE JSON object only — no prose, no code fences — of shape:\n"
    '{"verdict": "APPROVE|APPROVE_WARN|REVISE_REQUIRED|BLOCK_DRAFTING", '
    '"residual_risks": [str], '
    '"issues": [{"kind": str, "field": str|null, "detail": str, "severity": "info|warn|repair"}], '
    '"score": {"overall": int, "canon_consistency": int, "reader_clarity": int, "scene_utility": int, '
    '"specificity": int, "non_contradiction": int, "actionability": int}}'
)


def _compact(obj: Any) -> str:
    """Compact JSON dump — see scene_packet.author._compact. The chapter packet rides on every QA call
    as a cached prefix; pretty-printing it just doubles the prefix (and its cache-write cost)."""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def build_prefix(
    chapter_packet_body: dict[str, Any] | None,
    *,
    chapter_open_questions: dict[str, Any] | None = None,
) -> str | None:
    """The chapter packet is identical across every scene's QA, so it rides ahead as a cached block.

    Derived `_`-prefixed sections (`_surface_contract`) and the embedded `qa` audit blob are excluded —
    same rule as packet.qa.build_prompt: QA judges against the authoritative content, and on a mature
    chapter the derived duplicate + audit trail more than doubled the prefix (observed: a 167KB body
    whose prefix alone hit ~35k tokens and blew the QA input budget outright)."""
    if not chapter_packet_body:
        return None
    authoritative = {k: v for k, v in chapter_packet_body.items() if not str(k).startswith("_") and k != "qa"}
    parts = ["CHAPTER PACKET (the macro authority it must not contradict):\n" + _compact(authoritative)]
    rulings = format_chapter_rulings(chapter_open_questions)
    if rulings:
        parts.append(rulings)
    return "\n\n".join(parts)


def build_prompt(scene_packet: dict[str, Any]) -> str:
    return "Attack this scene packet and return your verdict.\n\nSCENE PACKET:\n" + _compact(scene_packet)


def build_prefix_blocks(
    chapter_packet_body: dict[str, Any] | None,
    *,
    chapter_open_questions: dict[str, Any] | None = None,
) -> tuple[CachedPrefixBlock, ...]:
    prefix = build_prefix(chapter_packet_body, chapter_open_questions=chapter_open_questions)
    return (CachedPrefixBlock(name="chapter_shared_prefix", text=prefix),) if prefix else ()


def context_sections_for_qa_call(*, prefix_blocks: tuple[CachedPrefixBlock, ...], user: str) -> dict[str, int]:
    sections = {"system": estimate_tokens(_SYSTEM)}
    sections.update({block.name: estimate_tokens(block.text) for block in prefix_blocks})
    sections["qa_prompt"] = estimate_tokens(user)
    return sections


async def prime_qa_shared_prefix(
    chapter_packet_body: dict[str, Any] | None,
    *,
    chapter_open_questions: dict[str, Any] | None = None,
    budget: TokenBudget,
) -> None:
    """Prime the QA chapter-shared prefix outside any scene-local work budget."""
    prefix_blocks = build_prefix_blocks(chapter_packet_body, chapter_open_questions=chapter_open_questions)
    if not prefix_blocks:
        return
    user = "Acknowledge cache prime."
    await llm.complete(
        model=settings.scene_packet_qa_model,
        system=_SYSTEM,
        user_prefix_blocks=prefix_blocks,
        user=user,
        max_tokens=16,
        budget=budget,
        expect_cache=True,
        context_window_budget=settings.scene_packet_context_window_budget,
        context_sections={
            "system": estimate_tokens(_SYSTEM),
            "chapter_shared_prefix": estimate_tokens(prefix_blocks[0].text),
            "prime_suffix": estimate_tokens(user),
        },
    )


async def qa_scene_packet(
    scene_packet: dict[str, Any],
    *,
    chapter_packet_body: dict[str, Any] | None = None,
    chapter_open_questions: dict[str, Any] | None = None,
    budget: TokenBudget,
) -> dict[str, Any] | None:
    """One bounded call -> {verdict, residual_risks, issues}, or None on a malformed response (the
    caller fails closed on None). If the first pass is truncated or unparseable, retry ONCE escalated
    to the fallback model with extra headroom before giving up — a cut-off verdict is recoverable."""
    prefix_blocks = build_prefix_blocks(chapter_packet_body, chapter_open_questions=chapter_open_questions)
    user = build_prompt(scene_packet)

    def _semantic_escalate(value: dict[str, Any] | None) -> bool:
        if value is None or not get_runtime_policy("scene_packet_qa_model").semantic_escalation:
            return False
        return should_semantic_escalate(score_qa_result(value))

    async def _attempt(model: str, max_tokens: int) -> tuple[dict[str, Any] | None, Any]:
        raw, usage = await llm.complete(
            model=model,
            system=_SYSTEM,
            user_prefix_blocks=prefix_blocks,
            user=user,
            max_tokens=max_tokens,
            budget=budget,
            expect_cache=bool(prefix_blocks),
            context_window_budget=settings.scene_packet_context_window_budget,
            context_sections=context_sections_for_qa_call(prefix_blocks=prefix_blocks, user=user),
            input_budget=settings.scene_packet_qa_prompt_budget,
        )
        return parse_scene_qa(raw), usage

    policy = policy_for_setting("scene_packet_qa_model")
    result, _model, _esc = await attempt_with_escalation(
        setting_key="scene_packet_qa_model",
        primary_model=settings.scene_packet_qa_model,
        primary_max_tokens=settings.scene_packet_qa_max_tokens,
        attempt_fn=_attempt,
        is_success=lambda v: v is not None,
        policy=policy,
        fallback_max_tokens=max(settings.scene_packet_qa_max_tokens, _QA_FALLBACK_MAX_TOKENS_FLOOR),
        semantic_escalate=_semantic_escalate,
        pick_preferred=qa_result_preferred,
    )
    return result if isinstance(result, dict) else None
