"""Dialogue rules load from Postgres, so the deployed drafter is not writing dialogue unconstrained.

THE FAILURE THIS CLOSES IS THE SAME SILENT ONE `test_style_documents.py` describes, one document
later. `forbidden_drift` was routed through `load_style_document`; `dialogue_rules` was not, and kept
reading `series/style/dialogue_rules.md` off disk. `series/` is gitignored and deploy is a `git pull`,
so on the box that file does not exist: the loader returned None, `assemble_context` put None into
`SceneContext.dialogue_rules`, `drafter.py`'s `if ctx.dialogue_rules:` skipped the block, and every
deployed draft wrote dialogue with no rules at all. Observed in production on 2026-08-28:

    [context] dialogue rules not found at 'series/style/dialogue_rules.md'; drafts will run without them

One unstructured `print`, once per process, and prose that looks fine. These tests pin the resolution
ORDER, not merely that some text came back — "it found the rules" is true of the broken arrangement
too, on the author's machine.
"""

from __future__ import annotations

from dominion.shared.config import settings
from dominion.shared.models import StyleDocument
from dominion.workers.context.dialogue_rules import _scope_dialogue_rules, load_dialogue_rules
from dominion.workers.context.style_source import slug_for

RULES = (
    "## General craft\nAlways use em dashes sparingly.\n\n"
    "### Marcus\nMarcus speaks in short clauses.\n\n"
    "### Serra\nSerra is formal and precise.\n"
)
DB_RULES = (
    "## General craft\nRules that came from the DATABASE.\n\n"
    "### Marcus\nMarcus speaks in short clauses.\n\n"
    "### Serra\nSerra is formal and precise.\n"
)


# --- scoping: pure, no IO ---------------------------------------------------------------------


def test_scoping_keeps_only_blocks_for_the_cast_on_page():
    scoped = _scope_dialogue_rules(RULES, ["Marcus"])
    assert "General craft" in scoped
    assert "Marcus speaks" in scoped
    assert "Serra is formal" not in scoped


def test_illyri_header_kept_when_marcus_present():
    """`_BLOCK_ALIASES` maps the inner voice onto its host — dropping it would silence the pairing."""
    text = "## General\nShared rules.\n\n### Illyri\nIllyri inner voice.\n\n### Eriadne\nOther voice.\n"
    scoped = _scope_dialogue_rules(text, ["Marcus"])
    assert "Illyri inner voice" in scoped
    assert "Other voice" not in scoped


def test_an_empty_roster_loads_the_full_ruleset_unscoped():
    """`POST /enrich` passes no roster on purpose: injected prose carries no cast list, so scoping to a
    guess would silently DELETE real rules. Empty must mean "all", never "none"."""
    assert _scope_dialogue_rules(RULES, []) == RULES
    assert "Serra is formal" in _scope_dialogue_rules(RULES, [])


# --- resolution order: the production bug ------------------------------------------------------


async def test_rules_resolve_from_postgres_when_the_file_is_absent(db_factory, tmp_path, monkeypatch):
    """THE PRODUCTION CASE. No `series/` on the box; the rules must still reach the drafter."""
    absent = tmp_path / "dialogue_rules.md"  # deliberately never written
    monkeypatch.setattr(settings, "dialogue_rules_path", str(absent))

    async with db_factory() as s:
        s.add(StyleDocument(slug=slug_for(str(absent)), content=DB_RULES, source_path="pushed"))
        await s.commit()

    async with db_factory() as s2:
        scoped = await load_dialogue_rules(s2, ["Marcus"])
    assert scoped is not None, "the deploy box would draft dialogue with no rules"
    assert "came from the DATABASE" in scoped
    assert "Serra is formal" not in scoped, "scoping still applies to the database copy"


async def test_the_database_copy_wins_over_disk(db_factory, tmp_path, monkeypatch):
    """Order, not presence. A disk-first implementation passes any test that only checks for text."""
    on_disk = tmp_path / "dialogue_rules.md"
    on_disk.write_text(RULES, encoding="utf-8")
    monkeypatch.setattr(settings, "dialogue_rules_path", str(on_disk))

    async with db_factory() as s:
        s.add(StyleDocument(slug=slug_for(str(on_disk)), content=DB_RULES, source_path="pushed"))
        await s.commit()

    async with db_factory() as s2:
        scoped = await load_dialogue_rules(s2, ["Marcus"])
    assert scoped is not None
    assert "came from the DATABASE" in scoped, "disk beat the database — the production-inert order"


async def test_crlf_in_a_pushed_document_still_scopes(db_factory, tmp_path, monkeypatch):
    """`_CHAR_BLOCK_RE` anchors on "\\n". A document pushed through a Windows shell arrives CRLF, and
    before `load_style_document` normalised on read that made every `###` block match nothing — the
    scoper returned empty, the drafter ran unconstrained, and the logs stayed clean. Same class as the
    `forbidden_drift` CRLF fix (6281c69), one document later."""
    absent = tmp_path / "dialogue_rules.md"
    monkeypatch.setattr(settings, "dialogue_rules_path", str(absent))

    async with db_factory() as s:
        s.add(StyleDocument(slug=slug_for(str(absent)), content=DB_RULES.replace("\n", "\r\n"), source_path="win"))
        await s.commit()

    async with db_factory() as s2:
        scoped = await load_dialogue_rules(s2, ["Marcus"])
    assert scoped is not None, "CRLF scoped every character block away"
    assert "Marcus speaks" in scoped
    assert "Serra is formal" not in scoped
    assert "\r" not in scoped


async def test_disk_is_still_the_fallback_for_local_work(db_factory, tmp_path, monkeypatch):
    """The author edits the file; nothing pushed yet. That has to keep working."""
    on_disk = tmp_path / "dialogue_rules.md"
    on_disk.write_text(RULES, encoding="utf-8")
    monkeypatch.setattr(settings, "dialogue_rules_path", str(on_disk))

    async with db_factory() as s:
        scoped = await load_dialogue_rules(s, ["Marcus"])
    assert scoped is not None
    assert "Marcus speaks" in scoped


async def test_neither_source_returns_none_without_raising(db_factory, tmp_path, monkeypatch):
    """Degrade, never crash a draft — the gate in `draft_readiness` is what refuses to draft."""
    monkeypatch.setattr(settings, "dialogue_rules_path", str(tmp_path / "nothing_here.md"))
    async with db_factory() as s:
        assert await load_dialogue_rules(s, ["Marcus"]) is None
