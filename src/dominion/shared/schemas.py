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
