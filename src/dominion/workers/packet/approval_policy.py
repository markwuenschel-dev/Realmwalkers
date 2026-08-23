"""Chapter-packet approval and derive gates — single source for routers and workers."""

from __future__ import annotations

from typing import Any

from dominion.shared.enums import PacketConfidence, PacketStatus, PacketVerdict
from dominion.shared.models import ChapterPacket
from dominion.shared.schemas import PacketOut
from dominion.workers.approval_projection import ApprovalPolicy, project
from dominion.workers.gates import GateRefusal
from dominion.workers.packet import open_questions as open_questions_policy

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
    """THE canonical open-questions gate reader (#277). The only place this column becomes a gate
    decision — enforced by `tests/test_issue223_fork3b_authorization_seam_guard.py`.

    Returns the items that are still UNRESOLVED, not every item. Before #277 this returned the raw
    `items[]` and `resolved[]` was never consulted at all, so emptying the list cleared the gate whether
    or not anything had been ruled — and a malformed value (`{"items": "x"}`) fell through the
    `isinstance` check to `[]` and OPENED approval.

    Fails closed on both counts now: a malformed value raises inside `normalize`, which is caught here
    and reported as a single un-clearable blocking item rather than an empty list, because a gate that
    cannot parse its own state must never read as "nothing to resolve".
    """
    try:
        normalized = open_questions_policy.normalize(packet.open_questions, mint=False)
    except open_questions_policy.OpenQuestionsInvalid as exc:
        return [{"text": f"open questions are malformed and cannot be evaluated: {exc}", "legacy": True}]
    return list(open_questions_policy.unresolved_items(normalized))


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


# The chapter tier's approval-projection policy. The kernel (workers/approval_projection) owns the
# precedence (held → open-questions → already-approved → approvable) and the three independent reason
# contracts; this policy supplies only the tier's classification, strings, and DTO extras.
_POLICY = ApprovalPolicy(
    # SUPERSEDED is held, not approvable (#261). Without it a superseded row with no open questions falls
    # through the precedence chain to the terminal "approvable" branch and `can_approve` returns True for a
    # terminal historical record — so the DTO would invite the author to re-approve a contract that has
    # already been replaced. There is no reader that feeds a superseded row into this projection today
    # (`_latest` takes the newest row and the successor is always newer), so this closes a LATENT trap in
    # the one module that interprets `status` rather than a live bug — which is exactly when it is cheap.
    held_state=lambda p: (
        "blocked"
        if p.status == PacketStatus.BLOCKED
        else ("superseded" if p.status == PacketStatus.SUPERSEDED else None)
    ),
    resolve_reason=resolve_blocked_reason,
    held_action_text=lambda p, state: (
        "This packet was superseded by an approved amendment — it is a historical record and cannot be "
        "approved. Open the chapter's current contract instead."
        if state == "superseded"
        else (resolve_blocked_reason(p) or "packet is blocked")
    ),
    extra_gate=lambda p: (
        ("open_questions", ["resolve the packet's open questions first"]) if open_question_items(p) else None
    ),
    is_approved=lambda p: p.status == PacketStatus.APPROVED,
    approved_copy="Packet already approved — edit the body or re-propose to make changes, then approve again.",
    dto_extras=lambda p, _reason: {
        "blocker_source": resolve_blocker_source(p),
        "blocker_kind": resolve_blocker_kind(p),
        "recovery_actions": resolve_recovery_actions(p),
        "blocker_diagnostics": resolve_blocker_diagnostics(p),
    },
)


def can_approve(packet: ChapterPacket) -> GateRefusal | None:
    """The real approve gate. Refuses only true blockers — a BLOCKED packet or unresolved open questions
    (an explicit human decision point); confidence/QA verdicts are advisory, never a gate. Stays
    idempotent for already-approved rows. Delegates to the shared projection kernel."""
    refusal = project(packet, _POLICY).gate_refusal
    return GateRefusal(refusal) if refusal is not None else None


def approval_blockers(packet: ChapterPacket) -> list[str]:
    return project(packet, _POLICY).standalone_blockers


def approval_state(packet: ChapterPacket) -> tuple[str, list[str]]:
    """(state, reasons) for the DTO — every non-approvable state carries a human-readable reason, so the
    UI never shows a greyed Approve with nothing to say. Distinct from can_approve() (the real gate,
    idempotent on approved rows). Delegates to the shared projection kernel."""
    pr = project(packet, _POLICY)
    return pr.state, pr.display_reasons


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


def open_questions_state_token(packet: ChapterPacket) -> str:
    """The expected-state token a client must echo back when it writes `open_questions` (clause B).

    Computed on read, never stored: a persisted digest drifts from the JSONB it describes the moment one
    writer updates either without the other. A malformed value still yields a stable token, so a client
    holding a broken row can still be told its write is stale rather than getting a 500."""
    try:
        normalized = open_questions_policy.normalize(packet.open_questions, mint=False)
    except open_questions_policy.OpenQuestionsInvalid:
        normalized = {"items": [], "resolved": [], "malformed": True}
    return open_questions_policy.state_token(normalized)


def enrich_packet_out(row: ChapterPacket) -> PacketOut:
    pr = project(row, _POLICY)
    out = PacketOut.model_validate(row)
    return out.model_copy(
        update={
            "can_approve": pr.state == "approvable",
            "approval_state": pr.state,
            "approval_blockers": pr.display_reasons,
            "blocked_reason": pr.blocked_reason,
            "open_questions_token": open_questions_state_token(row),
            **pr.extras,
        }
    )
