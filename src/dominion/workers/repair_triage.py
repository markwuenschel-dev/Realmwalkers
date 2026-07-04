"""Root-cause clustering for repair triage.

Pure, deterministic helpers (no DB, no LLM) that map accepted QA issues onto the
pinned root-cause cluster keys and plan repair-task creation:

    sequence_entry_state | scene_scope_bleed | budget_mismatch |
    canon_contract_leak | prose_polish | infra_rate_limit

Contract (see reports/ch1_pipeline_failure_analysis.md section 5):

- Each STRUCTURAL cluster (``sequence_entry_state``, ``scene_scope_bleed``,
  ``budget_mismatch``, ``canon_contract_leak``) collapses into ONE chapter-scoped
  root repair task carrying every member issue id — never a per-scene scatter.
- ``prose_polish`` issues become per-scene repair tasks only when NO structural
  cluster is unresolved; otherwise they are deferred (stay accepted, untasked).
- ``infra_rate_limit`` issues never create repair tasks — they are retry state.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from dominion.shared.enums import RepairAuthorityLevel

ROOT_CAUSE_SEQUENCE_ENTRY_STATE = "sequence_entry_state"
ROOT_CAUSE_SCENE_SCOPE_BLEED = "scene_scope_bleed"
ROOT_CAUSE_BUDGET_MISMATCH = "budget_mismatch"
ROOT_CAUSE_CANON_CONTRACT_LEAK = "canon_contract_leak"
ROOT_CAUSE_PROSE_POLISH = "prose_polish"
ROOT_CAUSE_INFRA_RATE_LIMIT = "infra_rate_limit"

#: Structural root causes in pinned priority order (also the task-creation order).
STRUCTURAL_ROOT_CAUSES: tuple[str, ...] = (
    ROOT_CAUSE_SEQUENCE_ENTRY_STATE,
    ROOT_CAUSE_SCENE_SCOPE_BLEED,
    ROOT_CAUSE_BUDGET_MISMATCH,
    ROOT_CAUSE_CANON_CONTRACT_LEAK,
)

#: Authority level of the single chapter-scoped root repair task per cluster.
STRUCTURAL_AUTHORITY: dict[str, RepairAuthorityLevel] = {
    ROOT_CAUSE_SEQUENCE_ENTRY_STATE: RepairAuthorityLevel.CHAPTER_STRUCTURAL,
    ROOT_CAUSE_SCENE_SCOPE_BLEED: RepairAuthorityLevel.CROSS_SCENE,
    ROOT_CAUSE_BUDGET_MISMATCH: RepairAuthorityLevel.CHAPTER_STRUCTURAL,
    ROOT_CAUSE_CANON_CONTRACT_LEAK: RepairAuthorityLevel.CHAPTER_STRUCTURAL,
}

#: Instruction preamble stamped onto each structural root repair task.
ROOT_CAUSE_INSTRUCTIONS: dict[str, str] = {
    ROOT_CAUSE_SEQUENCE_ENTRY_STATE: (
        "Root cause: broken entry-state chaining. Re-derive each scene's entry_state from the prior "
        "scene's exit_state (scene 1 uses the global entry state), then re-run dependent drafting. "
        "Do not patch pacing/transition symptoms scene by scene."
    ),
    ROOT_CAUSE_SCENE_SCOPE_BLEED: (
        "Root cause: scene scope bleed. Enforce beat_ownership and forbidden_duplicate_functions so "
        "every irreversible beat is performed exactly once, in its owning scene; strip duplicated beats "
        "from non-owning scenes."
    ),
    ROOT_CAUSE_BUDGET_MISMATCH: (
        "Root cause: scene/chapter word-budget contradiction. Reconcile scene packet budgets against the "
        "chapter hard_max BEFORE any rewriting; per-scene trims cannot fix a global contract error."
    ),
    ROOT_CAUSE_CANON_CONTRACT_LEAK: (
        "Root cause: canon contract leak. Remove or replace content that violates resolved chapter "
        "rulings and re-verify the draft against the canon contract."
    ),
}

# --- issue_kind routing tables (explicit kinds win over text heuristics) ---

_RATE_LIMIT_KINDS = frozenset({"infra_rate_limit"})
_CANON_KINDS = frozenset({"canon_contract_leak"})
_SCOPE_KINDS = frozenset({"scene_scope_bleed", "duplicate_irreversible_beat"})
_BUDGET_KINDS = frozenset({"sequence_budget_mismatch", "length", "budget", "word_budget"})
_BUDGET_VALIDATORS = frozenset({"length", "budget"})
_ENTRY_KINDS = frozenset({"sequence_entry_state", "entry_state_mismatch", "transition", "pacing"})
_ENTRY_VALIDATORS = frozenset({"pacing"})

# --- text heuristics for legacy issue kinds that only describe symptoms ---

_RATE_LIMIT_RE = re.compile(r"\b429\b|rate[ _-]?limit|tokens? per minute|\btpm\b", re.IGNORECASE)
_DUPLICATE_BEAT_RE = re.compile(
    r"duplicat|re-?perform|re-?stag|already (?:performed|staged|landed|revealed)"
    r"|repeat(?:ed|s)?\s+(?:the\s+)?(?:beat|recognition|reveal)",
    re.IGNORECASE,
)
_ENTRY_STATE_RE = re.compile(
    r"entry[ _-]state|exit[ _-]state|transition (?:mismatch|from|into)"
    r"|starts? (?:over )?from the (?:global|chapter) entry|does not pick up (?:from|where)",
    re.IGNORECASE,
)


class IssueLike(Protocol):
    """Minimal read surface the clustering needs from an issue row.

    Getter types are ``Any`` so both plain in-memory rows (tests) and
    SQLAlchemy ``Mapped[str]`` model attributes satisfy the protocol.
    """

    @property
    def issue_kind(self) -> Any: ...

    @property
    def validator(self) -> Any: ...

    @property
    def claim(self) -> Any: ...


def _issue_text(issue: IssueLike) -> str:
    parts = [getattr(issue, "claim", "") or "", getattr(issue, "recommended_action", "") or ""]
    return " ".join(part for part in parts if part)


def infer_root_cause(issue: IssueLike) -> str:
    """Map one issue onto a pinned root-cause cluster key.

    Explicit (new-generation) issue kinds route directly; legacy symptom kinds
    fall through deterministic text heuristics; everything residual is
    ``prose_polish``.
    """
    kind = (getattr(issue, "issue_kind", "") or "").strip().lower()
    validator = (getattr(issue, "validator", "") or "").strip().lower()
    text = _issue_text(issue)

    if kind in _RATE_LIMIT_KINDS or validator == "infra" or _RATE_LIMIT_RE.search(text):
        return ROOT_CAUSE_INFRA_RATE_LIMIT
    if kind in _CANON_KINDS or validator == "canon":
        return ROOT_CAUSE_CANON_CONTRACT_LEAK
    if kind in _SCOPE_KINDS:
        return ROOT_CAUSE_SCENE_SCOPE_BLEED
    if kind in _BUDGET_KINDS or validator in _BUDGET_VALIDATORS:
        return ROOT_CAUSE_BUDGET_MISMATCH
    if _DUPLICATE_BEAT_RE.search(text):
        return ROOT_CAUSE_SCENE_SCOPE_BLEED
    if kind in _ENTRY_KINDS or validator in _ENTRY_VALIDATORS or _ENTRY_STATE_RE.search(text):
        return ROOT_CAUSE_SEQUENCE_ENTRY_STATE
    return ROOT_CAUSE_PROSE_POLISH


def cluster_issues[IssueT: IssueLike](issues: Sequence[IssueT]) -> dict[str, list[IssueT]]:
    """Group issues by root-cause cluster key (insertion order preserved)."""
    clusters: dict[str, list[IssueT]] = {}
    for issue in issues:
        clusters.setdefault(infer_root_cause(issue), []).append(issue)
    return clusters


@dataclass(frozen=True)
class TriagePlan[IssueT: IssueLike]:
    """Deterministic repair-task plan for a set of accepted issues.

    ``structural_clusters`` maps each present structural key (in pinned order)
    to its member issues — exactly ONE chapter-scoped repair task per entry.
    ``prose_issues`` become per-scene tasks only when ``defer_prose`` is False.
    ``rate_limit_issues`` never create repair tasks.
    """

    structural_clusters: dict[str, list[IssueT]] = field(default_factory=dict)
    prose_issues: list[IssueT] = field(default_factory=list)
    rate_limit_issues: list[IssueT] = field(default_factory=list)

    @property
    def defer_prose(self) -> bool:
        """Prose repair is gated while any structural cluster is in the plan."""
        return bool(self.structural_clusters)


def plan_repair_tasks[IssueT: IssueLike](issues: Sequence[IssueT]) -> TriagePlan[IssueT]:
    """Cluster accepted issues and split them into the three planning lanes."""
    clusters = cluster_issues(issues)
    structural = {key: clusters[key] for key in STRUCTURAL_ROOT_CAUSES if key in clusters}
    return TriagePlan(
        structural_clusters=structural,
        prose_issues=clusters.get(ROOT_CAUSE_PROSE_POLISH, []),
        rate_limit_issues=clusters.get(ROOT_CAUSE_INFRA_RATE_LIMIT, []),
    )
