"""The single manual-grant authorization predicate (AUTHZ-AXIS C+).

`is_manual_grant` centralizes the "is this authority_level a manual-grant Authorization Requirement?"
decision that four call sites re-derived by hand — two as `== HUMAN_REQUIRED`, two as
`== HUMAN_REQUIRED.value` (the drifted-`.value` inconsistency). These unit tests pin that the one
predicate treats the enum, its `.value`, and the raw persisted string identically, so the call sites
cannot drift again.
"""

from __future__ import annotations

from dominion.shared.enums import RepairAuthorityLevel, is_manual_grant


def test_manual_grant_true_for_human_required_every_form():
    assert is_manual_grant(RepairAuthorityLevel.HUMAN_REQUIRED)
    assert is_manual_grant(RepairAuthorityLevel.HUMAN_REQUIRED.value)
    assert is_manual_grant("human_required")


def test_manual_grant_false_for_every_auto_approvable_level():
    for level in RepairAuthorityLevel:
        if level is RepairAuthorityLevel.HUMAN_REQUIRED:
            continue
        assert not is_manual_grant(level), level
        assert not is_manual_grant(level.value), level


def test_manual_grant_false_for_unknown_or_empty():
    # A garbage/unknown value is not a manual-grant requirement (it fails ceiling validity elsewhere).
    assert not is_manual_grant("garbage")
    assert not is_manual_grant("")
