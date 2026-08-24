"""THE permit for acts that borrow a chapter contract's authority (#283 C1, C2).

One row-selection, one reason code, two call sites. Both `POST /chapters/{id}/beats/approve` (C1) and
`POST /scenes/{id}/approve` (C2) authorize work *under* a chapter's approved contract, so both must ask
the same question before mutating: does that contract still hold authority, or is it carrying unresolved
open questions the human has not settled?

WHY THIS IS A MODULE AND NOT TWO COPIES. #223's fork-3b guard bans a second authorization seam
(`tests/test_issue223_fork3b_authorization_seam_guard.py`), and two routers each re-deriving "which row
is this chapter's authority, and is it still good?" is that seam appearing twice. The *decision* still
lives where it always did — `approval_policy.open_question_items` — and this module only selects the row
to ask it about and shapes the refusal. It adds no policy of its own.

THE MESSAGE IS THE CALLER'S, the reason code is not. What a refusal means to the operator differs by act
— approving beats authorizes the machine to start drafting, approving a scene blesses prose as canonical
— and collapsing both into one sentence would make the Desk vaguer than the truth. The machine-readable
``reason`` is shared precisely so clients can branch on one value.

FAIL-OPEN BY CONSTRUCTION IN EXACTLY ONE CASE, deliberately: a chapter with no approved contract returns
None (permit). There is no contract to contradict, and refusing would break contract-free authoring,
which is a supported flow and not what #283 is about. Every other path either finds a contract and
consults the gate, or raises.

Callers evaluate this UNDER the chapter lock, never before it — a permit read outside the lock is a
snapshot another transaction may already be rewriting.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dominion.shared.enums import PacketStatus
from dominion.shared.models import ChapterPacket
from dominion.workers.packet import approval_policy as packet_approval

__all__ = ["REASON_OPEN_QUESTIONS", "approved_contract_refusal"]

REASON_OPEN_QUESTIONS = "chapter_contract_has_open_questions"


async def approved_contract_refusal(
    session: AsyncSession,
    chapter_id: uuid.UUID,
    *,
    message: Callable[[int], str],
) -> dict[str, str] | None:
    """Return a refusal body for this chapter's approved contract, or None to permit.

    `message` receives the count of unresolved questions and returns the operator-facing sentence, so
    each call site explains its own act without duplicating the row selection or the predicate.
    """
    authority = (
        await session.execute(
            select(ChapterPacket)
            .where(
                ChapterPacket.chapter_id == chapter_id,
                ChapterPacket.status == PacketStatus.APPROVED.value,
            )
            .order_by(ChapterPacket.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if authority is None:
        return None  # no contract to contradict; contract-free authoring is out of scope for this permit
    unresolved = packet_approval.open_question_items(authority)
    if not unresolved:
        return None
    return {"reason": REASON_OPEN_QUESTIONS, "message": message(len(unresolved))}
