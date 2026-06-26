"""Packet Author agent (contract-first drafting, Phase 1).

Creates the chapter knowledge packet that constrains every later drafting agent — allowed/forbidden
knowledge & reveals, roster/canon/timeline locks, the emotional spine, chapter entry/exit state,
per-scene seeds, and known drift risks. It does NOT write prose. Unlike the writer, the author IS
allowed broad canon: scoping protects the writer, not the planner.

Every claim must carry a source-strength label AND a provenance handle into the canon snippets it was
given (or OUTLINE / null for inference), so "LOCKED CANON" is traceable, not just asserted. Output is
ONE JSON object; the orchestration (packet/__init__.py) fails closed if it can't be parsed/validated.
"""
from __future__ import annotations

from typing import Any

from dominion.shared.config import settings
from dominion.workers import llm
from dominion.workers.budget import TokenBudget
from dominion.workers.packet.parse import extract_object

# The packet schema is large (20+ array fields, nested scene_seeds + per-claim provenance), so a
# verbose chapter can run long. Headroom here prevents truncation mid-JSON, which would parse to None
# and fail the packet closed for a non-content reason. Truncation is logged (llm.truncated) if hit.
_AUTHOR_MAX_TOKENS = 16000

_SYSTEM = (
    "You are the Chapter Packet Author. Do NOT write prose.\n\n"
    "Your job is to create the chapter knowledge packet that will constrain all drafting agents. Use "
    "only locked canon, the supplied outline, prior-chapter state, and explicitly marked inferences.\n\n"
    "Separate every claim by source strength using exactly one of these labels: LOCKED_CANON, "
    "DERIVED_FROM_OUTLINE, PLAUSIBLE_INFERENCE, UNRESOLVED, FORBIDDEN. Flag any uncertainty as an open "
    "question or UNRESOLVED claim instead of resolving it creatively.\n\n"
    "Your packet must define: allowed vs forbidden reader knowledge, required vs forbidden reveals, "
    "roster constraints (present/absent/mentioned-only/forbidden), canon/roster/relationship/timeline "
    "locks, the emotional spine, chapter entry and exit state, per-scene seeds (job, required and "
    "forbidden beats, exit state, scene type, word budget), and known drift risks (risk, why it is "
    "dangerous, how to prevent it).\n\n"
    "A good packet prevents beautiful wrong scenes. A bad packet lets the writer discover the story "
    "while drafting.\n\n"
    "Reply with ONE JSON object only — no prose, no code fences."
)

# The exact JSON shape we ask for. Provenance: each claim cites a canon handle (e.g. \"C3\"), or
# \"OUTLINE\", or null for inference. The server resolves handles back to real canon ids + titles.
_SCHEMA_HINT = (
    '{\n'
    '  "chapter_job": str, "one_sentence_spine": str,\n'
    '  "entry_state": str, "exit_state": str, "emotional_spine": str,\n'
    '  "characters_present": [str], "characters_absent": [str],\n'
    '  "characters_mentioned_only": [str], "characters_forbidden": [str],\n'
    '  "allowed_knowledge": [str], "forbidden_knowledge": [str],\n'
    '  "required_reveals": [str], "forbidden_reveals": [str],\n'
    '  "canon_locks": [str], "roster_locks": [str], "relationship_locks": [str], "timeline_locks": [str],\n'
    '  "allowed_ui_concepts": [str], "forbidden_ui_concepts": [str],\n'
    '  "required_unanswered_questions": [str],\n'
    '  "scene_seeds": [{"scene_no": int, "scene_job": str, "required_beats": [str],\n'
    '     "forbidden_beats": [str], "exit_state": str, "scene_type": str,\n'
    '     "word_budget": {"min": int, "target": int, "max": int, "hard_max": int}}],\n'
    '  "known_risks": [{"risk": str, "why_dangerous": str, "prevention": str}],\n'
    '  "claims": [{"claim": str, "source_strength": '
    '"LOCKED_CANON|DERIVED_FROM_OUTLINE|PLAUSIBLE_INFERENCE|UNRESOLVED|FORBIDDEN",\n'
    '     "source_id": str|null (a canon handle like "C3", or "OUTLINE", or null), '
    '"confidence": "high|medium|low"}],\n'
    '  "open_questions": [str],\n'
    '  "confidence": "green|yellow|red"\n'
    '}'
)


def build_prompt(
    *,
    chapter_no: int,
    pov: str,
    outline: str,
    omniscient_summary: str | None,
    prior_exit_state: str | None,
    next_entry_intent: str | None,
    canon_handles: dict[str, dict[str, Any]],
) -> str:
    """Assemble the author's user message. `canon_handles` maps a short handle (C1, C2, …) to its
    canon row meta so the model can cite provenance by handle."""
    parts: list[str] = [f"CHAPTER {chapter_no} — POV: {pov}"]
    if prior_exit_state:
        parts.append(f"Previous chapter ended (entry state for this chapter):\n{prior_exit_state}")
    if next_entry_intent:
        parts.append(f"Next chapter is intended to open from:\n{next_entry_intent}")
    if omniscient_summary:
        parts.append(f"Story so far (all viewpoints):\n{omniscient_summary}")
    if canon_handles:
        snippets = "\n\n".join(
            f"[{h}] ({meta.get('name') or 'canon'}) {meta.get('body') or ''}"
            for h, meta in canon_handles.items()
        )
        parts.append(
            "CANON SNIPPETS (cite a claim's source_id by its bracket handle, e.g. C1):\n" + snippets
        )
    parts.append("CHAPTER OUTLINE:\n" + outline)
    parts.append(
        "Produce the chapter knowledge packet as ONE JSON object with exactly this shape:\n"
        + _SCHEMA_HINT
    )
    return "\n\n".join(parts)


async def author_packet(
    *,
    chapter_no: int,
    pov: str,
    outline: str,
    omniscient_summary: str | None,
    prior_exit_state: str | None,
    next_entry_intent: str | None,
    canon_handles: dict[str, dict[str, Any]],
    budget: TokenBudget,
) -> dict[str, Any] | None:
    """One bounded call -> the parsed packet dict, or None when nothing usable came back (the
    orchestration then fails closed to a blocked packet). Never raises on a malformed response."""
    raw, _usage = await llm.complete(
        model=settings.packet_author_model,
        system=_SYSTEM,
        user=build_prompt(
            chapter_no=chapter_no, pov=pov, outline=outline,
            omniscient_summary=omniscient_summary, prior_exit_state=prior_exit_state,
            next_entry_intent=next_entry_intent, canon_handles=canon_handles,
        ),
        max_tokens=_AUTHOR_MAX_TOKENS,
        budget=budget,
        expect_cache=False,
    )
    return extract_object(raw)
