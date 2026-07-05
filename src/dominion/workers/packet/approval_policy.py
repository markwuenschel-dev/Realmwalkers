"""Chapter-packet approval and derive gates — single source for routers and workers."""

from __future__ import annotations

from typing import Any

from dominion.shared.enums import PacketConfidence, PacketStatus, PacketVerdict
from dominion.shared.models import ChapterPacket
from dominion.shared.schemas import PacketOut
from dominion.workers.gates import GateRefusal, refusal_reasons

_CONF_ORDER = {PacketConfidence.GREEN: 0, PacketConfidence.YELLOW: 1, PacketConfidence.RED: 2}
_VERDICT_FLOOR = {
    PacketVerdict.APPROVE: PacketConfidence.GREEN,
    PacketVerdict.APPROVE_WARN: PacketConfidence.YELLOW,
    PacketVerdict.REVISE_REQUIRED: PacketConfidence.RED,
    PacketVerdict.BLOCK_DRAFTING: PacketConfidence.RED,
}


def _worst(a: PacketConfidence, b: PacketConfidence) -> PacketConfidence:
    return a if _CONF_ORDER[a] >= _CONF_ORDER[b] else b


def _as_confidence(value: Any) -> PacketConfidence:
    try:
        return PacketConfidence(str(value).strip().lower())
    except ValueError:
        return PacketConfidence.YELLOW


def _author_open_questions(packet: dict[str, Any]) -> list[str]:
    oq = packet.get("open_questions")
    return [str(q).strip() for q in oq if str(q).strip()] if isinstance(oq, list) else []


def open_question_items(packet: ChapterPacket) -> list[object]:
    oq = packet.open_questions or {}
    items = oq.get("items") if isinstance(oq, dict) else None
    return items if isinstance(items, list) else []


def _warnings(packet: ChapterPacket) -> dict[str, Any]:
    return packet.qa_warnings if isinstance(packet.qa_warnings, dict) else {}


def _body(packet: ChapterPacket) -> dict[str, Any]:
    return packet.body if isinstance(packet.body, dict) else {}


def resolve_blocked_reason(packet: ChapterPacket) -> str | None:
    if packet.status != PacketStatus.BLOCKED:
        return None
    warnings = _warnings(packet)
    body = _body(packet)
    if reason := warnings.get("blocked_reason"):
        return str(reason)
    if reason := body.get("blocked_reason"):
        return str(reason)
    return "Chapter packet is blocked but no blocked_reason was recorded"


def resolve_blocker_source(packet: ChapterPacket) -> str | None:
    if packet.status != PacketStatus.BLOCKED:
        return None
    source = _warnings(packet).get("blocker_source")
    return str(source) if source else None


def resolve_blocker_kind(packet: ChapterPacket) -> str | None:
    if packet.status != PacketStatus.BLOCKED:
        return None
    kind = _warnings(packet).get("blocker_kind")
    return str(kind) if kind else None


def resolve_recovery_actions(packet: ChapterPacket) -> list[str]:
    actions = _warnings(packet).get("recovery_actions")
    if not isinstance(actions, list):
        return []
    return [str(action).strip() for action in actions if str(action).strip()]


def resolve_blocker_diagnostics(packet: ChapterPacket) -> dict[str, Any] | None:
    diagnostics = _warnings(packet).get("blocker_diagnostics")
    return diagnostics if isinstance(diagnostics, dict) else None


def can_approve(packet: ChapterPacket) -> GateRefusal | None:
    """Refuse approval only on true blockers: a BLOCKED packet (deterministic block-severity failure or
    a failed agent call) or unresolved open questions (an explicit human decision point). Confidence and
    QA verdicts are LLM signals — advisory, shown to the human, never a gate. A packet carrying only
    repair/warn issues is approvable (approve-with-repairs; the repairs still gate final export)."""
    if packet.status == PacketStatus.BLOCKED:
        return GateRefusal(resolve_blocked_reason(packet) or "packet is blocked")
    if open_question_items(packet):
        return GateRefusal("resolve the packet's open questions first")
    return None


def approval_blockers(packet: ChapterPacket) -> list[str]:
    return refusal_reasons(can_approve(packet))


def approval_state(packet: ChapterPacket) -> tuple[str, list[str]]:
    """(state, reasons) for the DTO — every non-approvable state carries a human-readable reason, so the
    UI never shows a greyed Approve with nothing to say. Distinct from can_approve(), which the approve
    endpoints use as the real gate (and which stays idempotent for already-approved rows)."""
    if packet.status == PacketStatus.BLOCKED:
        return "blocked", [resolve_blocked_reason(packet) or "packet is blocked"]
    if open_question_items(packet):
        return "open_questions", ["resolve the packet's open questions first"]
    if packet.status == PacketStatus.APPROVED:
        return "already_approved", [
            "Packet already approved — edit the body or re-propose to make changes, then approve again."
        ]
    return "approvable", []


def can_derive_scene_packets(chapter_packet: ChapterPacket | None) -> GateRefusal | None:
    if chapter_packet is None or chapter_packet.status != PacketStatus.APPROVED:
        return GateRefusal("no approved chapter packet — approve the chapter packet first")
    return None


def status_from_qa(packet_body: dict[str, Any], qa: dict[str, Any]) -> tuple[PacketConfidence, PacketStatus]:
    """Confidence + status from author self-assessment and QA verdict (propose path). QA is an LLM
    attacker — its verdict shapes CONFIDENCE (a signal for the human), never status: only deterministic
    validation may block drafting, so even BLOCK_DRAFTING yields a proposed, red-confidence packet whose
    issues ride along as repair tasks."""
    verdict: PacketVerdict = qa["verdict"]
    conf = _worst(_as_confidence(packet_body.get("confidence")), _VERDICT_FLOOR[verdict])
    has_flags = bool(_author_open_questions(packet_body)) or bool(qa.get("issues"))
    if conf == PacketConfidence.GREEN and has_flags:
        conf = PacketConfidence.YELLOW
    return conf, PacketStatus.PROPOSED


def enrich_packet_out(row: ChapterPacket) -> PacketOut:
    state, blockers = approval_state(row)
    out = PacketOut.model_validate(row)
    return out.model_copy(
        update={
            "can_approve": state == "approvable",
            "approval_state": state,
            "approval_blockers": blockers,
            "blocked_reason": resolve_blocked_reason(row),
            "blocker_source": resolve_blocker_source(row),
            "blocker_kind": resolve_blocker_kind(row),
            "recovery_actions": resolve_recovery_actions(row),
            "blocker_diagnostics": resolve_blocker_diagnostics(row),
        }
    )
