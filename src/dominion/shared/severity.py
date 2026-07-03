"""Contract-issue severity vocabulary shared by the chapter- and scene-packet tiers.

Three levels (writer-first policy — the product is drafting):

- ``block``  — a true blocker: schema-invalid/unparseable body, direct canon contradiction,
  impossible timeline, a missing required contract field, a chapter↔scene contradiction, or no
  draftable scene purpose. Stops drafting and everything downstream. Only DETERMINISTIC checks may
  emit it — an LLM QA agent can never hard-block drafting.
- ``repair`` — fixable. Does NOT block drafting or human review; DOES block final export. Emitted
  as a machine-readable repair task routed back to the author (agent or human).
- ``warn``   — advisory. Blocks nothing.

``issue_gates`` is the single pure derivation from severity to the three ``blocks_*`` facts every
serialized issue carries; policies and UIs read those facts instead of re-deriving them.
"""

from __future__ import annotations

from typing import Any, Literal

Severity = Literal["warn", "repair", "block"]

#: Severities that stop drafting (and human review). Deterministic checks only.
DRAFT_BLOCKING: frozenset[str] = frozenset({"block"})
#: Severities that stop final export — a repair task must be resolved before the chapter ships.
EXPORT_BLOCKING: frozenset[str] = frozenset({"block", "repair"})

# The most severe level an LLM QA agent may assign to an issue. QA is an attacker that is good at
# semantic risk but unreliable at hard facts — it may raise repair tasks, never hard blocks.
_LLM_SEVERITY_CAP_ALIASES: frozenset[str] = frozenset({"block", "blocker", "critical", "block_drafting"})
_KNOWN_ISSUE_SEVERITIES: frozenset[str] = frozenset({"info", "warn", "repair"})


def issue_gates(severity: str) -> dict[str, bool]:
    """The three gate facts derived from a severity. Pure; the only place the mapping lives."""
    blocks = severity in DRAFT_BLOCKING
    return {
        "blocks_drafting": blocks,
        "blocks_human_review": blocks,
        "blocks_final_export": severity in EXPORT_BLOCKING,
    }


def normalize_llm_issue(issue: dict[str, Any]) -> dict[str, Any]:
    """Normalize one LLM QA issue to the machine-readable shape: guaranteed ``severity`` (capped at
    ``repair`` — an LLM-claimed "block" is demoted, never trusted as a gate) plus the derived
    ``blocks_*`` facts. Unknown/missing severity degrades to ``warn``. Other keys pass through."""
    raw = str(issue.get("severity", "")).strip().lower()
    severity = "repair" if raw in _LLM_SEVERITY_CAP_ALIASES else (raw if raw in _KNOWN_ISSUE_SEVERITIES else "warn")
    return {
        "kind": str(issue.get("kind") or "") or None,
        "field": issue.get("field") if isinstance(issue.get("field"), str) else None,
        "detail": str(issue.get("detail") or issue.get("problem") or ""),
        "severity": severity,
        **issue_gates(severity),
    }
