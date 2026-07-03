"""Chapter-packet pipeline + approval-gate tests against real Postgres (skips if unreachable).

The agents are mocked, so these exercise the orchestration's fail-closed behavior, persistence, seed
minting, and the router's approval gate — not the LLM. Mirrors tests/test_desk_api.py: router/pipeline
functions are called directly with a session (see tests/conftest.py for the DB fixture).
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from dominion.api.routers import packets
from dominion.shared.enums import PacketStatus, PacketVerdict
from dominion.shared.models import Book, Chapter, ChapterPacket
from dominion.shared.schemas import PacketUpdateIn
from dominion.workers import packet as packet_pipeline
from dominion.workers.packet import approval_policy
from dominion.workers.packet import author as author_mod
from dominion.workers.packet import qa as qa_mod


async def _seed_chapter(s, outline: str = "Marcus intercepts the rogue.") -> Chapter:
    book = Book(title="X")
    s.add(book)
    await s.flush()
    ch = Chapter(book_id=book.id, chapter_no=1, pov="Marcus", outline=outline)
    s.add(ch)
    await s.flush()
    return ch


def _packet(confidence: str = "green", open_q: list[str] | None = None) -> dict:
    return {
        "confidence": confidence,
        "exit_state": "the duel begins",
        "scene_seeds": [{"scene_no": 1, "scene_job": "Marcus reads the route and intercepts."}],
        "claims": [{"claim": "Realm is real", "source_strength": "LOCKED_CANON", "source_id": "C1"}],
        "open_questions": open_q or [],
    }


def _qa(verdict: PacketVerdict = PacketVerdict.APPROVE, issues: list | None = None) -> dict:
    return {"verdict": verdict, "residual_risks": ["do not name Serra"], "issues": issues or []}


def _patch(monkeypatch, packet, qa) -> None:
    async def fake_author(**kwargs):
        return packet

    async def fake_qa(_packet, **kwargs):
        return qa

    monkeypatch.setattr(author_mod, "author_packet", fake_author)
    monkeypatch.setattr(qa_mod, "qa_packet", fake_qa)


# --- success path ---------------------------------------------------------------------------------


async def test_propose_persists_proposed_packet_with_seed_ids(db_factory, monkeypatch):
    _patch(monkeypatch, _packet(), _qa())
    async with db_factory() as s:
        ch = await _seed_chapter(s)
        row = await packet_pipeline.propose_packet(s, chapter=ch)
        assert row.status == PacketStatus.PROPOSED
        assert row.confidence == "green"
        # server minted a stable seed id on each scene seed
        assert row.body["scene_seeds"][0].get("seed_id")
        # exactly one current packet for the chapter
        n = len((await s.execute(select(ChapterPacket).where(ChapterPacket.chapter_id == ch.id))).scalars().all())
        assert n == 1


# --- fail closed ----------------------------------------------------------------------------------


async def test_malformed_author_fails_closed_to_blocked(db_factory, monkeypatch):
    async def author_none(**kwargs):
        return None

    monkeypatch.setattr(author_mod, "author_packet", author_none)
    async with db_factory() as s:
        ch = await _seed_chapter(s)
        row = await packet_pipeline.propose_packet(s, chapter=ch)
        assert row.status == PacketStatus.BLOCKED
        assert row.confidence == "red"


async def test_author_timeout_persists_actionable_blocker_metadata(db_factory, monkeypatch):
    async def author_timeout(**kwargs):
        raise TimeoutError

    monkeypatch.setattr(author_mod, "author_packet", author_timeout)
    async with db_factory() as s:
        ch = await _seed_chapter(s)
        row = await packet_pipeline.propose_packet(s, chapter=ch)
        warnings = row.qa_warnings or {}

        assert row.status == PacketStatus.BLOCKED
        assert row.confidence == "red"
        assert warnings.get("blocker_source") == "author"
        assert warnings.get("blocker_kind") == "timeout"
        assert "timed out after" in warnings.get("blocked_reason", "")
        assert "Re-propose will likely time out again" in warnings.get("blocked_reason", "")
        assert warnings.get("blocker_diagnostics", {}).get("stage") == "packet_author"
        assert warnings.get("blocker_diagnostics", {}).get("exception_type") == "TimeoutError"
        assert warnings.get("blocker_diagnostics", {}).get("timeout_s") is not None
        assert warnings.get("blocker_diagnostics", {}).get("model")
        assert any("DOMINION_PACKET_TIME_BUDGET_S" in a for a in warnings.get("recovery_actions", []))

        out = approval_policy.enrich_packet_out(row)
        assert out.blocked_reason == warnings["blocked_reason"]
        assert out.blocker_source == "author"
        assert out.blocker_kind == "timeout"
        assert out.blocker_diagnostics == warnings["blocker_diagnostics"]
        assert out.recovery_actions == warnings["recovery_actions"]


async def test_malformed_qa_fails_closed_but_keeps_body(db_factory, monkeypatch):
    async def author_ok(**kwargs):
        return _packet()

    async def qa_none(_packet, **kwargs):
        return None

    monkeypatch.setattr(author_mod, "author_packet", author_ok)
    monkeypatch.setattr(qa_mod, "qa_packet", qa_none)
    async with db_factory() as s:
        ch = await _seed_chapter(s)
        row = await packet_pipeline.propose_packet(s, chapter=ch)
        assert row.status == PacketStatus.BLOCKED
        # the authored body is preserved for inspection even though QA failed
        assert row.body.get("scene_seeds")


async def test_no_outline_fails_closed(db_factory, monkeypatch):
    _patch(monkeypatch, _packet(), _qa())
    async with db_factory() as s:
        ch = await _seed_chapter(s, outline="   ")
        row = await packet_pipeline.propose_packet(s, chapter=ch)
        assert row.status == PacketStatus.BLOCKED


# --- approval gate --------------------------------------------------------------------------------


async def test_blocked_packet_cannot_be_approved(db_factory, monkeypatch):
    async def author_none(**kwargs):
        return None

    monkeypatch.setattr(author_mod, "author_packet", author_none)
    async with db_factory() as s:
        ch = await _seed_chapter(s)
        await packet_pipeline.propose_packet(s, chapter=ch)
        with pytest.raises(HTTPException) as exc:
            await packets.approve_packet(ch.id, s)
        assert exc.value.status_code == 409


async def test_open_questions_block_approval_until_resolved(db_factory, monkeypatch):
    # green author + an open question -> derived yellow, and approval is gated until it's cleared.
    _patch(monkeypatch, _packet(open_q=["who is present during the hijack?"]), _qa())
    async with db_factory() as s:
        ch = await _seed_chapter(s)
        row = await packet_pipeline.propose_packet(s, chapter=ch)
        assert row.confidence == "yellow"
        with pytest.raises(HTTPException) as exc:
            await packets.approve_packet(ch.id, s)
        assert exc.value.status_code == 409

        await packets.update_packet(ch.id, PacketUpdateIn(open_questions={"items": []}), s)
        approved = await packets.approve_packet(ch.id, s)
        assert approved.status == PacketStatus.APPROVED


async def test_clean_green_packet_approves(db_factory, monkeypatch):
    _patch(monkeypatch, _packet(), _qa())
    async with db_factory() as s:
        ch = await _seed_chapter(s)
        await packet_pipeline.propose_packet(s, chapter=ch)
        approved = await packets.approve_packet(ch.id, s)
        assert approved.status == PacketStatus.APPROVED


# --- a failed re-propose must not wipe an approved packet -----------------------------------------


# --- deterministic roster-consistency validation (repair tasks, drafting stays reachable) ----------


async def test_double_bucketed_roster_is_repair_task_not_block(db_factory, monkeypatch):
    """A character listed in both characters_present and characters_absent is a fixable data-entry
    contradiction: the packet stays PROPOSED (drafting reachable), QA still runs, and the violation is
    persisted as a machine-readable repair task that gates final export only."""
    packet = {
        **_packet(),
        "characters_present": ["Mara (present, unidentified until Ch2)"],
        "characters_absent": ["Mara"],
    }
    qa_called = False

    async def author_ok(**kwargs):
        return packet

    async def qa_runs(_packet, **kwargs):
        nonlocal qa_called
        qa_called = True
        return _qa()

    monkeypatch.setattr(author_mod, "author_packet", author_ok)
    monkeypatch.setattr(qa_mod, "qa_packet", qa_runs)
    async with db_factory() as s:
        ch = await _seed_chapter(s)
        row = await packet_pipeline.propose_packet(s, chapter=ch)
        assert row.status == PacketStatus.PROPOSED
        assert qa_called is True
        repairs = [v for v in row.qa_warnings.get("violations", []) if v["kind"] == "roster_double_bucketed"]
        assert repairs and repairs[0]["severity"] == "repair"
        assert repairs[0]["blocks_drafting"] is False
        assert repairs[0]["blocks_final_export"] is True


async def test_clean_roster_still_runs_qa_and_proposes(db_factory, monkeypatch):
    """The flip side: a packet with no roster contradiction proceeds through QA as before."""
    _patch(monkeypatch, _packet(), _qa())
    async with db_factory() as s:
        ch = await _seed_chapter(s)
        row = await packet_pipeline.propose_packet(s, chapter=ch)
        assert row.status == PacketStatus.PROPOSED
        assert "violations" not in row.qa_warnings


async def test_failed_repropose_preserves_approved(db_factory, monkeypatch):
    _patch(monkeypatch, _packet(), _qa())
    async with db_factory() as s:
        ch = await _seed_chapter(s)
        await packet_pipeline.propose_packet(s, chapter=ch)
        approved = await packets.approve_packet(ch.id, s)
        approved_id = approved.id

        # now the author fails on a re-propose — the approved packet must survive untouched
        async def author_none(**kwargs):
            return None

        monkeypatch.setattr(author_mod, "author_packet", author_none)
        row = await packet_pipeline.propose_packet(s, chapter=ch)
        assert row.status == PacketStatus.APPROVED
        assert row.id == approved_id
