"""Unit tests for deterministic ChapterPacket roster-consistency validation (pure, no DB, no LLM)."""

from __future__ import annotations

from typing import Any

from dominion.workers.packet.validation import validate_chapter_packet_contract


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


def test_three_way_double_bucketing_reports_each_pair():
    body = _body(
        characters_present=["Mathias (present, has dialogue)"],
        characters_absent=["Mathias"],
        characters_mentioned_only=["Mathias (mentioned only)"],
    )
    v = validate_chapter_packet_contract(body)
    kinds = [x.kind for x in v if x.severity == "block"]
    assert kinds.count("roster_double_bucketed") >= 2


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
