"""Scene-packet approval, QA, derive-status, and drafting gates.

REVISE_REQUIRED asymmetry (intentional):
  * After derive / QA re-run: packet stays `proposed` so the human can fix the contract.
  * Human approve: REVISE_REQUIRED blocks approval (same as BLOCK_DRAFTING for approval gate).
"""

from __future__ import annotations

from typing import Any, Literal

from dominion.shared.enums import ScenePacketStatus, ScenePacketVerdict
from dominion.shared.models import ScenePacket
from dominion.shared.schemas import ScenePacketOut
from dominion.workers.context.types import ScenePacketRequiredError
from dominion.workers.gates import GateRefusal
from dominion.workers.scene_packet.parse import valid_scene_packet_body

_BLOCKING_VERDICTS = {ScenePacketVerdict.BLOCK_DRAFTING, ScenePacketVerdict.REVISE_REQUIRED}

BlockerSource = Literal["author", "qa", "derive", "unknown"]

_STALE_GATE_RECONCILIATION = (
    "QA now approves, but the packet remains blocked from an earlier gate. Re-run derive or edit/reconcile the packet."
)
_NO_BLOCKED_REASON = "Scene packet is blocked but no blocked_reason was recorded"


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


def first_blocking_qa_reason_or_default(result: dict[str, Any]) -> str:
    """First block-severity issue detail from a QA result, or the default QA block message."""
    issues = result.get("issues")
    if isinstance(issues, list):
        for i in issues:
            if isinstance(i, dict) and i.get("severity") == "block":
                kind = i.get("kind")
                detail = i.get("detail") or "unspecified"
                return f"Blocking issue{f' ({kind})' if kind else ''}: {detail}"
    return "scene packet QA blocked drafting"


def resolve_blocked_reason(packet: ScenePacket) -> str | None:
    """Resolve a human-readable blocked reason from persisted fields and QA state."""
    if packet.status != ScenePacketStatus.BLOCKED:
        return None
    warnings = packet.qa_warnings if isinstance(packet.qa_warnings, dict) else {}
    body = packet.body if isinstance(packet.body, dict) else {}
    if reason := warnings.get("blocked_reason"):
        return str(reason)
    if reason := body.get("blocked_reason"):
        return str(reason)
    qa_reasons = blocking_qa_reasons(packet)
    if qa_reasons:
        return qa_reasons[0]
    return _NO_BLOCKED_REASON


def infer_blocker_source(packet: ScenePacket, reason: str | None) -> BlockerSource:
    """Classify which gate blocked the packet (computed at response time, not persisted)."""
    if packet.status != ScenePacketStatus.BLOCKED:
        return "unknown"
    if not valid_scene_packet_body(packet.body):
        return "author"
    if packet.qa_verdict == ScenePacketVerdict.BLOCK_DRAFTING.value:
        return "qa"
    issues = (packet.qa_warnings or {}).get("issues") if isinstance(packet.qa_warnings, dict) else None
    if isinstance(issues, list) and any(isinstance(i, dict) and i.get("severity") == "block" for i in issues):
        return "qa"
    if packet.qa_verdict in {
        ScenePacketVerdict.APPROVE.value,
        ScenePacketVerdict.APPROVE_WARN.value,
    }:
        return "derive"
    return "unknown"


def can_approve(packet: ScenePacket) -> GateRefusal | None:
    if packet.status == ScenePacketStatus.BLOCKED:
        return GateRefusal("scene packet is blocked — re-derive or edit first")
    if has_blocking_qa(packet):
        return GateRefusal("scene packet QA blocks drafting — resolve first")
    return None


def approval_blockers(packet: ScenePacket) -> list[str]:
    if packet.status == ScenePacketStatus.BLOCKED:
        return [resolve_blocked_reason(packet) or "scene packet is blocked — re-derive or edit first"]
    if packet.status != ScenePacketStatus.PROPOSED:
        return []
    return blocking_qa_reasons(packet)


def is_approvable_for_batch(packet: ScenePacket) -> bool:
    return can_approve(packet) is None


def status_after_author_qa(
    body: dict[str, Any] | None,
    qa: dict[str, Any] | None,
    error_detail: str | None = None,
) -> tuple[str, str | None]:
    """(status, blocked_reason). Fail closed on thin body or unusable QA."""
    if not valid_scene_packet_body(body):
        return ScenePacketStatus.BLOCKED, (error_detail or "scene packet author returned an incomplete body")
    if qa is None:
        return ScenePacketStatus.BLOCKED, (error_detail or "scene packet QA returned no usable verdict")
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

    existing_reason = resolve_blocked_reason(row)
    existing_source = infer_blocker_source(row, existing_reason)

    row.qa_verdict = result["verdict"]
    qa_warnings: dict[str, Any] = {
        "residual_risks": result["residual_risks"],
        "issues": result["issues"],
    }
    if result["verdict"] == ScenePacketVerdict.BLOCK_DRAFTING:
        row.status = ScenePacketStatus.BLOCKED
        qa_warnings["blocked_reason"] = first_blocking_qa_reason_or_default(result)
    elif row.status == ScenePacketStatus.BLOCKED:
        if existing_source in {"author", "derive"} and existing_reason:
            qa_warnings["blocked_reason"] = existing_reason
        else:
            qa_warnings["blocked_reason"] = _STALE_GATE_RECONCILIATION
    row.qa_warnings = qa_warnings


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
    reason = resolve_blocked_reason(row) if row.status == ScenePacketStatus.BLOCKED else None
    source = infer_blocker_source(row, reason) if reason else None
    out = ScenePacketOut.model_validate(row)
    return out.model_copy(
        update={
            "can_approve": row.status == ScenePacketStatus.PROPOSED and not blockers,
            "approval_blockers": blockers,
            "blocked_reason": reason,
            "blocker_source": source,
        }
    )
