"""Reconcile per-scene word budgets against the chapter sequence envelope.

Pure, import-light module (stdlib only) so the regression harness (lane 10)
and any worker can import it without pulling in DB/LLM machinery.

Policy (pinned, lane 3 of the ch1 recovery push):

* The chapter envelope — the sequence's ``hard_max_words`` — is AUTHORITATIVE.
* When the scenes' ``word_budget.hard_max`` values sum past the envelope, the
  scene budgets scale DOWN proportionally: each scene keeps its ``min`` floor,
  and the headroom above the floor (``hard_max - min``) is compressed by a
  single shared ratio so relative scene weights are preserved. Integer floor
  rounding guarantees ``sum(hard_max) <= chapter hard_max``.
* ``target`` and ``max`` get the same floor-anchored proportional treatment,
  which keeps the per-scene ordering ``min <= target <= max <= hard_max``
  intact (floor rounding is monotonic on the above-floor deltas).
* If reconciliation is impossible — the scene ``min`` floors alone exceed the
  chapter ``hard_max_words`` — no scaling can help. We emit exactly ONE
  blocking issue of kind ``sequence_budget_mismatch`` (never per-scene spam)
  so drafting is stopped BEFORE any LLM spend.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

# Pinned vocabulary: the blocking issue kind for an irreconcilable envelope.
SEQUENCE_BUDGET_MISMATCH = "sequence_budget_mismatch"

# The scalar fields of a scene word_budget that participate in reconciliation.
_SCALED_FIELDS = ("target", "max", "hard_max")


@dataclass(frozen=True)
class BudgetIssue:
    """A structural budget issue, shaped like the pipeline's QA issues."""

    kind: str
    detail: str
    severity: str = "block"
    blocks_drafting: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "detail": self.detail,
            "severity": self.severity,
            "blocks_drafting": self.blocks_drafting,
        }


@dataclass(frozen=True)
class ReconciliationResult:
    """Outcome of reconciling scene budgets against the chapter envelope.

    ``budgets`` is one dict per input scene budget, in input order, carrying
    (at least) ``min``/``target``/``max``/``hard_max``. Non-numeric keys from
    the input budgets (expansion/compression priorities etc.) are preserved
    untouched. When ``changed`` is False the dicts are value-identical to the
    inputs.
    """

    budgets: tuple[dict[str, Any], ...]
    issues: tuple[BudgetIssue, ...]
    changed: bool

    @property
    def blocking(self) -> bool:
        return any(issue.blocks_drafting for issue in self.issues)


def _coerce_int(value: Any, *, field: str, scene_label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"scene {scene_label}: word_budget.{field} is not numeric: {value!r}")
    return int(value)


def _read_budget(budget: Mapping[str, Any], *, scene_label: str) -> tuple[int, int, int, int]:
    mn = _coerce_int(budget.get("min", 0), field="min", scene_label=scene_label)
    tg = _coerce_int(budget.get("target", 0), field="target", scene_label=scene_label)
    mx = _coerce_int(budget.get("max", 0), field="max", scene_label=scene_label)
    hd = _coerce_int(budget.get("hard_max", 0), field="hard_max", scene_label=scene_label)
    return mn, tg, mx, hd


def reconcile(
    sequence_body: Mapping[str, Any] | int,
    scene_budgets: Sequence[Mapping[str, Any]] | None = None,
) -> ReconciliationResult:
    """Reconcile scene ``word_budget`` dicts against the chapter envelope.

    ``sequence_body`` is either the chapter ``hard_max_words`` itself (int) or
    a mapping that carries it (a ChapterSequence row/schema dump, or any dict
    with ``hard_max_words``). ``scene_budgets`` is the list of per-scene
    ``word_budget`` mappings; when omitted, they are pulled from
    ``sequence_body["scenes"]``/``sequence_body["body"]["scenes"]``.

    Returns a :class:`ReconciliationResult`:

    * consistent input  -> budgets unchanged, no issues
    * over-budget input -> budgets scaled down per the pinned policy, no issues
    * impossible input  -> budgets unchanged, ONE blocking
      ``sequence_budget_mismatch`` issue
    """
    if isinstance(sequence_body, Mapping):
        chapter_hard_max = sequence_body.get("hard_max_words")
        if scene_budgets is None:
            scenes = sequence_body.get("scenes")
            if scenes is None and isinstance(sequence_body.get("body"), Mapping):
                scenes = sequence_body["body"].get("scenes")
            scene_budgets = [scene.get("word_budget") or {} for scene in (scenes or [])]
    else:
        chapter_hard_max = sequence_body
    if scene_budgets is None:
        scene_budgets = []

    originals = tuple(dict(budget) for budget in scene_budgets)

    if not isinstance(chapter_hard_max, (int, float)) or isinstance(chapter_hard_max, bool):
        raise ValueError(f"chapter hard_max_words is not numeric: {chapter_hard_max!r}")
    chapter_hard_max = int(chapter_hard_max)

    if not originals:
        return ReconciliationResult(budgets=(), issues=(), changed=False)

    parsed = [_read_budget(budget, scene_label=str(index + 1)) for index, budget in enumerate(originals)]
    total_hard = sum(hd for _, _, _, hd in parsed)
    total_min = sum(mn for mn, _, _, _ in parsed)

    # Already consistent: hands off.
    if total_hard <= chapter_hard_max:
        return ReconciliationResult(budgets=originals, issues=(), changed=False)

    # Impossible: even the min floors overflow the chapter envelope. One
    # blocking issue for the whole sequence — this is a single global
    # contract error, not a per-scene defect.
    if total_min > chapter_hard_max:
        issue = BudgetIssue(
            kind=SEQUENCE_BUDGET_MISMATCH,
            detail=(
                "Scene word budgets cannot fit the chapter envelope: the scene "
                f"min floors sum to {total_min} words but the sequence "
                f"hard_max_words is {chapter_hard_max}. No proportional "
                "scaling can reconcile this; the sequence envelope or the "
                "scene min floors must be revised before drafting."
            ),
        )
        return ReconciliationResult(budgets=originals, issues=(issue,), changed=False)

    # Scale down: keep each min floor, compress the headroom above the floor
    # by a shared ratio (integer floor division preserves relative weights and
    # guarantees the scaled hard_max values sum to <= chapter_hard_max).
    headroom = chapter_hard_max - total_min
    total_over = total_hard - total_min  # > 0 here (total_hard > chapter >= total_min)
    scaled: list[dict[str, Any]] = []
    for original, (mn, _tg, _mx, _hd) in zip(originals, parsed, strict=True):
        budget = dict(original)
        budget["min"] = mn
        for field in _SCALED_FIELDS:
            delta = max(
                0,
                _coerce_int(original.get(field, mn), field=field, scene_label="?") - mn,
            )
            budget[field] = mn + (delta * headroom) // total_over
        scaled.append(budget)

    return ReconciliationResult(budgets=tuple(scaled), issues=(), changed=True)


def check_sequence_budget_consistency(
    chapter_hard_max_words: Any,
    scene_budgets: Sequence[Mapping[str, Any]],
) -> list[BudgetIssue]:
    """Draft-gate check: is the PERSISTED envelope consistent as stored?

    Unlike :func:`reconcile` (which silently repairs a scalable overflow at
    derivation time), the gate cannot rewrite persisted packets — a stored
    contradiction of either flavor must block drafting until the sequence is
    re-derived or the envelope is fixed. Returns at most ONE blocking
    ``sequence_budget_mismatch`` issue.

    A sequence with no numeric envelope is skipped (nothing to contradict) —
    this gate targets the arithmetic contradiction, not missing data.
    """
    if not scene_budgets:
        return []
    if not isinstance(chapter_hard_max_words, (int, float)) or isinstance(chapter_hard_max_words, bool):
        return []
    result = reconcile(int(chapter_hard_max_words), scene_budgets)
    if result.issues:
        return list(result.issues)
    if result.changed:
        parsed = [_read_budget(budget, scene_label=str(index + 1)) for index, budget in enumerate(scene_budgets)]
        total_hard = sum(hd for _, _, _, hd in parsed)
        return [
            BudgetIssue(
                kind=SEQUENCE_BUDGET_MISMATCH,
                detail=(
                    "Persisted scene word budgets overflow the chapter "
                    f"envelope: scene hard_max values sum to {total_hard} "
                    f"words but the sequence hard_max_words is "
                    f"{int(chapter_hard_max_words)}. Re-derive the sequence "
                    "(budgets now auto-scale at derivation) before drafting."
                ),
            )
        ]
    return []
