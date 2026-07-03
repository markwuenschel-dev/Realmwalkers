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


def test_parse_qa_normalizes_issue_severity_capped_at_repair():
    # LLM issues become machine-readable: guaranteed severity + blocks_* facts. An LLM-claimed "block"
    # is demoted to repair (no LLM-driven control path); a missing severity degrades to warn.
    out = qa_mod.parse_qa(
        '{"verdict": "REVISE_REQUIRED", "residual_risks": [], "issues": ['
        '{"kind": "canon_leak", "field": "scene_seeds", "detail": "leak", "severity": "block"},'
        '{"kind": "vague", "detail": "unclear beat"}]}'
    )
    assert out is not None
    leak, vague = out["issues"]
    assert leak["severity"] == "repair"
    assert leak["blocks_drafting"] is False and leak["blocks_human_review"] is False
    assert leak["blocks_final_export"] is True
    assert vague["severity"] == "warn"
    assert vague["blocks_final_export"] is False


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


def test_derive_block_drafting_is_red_but_proposed():
    # QA is advisory: even BLOCK_DRAFTING never blocks the packet — it floors confidence to red (a
    # signal for the human) while the packet stays proposed and its issues ride along as repair tasks.
    packet = {"confidence": "green", "open_questions": []}
    conf, status = approval_policy.status_from_qa(packet, _qa(PacketVerdict.BLOCK_DRAFTING))
    assert conf == PacketConfidence.RED and status == PacketStatus.PROPOSED


def test_derive_takes_worst_of_author_and_verdict():
    # Author self-assesses red even though QA approves — the worst wins.
    packet = {"confidence": "red", "open_questions": []}
    conf, status = approval_policy.status_from_qa(packet, _qa(PacketVerdict.APPROVE))
    assert conf == PacketConfidence.RED and status == PacketStatus.PROPOSED
