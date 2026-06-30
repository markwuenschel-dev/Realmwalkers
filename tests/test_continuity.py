"""Continuity reviewer unit tests — the deterministic compare, with the extraction LLM mocked."""

from __future__ import annotations

import uuid

from dominion.shared.enums import Severity
from dominion.workers import llm
from dominion.workers.budget import TokenBudget, Usage
from dominion.workers.context import SceneContext
from dominion.workers.reviewers.continuity import continuity_reviewer


def _ctx(ledger: dict[str, dict[str, object]]) -> SceneContext:
    return SceneContext(
        book_id=uuid.uuid4(),
        chapter_id=uuid.uuid4(),
        pov="Marcus",
        scene_no=1,
        tags=[],
        characters_present=["Marcus"],
        beat_text="x",
        expected_state_changes=None,
        knowledge_injections=[],
        voice_spec=None,
        budget=TokenBudget(max_tokens=40_000),
        ledger=ledger,
    )


async def test_empty_ledger_skips_extraction_entirely(monkeypatch):
    called = False

    async def fake_complete(**kwargs):
        nonlocal called
        called = True
        return "[]", Usage(1, 1)

    monkeypatch.setattr(llm, "complete", fake_complete)
    flags = await continuity_reviewer.review("any prose", _ctx({}))
    assert flags == []
    assert called is False  # nothing canonical to protect -> no LLM call, no tokens spent


async def test_flags_numeric_contradiction_as_hard(monkeypatch):
    async def fake_complete(**kwargs):
        return (
            '[{"character":"Marcus","attribute":"level","value":"7",'
            '"context_sentence":"His interface blinked LEVEL 7."}]'
        ), Usage(10, 10)

    monkeypatch.setattr(llm, "complete", fake_complete)
    flags = await continuity_reviewer.review("...", _ctx({"Marcus": {"level": 5}}))

    assert len(flags) == 1
    flag = flags[0]
    assert flag.reviewer == "continuity"
    assert flag.severity == Severity.HARD
    assert flag.payload is not None
    assert flag.payload["prose_value"] == "7"
    assert flag.payload["ledger_value"] == "5"
    assert flag.payload["context_sentence"] == "His interface blinked LEVEL 7."
    # the value isn't present verbatim in the scene prose ("...") -> span can't be located
    assert flag.payload["span"] is None


async def test_locates_span_and_derives_context_from_prose(monkeypatch):
    """When the LLM omits context_sentence, the reviewer derives it (sentence-scoped) and pins the
    value's char offsets in the prose — both deterministic, no LLM guessing."""
    prose = "The corridor was cold. His interface blinked LEVEL 7 in the dark."

    async def fake_complete(**kwargs):
        return '[{"character":"Marcus","attribute":"level","value":"7"}]', Usage(10, 10)

    monkeypatch.setattr(llm, "complete", fake_complete)
    flags = await continuity_reviewer.review(prose, _ctx({"Marcus": {"level": 5}}))

    payload = flags[0].payload
    assert payload is not None
    start = prose.index("7")
    assert payload["span"] == [start, start + 1]
    assert payload["context_sentence"] == "His interface blinked LEVEL 7 in the dark."


async def test_consistent_value_produces_no_flag(monkeypatch):
    async def fake_complete(**kwargs):
        return '[{"character":"Marcus","attribute":"level","value":"5","context_sentence":"."}]', Usage(10, 10)

    monkeypatch.setattr(llm, "complete", fake_complete)
    flags = await continuity_reviewer.review("...", _ctx({"Marcus": {"level": 5}}))
    assert flags == []


async def test_malformed_extraction_is_swallowed(monkeypatch):
    async def fake_complete(**kwargs):
        return "sorry, I can't do that", Usage(5, 5)  # not JSON

    monkeypatch.setattr(llm, "complete", fake_complete)
    flags = await continuity_reviewer.review("...", _ctx({"Marcus": {"level": 5}}))
    assert flags == []  # advisory: a bad extraction never crashes review
