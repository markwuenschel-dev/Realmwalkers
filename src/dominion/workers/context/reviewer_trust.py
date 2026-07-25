"""The reviewer-contract trust split (ADR-0033 D5).

**The failure this prevents.** Under ADR-0030's unattended path a ScenePacket's contract is derived from
evidence and approved by policy, with no human reading it. Every lane reviewer then reads that contract
before judging the prose, including two fields whose entire job is to *stop* it reporting things:
`forbidden_beats` ("do not ask for these") and `reviewer_false_positive_traps` ("do not flag"). So a
derivation that drifted could write a trap covering exactly the drift, and the detector would be silenced
by the thing it was meant to detect. Under `S = info` (ADR-0033 D2) that does not merely hide a note from
the author — every reviewer `warn` keeps the converge loop running, so a suppressed finding *ends the
loop early* and the scene is declared Review-Ready.

**The rule.** Positive fields are always honoured. Suppression fields are honoured only when a human
approved the owning packet (`ScenePacketApprovalSource.MANUAL_COMMAND`).

**Why it lives in the projection, not in the reviewer.** The reviewers are seven independent modules, and
an eighth will be added. A rule they each have to remember is a rule one of them will forget — and the
failure would be silent, because a reviewer that wrongly trusts a trap simply reports nothing. Filtering
where the contract is *built* means an untrusted suppression field never reaches a reviewer's prompt at
all, so there is nothing to forget. `reviewers/lane.py` is unchanged by design.
"""

from __future__ import annotations

from typing import Any

from dominion.shared.enums import ScenePacketApprovalSource

#: Keys a reviewer may act on only when a human approved the contract. `forbidden_beats` and
#: `reviewer_false_positive_traps` are suppression by their own prompt text (`reviewers/lane.py:47,53`).
#: `reviewer_instructions` is FREE TEXT per lane, so it can suppress ("don't flag the stamina tracking")
#: and cannot be verified — ADR-0033 D5a records it as suppression by derivation from the ruling's
#: principle: anything that can suppress and cannot be checked is withheld until a human has read it.
SUPPRESSION_FIELDS: frozenset[str] = frozenset(
    {"forbidden_beats", "reviewer_false_positive_traps", "reviewer_instructions"}
)

#: The one provenance that makes suppression trustworthy. Everything else — autonomous policy, legacy
#: rows approved before provenance existed, and a packet not yet approved at all — is untrusted. Fails
#: closed on an unknown value by construction: only this exact string trusts.
_TRUSTED_SOURCE = ScenePacketApprovalSource.MANUAL_COMMAND.value


def suppression_is_trusted(approval_source: str | None) -> bool:
    """True iff a human approved this contract, so its suppression fields may be honoured."""
    return approval_source == _TRUSTED_SOURCE


def trusted_reviewer_contract(reviewer_contract: dict[str, Any], *, approval_source: str | None) -> dict[str, Any]:
    """The reviewer contract with untrusted suppression fields REMOVED (not blanked — removed, so
    `lane.py`'s `if rc.get(...)` guards skip them exactly as they do for an absent field).

    Returns the input unchanged when the contract is human-approved. Positive fields (`scene_job`,
    `scene_type`, `required_beats`, `word_budget`) always survive: they tell a reviewer what to look FOR,
    and withholding them would cost precision without buying any safety.
    """
    if suppression_is_trusted(approval_source):
        return reviewer_contract
    return {k: v for k, v in reviewer_contract.items() if k not in SUPPRESSION_FIELDS}
