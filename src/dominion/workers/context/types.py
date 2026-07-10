"""Shared types for scene context assembly."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from dominion.shared.models import Beat, Chapter, PovProfile
from dominion.workers.budget import TokenBudget


class ScenePacketRequiredError(RuntimeError):
    """A draft job referenced a ScenePacket that is missing, not approved, or stale. Drafting fails
    closed rather than silently falling back to the chapter packet (scene-packet contract system)."""


@dataclass
class SceneContext:
    book_id: uuid.UUID
    chapter_id: uuid.UUID
    pov: str
    scene_no: int
    tags: list[str]
    characters_present: list[str]
    beat_text: str | None
    expected_state_changes: dict[str, Any] | None
    knowledge_injections: list[str]
    voice_spec: str | None
    budget: TokenBudget
    target_words: int | None = None
    exemplars: list[str] = field(default_factory=list)
    dialogue_rules: str | None = None
    canon: list[str] = field(default_factory=list)
    pov_summary: str | None = None
    ledger: dict[str, dict[str, Any]] = field(default_factory=dict)
    contract: dict[str, Any] | None = None
    scene_packet_id: uuid.UUID | None = None
    chapter_contract: dict[str, Any] | None = None
    scene_contract: dict[str, Any] | None = None
    reader_state_contract: dict[str, Any] | None = None
    word_budget: dict[str, Any] | None = None
    reviewer_contract: dict[str, Any] | None = None
    # Sectioned drafter view of the active SceneFidelity contract (Lane 3A); None when inert.
    fidelity: dict[str, Any] | None = None
    prior_scene_tail: str | None = None
    prior_prose: str | None = None
    revise_feedback: str | None = None
    target_pass: str | None = None
    # Live production timeline memory injected when DraftRunTimeline is active
    prior_exit_state: str | None = None
    spent_beats: list[str] = field(default_factory=list)
    reader_learned: list[str] = field(default_factory=list)
    must_not_repeat: list[str] = field(default_factory=list)
    chapter_so_far_summary: str | None = None


@dataclass(frozen=True)
class ResolvedJob:
    book_id: uuid.UUID
    chapter: Chapter
    beat: Beat
    profile: PovProfile | None
    scene_no: int
    scene_packet_id: uuid.UUID | None


@dataclass(frozen=True)
class DraftMemory:
    ledger: dict[str, dict[str, Any]]
    exemplars: list[str]
    canon: list[str]
    pov_summary: str | None
    prior_scene_tail: str | None
    # Production timeline memory (active DraftRunTimeline when a production run is driving sequential drafting)
    prior_exit_state: str | None = None
    spent_beats: list[str] = field(default_factory=list)
    reader_learned: list[str] = field(default_factory=list)
    must_not_repeat: list[str] = field(default_factory=list)
    chapter_so_far_summary: str | None = None


@dataclass(frozen=True)
class ScenePacketFields:
    scene_packet_id: uuid.UUID
    scene_contract: dict[str, Any]
    chapter_contract: dict[str, Any]
    word_budget: dict[str, Any] | None
    reader_state_contract: dict[str, Any]
    reviewer_contract: dict[str, Any]
    contract: dict[str, Any] | None
    fidelity: dict[str, Any] | None = None


@dataclass(frozen=True)
class RevisionState:
    prior_prose: str | None
    revise_feedback: str | None
