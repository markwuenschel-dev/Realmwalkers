"""Tolerant parsing + validation for ScenePacket author/QA responses (scene-packet contract system).

Same fail-closed philosophy as the chapter packet parser: lenient extraction of one JSON object, but
an unrecognizable verdict or a structurally-thin body is treated as None so the orchestration blocks
the packet rather than degrading into partial constraints. Reuses the chapter packet extractor.
"""

from __future__ import annotations

from typing import Any

from dominion.shared.enums import ScenePacketVerdict
from dominion.shared.grading import parse_score
from dominion.shared.severity import normalize_llm_issue
from dominion.workers.packet.parse import extract_object, str_list  # shared tolerant extractor

__all__ = ["extract_object", "str_list", "parse_scene_qa", "valid_scene_packet_body"]

_VERDICTS = {v.value.upper(): v for v in ScenePacketVerdict}

# The body fields a usable ScenePacket must structurally carry (the contract's load-bearing sections).
_REQUIRED_BODY_KEYS = ("known_before_scene", "learned_during_scene", "word_budget")


def parse_scene_qa(raw: str) -> dict[str, Any] | None:
    """Parse a ScenePacket QA response into {verdict, residual_risks, issues, score}, or None (fail
    closed). An unknown verdict yields None — a gate never guesses. Issues are normalized to the
    machine-readable shape (guaranteed `severity`, capped at `repair`, plus derived `blocks_*` facts) —
    an LLM issue can never carry a drafting-blocking severity. `score` is the tolerantly-parsed
    per-dimension grade (missing scores -> None, never a gate — the Workstream-G object is advisory)."""
    obj = extract_object(raw)
    if obj is None:
        return None
    verdict = _VERDICTS.get(str(obj.get("verdict", "")).strip().upper())
    if verdict is None:
        return None
    issues = obj.get("issues")
    return {
        "verdict": verdict,
        "residual_risks": str_list(obj.get("residual_risks")),
        "issues": [normalize_llm_issue(i) for i in issues if isinstance(i, dict)] if isinstance(issues, list) else [],
        "score": parse_score(obj.get("score")),
    }


def valid_scene_packet_body(body: Any) -> bool:
    """A usable ScenePacket body must be a dict carrying the load-bearing contract sections."""
    if not isinstance(body, dict):
        return False
    return all(key in body for key in _REQUIRED_BODY_KEYS)
