"""Characterization + parity for the approval PROJECTION layer (C2).

Pins the EXACT current outputs of the five projection functions (`can_approve`, `approval_blockers`,
`approval_state`, `resolve_blocked_reason`, `enrich_*_out`) for both tiers and every state, so the
composed `approval_projection` kernel refactor stays byte-identical. No database.

Three observable contracts must stay independent (they diverge):
  - `can_approve()` — the endpoint gate's ACTION refusal text.
  - `approval_blockers()` — the standalone display list.
  - the enriched DTO's `approval_state` reasons / `approval_blockers` field (from `approval_state`).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from dominion.shared.enums import (
    PacketConfidence,
    PacketStatus,
    ScenePacketStatus,
    ScenePacketVerdict,
)
from dominion.shared.models import ChapterPacket, ScenePacket
from dominion.workers.packet import approval_policy as cp
from dominion.workers.scene_packet import approval_policy as sp

CHAPTER_APPROVED_COPY = "Packet already approved — edit the body or re-propose to make changes, then approve again."
CHAPTER_NO_REASON = "Chapter packet is blocked but no blocked_reason was recorded"
CHAPTER_OQ = "resolve the packet's open questions first"
SCENE_APPROVED_COPY = "Scene packet already approved — edit or re-derive to propose changes, then approve again."
SCENE_NO_REASON = "Scene packet is blocked but no blocked_reason was recorded"
SCENE_BLOCKED_ACTION = "scene packet is blocked — re-derive or edit first"
SCENE_RATE_ACTION = (
    "scene packet derive was rate limited by the provider (transient) — retry derive, or re-run QA "
    "if the contract body survived"
)


def _cp(**kw: object) -> ChapterPacket:
    d = dict(
        id=uuid.uuid4(),
        book_id=uuid.uuid4(),
        chapter_id=uuid.uuid4(),
        status=PacketStatus.PROPOSED,
        confidence=PacketConfidence.GREEN,
        body={},
        open_questions={"items": []},
        created_at=datetime.now(UTC),
    )
    d.update(kw)
    return ChapterPacket(**d)  # type: ignore[arg-type]


def _sp(**kw: object) -> ScenePacket:
    d = dict(
        id=uuid.uuid4(),
        book_id=uuid.uuid4(),
        chapter_id=uuid.uuid4(),
        chapter_packet_id=uuid.uuid4(),
        scene_no=1,
        status=ScenePacketStatus.PROPOSED,
        body={},
        qa_verdict=ScenePacketVerdict.APPROVE.value,
        qa_warnings={"residual_risks": [], "issues": []},
        created_at=datetime.now(UTC),
    )
    d.update(kw)
    return ScenePacket(**d)  # type: ignore[arg-type]


def _refusal(r) -> str | None:
    return None if r is None else r.detail


# ------------------------------------------------------------------ chapter tier
def test_chapter_blocked_no_reason():
    p = _cp(status=PacketStatus.BLOCKED)
    assert _refusal(cp.can_approve(p)) == CHAPTER_NO_REASON
    assert cp.approval_blockers(p) == [CHAPTER_NO_REASON]
    assert cp.approval_state(p) == ("blocked", [CHAPTER_NO_REASON])
    assert cp.resolve_blocked_reason(p) == CHAPTER_NO_REASON
    out = cp.enrich_packet_out(p)
    assert (out.can_approve, out.approval_state, out.approval_blockers, out.blocked_reason) == (
        False,
        "blocked",
        [CHAPTER_NO_REASON],
        CHAPTER_NO_REASON,
    )
    assert (out.blocker_source, out.blocker_kind, out.recovery_actions, out.blocker_diagnostics) == (
        None,
        None,
        [],
        None,
    )


def test_chapter_blocked_with_persisted_extras():
    p = _cp(
        status=PacketStatus.BLOCKED,
        qa_warnings={
            "blocked_reason": "roster double-bucketed",
            "blocker_source": "validation",
            "blocker_kind": "roster",
            "recovery_actions": ["fix the roster", " "],
            "blocker_diagnostics": {"n": 2},
        },
    )
    assert _refusal(cp.can_approve(p)) == "roster double-bucketed"
    assert cp.approval_blockers(p) == ["roster double-bucketed"]
    out = cp.enrich_packet_out(p)
    assert out.blocked_reason == "roster double-bucketed"
    assert (out.blocker_source, out.blocker_kind) == ("validation", "roster")
    assert out.recovery_actions == ["fix the roster"]
    assert out.blocker_diagnostics == {"n": 2}


def test_chapter_open_questions():
    p = _cp(open_questions={"items": ["who?"]})
    assert _refusal(cp.can_approve(p)) == CHAPTER_OQ
    assert cp.approval_blockers(p) == [CHAPTER_OQ]
    assert cp.approval_state(p) == ("open_questions", [CHAPTER_OQ])
    assert cp.resolve_blocked_reason(p) is None
    out = cp.enrich_packet_out(p)
    assert (out.can_approve, out.approval_state, out.approval_blockers, out.blocked_reason) == (
        False,
        "open_questions",
        [CHAPTER_OQ],
        None,
    )


def test_chapter_already_approved_standalone_vs_display_diverge():
    p = _cp(status=PacketStatus.APPROVED)
    assert cp.can_approve(p) is None  # gate stays idempotent
    assert cp.approval_blockers(p) == []  # standalone: empty
    assert cp.approval_state(p) == ("already_approved", [CHAPTER_APPROVED_COPY])  # display: the copy
    assert cp.resolve_blocked_reason(p) is None
    out = cp.enrich_packet_out(p)
    assert (out.can_approve, out.approval_state, out.approval_blockers, out.blocked_reason) == (
        False,
        "already_approved",
        [CHAPTER_APPROVED_COPY],
        None,
    )


def test_chapter_approvable():
    p = _cp()
    assert cp.can_approve(p) is None
    assert cp.approval_blockers(p) == []
    assert cp.approval_state(p) == ("approvable", [])
    assert cp.resolve_blocked_reason(p) is None
    out = cp.enrich_packet_out(p)
    assert (out.can_approve, out.approval_state, out.approval_blockers) == (True, "approvable", [])


# ------------------------------------------------------------------ scene tier
def test_scene_blocked_action_text_differs_from_display_reason():
    p = _sp(status=ScenePacketStatus.BLOCKED, qa_warnings={"blocked_reason": "author returned thin body"})
    assert _refusal(sp.can_approve(p)) == SCENE_BLOCKED_ACTION  # fixed action text
    assert sp.approval_blockers(p) == ["author returned thin body"]  # resolved display reason
    assert sp.approval_state(p) == ("blocked", ["author returned thin body"])
    assert sp.resolve_blocked_reason(p) == "author returned thin body"
    out = sp.enrich_scene_packet_out(p)
    assert (out.can_approve, out.approval_state, out.approval_blockers, out.blocked_reason) == (
        False,
        "blocked",
        ["author returned thin body"],
        "author returned thin body",
    )


def test_scene_blocked_no_reason_fallback():
    p = _sp(status=ScenePacketStatus.BLOCKED, qa_warnings=None)
    assert _refusal(sp.can_approve(p)) == SCENE_BLOCKED_ACTION
    assert sp.approval_blockers(p) == [SCENE_NO_REASON]
    assert sp.resolve_blocked_reason(p) == SCENE_NO_REASON


def test_scene_rate_limited():
    p = _sp(
        status=ScenePacketStatus.RATE_LIMITED,
        qa_verdict=None,
        qa_warnings={
            "blocked_reason": "Rate limited by provider during scene author (429).",
            "blocker_source": "rate_limit",
        },
    )
    assert _refusal(sp.can_approve(p)) == SCENE_RATE_ACTION
    assert sp.approval_blockers(p) == ["Rate limited by provider during scene author (429)."]
    assert sp.approval_state(p) == ("rate_limited", ["Rate limited by provider during scene author (429)."])
    out = sp.enrich_scene_packet_out(p)
    assert (out.can_approve, out.approval_state, out.blocked_reason, out.blocker_source) == (
        False,
        "rate_limited",
        "Rate limited by provider during scene author (429).",
        "rate_limit",
    )


def test_scene_stale_is_approvable_two_axis():
    p = _sp(status=ScenePacketStatus.STALE, stale_reason="upstream changed")
    assert sp.can_approve(p) is None  # approvable axis
    assert sp.approval_blockers(p) == []
    assert sp.approval_state(p) == ("approvable", [])
    assert sp.resolve_blocked_reason(p) is None
    out = sp.enrich_scene_packet_out(p)
    assert (out.can_approve, out.approval_state, out.approval_blockers, out.blocker_source) == (
        True,
        "approvable",
        [],
        None,
    )


def test_scene_already_approved_standalone_vs_display_diverge():
    p = _sp(status=ScenePacketStatus.APPROVED)
    assert sp.can_approve(p) is None
    assert sp.approval_blockers(p) == []  # standalone: empty
    assert sp.approval_state(p) == ("already_approved", [SCENE_APPROVED_COPY])  # display: the copy
    assert sp.resolve_blocked_reason(p) is None
    out = sp.enrich_scene_packet_out(p)
    assert (out.can_approve, out.approval_state, out.approval_blockers, out.blocker_source) == (
        False,
        "already_approved",
        [SCENE_APPROVED_COPY],
        None,
    )


def test_scene_approvable():
    p = _sp()
    assert sp.can_approve(p) is None
    assert sp.approval_blockers(p) == []
    assert sp.approval_state(p) == ("approvable", [])
    out = sp.enrich_scene_packet_out(p)
    assert (out.can_approve, out.approval_state, out.approval_blockers) == (True, "approvable", [])


# ------------------------------------------------------------------ import-compat smoke
def test_module_level_projection_api_still_imports():
    for name in ("can_approve", "approval_blockers", "approval_state", "resolve_blocked_reason", "enrich_packet_out"):
        assert callable(getattr(cp, name))
    for name in (
        "can_approve",
        "approval_blockers",
        "approval_state",
        "resolve_blocked_reason",
        "enrich_scene_packet_out",
    ):
        assert callable(getattr(sp, name))
    # The scene facade re-exports only these two.
    from dominion.workers.scene_packet import can_approve as facade_can_approve
    from dominion.workers.scene_packet import enrich_scene_packet_out as facade_enrich

    assert callable(facade_can_approve) and callable(facade_enrich)
