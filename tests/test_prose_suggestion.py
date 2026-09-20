"""Prose suggestion: the one role that writes prose for a read-through note.

No model is touched — `llm.complete` is faked at the provider seam, as in `test_read_through_routes`.
The style documents are synthetic tmp files, so the real `series/` guide is never read.

The registry guards at the top are deliberately broader than this feature. Adding an agent role means
adding a `FALLBACK_ATTR` entry and three `Settings` fields, and forgetting either COMPILES: the
fallback silently never fires (`llm_escalation.resolve_fallback_model` returns ""), and the Agent Ops
row shows "no fallback" however the author sets it. Nothing caught that before.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from dominion.api.routers import read_throughs as router_mod
from dominion.shared import agent_ops
from dominion.shared.agent_registry import AGENTS, FALLBACK_ATTR, STAGE_TO_SETTING
from dominion.shared.config import settings
from dominion.shared.models import Book, LlmCall, ReadThrough, ReadThroughChapter, ReadThroughNote
from dominion.workers import llm, telemetry
from dominion.workers.budget import Usage
from dominion.workers.llm_escalation import policy_for_setting, resolve_fallback_model
from dominion.workers.read_through.suggest import _window

CHAPTER = (
    "The lamp went out at the third hour and nobody relit it.\n\n"
    '"Body," he said, touching the first. "Soul." His finger moved to the second. "Name."\n\n'
    '"If one tears loose before the others, she dies."\n\n'
    "Outside, the tide came in over the causeway and took the road with it.\n"
)
ANCHOR = '"If one tears loose before the others, she dies."'
USAGE = Usage(input_tokens=900, output_tokens=210)


# --------------------------------------------------------------------------------------------------
# Registry coherence — these guard every role, not just this one.
# --------------------------------------------------------------------------------------------------


def test_every_agent_role_has_a_fallback_setting() -> None:
    """A role missing from FALLBACK_ATTR accepts the author's fallback choice and discards it."""
    missing = sorted(a.setting_key for a in AGENTS if a.setting_key not in FALLBACK_ATTR)
    assert missing == [], f"these roles would silently never escalate: {missing}"


def test_every_agent_role_has_its_settings_fields() -> None:
    missing: list[str] = []
    for agent in AGENTS:
        if not hasattr(settings, agent.setting_key):
            missing.append(agent.setting_key)
        fallback_attr = FALLBACK_ATTR.get(agent.setting_key)
        if fallback_attr and not hasattr(settings, fallback_attr):
            missing.append(fallback_attr)
    assert missing == [], f"roles whose model settings do not exist: {missing}"


def test_prose_suggestion_role_is_wired() -> None:
    assert STAGE_TO_SETTING["prose_suggestion"] == "prose_suggestion_model"
    assert resolve_fallback_model("prose_suggestion_model") == settings.prose_suggestion_fallback_model
    policy = policy_for_setting("prose_suggestion_model")
    # Prose truncates mid-sentence; the retry needs more room than the primary had.
    assert policy.fallback_max_tokens_floor is not None
    # A haiku-tier model writing in someone else's voice produces fluent pastiche, which is the one
    # failure the author cannot spot by skimming.
    assert "haiku" in policy.never_fallback_tiers


def test_quality_knob_is_reported_live_because_the_worker_reads_it() -> None:
    """`suggest.py` passes quality_temperature/quality_effort, so the page must not say it is dead."""
    assert "prose_suggestion_model" in agent_ops.QUALITY_LIVE


# --------------------------------------------------------------------------------------------------
# Pure helpers
# --------------------------------------------------------------------------------------------------


def test_window_centres_on_the_anchor_and_keeps_it_whole() -> None:
    window = _window(CHAPTER, ANCHOR)
    assert ANCHOR in window
    assert "lamp went out" in window  # context before survives


def test_window_falls_back_to_the_head_when_the_quote_is_absent() -> None:
    """A book-pass note can carry an unlocated anchor; an empty passage would produce prose written
    against nothing at all, which is worse than prose written against the opening."""
    window = _window(CHAPTER, "a sentence that is not in this chapter")
    assert window.startswith("The lamp went out")


def _anchor(state: str, text: str, chapter_id: uuid.UUID | None = None) -> dict[str, Any]:
    return {
        "chapter_id": str(chapter_id) if chapter_id else None,
        "state": state,
        "text_quoted": text,
        "segments": [{"start": 0, "end": len(text), "text": text}] if state != "unlocated" else [],
        "candidates": [],
        "candidate_count": 0,
    }


def test_anchor_choice_prefers_located_over_ambiguous() -> None:
    note = ReadThroughNote(anchors=[_anchor("ambiguous", "second best"), _anchor("located", ANCHOR)])
    chosen = router_mod._anchor_for_suggestion(note)
    assert chosen is not None and chosen[1] == ANCHOR


def test_anchor_choice_is_none_when_nothing_was_located() -> None:
    note = ReadThroughNote(anchors=[_anchor("unlocated", "nowhere")])
    assert router_mod._anchor_for_suggestion(note) is None


# --------------------------------------------------------------------------------------------------
# The endpoint
# --------------------------------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _synthetic_standards(tmp_path, monkeypatch) -> None:
    """Real style documents live in gitignored `series/`; these stand in so the route is not 503."""
    for attr, body in (
        ("prose_contract_path", "# contract\n7. Earn the turn.\n"),
        ("prose_clarity_rules_path", "# clarity\nR2. One idea per sentence.\n"),
        ("forbidden_drift_path", "# drift\n## 18 Overworked Voice\nSigns: piling clauses.\n"),
    ):
        path = tmp_path / f"{attr}.md"
        path.write_text(body, encoding="utf-8")
        monkeypatch.setattr(settings, attr, str(path))


@pytest.fixture
def fake_model(monkeypatch) -> list[dict[str, Any]]:
    """One suggestion whose anchor is real, one whose anchor is invented."""
    calls: list[dict[str, Any]] = []

    async def fake_complete(**kwargs: Any) -> tuple[str, Usage]:
        calls.append(kwargs)
        raw = json.dumps(
            [
                {
                    "mode": "insert_before",
                    "anchor_quote": ANCHOR,
                    "prose": "Three tethers, and the name was the one that bled.",
                    "why": "Gives the rule a cost before the operation starts.",
                },
                {
                    "mode": "replace",
                    "anchor_quote": "a line the author never wrote",
                    "prose": "Invented.",
                    "why": "Should be dropped.",
                },
            ]
        )
        telemetry.record(
            model=kwargs["model"],
            input_tokens=USAGE.input_tokens,
            output_tokens=USAGE.output_tokens,
            cache_creation_tokens=0,
            cache_read_tokens=0,
            truncated=False,
            latency_ms=4,
        )
        return raw, USAGE

    monkeypatch.setattr(llm, "complete", fake_complete)
    return calls


async def _seed_note(db_factory, *, anchors: list[dict[str, Any]] | None = None, chapter_id_on_note: bool = True):
    async with db_factory() as s:
        book = Book(title="Synthetic Book")
        s.add(book)
        await s.flush()
        rt = ReadThrough(
            book_id=book.id,
            title="Seeded",
            client_request_id=f"seed-{uuid.uuid4()}",
            payload_sha256="0" * 64,
            status="succeeded",
            deadline_at=datetime.now(UTC) + timedelta(hours=3),
            settings_snapshot={"model": "fake-model"},
            attempt_allowance=4,
            voice_guide_snapshot="## Voice\nPlain, close third. Short sentences under pressure.\n",
        )
        s.add(rt)
        await s.flush()
        chapter = ReadThroughChapter(
            read_through_id=rt.id,
            position=1,
            label="Chapter 3",
            text=CHAPTER,
            word_count=len(CHAPTER.split()),
            status="done",
        )
        s.add(chapter)
        await s.flush()
        note = ReadThroughNote(
            read_through_id=rt.id,
            chapter_id=chapter.id if chapter_id_on_note else None,
            position=1,
            category="clarity",
            priority="high",
            title="The ritual's rules remain too abstract",
            observation="The reader can follow the operation but not reason about it.",
            recommendation="Ground the three anchors before the operation, fragmentary and in voice.",
            anchor_role="evidence",
            anchors=anchors if anchors is not None else [_anchor("located", ANCHOR, chapter.id)],
            scope_chapter_ids=[],
            status="open",
        )
        s.add(note)
        await s.commit()
        return rt.id, book.id, note.id


async def test_unknown_note_is_404(app_client) -> None:
    resp = await app_client.post(f"/read-through-notes/{uuid.uuid4()}/prose-suggestion")
    assert resp.status_code == 404


async def test_a_note_with_no_located_anchor_is_refused_before_paying(app_client, db_factory, fake_model) -> None:
    _, _, note_id = await _seed_note(db_factory, anchors=[_anchor("unlocated", "nowhere")])
    resp = await app_client.post(f"/read-through-notes/{note_id}/prose-suggestion")
    assert resp.status_code == 422
    assert "no anchor" in resp.json()["detail"]
    assert fake_model == [], "the model must not be called for a note with nothing to quote"


async def test_suggestion_returns_prose_and_drops_the_invented_anchor(app_client, db_factory, fake_model) -> None:
    _, _, note_id = await _seed_note(db_factory)
    resp = await app_client.post(f"/read-through-notes/{note_id}/prose-suggestion")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # The fabrication guard is deterministic, not a matter of trust: a suggestion pinned to text that
    # is not in the chapter is unfalsifiable, and the author would have to go looking to find out.
    assert len(body["suggestions"]) == 1
    assert body["fabricated_dropped"] == 1
    assert body["suggestions"][0]["mode"] == "insert_before"
    assert body["suggestions"][0]["anchor_quote"] == ANCHOR

    assert "voice_guide" in body["standards_loaded"]
    assert body["model"] == settings.prose_suggestion_model
    assert body["telemetry_recorded"] is True


async def test_the_note_and_the_voice_guide_reach_the_prompt(app_client, db_factory, fake_model) -> None:
    _, _, note_id = await _seed_note(db_factory)
    assert (await app_client.post(f"/read-through-notes/{note_id}/prose-suggestion")).status_code == 200
    user = fake_model[0]["user"]
    # The note IS the brief — without it this is a generic rewrite request.
    assert "Ground the three anchors before the operation" in user
    assert "The reader can follow the operation" in user
    assert "Plain, close third" in user
    assert ANCHOR in user
    assert fake_model[0]["setting_key"] == "prose_suggestion_model"


async def test_spend_lands_on_the_read_through_run_not_a_fresh_one(app_client, db_factory, fake_model) -> None:
    """run_id is the read-through's, so the cost joins the run the author already recognises and the
    Telemetry tab keeps labelling it "Read-through" rather than showing a bare uuid."""
    rt_id, book_id, note_id = await _seed_note(db_factory)
    assert (await app_client.post(f"/read-through-notes/{note_id}/prose-suggestion")).status_code == 200
    async with db_factory() as s:
        rows = (await s.execute(LlmCall.__table__.select())).mappings().all()
    assert len(rows) == 1
    assert rows[0]["stage"] == "prose_suggestion"
    assert rows[0]["run_id"] == rt_id
    assert rows[0]["book_id"] == book_id
    # No chapters row exists for a snapshot, so this must stay NULL — a FK to chapters.id is what
    # makes that structural, not a choice.
    assert rows[0]["chapter_id"] is None


async def test_no_standards_is_503_rather_than_prose_from_a_general_assistant(
    app_client, db_factory, fake_model, monkeypatch
) -> None:
    for attr in ("prose_contract_path", "prose_clarity_rules_path", "forbidden_drift_path"):
        monkeypatch.setattr(settings, attr, "/nonexistent/none.md")
    _, _, note_id = await _seed_note(db_factory)
    async with db_factory() as s:
        rt = (await s.execute(ReadThrough.__table__.select())).mappings().first()
        await s.execute(
            ReadThrough.__table__.update().where(ReadThrough.id == rt["id"]).values(voice_guide_snapshot=None)
        )
        await s.commit()
    resp = await app_client.post(f"/read-through-notes/{note_id}/prose-suggestion")
    assert resp.status_code == 503
    assert "standards" in resp.json()["detail"]
