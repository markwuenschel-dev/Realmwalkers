"""Mode-specific evaluation prompts (Lane 3B).

Each adapter may READ all relevant context but returns findings ONLY for the clauses its mode owns
(ADR 0011/0013). The model reports evidence — never a verdict with authority: it emits per-clause
``satisfied`` / ``lost`` / ``indeterminate`` with prose anchors; deterministic code owns everything else
(coverage, dependency blocking, policy, currentness). Prompt text is versioned by
``settings.scene_fidelity_prompt_version`` and recorded on every report as provenance (ADR 0014).
"""

from __future__ import annotations

import json
from typing import Any

from dominion.workers.scene_fidelity.models import FidelityMode

# One-line framing per mode. Perceptual/relational, never generic taste review (ADR 0011).
_MODE_LENS: dict[FidelityMode, str] = {
    FidelityMode.RELATIONSHIP_TURN: (
        "You judge whether a relationship BEAT turns as required — who drives it, whose agency is "
        "exercised, and whether the shift is dramatized on the page rather than asserted in narration."
    ),
    FidelityMode.INTIMACY_BLOCKING: (
        "You judge the physical and consent blocking of an intimate beat: what is established before "
        "contact escalates, and whether the choreography stays coherent."
    ),
    FidelityMode.COMBAT_BLOCKING: (
        "You judge combat choreography: weapon/position/wound continuity and whether a decisive object "
        "or move is established and reachable before it is used."
    ),
    FidelityMode.SPATIAL_AFFORDANCE: (
        "You judge spatial coherence: whether the geography the prose relies on is established and whether "
        "what a character reaches for is actually reachable from where the scene places them."
    ),
    FidelityMode.READER_MOVIE: (
        "You judge only whether the reader can PERCEIVE the decisive action — is the turning beat rendered "
        "(shown) or summarized after the fact? This is perceptual, not a general quality or taste review."
    ),
}

_EVIDENCE_KINDS = (
    "action, dialogue, interiority, sequence, spatial_relation, sensory_anchor, state_change, absence_or_restraint"
)
_ANCHOR_KINDS = "contradiction, expected_beat, transition, satisfaction"


def system_prompt(mode: FidelityMode) -> str:
    return (
        f"You are the SceneFidelity {mode.value} evaluator. {_MODE_LENS[mode]}\n\n"
        "You are given a scene's prose and a list of CLAUSES, each with a stable clause_id and — for hard "
        "clauses — a satisfaction criterion. For EACH clause you are given (and ONLY those), decide:\n"
        "  - satisfied: the prose contains positive evidence meeting the criterion. Cite a `satisfaction` "
        "anchor quoting the exact prose that satisfies it.\n"
        "  - lost: the prose directly contradicts the clause, or a required beat/omission is corroborated. "
        "Cite a `contradiction` anchor (for a contradiction) or an `expected_beat`/`transition` anchor "
        "nearest where the missing beat should occur (for an omission — absence has no quote of its own).\n"
        "  - indeterminate: the prose neither clearly satisfies nor contradicts the clause.\n\n"
        "Report evidence, not judgement about importance. Never invent a quote: every anchor's `quote` must "
        "be an EXACT substring of the prose, with `start`/`end` character offsets into it. Do NOT report on "
        "any clause_id you were not given. Output ONLY strict JSON of the form:\n"
        '{"findings": [{"clause_id": "...", "result": "satisfied|lost|indeterminate", '
        '"evidence_anchors": [{"start": int, "end": int, "quote": "...", "anchor_kind": '
        f'"{_ANCHOR_KINDS}"}}], "explanation": "..."}}]}}\n'
        f"Valid evidence kinds referenced by criteria: {_EVIDENCE_KINDS}."
    )


def user_prompt(clauses: list[dict[str, Any]], *, prose: str, scene_context: dict[str, Any] | None = None) -> str:
    """The clauses this mode owns + the prose to evaluate, plus optional read-only context."""
    payload = {
        "clauses": [
            {
                "clause_id": c.get("clause_id"),
                "enforcement": c.get("enforcement"),
                "statement": c.get("statement"),
                "satisfaction_criterion": c.get("satisfaction_criterion"),
                "depends_on_clause_ids": c.get("depends_on_clause_ids") or [],
            }
            for c in clauses
        ]
    }
    parts = ["CLAUSES YOU OWN (report on these clause_ids only):", json.dumps(payload, ensure_ascii=False)]
    if scene_context:
        parts += ["\nSCENE CONTEXT (read-only, for reference):", json.dumps(scene_context, ensure_ascii=False)]
    parts += ["\nSCENE PROSE:", prose or "(empty)"]
    return "\n".join(parts)
