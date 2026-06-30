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


def can_approve(packet: ChapterPacket) -> GateRefusal | None:
    if packet.status == PacketStatus.BLOCKED:
        return GateRefusal("packet is blocked — re-propose or edit it first")
    if packet.confidence == PacketConfidence.RED:
        return GateRefusal("red-confidence packet — resolve before approving")
    if open_question_items(packet):
        return GateRefusal("resolve the packet's open questions first")
    return None


def approval_blockers(packet: ChapterPacket) -> list[str]:
    return refusal_reasons(can_approve(packet))


def can_derive_scene_packets(chapter_packet: ChapterPacket | None) -> GateRefusal | None:
    if chapter_packet is None or chapter_packet.status != PacketStatus.APPROVED:
        return GateRefusal("no approved chapter packet — approve the chapter packet first")
    return None


def status_from_qa(packet_body: dict[str, Any], qa: dict[str, Any]) -> tuple[PacketConfidence, PacketStatus]:
    """Confidence + status from author self-assessment and QA verdict (propose path)."""
    verdict: PacketVerdict = qa["verdict"]
    conf = _worst(_as_confidence(packet_body.get("confidence")), _VERDICT_FLOOR[verdict])
    has_flags = bool(_author_open_questions(packet_body)) or bool(qa.get("issues"))
    if conf == PacketConfidence.GREEN and has_flags:
        conf = PacketConfidence.YELLOW
    status = PacketStatus.BLOCKED if verdict == PacketVerdict.BLOCK_DRAFTING else PacketStatus.PROPOSED
    return conf, status


def enrich_packet_out(row: ChapterPacket) -> PacketOut:
    blockers = approval_blockers(row)
    out = PacketOut.model_validate(row)
    return out.model_copy(
        update={
            "can_approve": row.status == PacketStatus.PROPOSED and not blockers,
            "approval_blockers": blockers,
        }
    )
