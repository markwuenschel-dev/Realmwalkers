"""Actionable telemetry problem detection for the Desk debugging console."""

from __future__ import annotations

import uuid
from typing import Any

from dominion.shared.models import LlmCall
from dominion.workers.telemetry_agg import PRIME_STAGES, call_metadata, group_calls

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


def _section_truncation_detail(stage_calls: list[LlmCall]) -> list[dict[str, Any]]:
    """Per-(section, model, attempt) truncation counts, so a scene_packet_author truncation names the
    exact section and cap that was cut off instead of a generic 'increase max_tokens'."""
    by_section: dict[tuple[Any, Any, Any, Any], int] = {}
    for c in stage_calls:
        m = call_metadata(c)
        name = m.get("section_name")
        if not name:
            continue
        key = (name, c.model, m.get("section_max_tokens") or m.get("max_tokens"), m.get("section_attempt_kind"))
        by_section[key] = by_section.get(key, 0) + 1
    return [
        {"section": name, "model": model, "max_tokens": mt, "attempt_kind": kind, "count": n}
        for (name, model, mt, kind), n in sorted(by_section.items(), key=lambda kv: kv[1], reverse=True)
    ]


def detect_truncations(calls: list[LlmCall]) -> dict[str, Any] | None:
    truncated = [c for c in calls if c.truncated]
    if not truncated:
        return None
    by_stage = group_calls(truncated, lambda c: c.stage)
    breakdown = []
    for stage, t in by_stage:
        stage_calls = [c for c in truncated if c.stage == stage]
        meta0 = call_metadata(stage_calls[0]) or {}
        stop = meta0.get("stop_reason")
        max_tok = meta0.get("max_tokens")
        entry: dict[str, Any] = {
            "stage": stage,
            "count": t["calls"],
            "scenes": sorted({c.scene_no for c in stage_calls if c.scene_no is not None}),
            "stop_reason": stop,
            "max_tokens": max_tok,
        }
        sections = _section_truncation_detail(stage_calls)
        if sections:
            # Name the exact section/model/cap that truncated, e.g. "reviewer section truncated at 2000
            # on claude-haiku-4-5 (primary)" — actionable where a bare stage+max_tokens was not.
            entry["sections"] = sections
            worst = sections[0]
            entry["recommended_action"] = (
                f"Section '{worst['section']}' truncated at max_tokens={worst['max_tokens']} on "
                f"{worst['model']} ({worst['attempt_kind']}). Raise that section's max_tokens/fallback_floor "
                "or shorten the chapter/scene prefix."
            )
        elif stage.startswith("scene_packet"):
            entry["recommended_action"] = "Increase scene_packet max_tokens or shorten chapter prefix."
        elif stage == "drafter":
            entry["recommended_action"] = "Increase draft max_tokens or reduce scene context."
        else:
            entry["recommended_action"] = "Inspect call detail; increase max_tokens or shorten prompt."
        breakdown.append(entry)
    return _problem(
        # kind kept as "truncation" (the frontend ProblemsPanel switches on it); the breakdown now carries
        # the precise section/model/max_tokens detail that makes an output truncation actionable.
        kind="truncation",
        severity="warn",
        summary=f"{len(truncated)} truncated call{'s' if len(truncated) != 1 else ''}",
        count=len(truncated),
        breakdown=breakdown,
        recommended_action=(
            "Open truncated calls by stage; raise the failing section/stage max_tokens or shorten its prompt."
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


def _budget_entry(c: LlmCall) -> dict[str, Any]:
    m = call_metadata(c)
    used = m.get("budget_used_after_charge")
    soft = m.get("budget_soft_limit")
    hard = m.get("budget_hard_limit")
    over_soft = used - soft if isinstance(used, int) and isinstance(soft, int) else None
    return {
        "stage": c.stage,
        "scene_no": c.scene_no,
        "used": used,
        "soft_limit": soft,
        "hard_limit": hard,
        "over_soft_by": over_soft,
    }


def detect_budget_overages(calls: list[LlmCall]) -> list[dict[str, Any]]:
    """Surface work-budget pressure from the per-call budget metadata. A HARD overage blocked output and
    is an error; a SOFT-only overage produced valid output just past the target (the `60043 > 60000` case)
    and is informational — naming it explicitly stops it from reading as a hard failure."""
    problems: list[dict[str, Any]] = []
    hard = [c for c in calls if call_metadata(c).get("budget_hard_exceeded")]
    soft_only = [
        c
        for c in calls
        if call_metadata(c).get("budget_soft_exceeded") and not call_metadata(c).get("budget_hard_exceeded")
    ]
    if hard:
        problems.append(
            _problem(
                kind="hard_work_budget_exceeded",
                severity="error",
                summary=f"{len(hard)} call(s) crossed the hard work budget (output blocked)",
                count=len(hard),
                breakdown=[_budget_entry(c) for c in hard[:10]],
                recommended_action=(
                    "A call exceeded its HARD work ceiling and failed closed. Raise the stage's hard budget "
                    "(scene_token_hard_budget / prefix-prime / manual-QA hard) or reduce per-scene work."
                ),
                drill_down={"errors": True},
            )
        )
    if soft_only:
        problems.append(
            _problem(
                kind="soft_work_budget_exceeded",
                severity="info",
                summary=f"{len(soft_only)} call(s) over the soft work budget but under the hard ceiling",
                count=len(soft_only),
                breakdown=[_budget_entry(c) for c in soft_only[:10]],
                recommended_action=(
                    "Informational: these calls produced valid output just over the soft target and were "
                    "persisted with a warning. Raise the soft budget only if the warnings are noisy."
                ),
                drill_down={"stage": "scene_packet_author"},
            )
        )
    return problems


def detect_token_count_fallbacks(calls: list[LlmCall]) -> dict[str, Any] | None:
    """Flag context-window preflights that fell back to the local estimate because Anthropic token
    counting was unavailable — the window was then gated by an approximation, not the real count."""
    fb = [c for c in calls if call_metadata(c).get("token_count_method") == "local_estimate"]
    if not fb:
        return None
    return _problem(
        kind="token_count_fallback",
        severity="warn",
        summary=f"{len(fb)} call(s) fell back to a local token estimate for the context-window preflight",
        count=len(fb),
        breakdown=[
            {"stage": c.stage, "scene_no": c.scene_no, "error": call_metadata(c).get("token_count_error")}
            for c in fb[:10]
        ],
        recommended_action=(
            "Anthropic count_tokens failed, so the local estimate gated the context window. Check API "
            "connectivity / SDK version; set llm_token_counting_fail_closed to block instead of estimate."
        ),
        drill_down={"errors": True},
    )


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
    problems.extend(detect_budget_overages(calls))
    tcf = detect_token_count_fallbacks(calls)
    if tcf:
        problems.append(tcf)
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
