"""Shared aggregation helpers for telemetry API responses."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from dominion.shared.models import LlmCall
from dominion.workers.telemetry_cost import estimate_cache_savings_usd, estimate_calls_cost_usd

# Canonical pipeline order for scene timeline display.
PIPELINE_STAGE_ORDER: tuple[str, ...] = (
    "scene_packet_author_prefix_prime",
    "scene_packet_qa_prefix_prime",
    "scene_packet_author",
    "scene_packet_qa",
    "drafter",
    "reviewers",
    "enrichment",
    "length",
    "packet_author",
    "packet_qa",
    "beats",
    "chapter_title",
    "summary",
)

PRIME_STAGES = frozenset({"scene_packet_author_prefix_prime", "scene_packet_qa_prefix_prime"})


def call_metadata(call: LlmCall) -> dict[str, Any]:
    return dict(call.metadata_ or {})


def _totals(calls: Iterable[LlmCall]) -> dict[str, Any]:
    calls = list(calls)
    input_t = sum(c.input_tokens for c in calls)
    cc = sum(c.cache_creation_tokens for c in calls)
    cr = sum(c.cache_read_tokens for c in calls)
    prompt = input_t + cc + cr
    latencies = [c.latency_ms for c in calls if c.latency_ms is not None]
    cost = estimate_calls_cost_usd(calls)
    cache_saved_usd = estimate_cache_savings_usd(calls)
    fallbacks = sum(1 for c in calls if call_metadata(c).get("fallback_attempt"))
    return dict(
        calls=len(calls),
        input_tokens=input_t,
        output_tokens=sum(c.output_tokens for c in calls),
        cache_creation_tokens=cc,
        cache_read_tokens=cr,
        cache_hit_ratio=round(cr / prompt, 3) if prompt else 0.0,
        cache_tokens_saved=int(cr * 0.9),
        truncations=sum(1 for c in calls if c.truncated),
        errors=sum(1 for c in calls if c.error),
        fallbacks=fallbacks,
        avg_latency_ms=int(sum(latencies) / len(latencies)) if latencies else None,
        estimated_cost_usd=round(cost, 4),
        cache_savings_usd=round(cache_saved_usd, 4),
    )


def group_calls(calls: list[LlmCall], key: Callable[[LlmCall], object]) -> list[tuple[str, dict[str, Any]]]:
    buckets: dict[str, list[LlmCall]] = {}
    for c in calls:
        k = key(c)
        if k:
            buckets.setdefault(str(k), []).append(c)
    groups = [(k, _totals(v)) for k, v in buckets.items()]
    return sorted(groups, key=lambda kv: kv[1]["calls"], reverse=True)


def scene_status(calls: list[LlmCall]) -> str:
    if any(c.error for c in calls):
        return "error"
    if any(c.truncated for c in calls):
        return "warn"
    return "ok"


def scene_stages(calls: list[LlmCall]) -> list[str]:
    present = {c.stage for c in calls}
    ordered = [s for s in PIPELINE_STAGE_ORDER if s in present]
    extras = sorted(present - set(ordered))
    return ordered + extras


def pipeline_steps(calls: list[LlmCall]) -> list[dict[str, Any]]:
    by_stage: dict[str, list[LlmCall]] = {}
    for c in calls:
        by_stage.setdefault(c.stage, []).append(c)
    steps: list[dict[str, Any]] = []
    seen: set[str] = set()
    for stage in PIPELINE_STAGE_ORDER:
        if stage not in by_stage:
            continue
        seen.add(stage)
        sc = by_stage[stage]
        steps.append(
            {
                "stage": stage,
                "calls": len(sc),
                "truncations": sum(1 for x in sc if x.truncated),
                "errors": sum(1 for x in sc if x.error),
                **_totals(sc),
            }
        )
    for stage in sorted(by_stage.keys() - seen):
        sc = by_stage[stage]
        steps.append(
            {
                "stage": stage,
                "calls": len(sc),
                "truncations": sum(1 for x in sc if x.truncated),
                "errors": sum(1 for x in sc if x.error),
                **_totals(sc),
            }
        )
    return steps


def scene_stage_summary(calls: list[LlmCall]) -> str:
    """Compact per-scene status like 'author ok, QA ok, draft truncated'."""
    by_stage: dict[str, list[LlmCall]] = {}
    for c in calls:
        by_stage.setdefault(c.stage, []).append(c)
    labels: list[str] = []
    for stage in scene_stages(calls):
        short = stage.replace("scene_packet_", "").replace("_", " ")
        sc = by_stage[stage]
        if any(x.error for x in sc):
            labels.append(f"{short} err")
        elif any(x.truncated for x in sc):
            labels.append(f"{short} trunc")
        else:
            labels.append(f"{short} ok")
    return ", ".join(labels)


def sort_calls(calls: list[LlmCall]) -> list[LlmCall]:
    def _key(c: LlmCall) -> tuple[int, Any]:
        meta = call_metadata(c)
        idx = meta.get("call_index")
        return (idx if isinstance(idx, int) else 999999, c.created_at)

    return sorted(calls, key=_key)
