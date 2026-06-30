"""Learning-from-edits Tier 3 (distill edits → proposed rules) against real Postgres.

A review-model pass reads the POV's recent EditPair before→after rows and PROPOSES voice/dialogue
rules (stored pending); accepting one appends it to the POV's PovProfile.voice_spec (read fresh by the
drafter on the next scene); rejecting changes nothing. The LLM call is mocked. Mirrors test_learning.
"""

from __future__ import annotations

import json

from sqlalchemy import select

from dominion.api.routers import learning
from dominion.shared.enums import RuleProposalStatus
from dominion.shared.models import Book, Chapter, EditPair, PovProfile, RuleProposal, Scene
from dominion.shared.schemas import RuleProposalDecisionIn
from dominion.workers import llm
from dominion.workers.budget import Usage

# --- fixtures (mirror test_learning's tiny builders) ----------------------------------------------


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


async def _scene(s, ch, scene_no=1):
    sc = Scene(chapter_id=ch.id, scene_no=scene_no, version=1, prose="Prose.", prose_source="agent")
    s.add(sc)
    await s.flush()
    return sc


async def _pair(s, sc, *, pov="Marcus", agent_text="He saw the door.", human_text="The door."):
    p = EditPair(scene_id=sc.id, version=sc.version, pov=pov, agent_text=agent_text, human_text=human_text)
    s.add(p)
    await s.flush()
    return p


def _mock_llm(monkeypatch, payload):
    """Stub llm.complete to return a fixed JSON body (the distiller parses it tolerantly)."""

    async def _complete(*, model, system, user, max_tokens, budget):
        budget.charge(Usage(10, 10))
        return json.dumps(payload), Usage(10, 10)

    monkeypatch.setattr(llm, "complete", _complete)


# --- distill: propose + persist -------------------------------------------------------------------


async def test_distill_creates_pending_proposals_from_pairs(db_factory, monkeypatch):
    _mock_llm(
        monkeypatch,
        [
            {"kind": "voice", "rule": "Trim filter verbs (saw/felt/noticed)", "why": "cut 'He saw'"},
            {"kind": "dialogue", "rule": "Keep tags to said/asked", "why": "tags simplified"},
        ],
    )
    async with db_factory() as s:
        book = await _book(s)
        ch = await _chapter(s, book)  # pov Marcus
        sc = await _scene(s, ch, 1)
        await _pair(s, sc)
        await s.commit()

        created = await learning.distill_rules(book.id, s, pov="Marcus")
        await s.commit()

        assert {c.kind for c in created} == {"voice", "dialogue"}
        assert all(c.status == RuleProposalStatus.PENDING for c in created)
        assert all(c.pov == "Marcus" for c in created)
        # provenance: the pair this batch was distilled from is recorded
        assert created[0].source_pair_ids and len(created[0].source_pair_ids) == 1


async def test_distill_dedups_against_existing_non_rejected(db_factory, monkeypatch):
    _mock_llm(monkeypatch, [{"kind": "voice", "rule": "Trim filter verbs", "why": "x"}])
    async with db_factory() as s:
        book = await _book(s)
        ch = await _chapter(s, book)
        sc = await _scene(s, ch, 1)
        await _pair(s, sc)
        await s.commit()

        first = await learning.distill_rules(book.id, s, pov="Marcus")
        await s.commit()
        again = await learning.distill_rules(book.id, s, pov="Marcus")  # same rule comes back
        await s.commit()

        assert len(first) == 1
        assert again == []  # identical rule isn't re-proposed
        rows = (await s.execute(select(RuleProposal))).scalars().all()
        assert len(rows) == 1


async def test_distill_skips_noop_pairs(db_factory, monkeypatch):
    # A pair whose human_text == agent_text (approved without a real edit) carries no signal — with no
    # usable pairs the distiller proposes nothing and never calls the model.
    called = {"n": 0}

    async def _complete(**_):
        called["n"] += 1
        return "[]", Usage(0, 0)

    monkeypatch.setattr(llm, "complete", _complete)
    async with db_factory() as s:
        book = await _book(s)
        ch = await _chapter(s, book)
        sc = await _scene(s, ch, 1)
        await _pair(s, sc, agent_text="Same text.", human_text="Same text.")
        await s.commit()

        created = await learning.distill_rules(book.id, s, pov="Marcus")
        await s.commit()
        assert created == []
        assert called["n"] == 0  # no pairs → no LLM call


# --- decision: accept appends to voice_spec; reject is a no-op ------------------------------------


async def test_accept_appends_rule_to_voice_spec(db_factory, monkeypatch):
    _mock_llm(monkeypatch, [{"kind": "voice", "rule": "Trim filter verbs", "why": "x"}])
    async with db_factory() as s:
        book = await _book(s)
        ch = await _chapter(s, book)
        sc = await _scene(s, ch, 1)
        await _pair(s, sc)
        # Seed an existing hand-authored voice spec — the accepted rule appends, never clobbers it.
        s.add(PovProfile(book_id=book.id, character="Marcus", voice_spec="terse, wry"))
        await s.commit()

        [proposal] = await learning.distill_rules(book.id, s, pov="Marcus")
        await s.commit()

        out = await learning.decide_rule_proposal(
            proposal.id, RuleProposalDecisionIn(status=RuleProposalStatus.ACCEPTED), s
        )
        await s.commit()
        assert out.status == RuleProposalStatus.ACCEPTED

        prof = (await s.execute(select(PovProfile).where(PovProfile.character == "Marcus"))).scalar_one()
        assert prof.voice_spec == "terse, wry\n- Trim filter verbs"

        # Re-accepting must not append the rule a second time (idempotent on the transition).
        await learning.decide_rule_proposal(proposal.id, RuleProposalDecisionIn(status=RuleProposalStatus.ACCEPTED), s)
        await s.commit()
        prof = (await s.execute(select(PovProfile).where(PovProfile.character == "Marcus"))).scalar_one()
        assert prof.voice_spec.count("- Trim filter verbs") == 1


async def test_accept_with_edited_text_uses_authors_wording(db_factory, monkeypatch):
    _mock_llm(monkeypatch, [{"kind": "voice", "rule": "Trim verbs", "why": "x"}])
    async with db_factory() as s:
        book = await _book(s)
        ch = await _chapter(s, book)
        sc = await _scene(s, ch, 1)
        await _pair(s, sc)
        await s.commit()

        [proposal] = await learning.distill_rules(book.id, s, pov="Marcus")
        await s.commit()

        await learning.decide_rule_proposal(
            proposal.id,
            RuleProposalDecisionIn(status=RuleProposalStatus.ACCEPTED, rule_text="Cut filter verbs entirely"),
            s,
        )
        await s.commit()
        prof = (await s.execute(select(PovProfile).where(PovProfile.character == "Marcus"))).scalar_one()
        assert prof.voice_spec == "- Cut filter verbs entirely"  # no profile existed → starts fresh


async def test_reject_leaves_voice_spec_untouched(db_factory, monkeypatch):
    _mock_llm(monkeypatch, [{"kind": "voice", "rule": "Trim verbs", "why": "x"}])
    async with db_factory() as s:
        book = await _book(s)
        ch = await _chapter(s, book)
        sc = await _scene(s, ch, 1)
        await _pair(s, sc)
        await s.commit()

        [proposal] = await learning.distill_rules(book.id, s, pov="Marcus")
        await s.commit()

        await learning.decide_rule_proposal(proposal.id, RuleProposalDecisionIn(status=RuleProposalStatus.REJECTED), s)
        await s.commit()
        assert (await s.execute(select(PovProfile))).scalar_one_or_none() is None  # nothing applied
