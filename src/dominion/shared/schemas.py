"""Pydantic DTOs — the wire contract for the API (mirrors the TS types in frontend/src/types.ts)."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from dominion.shared.enums import Decision, GateMode, RuleProposalStatus, SuggestionStatus


class _ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class CritiqueOut(_ORM):
    id: uuid.UUID
    reviewer: str
    severity: str
    note: str | None = None
    payload: dict[str, Any] | None = None


class SceneOut(_ORM):
    id: uuid.UUID
    chapter_id: uuid.UUID
    scene_no: int
    version: int
    status: str
    prose: str | None = None
    prose_source: str
    passes_run: list[str] | None = None
    token_count: int | None = None
    model: str | None = None
    created_at: datetime


class SceneDetail(SceneOut):
    critiques: list[CritiqueOut] = []
    is_exemplar: bool = False  # is this scene a curated voice exemplar for its POV? (Tier 2 learning)


class ExemplarIn(BaseModel):
    """Toggle a scene as a voice exemplar for its POV (LEARNING_FROM_EDITS Tier 2)."""
    enabled: bool


class DecisionIn(BaseModel):
    """POST body for approve / deny / revise (DESIGN §9)."""
    decision: Decision
    target_pass: str | None = None       # set to scope a revision to one specialist pass
    feedback: str | None = None
    edited_prose: str | None = None      # hand-edit in the inbox -> becomes canonical text


class RunIn(BaseModel):
    """POST body to start a generation run (DESIGN §8)."""
    book_id: uuid.UUID
    scope: dict[str, Any]                          # e.g. {"chapter": 4} or {"chapters": [3, 4, 5]}
    gate_mode: GateMode = GateMode.PAUSE_EACH
    token_budget: int | None = None


class ContinuityResolveIn(BaseModel):
    """Resolve one continuity mismatch from the panel: pick prose or ledger (DESIGN §9)."""
    critique_id: uuid.UUID
    choice: str                          # "use_prose" | "use_ledger" | "edit"


# --- Gate 1: books, runs, chapters, beats (DESIGN §4, §8) -----------------------------------------

class BookIn(BaseModel):
    """POST body to create a book."""
    title: str
    premise: str | None = None


class BookOut(_ORM):
    id: uuid.UUID
    title: str
    premise: str | None = None
    created_at: datetime


class BeatOut(_ORM):
    id: uuid.UUID
    chapter_id: uuid.UUID
    scene_no: int
    beat_text: str | None = None
    characters_present: list[str] | None = None
    tags: list[str] | None = None
    expected_state_changes: dict[str, Any] | None = None
    knowledge_injections: list[str] | None = None
    target_words: int | None = None
    status: str


class BeatUpdateIn(BaseModel):
    """PUT body to edit a proposed beat (gate 1). Only provided fields are applied."""
    beat_text: str | None = None
    characters_present: list[str] | None = None
    tags: list[str] | None = None
    expected_state_changes: dict[str, Any] | None = None
    knowledge_injections: list[str] | None = None
    target_words: int | None = None


class BeatCreateIn(BaseModel):
    """POST body to add a beat by hand (a scene the planner didn't propose)."""
    scene_no: int
    beat_text: str | None = None
    characters_present: list[str] | None = None
    tags: list[str] | None = None
    expected_state_changes: dict[str, Any] | None = None
    knowledge_injections: list[str] | None = None
    target_words: int | None = None


class ApproveBeatsIn(BaseModel):
    """Optional POST body for approve: restrict to a subset of beats (those to draft now)."""
    beat_ids: list[uuid.UUID] | None = None


class ChapterOut(_ORM):
    id: uuid.UUID
    book_id: uuid.UUID
    chapter_no: int
    title: str | None = None
    pov: str
    outline: str | None = None
    status: str


class ChapterUpdateIn(BaseModel):
    """PATCH body to edit a chapter's authored fields (currently just the title). Only provided
    fields are applied (mirrors BeatUpdateIn / ThreadUpdateIn)."""
    title: str | None = None


class RunStartIn(BaseModel):
    """POST body to start a run: outline a chapter; the planner proposes its beats (gate 1).

    Re-running for the same chapter re-proposes (replaces the chapter's still-proposed beats).
    """
    book_id: uuid.UUID
    chapter_no: int
    pov: str
    outline: str
    gate_mode: GateMode = GateMode.PAUSE_EACH
    token_budget: int | None = None
    max_beats: int | None = None       # cap how many scenes the planner proposes
    target_words: int | None = None    # default per-scene length stamped on each proposed beat


class RunStartOut(BaseModel):
    """Result of starting a run: the chapter and its proposed (unapproved) beats."""
    run_id: uuid.UUID
    chapter_id: uuid.UUID
    chapter_no: int
    pov: str
    beats: list[BeatOut] = []


# --- History + manuscript read surfaces (DESIGN §9, §13) ------------------------------------------

class SceneVersionOut(SceneOut):
    """A scene row plus its preserved pre-edit text, for version diffing in History."""
    agent_original: str | None = None


class ManuscriptScene(BaseModel):
    scene_no: int
    prose: str | None = None


class ManuscriptChapter(BaseModel):
    chapter_no: int
    title: str | None = None
    pov: str
    scenes: list[ManuscriptScene] = []


class ManuscriptOut(BaseModel):
    """The approved manuscript, assembled in reading order (latest approved version per scene)."""
    book_id: uuid.UUID
    title: str
    chapters: list[ManuscriptChapter] = []


# --- Drafting (browser-driven worker) -------------------------------------------------------------

class ActiveScene(BaseModel):
    chapter_no: int | None = None
    scene_no: int | None = None
    # What the worker is doing right now ("drafting prose", "enriching · combat", "reviewing"), plus
    # how long it's been on this scene — so the Desk shows live progress, not a frozen spinner. Both
    # come from the in-process phase registry (workers/progress.py); null when unknown.
    phase: str | None = None
    elapsed_s: int | None = None


class JobsStatusOut(BaseModel):
    """Live queue state, so the Desk can show a 'drafting…' indicator without a terminal."""
    running: bool = False
    queued: int = 0
    failed: int = 0
    active_scene: ActiveScene | None = None


class DraftNextOut(BaseModel):
    scheduled: bool = False
    queued: int = 0
    running: bool = False


class RetryFailedOut(BaseModel):
    """Result of re-queuing FAILED jobs (e.g. after a transient outage or topping up API credits)."""
    requeued: int = 0
    scheduled: bool = False
    queued: int = 0
    running: bool = False


# --- World ledger + in-prose entity cards (DESIGN §5, §7) -----------------------------------------

class CharacterStateOut(BaseModel):
    """Hard numbers from the Oracle (CharacterState), with the canon body if the character has one."""
    character: str
    stats: dict[str, Any] = {}
    provisional: bool = False
    is_pov: bool = False
    body: str | None = None


class CanonEntityOut(_ORM):
    id: uuid.UUID
    kind: str | None = None
    name: str | None = None
    body: str | None = None


class CanonEntityIn(BaseModel):
    """Create a canon entity (location/faction/item/lore/…). Re-embedded on write for retrieval."""
    kind: str | None = None
    name: str | None = None
    body: str | None = None


class CanonEntityUpdateIn(BaseModel):
    """Edit a canon entity. Only provided fields are applied; body changes trigger a re-embed."""
    kind: str | None = None
    name: str | None = None
    body: str | None = None


class CharacterStateIn(BaseModel):
    """Seed/replace a character's Oracle stats (absolute values, not deltas), with an optional canon
    description. `stats` overwrites the stored stat block; `body` upserts a kind='character' canon
    entity so the hover-card / Ledger body and RAG see it too."""
    stats: dict[str, Any] = {}
    body: str | None = None


class CanonIngestOut(BaseModel):
    """Result of rebuilding the retrieval index from the on-disk canon docs."""
    indexed: int


# --- World threads (curated arcs across scenes) ---------------------------------------------------

class ThreadBeatOut(_ORM):
    id: uuid.UUID
    scene_no: int
    label: str | None = None
    flag: bool = False


class ThreadOut(BaseModel):
    id: uuid.UUID
    name: str
    kind: str | None = None
    state: str | None = None
    note: str | None = None
    beats: list[ThreadBeatOut] = []


class ThreadIn(BaseModel):
    name: str
    kind: str | None = None
    state: str | None = None
    note: str | None = None


class ThreadUpdateIn(BaseModel):
    """Only provided fields are applied (mirrors BeatUpdateIn)."""
    name: str | None = None
    kind: str | None = None
    state: str | None = None
    note: str | None = None


class ThreadBeatIn(BaseModel):
    scene_no: int
    label: str | None = None
    flag: bool = False


# --- Scene markup: annotations (margin notes) + suggestions (tracked changes) ---------------------

class AnnotationOut(_ORM):
    id: uuid.UUID
    scene_id: uuid.UUID
    version: int | None = None
    quote: str | None = None
    author: str | None = None
    note: str | None = None
    created_at: datetime


class AnnotationIn(BaseModel):
    note: str
    quote: str | None = None
    author: str | None = None


class SuggestionOut(_ORM):
    id: uuid.UUID
    scene_id: uuid.UUID
    version: int | None = None
    quote: str
    new_text: str | None = None
    author: str | None = None
    why: str | None = None
    status: str
    created_at: datetime


class SuggestionIn(BaseModel):
    quote: str
    new_text: str | None = None
    author: str | None = None
    why: str | None = None


class SuggestionDecisionIn(BaseModel):
    status: SuggestionStatus


# --- distilled voice/dialogue rules (LEARNING_FROM_EDITS Tier 3) -----------------------------------

class RuleProposalOut(_ORM):
    id: uuid.UUID
    book_id: uuid.UUID
    pov: str
    kind: str                                    # voice | dialogue
    rule_text: str
    rationale: str | None = None
    source_pair_ids: list[str] | None = None
    status: str                                  # pending | accepted | rejected
    created_at: datetime


class RuleProposalDecisionIn(BaseModel):
    """Accept or reject a proposed rule. On accept, `rule_text` (if set) replaces the proposed text,
    so the author can edit a rule before it lands in the POV's voice spec."""
    status: RuleProposalStatus
    rule_text: str | None = None


# --- canon / planning / style docs (Domain-B markdown, read-only) ---------------------------------
class DocMeta(BaseModel):
    path: str  # id, relative to the docs root (e.g. "canon/timeline/master_timeline.md")
    title: str  # first "# " heading, else a humanised filename
    category: str  # top-level folder: "canon" | "planning" | "style"


class DocOut(DocMeta):
    content: str  # raw markdown
