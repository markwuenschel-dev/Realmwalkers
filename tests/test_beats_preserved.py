"""Unit table for the deterministic beat-preservation helper (audit candidate D5).

Exercises the pure logic in ``dominion.workers.beat_preservation`` in isolation: the before→after
regression delta, the empty/unavailable status branches, chapter-region relocation via concatenated
prose, and the documented keyword-threshold paraphrase limitation.
"""

from __future__ import annotations

from dominion.workers.beat_preservation import (
    SCENE_BREAK,
    beats_preserved,
    ordered_unique,
)

# Beats carry a leading "Show" directive (dropped by the matcher), then distinctive common-noun
# keywords. The "present" prose repeats those content words verbatim so a match is threshold-robust;
# the "unrelated" prose shares none.
BEAT_REACTOR = "Show the reactor core overheating beyond safe operating limits"
BEAT_BREACH = "Show the engineer sealing the hull breach with molten alloy"

PROSE_REACTOR = "The reactor core kept overheating past every safe operating limit as the sirens rose."
PROSE_BREACH = "The engineer knelt, sealing the ragged hull breach with a ribbon of molten alloy."
PROSE_UNRELATED = "Rain fell on the quiet garden; a cat dozed on the warm windowsill."


def test_present_before_present_after_preserved():
    r = beats_preserved(PROSE_REACTOR, PROSE_REACTOR + " Later, calm returned.", [BEAT_REACTOR])
    assert r.status == "checked"
    assert r.preserved is True
    assert r.present_before_count == 1
    assert r.dropped_beats == ()


def test_present_before_absent_after_dropped():
    r = beats_preserved(PROSE_REACTOR, PROSE_UNRELATED, [BEAT_REACTOR])
    assert r.status == "checked"
    assert r.preserved is False
    assert r.present_before_count == 1
    assert r.dropped_beats == (BEAT_REACTOR,)


def test_absent_before_absent_after_ignored():
    # Beat was never in the before-prose (a drafting gap, not a repair regression) -> not counted.
    r = beats_preserved(PROSE_UNRELATED, PROSE_UNRELATED, [BEAT_REACTOR])
    assert r.status == "checked"
    assert r.preserved is True
    assert r.present_before_count == 0
    assert r.checked_count == 1
    assert r.dropped_beats == ()


def test_empty_required_beats_is_vacuously_preserved():
    r = beats_preserved(PROSE_REACTOR, PROSE_UNRELATED, [])
    assert r.status == "empty_required_beats"
    assert r.preserved is True
    assert r.checked_count == 0


def test_none_inputs_are_unavailable_not_a_failure():
    for args in (
        (None, PROSE_REACTOR, [BEAT_REACTOR]),
        (PROSE_REACTOR, None, [BEAT_REACTOR]),
        (PROSE_REACTOR, PROSE_REACTOR, None),
    ):
        r = beats_preserved(*args)
        assert r.status == "unavailable"
        assert r.preserved is True  # absence of evidence is not a preservation failure
        assert r.reason


def test_chapter_concatenation_allows_relocation():
    # Beat present in scene-3 before, relocated to scene-4 after. The concatenated chapter region still
    # contains it in both -> preserved (a legitimate move between revised scenes is not a drop).
    before = SCENE_BREAK.join([PROSE_REACTOR, PROSE_UNRELATED])  # scene3 has it, scene4 does not
    after = SCENE_BREAK.join([PROSE_UNRELATED, PROSE_REACTOR])  # moved to scene4
    r = beats_preserved(before, after, [BEAT_REACTOR])
    assert r.preserved is True
    assert r.dropped_beats == ()


def test_chapter_region_true_drop_is_reported():
    before = SCENE_BREAK.join([PROSE_REACTOR, PROSE_BREACH])
    after = SCENE_BREAK.join([PROSE_UNRELATED, PROSE_BREACH])  # reactor beat gone from the whole region
    r = beats_preserved(before, after, ordered_unique([BEAT_REACTOR, BEAT_BREACH]))
    assert r.preserved is False
    assert r.dropped_beats == (BEAT_REACTOR,)
    assert BEAT_BREACH not in r.dropped_beats


def test_paraphrase_threshold_behavior_is_documented():
    # KNOWN LIMITATION: the matcher is keyword-fraction based. A heavy paraphrase that shares no
    # distinctive keyword stems reads as "absent" even if the beat is arguably still present. This is
    # the accepted trade for a deterministic, LLM-free, advisory signal — documented, not endorsed.
    heavy_paraphrase = "Everything ran far too hot, and nobody could keep it within the numbers."
    r = beats_preserved(PROSE_REACTOR, heavy_paraphrase, [BEAT_REACTOR])
    assert r.status == "checked"
    assert r.preserved is False  # documents the false-alarm boundary


def test_ordered_unique_dedupes_preserving_order_and_drops_empty():
    assert ordered_unique(["b", "a", "b", "", "a", "c"]) == ["b", "a", "c"]
