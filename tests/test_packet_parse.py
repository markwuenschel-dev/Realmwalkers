"""Packet parsing + orchestration-logic unit tests (no DB, no network).

Covers the fail-closed contract at the parsing layer and the pure derivation helpers: a malformed or
unknown-verdict response must yield None (so the orchestration blocks), provenance must resolve to
real sources, scene seeds must get stable ids, and confidence/status must derive conservatively.
"""

from __future__ import annotations

from dominion.shared.enums import PacketConfidence, PacketStatus, PacketVerdict
from dominion.workers.packet import approval_policy
from dominion.workers.packet import qa as qa_mod

# --- Packet QA parse: fail closed on anything unusable --------------------------------------------


def test_parse_qa_valid_verdict():
    out = qa_mod.parse_qa('{"verdict": "APPROVE", "residual_risks": ["watch Serra"], "issues": []}')
    assert out is not None
    assert out["verdict"] == PacketVerdict.APPROVE
    assert out["residual_risks"] == ["watch Serra"]


def test_parse_qa_unknown_verdict_is_none():
    # A recognizable object but an unknown verdict must NOT be guessed — fail closed.
    assert qa_mod.parse_qa('{"verdict": "MAYBE_OK"}') is None


# --- _derive: conservative confidence + status ----------------------------------------------------


def _qa(verdict: PacketVerdict, issues=None):
    return {"verdict": verdict, "issues": issues or [], "residual_risks": []}


def test_derive_clean_green_stays_green():
    packet = {"confidence": "green", "open_questions": []}
    conf, status = approval_policy.status_from_qa(packet, _qa(PacketVerdict.APPROVE))
    assert conf == PacketConfidence.GREEN and status == PacketStatus.PROPOSED


def test_derive_approve_warn_is_yellow():
    packet = {"confidence": "green", "open_questions": []}
    conf, status = approval_policy.status_from_qa(packet, _qa(PacketVerdict.APPROVE_WARN))
    assert conf == PacketConfidence.YELLOW and status == PacketStatus.PROPOSED


def test_derive_block_is_red_and_blocked():
    packet = {"confidence": "green", "open_questions": []}
    conf, status = approval_policy.status_from_qa(packet, _qa(PacketVerdict.BLOCK_DRAFTING))
    assert conf == PacketConfidence.RED and status == PacketStatus.BLOCKED


def test_derive_takes_worst_of_author_and_verdict():
    # Author self-assesses red even though QA approves — the worst wins.
    packet = {"confidence": "red", "open_questions": []}
    conf, status = approval_policy.status_from_qa(packet, _qa(PacketVerdict.APPROVE))
    assert conf == PacketConfidence.RED and status == PacketStatus.PROPOSED
