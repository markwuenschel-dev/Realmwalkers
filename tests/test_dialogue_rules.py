"""Pure unit tests for dialogue rules loading and scoping."""

from __future__ import annotations

from dominion.shared.config import settings
from dominion.workers.context import dialogue_rules as dr_mod


def test_load_dialogue_rules_scopes_character_blocks(tmp_path, monkeypatch):
    rules_file = tmp_path / "dialogue_rules.md"
    rules_file.write_text(
        "## General craft\nAlways use em dashes sparingly.\n\n"
        "### Marcus\nMarcus speaks in short clauses.\n\n"
        "### Serra\nSerra is formal and precise.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "dialogue_rules_path", str(rules_file))
    monkeypatch.setattr(dr_mod, "_dialogue_rules_warned", False)

    scoped = dr_mod.load_dialogue_rules(["Marcus"])
    assert scoped is not None
    assert "General craft" in scoped
    assert "Marcus speaks" in scoped
    assert "Serra is formal" not in scoped


def test_load_dialogue_rules_missing_file_returns_none(tmp_path, monkeypatch):
    missing = tmp_path / "no_such_rules.md"
    monkeypatch.setattr(settings, "dialogue_rules_path", str(missing))
    monkeypatch.setattr(dr_mod, "_dialogue_rules_warned", False)

    assert dr_mod.load_dialogue_rules(["Marcus"]) is None


def test_illyri_header_kept_when_marcus_present(tmp_path, monkeypatch):
    rules_file = tmp_path / "dialogue_rules.md"
    rules_file.write_text(
        "## General\nShared rules.\n\n### Illyri\nIllyri inner voice.\n\n### Eriadne\nOther voice.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "dialogue_rules_path", str(rules_file))
    monkeypatch.setattr(dr_mod, "_dialogue_rules_warned", False)

    scoped = dr_mod.load_dialogue_rules(["Marcus"])
    assert scoped is not None
    assert "Illyri inner voice" in scoped
    assert "Other voice" not in scoped
