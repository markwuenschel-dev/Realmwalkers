"""State-drift reviewer (DESIGN §6, OPEN-1). Advisory: flags prose implying UNDECLARED state changes.

The beat declares the state changes a scene is supposed to land (`expected_state_changes`), and those
commit to the Oracle's ledger on approval. This reviewer reports the other direction: prose that
implies a stat/inventory/condition change the beat never declared — drift the ledger would silently
miss. It is advisory (INFO/WARN), never HARD, never blocking; the Oracle still owns truth and the
human adjudicates (DESIGN §5, §15). With nothing declared to compare against, it stays silent.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from dominion.shared.agent_policy import quality_effort, quality_temperature
from dominion.shared.config import settings
from dominion.workers.llm_escalation import complete_with_rate_limit_fallback
from dominion.workers.reviewers.base import Flag, advisory_severity, parse_json_objects

if TYPE_CHECKING:
    from dominion.workers.context import SceneContext

_REVIEW_MAX_TOKENS = 1200

_SYSTEM = (
    "You compare a scene's prose against the state changes its plan DECLARED. Report only concrete "
    "changes to a character's hard state — level, stats, HP, inventory, injuries, durable conditions "
    "— that the prose clearly implies but that are NOT in the declared list. Ignore mood and "
    "transient feeling. Do not infer aggressively, do not rewrite, do not invent. If everything the "
    "prose implies is already declared, report nothing."
)


def _prompt(prose: str, declared: dict[str, object]) -> str:
    return (
        "DECLARED state changes for this scene (JSON):\n"
        + json.dumps(declared)
        + "\n\nSCENE:\n"
        + prose
        + "\n\nReturn ONLY a JSON array (no prose, no code fences). Each item: "
        '{"character": str, "change": str, "note": str, "severity": "info"|"warn"}. '
        "Empty array [] if no undeclared state change is implied."
    )


class StateDriftReviewer:
    name = "state_drift"

    async def review(self, scene_prose: str, ctx: SceneContext) -> list[Flag]:
        if not ctx.expected_state_changes or not scene_prose.strip():
            return []
        raw, _usage = await complete_with_rate_limit_fallback(
            setting_key="review_model",
            model=settings.review_model,
            system=_SYSTEM,
            user=_prompt(scene_prose, ctx.expected_state_changes),
            max_tokens=_REVIEW_MAX_TOKENS,
            budget=ctx.budget,
            temperature=quality_temperature("review_model"),
            effort=quality_effort("review_model"),
        )
        flags: list[Flag] = []
        for item in parse_json_objects(raw):
            note = str(item.get("note", "")).strip()
            if not note:
                continue
            character = str(item.get("character", "")).strip()
            change = str(item.get("change", "")).strip()
            flags.append(
                Flag(
                    reviewer=self.name,
                    severity=advisory_severity(item.get("severity")),
                    note=note,
                    payload={"character": character, "change": change} if character or change else None,
                )
            )
        return flags


state_drift_reviewer = StateDriftReviewer()
