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


def test_absent_and_mentioned_only_is_not_a_contradiction():
    # The reported bug: "mentioned only" means off-page but referenced, which implies physical absence, so
    # a name in BOTH characters_absent and characters_mentioned_only is redundant, not contradictory. The
    # raw validator must not block it.
    body = _body(
        characters_absent=["Seb's brother"],
        characters_mentioned_only=["Seb's brother (dead before the chapter, referenced only)"],
    )
    assert validate_chapter_packet_contract(body) == []


def test_present_and_mentioned_only_normalizes_to_present():
    # A physically present character redundantly echoed in mentioned_only (the masked / late-reveal
    # mis-bucket) is NOT a contradiction: present dominates, so it is dropped from mentioned_only and the
    # packet is not blocked. The raw validator must not flag it, and evaluate must normalize it.
    body = _body(
        characters_present=["Serra Hawthorne (Dead Hand rogue, unrecognized until mid-duel)"],
        characters_mentioned_only=["Serra Hawthorne"],
    )
    assert validate_chapter_packet_contract(body) == []
    result = evaluate_chapter_packet(body)
    assert result.draftable
    assert not result.draft_blockers
    assert result.normalized_body["characters_present"] == [
        "Serra Hawthorne (Dead Hand rogue, unrecognized until mid-duel)"
    ]
    assert result.normalized_body["characters_mentioned_only"] == []
    assert [w.kind for w in result.warnings] == ["roster_normalized"]


def test_hidden_identity_present_not_mentioned_only():
    # Serra is physically present under a hidden identity; she must resolve to characters_present only,
    # never lingering in characters_mentioned_only after normalization.
    body = _body(
        characters_present=["Marcus Vye", "Serra Hawthorne"],
        characters_mentioned_only=["Seb's brother", "Serra Hawthorne"],
    )
    result = evaluate_chapter_packet(body)
    assert result.draftable
    assert "Serra Hawthorne" in result.normalized_body["characters_present"]
    mentioned_leads = {e.split(" (")[0] for e in result.normalized_body["characters_mentioned_only"]}
    assert "Serra Hawthorne" not in mentioned_leads
    assert "Seb's brother" in mentioned_leads  # a genuinely mentioned-only character is untouched


def test_non_dict_body_blocks():
    v = validate_chapter_packet_contract("not a dict")  # type: ignore[arg-type]
    assert len(v) == 1 and v[0].severity == "block"


# --- Invariant / generic surface contract tests (no story names, focus on scopes + policy) ---


def test_forbidden_term_in_drafter_field_without_policy_blocks_at_surface():
    from dominion.workers.packet.surface_contract import build_surface_contract

    body = _body(
        characters_forbidden=["Hidden Canonical Name"],
        scene_seeds=[_seed(required_beats=["Introduce Hidden Canonical Name on page."])],
    )
    res = build_surface_contract(body)
    assert res.blockers, "Surface must block when no replace/omit policy exists for drafter field"


def test_surface_policy_replace_produces_clean_drafter_contract_and_carries_policies():
    from dominion.workers.packet.surface_contract import build_surface_contract

    body = _body(
        characters_forbidden=["Hidden Canonical Name"],
        surface_terms=[
            {
                "canonical_term": "Hidden Canonical Name",
                "forbidden_surface_terms": ["Hidden Canonical Name"],
                "surface_label": "the anonymous operative",
                "policy": "replace",
            }
        ],
        scene_seeds=[_seed(required_beats=["Hidden Canonical Name is introduced carefully."])],
    )
    res = build_surface_contract(body)
    assert not res.blockers
    seed0 = res.surface_body.get("scene_seeds", [{}])[0]
    text = " ".join(seed0.get("required_beats", []))
    assert "the anonymous operative" in text
    assert "Hidden Canonical Name" not in text
    assert any(p.canonical_term == "Hidden Canonical Name" and p.surface_label for p in res.policies)
