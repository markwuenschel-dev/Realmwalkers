"""The /style-review route — its telemetry contract, not the audit's judgement.

`tests/test_style_audit.py` covers what the audit decides. This file covers what the REQUEST leaves
behind, which the audit deliberately does not own: one run row per invocation, persisted and
committed even when the provider fails, and never allowed to convert a bookkeeping error into a
failed audit the author has already paid for.

The assertions are on `llm_calls` ROWS, not on the sink. A sink that collected records and was never
committed looks identical to a successful one from inside the process — which is exactly the failure
mode this endpoint had to be written around.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from dominion.shared.models import LlmCall, StyleDocument
from dominion.workers import telemetry
from dominion.workers.reviewers import style_audit

PROSE = "Marcus set the cup down without drinking. He had been on the other side of it before."

FINDING = (
    '[{"rule": "R2", "rule_source": "prose_clarity_rules", "severity": "warn", '
    '"quote": "Marcus set the cup down without drinking.", "why": "Refers to an unstaged event."}]'
)


async def _seed_standards(db_factory) -> None:
    """One real style document, so the audit has something to judge against and returns 200."""
    async with db_factory() as s:
        s.add(StyleDocument(slug="style/prose_clarity_rules", content="R2 — Never refer to an event."))
        await s.commit()


def _fake_call(monkeypatch, *, raw: str | None = None, boom: Exception | None = None) -> None:
    """Stand in for the provider, recording to the ambient sink exactly as `llm.py` does — including
    on failure, which is the path that matters here."""

    async def fake_complete(**kwargs):
        telemetry.record(
            model=kwargs.get("model", "test-model"),
            input_tokens=1200,
            output_tokens=64,
            cache_creation_tokens=0,
            cache_read_tokens=0,
            truncated=False,
            latency_ms=12,
            error=f"{type(boom).__name__}: {boom}" if boom else None,
        )
        if boom:
            raise boom
        return raw or "[]", {}

    monkeypatch.setattr(style_audit, "complete_with_rate_limit_fallback", fake_complete)


@pytest.mark.asyncio
async def test_successful_audit_persists_one_attributable_row(app_client, db_factory, monkeypatch):
    await _seed_standards(db_factory)
    _fake_call(monkeypatch, raw=FINDING)

    resp = await app_client.post("/style-review", json={"prose": PROSE, "pov": "Marcus"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["telemetry_recorded"] is True
    assert len(body["suggestions"]) == 1

    async with db_factory() as s:
        rows = list((await s.execute(select(LlmCall).where(LlmCall.stage == "style_audit"))).scalars())
    assert len(rows) == 1
    row = rows[0]
    # `stage` is the whole attribution mechanism: agent_ops.build_agent_stats maps it through
    # STAGE_TO_SETTING to the style_audit_model agent. A row with any other stage is invisible there.
    assert row.stage == "style_audit"
    assert row.input_tokens == 1200
    # A book-less run is the point: the Edit desk audits prose pasted outside any chapter, and
    # inventing a book association would corrupt per-book attribution.
    assert row.book_id is None
    assert row.run_id is not None


@pytest.mark.asyncio
async def test_the_persisted_row_surfaces_under_the_style_audit_agent(app_client, db_factory, monkeypatch):
    """End of the attribution chain, asserted through the real reader rather than inferred from the
    stage string: `build_agent_stats` is what Agent Operations renders."""
    await _seed_standards(db_factory)
    _fake_call(monkeypatch, raw=FINDING)
    assert (await app_client.post("/style-review", json={"prose": PROSE})).status_code == 200

    from dominion.shared.agent_ops import build_agent_stats

    async with db_factory() as s:
        stats = await build_agent_stats(s)
    row = next(a for a in stats.agents if a.setting == "style_audit_model")
    assert row.label == "Style audit"
    assert row.calls == 1


@pytest.mark.asyncio
async def test_provider_failure_still_persists_its_telemetry(app_client, db_factory, monkeypatch):
    """A failed call is a billable call. Dropping its row would leave exactly the runs worth
    investigating as the ones with no record."""
    await _seed_standards(db_factory)
    _fake_call(monkeypatch, boom=RuntimeError("provider exploded"))

    with pytest.raises(RuntimeError, match="provider exploded"):
        await app_client.post("/style-review", json={"prose": PROSE})

    async with db_factory() as s:
        rows = list((await s.execute(select(LlmCall).where(LlmCall.stage == "style_audit"))).scalars())
    assert len(rows) == 1
    assert rows[0].error is not None
    assert "provider exploded" in rows[0].error


@pytest.mark.asyncio
async def test_telemetry_failure_does_not_discard_the_suggestions(app_client, db_factory, monkeypatch):
    """The money is already spent. Failing the request would throw away real output AND invite an
    expensive duplicate retry, so the audit returns and the fault is reported in the payload."""
    await _seed_standards(db_factory)
    _fake_call(monkeypatch, raw=FINDING)

    from dominion.api.routers import style_review as route

    def explode(*_a, **_k):
        raise RuntimeError("llm_calls insert failed")

    monkeypatch.setattr(route.telemetry_db, "persist_sink", explode)

    resp = await app_client.post("/style-review", json={"prose": PROSE})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["telemetry_recorded"] is False
    assert len(body["suggestions"]) == 1

    async with db_factory() as s:
        rows = list((await s.execute(select(LlmCall).where(LlmCall.stage == "style_audit"))).scalars())
    assert rows == []


@pytest.mark.asyncio
async def test_each_audit_is_its_own_run(app_client, db_factory, monkeypatch):
    """Agent Operations keeps only the most recent distinct run ids, so a shared bucket would make
    older audits vanish as a group rather than age out one at a time."""
    await _seed_standards(db_factory)
    _fake_call(monkeypatch, raw="[]")

    for _ in range(2):
        assert (await app_client.post("/style-review", json={"prose": PROSE})).status_code == 200

    async with db_factory() as s:
        rows = list((await s.execute(select(LlmCall).where(LlmCall.stage == "style_audit"))).scalars())
    assert len({r.run_id for r in rows}) == 2


@pytest.mark.asyncio
async def test_no_standards_returns_503_and_records_nothing(app_client, db_factory, monkeypatch):
    """With nothing to judge against, an empty suggestion list would read as "your prose is clean".
    No model call is made, so there is also no cost to record."""

    async def explode(**_k):  # pragma: no cover - must not run
        raise AssertionError("called the model with no standards loaded")

    monkeypatch.setattr(style_audit, "complete_with_rate_limit_fallback", explode)

    # Patching the loader covers the disk fallback too: `load_style_document` is the single seam
    # through which every standard arrives, DB first and disk second.
    async def no_docs(_session, _path):
        return None

    monkeypatch.setattr(style_audit, "load_style_document", no_docs)

    resp = await app_client.post("/style-review", json={"prose": PROSE})
    assert resp.status_code == 503
    assert "push_style" in resp.json()["detail"]

    async with db_factory() as s:
        rows = list((await s.execute(select(LlmCall).where(LlmCall.stage == "style_audit"))).scalars())
    assert rows == []
