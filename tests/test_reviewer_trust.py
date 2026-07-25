"""ADR-0033 D5/D5b — scene-packet approval provenance and the reviewer-contract trust split.

Two invariants:

1. **Provenance exists and has one writer.** `status = approved` used to be identical whether a human or
   a policy set it, so "did a human read this contract?" was unanswerable. The single approval seam now
   records it, and no caller can inherit human provenance by omission (`source` has no default).
2. **A contract no human approved cannot silence a reviewer.** Its suppression fields never reach the
   reviewer's prompt. This is enforced in the PROJECTION, so it holds for every reviewer including ones
   not written yet — which is why these tests assert on the projected contract, not on a reviewer.
"""

from __future__ import annotations

import inspect

import pytest

from dominion.shared.enums import ScenePacketApprovalSource, ScenePacketStatus
from dominion.shared.models import Book, Chapter, ChapterPacket, ScenePacket
from dominion.workers import scene_packet as sp_pipeline
from dominion.workers.context.reviewer_trust import (
    SUPPRESSION_FIELDS,
    suppression_is_trusted,
    trusted_reviewer_contract,
)

POSITIVE = {"scene_job", "scene_type", "required_beats", "word_budget"}


def _contract() -> dict:
    return {
        "scene_job": "Marcus intercepts.",
        "scene_type": "combat",
        "required_beats": ["land the hit"],
        "word_budget": {"target": 1500},
        "forbidden_beats": ["Marcus uses his Aspect"],
        "reviewer_false_positive_traps": ["the missing tip source is intentional"],
        "reviewer_instructions": {"combat": ["track stamina"]},
    }


# --- the split (pure) -----------------------------------------------------------------------------


def test_human_approved_contract_is_passed_through_untouched():
    c = _contract()
    out = trusted_reviewer_contract(c, approval_source=ScenePacketApprovalSource.MANUAL_COMMAND.value)
    assert out == c


@pytest.mark.parametrize(
    "source",
    [
        None,  # never approved
        ScenePacketApprovalSource.AUTONOMOUS_POLICY.value,
        ScenePacketApprovalSource.LEGACY_UNCLASSIFIED.value,
        "something_invented_later",  # fails closed on an unknown provenance
    ],
)
def test_untrusted_contract_loses_every_suppression_field_and_keeps_every_positive_one(source):
    out = trusted_reviewer_contract(_contract(), approval_source=source)
    assert set(out) == POSITIVE
    for field in SUPPRESSION_FIELDS:
        # REMOVED, not blanked — lane.py's `if rc.get(...)` guards must skip it as they do an absent key.
        assert field not in out
    assert out["scene_job"] == "Marcus intercepts."
    assert out["required_beats"] == ["land the hit"]


def test_suppression_fields_are_exactly_the_three_that_can_silence_a_reviewer():
    # Pinned so adding a new contract key is a deliberate classification, not an accident. A key that can
    # suppress and is not listed here would silently reach an untrusted reviewer.
    assert SUPPRESSION_FIELDS == {"forbidden_beats", "reviewer_false_positive_traps", "reviewer_instructions"}


def test_only_manual_command_is_trusted():
    assert suppression_is_trusted(ScenePacketApprovalSource.MANUAL_COMMAND.value)
    for source in (None, "", "autonomous_policy", "legacy_unclassified", "human"):
        assert not suppression_is_trusted(source), source


# --- provenance is recorded, and cannot be inherited by omission ----------------------------------


def test_approval_source_has_no_default_on_the_seam_or_the_facade():
    """The same discipline `apply_repair_task(autonomous=...)` uses: a defaulted provenance would let a
    future autonomous approver claim a human read the contract simply by not saying otherwise."""
    for fn in (sp_pipeline.approve_scene_packet, sp_pipeline.approve_scene_packets):
        assert inspect.signature(fn).parameters["source"].default is inspect.Parameter.empty, fn.__name__


async def _proposed_packet(s) -> ScenePacket:
    book = Book(title="Realmwalkers")
    s.add(book)
    await s.flush()
    ch = Chapter(book_id=book.id, chapter_no=1, pov="Mara")
    s.add(ch)
    await s.flush()
    cp = ChapterPacket(book_id=book.id, chapter_id=ch.id, status="approved", body={"scene_seeds": []})
    s.add(cp)
    await s.flush()
    packet = ScenePacket(
        book_id=book.id,
        chapter_id=ch.id,
        chapter_packet_id=cp.id,
        scene_no=1,
        status=ScenePacketStatus.PROPOSED,
        body=_contract() | {"scene_no": 1},
    )
    s.add(packet)
    await s.flush()
    assert packet.approval_source is None  # never approved -> no provenance to claim
    return packet


async def test_the_approval_seam_records_provenance(db_factory):
    async with db_factory() as s:
        packet = await _proposed_packet(s)
        await sp_pipeline.approve_scene_packet(s, packet=packet, source=ScenePacketApprovalSource.MANUAL_COMMAND.value)
        await s.commit()
        got = await s.get(ScenePacket, packet.id)
        assert got.status == ScenePacketStatus.APPROVED
        assert got.approval_source == ScenePacketApprovalSource.MANUAL_COMMAND.value


async def test_an_autonomously_approved_packet_is_recorded_as_such(db_factory):
    """The provenance a driver would write. Nothing in production passes this yet — the point is that the
    seam CAN distinguish it, which is the fact D5's split needs and which did not exist before."""
    async with db_factory() as s:
        packet = await _proposed_packet(s)
        await sp_pipeline.approve_scene_packet(
            s, packet=packet, source=ScenePacketApprovalSource.AUTONOMOUS_POLICY.value
        )
        await s.commit()
        got = await s.get(ScenePacket, packet.id)
        assert got.approval_source == ScenePacketApprovalSource.AUTONOMOUS_POLICY.value
        assert not suppression_is_trusted(got.approval_source)  # so its suppression stays withheld


# --- end to end through the real context projection -----------------------------------------------


async def test_projection_withholds_suppression_from_an_autonomously_approved_contract(db_factory):
    """The invariant a reviewer relies on without knowing it: `ctx.reviewer_contract` is already filtered
    by the time any reviewer sees it, so `reviewers/lane.py` needs no rule and can't forget one."""
    from dominion.workers.context.contracts import load_scene_packet_fields

    async with db_factory() as s:
        book = Book(title="Realmwalkers")
        s.add(book)
        await s.flush()
        ch = Chapter(book_id=book.id, chapter_no=1, pov="Mara")
        s.add(ch)
        await s.flush()
        cp = ChapterPacket(book_id=book.id, chapter_id=ch.id, status="approved", body={"scene_seeds": []})
        s.add(cp)
        await s.flush()
        packet = ScenePacket(
            book_id=book.id,
            chapter_id=ch.id,
            chapter_packet_id=cp.id,
            scene_no=1,
            status=ScenePacketStatus.APPROVED,
            approval_source=ScenePacketApprovalSource.AUTONOMOUS_POLICY.value,
            body=_contract() | {"scene_no": 1},
        )
        s.add(packet)
        await s.flush()

        fields = await load_scene_packet_fields(s, packet.id)
        rc = fields.reviewer_contract
        assert rc["scene_job"] == "Marcus intercepts."  # positive survives
        assert "reviewer_false_positive_traps" not in rc  # a drifted contract cannot silence its detector
        assert "forbidden_beats" not in rc
        assert "reviewer_instructions" not in rc

        # Flip provenance to a human command and the same packet's traps come back.
        packet.approval_source = ScenePacketApprovalSource.MANUAL_COMMAND.value
        await s.flush()
        rc = (await load_scene_packet_fields(s, packet.id)).reviewer_contract
        assert rc["reviewer_false_positive_traps"] == ["the missing tip source is intentional"]
