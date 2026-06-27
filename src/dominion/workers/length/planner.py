"""Deterministic per-scene word-budget planner (DESIGN: word budgeting).

The ChapterPacket gives a chapter target; this module splits it across the chapter's scenes by
deterministic weight (scene type + structural pressure) and emits a full `word_budget` per scene —
min/target/max/hard_max plus compression/expansion priorities and `must_not_spend_words_on`. The
ScenePacket builder folds this budget into each scene's contract, and the Length Guard enforces it.

Pure and side-effect free: same inputs → same budgets. A human-provided `word_budget` on a scene
seed overrides the deterministic allocation for that scene (the author's call wins).
"""
from __future__ import annotations

from typing import Any

# Per-scene-type weight: how much page-space a scene of this kind earns relative to a plain dialogue
# scene (1.0). Combat/climax/rupture earn the most; bridges/transitions the least.
SCENE_TYPE_WEIGHTS: dict[str, float] = {
    "bridge": 0.55,
    "transition": 0.65,
    "setup": 0.85,
    "logistics": 0.85,
    "dialogue": 1.00,
    "investigation": 1.15,
    "reveal": 1.25,
    "emotional": 1.35,
    "combat": 1.60,
    "duel": 1.80,
    "coercion": 1.80,
    "rupture": 2.00,
    "climax": 2.10,
    "aftermath": 1.10,
}

_DEFAULT_WEIGHT = 1.0

# Clamps (DESIGN: word budgeting).
_MIN_TARGET = 700
_NORMAL_HARD_MAX = 4500
_CLIMAX_HARD_MAX = 6500

# Default compression order: what to cut first, what to protect. Scene-specific
# `must_not_spend_words_on` is layered on top from the seed's forbidden knowledge/reveals.
_DEFAULT_COMPRESSION_PRIORITY: tuple[str, ...] = (
    "Compress logistics first.",
    "Compress exposition second.",
    "Compress repeated internal processing third.",
    "Preserve required reveals.",
    "Preserve the emotional turn.",
    "Preserve any irreversible state change.",
    "Preserve the scene exit state.",
)

_DEFAULT_EXPANSION_PRIORITY: tuple[str, ...] = (
    "Add physical grounding around required beats.",
    "Add reaction beats to land the emotional turn.",
    "Clarify the scene's exit state.",
)


def _as_str_list(value: Any) -> list[str]:
    return [str(v).strip() for v in value if str(v).strip()] if isinstance(value, list) else []


def _scene_type(seed: dict[str, Any]) -> str:
    return str(seed.get("scene_type") or "").strip().lower()


def _is_climax(seed: dict[str, Any]) -> bool:
    return (
        _scene_type(seed) == "climax"
        or str(seed.get("chapter_position") or "").strip().lower() == "climax"
        or bool(seed.get("is_chapter_climax"))
    )


def scene_weight(seed: dict[str, Any]) -> float:
    """Deterministic weight for one scene seed. Base 1.0, scaled by scene type and structural load."""
    weight = SCENE_TYPE_WEIGHTS.get(_scene_type(seed), _DEFAULT_WEIGHT)

    required_beats = _as_str_list(seed.get("required_beats"))
    if len(required_beats) > 2:
        weight += 0.12 * (len(required_beats) - 2)

    required_reveals = _as_str_list(seed.get("required_reveals")) or _as_str_list(
        seed.get("reader_must_learn")
    )
    weight += 0.20 * len(required_reveals)

    characters = _as_str_list(seed.get("characters_present")) or _as_str_list(seed.get("characters"))
    if len(characters) > 2:
        weight += 0.10 * (len(characters) - 2)

    if seed.get("major_emotional_turn"):
        weight += 0.25
    if seed.get("irreversible_state_change"):
        weight += 0.35
    if _is_climax(seed):
        weight += 0.40

    return round(weight, 4)


def _must_not_spend_words_on(seed: dict[str, Any], chapter_body: dict[str, Any]) -> list[str]:
    """Things this scene must not burn words on, from its forbidden reveals/knowledge + packet drift
    risks — phrased as instructions the compressor and drafter can act on."""
    items: list[str] = []
    for reveal in _as_str_list(seed.get("forbidden_reveals")) + _as_str_list(
        chapter_body.get("forbidden_reveals")
    ):
        items.append(f"revealing or explaining: {reveal}")
    for fact in _as_str_list(seed.get("forbidden_knowledge")) + _as_str_list(
        chapter_body.get("forbidden_knowledge")
    ):
        items.append(f"explaining hidden canon: {fact}")
    # de-dup, preserve order
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _budget_for(target: int, *, climax: bool) -> dict[str, Any]:
    target = max(target, _MIN_TARGET)
    hard_cap = _CLIMAX_HARD_MAX if climax else _NORMAL_HARD_MAX
    hard_max = min(round(target * 1.60), hard_cap)
    return {
        "min": round(target * 0.70),
        "target": target,
        "max": min(round(target * 1.35), hard_max),
        "hard_max": hard_max,
    }


def _manual_budget(seed: dict[str, Any]) -> dict[str, Any] | None:
    """A human-provided word_budget on the seed overrides the deterministic allocation. Requires at
    least a numeric target; missing min/max/hard_max are filled from the target."""
    wb = seed.get("word_budget")
    if not isinstance(wb, dict):
        return None
    target = wb.get("target")
    if not isinstance(target, int) or target <= 0:
        return None
    base = _budget_for(target, climax=_is_climax(seed))
    out = dict(base)
    for key in ("min", "max", "hard_max"):
        val = wb.get(key)
        if isinstance(val, int) and val > 0:
            out[key] = val
    return out


def plan_word_budgets(
    *,
    chapter_target_words: int,
    chapter_max_words: int | None,
    scene_seeds: list[dict[str, Any]],
    chapter_packet_body: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """Allocate a full word_budget per scene seed, keyed by the seed's `seed_id` (stringified).

    Targets are normalized so they sum to roughly `chapter_target_words`, then each scene's budget is
    derived from its target and decorated with compression/expansion priorities. A seed carrying a
    manual `word_budget` keeps it (the author's call); the remaining chapter words are split across
    the auto-planned scenes by weight.
    """
    chapter_body = chapter_packet_body or {}
    seeds = [s for s in scene_seeds if isinstance(s, dict) and s.get("seed_id")]
    if not seeds:
        return {}

    manual: dict[str, dict[str, Any]] = {}
    auto: list[dict[str, Any]] = []
    for seed in seeds:
        mb = _manual_budget(seed)
        if mb is not None:
            manual[str(seed["seed_id"])] = mb
        else:
            auto.append(seed)

    manual_spend = sum(b["target"] for b in manual.values())
    remaining = max(chapter_target_words - manual_spend, 0)

    budgets: dict[str, dict[str, Any]] = {}
    total_weight = sum(scene_weight(s) for s in auto) or 1.0
    for seed in auto:
        sid = str(seed["seed_id"])
        w = scene_weight(seed)
        target = round(remaining * w / total_weight) if remaining else _MIN_TARGET
        budgets[sid] = _budget_for(target, climax=_is_climax(seed))

    # Decorate every budget (manual + auto) with priorities and scene-specific don't-spend list.
    by_id = {str(s["seed_id"]): s for s in seeds}
    for sid, budget in {**manual, **budgets}.items():
        seed = by_id[sid]
        budget["compression_priority"] = list(_DEFAULT_COMPRESSION_PRIORITY)
        budget["expansion_priority"] = list(_DEFAULT_EXPANSION_PRIORITY)
        budget["must_not_spend_words_on"] = _must_not_spend_words_on(seed, chapter_body)
        budgets[sid] = budget

    # If a chapter hard cap is given, never let any single scene's hard_max exceed it.
    if isinstance(chapter_max_words, int) and chapter_max_words > 0:
        for budget in budgets.values():
            budget["hard_max"] = min(budget["hard_max"], chapter_max_words)
            budget["max"] = min(budget["max"], budget["hard_max"])

    return budgets
