"""Unit tests for chapter-packet approval policy (no database)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from dominion.shared.enums import PacketConfidence, PacketStatus, PacketVerdict
from dominion.shared.models import ChapterPacket
from dominion.workers.packet import approval_policy


def _packet(**kwargs: object) -> ChapterPacket:
    defaults = dict(
        id=uuid.uuid4(),
        book_id=uuid.uuid4(),
        chapter_id=uuid.uuid4(),
        status=PacketStatus.PROPOSED,
        confidence=PacketConfidence.GREEN,
        body={},
        open_questions={"items": []},
        created_at=datetime.now(UTC),
    )
    defaults.update(kwargs)
    return ChapterPacket(**defaults)  # type: ignore[arg-type]


def test_can_approve_blocked():
    p = _packet(status=PacketStatus.BLOCKED)
    refusal = approval_policy.can_approve(p)
    assert refusal is not None
    assert "blocked" in refusal.detail


def test_can_approve_red_confidence():
    p = _packet(confidence=PacketConfidence.RED)
    refusal = approval_policy.can_approve(p)
    assert refusal is not None
    assert "red-confidence" in refusal.detail


def test_can_approve_open_questions():
    p = _packet(open_questions={"items": ["who is the traitor?"]})
    refusal = approval_policy.can_approve(p)
    assert refusal is not None
    assert "open questions" in refusal.detail


def test_can_approve_clean_proposed():
    assert approval_policy.can_approve(_packet()) is None


def test_status_from_qa_block_drafting():
    conf, status = approval_policy.status_from_qa(
        {"confidence": "green", "open_questions": []},
        {"verdict": PacketVerdict.BLOCK_DRAFTING, "issues": []},
    )
    assert conf == PacketConfidence.RED
    assert status == PacketStatus.BLOCKED


def test_status_from_qa_revise_required_stays_proposed():
    conf, status = approval_policy.status_from_qa(
        {"confidence": "green", "open_questions": []},
        {"verdict": PacketVerdict.REVISE_REQUIRED, "issues": []},
    )
    assert conf == PacketConfidence.RED
    assert status == PacketStatus.PROPOSED


def test_can_derive_scene_packets_requires_approved():
    p = _packet(status=PacketStatus.PROPOSED)
    refusal = approval_policy.can_derive_scene_packets(p)
    assert refusal is not None
    assert approval_policy.can_derive_scene_packets(None) is not None
    assert approval_policy.can_derive_scene_packets(_packet(status=PacketStatus.APPROVED)) is None


def test_enrich_packet_out_can_approve():
    p = _packet()
    out = approval_policy.enrich_packet_out(p)
    assert out.can_approve is True
    assert out.approval_blockers == []

    blocked = _packet(status=PacketStatus.BLOCKED)
    out2 = approval_policy.enrich_packet_out(blocked)
    assert out2.can_approve is False
    assert out2.approval_blockers
