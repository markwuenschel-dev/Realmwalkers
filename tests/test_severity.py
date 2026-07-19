"""SEV-ALIAS: the severity 'is-blocking' decision lives in one place (severity.is_blocking /
normalize_severity), folding the legacy 'hard' spelling. No module hand-rolls a ('hard','block') alias
tuple, and the gate SoT (issue_gates) treats a snapshot 'hard' as blocking."""

from __future__ import annotations

import re
from pathlib import Path

from dominion.shared.severity import is_blocking, issue_gates, normalize_severity

_SRC = Path(__file__).resolve().parent.parent / "src" / "dominion"
_ALIAS_TUPLE = re.compile(r'\(\s*"hard"\s*,\s*"block"\s*\)|\(\s*"block"\s*,\s*"hard"\s*\)')


def test_no_module_hand_rolls_the_hard_block_alias():
    offenders = [
        str(py.relative_to(_SRC))
        for py in _SRC.rglob("*.py")
        if py.name != "severity.py" and _ALIAS_TUPLE.search(py.read_text(encoding="utf-8"))
    ]
    assert not offenders, f"('hard','block') alias tuple should route through severity.is_blocking: {offenders}"


def test_is_blocking_and_normalize_fold_legacy_hard():
    assert is_blocking("hard") is True
    assert is_blocking("block") is True
    assert is_blocking("repair") is False
    assert is_blocking("warn") is False
    assert is_blocking("info") is False
    assert normalize_severity("  HARD ") == "block"  # strip + casefold + alias fold
    assert normalize_severity("repair") == "repair"  # non-alias passes through


def test_issue_gates_treats_snapshot_hard_as_block():
    gates = issue_gates("hard")
    assert gates["blocks_drafting"] is True
    assert gates["blocks_human_review"] is True
    assert gates["blocks_final_export"] is True
