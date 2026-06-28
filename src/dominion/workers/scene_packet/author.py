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
from dominion.workers.budget import TokenBudget, Usage
from dominion.workers.scene_packet.parse import extract_object, valid_scene_packet_body


def _compact(obj: Any) -> str:
    """Dump JSON with no indentation/whitespace. The chapter packet rides on every author + QA call as
    a cached prefix; pretty-printing (indent=2) roughly doubles its token count for no model benefit —
    these are machine-read contracts, not human-read. Compact keeps the prefix (and its cache-write
    cost) about half the size."""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))

# Headroom for the fallback attempt: a genuine truncation needs more room than the first cap gave it.
_FALLBACK_MAX_TOKENS_FLOOR = 12000


class ScenePacketAuthorError(RuntimeError):
    """The author could not produce a usable ScenePacket body (truncated/unparseable/thin), even after
    escalating to the fallback model. Carries a human-readable cause so the derive can persist *why*
    the scene blocked instead of a generic 'incomplete body'."""

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


def build_prefix(
    *,
    chapter_packet_body: dict[str, Any],
    pov_summary: str | None = None,
    omniscient_summary: str | None = None,
) -> str:
    """The chapter-wide context that is IDENTICAL across every scene of the chapter, sent as a cached
    block so scenes 2..N read it instead of re-paying for it. Everything scene-specific lives in
    build_prompt below the cache breakpoint."""
    parts = ["APPROVED CHAPTER PACKET (chapter-wide authority):\n" + _compact(chapter_packet_body)]
    if pov_summary:
        parts.append(f"What this POV knows so far:\n{pov_summary}")
    if omniscient_summary:
        parts.append(f"Story so far (all viewpoints):\n{omniscient_summary}")
    return "\n\n".join(parts)


def build_scene_context(
    *,
    pov: str,
    scene_seed: dict[str, Any],
    word_budget: dict[str, Any],
    prior_scene_summaries: list[str] | None = None,
    prior_exit_state: str | None = None,
    owner_snippets: list[str] | None = None,
    canon_snippets: list[str] | None = None,
) -> str:
    """The scene-specific CONTEXT (seed, word budget, prior state, retrieved canon) — everything that is
    constant across a scene's author call(s) but varies per scene, carrying NO closing instruction. The
    sectioned author caches this whole block as a prefix and varies only the per-section directive below
    it, so all of a scene's section calls share (and the priming call writes) one identical cached body."""
    parts: list[str] = [f"POV: {pov}"]
    parts.append("THIS SCENE'S SEED:\n" + _compact(scene_seed))
    parts.append("WORD BUDGET (use verbatim):\n" + _compact(word_budget))
    if prior_exit_state:
        parts.append(f"Prior scene exit state:\n{prior_exit_state}")
    if prior_scene_summaries:
        parts.append("Prior approved scenes (summaries):\n"
                     + "\n".join(f"- {s}" for s in prior_scene_summaries))
    if owner_snippets:
        parts.append("OWNER FILES (authority over retrieved snippets):\n"
                     + "\n\n".join(owner_snippets))
    if canon_snippets:
        parts.append("RETRIEVED CANON (supporting context):\n" + "\n\n".join(canon_snippets))
    return "\n\n".join(parts)


def build_prompt(
    *,
    pov: str,
    scene_seed: dict[str, Any],
    word_budget: dict[str, Any],
    prior_scene_summaries: list[str] | None = None,
    prior_exit_state: str | None = None,
    owner_snippets: list[str] | None = None,
    canon_snippets: list[str] | None = None,
    closing: str | None = None,
) -> str:
    """The full scene-specific prompt: the shared scene context plus a trailing instruction (the default
    whole-packet schema, or a `closing` override). The chapter-wide authority/summaries ride ahead of
    this as the cached prefix (build_prefix)."""
    context = build_scene_context(
        pov=pov, scene_seed=scene_seed, word_budget=word_budget,
        prior_scene_summaries=prior_scene_summaries, prior_exit_state=prior_exit_state,
        owner_snippets=owner_snippets, canon_snippets=canon_snippets,
    )
    closing_text = closing or (
        "Produce the ScenePacket as ONE JSON object with exactly this shape:\n" + _SCHEMA_HINT
    )
    return context + "\n\n" + closing_text


def _stamp(body: dict[str, Any], word_budget: dict[str, Any]) -> dict[str, Any]:
    body["word_budget"] = word_budget  # planner is authoritative, never the model
    return body


def _why(body: Any, usage: Usage, *, model: str, max_tokens: int) -> str:
    """A specific cause for an unusable body, so a blocked scene names its real reason."""
    if usage.truncated:
        return (f"{model} response truncated at max_tokens={max_tokens} "
                f"({usage.output_tokens} output tokens) — JSON cut off mid-object")
    if not isinstance(body, dict):
        return f"{model} returned no parseable JSON object"
    return f"{model} returned a thin body (missing required contract sections)"


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
) -> dict[str, Any]:
    """Produce one usable ScenePacket body. The word budget is re-stamped server-side so the model can
    never override the planner's numbers.

    Fail loud, not thin: if the primary model returns a truncated/unparseable/thin body, retry ONCE
    escalated to the configured fallback model with extra token headroom (which fixes both a real
    truncation and a model that can't emit clean JSON for this schema). If it still can't, raise
    ScenePacketAuthorError carrying the cause — the derive persists that as the scene's blocked reason."""
    prefix = build_prefix(
        chapter_packet_body=chapter_packet_body,
        pov_summary=pov_summary, omniscient_summary=omniscient_summary,
    )
    user = build_prompt(
        pov=pov, scene_seed=scene_seed, word_budget=word_budget,
        prior_scene_summaries=prior_scene_summaries, prior_exit_state=prior_exit_state,
        owner_snippets=owner_snippets, canon_snippets=canon_snippets,
    )

    async def _attempt(model: str, max_tokens: int) -> tuple[Any, Usage]:
        raw, usage = await llm.complete(
            model=model, system=_SYSTEM, user_prefix=prefix, user=user,
            max_tokens=max_tokens, budget=budget,
        )
        return extract_object(raw), usage

    primary = settings.scene_packet_author_model
    primary_max = settings.scene_packet_author_max_tokens
    body, usage = await _attempt(primary, primary_max)
    if valid_scene_packet_body(body):
        return _stamp(body, word_budget)

    first_cause = _why(body, usage, model=primary, max_tokens=primary_max)
    fallback = (settings.scene_packet_author_fallback_model or "").strip()
    if not fallback or fallback == primary:
        raise ScenePacketAuthorError(f"{first_cause}; no fallback model configured")

    fb_max = max(primary_max, _FALLBACK_MAX_TOKENS_FLOOR)
    body2, usage2 = await _attempt(fallback, fb_max)
    if valid_scene_packet_body(body2):
        return _stamp(body2, word_budget)
    raise ScenePacketAuthorError(
        f"{first_cause}; fallback {_why(body2, usage2, model=fallback, max_tokens=fb_max)}"
    )
