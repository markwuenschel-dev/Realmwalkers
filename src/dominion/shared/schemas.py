"""Pydantic DTOs — the wire contract for the API (mirrors the TS types in frontend/src/types.ts)."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from dominion.shared.enums import Decision, GateMode


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
    title: str | None = None             # optional; UI falls back to "Scene N"
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
    status: str


class BeatUpdateIn(BaseModel):
    """PUT body to edit a proposed beat (gate 1). Only provided fields are applied."""
    beat_text: str | None = None
    characters_present: list[str] | None = None
    tags: list[str] | None = None
    expected_state_changes: dict[str, Any] | None = None
    knowledge_injections: list[str] | None = None


class ChapterOut(_ORM):
    id: uuid.UUID
    book_id: uuid.UUID
    chapter_no: int
    title: str | None = None             # optional; UI falls back to "Chapter N"
    pov: str
    outline: str | None = None
    status: str


class RunStartIn(BaseModel):
    """POST body to start a run: outline a chapter; the planner proposes its beats (gate 1)."""
    book_id: uuid.UUID
    chapter_no: int
    pov: str
    outline: str
    gate_mode: GateMode = GateMode.PAUSE_EACH
    token_budget: int | None = None


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
    title: str | None = None
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


# --- Ledger read surfaces: characters + canon (PR-B) ----------------------------------------------

class CharacterOut(BaseModel):
    """A character's hard state, from the Oracle ledger. `role` is read from stats if present."""
    character: str
    role: str | None = None
    stats: dict[str, Any] = {}


class CanonOut(_ORM):
    id: uuid.UUID
    kind: str | None = None
    name: str | None = None
    body: str | None = None


# --- Curated/write surfaces: threads, annotations, suggestions (PR-C) ------------------------------

class ThreadOut(_ORM):
    id: uuid.UUID
    book_id: uuid.UUID
    name: str
    kind: str | None = None
    state: str | None = None
    note: str | None = None
    beats: list[dict[str, Any]] | None = None


class ThreadIn(BaseModel):
    """POST body to create a thread (author-curated)."""
    name: str
    kind: str | None = None
    state: str | None = None
    note: str | None = None
    beats: list[dict[str, Any]] | None = None


class ThreadUpdateIn(BaseModel):
    """PUT body to curate a thread. Only provided fields are applied."""
    name: str | None = None
    kind: str | None = None
    state: str | None = None
    note: str | None = None
    beats: list[dict[str, Any]] | None = None


class AnnotationOut(_ORM):
    id: uuid.UUID
    scene_id: uuid.UUID
    version: int | None = None
    quote: str | None = None
    author: str | None = None
    note: str | None = None
    created_at: datetime


class AnnotationIn(BaseModel):
    """POST body to add a margin note anchored to a quote in the scene."""
    quote: str | None = None
    author: str | None = None
    note: str
    version: int | None = None


class SuggestionOut(_ORM):
    id: uuid.UUID
    scene_id: uuid.UUID
    version: int | None = None
    quote: str | None = None
    new_text: str | None = None
    author: str | None = None
    why: str | None = None
    status: str
    created_at: datetime


class SuggestionIn(BaseModel):
    """POST body to add a track-changes suggestion (replace `quote` with `new_text`)."""
    quote: str | None = None
    new_text: str | None = None
    author: str | None = None
    why: str | None = None
    version: int | None = None


class SuggestionDecisionIn(BaseModel):
    """POST body to accept/reject a suggestion."""
    status: str                          # "accepted" | "rejected"
