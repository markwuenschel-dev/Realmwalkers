"""Run-stage state machine for production runs — pure, deterministic, DB-free (recovery lane 6).

Canonical pipeline order for a ProductionRun:

    waiting_for_scene_drafts -> drafting_scenes -> scene_qa -> assembling_chapter
        -> chapter_qa -> (structural_repair_required | repair flow | final_ready)

`ProductionRun.current_stage` is a plain string column; the constants below are the pinned
vocabulary for the ordered part of the lifecycle. Every decision in this module is cheap
arithmetic/set logic and runs BEFORE any LLM call at its stage boundary — a structurally
broken run must be refused before a single token is spent. `production.py` owns persistence
and events; this module owns the decisions, so the decisions are testable without a database.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

# --- Pinned stage strings (plain strings — the column is a string; no enum migration) -------------

STAGE_WAITING_FOR_SCENE_DRAFTS = "waiting_for_scene_drafts"
STAGE_DRAFTING_SCENES = "drafting_scenes"
STAGE_SCENE_QA = "scene_qa"
STAGE_ASSEMBLING_CHAPTER = "assembling_chapter"
STAGE_CHAPTER_QA = "chapter_qa"
STAGE_STRUCTURAL_REPAIR_REQUIRED = "structural_repair_required"
STAGE_PROVIDER_RATE_LIMITED = "provider_rate_limited"

# Blocking issue kinds that are STRUCTURAL: they invalidate the chapter's skeleton, so per-symptom
# prose repair (repair_execution) is wasted spend until the structure is fixed. Pinned vocabulary —
# other recovery lanes emit these kinds; this lane only routes on them.
STRUCTURAL_BLOCKING_ISSUE_KINDS: frozenset[str] = frozenset(
    {
        "sequence_budget_mismatch",
        "sequence_scene_count_mismatch",
        "scene_scope_bleed",
        "duplicate_irreversible_beat",
        "canon_contract_leak",
    }
)


@dataclass
class StageDecision:
    """A deterministic verdict at a stage boundary.

    ok=True  -> proceed; next_stage is the stage the run should enter.
    ok=False -> refuse; next_stage is where the run must park (None = stay put),
                reason is a machine key, violations carry structured details for the run event.
    """

    ok: bool
    next_stage: str | None = None
    reason: str | None = None
    violations: list[dict[str, Any]] = field(default_factory=list)


# --- Helpers ---------------------------------------------------------------------------------------


def expected_scene_nos(sequence_body: Mapping[str, Any] | None) -> list[int]:
    """Scene numbers the derived ChapterSequence says must exist."""
    if not sequence_body:
        return []
    nos: set[int] = set()
    for item in sequence_body.get("scenes") or []:
        if isinstance(item, dict):
            no = item.get("scene_no")
            if isinstance(no, int) and no > 0:
                nos.add(no)
            elif isinstance(no, str) and no.isdigit() and int(no) > 0:
                nos.add(int(no))
    return sorted(nos)


def structural_kinds(issue_kinds: Iterable[str]) -> list[str]:
    """The subset of issue kinds that are structural blockers (sorted, de-duplicated)."""
    return sorted({str(k) for k in issue_kinds if str(k) in STRUCTURAL_BLOCKING_ISSUE_KINDS})


def _budget_int(word_budget: Any, key: str) -> int | None:
    if isinstance(word_budget, Mapping):
        value = word_budget.get(key)
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    return None


# --- Stage-boundary decisions ----------------------------------------------------------------------


def evaluate_assembly_readiness(
    sequence_body: Mapping[str, Any] | None,
    scenes_with_prose: Iterable[int],
    *,
    sequence_blocked: bool = False,
) -> StageDecision:
    """Assembly gate: may the run assemble a chapter right now?

    Refusals are structured (recorded as a run event by the caller), never an exception dump:
    - sequence structurally blocked      -> structural_repair_required
    - expected scenes lack prose         -> waiting_for_scene_drafts (missing scene numbers listed)
    - nothing has prose at all           -> waiting_for_scene_drafts
    Pass -> assembling_chapter.
    """
    have_prose = {int(n) for n in scenes_with_prose}
    if sequence_blocked:
        return StageDecision(
            ok=False,
            next_stage=STAGE_STRUCTURAL_REPAIR_REQUIRED,
            reason="sequence_blocked",
            violations=[
                {
                    "kind": "sequence_blocked",
                    "detail": "ChapterSequence QA blocks drafting; the sequence must be repaired before assembly.",
                }
            ],
        )
    expected = expected_scene_nos(sequence_body)
    missing = sorted(set(expected) - have_prose)
    if missing:
        return StageDecision(
            ok=False,
            next_stage=STAGE_WAITING_FOR_SCENE_DRAFTS,
            reason="missing_scene_prose",
            violations=[
                {
                    "kind": "missing_scene_prose",
                    "missing_scene_nos": missing,
                    "expected_scene_count": len(expected),
                    "detail": f"{len(missing)} of {len(expected)} sequence scenes have no prose.",
                }
            ],
        )
    if not have_prose:
        return StageDecision(
            ok=False,
            next_stage=STAGE_WAITING_FOR_SCENE_DRAFTS,
            reason="no_scene_prose",
            violations=[{"kind": "no_scene_prose", "detail": "No scene has any prose to assemble."}],
        )
    return StageDecision(ok=True, next_stage=STAGE_ASSEMBLING_CHAPTER)


def classify_qa_outcome(issue_kinds: Iterable[str]) -> StageDecision:
    """Post-assembly chapter-QA routing.

    Structural blocking kinds present -> structural_repair_required (single root-cause parking spot,
    NOT a scatter into repair_execution). Otherwise -> chapter_qa (normal triage/repair flow owns
    the run from there).
    """
    structural = structural_kinds(issue_kinds)
    if structural:
        return StageDecision(
            ok=False,
            next_stage=STAGE_STRUCTURAL_REPAIR_REQUIRED,
            reason="structural_blocking_issues",
            violations=[{"kind": kind} for kind in structural],
        )
    return StageDecision(ok=True, next_stage=STAGE_CHAPTER_QA)


def evaluate_drafting_readiness(
    *,
    sequence_status: str | None,
    sequence_qa_verdict: str | None,
    sequence_body: Mapping[str, Any] | None,
    scene_packets: Mapping[int, Mapping[str, Any]],
) -> StageDecision:
    """Drafting gate: may draft jobs (LLM spend) be queued for this run?

    All checks are deterministic and run BEFORE any LLM call:
    1. a derived sequence with scenes must exist;
    2. the sequence must not be QA-blocked (structural);
    3. per-scene hard-max budgets must fit the chapter hard max — a contradictory budget
       guarantees an over-budget chapter before a single token is spent (sequence_budget_mismatch);
    4. every sequence scene needs an APPROVED, NON-STALE ScenePacket.

    scene_packets: {scene_no: {"status": str, "word_budget": dict | None}}.
    """
    if not sequence_body or not expected_scene_nos(sequence_body):
        return StageDecision(
            ok=False,
            next_stage=None,
            reason="sequence_missing",
            violations=[{"kind": "sequence_missing", "detail": "No derived chapter sequence with scenes."}],
        )
    if str(sequence_status or "") == "blocked" or str(sequence_qa_verdict or "") == "block_drafting":
        return StageDecision(
            ok=False,
            next_stage=STAGE_STRUCTURAL_REPAIR_REQUIRED,
            reason="sequence_blocked",
            violations=[
                {
                    "kind": "sequence_blocked",
                    "detail": "ChapterSequence QA blocks drafting; repair the sequence before spending on drafts.",
                }
            ],
        )

    expected = expected_scene_nos(sequence_body)
    seq_by_no: dict[int, Mapping[str, Any]] = {}
    for item in sequence_body.get("scenes") or []:
        if isinstance(item, dict):
            no = item.get("scene_no")
            if isinstance(no, int):
                seq_by_no[no] = item

    # 3) Budget arithmetic — packet budgets are what the drafter/length guard actually enforce, so
    # their ceilings must fit the chapter ceiling (ch1 failure: 2200+2400+3200+2600 = 10,400 vs 7,200).
    chapter_hard_max = _budget_int(sequence_body, "hard_max_words") or 0
    if chapter_hard_max:
        per_scene: dict[int, int] = {}
        for no in expected:
            packet = scene_packets.get(no) or {}
            hard = _budget_int(packet.get("word_budget"), "hard_max")
            if hard is None:
                hard = _budget_int(seq_by_no.get(no, {}).get("word_budget"), "hard_max")
            if hard is not None:
                per_scene[no] = hard
        planned_hard_max = sum(per_scene.values())
        if per_scene and planned_hard_max > chapter_hard_max:
            return StageDecision(
                ok=False,
                next_stage=STAGE_STRUCTURAL_REPAIR_REQUIRED,
                reason="sequence_budget_mismatch",
                violations=[
                    {
                        "kind": "sequence_budget_mismatch",
                        "planned_hard_max_words": planned_hard_max,
                        "chapter_hard_max_words": chapter_hard_max,
                        "per_scene_hard_max": {str(no): words for no, words in sorted(per_scene.items())},
                        "detail": (
                            f"Scene hard-max budgets sum to {planned_hard_max} words, exceeding the "
                            f"chapter hard max of {chapter_hard_max} — contradictory before drafting."
                        ),
                    }
                ],
            )

    # 4) Approved, non-stale ScenePackets for every expected scene.
    packet_violations: list[dict[str, Any]] = []
    for no in expected:
        packet = scene_packets.get(no)
        status = str((packet or {}).get("status") or "")
        if packet is None:
            packet_violations.append({"kind": "scene_packet_missing", "scene_no": no})
        elif status == "stale":
            packet_violations.append({"kind": "scene_packet_stale", "scene_no": no, "status": status})
        elif status != "approved":
            packet_violations.append({"kind": "scene_packet_not_approved", "scene_no": no, "status": status})
    if packet_violations:
        return StageDecision(
            ok=False,
            next_stage=None,  # a human gate, not a stage regression — the run stays where it is
            reason="scene_packets_not_ready",
            violations=packet_violations,
        )

    return StageDecision(ok=True, next_stage=STAGE_DRAFTING_SCENES)


# --- Failure classification ------------------------------------------------------------------------

_RATE_LIMIT_MARKERS = ("llmratelimited", "rate limit", "rate_limit", "ratelimit", "429")


def is_provider_rate_limited(error: BaseException | str | None) -> bool:
    """True when a failure is a provider rate limit (429 past retries) on any path.

    Matches the LlmRateLimited exception type name as persisted in Job.last_error
    ("LlmRateLimited: provider rate limit (429) ...") as well as raw 429 signatures.
    """
    if error is None:
        return False
    text = f"{type(error).__name__}: {error}" if isinstance(error, BaseException) else str(error)
    lowered = text.lower()
    return any(marker in lowered for marker in _RATE_LIMIT_MARKERS)


def stage_after_draft_failure(error: BaseException | str | None) -> str | None:
    """Run-stage consequence of a failed draft/QA call.

    Provider rate limit -> provider_rate_limited (transient, retryable). Anything else -> None:
    a 429 must NEVER be classified as a contract/author failure, and this lane never routes other
    failures to a contract-failure state from here.
    """
    return STAGE_PROVIDER_RATE_LIMITED if is_provider_rate_limited(error) else None
