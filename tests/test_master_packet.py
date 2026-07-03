"""Unit tests for the canonical chapter_master_packet reader + validator (pure, no DB, no LLM).

Locks the compatibility contract: the tolerant reader accepts BOTH the legacy AuthorPacketInternal
shape and the canonical shape, always returns the canonical shape, keeps the legacy flat fields as
regenerated compat mirrors, and is idempotent (to_master_packet ∘ to_master_packet == to_master_packet).
"""

from __future__ import annotations

from typing import Any

from dominion.workers.packet.master import (
    SCHEMA_VERSION,
    drafter_view,
    master_open_questions,
    to_master_packet,
    validate_master_packet,
    with_open_questions,
)


def _legacy_body(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "chapter_job": "Marcus intercepts the rogue courier",
        "one_sentence_spine": "The hunt closes in.",
        "entry_state": "Marcus is en route",
        "exit_state": "the duel begins",
        "emotional_spine": "dread hardening into resolve",
        "characters_present": ["Marcus (POV)", "Serra (anonymous assassin)"],
        "characters_absent": ["Brent"],
        "characters_mentioned_only": ["Seb's brother (dead, referenced only)"],
        "characters_forbidden": ["The Broker (not yet introduced)"],
        "canon_locks": ["The Realm is real"],
        "roster_locks": [],
        "relationship_locks": [],
        "timeline_locks": ["Chapter spans one night"],
        "scene_seeds": [
            {
                "seed_id": "11111111-1111-1111-1111-111111111111",
                "scene_no": 1,
                "scene_job": "Marcus reads the route and intercepts.",
                "required_beats": ["Marcus enters the scrim"],
                "exit_state": "blade drawn",
            }
        ],
        "claims": [{"claim": "Realm is real", "source_strength": "LOCKED_CANON", "source_id": "C1"}],
        "open_questions": ["who hired the courier?"],
        "confidence": "green",
    }
    base.update(over)
    return base


# --- tolerant reader: legacy -> canonical ----------------------------------------------------------


def test_legacy_body_normalizes_to_canonical():
    out = to_master_packet(_legacy_body(), book_id="b1", chapter_id="c1", chapter_no=3, pov="Marcus", status="proposed")
    assert out["schema_version"] == SCHEMA_VERSION
    assert (out["book_id"], out["chapter_id"], out["chapter_no"], out["pov"], out["status"]) == (
        "b1",
        "c1",
        3,
        "Marcus",
        "proposed",
    )
    # cast[] replaces the flat 4-array roster (which stays as a derived mirror)
    by_name = {entry["name"]: entry for entry in out["cast"]}
    assert by_name["Marcus"]["presence"] == "present"
    assert by_name["Serra"]["presence"] == "present"
    assert by_name["Serra"]["notes"] == "Serra (anonymous assassin)"  # annotations preserved
    assert by_name["Brent"]["presence"] == "absent"
    assert by_name["Seb's brother"]["presence"] == "mentioned_only"
    assert by_name["The Broker"]["presence"] == "forbidden"
    assert all(entry["reader_must_notice"] is False for entry in out["cast"])
    assert all(entry["minimum_visible_evidence"] is None for entry in out["cast"])
    # mirrors regenerate the original entries (round-trip through notes)
    assert out["characters_present"] == ["Marcus (POV)", "Serra (anonymous assassin)"]
    assert out["characters_forbidden"] == ["The Broker (not yet introduced)"]
    # chapter_contract groups job/spine/entry/exit/locks/claims/open_questions
    contract = out["chapter_contract"]
    assert contract["job"] == "Marcus intercepts the rogue courier"
    assert contract["spine"] == "The hunt closes in."
    assert contract["locks"]["timeline_locks"] == ["Chapter spans one night"]
    assert contract["claims"] == out["claims"]
    assert contract["open_questions"] == {"items": ["who hired the courier?"], "resolved": []}
    # legacy open_questions mirror stays the author-shape string list
    assert out["open_questions"] == ["who hired the courier?"]
    # every seed gains the visible_character_evidence slot
    assert out["scene_seeds"][0]["visible_character_evidence"] == []
    # qa section always present
    assert out["qa"]["verdict"] is None and out["qa"]["blocking_issues"] == []
    # untouched legacy fields pass through
    assert out["confidence"] == "green"


def test_reader_folds_sibling_open_questions_column():
    column = {"items": ["is Serra recognized?"], "resolved": [{"q": "who hired the courier?", "a": "The Broker"}]}
    out = to_master_packet(_legacy_body(), column)
    assert out["chapter_contract"]["open_questions"]["items"] == ["is Serra recognized?"]
    assert out["chapter_contract"]["open_questions"]["resolved"] == column["resolved"]
    assert out["open_questions"] == ["is Serra recognized?"]
    # helper reads the same fold without full normalization
    assert master_open_questions(_legacy_body(), column)["items"] == ["is Serra recognized?"]
    # legacy fallback: no column -> the author's list
    assert master_open_questions(_legacy_body())["items"] == ["who hired the courier?"]


def test_round_trip_is_idempotent():
    once = to_master_packet(_legacy_body(), {"items": ["q1"], "resolved": []}, book_id="b1", chapter_no=2)
    twice = to_master_packet(once)
    assert twice == once


def test_reader_tolerates_garbage():
    out = to_master_packet("not a dict")
    assert out["schema_version"] == SCHEMA_VERSION
    assert out["cast"] == [] and out["scene_seeds"] == [] and out["claims"] == []
    assert out["chapter_contract"]["open_questions"] == {"items": [], "resolved": []}


def test_edited_mirrors_win_and_cast_extras_survive():
    # Canonicalize, enrich a cast entry, then simulate a Desk edit of the flat arrays: the mirrors win
    # on membership, and the untouched entry keeps its per-name extras.
    body = to_master_packet(_legacy_body())
    for entry in body["cast"]:
        if entry["name"] == "Marcus":
            entry["reader_must_notice"] = True
            entry["minimum_visible_evidence"] = "at least one line of dialogue"
    body["characters_present"] = ["Marcus (POV)", "Kael (new arrival)"]  # human swapped Serra for Kael
    out = to_master_packet(body)
    by_name = {entry["name"]: entry for entry in out["cast"] if entry["presence"] == "present"}
    assert set(by_name) == {"Marcus", "Kael"}
    assert by_name["Marcus"]["reader_must_notice"] is True
    assert by_name["Marcus"]["minimum_visible_evidence"] == "at least one line of dialogue"
    assert by_name["Kael"]["reader_must_notice"] is False


def test_with_open_questions_updates_canonical_and_skips_legacy():
    canonical = to_master_packet(_legacy_body())
    updated = with_open_questions(canonical, {"items": [], "resolved": [{"q": "q1", "a": "yes"}]})
    assert updated["chapter_contract"]["open_questions"]["items"] == []
    assert updated["open_questions"] == []
    legacy = _legacy_body()
    assert with_open_questions(legacy, {"items": []}) is legacy  # untouched


def test_drafter_view_prefers_derived_surface():
    body = to_master_packet(_legacy_body())
    assert drafter_view(body) is body  # no projection yet -> the body itself (legacy behavior)
    surface = {"scene_seeds": [{"scene_no": 1, "scene_job": "safe wording"}]}
    body["_surface_contract"] = surface
    assert drafter_view(body) is surface
    assert drafter_view(None) == {}


# --- validate_master_packet -------------------------------------------------------------------------


def test_validate_clean_canonical_body_passes():
    body = to_master_packet(_legacy_body())
    assert validate_master_packet(body) == []


def test_validate_non_dict_blocks():
    v = validate_master_packet(None)
    assert len(v) == 1 and v[0]["severity"] == "block"
    assert v[0]["blocks_drafting"] is True and v[0]["blocks_final_export"] is True


def test_validate_blocks_reserved_for_true_blockers():
    # No draftable scene purpose anywhere -> block; a single seed missing its job among good ones -> repair.
    body = to_master_packet(_legacy_body(scene_seeds=[{"scene_no": 1, "scene_job": "   "}]))
    kinds = {v["kind"]: v for v in validate_master_packet(body)}
    assert kinds["no_draftable_scene_purpose"]["severity"] == "block"
    assert kinds["scene_purpose_missing"]["severity"] == "repair"

    no_seeds = to_master_packet(_legacy_body(scene_seeds=[]))
    assert any(v["kind"] == "no_scenes" and v["severity"] == "block" for v in validate_master_packet(no_seeds))

    wrong_version = {**to_master_packet(_legacy_body()), "schema_version": 99}
    assert any(
        v["kind"] == "schema_version_invalid" and v["severity"] == "block"
        for v in validate_master_packet(wrong_version)
    )


def test_validate_fixable_gaps_are_repair_not_block():
    body = to_master_packet(_legacy_body(chapter_job="  "))
    body["cast"].append({"name": "Ghost", "presence": "haunting"})  # invalid presence
    v = validate_master_packet(body)
    kinds = {x["kind"] for x in v}
    assert {"contract_job_missing", "cast_presence_invalid"} <= kinds
    assert all(x["severity"] == "repair" for x in v)
    assert all(x["blocks_drafting"] is False and x["blocks_final_export"] is True for x in v)


def test_validate_flags_roster_mirror_drift_as_repair():
    body = to_master_packet(_legacy_body())
    body["characters_present"] = [*body["characters_present"], "Someone Out Of Band"]
    v = [x for x in validate_master_packet(body) if x["kind"] == "roster_mirror_drift"]
    assert v and v[0]["severity"] == "repair" and v[0]["field"] == "characters_present"


def test_double_bucketed_roster_survives_normalization_for_the_validator():
    # The reader must NOT silently resolve a true contradiction — one cast entry per authored roster
    # entry, so validate_chapter_packet_contract still sees the present∩absent repair case on the mirrors.
    from dominion.workers.packet.validation import validate_chapter_packet_contract

    body = to_master_packet(_legacy_body(characters_present=["Mara (masked)"], characters_absent=["Mara"]))
    presences = {e["presence"] for e in body["cast"] if e["name"] == "Mara"}
    assert presences == {"present", "absent"}
    v = validate_chapter_packet_contract(body)
    assert any(x.kind == "roster_double_bucketed" and x.severity == "repair" for x in v)
