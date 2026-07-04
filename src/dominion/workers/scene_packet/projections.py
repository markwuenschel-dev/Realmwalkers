"""Project an approved ScenePacket body into the consumer-facing contract shapes.

Projection field names mirror the author schema in author_sections.py. Context assembly calls
project() after loading the packet from Postgres; consumers read the results via SceneContext.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from dominion.workers.packet.parse import str_list

# Chapter-level locks that still bind every scene; lifted into the flat drafter contract.
_CHAPTER_LOCK_KEYS: tuple[str, ...] = (
    "canon_locks",
    "roster_locks",
    "relationship_locks",
    "timeline_locks",
    "allowed_ui_concepts",
    "forbidden_ui_concepts",
)


@dataclass(frozen=True)
class ScenePacketProjections:
    scene_body: dict[str, Any]
    chapter_body: dict[str, Any]
    word_budget: dict[str, Any] | None
    reader_state: dict[str, Any]
    reviewer: dict[str, Any]
    drafter_flat: dict[str, Any]


def project(scene_body: dict[str, Any], chapter_body: dict[str, Any]) -> ScenePacketProjections:
    """Slice one ScenePacket body (+ chapter locks) into the shapes drafting and review consume."""
    word_budget = scene_body.get("word_budget") if isinstance(scene_body.get("word_budget"), dict) else None
    return ScenePacketProjections(
        scene_body=scene_body,
        chapter_body=chapter_body,
        word_budget=word_budget,
        reader_state=_reader_state(scene_body),
        reviewer=_reviewer(scene_body, word_budget),
        drafter_flat=_flat_drafter_contract(scene_body, chapter_body),
    )


def _reader_state(body: dict[str, Any]) -> dict[str, Any]:
    return {
        "known_before_scene": body.get("known_before_scene") or {},
        "learned_during_scene": body.get("learned_during_scene") or {},
        "must_remain_hidden": body.get("must_remain_hidden") or {},
        "pov_permissions": body.get("pov_permissions") or {},
        "intentional_mysteries": body.get("intentional_mysteries") or [],
        "reviewer_false_positive_traps": body.get("reviewer_false_positive_traps") or [],
    }


def _reviewer(body: dict[str, Any], word_budget: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "scene_job": body.get("scene_job"),
        "scene_type": body.get("scene_type"),
        "required_beats": str_list(body.get("required_beats")),
        "forbidden_beats": str_list(body.get("forbidden_beats")),
        "reviewer_false_positive_traps": body.get("reviewer_false_positive_traps") or [],
        "reviewer_instructions": body.get("reviewer_instructions") or {},
        "word_budget": word_budget,
    }


def _flat_drafter_contract(scene_body: dict[str, Any], chapter_body: dict[str, Any]) -> dict[str, Any]:
    """Translate the structured ScenePacket (+ chapter locks) into the flat MUST/MUST-NOT view the
    drafter's _contract_block already formats. Scene-local reveal/hidden rules become the reveal
    constraints; chapter locks remain immutable."""
    hidden = scene_body.get("must_remain_hidden") or {}
    learned = scene_body.get("learned_during_scene") or {}
    pov_perms = scene_body.get("pov_permissions") or {}
    contract: dict[str, Any] = {}
    forbidden_reveals = str_list(hidden.get("reader")) + str_list(hidden.get("all_surface_prose"))
    if forbidden_reveals:
        contract["forbidden_reveals"] = forbidden_reveals
    forbidden_knowledge = str_list(hidden.get("pov")) + str_list(pov_perms.get("must_not_know"))
    if forbidden_knowledge:
        contract["forbidden_knowledge"] = forbidden_knowledge
    required_reveals = str_list(learned.get("reader_must_learn"))
    if required_reveals:
        contract["required_reveals"] = required_reveals
    for key in ("owned_beats", "required_beats", "forbidden_beats", "beats_owned_by_later_scenes"):
        if vals := str_list(scene_body.get(key)):
            contract[key] = vals
    if isinstance(scene_body.get("entry_state"), str) and scene_body["entry_state"].strip():
        contract["entry_state"] = scene_body["entry_state"].strip()
    if isinstance(scene_body.get("exit_state"), str) and scene_body["exit_state"].strip():
        contract["exit_state"] = scene_body["exit_state"].strip()
    for key in _CHAPTER_LOCK_KEYS:
        if vals := str_list(chapter_body.get(key)):
            contract[key] = vals
    return contract
