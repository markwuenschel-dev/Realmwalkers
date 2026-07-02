"""Learning-from-edits Tier 1 (clean capture) + Tier 2 (the exemplar wire) against real Postgres.

Tier 1: a hand-edit in review snapshots a faithful agent→human pair (EditPair), rendered (not marker
form), upserted per (scene, version). Tier 2: assemble_context loads the POV's curated exemplars into
ctx.exemplars — the wire the drafter already consumes but nothing fed. LLM/embedding calls are mocked.
"""

from __future__ import annotations

import uuid

from fastapi import BackgroundTasks
from sqlalchemy import select

from dominion.api.routers import reviews, scenes
from dominion.shared.config import settings
from dominion.shared.enums import BeatStatus, Decision, GateMode, JobKind, RunStatus, SceneStatus
from dominion.shared.models import (
    Beat,
    Book,
    Chapter,
    EditPair,
    Job,
    PovProfile,
    Run,
    Scene,
)
from dominion.shared.schemas import DecisionIn, ExemplarIn
from dominion.workers.context import assemble_context
from dominion.workers.legacy import set_exemplars as set_exemplars_mod
from dominion.workers.memory import canon_rag, summaries
from tests.conftest import seed_scene_packet

# --- fixtures (mirror test_phase2's tiny builders) ------------------------------------------------


async def _book(s, title="Dominion Realm"):
    book = Book(title=title)
    s.add(book)
    await s.flush()
    return book


async def _chapter(s, book, no=1, pov="Marcus"):
    ch = Chapter(book_id=book.id, chapter_no=no, pov=pov)
    s.add(ch)
    await s.flush()
    return ch


async def _scene(
    s, ch, scene_no=1, *, prose="Prose.", agent_original=None, status=SceneStatus.PENDING_REVIEW, version=1
):
    sc = Scene(
        chapter_id=ch.id,
        scene_no=scene_no,
        version=version,
        status=status,
        prose=prose,
        prose_source="agent",
        agent_original=agent_original,
        passes_run=["drafter"],
    )
    s.add(sc)
    await s.flush()
    return sc


# --- Tier 1: clean capture ------------------------------------------------------------------------


async def test_hand_edit_captures_rendered_agent_pair(db_factory):
    """A hand-edit records one EditPair whose agent_text is the RENDERED draft (box-art, no markers)."""
    async with db_factory() as s:
        book = await _book(s)
        ch = await _chapter(s, book)  # pov Marcus
        sc = await _scene(
            s,
            ch,
            1,
            agent_original="Marcus checked his sight.\n\n```stat\nPerception: 15\n```",
            prose="(rendered form lives here)",
        )
        await reviews.decide(
            sc.id,
            DecisionIn(decision=Decision.APPROVE, edited_prose="Marcus checked his sharpened sight."),
            s,
            BackgroundTasks(),
        )
        await s.commit()

        pair = (await s.execute(select(EditPair))).scalar_one()
        assert pair.scene_id == sc.id
        assert pair.version == sc.version
        assert pair.pov == "Marcus"
        assert pair.human_text == "Marcus checked his sharpened sight."
        # agent_text is the marker form RENDERED into a box — not the raw ```stat``` markers.
        assert "┌" in (pair.agent_text or "") and "Perception" in (pair.agent_text or "")
        assert "```stat" not in (pair.agent_text or "")


async def test_approve_without_edit_captures_no_pair(db_factory):
    async with db_factory() as s:
        book = await _book(s)
        ch = await _chapter(s, book)
        sc = await _scene(s, ch, 1, prose="Untouched agent prose.")
        await reviews.decide(sc.id, DecisionIn(decision=Decision.APPROVE), s, BackgroundTasks())
        await s.commit()
        assert (await s.execute(select(EditPair))).scalar_one_or_none() is None


async def test_reedit_refreshes_human_text_keeps_agent_draft(db_factory):
    """Re-editing the same scene version updates human_text only — never a second row, never a
    human→human pair (agent_text stays the original model draft even though scene.prose has moved on)."""
    async with db_factory() as s:
        book = await _book(s)
        ch = await _chapter(s, book)
        # No agent_original -> agent_text falls back to the pre-edit prose (the agent draft).
        sc = await _scene(s, ch, 1, prose="Agent draft.", agent_original=None)
        await reviews.decide(
            sc.id,
            DecisionIn(decision=Decision.APPROVE, edited_prose="First human edit."),
            s,
            BackgroundTasks(),
        )
        await s.commit()

        await reviews.decide(
            sc.id,
            DecisionIn(decision=Decision.APPROVE, edited_prose="Second human edit."),
            s,
            BackgroundTasks(),
        )
        await s.commit()

        pairs = (await s.execute(select(EditPair))).scalars().all()
        assert len(pairs) == 1  # upsert, not a duplicate
        assert pairs[0].agent_text == "Agent draft."  # original draft preserved
        assert pairs[0].human_text == "Second human edit."  # human side refreshed


# --- Tier 2: the exemplar wire --------------------------------------------------------------------


def _stub_memory(monkeypatch):
    """assemble_context fans out to canon RAG + summaries (LLM/embedding calls); stub them for unit speed."""

    async def _no_canon(*a, **k):
        return []

    async def _no_summary(*a, **k):
        return None

    monkeypatch.setattr(canon_rag, "retrieve", _no_canon)
    monkeypatch.setattr(summaries, "pov_summary", _no_summary)


async def _draft_job(s, book, ch, scene_no=2):
    """A queued DRAFT job + the beat it needs, so assemble_context can run."""
    run = Run(
        book_id=book.id,
        scope_json={"chapter": 1},
        gate_mode=GateMode.PAUSE_EACH,
        token_budget=40_000,
        status=RunStatus.ACTIVE,
    )
    s.add(run)
    await s.flush()
    beat = Beat(
        chapter_id=ch.id,
        scene_no=scene_no,
        tags=[],
        characters_present=["Marcus"],
        status=BeatStatus.APPROVED,
        beat_text="Marcus presses on.",
    )
    s.add(beat)
    await s.flush()
    await seed_scene_packet(s, chapter=ch, beat=beat)
    job = Job(run_id=run.id, kind=JobKind.DRAFT, chapter_no=ch.chapter_no, scene_no=scene_no, token_budget=40_000)
    s.add(job)
    await s.flush()
    return job


async def test_assemble_context_loads_curated_exemplars_in_order(db_factory, monkeypatch):
    _stub_memory(monkeypatch)
    async with db_factory() as s:
        book = await _book(s)
        ch = await _chapter(s, book)  # pov Marcus
        a = await _scene(s, ch, 1, prose="Exemplar A prose.", status=SceneStatus.APPROVED)
        b = await _scene(s, ch, 3, prose="Exemplar B prose.", status=SceneStatus.APPROVED)
        # Author's curated order is B then A — assemble_context must preserve it (not the IN-query order).
        s.add(PovProfile(book_id=book.id, character="Marcus", exemplar_scene_ids=[str(b.id), str(a.id)]))
        job = await _draft_job(s, book, ch, scene_no=2)
        await s.commit()

        ctx = await assemble_context(s, job)
        assert ctx.exemplars == ["Exemplar B prose.", "Exemplar A prose."]


async def test_exemplars_capped_by_count_and_length(db_factory, monkeypatch):
    _stub_memory(monkeypatch)
    monkeypatch.setattr(settings, "exemplar_max_count", 2)
    monkeypatch.setattr(settings, "exemplar_max_chars", 10)
    async with db_factory() as s:
        book = await _book(s)
        ch = await _chapter(s, book)
        ids = []
        for i in range(3):
            sc = await _scene(s, ch, 10 + i, prose=f"Exemplar {i} " + "x" * 100, status=SceneStatus.APPROVED)
            ids.append(str(sc.id))
        s.add(PovProfile(book_id=book.id, character="Marcus", exemplar_scene_ids=ids))
        job = await _draft_job(s, book, ch, scene_no=2)
        await s.commit()

        ctx = await assemble_context(s, job)
        assert len(ctx.exemplars) == 2  # count cap
        assert all(len(e) <= 10 for e in ctx.exemplars)  # per-passage length cap


async def test_no_profile_yields_no_exemplars(db_factory, monkeypatch):
    _stub_memory(monkeypatch)
    async with db_factory() as s:
        book = await _book(s)
        ch = await _chapter(s, book)  # no PovProfile at all
        job = await _draft_job(s, book, ch, scene_no=2)
        await s.commit()

        ctx = await assemble_context(s, job)
        assert ctx.exemplars == []


async def test_duplicate_beats_do_not_crash_drafting(db_factory, monkeypatch):
    # A re-run plan-call / re-enqueue can leave two beats for one (chapter, scene). assemble_context
    # must pick a canonical beat (the approved one) instead of raising MultipleResultsFound and failing
    # the draft before it begins — the bug that stranded a chapter of scenes as FAILED.
    _stub_memory(monkeypatch)
    async with db_factory() as s:
        book = await _book(s)
        ch = await _chapter(s, book)
        job = await _draft_job(s, book, ch, scene_no=2)  # adds one APPROVED beat ("Marcus presses on.")
        s.add(
            Beat(
                chapter_id=ch.id,
                scene_no=2,
                tags=[],
                characters_present=["Marcus"],
                status=BeatStatus.PROPOSED,
                beat_text="stale duplicate beat",
            )
        )
        await s.commit()

        ctx = await assemble_context(s, job)  # must not raise on the duplicate
        assert ctx.beat_text == "Marcus presses on."  # the approved beat wins


# --- set_exemplars authoring CLI ------------------------------------------------------------------


async def test_set_exemplars_upserts_and_preserves_voice_spec(db_factory):
    async with db_factory() as s:
        book = await _book(s, title="Exemplar Book")
        ch = await _chapter(s, book)
        sc = await _scene(s, ch, 1, status=SceneStatus.APPROVED)
        # Seed a voice_spec the exemplar upsert must NOT clobber.
        s.add(PovProfile(book_id=book.id, character="Marcus", voice_spec="terse, wry"))
        await s.commit()

        await set_exemplars_mod.set_exemplars(s, book_title="Exemplar Book", character="Marcus", scene_ids=[sc.id])
        await s.commit()
        row = (await s.execute(select(PovProfile).where(PovProfile.character == "Marcus"))).scalar_one()
        assert row.exemplar_scene_ids == [str(sc.id)]
        assert row.voice_spec == "terse, wry"  # untouched

    async with db_factory() as s:
        # Re-run with an empty list clears the exemplars in place (same single row).
        await set_exemplars_mod.set_exemplars(s, book_title="Exemplar Book", character="Marcus", scene_ids=[])
        await s.commit()
        rows = (await s.execute(select(PovProfile).where(PovProfile.character == "Marcus"))).scalars().all()
        assert len(rows) == 1
        assert rows[0].exemplar_scene_ids is None
        assert rows[0].voice_spec == "terse, wry"


def test_parse_ids_rejects_garbage():
    import pytest

    good = uuid.uuid4()
    assert set_exemplars_mod._parse_ids(f"{good}, {good}") == [good, good]
    with pytest.raises(SystemExit):
        set_exemplars_mod._parse_ids("not-a-uuid")


# --- exemplar toggle endpoint (the in-editor button's backend) ------------------------------------


async def test_exemplar_toggle_endpoint_round_trips(db_factory):
    async with db_factory() as s:
        book = await _book(s)
        ch = await _chapter(s, book)  # pov Marcus, no profile yet
        sc = await _scene(s, ch, 1, prose="Some prose.", status=SceneStatus.APPROVED)
        await s.commit()

        # enable: creates the POV profile and adds the scene id; scene_detail reflects it
        on = await scenes.set_exemplar(sc.id, ExemplarIn(enabled=True), s)
        assert on == {"scene": str(sc.id), "is_exemplar": True}
        prof = (await s.execute(select(PovProfile).where(PovProfile.character == "Marcus"))).scalar_one()
        assert prof.exemplar_scene_ids == [str(sc.id)]
        assert (await scenes.scene_detail(sc.id, s)).is_exemplar is True

        # idempotent enable doesn't duplicate
        await scenes.set_exemplar(sc.id, ExemplarIn(enabled=True), s)
        prof = (await s.execute(select(PovProfile).where(PovProfile.character == "Marcus"))).scalar_one()
        assert prof.exemplar_scene_ids == [str(sc.id)]

        # disable: removes it, clears the list to None, detail flips back
        off = await scenes.set_exemplar(sc.id, ExemplarIn(enabled=False), s)
        assert off == {"scene": str(sc.id), "is_exemplar": False}
        prof = (await s.execute(select(PovProfile).where(PovProfile.character == "Marcus"))).scalar_one()
        assert prof.exemplar_scene_ids is None
        assert (await scenes.scene_detail(sc.id, s)).is_exemplar is False
