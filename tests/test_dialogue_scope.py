"""Dialogue-rules scoping: general craft is always-on, per-character profiles are scene-scoped."""
from __future__ import annotations

from dominion.workers.context import (
    _header_names,
    _load_dialogue_rules,
    _scope_dialogue_rules,
)

_DOC = """\
# Dialogue Rules

## The Fundamental Rule
People do not say what they mean.

## Tool 2: Character Voice Profiles

Intro paragraph that must always survive.

### Marcus (Soren)
Soren talks in revisions.

---

### Serra
Serra says it and stops.

---

### Ayla
Short. Dry. Off-center.

---

## Formatting
Use "said".
"""


def _headers(text: str) -> list[str]:
    return [line[4:] for line in text.splitlines() if line.startswith("### ")]


def test_header_names_parses_aliases():
    assert _header_names("Marcus (Soren)") == {"marcus", "soren"}
    assert _header_names("Serra") == {"serra"}
    assert _header_names("Ayla") == {"ayla", "soren", "marcus"}  # rides along with Soren


def test_scope_keeps_general_and_present_drops_absent():
    scoped = _scope_dialogue_rules(_DOC, ["Serra"])
    assert _headers(scoped) == ["Serra"]
    # general craft survives regardless of cast
    assert "## The Fundamental Rule" in scoped
    assert "## Formatting" in scoped
    assert "## Tool 2: Character Voice Profiles" in scoped
    assert "Intro paragraph that must always survive." in scoped
    # absent characters' idiolect is gone
    assert "Soren talks in revisions." not in scoped
    assert "Short. Dry. Off-center." not in scoped


def test_scope_matches_pov_alias_and_ayla_rides_with_soren():
    scoped = _scope_dialogue_rules(_DOC, ["Soren"])  # POV name, not the header's "Marcus (Soren)"
    assert _headers(scoped) == ["Marcus (Soren)", "Ayla"]
    assert "Serra says it and stops." not in scoped


def test_scope_empty_present_returns_everything():
    scoped = _scope_dialogue_rules(_DOC, [])
    assert _headers(scoped) == ["Marcus (Soren)", "Serra", "Ayla"]


def test_load_dialogue_rules_scopes_the_real_file():
    full = _load_dialogue_rules([])
    soren = _load_dialogue_rules(["Soren"])
    assert full is not None and soren is not None
    # only the POV profile (and Ayla, bound to Soren) survive
    assert set(_headers(soren)) == {"Marcus (Soren)", "Ayla"}
    assert "Serra" not in _headers(soren)
    # general craft is always-on
    assert "## Formatting" in soren
    assert "The Fundamental Rule" in soren
    # scoping actually shrinks the always-on payload
    assert len(soren) < len(full)
