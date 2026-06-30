"""Telemetry stage names for per-reviewer LLM call attribution (agent-ops Phase 3)."""

from __future__ import annotations

REVIEWER_NAMES: tuple[str, ...] = (
    "continuity",
    "voice",
    "pacing",
    "state_drift",
    "combat",
    "sensory",
    "dialogue",
)

REVIEWER_TELEMETRY_STAGES: tuple[str, ...] = tuple(f"reviewer_{name}" for name in REVIEWER_NAMES)

# Legacy coarse bucket — still mapped for historical llm_calls rows.
LEGACY_REVIEWERS_STAGE = "reviewers"


def reviewer_telemetry_stage(name: str) -> str:
    return f"reviewer_{name}"
