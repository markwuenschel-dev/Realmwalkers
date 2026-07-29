"""Pure tests of the authoritative draft gate (recovery L8) — resolve_draft_gate over counts.

No DB, no fixtures beyond the preserved Ch1 bad-run JSON: each gate failing ALONE yields its own
one-sentence reason; the FIRST failing gate in pipeline order (packet → sequence/budget/structural →
scene packets (coverage/stale) → beats → jobs → prose coverage → rate limit) always wins; can_draft and
disabled_reason are mutually consistent by construction.

The QA-verdict gate that once sat between `stale` and `beats` is GONE (#278, ADR-0031 R3 Fork 2): it was
decided by raw LLM output that the QA prompt had already coached, so the gate's enforcement was one
sentence of prose and its failure direction was permissive. Its one unique class of coverage — a
contract that contradicts itself — is now a deterministic `canon_contract_leak` structural blocker,
covered below and end-to-end in tests/test_issue278_prompt_gate_authority.py.
"""

from __future__ import annotations

import dataclasses
import json
import uuid
from pathlib import Path

import pytest

from dominion.shared.schemas import StructuralBlockerOut
from dominion.workers.draft_readiness import (
    DraftGateInputs,
    canon_contract_leak_blockers,
    duplicate_irreversible_beat_blockers,
    resolve_draft_gate,
    scene_scope_bleed_blockers,
    sequence_budget_blockers,
)

FIXTURES = Path(__file__).parent / "fixtures" / "ch1_bad_run"


# A chapter where every gate passes: approved packet, full approved scene-packet coverage, linked
# beats, no jobs running, and 4 undrafted scenes waiting for prose.
_READY = DraftGateInputs(
    chapter_packet_approved=True,
    structural_blockers=(),
    scene_packets_derived=4,
    scene_packets_approved=4,
    missing_scene_packets=(),
    scene_packets_stale=0,
    approved_beats=4,
    unlinked_beats=0,
    queue_blocker_messages=(),
    active_draft_jobs=0,
    draftable_scenes=4,
    missing_scene_drafts=(1, 2, 3, 4),
    provider_rate_limited=False,
)


def ready(**overrides) -> DraftGateInputs:
    return dataclasses.replace(_READY, **overrides)


def test_all_gates_passing_yields_can_draft_true_and_no_reason():
    assert resolve_draft_gate(ready()) == (True, None)


# --- each gate failing alone names itself ----------------------------------------------------------


def test_gate_1_chapter_packet():
    can, reason = resolve_draft_gate(ready(chapter_packet_approved=False))
    assert can is False
    assert reason == "Chapter packet is not approved yet — approve it first."


def test_gate_2_structural_blocker_message_passes_through_verbatim():
    blocker = StructuralBlockerOut(kind="sequence_budget_mismatch", message="Budgets disagree — rebalance.")
    can, reason = resolve_draft_gate(ready(structural_blockers=(blocker,)))
    assert can is False
    assert reason == "Budgets disagree — rebalance."


def test_gate_3_no_scene_packets_derived():
    can, reason = resolve_draft_gate(ready(scene_packets_derived=0, scene_packets_approved=0))
    assert can is False
    assert reason == "No scene packets derived yet — derive scene packets first."


def test_gate_3_derived_but_none_approved():
    can, reason = resolve_draft_gate(ready(scene_packets_derived=4, scene_packets_approved=0))
    assert can is False
    assert reason == "No approved scene packets (4 derived) — approve the scene packets first."


def test_gate_3_partial_coverage_names_the_missing_scenes():
    can, reason = resolve_draft_gate(ready(scene_packets_approved=2, missing_scene_packets=(2, 4)))
    assert can is False
    assert reason is not None and "scene(s) 2, 4" in reason and "approve or re-derive" in reason


def test_gate_3_stale_scene_packets():
    can, reason = resolve_draft_gate(ready(scene_packets_stale=2))
    assert can is False
    assert reason == "2 scene packet(s) are stale — re-derive or re-approve them before drafting."


def test_no_gate_can_be_fed_from_llm_output():
    """#278 — the gate that read `ScenePacket.qa_verdict` is removed, not renamed. Passing the retired
    field must be a hard TypeError, so a revert or a merge that reintroduces it cannot pass silently."""
    with pytest.raises(TypeError):
        ready(scene_packet_qa_blocking=1)  # type: ignore[call-arg]


def test_gate_4_no_approved_beats():
    can, reason = resolve_draft_gate(ready(approved_beats=0))
    assert can is False
    assert reason == "No approved beats yet — approving scene packets derives the chapter's beats."


def test_gate_4_unlinked_beats():
    can, reason = resolve_draft_gate(ready(unlinked_beats=1))
    assert can is False
    assert reason is not None and reason.startswith("1 of 4 approved beats are not linked")


def test_gate_4_queue_blockers_surface_the_first_message():
    can, reason = resolve_draft_gate(
        ready(queue_blocker_messages=("Ch1 sc2 has no approved ScenePacket.", "Ch1 sc3 has no approved ScenePacket."))
    )
    assert can is False
    assert reason == "2 draft-queue blocker(s): Ch1 sc2 has no approved ScenePacket."


def test_gate_5_active_draft_jobs():
    can, reason = resolve_draft_gate(ready(active_draft_jobs=2))
    assert can is False
    assert reason == "Scene drafting is already in progress (2 active draft job(s))."


def test_gate_6_everything_already_drafted():
    can, reason = resolve_draft_gate(ready(draftable_scenes=0, missing_scene_drafts=()))
    assert can is False
    assert reason == "Every scene already has a draft — use redraft to regenerate a scene."


def test_gate_6_draft_rows_without_prose_point_at_redraft():
    can, reason = resolve_draft_gate(ready(draftable_scenes=0, missing_scene_drafts=(3,)))
    assert can is False
    assert reason == "Scene(s) 3 have draft rows but no prose — use redraft to regenerate them."


def test_gate_7_provider_rate_limited_checked_last():
    can, reason = resolve_draft_gate(ready(provider_rate_limited=True))
    assert can is False
    assert reason == "The provider is rate-limiting scene generation — wait a moment and retry."


# --- ordering: the FIRST failing gate in pipeline order wins ---------------------------------------


def test_packet_gate_outranks_everything_else():
    can, reason = resolve_draft_gate(
        ready(
            chapter_packet_approved=False,
            structural_blockers=(StructuralBlockerOut(kind="scene_scope_bleed", message="bleed"),),
            scene_packets_stale=3,
            active_draft_jobs=5,
            provider_rate_limited=True,
        )
    )
    assert can is False
    assert reason == "Chapter packet is not approved yet — approve it first."


def test_stale_outranks_jobs_and_rate_limit():
    can, reason = resolve_draft_gate(ready(scene_packets_stale=1, active_draft_jobs=3, provider_rate_limited=True))
    assert can is False
    assert reason is not None and "stale" in reason


def test_jobs_outrank_prose_coverage():
    can, reason = resolve_draft_gate(ready(active_draft_jobs=1, draftable_scenes=0, missing_scene_drafts=()))
    assert can is False
    assert reason == "Scene drafting is already in progress (1 active draft job(s))."


def test_can_draft_and_disabled_reason_are_mutually_consistent():
    variants = [
        ready(),
        ready(chapter_packet_approved=False),
        ready(structural_blockers=(StructuralBlockerOut(kind="canon_contract_leak", message="leak"),)),
        ready(scene_packets_derived=0, scene_packets_approved=0),
        ready(scene_packets_approved=1, missing_scene_packets=(2, 3, 4)),
        ready(scene_packets_stale=4),
        ready(approved_beats=0),
        ready(unlinked_beats=4),
        ready(queue_blocker_messages=("x",)),
        ready(active_draft_jobs=1),
        ready(draftable_scenes=0, missing_scene_drafts=()),
        ready(provider_rate_limited=True),
    ]
    for g in variants:
        can, reason = resolve_draft_gate(g)
        assert can == (reason is None), f"inconsistent gate output for {g}"


# --- structural detectors (pure) -------------------------------------------------------------------


def test_sequence_budget_mismatch_on_the_real_ch1_numbers():
    """The preserved bad run: 4 scene packets summing to 10,400 hard-max words against a 7,200-word
    chapter hard max, and a sequence planning 6 scenes over 4 seeds — both mismatches must fire."""
    seq = json.loads((FIXTURES / "chapter_sequence.json").read_text(encoding="utf-8"))
    packets = json.loads((FIXTURES / "scene_packets.json").read_text(encoding="utf-8"))
    packet_json = json.loads((FIXTURES / "chapter_packet.json").read_text(encoding="utf-8"))
    seed_count = len(packet_json["body"]["scene_seeds"])
    total = sum(p["body"]["word_budget"]["hard_max"] for p in packets)
    out = sequence_budget_blockers(
        seed_count=seed_count,
        sequence_scene_count=seq["target_scene_count"],
        sequence_hard_max_words=seq["hard_max_words"],
        scene_hard_max_total=total,
        sequence_id=uuid.UUID(seq["id"]),
    )
    assert len(out) == 2
    # Deliberate kind split (Desk Control Round): the scene-count arm is its own kind and carries
    # the machine fields the one-click "Align plan to N seeded scenes" action needs; the word-budget
    # arm keeps the original recovery-era kind.
    assert out[0].kind == "sequence_scene_count_mismatch"
    assert out[0].sequence_id == uuid.UUID(seq["id"])
    assert out[0].planned_scene_count == 6 and out[0].seed_count == 4
    assert out[1].kind == "sequence_budget_mismatch"
    assert "6 scenes" in out[0].message and "4" in out[0].message
    assert "10400" in out[1].message and "7200" in out[1].message


def test_sequence_budget_ok_when_numbers_agree():
    assert (
        sequence_budget_blockers(
            seed_count=4, sequence_scene_count=4, sequence_hard_max_words=7200, scene_hard_max_total=7000
        )
        == []
    )


def test_sequence_budget_skips_unknown_sides():
    assert (
        sequence_budget_blockers(
            seed_count=0, sequence_scene_count=None, sequence_hard_max_words=None, scene_hard_max_total=None
        )
        == []
    )


def test_scene_scope_bleed_flags_only_mismatched_links():
    out = scene_scope_bleed_blockers([(1, 1), (2, 3), (4, 4)])
    assert len(out) == 1
    assert out[0].kind == "scene_scope_bleed"
    assert "scene 2" in out[0].message and "scene-3" in out[0].message


def test_duplicate_beats_per_scene_are_flagged():
    out = duplicate_irreversible_beat_blockers(beat_scene_nos=[1, 2, 2, 3], scene_seeds=[])
    assert len(out) == 1
    assert out[0].kind == "duplicate_irreversible_beat"
    assert "Scene 2 has 2 approved beats" in out[0].message


def test_same_irreversible_change_seeded_twice_is_flagged():
    seeds = [
        {"scene_no": 2, "irreversible_state_change": "Mutual recognition lands."},
        {"scene_no": 3, "irreversible_state_change": "mutual  recognition lands."},  # case/space variant
        {"scene_no": 4, "irreversible_state_change": "The duel is interrupted."},
    ]
    out = duplicate_irreversible_beat_blockers(beat_scene_nos=[2, 3, 4], scene_seeds=seeds)
    assert len(out) == 1
    assert out[0].kind == "duplicate_irreversible_beat"
    assert "scenes 2, 3" in out[0].message


def test_non_string_irreversible_flags_are_ignored():
    seeds = [{"scene_no": 1, "irreversible_state_change": True}, {"scene_no": 2, "irreversible_state_change": True}]
    assert duplicate_irreversible_beat_blockers(beat_scene_nos=[1, 2], scene_seeds=seeds) == []


def test_canon_contract_leak_against_chapter_forbidden_list():
    out = canon_contract_leak_blockers(
        packets=[(1, {"learned_during_scene": {"reader_must_learn": ["Marcus's true name."]}})],
        chapter_forbidden=["marcus's  true name."],  # normalization: case + whitespace
    )
    assert len(out) == 1
    assert out[0].kind == "canon_contract_leak"
    assert "Scene 1" in out[0].message and "chapter packet forbids" in out[0].message


def test_canon_contract_leak_within_one_contract():
    body = {
        "learned_during_scene": {"reader_may_learn": ["The Eyes signal fired."]},
        "must_remain_hidden": {"reader": ["The Eyes signal fired."]},
    }
    out = canon_contract_leak_blockers(packets=[(2, body)], chapter_forbidden=[])
    assert len(out) == 1
    assert "both reveals and hides" in out[0].message


def test_clean_contracts_produce_no_leaks():
    body = {
        "learned_during_scene": {"reader_must_learn": ["The match started."]},
        "must_remain_hidden": {"reader": ["Roth's identity."]},
    }
    assert canon_contract_leak_blockers(packets=[(1, body)], chapter_forbidden=["Marcus's true name."]) == []


# --- #278: the exposure matrix that replaces the retired QA-verdict gate ---------------------------
# Each case below is a contract the LLM QA prompt already asks the model to catch (qa.py:36-39) on a
# field pair the detector never cross-checked, so it was reachable ONLY through the model's verdict.


@pytest.mark.parametrize(
    ("exposed_path", "body"),
    [
        (
            "known_before_scene.reader",
            {
                "known_before_scene": {"reader": ["Mara lit the fire."]},
                "must_remain_hidden": {"reader": ["Mara lit the fire."]},
            },
        ),
        (
            "learned_during_scene.reader_may_infer_only",
            {
                "learned_during_scene": {"reader_may_infer_only": ["Mara lit the fire."]},
                "must_remain_hidden": {"reader": ["Mara lit the fire."]},
            },
        ),
        (
            "known_before_scene.pov",
            {
                "known_before_scene": {"pov": ["Mara lit the fire."]},
                "must_remain_hidden": {"pov": ["Mara lit the fire."]},
            },
        ),
        (
            "pov_permissions.may_notice",
            {
                "pov_permissions": {"may_notice": ["Mara lit the fire."]},
                "must_remain_hidden": {"pov": ["Mara lit the fire."]},
            },
        ),
        (
            "required_beats",
            {
                "required_beats": ["Mara lit the fire."],
                "must_remain_hidden": {"all_surface_prose": ["Mara lit the fire."]},
            },
        ),
        (
            "exit_state",
            {"exit_state": "Mara lit the fire.", "must_remain_hidden": {"reader": ["Mara lit the fire."]}},
        ),
    ],
)
def test_a_fact_declared_hidden_and_then_exposed_blocks(exposed_path, body):
    out = canon_contract_leak_blockers(packets=[(3, body)], chapter_forbidden=[])
    assert len(out) == 1, out
    assert out[0].kind == "canon_contract_leak"
    assert "both reveals and hides" in out[0].message
    assert exposed_path in out[0].message


def test_author_only_layering_is_correct_and_never_blocks():
    """`known_before_scene.omniscient_author` is what the AUTHOR knows and legitimately overlaps what the
    reader knows, so it is deliberately not a hiding declaration — pairing it would fail correct
    contracts, and a structural blocker is unappealable (qa.py:47-52 states the same layering rule)."""
    body = {
        "known_before_scene": {
            "reader": ["The garrison fell."],
            "omniscient_author": ["The garrison fell.", "Mara lit it."],
        },
        "must_remain_hidden": {"reader": ["Mara lit it."]},
    }
    assert canon_contract_leak_blockers(packets=[(1, body)], chapter_forbidden=[]) == []


def test_a_pov_who_knows_a_reader_secret_is_craft_not_a_leak():
    """The chapter's forbidden list is about what the READER may learn. A POV holding a secret the reader
    must not is ordinary dramatic irony, so POV-visible fields are excluded from the forbidden arm."""
    body = {"known_before_scene": {"pov": ["Roth betrayed them."]}}
    assert canon_contract_leak_blockers(packets=[(1, body)], chapter_forbidden=["Roth betrayed them."]) == []


def test_forbidden_reveal_staged_on_page_blocks():
    body = {"required_beats": ["Roth betrayed them."]}
    out = canon_contract_leak_blockers(packets=[(2, body)], chapter_forbidden=["roth  BETRAYED them."])
    assert len(out) == 1
    assert "chapter packet forbids" in out[0].message


def test_one_fact_exposed_in_two_fields_reports_both_once_each():
    """Per-(field, fact) reporting: the human fixes fields, so each offending field is named — but a
    single field is never double-reported when two hidden declarations cover the same fact."""
    body = {
        "known_before_scene": {"reader": ["Mara lit the fire."]},
        "required_beats": ["Mara lit the fire."],
        "must_remain_hidden": {"reader": ["Mara lit the fire."], "all_surface_prose": ["Mara lit the fire."]},
    }
    out = canon_contract_leak_blockers(packets=[(1, body)], chapter_forbidden=[])
    assert len(out) == 2, [b.message for b in out]
