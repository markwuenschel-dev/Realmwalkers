"""Actionable telemetry problem detection for the Desk debugging console."""

from __future__ import annotations

import uuid
from typing import Any

from dominion.shared.models import LlmCall
from dominion.workers.telemetry_agg import PRIME_STAGES, group_calls

# Prime calls below this input token count likely did not write a meaningful cache breakpoint.
_PRIME_MIN_INPUT_TOKENS = 1024
_HIGH_LATENCY_MS = 30_000


def _problem(
    *,
    kind: str,
    severity: str,
    summary: str,
    count: int,
    breakdown: list[dict[str, Any]],
    recommended_action: str,
    drill_down: dict[str, Any],
) -> dict[str, Any]:
    return {
        "kind": kind,
        "severity": severity,
        "summary": summary,
        "count": count,
        "breakdown": breakdown,
        "recommended_action": recommended_action,
        "drill_down": drill_down,
    }


def detect_truncations(calls: list[LlmCall]) -> dict[str, Any] | None:
    truncated = [c for c in calls if c.truncated]
    if not truncated:
        return None
    by_stage = group_calls(truncated, lambda c: c.stage)
    breakdown = [
        {
            "stage": stage,
            "count": t["calls"],
            "scenes": sorted({c.scene_no for c in truncated if c.stage == stage and c.scene_no is not None}),
        }
        for stage, t in by_stage
    ]
    return _problem(
        kind="truncation",
        severity="warn",
        summary=f"{len(truncated)} truncated call{'s' if len(truncated) != 1 else ''}",
        count=len(truncated),
        breakdown=breakdown,
        recommended_action=(
            "Open truncated calls by stage; increase max_tokens or shorten prompt for the failing stage."
        ),
        drill_down={"truncated": True},
    )


def detect_failed_jobs(
    failed_jobs: list[tuple[uuid.UUID, int | None, int | None, str | None]],
) -> dict[str, Any] | None:
    if not failed_jobs:
        return None
    breakdown = [
        {
            "job_id": str(jid),
            "chapter_no": ch,
            "scene_no": sc,
            "error": (err or "")[:200],
        }
        for jid, ch, sc, err in failed_jobs
    ]
    missing_sp = sum(1 for _, _, _, err in failed_jobs if err and "scene_packet" in err.lower())
    action = (
        "Run draft readiness check before retrying; use contract-first requeue, not blind retry-failed."
        if missing_sp
        else "Inspect failed job errors; fix root cause before requeue."
    )
    return _problem(
        kind="failed_draft_job",
        severity="error",
        summary=f"{len(failed_jobs)} failed draft job{'s' if len(failed_jobs) != 1 else ''}",
        count=len(failed_jobs),
        breakdown=breakdown,
        recommended_action=action,
        drill_down={"errors": True},
    )


def detect_cache_issues(calls: list[LlmCall]) -> list[dict[str, Any]]:
    problems: list[dict[str, Any]] = []
    by_run: dict[uuid.UUID | None, list[LlmCall]] = {}
    for c in calls:
        by_run.setdefault(c.run_id, []).append(c)

    short_primes: list[dict[str, Any]] = []
    for c in calls:
        if c.stage in PRIME_STAGES and c.input_tokens < _PRIME_MIN_INPUT_TOKENS:
            short_primes.append(
                {"stage": c.stage, "input_tokens": c.input_tokens, "run_id": str(c.run_id) if c.run_id else None}
            )
    if short_primes:
        problems.append(
            _problem(
                kind="cache_prime_short",
                severity="warn",
                summary=(
                    f"{len(short_primes)} prefix prime call{'s' if len(short_primes) != 1 else ''} "
                    "with unusually low input"
                ),
                count=len(short_primes),
                breakdown=short_primes[:10],
                recommended_action="Expected large shared chapter prefix; verify chapter packet size and prime prompt.",
                drill_down={"stage": "scene_packet_author_prefix_prime"},
            )
        )

    for rid, run_calls in by_run.items():
        if rid is None:
            continue
        primes = [c for c in run_calls if c.stage in PRIME_STAGES]
        authors = [c for c in run_calls if c.stage == "scene_packet_author"]
        if primes and authors:
            had_prime_read = any(c.cache_read_tokens > 0 for c in primes)
            zero_read_authors = [c for c in authors if c.cache_read_tokens == 0 and c.cache_creation_tokens > 5000]
            if had_prime_read and zero_read_authors:
                problems.append(
                    _problem(
                        kind="cache_miss_after_prime",
                        severity="warn",
                        summary=f"{len(zero_read_authors)} author call(s) paid full prefix after prime in run",
                        count=len(zero_read_authors),
                        breakdown=[
                            {"scene_no": c.scene_no, "cache_creation": c.cache_creation_tokens}
                            for c in zero_read_authors[:10]
                        ],
                        recommended_action="Verify prefix prime ran before fanout and cache TTL has not expired.",
                        drill_down={"run_id": str(rid), "stage": "scene_packet_author"},
                    )
                )
    return problems


def detect_high_latency(calls: list[LlmCall]) -> dict[str, Any] | None:
    slow = [c for c in calls if c.latency_ms is not None and c.latency_ms >= _HIGH_LATENCY_MS]
    if not slow:
        return None
    by_stage = group_calls(slow, lambda c: c.stage)
    breakdown = [
        {
            "stage": stage,
            "count": t["calls"],
            "max_latency_ms": max((c.latency_ms or 0 for c in slow if c.stage == stage), default=0),
        }
        for stage, t in by_stage
    ]
    return _problem(
        kind="high_latency",
        severity="info",
        summary=f"{len(slow)} slow call(s) (≥{_HIGH_LATENCY_MS}ms)",
        count=len(slow),
        breakdown=breakdown,
        recommended_action="Inspect worst calls by stage; check model choice and prompt size.",
        drill_down={"min_latency_ms": _HIGH_LATENCY_MS},
    )


def build_problems(
    calls: list[LlmCall],
    failed_jobs: list[tuple[uuid.UUID, int | None, int | None, str | None]],
) -> list[dict[str, Any]]:
    problems: list[dict[str, Any]] = []
    t = detect_truncations(calls)
    if t:
        problems.append(t)
    fj = detect_failed_jobs(failed_jobs)
    if fj:
        problems.append(fj)
    problems.extend(detect_cache_issues(calls))
    hl = detect_high_latency(calls)
    if hl:
        problems.append(hl)
    err_calls = [c for c in calls if c.error]
    if err_calls:
        problems.append(
            _problem(
                kind="error",
                severity="error",
                summary=f"{len(err_calls)} call(s) recorded errors",
                count=len(err_calls),
                breakdown=[
                    {"stage": c.stage, "scene_no": c.scene_no, "error": (c.error or "")[:120]} for c in err_calls[:10]
                ],
                recommended_action="Open error calls for stage/scene context.",
                drill_down={"errors": True},
            )
        )
    return problems
