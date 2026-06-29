"""Packet parsing + orchestration-logic unit tests (no DB, no network).

Covers the fail-closed contract at the parsing layer and the pure derivation helpers: a malformed or
unknown-verdict response must yield None (so the orchestration blocks), provenance must resolve to
real sources, scene seeds must get stable ids, and confidence/status must derive conservatively.
"""
from __future__ import annotations

import uuid

from dominion.shared.enums import PacketConfidence, PacketStatus, PacketVerdict
from dominion.workers import packet as pipeline
from dominion.workers.packet import approval_policy, parse
from dominion.workers.packet import author as author_mod
from dominion.workers.packet import qa as qa_mod

# --- extract_object (tolerant single-object parse) ------------------------------------------------

def test_extract_object_plain():
    assert parse.extract_object('{"a": 1}') == {"a": 1}


def test_extract_object_fenced():
    assert parse.extract_object('```json\n{"a": 1}\n```') == {"a": 1}


def test_extract_object_prose_preamble():
    assert parse.extract_object('Here you go:\n{"a": 1}\nHope that helps') == {"a": 1}


def test_extract_object_garbage_is_none():
    assert parse.extract_object("sorry, no json here") is None


def test_str_list_coerces_and_drops_blanks():
    assert parse.str_list(["a", " b ", "", 3]) == ["a", "b", "3"]
    assert parse.str_list("not a list") == []


# --- Packet QA parse: fail closed on anything unusable --------------------------------------------

def test_parse_qa_valid_verdict():
    out = qa_mod.parse_qa('{"verdict": "APPROVE", "residual_risks": ["watch Serra"], "issues": []}')
    assert out is not None
    assert out["verdict"] == PacketVerdict.APPROVE
    assert out["residual_risks"] == ["watch Serra"]


def test_parse_qa_unknown_verdict_is_none():
    # A recognizable object but an unknown verdict must NOT be guessed — fail closed.
    assert qa_mod.parse_qa('{"verdict": "MAYBE_OK"}') is None


def test_parse_qa_garbage_is_none():
    assert qa_mod.parse_qa("the packet looks fine to me") is None


def test_parse_qa_block_verdict():
    out = qa_mod.parse_qa('{"verdict": "BLOCK_DRAFTING", "issues": [{"kind": "canon_leak", "detail": "x"}]}')
    assert out is not None and out["verdict"] == PacketVerdict.BLOCK_DRAFTING


# --- _valid_packet --------------------------------------------------------------------------------

def test_valid_packet_requires_seeds_and_claims():
    assert pipeline._valid_packet({"scene_seeds": [{"scene_no": 1}], "claims": []}) is True
    assert pipeline._valid_packet({"scene_seeds": [], "claims": []}) is False
    assert pipeline._valid_packet({"scene_seeds": [{"scene_no": 1}]}) is False  # no claims list
    assert pipeline._valid_packet({"claims": []}) is False


# --- _mint_seed_ids: server-side stable ids -------------------------------------------------------

def test_mint_seed_ids_are_present_and_unique():
    packet = {"scene_seeds": [{"scene_no": 1}, {"scene_no": 2}]}
    pipeline._mint_seed_ids(packet)
    ids = [s["seed_id"] for s in packet["scene_seeds"]]
    assert all(uuid.UUID(i) for i in ids)        # valid UUIDs
    assert len(set(ids)) == 2                     # unique per seed


# --- _resolve_provenance: claim handles -> real sources -------------------------------------------

def test_resolve_provenance_canon_outline_and_inference():
    canon_id = uuid.uuid4()
    handles = {"C1": {"id": canon_id, "name": "Cosmology", "body": "The Realm is not a game." * 20}}
    packet = {"claims": [
        {"claim": "Realm is real", "source_strength": "LOCKED_CANON", "source_id": "C1"},
        {"claim": "Serra pressures", "source_strength": "DERIVED_FROM_OUTLINE", "source_id": "OUTLINE"},
        {"claim": "404 feels loose", "source_strength": "PLAUSIBLE_INFERENCE", "source_id": None},
    ]}
    pipeline._resolve_provenance(packet, handles)
    c0, c1, c2 = packet["claims"]
    assert c0["source_id"] == str(canon_id) and c0["source_title_or_file"] == "Cosmology"
    assert c0["excerpt"] and len(c0["excerpt"]) <= pipeline._EXCERPT_CHARS
    assert c1["source_id"] == "OUTLINE" and c1["source_title_or_file"] == "chapter outline"
    assert c2["source_id"] is None and c2["source_title_or_file"] is None


# --- _derive: conservative confidence + status ----------------------------------------------------

def _qa(verdict: PacketVerdict, issues=None):
    return {"verdict": verdict, "issues": issues or [], "residual_risks": []}


def test_derive_clean_green_stays_green():
    packet = {"confidence": "green", "open_questions": []}
    conf, status = approval_policy.status_from_qa(packet, _qa(PacketVerdict.APPROVE))
    assert conf == PacketConfidence.GREEN and status == PacketStatus.PROPOSED


def test_derive_green_downgraded_by_open_questions():
    packet = {"confidence": "green", "open_questions": ["who is present during the hijack?"]}
    conf, _ = approval_policy.status_from_qa(packet, _qa(PacketVerdict.APPROVE))
    assert conf == PacketConfidence.YELLOW


def test_derive_green_downgraded_by_qa_issues():
    packet = {"confidence": "green", "open_questions": []}
    conf, _ = approval_policy.status_from_qa(packet, _qa(PacketVerdict.APPROVE, issues=[{"kind": "x", "detail": "y"}]))
    assert conf == PacketConfidence.YELLOW


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


# --- prompt builders carry the key inputs ---------------------------------------------------------

def test_author_prompt_includes_outline_and_canon_handles():
    prompt = author_mod.build_prompt(
        chapter_no=1, pov="Marcus", outline="Marcus intercepts the rogue.",
        omniscient_summary=None, prior_exit_state=None, next_entry_intent=None,
        canon_handles={"C1": {"name": "Cosmology", "body": "real not game"}},
    )
    assert "Marcus intercepts the rogue." in prompt
    assert "[C1]" in prompt and "Cosmology" in prompt
