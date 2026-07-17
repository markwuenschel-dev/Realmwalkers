"""Scene-packet approval, QA, derive-status, and drafting gates.

QA is advisory (writer-first): an LLM verdict — even BLOCK_DRAFTING — never blocks drafting, human
review, or approval. LLM issues are capped at `repair` severity at parse time and ride along as
machine-readable repair tasks that gate final export only. The only hard gates left are deterministic:
a thin/unusable author body, a block-severity contract violation, or QA failing to return a usable
verdict at all (fail closed on infrastructure, never on judgment).
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Literal

from dominion.shared.enums import ScenePacketStatus, ScenePacketVerdict
from dominion.shared.models import ApprovalBlocker, ScenePacket
from dominion.shared.schemas import ScenePacketOut
from dominion.workers.approval_projection import ApprovalPolicy, project
from dominion.workers.context.types import ScenePacketRequiredError
from dominion.workers.gates import GateRefusal
from dominion.workers.scene_packet.parse import valid_scene_packet_body

BlockerSource = Literal["author", "validation", "qa", "derive", "rate_limit", "unknown"]

# Blocker sources the derive persists on `qa_warnings["blocker_source"]`. "validation" is a deterministic
# draft-safety block (NOT a QA block) — the distinction the UI needs so it stops labeling both as QA.
# "rate_limit" is a provider 429 past its retries — transient infrastructure, never an author failure.
_PERSISTED_BLOCKER_SOURCES: frozenset[str] = frozenset({"author", "validation", "qa", "derive", "rate_limit"})

_STALE_GATE_RECONCILIATION = (
    "QA re-ran, but the packet remains blocked from an earlier gate. Re-run derive or edit/reconcile the packet."
)
_NO_BLOCKED_REASON = "Scene packet is blocked but no blocked_reason was recorded"


def blocking_qa_reasons(packet: ScenePacket) -> list[str]:
    """Human-readable reasons from a LEGACY QA-held block (rows persisted before QA became advisory).
    Only used to explain an already-BLOCKED row — never to gate anything."""
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


_HELD_STATUSES: frozenset[str] = frozenset({ScenePacketStatus.BLOCKED, ScenePacketStatus.RATE_LIMITED})


def resolve_blocked_reason(packet: ScenePacket) -> str | None:
    """Resolve a human-readable blocked reason from persisted fields and QA state."""
    if packet.status not in _HELD_STATUSES:
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
    """Classify which gate blocked the packet. The derive now PERSISTS this on
    `qa_warnings["blocker_source"]`, so prefer that authoritative value; the heuristics below remain only
    as a fallback for packets derived before that field existed."""
    if packet.status not in _HELD_STATUSES:
        return "unknown"
    if packet.status == ScenePacketStatus.RATE_LIMITED:
        return "rate_limit"
    warnings = packet.qa_warnings if isinstance(packet.qa_warnings, dict) else {}
    persisted = warnings.get("blocker_source")
    if isinstance(persisted, str) and persisted in _PERSISTED_BLOCKER_SOURCES:
        return persisted  # type: ignore[return-value]
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


_SCENE_BLOCKED_ACTION = "scene packet is blocked — re-derive or edit first"
_SCENE_RATE_LIMIT_ACTION = (
    "scene packet derive was rate limited by the provider (transient) — retry derive, or re-run QA "
    "if the contract body survived"
)

# The scene tier's approval-projection policy. The kernel (workers/approval_projection) owns the
# precedence and the three independent reason contracts; this policy supplies the tier's held
# classification, action strings, approved copy, and DTO blocker_source. STALE is NOT held here — it is
# approvable (its remedy IS re-approve); draft-readiness is a separate axis (assert_draft_ready).
_POLICY = ApprovalPolicy(
    held_state=lambda p: (
        "rate_limited"
        if p.status == ScenePacketStatus.RATE_LIMITED
        else "blocked"
        if p.status == ScenePacketStatus.BLOCKED
        else None
    ),
    resolve_reason=resolve_blocked_reason,
    held_action_text=lambda p, state: _SCENE_RATE_LIMIT_ACTION if state == "rate_limited" else _SCENE_BLOCKED_ACTION,
    extra_gate=lambda p: None,
    is_approved=lambda p: p.status == ScenePacketStatus.APPROVED,
    approved_copy="Scene packet already approved — edit or re-derive to propose changes, then approve again.",
    dto_extras=lambda p, reason: {"blocker_source": infer_blocker_source(p, reason) if reason else None},
)


def can_approve(packet: ScenePacket) -> GateRefusal | None:
    """The real approve gate. Refuses only a BLOCKED or RATE_LIMITED packet; QA verdicts and repair/warn
    issues are advisory, never a gate. STALE stays approvable. Delegates to the shared projection kernel."""
    refusal = project(packet, _POLICY).gate_refusal
    return GateRefusal(refusal) if refusal is not None else None


def approval_blockers(packet: ScenePacket) -> list[str]:
    return project(packet, _POLICY).standalone_blockers


def approval_state(packet: ScenePacket) -> tuple[str, list[str]]:
    """(state, reasons) for the DTO — every non-approvable state carries a reason. STALE stays approvable
    (re-approve IS the remedy). Distinct from can_approve(), the real gate. Delegates to the kernel."""
    pr = project(packet, _POLICY)
    return pr.state, pr.display_reasons


def is_approvable_for_batch(packet: ScenePacket) -> bool:
    return can_approve(packet) is None


def status_after_author_qa(
    body: dict[str, Any] | None,
    qa: dict[str, Any] | None,
    error_detail: str | None = None,
    *,
    blocker_source: str | None = None,
) -> tuple[str, str | None]:
    """(status, blocked_reason). Fail closed on a thin body or unusable QA (infrastructure failures).
    A usable QA verdict — including BLOCK_DRAFTING — is advisory and leaves the packet proposed; its
    issues ride along as repair tasks. A provider rate limit (blocker_source="rate_limit") lands as
    RATE_LIMITED, not BLOCKED: the scene contract is not invalid — the provider refused the call."""
    if blocker_source == "rate_limit" and (not valid_scene_packet_body(body) or qa is None):
        return ScenePacketStatus.RATE_LIMITED, (
            error_detail or "provider rate limit (429) interrupted this scene's derive — retry"
        )
    if not valid_scene_packet_body(body):
        return ScenePacketStatus.BLOCKED, (error_detail or "scene packet author returned an incomplete body")
    if qa is None:
        return ScenePacketStatus.BLOCKED, (error_detail or "scene packet QA returned no usable verdict")
    return ScenePacketStatus.PROPOSED, None


def apply_qa_rerun(row: ScenePacket, result: dict[str, Any] | None) -> None:
    """Mutate row after a manual QA re-run (router endpoint). QA is advisory: any usable verdict —
    including BLOCK_DRAFTING — leaves the row's status alone except that a LEGACY QA-held block (rows
    blocked back when a QA verdict could block) is released to proposed, since QA can no longer hold
    one. A block from a non-QA gate (author/validation/derive) is never released here. Only an unusable
    QA response still fails closed."""
    # Deterministic contract violations are independent of QA — carry them forward so a re-run never
    # erases the advisory/repair/blocking findings the editor is looking at.
    prior = row.qa_warnings if isinstance(row.qa_warnings, dict) else {}
    prior_violations = prior.get("violations")

    if result is None:
        row.qa_verdict = ScenePacketVerdict.BLOCK_DRAFTING
        none_warnings: dict[str, Any] = {
            "residual_risks": [],
            "blocked_reason": "QA returned no usable verdict",
            "blocker_source": "qa",
        }
        if prior_violations:
            none_warnings["violations"] = prior_violations
        row.qa_warnings = none_warnings
        row.status = ScenePacketStatus.BLOCKED
        return

    existing_reason = resolve_blocked_reason(row)
    existing_source = infer_blocker_source(row, existing_reason)

    row.qa_verdict = result["verdict"]
    qa_warnings: dict[str, Any] = {
        "residual_risks": result["residual_risks"],
        "issues": result["issues"],
    }
    if prior_violations:
        qa_warnings["violations"] = prior_violations
    if row.status == ScenePacketStatus.RATE_LIMITED:
        # The only hold was transient infrastructure (a 429'd QA call on a valid body). A usable
        # verdict just arrived, so the hold is over — the packet is an ordinary proposed one again.
        row.status = ScenePacketStatus.PROPOSED
    elif row.status == ScenePacketStatus.BLOCKED:
        if existing_source in {"author", "validation", "derive"} and existing_reason:
            # An earlier non-QA gate still holds the packet — keep that reason AND its source so
            # enrichment doesn't degrade a "validation"/"author" block into a guessed "derive".
            qa_warnings["blocked_reason"] = existing_reason
            qa_warnings["blocker_source"] = existing_source
        elif existing_source == "qa":
            # Legacy QA-held block: QA verdicts no longer gate, so the re-run releases it.
            row.status = ScenePacketStatus.PROPOSED
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


def _project_out(row: ScenePacket, policy: ApprovalPolicy) -> ScenePacketOut:
    # Mirrors the real gate (can_approve() refuses only BLOCKED/RATE_LIMITED): STALE is re-approvable —
    # assert_draft_ready's own remedy says "re-derive or re-approve" — so the UI must offer the button,
    # not just the error message. Every field comes from one projection.
    pr = project(row, policy)
    out = ScenePacketOut.model_validate(row)
    return out.model_copy(
        update={
            "can_approve": pr.state == "approvable",
            "approval_state": pr.state,
            "approval_blockers": pr.display_reasons,
            "blocked_reason": pr.blocked_reason,
            **pr.extras,
        }
    )


def enrich_scene_packet_out(row: ScenePacket) -> ScenePacketOut:
    """Base scene-packet projection — approval status + QA only, blocker-AGNOSTIC. This is NOT the
    router-facing read path: every endpoint that serializes approval fields goes through the
    blocker-aware `blockers.scene_out(s)_with_blockers` bulk path (A1c F6), so an active ApprovalBlocker
    is never advertised as approvable. Kept as the kernel the blocker-aware projector composes over and
    for the projection unit tests; deliberately not re-exported from the pipeline facade."""
    return _project_out(row, _POLICY)


_BLOCKER_UNKNOWN_REASON = "approval-blocker facts were not loaded"


def _blocker_extra_gate(active_blockers: list[ApprovalBlocker] | None):
    """Build C2's `extra_gate` over PRELOADED blocker facts, so blocker state enters the projection only
    AFTER the held precedence — a genuinely BLOCKED/RATE_LIMITED packet is never relabeled as an
    open-question hold. Tri-state, fail closed (A1c F6/F7): None = facts were not loaded → an explicit
    not-approvable `blocker_unknown` gate, never a silent 'no blockers'; `[]` = loaded and none → no gate
    (fall through to approved/approvable); a non-empty list → the `blocked_by_open_question` gate carrying
    every open question."""

    def gate(_packet: ScenePacket) -> tuple[str, list[str]] | None:
        if active_blockers is None:
            return ("blocker_unknown", [_BLOCKER_UNKNOWN_REASON])
        if not active_blockers:
            return None
        return ("blocked_by_open_question", [b.question for b in active_blockers])

    return gate


def project_scene_packet_out(row: ScenePacket, active_blockers: list[ApprovalBlocker] | None) -> ScenePacketOut:
    """The blocker-aware scene projection — the router-facing truth. Feeds preloaded blocker facts into
    C2's `extra_gate` (never overwriting C2's output), so precedence stays held → blocker →
    already-approved → approvable. `active_blockers=None` fails closed (not approvable); `[]` = no active
    blocker; a non-empty list surfaces the open questions and holds approval."""
    return _project_out(row, replace(_POLICY, extra_gate=_blocker_extra_gate(active_blockers)))
