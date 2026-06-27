"""ScenePacket Author agent (scene-packet contract system).

Translates the approved ChapterPacket's chapter-wide constraints into ONE scene's local boundary: what
the reader and POV know before the scene, what may be learned/inferred, what must remain hidden, the
intentional mysteries, and the reviewer false-positive traps. It does NOT draft prose, invent story,
or resolve unresolved canon. The chapter packet is authority; owner files win over retrieved snippets.

The word budget is supplied by the deterministic Length Planner (not invented by the model) and folded
into the returned body verbatim. Output is ONE JSON object; the orchestration fails closed on a
malformed/thin body.
"""
from __future__ import annotations

import json
from typing import Any

from dominion.shared.config import settings
from dominion.workers import llm
from dominion.workers.budget import TokenBudget
from dominion.workers.scene_packet.parse import extract_object

_AUTHOR_MAX_TOKENS = 6000

_SYSTEM = (
    "You are the ScenePacket Author. Do NOT write prose. Do NOT invent story. Do NOT resolve "
    "unresolved canon. Do NOT import future knowledge into the reader or POV fields.\n\n"
    "Translate the approved chapter packet's chapter-wide constraints into THIS scene's local "
    "boundary. Decide precisely what the reader knows before the scene vs. what the POV character "
    "knows; what the reader must learn, may learn, or may only infer; what must remain hidden from "
    "reader and POV; the intentional mysteries (with their desired reader effect); and the reviewer "
    "false-positive traps a reviewer might wrongly flag as missing context.\n\n"
    "The chapter packet is your authority. Owner files win over retrieved snippets. Use the supplied "
    "word_budget EXACTLY — do not change its numbers. Also list phrases the drafter should avoid "
    "echoing (contract/packet language that would read as machine prose).\n\n"
    "Reply with ONE JSON object only — no prose, no code fences."
)

_SCHEMA_HINT = (
    '{\n'
    '  "scene_no": int, "scene_job": str, "scene_type": str,\n'
    '  "chapter_position": "opening|middle|climax|aftermath|bridge",\n'
    '  "word_budget": <use the supplied word_budget object verbatim>,\n'
    '  "known_before_scene": {"reader": [str], "pov": [str], "omniscient_author": [str]},\n'
    '  "learned_during_scene": {"reader_must_learn": [str], "reader_may_learn": [str], '
    '"reader_may_infer_only": [str]},\n'
    '  "must_remain_hidden": {"reader": [str], "pov": [str], "all_surface_prose": [str]},\n'
    '  "pov_permissions": {"may_notice": [str], "may_infer": [str], "must_not_know": [str], '
    '"may_be_wrong_about": [str]},\n'
    '  "intentional_mysteries": [{"mystery": str, "desired_reader_effect": str, "do_not_explain": true}],\n'
    '  "reviewer_false_positive_traps": [str],\n'
    '  "required_beats": [str], "forbidden_beats": [str], "exit_state": str, "tone_pressure": str,\n'
    '  "phrases_to_avoid_echoing": [str],\n'
    '  "reviewer_instructions": {"continuity": [str], "pacing": [str], "dialogue": [str], '
    '"combat": [str], "sensory": [str], "voice": [str]}\n'
    '}'
)


def build_prompt(
    *,
    pov: str,
    chapter_packet_body: dict[str, Any],
    scene_seed: dict[str, Any],
    word_budget: dict[str, Any],
    prior_scene_summaries: list[str] | None = None,
    prior_exit_state: str | None = None,
    pov_summary: str | None = None,
    omniscient_summary: str | None = None,
    owner_snippets: list[str] | None = None,
    canon_snippets: list[str] | None = None,
) -> str:
    parts: list[str] = [f"POV: {pov}"]
    parts.append("APPROVED CHAPTER PACKET (chapter-wide authority):\n"
                 + json.dumps(chapter_packet_body, ensure_ascii=False, indent=2))
    parts.append("THIS SCENE'S SEED:\n" + json.dumps(scene_seed, ensure_ascii=False, indent=2))
    parts.append("WORD BUDGET (use verbatim):\n" + json.dumps(word_budget, ensure_ascii=False, indent=2))
    if prior_exit_state:
        parts.append(f"Prior scene exit state:\n{prior_exit_state}")
    if prior_scene_summaries:
        parts.append("Prior approved scenes (summaries):\n"
                     + "\n".join(f"- {s}" for s in prior_scene_summaries))
    if pov_summary:
        parts.append(f"What this POV knows so far:\n{pov_summary}")
    if omniscient_summary:
        parts.append(f"Story so far (all viewpoints):\n{omniscient_summary}")
    if owner_snippets:
        parts.append("OWNER FILES (authority over retrieved snippets):\n"
                     + "\n\n".join(owner_snippets))
    if canon_snippets:
        parts.append("RETRIEVED CANON (supporting context):\n" + "\n\n".join(canon_snippets))
    parts.append("Produce the ScenePacket as ONE JSON object with exactly this shape:\n" + _SCHEMA_HINT)
    return "\n\n".join(parts)


async def author_scene_packet(
    *,
    pov: str,
    chapter_packet_body: dict[str, Any],
    scene_seed: dict[str, Any],
    word_budget: dict[str, Any],
    prior_scene_summaries: list[str] | None = None,
    prior_exit_state: str | None = None,
    pov_summary: str | None = None,
    omniscient_summary: str | None = None,
    owner_snippets: list[str] | None = None,
    canon_snippets: list[str] | None = None,
    budget: TokenBudget,
) -> dict[str, Any] | None:
    """One bounded call -> a ScenePacket body dict, or None when nothing usable came back. The word
    budget is re-stamped server-side so the model can never override the planner's numbers."""
    raw, _usage = await llm.complete(
        model=settings.scene_packet_author_model,
        system=_SYSTEM,
        user=build_prompt(
            pov=pov, chapter_packet_body=chapter_packet_body, scene_seed=scene_seed,
            word_budget=word_budget, prior_scene_summaries=prior_scene_summaries,
            prior_exit_state=prior_exit_state, pov_summary=pov_summary,
            omniscient_summary=omniscient_summary, owner_snippets=owner_snippets,
            canon_snippets=canon_snippets,
        ),
        max_tokens=_AUTHOR_MAX_TOKENS,
        budget=budget,
        expect_cache=False,
    )
    body = extract_object(raw)
    if isinstance(body, dict):
        body["word_budget"] = word_budget  # planner is authoritative, never the model
    return body
