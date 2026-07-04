"""Beat-ownership scope guards (recovery lane L2) — pure, deterministic, fixture-driven.

Uses the real Ch1 bad-run ChapterSequence body (tests/fixtures/ch1_bad_run/) plus synthetic prose.
No network, no LLM, no Postgres.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dominion.workers import scene_scope
from dominion.workers.production import run_chapter_draft_qa
from dominion.workers.scene_scope import (
    DUPLICATE_IRREVERSIBLE_BEAT,
    SCENE_SCOPE_BLEED,
    beat_keywords,
    beat_matches_prose,
    beats_owned_by_later_scenes,
    detect_duplicate_irreversible_beats,
    detect_scene_scope_bleed,
    evaluate_scene_scope,
    is_irreversible_beat,
    owned_beats_for_scene,
)

_FIXTURES = Path(__file__).parent / "fixtures" / "ch1_bad_run"


@pytest.fixture(scope="module")
def sequence_body() -> dict:
    return json.loads((_FIXTURES / "chapter_sequence.json").read_text(encoding="utf-8"))["body"]


def _hood_beat(sequence_body: dict) -> str:
    return next(b for b in sequence_body["beat_ownership"] if "hood" in b)


# Synthetic prose. The hood-tear/recognition language is test data (the beat text itself comes
# from the fixture; detection patterns are derived from it, not from these strings).
BLEED_SCENE_2 = (
    "The scrim opened clean. Then her hood tore free in the exchange and red hair spilled loose. "
    "Marcus recognized her instantly, the recognition landing like a dropped frame, and the reveal "
    "rewrote everything he thought he knew about the match."
)
CLEAN_SCENE_2 = (
    "Seb arrived late, jaw set, and told them his brother had died before the match. The guild "
    "settled into the scrim baseline: Brent anchored, Mathias called rotations, Kip watched the "
    "flanks, and Marcus kept his tactical language grounded while the safety assumptions still "
    "felt normal."
)
RECOGNITION_SCENE_3 = (
    "The veil tore under his counter and red hair came loose; recognition hit Marcus mid-swing. "
    "Her aspect shell cracked and she glimpsed the biometric avatar underneath — her own "
    "recognition of him — and the combat kept going, faster and more dangerous after the reveal."
)
RECOGNITION_SCENE_4 = (
    "Even as the screens failed he saw the red hair again and the hood in tatters, and the "
    "recognition replayed — she knew him, he knew her, the reveal doubling back on itself while "
    "logout failed and the match logic broke."
)
CLEAN_SCENE_1 = (
    "Marcus stayed late over the population dashboards, testing ordinary explanations and "
    "rejecting each one, then marked the anomaly honestly and deferred escalation, carrying one "
    "image from the boundary logs toward the coming match."
)
CLEAN_SCENE_4 = (
    "The screens snowed over and authority changed hands; the suited figure spoke in compliance "
    "language while logout failed and Brent named the thing a gun. Each of them consented under "
    "duress in a different register, and it ended on a pressure image."
)


# ---------------------------------------------------------------------------------------------
# Pattern derivation (from beat text, not hardcoded story strings)


def test_beat_keywords_derived_from_beat_text(sequence_body: dict) -> None:
    keywords = beat_keywords(_hood_beat(sequence_body))
    # Directive verb ("Use"), stopwords, and the proper noun (Marcus) are gone; content survives.
    assert "hood" in keywords
    assert "recognition" in keywords
    assert "marcus" not in keywords
    assert "use" not in keywords
    assert "the" not in keywords


def test_irreversibility_is_classified_from_beat_language(sequence_body: dict) -> None:
    assert is_irreversible_beat(_hood_beat(sequence_body))
    # A purely situational beat with no irreversible narrative function stays reversible.
    assert not is_irreversible_beat("Have the screens snow over and the match authority change hands.")


def test_stem_alignment_matches_inflected_prose(sequence_body: dict) -> None:
    beat = _hood_beat(sequence_body)
    assert beat_matches_prose(beat, BLEED_SCENE_2)  # "recognized" matches "recognition"
    assert not beat_matches_prose(beat, CLEAN_SCENE_2)


def test_ownership_projections(sequence_body: dict) -> None:
    assert _hood_beat(sequence_body) in owned_beats_for_scene(3, sequence_body)
    later_for_2 = beats_owned_by_later_scenes(2, sequence_body)
    assert all(owner > 2 for _beat, owner in later_for_2)
    assert any(beat == _hood_beat(sequence_body) and owner == 3 for beat, owner in later_for_2)
    # The final scene has nothing later to leak.
    assert beats_owned_by_later_scenes(4, sequence_body) == []


# ---------------------------------------------------------------------------------------------
# (a) scene_scope_bleed — scene 2 performs scene 3's hood-tear/recognition beat


def test_scene_2_performing_scene_3_recognition_is_scope_bleed(sequence_body: dict) -> None:
    issues = detect_scene_scope_bleed(2, BLEED_SCENE_2, sequence_body)
    assert issues, "scene 2 prose staging the hood-tear/recognition beat must raise scene_scope_bleed"
    hood_issues = [i for i in issues if i["beat"] == _hood_beat(sequence_body)]
    assert len(hood_issues) == 1
    issue = hood_issues[0]
    assert issue["kind"] == SCENE_SCOPE_BLEED
    assert issue["scene_no"] == 2
    assert issue["owner_scene_no"] == 3
    assert issue["irreversible"] is True
    assert issue["severity"] == "block"
    assert "recognition" in issue["matched_keywords"]


def test_owner_scene_performing_its_own_beat_is_not_bleed(sequence_body: dict) -> None:
    assert [i for i in detect_scene_scope_bleed(3, RECOGNITION_SCENE_3, sequence_body) if "hood" in i["beat"]] == []


# ---------------------------------------------------------------------------------------------
# (b) duplicate_irreversible_beat — recognition staged in scenes 3 AND 4


def test_recognition_in_scenes_3_and_4_is_duplicate_irreversible_beat(sequence_body: dict) -> None:
    issues = detect_duplicate_irreversible_beats({3: RECOGNITION_SCENE_3, 4: RECOGNITION_SCENE_4}, sequence_body)
    dupes = [i for i in issues if i["beat"] == _hood_beat(sequence_body)]
    assert len(dupes) == 1
    issue = dupes[0]
    assert issue["kind"] == DUPLICATE_IRREVERSIBLE_BEAT
    assert issue["scene_nos"] == [3, 4]
    assert issue["owner_scene_no"] == 3
    assert issue["severity"] == "block"
    assert set(issue["matched_keywords_by_scene"]) == {3, 4}


def test_recognition_only_in_owning_scene_is_not_duplicate(sequence_body: dict) -> None:
    issues = detect_duplicate_irreversible_beats({3: RECOGNITION_SCENE_3, 4: CLEAN_SCENE_4}, sequence_body)
    assert [i for i in issues if i["beat"] == _hood_beat(sequence_body)] == []


# ---------------------------------------------------------------------------------------------
# Clean chapter → no issues


def test_clean_scenes_produce_no_issues(sequence_body: dict) -> None:
    clean = {1: CLEAN_SCENE_1, 2: CLEAN_SCENE_2, 3: RECOGNITION_SCENE_3, 4: CLEAN_SCENE_4}
    assert evaluate_scene_scope(clean, sequence_body) == []


def test_empty_prose_and_empty_sequence_are_safe(sequence_body: dict) -> None:
    assert detect_scene_scope_bleed(2, "", sequence_body) == []
    assert evaluate_scene_scope({1: "words here"}, {}) == []


# ---------------------------------------------------------------------------------------------
# Chapter QA wiring — run_chapter_draft_qa surfaces both kinds and blocks


def _scene_rows(prose_by_no: dict[int, str], sequence_body: dict) -> list[dict]:
    items = {int(s["scene_no"]): s for s in sequence_body["scenes"]}
    return [
        {
            "scene_no": no,
            "prose": prose,
            "word_count": len(prose.split()),
            "scene_function": items[no]["scene_function"],
            "entry_state": items[no]["entry_state"],
            "exit_state": items[no]["exit_state"],
        }
        for no, prose in sorted(prose_by_no.items())
    ]


def test_chapter_draft_qa_raises_scope_findings_and_blocks(sequence_body: dict) -> None:
    prose_by_no = {1: CLEAN_SCENE_1, 2: BLEED_SCENE_2, 3: RECOGNITION_SCENE_3, 4: RECOGNITION_SCENE_4}
    qa = run_chapter_draft_qa(sequence_body, _scene_rows(prose_by_no, sequence_body), "\n\n".join(prose_by_no.values()))
    kinds = {f["kind"] for f in qa["findings"]}
    assert SCENE_SCOPE_BLEED in kinds
    assert DUPLICATE_IRREVERSIBLE_BEAT in kinds
    assert qa["verdict"] == "block"
    scope = [f for f in qa["findings"] if f["kind"] in (SCENE_SCOPE_BLEED, DUPLICATE_IRREVERSIBLE_BEAT)]
    assert all(f["blocks_final_export"] for f in scope)
    blocking = [f for f in scope if f["severity"] == "block"]
    assert blocking and all(f["blocks_drafting"] for f in blocking)


def test_chapter_draft_qa_clean_prose_has_no_scope_findings(sequence_body: dict) -> None:
    prose_by_no = {1: CLEAN_SCENE_1, 2: CLEAN_SCENE_2, 3: RECOGNITION_SCENE_3, 4: CLEAN_SCENE_4}
    qa = run_chapter_draft_qa(sequence_body, _scene_rows(prose_by_no, sequence_body), "\n\n".join(prose_by_no.values()))
    assert {f["kind"] for f in qa["findings"]} & {SCENE_SCOPE_BLEED, DUPLICATE_IRREVERSIBLE_BEAT} == set()


# ---------------------------------------------------------------------------------------------
# Module purity — lane 10 imports these functions; they must stay pure and deterministic


def test_module_is_pure_and_deterministic(sequence_body: dict) -> None:
    first = evaluate_scene_scope({2: BLEED_SCENE_2, 3: RECOGNITION_SCENE_3}, sequence_body)
    second = evaluate_scene_scope({2: BLEED_SCENE_2, 3: RECOGNITION_SCENE_3}, sequence_body)
    assert first == second
    # No I/O, DB, or LLM machinery in the module namespace.
    assert not hasattr(scene_scope, "AsyncSession")
    assert not hasattr(scene_scope, "llm")
