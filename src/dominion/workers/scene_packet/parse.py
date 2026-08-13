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

#: Verdicts that assert the contract is not fit to draft from as written. They are NOMINATIONS: the
#: model raises the repair bar, it does not close a gate (#278).
_NOMINATING_VERDICTS: frozenset[ScenePacketVerdict] = frozenset(
    {ScenePacketVerdict.BLOCK_DRAFTING, ScenePacketVerdict.REVISE_REQUIRED}
)

#: The `kind` of the issue a nominating verdict is recorded as. Rides the normal issue channel, so the
#: Desk's repair queue (`frontend/src/desk/lib/packetBlockers.ts:89`) and the export gate
#: (`severity.issue_gates`) pick it up with no new field and no new reader to remember.
VERDICT_NOMINATION_KIND = "qa_verdict_nomination"


def _verdict_nomination(verdict: ScenePacketVerdict, issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Record a nominating verdict that the model filed NO repair-or-worse issue for, so the signal is
    carried by the channel that legitimately gates (final export) instead of the one that must not
    (drafting).

    Why this exists. Severing the raw verdict from the draft gate (#278) would otherwise DROP the
    model's strongest statement on the floor whenever it said "unsafe" without itemizing why. ADR-0031
    R3 Fork 2 ruled that a model may nominate, never mint — so the verdict is converted into the same
    machine-readable `repair` task an itemized finding would have produced, capped exactly as
    `severity.normalize_llm_issue` caps an LLM-claimed `block`. Skipped when the model already filed a
    repair/blocking issue: that finding IS the nomination, and duplicating it would double the editor's
    repair queue for one problem.
    """
    if verdict not in _NOMINATING_VERDICTS:
        return []
    if any(i.get("blocks_final_export") for i in issues):
        return []
    return [
        normalize_llm_issue(
            {
                "kind": VERDICT_NOMINATION_KIND,
                "field": None,
                "detail": (
                    f"Scene-packet QA returned the verdict {verdict.value!r} without itemizing a repair. "
                    "An LLM verdict is advisory — it does not block drafting — but it is recorded here as a "
                    "repair task so it still blocks final export until a human or the author agent clears it."
                ),
                "severity": "repair",
            }
        )
    ]


def parse_scene_qa(raw: str) -> dict[str, Any] | None:
    """Parse a ScenePacket QA response into {verdict, residual_risks, issues, score}, or None (fail
    closed). An unknown verdict yields None — a gate never guesses. Issues are normalized to the
    machine-readable shape (guaranteed `severity`, capped at `repair`, plus derived `blocks_*` facts) —
    an LLM issue can never carry a drafting-blocking severity. `score` is the tolerantly-parsed
    per-dimension grade (missing scores -> None, never a gate — the Workstream-G object is advisory).

    The same cap now covers the VERDICT (#278). It was applied to `severity` and simply never applied to
    its sibling field, so `BLOCK_DRAFTING` stayed a live drafting gate reachable from a prompt: the
    chapter's resolved author rulings are rendered into this agent's prompt with "do NOT re-litigate"
    (`author.py:format_chapter_rulings`) and `qa.py:_SYSTEM` adds "do NOT flag it as an unresolved open
    question", so model compliance alone moved the gate — permissively. Here the verdict is kept as the
    advisory record it is, and a nominating verdict is materialized into the repair queue instead.
    """
    obj = extract_object(raw)
    if obj is None:
        return None
    verdict = _VERDICTS.get(str(obj.get("verdict", "")).strip().upper())
    if verdict is None:
        return None
    raw_issues = obj.get("issues")
    issues = [normalize_llm_issue(i) for i in raw_issues if isinstance(i, dict)] if isinstance(raw_issues, list) else []
    return {
        "verdict": verdict,
        "residual_risks": str_list(obj.get("residual_risks")),
        "issues": issues + _verdict_nomination(verdict, issues),
        "score": parse_score(obj.get("score")),
    }


def valid_scene_packet_body(body: Any) -> bool:
    """A usable ScenePacket body must be a dict carrying the load-bearing contract sections."""
    if not isinstance(body, dict):
        return False
    return all(key in body for key in _REQUIRED_BODY_KEYS)
