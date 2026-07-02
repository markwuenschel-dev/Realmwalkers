"""Unit tests for deterministic ChapterPacket roster-consistency validation (pure, no DB, no LLM)."""

from __future__ import annotations

from typing import Any

from dominion.workers.packet.validation import (
    evaluate_chapter_packet,
    validate_chapter_packet_contract,
)


def _seed(scene_no: int = 1, **over: Any) -> dict[str, Any]:
    base = {"scene_no": scene_no, "scene_job": "the scrim begins", "required_beats": [], "exit_state": ""}
    base.update(over)
    return base


def _body(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "characters_present": [],
        "characters_absent": [],
        "characters_mentioned_only": [],
        "characters_forbidden": [],
        "scene_seeds": [],
    }
    base.update(over)
    return base


def test_clean_body_has_no_violations():
    body = _body(
        characters_present=["Marcus (POV)", "Serra (anonymous assassin)"],
        characters_absent=["Brent"],
        characters_mentioned_only=["Seb's brother (dead, referenced only)"],
        characters_forbidden=["The Broker (not yet introduced)"],
        scene_seeds=[_seed(required_beats=["Marcus enters the scrim"])],
    )
    assert validate_chapter_packet_contract(body) == []


def test_same_name_in_two_buckets_blocks():
    body = _body(
        characters_present=["Mara (present, unidentified until Ch2)"],
        characters_absent=["Mara"],
    )
    v = validate_chapter_packet_contract(body)
    blocked = [x for x in v if x.kind == "roster_double_bucketed" and x.severity == "block"]
    assert blocked and "characters_present" in blocked[0].field and "characters_absent" in blocked[0].field


def test_seb_present_and_sebs_brother_mentioned_only_is_not_a_false_positive():
    # Regression: "Seb" (present) and "Seb's brother" (mentioned-only) are DIFFERENT entities. A naive
    # whole-word scan of the other bucket's raw text would collide on "Seb" inside "Seb's" -- the
    # leading-name-exact-match design must not flag this.
    body = _body(
        characters_present=["Seb (arrives late to scrim, present in guild channel)"],
        characters_mentioned_only=["Seb's brother (dead before the chapter; referenced by Seb's late arrival)"],
    )
    assert validate_chapter_packet_contract(body) == []


def test_three_way_double_bucketing_reports_only_contradictory_pairs():
    # Mathias in present + absent + mentioned_only. Only the TRUE-opposite pairs involving `present` are
    # contradictions (present∩absent, present∩mentioned_only). The absent∩mentioned_only pair is NOT a
    # contradiction (mentioned_only implies absence), so it must not appear as a block.
    body = _body(
        characters_present=["Mathias (present, has dialogue)"],
        characters_absent=["Mathias"],
        characters_mentioned_only=["Mathias (mentioned only)"],
    )
    v = validate_chapter_packet_contract(body)
    blocks = [x for x in v if x.kind == "roster_double_bucketed" and x.severity == "block"]
    assert len(blocks) == 2
    flagged_pairs = {frozenset(x.field.split(",")) for x in blocks}
    assert frozenset({"characters_present", "characters_absent"}) in flagged_pairs
    assert frozenset({"characters_present", "characters_mentioned_only"}) in flagged_pairs
    # The compatible pair is never flagged.
    assert frozenset({"characters_absent", "characters_mentioned_only"}) not in flagged_pairs


def test_absent_and_mentioned_only_is_not_a_contradiction():
    # The reported bug: "mentioned only" means off-page but referenced, which implies physical absence, so
    # a name in BOTH characters_absent and characters_mentioned_only is redundant, not contradictory. The
    # raw validator must not block it.
    body = _body(
        characters_absent=["Seb's brother"],
        characters_mentioned_only=["Seb's brother (dead before the chapter, referenced only)"],
    )
    assert validate_chapter_packet_contract(body) == []


def test_absent_and_forbidden_is_not_a_contradiction():
    # "forbidden" (must not be named on-page) also presupposes absence — a name in both is coherent, not a
    # contradiction, and must not block.
    body = _body(
        characters_absent=["The Broker"],
        characters_forbidden=["The Broker (Soulkeepers' Exchange — not yet introduced)"],
    )
    assert validate_chapter_packet_contract(body) == []


def test_present_and_mentioned_only_blocks():
    # present (on-page, acting) contradicts mentioned_only (off-page, referenced only).
    body = _body(
        characters_present=["Mara (present, has dialogue)"],
        characters_mentioned_only=["Mara"],
    )
    blocks = [x for x in validate_chapter_packet_contract(body) if x.severity == "block"]
    assert blocks and blocks[0].kind == "roster_double_bucketed"


def test_mentioned_only_and_forbidden_blocks():
    # A name referenced on-page (mentioned_only) cannot also be forbidden from on-page reference — these
    # are true opposites on the surface-reference axis.
    body = _body(
        characters_mentioned_only=["Mara Valeria"],
        characters_forbidden=["Mara Valeria"],
    )
    blocks = [x for x in validate_chapter_packet_contract(body) if x.severity == "block"]
    assert blocks and blocks[0].kind == "roster_double_bucketed"
    assert frozenset(blocks[0].field.split(",")) == frozenset({"characters_mentioned_only", "characters_forbidden"})


def test_evaluate_normalizes_absent_relation_mentioned_only():
    # End-to-end via evaluate_chapter_packet: absent∩mentioned_only does not block, and the name is
    # dropped from characters_absent (kept in the more specific mentioned_only bucket) so the downstream
    # scene-packet absence check won't false-block a legitimate on-page mention.
    body = _body(
        characters_absent=["Seb's brother"],
        characters_mentioned_only=["Seb's brother (dead before the chapter, referenced only)"],
    )
    result = evaluate_chapter_packet(body)
    assert result.draftable
    assert not result.draft_blockers
    assert result.normalized_body["characters_absent"] == []
    assert result.normalized_body["characters_mentioned_only"] == [
        "Seb's brother (dead before the chapter, referenced only)"
    ]
    assert [w.kind for w in result.warnings] == ["roster_normalized"]


def test_evaluate_keeps_genuinely_absent_names():
    # Normalization only removes the redundant overlap — a name that is ONLY absent stays put, and a
    # different mentioned_only entity ("Seb" vs "Seb's brother") does not trigger removal.
    body = _body(
        characters_absent=["Brent", "Seb"],
        characters_mentioned_only=["Seb's brother (referenced only)"],
    )
    result = evaluate_chapter_packet(body)
    assert result.draftable
    assert result.normalized_body["characters_absent"] == ["Brent", "Seb"]
    assert result.warnings == []


def test_evaluate_still_blocks_true_presence_contradiction():
    body = _body(
        characters_present=["Mara (present, unidentified until Ch2)"],
        characters_absent=["Mara"],
    )
    result = evaluate_chapter_packet(body)
    assert not result.draftable
    assert any(v.kind == "roster_double_bucketed" for v in result.draft_blockers)


def test_forbidden_name_in_required_beats_blocks():
    body = _body(
        characters_forbidden=["The Broker (Soulkeepers' Exchange -- not yet introduced)"],
        scene_seeds=[_seed(scene_no=2, required_beats=["The Broker arrives and delivers the offer"])],
    )
    v = validate_chapter_packet_contract(body)
    blocked = [x for x in v if x.kind == "forbidden_name_in_scene_seed" and x.severity == "block"]
    assert blocked and "scene_no=2" in blocked[0].field


def test_forbidden_name_absent_from_scene_seeds_is_clean():
    body = _body(
        characters_forbidden=["The Broker (not yet introduced)"],
        scene_seeds=[_seed(required_beats=["Marcus flags the anomaly"])],
    )
    assert validate_chapter_packet_contract(body) == []


def test_forbidden_descriptive_entry_without_a_proper_noun_never_false_positives():
    # A forbidden entry with no extractable proper-noun name (a descriptive category, not an identity)
    # must not spuriously match unrelated scene-seed prose.
    body = _body(
        characters_forbidden=["Any named Astria executive or manager not established in prior canon"],
        scene_seeds=[_seed(required_beats=["Astria's model flags the anomaly"])],
    )
    assert validate_chapter_packet_contract(body) == []


def test_non_dict_body_blocks():
    v = validate_chapter_packet_contract("not a dict")  # type: ignore[arg-type]
    assert len(v) == 1 and v[0].severity == "block"


def test_empty_body_has_no_violations():
    assert validate_chapter_packet_contract(_body()) == []
