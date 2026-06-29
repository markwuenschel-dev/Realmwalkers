"""Scene-packet approval, QA, derive-status, and drafting gates.

REVISE_REQUIRED asymmetry (intentional):
  * After derive / QA re-run: packet stays `proposed` so the human can fix the contract.
  * Human approve: REVISE_REQUIRED blocks approval (same as BLOCK_DRAFTING for approval gate).
"""
from __future__ import annotations

from typing import Any

from dominion.shared.enums import ScenePacketStatus, ScenePacketVerdict
from dominion.shared.models import ScenePacket
from dominion.shared.schemas import ScenePacketOut
from dominion.workers.context.types import ScenePacketRequiredError
from dominion.workers.gates import GateRefusal
from dominion.workers.scene_packet.parse import valid_scene_packet_body

_BLOCKING_VERDICTS = {ScenePacketVerdict.BLOCK_DRAFTING, ScenePacketVerdict.REVISE_REQUIRED}


def has_blocking_qa(packet: ScenePacket) -> bool:
    if packet.qa_verdict in {v.value for v in _BLOCKING_VERDICTS}:
        return True
    issues = (packet.qa_warnings or {}).get("issues") if isinstance(packet.qa_warnings, dict) else None
    if isinstance(issues, list):
        return any(isinstance(i, dict) and i.get("severity") == "block" for i in issues)
    return False


def blocking_qa_reasons(packet: ScenePacket) -> list[str]:
    reasons: list[str] = []
    v = packet.qa_verdict
    if v == ScenePacketVerdict.BLOCK_DRAFTING.value:
        reasons.append("QA verdict: block drafting — no prose may be written from this packet.")
    elif v == ScenePacketVerdict.REVISE_REQUIRED.value:
        reasons.append("QA verdict: revise required — the contract must be fixed first.")
    issues = (packet.qa_warnings or {}).get("issues") if isinstance(packet.qa_warnings, dict) else None
    if isinstance(issues, list):
        for i in issues:
            if isinstance(i, dict) and i.get("severity") == "block":
                kind = i.get("kind")
                detail = i.get("detail") or "unspecified"
                reasons.append(f"Blocking issue{f' ({kind})' if kind else ''}: {detail}")
    return reasons


def can_approve(packet: ScenePacket) -> GateRefusal | None:
    if packet.status == ScenePacketStatus.BLOCKED:
        return GateRefusal("scene packet is blocked — re-derive or edit first")
    if has_blocking_qa(packet):
        return GateRefusal("scene packet QA blocks drafting — resolve first")
    return None


def approval_blockers(packet: ScenePacket) -> list[str]:
    if packet.status != ScenePacketStatus.PROPOSED:
        return []
    blockers: list[str] = []
    if packet.status == ScenePacketStatus.BLOCKED:
        blockers.append("scene packet is blocked — re-derive or edit first")
    blockers.extend(blocking_qa_reasons(packet))
    return blockers


def is_approvable_for_batch(packet: ScenePacket) -> bool:
    return can_approve(packet) is None


def status_after_author_qa(
    body: dict[str, Any] | None,
    qa: dict[str, Any] | None,
    error_detail: str | None = None,
) -> tuple[str, str | None]:
    """(status, blocked_reason). Fail closed on thin body or unusable QA."""
    if not valid_scene_packet_body(body):
        return ScenePacketStatus.BLOCKED, (
            error_detail or "scene packet author returned an incomplete body"
        )
    if qa is None:
        return ScenePacketStatus.BLOCKED, (
            error_detail or "scene packet QA returned no usable verdict"
        )
    if qa["verdict"] == ScenePacketVerdict.BLOCK_DRAFTING:
        return ScenePacketStatus.BLOCKED, "scene packet QA blocked drafting"
    return ScenePacketStatus.PROPOSED, None


def apply_qa_rerun(row: ScenePacket, result: dict[str, Any] | None) -> None:
    """Mutate row after a manual QA re-run (router endpoint). REVISE_REQUIRED stays proposed."""
    if result is None:
        row.qa_verdict = ScenePacketVerdict.BLOCK_DRAFTING
        row.qa_warnings = {"residual_risks": [], "blocked_reason": "QA returned no usable verdict"}
        row.status = ScenePacketStatus.BLOCKED
        return
    row.qa_verdict = result["verdict"]
    row.qa_warnings = {"residual_risks": result["residual_risks"], "issues": result["issues"]}
    if result["verdict"] == ScenePacketVerdict.BLOCK_DRAFTING:
        row.status = ScenePacketStatus.BLOCKED


def assert_draft_ready(packet: ScenePacket) -> None:
    """Raise ScenePacketRequiredError when drafting must not proceed."""
    if packet.status == ScenePacketStatus.STALE:
        raise ScenePacketRequiredError(
            f"scene packet {packet.id} is stale ({packet.stale_reason or 'inputs changed'}) — "
            "re-derive or re-approve it before drafting"
        )
    if packet.status != ScenePacketStatus.APPROVED:
        raise ScenePacketRequiredError(
            f"scene packet {packet.id} is {packet.status}, not approved — approve it before drafting"
        )


def enrich_scene_packet_out(row: ScenePacket) -> ScenePacketOut:
    blockers = approval_blockers(row)
    out = ScenePacketOut.model_validate(row)
    return out.model_copy(update={
        "can_approve": row.status == ScenePacketStatus.PROPOSED and not blockers,
        "approval_blockers": blockers,
    })
