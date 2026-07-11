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
from dominion.workers.llm_escalation import attempt_with_escalation, policy_for_setting
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
    "Roster: every named or referenced character belongs in EXACTLY ONE of these four buckets — never "
    "two, and never omit a character who has ANY role in the chapter:\n"
    "  - characters_present: physically in a scene this chapter, in ANY capacity — named or anonymous, "
    "lead or background, even a single line of dialogue or a one-beat guild-chat callout. If a character "
    "speaks, acts, or is directly perceived on-page at all this chapter, they are PRESENT, never absent, "
    "no matter how minor. An anonymous/unidentified presence (e.g. a masked antagonist not yet recognized) "
    "is still PRESENT — annotate the anonymity/reveal-timing in the entry itself.\n"
    "  - characters_mentioned_only: talked ABOUT by other characters or referenced in narration, but never "
    "physically on-page themselves this chapter (e.g. 'has anyone heard from X'). Do NOT put a physically "
    "present character here just because their identity is hidden or withheld at first — a masked or "
    "not-yet-recognized character who acts on-page is PRESENT, not mentioned-only. Never list the same "
    "character in both characters_present and characters_mentioned_only.\n"
    "  - characters_absent: has NO role this chapter at all — not present, not mentioned, not referenced "
    "in any form. Use this ONLY when the character contributes nothing to this chapter whatsoever. If the "
    "outline gives them even minor scene function (a line, a callout, a background action), that is "
    "characters_present, NOT characters_absent — do not default a minor/background character to absent "
    "just because they are not central to the chapter.\n"
    "  - characters_forbidden: must not be named or referenced in any form for narrative reasons (not yet "
    "introduced, spoiler, cosmological stakes not yet on the table).\n"
    "When uncertain whether a background character has any on-page role, prefer characters_present (or "
    "characters_mentioned_only if they are only discussed) over characters_absent — the wrong call in "
    "that direction produces a contradiction the scene-level author will hit later, blocking drafting.\n"
    "If a character is physically present but their name must be temporarily withheld from the reader, put "
    "them in characters_present (a surface-safe label is fine, e.g. 'suited Astria figure'), put the "
    "name/identity in characters_forbidden and the reveal-timing in the reader/POV-knowledge fields — do "
    "NOT express name-withholding by demoting them to characters_mentioned_only or by naming them on-page. "
    "NEVER put such a character in characters_absent with a note like 'named form absent; surface form "
    "present' — roster presence is about entity PARTICIPATION, not whether the true name is spoken; a "
    "withheld name is never a roster absence.\n"
    "Resolve conditional presence — never hedge a roster state. Entries like 'may be present' or 'possibly "
    "appears' are forbidden in every roster bucket AND in roster_locks: decide. If the character appears at "
    "all (even brief comms/voice/remote contact), that is characters_present; if they are only referenced, "
    "characters_mentioned_only; if they truly do not appear and are not referenced, characters_absent with "
    "no hedging — and no 'may be present' roster_lock.\n\n"
    "INTERNAL vs SURFACE CONTRACT:\n"
    "You are creating an INTERNAL author packet (AuthorPacketInternal). Internal planning, claims, "
    "canon_locks, roster buckets, and raw scene seeds may contain hidden canonical truth (names, "
    "identities, future reveals, author-only facts). These are NEVER shown to drafters or readers.\n\n"
    "A SurfaceContract is derived deterministically from your packet. Only the SurfaceContract (with "
    "projected scene seeds) is handed to ScenePacket derivation and drafting agents.\n\n"
    "For any term the system must know internally but the drafter/prose must not surface yet "
    "(characters, factions, places, artifacts, powers, relationships, deaths, cosmology terms, "
    "future reveals...), you MUST populate the generic surface_terms array. Do not rely on raw "
    "internal wording reaching a drafter.\n\n"
    "surface_terms policy entries let you declare safe replacements:\n"
    "  - canonical_term: internal truth\n"
    "  - forbidden_surface_terms: exact terms drafter must never see\n"
    "  - surface_label: the safe wording to use in DRAFTER_SURFACE fields (scene seeds etc)\n"
    '  - policy: "replace" | "omit" | "block"\n'
    "  - reason, until: optional\n\n"
    "When you list something in characters_forbidden (or other forbidden_*), also supply a "
    "surface_terms entry with a replace policy + surface_label whenever a safe label exists. "
    "Otherwise the SurfaceContractBuilder will block the packet.\n\n"
    "Raw scene seeds inside your packet are INTERNAL PLANNING. After projection they become "
    "DRAFTER_SURFACE and must be safe. Never write a forbidden canonical name directly into what "
    "will become a drafter-facing scene_job / required_beats / exit_state.\n"
    "Correct pattern: put the hidden name in characters_forbidden + surface_terms; use the "
    "surface_label in any drafter-facing text you emit.\n\n"
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
    "{\n"
    '  "chapter_job": str, "one_sentence_spine": str,\n'
    '  "entry_state": str, "exit_state": str, "emotional_spine": str,\n'
    '  "characters_present": [str], "characters_absent": [str],\n'
    '  "characters_mentioned_only": [str], "characters_forbidden": [str],\n'
    '  "surface_terms": [\n'
    '    {"canonical_term": str, "forbidden_surface_terms": [str], "surface_label": str|null,\n'
    '     "allowed_surface_terms": [str], "policy": "replace|omit|block", "until": str|null, "reason": str}\n'
    "  ],\n"
    "  # legacy (still accepted during transition)\n"
    '  "entity_bindings": [{"canonical_name": str, "surface_label": str, '
    '"forbidden_surface_terms": [str]}],\n'
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
    "}"
)


def build_prompt(
    *,
    chapter_no: int | None,
    pov: str,
    outline: str,
    omniscient_summary: str | None,
    prior_exit_state: str | None,
    next_entry_intent: str | None,
    canon_handles: dict[str, dict[str, Any]],
) -> str:
    """Assemble the author's user message. `canon_handles` maps a short handle (C1, C2, …) to its
    canon row meta so the model can cite provenance by handle."""
    # chapter_no is display-only and absent for a numberless kind (prologue/…); fall back to "CHAPTER".
    heading = f"CHAPTER {chapter_no}" if chapter_no is not None else "CHAPTER"
    parts: list[str] = [f"{heading} — POV: {pov}"]
    if prior_exit_state:
        parts.append(f"Previous chapter ended (entry state for this chapter):\n{prior_exit_state}")
    if next_entry_intent:
        parts.append(f"Next chapter is intended to open from:\n{next_entry_intent}")
    if omniscient_summary:
        parts.append(f"Story so far (all viewpoints):\n{omniscient_summary}")
    if canon_handles:
        snippets = "\n\n".join(
            f"[{h}] ({meta.get('name') or 'canon'}) {meta.get('body') or ''}" for h, meta in canon_handles.items()
        )
        parts.append("CANON SNIPPETS (cite a claim's source_id by its bracket handle, e.g. C1):\n" + snippets)
    parts.append("CHAPTER OUTLINE:\n" + outline)
    parts.append("Produce the chapter knowledge packet as ONE JSON object with exactly this shape:\n" + _SCHEMA_HINT)
    return "\n\n".join(parts)


async def author_packet(
    *,
    chapter_no: int | None,
    pov: str,
    outline: str,
    omniscient_summary: str | None,
    prior_exit_state: str | None,
    next_entry_intent: str | None,
    canon_handles: dict[str, dict[str, Any]],
    budget: TokenBudget,
) -> dict[str, Any] | None:
    """One bounded call -> the parsed packet dict, or None when nothing usable came back."""
    user = build_prompt(
        chapter_no=chapter_no,
        pov=pov,
        outline=outline,
        omniscient_summary=omniscient_summary,
        prior_exit_state=prior_exit_state,
        next_entry_intent=next_entry_intent,
        canon_handles=canon_handles,
    )

    async def _attempt(model: str, max_tokens: int) -> tuple[dict[str, Any] | None, Any]:
        raw, usage = await llm.complete(
            model=model,
            system=_SYSTEM,
            user=user,
            max_tokens=max_tokens,
            budget=budget,
            expect_cache=False,
            setting_key="packet_author_model",
        )
        obj = extract_object(raw)
        return obj if isinstance(obj, dict) else None, usage

    result, _model, _esc = await attempt_with_escalation(
        setting_key="packet_author_model",
        primary_model=settings.packet_author_model,
        primary_max_tokens=_AUTHOR_MAX_TOKENS,
        attempt_fn=_attempt,
        is_success=lambda v: isinstance(v, dict) and bool(v.get("chapter_job")),
        policy=policy_for_setting("packet_author_model"),
    )
    return result if isinstance(result, dict) else None
