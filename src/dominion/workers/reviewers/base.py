"""Reviewer protocol. Reviewers ADVISE; they never mutate prose or block the inbox (DESIGN §2, §9)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from dominion.shared.enums import Severity

if TYPE_CHECKING:
    from dominion.workers.context import SceneContext


def strip_fences(s: str) -> str:
    """Drop a leading ```lang fence and trailing ``` from a model response, if present."""
    s = s.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else ""
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
    return s.strip()


def parse_json_objects(raw: str) -> list[dict[str, Any]]:
    """Tolerantly parse a model response into a list of JSON objects. Any malformed output -> []
    (advisory reviewers never fail a job on a bad LLM response, DESIGN §6)."""
    try:
        data = json.loads(strip_fences(raw))
    except (json.JSONDecodeError, ValueError):
        return []
    return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []


def advisory_severity(value: object) -> Severity:
    """Clamp a model-suggested severity to an advisory level. Reviewers never emit HARD — that is
    reserved for the continuity hard-number check (DESIGN §6, §9)."""
    return Severity.WARN if str(value).strip().lower() == "warn" else Severity.INFO


@dataclass
class Flag:
    """An advisory finding. Persisted as a Critique row; HARD numeric ones feed the continuity panel."""

    reviewer: str
    severity: Severity
    note: str
    payload: dict[str, Any] | None = None


@runtime_checkable
class Reviewer(Protocol):
    name: str

    async def review(self, scene_prose: str, ctx: SceneContext) -> list[Flag]: ...
