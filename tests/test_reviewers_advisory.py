"""Advisory reviewer unit tests — mock the LLM (DESIGN §6). These reviewers never block, never emit
HARD, and never raise on malformed output; they no-op (no LLM call) when their inputs are absent."""
from __future__ import annotations

import uuid

from dominion.shared.enums import Severity
from dominion.workers import llm
from dominion.workers.budget import TokenBudget, Usage
from dominion.workers.context import SceneContext
from dominion.workers.reviewers.continuity import continuity_reviewer
from dominion.workers.reviewers.pacing import pacing_reviewer
from dominion.workers.reviewers.state_drift import state_drift_reviewer
from dominion.workers.reviewers.voice import voice_reviewer


def _ctx(**overrides: object) -> SceneContext:
    base: dict[str, object] = dict(
        book_id=uuid.uuid4(), chapter_id=uuid.uuid4(), pov="Soren", scene_no=1,
        tags=[], characters_present=["Soren"], beat_text="Soren tests his eyes.",
        expected_state_changes=None, knowledge_injections=[], voice_spec=None,
        budget=TokenBudget(max_tokens=40_000),
    )
    base.update(overrides)
    return SceneContext(**base)  # type: ignore[arg-type]


def _mock(monkeypatch, response: str) -> dict[str, int]:
    calls = {"n": 0}

    async def fake_complete(**kwargs):
        calls["n"] += 1
        kwargs["budget"].charge(Usage(5, 5))
        return response, Usage(5, 5)

    monkeypatch.setattr(llm, "complete", fake_complete)
    return calls


def _no_call(monkeypatch) -> dict[str, int]:
    return _mock(monkeypatch, "[]")


# --- voice ----------------------------------------------------------------------------------------

async def test_voice_flags_drift_as_advisory(monkeypatch):
    _mock(monkeypatch, '[{"severity": "warn", "note": "too florid for this voice", "quote": "gilded dawn"}]')
    flags = await voice_reviewer.review("The gilded dawn wept.", _ctx(voice_spec="Terse, wry."))
    assert len(flags) == 1
    assert flags[0].reviewer == "voice" and flags[0].severity == Severity.WARN
    assert flags[0].payload == {"quote": "gilded dawn"}


async def test_voice_noops_without_spec(monkeypatch):
    calls = _no_call(monkeypatch)
    assert await voice_reviewer.review("Some prose.", _ctx(voice_spec=None)) == []
    assert calls["n"] == 0            # no spec -> no LLM call


async def test_voice_tolerates_garbage(monkeypatch):
    _mock(monkeypatch, "not json at all")
    assert await voice_reviewer.review("Prose.", _ctx(voice_spec="Terse.")) == []


# --- pacing ---------------------------------------------------------------------------------------

async def test_pacing_flags_on_long_prose(monkeypatch):
    _mock(monkeypatch, '[{"severity": "info", "note": "the middle drags"}]')
    flags = await pacing_reviewer.review("word " * 400, _ctx())   # > _MIN_PROSE_CHARS
    assert len(flags) == 1 and flags[0].reviewer == "pacing" and flags[0].severity == Severity.INFO


async def test_pacing_noops_on_short_prose(monkeypatch):
    calls = _no_call(monkeypatch)
    assert await pacing_reviewer.review("Too short to pace.", _ctx()) == []
    assert calls["n"] == 0            # below the floor -> no LLM call


async def test_pacing_never_hard(monkeypatch):
    _mock(monkeypatch, '[{"severity": "hard", "note": "trying to escalate"}]')
    flags = await pacing_reviewer.review("word " * 400, _ctx())
    assert flags and all(f.severity != Severity.HARD for f in flags)   # clamped to advisory


# --- state drift ----------------------------------------------------------------------------------

async def test_state_drift_flags_undeclared_change(monkeypatch):
    _mock(monkeypatch, '[{"character": "Soren", "change": "gains a sword", '
                       '"note": "prose shows him pocketing a blade", "severity": "warn"}]')
    flags = await state_drift_reviewer.review(
        "Soren slid the blade into his belt.",
        _ctx(expected_state_changes={"Soren": {"level": "+1"}}),
    )
    assert len(flags) == 1 and flags[0].reviewer == "state_drift"
    assert flags[0].severity in (Severity.INFO, Severity.WARN)
    assert flags[0].payload == {"character": "Soren", "change": "gains a sword"}


async def test_state_drift_noops_without_declared_changes(monkeypatch):
    calls = _no_call(monkeypatch)
    assert await state_drift_reviewer.review("Prose.", _ctx(expected_state_changes=None)) == []
    assert calls["n"] == 0


async def test_state_drift_tolerates_garbage(monkeypatch):
    _mock(monkeypatch, "```\noops\n```")
    flags = await state_drift_reviewer.review("Prose.", _ctx(expected_state_changes={"Soren": {"hp": 1}}))
    assert flags == []


# --- continuity POV-knowledge (additive; hard-number path unaffected) ------------------------------

async def test_continuity_knowledge_flags_are_advisory(monkeypatch):
    _mock(monkeypatch, '[{"reference": "the queen\'s death", "note": "Soren never learned this"}]')
    # ledger empty -> hard-number path no-ops; pov_summary present -> knowledge path runs.
    flags = await continuity_reviewer.review(
        "He thought of the queen's death.",
        _ctx(pov_summary="Soren has woken in the Realm and met no royalty."),
    )
    assert len(flags) == 1
    assert flags[0].reviewer == "continuity" and flags[0].severity == Severity.WARN
    assert flags[0].payload == {"kind": "knowledge", "reference": "the queen's death"}
    assert all(f.severity != Severity.HARD for f in flags)   # knowledge findings are never HARD


async def test_continuity_knowledge_noops_without_pov_summary(monkeypatch):
    calls = _no_call(monkeypatch)
    assert await continuity_reviewer.review("Prose.", _ctx(pov_summary=None)) == []
    assert calls["n"] == 0            # no ledger + no pov_summary -> no LLM call at all
