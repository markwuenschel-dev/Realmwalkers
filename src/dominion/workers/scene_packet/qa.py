"""ScenePacket QA agent (scene-packet contract system).

A separate agent that ATTACKS each derived ScenePacket — the author must not validate its own
contract. It checks for the failure modes that make a scene-local contract unsafe (future-knowledge
leak, reader/POV knowledge collapse, author-only canon placed in a reader-known field, missing
required reveal, premature reveal permission, contradictions with the chapter packet / relationship
invariants / cast, implausible word budget, missing false-positive traps or phrase-avoidance). A
malformed QA response blocks packet approval (fail closed).
"""
from __future__ import annotations

import json
from typing import Any

from dominion.shared.config import settings
from dominion.workers import llm
from dominion.workers.budget import TokenBudget
from dominion.workers.scene_packet.parse import parse_scene_qa

_QA_MAX_TOKENS = 2500

_SYSTEM = (
    "You are the ScenePacket QA agent. Do NOT improve prose. Do NOT draft. Attack the scene packet.\n\n"
    "Check for: future-knowledge leak into reader/POV fields; collapse of the distinction between what "
    "the reader knows and what the POV knows; author-only canon placed in a reader-known field; a "
    "missing required reveal; a premature reveal permission; contradiction with the chapter packet; "
    "contradiction with relationship invariants or cast/roster constraints; an implausible word "
    "budget; missing reviewer false-positive traps; and missing phrases_to_avoid_echoing for abstract "
    "packet language.\n\n"
    "Return exactly one verdict: APPROVE, APPROVE_WARN, REVISE_REQUIRED, BLOCK_DRAFTING. "
    "BLOCK_DRAFTING means no prose may be written from this scene packet.\n\n"
    "Reply with ONE JSON object only — no prose, no code fences — of shape:\n"
    '{"verdict": "APPROVE|APPROVE_WARN|REVISE_REQUIRED|BLOCK_DRAFTING", '
    '"residual_risks": [str], '
    '"issues": [{"kind": str, "detail": str, "severity": "info|warn|block"}]}'
)


def build_prompt(scene_packet: dict[str, Any], *, chapter_packet_body: dict[str, Any] | None) -> str:
    parts = ["Attack this scene packet and return your verdict.\n"]
    if chapter_packet_body:
        parts.append("CHAPTER PACKET (the macro authority it must not contradict):\n"
                     + json.dumps(chapter_packet_body, ensure_ascii=False, indent=2))
    parts.append("SCENE PACKET:\n" + json.dumps(scene_packet, ensure_ascii=False, indent=2))
    return "\n\n".join(parts)


async def qa_scene_packet(
    scene_packet: dict[str, Any],
    *,
    chapter_packet_body: dict[str, Any] | None = None,
    budget: TokenBudget,
) -> dict[str, Any] | None:
    """One bounded call -> {verdict, residual_risks, issues}, or None on a malformed response."""
    raw, _usage = await llm.complete(
        model=settings.scene_packet_qa_model,
        system=_SYSTEM,
        user=build_prompt(scene_packet, chapter_packet_body=chapter_packet_body),
        max_tokens=_QA_MAX_TOKENS,
        budget=budget,
        expect_cache=False,
    )
    return parse_scene_qa(raw)
