"""Shared gate refusal types for fail-closed approval and drafting policies."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GateRefusal:
    """Human-readable reason an action is refused (HTTP 409 detail or Desk blocker copy)."""

    detail: str


def refusal_reasons(*refusals: GateRefusal | None) -> list[str]:
    return [r.detail for r in refusals if r is not None]
