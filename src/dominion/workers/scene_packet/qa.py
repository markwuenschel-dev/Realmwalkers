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
from dominion.workers.llm import CachedPrefixBlock, estimate_tokens
from dominion.workers.scene_packet.parse import parse_scene_qa

# Headroom for the fallback attempt when the first QA pass is cut off mid-verdict.
_QA_FALLBACK_MAX_TOKENS_FLOOR = 5000

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


def _compact(obj: Any) -> str:
    """Compact JSON dump — see scene_packet.author._compact. The chapter packet rides on every QA call
    as a cached prefix; pretty-printing it just doubles the prefix (and its cache-write cost)."""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def build_prefix(chapter_packet_body: dict[str, Any] | None) -> str | None:
    """The chapter packet is identical across every scene's QA, so it rides ahead as a cached block."""
    if not chapter_packet_body:
        return None
    return ("CHAPTER PACKET (the macro authority it must not contradict):\n"
            + _compact(chapter_packet_body))


def build_prompt(scene_packet: dict[str, Any]) -> str:
    return ("Attack this scene packet and return your verdict.\n\n"
            "SCENE PACKET:\n" + _compact(scene_packet))



def build_prefix_blocks(chapter_packet_body: dict[str, Any] | None) -> tuple[CachedPrefixBlock, ...]:
    prefix = build_prefix(chapter_packet_body)
    return (CachedPrefixBlock(name="chapter_shared_prefix", text=prefix),) if prefix else ()


def context_sections_for_qa_call(
    *, prefix_blocks: tuple[CachedPrefixBlock, ...], user: str
) -> dict[str, int]:
    sections = {"system": estimate_tokens(_SYSTEM)}
    sections.update({block.name: estimate_tokens(block.text) for block in prefix_blocks})
    sections["qa_prompt"] = estimate_tokens(user)
    return sections


async def prime_qa_shared_prefix(
    chapter_packet_body: dict[str, Any] | None, *, budget: TokenBudget
) -> None:
    """Prime the QA chapter-shared prefix outside any scene-local work budget."""
    prefix_blocks = build_prefix_blocks(chapter_packet_body)
    if not prefix_blocks:
        return
    user = "Acknowledge cache prime."
    await llm.complete(
        model=settings.scene_packet_qa_model, system=_SYSTEM, user_prefix_blocks=prefix_blocks,
        user=user, max_tokens=16, budget=budget, expect_cache=True,
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
    budget: TokenBudget,
) -> dict[str, Any] | None:
    """One bounded call -> {verdict, residual_risks, issues}, or None on a malformed response (the
    caller fails closed on None). If the first pass is truncated or unparseable, retry ONCE escalated
    to the fallback model with extra headroom before giving up — a cut-off verdict is recoverable."""
    prefix_blocks = build_prefix_blocks(chapter_packet_body)
    user = build_prompt(scene_packet)

    async def _attempt(model: str, max_tokens: int) -> tuple[dict[str, Any] | None, bool]:
        raw, usage = await llm.complete(
            model=model, system=_SYSTEM, user_prefix_blocks=prefix_blocks, user=user,
            max_tokens=max_tokens, budget=budget, expect_cache=bool(prefix_blocks),
            context_window_budget=settings.scene_packet_context_window_budget,
            context_sections=context_sections_for_qa_call(prefix_blocks=prefix_blocks, user=user),
        )
        return parse_scene_qa(raw), usage.truncated

    primary = settings.scene_packet_qa_model
    result, truncated = await _attempt(primary, settings.scene_packet_qa_max_tokens)
    if result is not None:
        return result

    fallback = (settings.scene_packet_qa_fallback_model or "").strip()
    if not fallback or fallback == primary:
        return None
    fb_max = max(settings.scene_packet_qa_max_tokens, _QA_FALLBACK_MAX_TOKENS_FLOOR) if truncated \
        else settings.scene_packet_qa_max_tokens
    result, _ = await _attempt(fallback, fb_max)
    return result
