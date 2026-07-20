"""Author-controlled repair previews (Lane 6) — pure helpers.

A RepairPreview is an immutable, bounded proposal tied to one actionable fidelity Critique: it shows a
diff, rationale, and preservation boundary but NEVER changes the current Scene (ADR 0017). Only the
author, by accepting or editing it, materializes a new scene revision. The DB-touching create/accept/
reject live in production_fidelity; this module owns the deterministic body, the diff, and the bounded
repair prompt (restricted to the cited loss — it may not touch canon, outcome, or unrelated scenes).
"""

from __future__ import annotations

import difflib
from typing import Any

from dominion.workers.scene_fidelity.models import ClauseEvaluation

REPAIR_PREVIEW_ARTIFACT_TYPE = "scene_fidelity_repair_preview"


def compute_diff(old_prose: str, candidate_prose: str) -> str:
    """A unified diff from the current prose to the candidate, for the author to inspect."""
    return "".join(
        difflib.unified_diff(
            (old_prose or "").splitlines(keepends=True),
            (candidate_prose or "").splitlines(keepends=True),
            fromfile="current",
            tofile="preview",
        )
    )


def evidence_window(anchors: list[dict[str, Any]]) -> dict[str, int] | None:
    """The minimal prose span the cited evidence covers — the region a repair is allowed to touch."""
    starts: list[int] = []
    ends: list[int] = []
    for a in anchors:
        start, end = a.get("start"), a.get("end")
        if isinstance(start, int) and isinstance(end, int):
            starts.append(start)
            ends.append(end)
    if not starts:
        return None
    return {"start": min(starts), "end": max(ends)}


def build_preview_body(
    *,
    source_issue_id: str,
    source_critique_id: str,
    source_report_artifact_id: str,
    source_draft_attempt_id: str,
    scene_id: str,
    prose_hash: str,
    packet_fingerprint: str,
    clause_ids: list[str],
    anchors: list[dict[str, Any]],
    old_prose: str,
    candidate_prose: str,
    rationale: str,
    edited: bool,
) -> dict[str, Any]:
    """Assemble the immutable preview Artifact body. It carries every provenance ID needed to check the
    preview is still current when the author acts, plus the bounded change (window + boundary + diff)."""
    window = evidence_window(anchors)
    return {
        "preview_schema_version": 1,
        "source_issue_id": source_issue_id,
        "source_critique_id": source_critique_id,
        "source_report_artifact_id": source_report_artifact_id,
        "source_draft_attempt_id": source_draft_attempt_id,
        "scene_id": scene_id,
        "prose_hash": prose_hash,
        "packet_contract_fingerprint": packet_fingerprint,
        "clause_ids": clause_ids,
        "evidence_window": window,
        "preservation_boundary": (
            f"Preserve all prose outside characters [{window['start']}, {window['end']}); keep the scene's "
            "outcome, canon, and every other clause intact."
            if window
            else "Preserve the scene's outcome, canon, and every other clause; change only the cited loss."
        ),
        "diff": compute_diff(old_prose, candidate_prose),
        "candidate_prose": candidate_prose,
        "rationale": rationale,
        "edited": edited,
        "status": "proposed",
    }


def build_repair_prompt(
    evaluation: ClauseEvaluation, *, prose: str, prerequisite_statements: list[str]
) -> tuple[str, str]:
    """A tightly bounded repair prompt for one lost clause. The model may rewrite ONLY the cited loss and
    its minimal adjacent prose; it may not change canon, the scene outcome, or any other clause (ADR 0017)."""
    quotes = [a.quote for a in evaluation.evidence_anchors if a.quote]
    system = (
        "You are a SceneFidelity surgical repair writer. You are given ONE lost fidelity clause, the exact "
        "prose evidence of the loss, and the scene prose. Rewrite the SMALLEST possible span that resolves "
        "the clause while preserving everything else — the scene's outcome, its canon, its voice, and every "
        "other clause. You MUST NOT change canon, alter the scene outcome, touch any other scene, or address "
        "anything beyond the cited loss. Output ONLY the full revised scene prose."
    )
    lines = [
        f"LOST CLAUSE ({evaluation.mode.value}): {evaluation.explanation}",
    ]
    if prerequisite_statements:
        lines.append("REQUIRED PREREQUISITES (already established; keep them):")
        lines += [f"- {p}" for p in prerequisite_statements]
    if quotes:
        lines.append("CITED EVIDENCE OF THE LOSS (the only region to change):")
        lines += [f"- {q!r}" for q in quotes]
    lines += ["\nSCENE PROSE:", prose or "(empty)"]
    return system, "\n".join(lines)
