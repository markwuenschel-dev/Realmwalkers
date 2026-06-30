"""Phase-2 derivation: an approved chapter packet becomes the chapter's beats, and its constraints
flow into the drafter. Against real Postgres (skips if unreachable); agents mocked where needed.
Mirrors tests/test_packet_pipeline.py — router/worker functions are called directly with a session."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select

from dominion.api.routers import packets
from dominion.shared.enums import PacketStatus, PacketVerdict
from dominion.shared.models import Beat, Book, Chapter, ChapterPacket
from dominion.shared.schemas import PacketUpdateIn
from dominion.workers import packet as packet_pipeline
from dominion.workers.budget import TokenBudget
from dominion.workers.context import SceneContext
from dominion.workers.packet import author as author_mod
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
    scene_no: int,
    job: str,
    *,
    scene_type: str | None = None,
    required: list[str] | None = None,
    target: int | None = None,
    exit_state: str | None = None,
    seed_id: str | None = None,
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
        book_id=ch.book_id,
        chapter_id=ch.id,
        status=PacketStatus.APPROVED,
        confidence="green",
        body=_body(seeds, **extra),
        open_questions={"items": []},
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


# --- chapter-packet approval no longer derives beats directly (scene-packet cutover) ---------------


async def test_chapter_packet_approval_does_not_derive_beats(db_factory, monkeypatch):
    # Beats now derive from APPROVED ScenePackets, not from chapter-packet scene seeds. Approving the
    # chapter packet alone must not create beats.
    _patch(
        monkeypatch,
        _body(
            [_seed(1, "Cold open on the anomaly.", scene_type="dialogue", target=900)],
            characters_present=["Marcus"],
        ),
    )
    async with db_factory() as s:
        ch = await _seed_chapter(s)
        await packet_pipeline.propose_packet(s, chapter=ch)
        await packets.approve_packet(ch.id, s)
        beats = (await s.execute(select(Beat).where(Beat.chapter_id == ch.id))).scalars().all()
        assert beats == []


# Beat derivation now flows from approved ScenePackets (idempotency + prune-but-keep-drafted are
# covered in tests/test_scene_packet.py against scene_packet.beats); the old chapter-packet→beat
# derivation was removed in the scene-packet cutover.


# --- seed-id minting on the human-edit path --------------------------------------------------------


async def test_update_packet_mints_missing_seed_ids_preserving_existing(db_factory, monkeypatch):
    _patch(monkeypatch, _body([_seed(1, "Only seed.")]))
    async with db_factory() as s:
        ch = await _seed_chapter(s)
        proposed = await packet_pipeline.propose_packet(s, chapter=ch)
        sid1 = proposed.body["scene_seeds"][0]["seed_id"]
        assert sid1

        new_body = _body(
            [
                {"scene_no": 1, "scene_job": "Only seed.", "seed_id": sid1},
                {"scene_no": 2, "scene_job": "Added by hand."},  # no id yet
            ]
        )
        updated = await packets.update_packet(ch.id, PacketUpdateIn(body=new_body), s)
        seeds = updated.body["scene_seeds"]
        assert seeds[0]["seed_id"] == sid1  # preserved
        assert seeds[1].get("seed_id") and seeds[1]["seed_id"] != sid1  # minted fresh


# --- the contract the drafter is scoped to ---------------------------------------------------------
# (The legacy chapter-packet contract loader was removed in the fail-closed cutover; the drafter now
# reads the flat contract from the approved ScenePacket — see tests/test_scene_packet.py. The drafter
# prompt-formatting of a flat contract dict is still covered below.)


def _ctx(contract: dict[str, Any] | None, *, revise: bool = False) -> SceneContext:
    return SceneContext(
        book_id=uuid.uuid4(),
        chapter_id=uuid.uuid4(),
        pov="Marcus",
        scene_no=1,
        tags=[],
        characters_present=["Marcus"],
        beat_text="They talk.",
        expected_state_changes=None,
        knowledge_injections=[],
        voice_spec=None,
        budget=TokenBudget(max_tokens=40_000),
        contract=contract,
        prior_prose="old draft" if revise else None,
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
    for prefix, user in (_beat_prompt(_ctx(contract)), _revise_prompt(_ctx(contract, revise=True))):
        # contract goes into the stable prefix; beat/revision content goes in user
        full = (prefix or "") + "\n" + user
        assert "CONTRACT — obey exactly" in full
        assert "Serra is the assassin" in full  # forbidden reveal
        assert "Marcus uses his Aspect" in full  # forbidden beat
        assert "the cohort is converging" in full  # required reveal
        assert "the scrim begins" in full  # exit state
        assert "the Realm is real" in full  # immutable lock
        assert "MUST NOT:" in full and "MUST:" in full and "IMMUTABLE" in full


def test_no_contract_means_no_block():
    prefix, user = _beat_prompt(_ctx(None))
    assert "CONTRACT — obey exactly" not in (prefix or "") + user
