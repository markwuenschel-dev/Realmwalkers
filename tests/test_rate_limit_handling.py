"""Recovery L7: a provider 429 is retryable PROVIDER state on every path — never an author,
QA, or contract failure. Pinned vocabulary: issue/problem kind `infra_rate_limit`, production-run
stage `provider_rate_limited`, scene-packet status `rate_limited`.

Deterministic: no network (provider errors are hand-built), no Postgres (classification is tested
at the pure-function / stub-session level). The llm-layer retry mechanics themselves are covered in
tests/test_llm_rate_limit.py; this file covers the CLASSIFICATION layer above it."""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from fastapi import HTTPException

from dominion.shared.config import settings
from dominion.shared.enums import ScenePacketStatus
from dominion.shared.models import ChapterPacket, ScenePacket
from dominion.workers import llm, llm_escalation, telemetry, telemetry_diagnostics, worker
from dominion.workers.budget import TokenBudget, Usage
from dominion.workers.llm import LlmRateLimited
from dominion.workers.scene_packet import approval_policy, derive
from dominion.workers.scene_packet import author as author_mod
from dominion.workers.scene_packet import author_sections as author_sections_mod
from dominion.workers.scene_packet import qa as qa_mod
from dominion.workers.specialists.base import PassError


def _resp_429(headers: dict[str, str] | None = None) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    response = httpx.Response(429, headers=headers or {}, request=request, text="rate limited")
    return httpx.HTTPStatusError("429 from provider: TPM exceeded", request=request, response=response)


_VALID_BODY: dict[str, Any] = {
    "scene_no": 1,
    "word_budget": {"target": 900},
    "known_before_scene": {"reader": [], "pov": [], "omniscient_author": []},
    "learned_during_scene": {"reader_must_learn": [], "reader_may_learn": [], "reader_may_infer_only": []},
    "must_remain_hidden": {"reader": [], "pov": [], "all_surface_prose": []},
}


# ---------------------------------------------------------------- vocabulary pins


def test_pinned_vocabulary_strings():
    assert worker.INFRA_RATE_LIMIT == "infra_rate_limit"
    assert worker.PROVIDER_RATE_LIMITED_STAGE == "provider_rate_limited"
    assert ScenePacketStatus.RATE_LIMITED == "rate_limited"


# ---------------------------------------------------------------- chain classification


def test_find_rate_limit_finds_direct_and_wrapped():
    direct = LlmRateLimited("provider rate limit (429) persisted", retry_after_s=3.0, attempts=4)
    assert llm.find_rate_limit(direct) is direct

    # Wrapped explicitly (`raise ... from`): an enrichment PassError around a 429.
    try:
        try:
            raise direct
        except LlmRateLimited as inner:
            raise PassError("combat enrichment pass failed: provider said no") from inner
    except PassError as wrapped_exc:
        wrapped = wrapped_exc
    assert llm.find_rate_limit(wrapped) is direct

    # Wrapped implicitly (__context__, no `from`).
    try:
        try:
            raise LlmRateLimited("429 again", attempts=2)
        except LlmRateLimited:
            raise RuntimeError("secondary crash while handling the 429")  # noqa: B904 — implicit chain on purpose
    except RuntimeError as implicit_exc:
        implicit = implicit_exc
    found = llm.find_rate_limit(implicit)
    assert isinstance(found, LlmRateLimited) and found.attempts == 2

    assert llm.find_rate_limit(ValueError("not a rate limit")) is None
    assert llm.find_rate_limit(None) is None


def test_classify_job_failure_rate_limit_is_prefixed_and_kinded():
    rl = LlmRateLimited("provider rate limit (429) persisted after 3 automatic retries: TPM", attempts=4)
    last_error, kind = worker.classify_job_failure(rl, " @ llm.py:333")
    assert last_error.startswith("LlmRateLimited: ")
    assert "429" in last_error and last_error.endswith("@ llm.py:333")
    assert kind == worker.INFRA_RATE_LIMIT


def test_classify_job_failure_wrapped_rate_limit_keeps_the_prefix():
    rl = LlmRateLimited("provider rate limit (429)", attempts=3)
    try:
        raise RuntimeError("scene author crashed mid-fan-out") from rl
    except RuntimeError as wrapped_exc:
        wrapped = wrapped_exc
    last_error, kind = worker.classify_job_failure(wrapped, " @ pipeline.py:73")
    assert last_error.startswith("LlmRateLimited: ")
    assert "RuntimeError" in last_error  # the wrapper is still named for diagnostics
    assert kind == worker.INFRA_RATE_LIMIT


def test_classify_job_failure_ordinary_error_is_unchanged():
    last_error, kind = worker.classify_job_failure(ValueError("boom"), " @ x.py:1")
    assert last_error == "ValueError: boom @ x.py:1"
    assert kind is None


# ---------------------------------------------------------------- diagnostics problem kinds


def test_rate_limited_jobs_report_as_infra_rate_limit_not_failed_draft_job():
    jid_rl, jid_real = uuid.uuid4(), uuid.uuid4()
    failed: list[tuple[uuid.UUID, int | None, int | None, str | None]] = [
        (jid_rl, 1, 2, "LlmRateLimited: provider rate limit (429) persisted after 3 automatic retries @ llm.py:333"),
        (jid_real, 1, 3, "ValueError: scene packet body invalid @ derive.py:100"),
    ]

    rl = telemetry_diagnostics.detect_rate_limited_jobs(failed)
    assert rl is not None
    assert rl["kind"] == "infra_rate_limit"
    assert rl["severity"] == "warn"  # retryable — not an error demanding a root-cause fix
    assert rl["count"] == 1
    assert rl["breakdown"][0]["job_id"] == str(jid_rl)
    assert "requeue" in rl["recommended_action"].lower() or "retry" in rl["recommended_action"].lower()

    fj = telemetry_diagnostics.detect_failed_jobs(failed)
    assert fj is not None
    assert fj["count"] == 1
    assert fj["breakdown"][0]["job_id"] == str(jid_real)

    kinds = [p["kind"] for p in telemetry_diagnostics.build_problems([], failed)]
    assert "infra_rate_limit" in kinds and "failed_draft_job" in kinds


def test_all_rate_limited_failures_leave_no_failed_draft_job_problem():
    failed: list[tuple[uuid.UUID, int | None, int | None, str | None]] = [
        (uuid.uuid4(), 1, 1, "LlmRateLimited: provider rate limit (429) @ llm.py:333")
    ]
    assert telemetry_diagnostics.detect_failed_jobs(failed) is None
    rl = telemetry_diagnostics.detect_rate_limited_jobs(failed)
    assert rl is not None and rl["count"] == 1


# ---------------------------------------------------------------- backoff honors Retry-After


async def test_backoff_floors_at_retry_after_hint(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "llm_max_retries", 1)
    monkeypatch.setattr(settings, "llm_retry_base_delay_s", 0.001)
    monkeypatch.setattr(settings, "llm_retry_max_delay_s", 30.0)

    sleeps: list[float] = []

    async def _no_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(llm.asyncio, "sleep", _no_sleep)

    attempts = 0

    async def _429_then_ok() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise _resp_429({"retry-after": "1.5"})
        return "ok"

    assert await llm._call_with_retries(_429_then_ok, what="create", is_transient=llm._is_transient_http) == "ok"
    assert len(sleeps) == 1 and sleeps[0] >= 1.5


async def test_backoff_caps_a_huge_retry_after_at_max_delay(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "llm_max_retries", 1)
    monkeypatch.setattr(settings, "llm_retry_base_delay_s", 0.001)
    monkeypatch.setattr(settings, "llm_retry_max_delay_s", 2.0)

    sleeps: list[float] = []

    async def _no_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(llm.asyncio, "sleep", _no_sleep)

    attempts = 0

    async def _429_then_ok() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise _resp_429({"retry-after": "60"})
        return "ok"

    assert await llm._call_with_retries(_429_then_ok, what="create", is_transient=llm._is_transient_http) == "ok"
    assert sleeps == [2.0]  # provider asked for 60s; the worker's ceiling wins


# ---------------------------------------------------------------- escalation


async def test_semantic_escalation_fallback_429_keeps_usable_primary(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(llm_escalation, "resolve_fallback_model", lambda key: "fallback-model")
    usage = Usage(input_tokens=10, output_tokens=10)

    async def attempt(model: str, max_tokens: int) -> tuple[Any, Usage]:
        if model == "primary-model":
            return {"verdict": "revise_required"}, usage
        raise LlmRateLimited("provider rate limit (429) on the fallback", attempts=4)

    value, model_used, escalated = await llm_escalation.attempt_with_escalation(
        setting_key="scene_packet_qa_model",
        primary_model="primary-model",
        primary_max_tokens=1000,
        attempt_fn=attempt,
        is_success=lambda v: isinstance(v, dict),
        semantic_escalate=lambda v: True,  # force the fallback attempt despite a usable primary
    )
    # The usable primary result survives a rate-limited fallback — provider state must not
    # masquerade as a failed/blocked author or QA verdict.
    assert value == {"verdict": "revise_required"}
    assert model_used == "primary-model"
    assert escalated is False


async def test_structural_failure_fallback_429_propagates_as_rate_limit(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(llm_escalation, "resolve_fallback_model", lambda key: "fallback-model")
    usage = Usage(input_tokens=10, output_tokens=10)

    async def attempt(model: str, max_tokens: int) -> tuple[Any, Usage]:
        if model == "primary-model":
            return None, usage  # unusable primary → structural escalation
        raise LlmRateLimited("provider rate limit (429) on the fallback", attempts=4)

    with pytest.raises(LlmRateLimited):
        await llm_escalation.attempt_with_escalation(
            setting_key="scene_packet_qa_model",
            primary_model="primary-model",
            primary_max_tokens=1000,
            attempt_fn=attempt,
            is_success=lambda v: isinstance(v, dict),
        )


# ---------------------------------------------------------------- scene-packet derive


def _scene_work() -> derive._SceneWork:
    return derive._SceneWork(
        seed={"seed_id": str(uuid.uuid4()), "summary": "Marcus reviews the readiness data."},
        seed_id=uuid.uuid4(),
        scene_no=1,
        word_budget={"target": 900},
        src_hash="test-hash",
        row=None,
        owner_snippets=[],
        canon_snippets=[],
        sources=[],
        budget=TokenBudget(max_tokens=10_000),
        pov="Marcus",
        pov_summary=None,
    )


async def _run_author_then_qa(item: derive._SceneWork):
    return await derive._author_then_qa(
        item,
        chapter_packet_body={},
        chapter_open_questions=None,
        pov="Marcus",
        pov_summary=None,
        omniscient_summary=None,
        sink=telemetry.TelemetrySink(),
        book_id=str(uuid.uuid4()),
        chapter_id=str(uuid.uuid4()),
    )


async def test_derive_author_429_lands_rate_limited_never_author_blocked(monkeypatch: pytest.MonkeyPatch):
    async def _rate_limited_author(**kwargs: Any) -> dict[str, Any]:
        raise LlmRateLimited("provider rate limit (429) persisted after 3 automatic retries: TPM", attempts=4)

    monkeypatch.setattr(author_mod, "author_scene_packet", _rate_limited_author)
    monkeypatch.setattr(author_sections_mod, "author_scene_packet_sectioned", _rate_limited_author)

    scene_body, qa, error_detail, violations, blocker_source = await _run_author_then_qa(_scene_work())
    assert scene_body is None and qa is None
    assert blocker_source == "rate_limit"  # NOT "author"
    assert "Rate limited by provider" in (error_detail or "")

    status, reason = approval_policy.status_after_author_qa(scene_body, qa, error_detail, blocker_source=blocker_source)
    assert status == ScenePacketStatus.RATE_LIMITED  # NOT blocked
    assert "retry" in (reason or "").lower()


async def test_derive_qa_429_preserves_valid_body_and_lands_rate_limited(monkeypatch: pytest.MonkeyPatch):
    async def _valid_author(**kwargs: Any) -> dict[str, Any]:
        return dict(_VALID_BODY)

    async def _rate_limited_qa(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise LlmRateLimited("provider rate limit (429) during QA", attempts=4)

    monkeypatch.setattr(author_mod, "author_scene_packet", _valid_author)
    monkeypatch.setattr(author_sections_mod, "author_scene_packet_sectioned", _valid_author)
    monkeypatch.setattr(qa_mod, "qa_scene_packet", _rate_limited_qa)

    scene_body, qa, error_detail, violations, blocker_source = await _run_author_then_qa(_scene_work())
    assert isinstance(scene_body, dict)  # the valid contract body is KEPT
    assert qa is None
    assert blocker_source == "rate_limit"  # NOT "qa"

    status, reason = approval_policy.status_after_author_qa(scene_body, qa, error_detail, blocker_source=blocker_source)
    assert status == ScenePacketStatus.RATE_LIMITED
    assert "re-run QA" in (reason or "")


def test_qa_rerun_releases_a_rate_limited_hold():
    """A RATE_LIMITED packet whose QA later succeeds becomes an ordinary proposed packet again."""
    row = ScenePacket(
        id=uuid.uuid4(),
        book_id=uuid.uuid4(),
        chapter_id=uuid.uuid4(),
        chapter_packet_id=uuid.uuid4(),
        scene_no=1,
        status=ScenePacketStatus.RATE_LIMITED,
        body=dict(_VALID_BODY),
    )
    approval_policy.apply_qa_rerun(row, {"verdict": "approve", "residual_risks": [], "issues": []})
    assert row.status == ScenePacketStatus.PROPOSED


# ---------------------------------------------------------------- manual QA rerun endpoint


class _StubSession:
    """Just enough AsyncSession for the QA-rerun route: get() and commit()."""

    def __init__(self, row: ScenePacket, chapter_packet: ChapterPacket | None = None) -> None:
        self._row = row
        self._cp = chapter_packet
        self.committed = False

    async def get(self, model: type, pk: Any) -> Any:
        if model is ScenePacket:
            return self._row
        if model is ChapterPacket:
            return self._cp
        return None

    async def commit(self) -> None:
        self.committed = True


async def test_manual_qa_rerun_429_returns_http_429_and_never_touches_the_packet(
    monkeypatch: pytest.MonkeyPatch,
):
    from dominion.api.routers import scene_packets as sp_router

    row = ScenePacket(
        id=uuid.uuid4(),
        book_id=uuid.uuid4(),
        chapter_id=uuid.uuid4(),
        chapter_packet_id=uuid.uuid4(),
        scene_no=1,
        status=ScenePacketStatus.PROPOSED,
        qa_verdict="approve",
        body=dict(_VALID_BODY),
    )

    async def _rate_limited_qa(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise LlmRateLimited("provider rate limit (429) during manual QA", retry_after_s=9.0, attempts=4)

    monkeypatch.setattr(sp_router.qa_mod, "qa_scene_packet", _rate_limited_qa)
    session = _StubSession(row)

    with pytest.raises(HTTPException) as exc_info:
        await sp_router.qa_scene_packet(row.id, session)  # type: ignore[arg-type]

    assert exc_info.value.status_code == 429  # surfaced as provider state, not a 500/blocked packet
    assert row.status == ScenePacketStatus.PROPOSED  # untouched
    assert row.qa_verdict == "approve"  # untouched
    assert session.committed is False  # nothing persisted
