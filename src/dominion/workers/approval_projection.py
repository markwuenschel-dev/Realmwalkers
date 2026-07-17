"""Composed approval PROJECTION kernel (C2).

One tested algorithm behind the read-only packet-approval projection for both tiers. It owns only
PRECEDENCE and PROJECTION — `held → extra gate → already-approved → approvable` — over a frozen,
per-tier `ApprovalPolicy`.

Scope boundary: this is a projection/display refactor. It does NOT touch the transactional approval
operation and does NOT repair raw approval-status writers — that is a separate seam. C2 does not close
ADR-0031 D6.

Three reason-carrying fields on `GatePresentation` are INDEPENDENT observable contracts — the kernel
sets each explicitly per branch and NEVER derives one from another:
  - `gate_refusal`        → the endpoint gate `can_approve()` ACTION text
  - `standalone_blockers` → the standalone `approval_blockers()` list
  - `display_reasons`     → the DTO `approval_state` reasons / enriched `approval_blockers` field
(They diverge: a scene held packet has fixed action text but a resolved display reason; an
already-approved packet has empty standalone blockers but a display copy.)
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class GatePresentation:
    state: str
    gate_refusal: str | None
    standalone_blockers: list[str]
    display_reasons: list[str]
    blocked_reason: str | None
    extras: dict[str, Any]


@dataclass(frozen=True)
class ApprovalPolicy:
    """Per-tier hooks. The kernel calls these; each tier binds them to its own model, strings, and DTO
    extras. `resolve_reason` returns the display/blocked reason for a held packet (never None on a held
    status — it carries the tier's own fallback)."""

    held_state: Callable[[Any], str | None]  # "blocked" / "rate_limited" / … or None when not held
    resolve_reason: Callable[[Any], str | None]
    held_action_text: Callable[[Any, str], str]  # (packet, held_state) → gate action text
    extra_gate: Callable[[Any], tuple[str, list[str]] | None]  # (state, display_reasons) e.g. open-questions, or None
    is_approved: Callable[[Any], bool]
    approved_copy: str
    dto_extras: Callable[[Any, str | None], dict[str, Any]]  # (packet, resolved_reason) → DTO extras


def project(packet: Any, policy: ApprovalPolicy) -> GatePresentation:
    """The one approval-projection algorithm. Precedence: held → extra gate → already-approved →
    approvable. Every field is filled explicitly per branch."""
    held = policy.held_state(packet)
    if held is not None:
        reason = policy.resolve_reason(packet)
        reasons = [reason] if reason is not None else []
        return GatePresentation(
            state=held,
            gate_refusal=policy.held_action_text(packet, held),
            standalone_blockers=reasons,
            display_reasons=reasons,
            blocked_reason=reason,
            extras=policy.dto_extras(packet, reason),
        )
    extra = policy.extra_gate(packet)
    if extra is not None:
        state, reasons = extra
        # One action string for the gate, but the FULL reason list for the standalone + display
        # contracts: a scene open-question hold can carry several questions; the chapter tier passes one,
        # so its single-item list projects byte-identically to the previous single-reason behaviour.
        return GatePresentation(
            state=state,
            gate_refusal=reasons[0] if reasons else None,
            standalone_blockers=list(reasons),
            display_reasons=list(reasons),
            blocked_reason=None,
            extras=policy.dto_extras(packet, None),
        )
    if policy.is_approved(packet):
        return GatePresentation(
            state="already_approved",
            gate_refusal=None,
            standalone_blockers=[],
            display_reasons=[policy.approved_copy],
            blocked_reason=None,
            extras=policy.dto_extras(packet, None),
        )
    return GatePresentation(
        state="approvable",
        gate_refusal=None,
        standalone_blockers=[],
        display_reasons=[],
        blocked_reason=None,
        extras=policy.dto_extras(packet, None),
    )
