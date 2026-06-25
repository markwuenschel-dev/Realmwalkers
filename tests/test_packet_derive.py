"""Phase-2 derivation: an approved chapter packet becomes the chapter's beats, and its constraints
flow into the drafter. Against real Postgres (skips if unreachable); agents mocked where needed.
Mirrors tests/test_packet_pipeline.py — router/worker functions are called directly with a session."""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select

from dominion.api.routers import packets
from dominion.shared.enums import BeatStatus, PacketStatus, PacketVerdict, SceneStatus
from dominion.shared.models import Beat, Book, Chapter, ChapterPacket, Scene
from dominion.shared.schemas import PacketUpdateIn
from dominion.workers import context as context_mod
from dominion.workers.budget import TokenBudget
from dominion.workers.context import SceneContext
from dominion.workers.packet import author as author_mod
from dominion.workers.packet import derive as derive_mod
from dominion.workers.packet import qa as qa_mod
from dominion.workers.specialists.drafter import _beat_prompt, _revise_prompt


async def _seed_chapter(s, *, outline: str = "Marcus intercepts the rogue.") -> Chapter:
    book = Book(title="X")
    s.add(book)
    await s.flush()
    ch = Chapter(book_id=book.id, chapter_no=1, pov="Marcus", outline=outline)
    s.add(ch)
    await s.flush()
    return ch


def _seed(
    scene_no: int, job: str, *, scene_type: str | None = None, required: list[str] | None = None,
    target: int | None = None, exit_state: str | None = None, seed_id: str | None = None,
) -> dict[str, Any]:
    d: dict[str, Any] = {"scene_no": scene_no, "scene_job": job}
    if scene_type:
        d["scene_type"] = scene_type
    if required:
        d["required_beats"] = required
    if target:
        d["word_budget"] = {"target": target}
    if exit_state:
        d["exit_state"] = exit_state
    if seed_id:
        d["seed_id"] = seed_id
    return d


def _body(seeds: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    return {"confidence": "green", "scene_seeds": seeds, "claims": [], **extra}


async def _approved_packet(s, ch: Chapter, seeds: list[dict[str, Any]], **extra: Any) -> ChapterPacket:
    row = ChapterPacket(
        book_id=ch.book_id, chapter_id=ch.id, status=PacketStatus.APPROVED, confidence="green",
        body=_body(seeds, **extra), open_questions={"items": []},
    )
    s.add(row)
    await s.flush()
    return row


def _patch(monkeypatch, body: dict[str, Any]) -> None:
    async def fake_author(**kwargs):
        return body

    async def fake_qa(_packet, **kwargs):
        return {"verdict": PacketVerdict.APPROVE, "residual_risks": [], "issues": []}

    monkeypatch.setattr(author_mod, "author_packet", fake_author)
    monkeypatch.setattr(qa_mod, "qa_packet", fake_qa)


# --- approval derives beats (end to end through the router) ----------------------------------------

async def test_approve_derives_one_beat_per_seed(db_factory, monkeypatch):
    _patch(monkeypatch, _body(
        [_seed(1, "Cold open on the anomaly.", scene_type="dialogue", required=["log the anomaly"], target=900),
         _seed(2, "The duel.", scene_type="combat", required=["Serra strikes"], target=1500,
               exit_state="both wounded")],
        characters_present=["Marcus", "Serra"], characters_absent=["Eriadne"],
    ))
    async with db_factory() as s:
        ch = await _seed_chapter(s)
        await packets.propose_packet(ch.id, s)
        approved = await packets.approve_packet(ch.id, s)

        seed_ids = {sd["seed_id"] for sd in approved.body["scene_seeds"]}
        beats = (await s.execute(
            select(Beat).where(Beat.chapter_id == ch.id).order_by(Beat.scene_no)
        )).scalars().all()
        assert len(beats) == 2
        assert {str(b.scene_seed_id) for b in beats} == seed_ids       # linked by the stable seed id
        b1, b2 = beats
        assert b1.status == BeatStatus.APPROVED                        # packet approval == gate 1
        assert "Cold open on the anomaly." in (b1.beat_text or "")
        assert b1.target_words == 900                                  # from word_budget.target
        assert b1.tags == ["dialogue"] and b2.tags == ["combat"]       # scene_type routed to lanes
        assert b1.characters_present == ["Marcus", "Serra"]            # present minus absent


# --- idempotent re-derive (update in place, no duplicates) -----------------------------------------

async def test_rederive_updates_in_place_no_duplicates(db_factory):
    async with db_factory() as s:
        ch = await _seed_chapter(s)
        sid = str(uuid.uuid4())
        pkt = await _approved_packet(s, ch, [_seed(1, "First job.", target=800, seed_id=sid)])
        assert await derive_mod.derive_beats(s, packet=pkt) == 1
        await s.flush()
        first = (await s.execute(select(Beat).where(Beat.chapter_id == ch.id))).scalars().all()
        assert len(first) == 1
        beat_id = first[0].id

        # same seed_id, edited fields -> the same beat row is updated, not duplicated
        pkt.body = _body([_seed(1, "Revised job.", target=1200, seed_id=sid)])
        assert await derive_mod.derive_beats(s, packet=pkt) == 1
        await s.flush()
        again = (await s.execute(select(Beat).where(Beat.chapter_id == ch.id))).scalars().all()
        assert len(again) == 1
        assert again[0].id == beat_id
        assert again[0].target_words == 1200
        assert "Revised job." in (again[0].beat_text or "")


# --- prune removed seeds, but never a beat whose scene is already drafted --------------------------

async def test_rederive_prunes_removed_seed_but_keeps_drafted(db_factory):
    async with db_factory() as s:
        ch = await _seed_chapter(s)
        s1, s2 = str(uuid.uuid4()), str(uuid.uuid4())
        pkt = await _approved_packet(s, ch, [_seed(1, "Scene one.", seed_id=s1), _seed(2, "Scene two.", seed_id=s2)])
        await derive_mod.derive_beats(s, packet=pkt)
        await s.flush()

        s.add(Scene(chapter_id=ch.id, scene_no=2, status=SceneStatus.PENDING_REVIEW, prose="drafted"))
        await s.flush()

        # drop BOTH seeds: scene-1 beat (undrafted) is pruned; scene-2 beat (drafted) is preserved
        pkt.body = _body([])
        assert await derive_mod.derive_beats(s, packet=pkt) == 0
        await s.flush()
        remaining = (await s.execute(
            select(Beat).where(Beat.chapter_id == ch.id)
        )).scalars().all()
        assert [b.scene_no for b in remaining] == [2]
        assert str(remaining[0].scene_seed_id) == s2


# --- seed-id minting on the human-edit path --------------------------------------------------------

async def test_update_packet_mints_missing_seed_ids_preserving_existing(db_factory, monkeypatch):
    _patch(monkeypatch, _body([_seed(1, "Only seed.")]))
    async with db_factory() as s:
        ch = await _seed_chapter(s)
        proposed = await packets.propose_packet(ch.id, s)
        sid1 = proposed.body["scene_seeds"][0]["seed_id"]
        assert sid1

        new_body = _body([
            {"scene_no": 1, "scene_job": "Only seed.", "seed_id": sid1},
            {"scene_no": 2, "scene_job": "Added by hand."},                 # no id yet
        ])
        updated = await packets.update_packet(ch.id, PacketUpdateIn(body=new_body), s)
        seeds = updated.body["scene_seeds"]
        assert seeds[0]["seed_id"] == sid1                                  # preserved
        assert seeds[1].get("seed_id") and seeds[1]["seed_id"] != sid1      # minted fresh


# --- the contract the drafter is scoped to ---------------------------------------------------------

async def test_load_contract_pulls_chapter_and_scene_constraints(db_factory):
    async with db_factory() as s:
        ch = await _seed_chapter(s)
        sid = str(uuid.uuid4())
        await _approved_packet(
            s, ch,
            [_seed(1, "job", scene_type="combat", required=["land the hit"], exit_state="wounded", seed_id=sid)],
            forbidden_reveals=["Serra is the assassin"], forbidden_knowledge=["the cohort is rigged"],
            required_reveals=["Marcus distrusts the model"], canon_locks=["the Realm is real"],
            timeline_locks=["this is the same night"],
        )
        c = await context_mod._load_contract(s, chapter_id=ch.id, scene_seed_id=uuid.UUID(sid))
        assert c is not None
        assert c["forbidden_reveals"] == ["Serra is the assassin"]
        assert c["forbidden_knowledge"] == ["the cohort is rigged"]
        assert c["required_reveals"] == ["Marcus distrusts the model"]
        assert c["canon_locks"] == ["the Realm is real"]
        assert c["required_beats"] == ["land the hit"]      # scene-level, lifted from the seed
        assert c["exit_state"] == "wounded"
        # a plan-call beat (no link) has no contract
        assert await context_mod._load_contract(s, chapter_id=ch.id, scene_seed_id=None) is None


async def test_load_contract_none_without_approved_packet(db_factory):
    async with db_factory() as s:
        ch = await _seed_chapter(s)
        sid = str(uuid.uuid4())
        s.add(ChapterPacket(
            book_id=ch.book_id, chapter_id=ch.id, status=PacketStatus.PROPOSED,
            body=_body([_seed(1, "x", seed_id=sid)]), open_questions={"items": []},
        ))
        await s.flush()
        # a merely-proposed packet must not leak constraints to the writer
        assert await context_mod._load_contract(s, chapter_id=ch.id, scene_seed_id=uuid.UUID(sid)) is None


def _ctx(contract: dict[str, Any] | None, *, revise: bool = False) -> SceneContext:
    return SceneContext(
        book_id=uuid.uuid4(), chapter_id=uuid.uuid4(), pov="Marcus", scene_no=1, tags=[],
        characters_present=["Marcus"], beat_text="They talk.", expected_state_changes=None,
        knowledge_injections=[], voice_spec=None, budget=TokenBudget(max_tokens=40_000),
        contract=contract, prior_prose="old draft" if revise else None,
        revise_feedback="tighten it" if revise else None,
    )


def test_contract_block_appears_in_both_prompts():
    contract = {
        "forbidden_reveals": ["Serra is the assassin"],
        "forbidden_beats": ["Marcus uses his Aspect"],
        "required_reveals": ["the cohort is converging"],
        "exit_state": "the scrim begins",
        "canon_locks": ["the Realm is real"],
    }
    for prompt in (_beat_prompt(_ctx(contract)), _revise_prompt(_ctx(contract, revise=True))):
        assert "CONTRACT — obey exactly" in prompt
        assert "Serra is the assassin" in prompt        # forbidden reveal
        assert "Marcus uses his Aspect" in prompt       # forbidden beat
        assert "the cohort is converging" in prompt     # required reveal
        assert "the scrim begins" in prompt             # exit state
        assert "the Realm is real" in prompt            # immutable lock
        assert "MUST NOT:" in prompt and "MUST:" in prompt and "IMMUTABLE" in prompt


def test_no_contract_means_no_block():
    assert "CONTRACT — obey exactly" not in _beat_prompt(_ctx(None))
