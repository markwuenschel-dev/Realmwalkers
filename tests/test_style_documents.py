"""Style guides resolve from Postgres, so the deployed drafter is not running unconstrained.

THE FAILURE THIS CLOSES IS A SILENT ONE. `series/` is gitignored by policy and deploy is a `git pull`,
so a style guide read from disk is present on the author's machine and absent on the box. The loader
returned None, the drafter ran without the constraint, and nothing reported anything. Working locally
and inert in production, with identical logs — the hardest shape of failure to notice.

These tests pin the resolution ORDER and the fallback, because "it found the text" is true in both the
working and the broken arrangement.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from dominion.shared.models import StyleDocument
from dominion.tools import push_style
from dominion.workers.context.style_source import load_style_document, read_from_disk, slug_for

DOC_A = "# Drift\n\n### 1. Thing  ·  `[PROSE]`\n\n**Correction:** Do the other thing.\n"
DOC_B = "# Drift\n\n### 1. Thing  ·  `[PROSE]`\n\n**Correction:** Do a DIFFERENT other thing.\n"


def test_slug_drops_the_series_root_and_the_extension():
    """The key has to survive a tree reorganisation that moves the file without changing what it is."""
    assert slug_for("series/style/forbidden_drift.md") == "style/forbidden_drift"
    assert slug_for("series/canon/timeline.md") == "canon/timeline"
    assert slug_for("style/voice_guide.md") == "style/voice_guide"


async def test_the_database_copy_wins_over_disk(db_factory, tmp_path, monkeypatch):
    """Order, not merely presence.

    A test that only asserted "some text came back" would pass against a disk-first implementation —
    which is the arrangement that is inert in production. So both sources exist here and differ, and
    the assertion is about WHICH one won.
    """
    on_disk = tmp_path / "forbidden_drift.md"
    on_disk.write_text(DOC_A, encoding="utf-8")

    async with db_factory() as s:
        s.add(StyleDocument(slug=slug_for(str(on_disk)), content=DOC_B, source_path="pushed"))
        await s.commit()

    async with db_factory() as s2:
        got = await load_style_document(s2, str(on_disk))
    assert got == DOC_B, "disk beat the database — this is the production-inert arrangement"


async def test_disk_is_the_fallback_when_nothing_was_pushed(db_factory, tmp_path):
    """Local development: the file is the thing the author edits and nothing has been uploaded yet."""
    on_disk = tmp_path / "voice_guide.md"
    on_disk.write_text(DOC_A, encoding="utf-8")
    async with db_factory() as s:
        assert await load_style_document(s, str(on_disk)) == DOC_A


async def test_neither_source_returns_none_and_does_not_raise(db_factory, tmp_path):
    """The box's state before anything is pushed. Must degrade, never fail a draft."""
    async with db_factory() as s:
        assert await load_style_document(s, str(tmp_path / "absent.md")) is None


async def test_an_empty_database_row_falls_through_to_disk(db_factory, tmp_path):
    """A blank row is a failed push, not an instruction to draft unconstrained. Treat it as absent."""
    on_disk = tmp_path / "forbidden_drift.md"
    on_disk.write_text(DOC_A, encoding="utf-8")
    async with db_factory() as s:
        s.add(StyleDocument(slug=slug_for(str(on_disk)), content="   \n", source_path="pushed"))
        await s.commit()
    async with db_factory() as s2:
        assert await load_style_document(s2, str(on_disk)) == DOC_A


# =================================================================================================
# The pusher
# =================================================================================================


async def test_push_is_idempotent_and_reports_what_changed(db_factory, tmp_path):
    docs = [("style/forbidden_drift", "series/style/forbidden_drift.md", DOC_A)]

    async with db_factory() as s:
        first = await push_style.push(s, docs, dry_run=False)
        await s.commit()
    assert "added" in first[0]

    async with db_factory() as s2:
        again = await push_style.push(s2, docs, dry_run=False)
        await s2.commit()
    assert "unchanged" in again[0], "an unchanged push reported a write"

    changed = [("style/forbidden_drift", "series/style/forbidden_drift.md", DOC_B)]
    async with db_factory() as s3:
        third = await push_style.push(s3, changed, dry_run=False)
        await s3.commit()
    assert "updated" in third[0]

    async with db_factory() as s4:
        row = (
            await s4.execute(select(StyleDocument).where(StyleDocument.slug == "style/forbidden_drift"))
        ).scalar_one()
        assert row.content == DOC_B


async def test_dry_run_writes_nothing(db_factory):
    docs = [("style/x", "series/style/x.md", DOC_A)]
    async with db_factory() as s:
        report = await push_style.push(s, docs, dry_run=True)
        await s.commit()
    assert "would add" in report[0]
    async with db_factory() as s2:
        assert (await s2.execute(select(StyleDocument))).scalars().first() is None, "a dry run wrote a row"


def test_sql_mode_survives_markdown_punctuation():
    """The deploy path pipes this into psql over ssh. Markdown is full of quotes, apostrophes and
    backslashes; a naive quoting scheme corrupts the document or breaks the statement."""
    nasty = "It's `**bold**` with 'single' and \"double\" quotes, a backslash \\, and $$ dollars.\n"
    sql = push_style.emit_sql([("style/x", "series/style/x.md", nasty)])
    assert sql.startswith("BEGIN;") and sql.rstrip().endswith("COMMIT;")
    assert nasty in sql, "the content was mangled by quoting"
    assert "ON CONFLICT (slug) DO UPDATE" in sql, "the SQL is not idempotent"


def test_sql_mode_escapes_a_document_containing_the_quote_tag():
    """A document that literally contains the dollar-quote tag must not be able to terminate it early —
    that would turn document text into executable SQL."""
    hostile = "prose containing $style$ verbatim\n"
    sql = push_style.emit_sql([("style/x", "series/style/x.md", hostile)])
    assert "$style1$" in sql, "the quote tag did not move aside for content that contained it"
    assert hostile in sql


def test_read_from_disk_returns_none_rather_than_raising():
    assert read_from_disk("series/style/definitely_not_here.md") is None


def test_the_real_style_tree_pushes_if_present():
    """End-to-end shape check against the author's actual tree, skipped where series/ is absent."""
    docs = push_style._documents("series/style")
    if not docs:
        pytest.skip("series/ is gitignored and absent in this environment")
    slugs = {slug for slug, _src, _content in docs}
    assert "style/forbidden_drift" in slugs
    assert all(content.strip() for _s, _p, content in docs), "an empty style document would push a blank row"


# =================================================================================================
# CRLF — the bug that shipped
# =================================================================================================


async def test_crlf_content_in_the_database_still_parses(db_factory):
    """The failure this file did not catch the first time, and the reason it did not.

    The first real push went through Windows text-mode stdout, which turned every newline into CRLF
    inside the dollar-quoted literal. 34,689 characters landed in the database intact-looking — and
    `scope_forbidden_drift` matched **zero** patterns, because its block regex anchors on a closing
    backtick followed by "\n". No error. No warning. The document loaded and the drafter ran
    unconstrained.

    Every earlier test here built its fixture in Python with LF, so none of them could see it. This one
    stores exactly what the shell produced.
    """
    from dominion.workers.context.forbidden_drift import scope_forbidden_drift

    lf = "# Drift\n\n### 1. Thing  ·  `[PROSE]`\n\n**Correction:** Do the other thing.\n"
    crlf = lf.replace("\n", "\r\n")

    async with db_factory() as s:
        s.add(StyleDocument(slug="style/crlf_probe", content=crlf, source_path="pushed-from-windows"))
        await s.commit()

    async with db_factory() as s2:
        got = await load_style_document(s2, "series/style/crlf_probe.md")

    assert got is not None
    assert "\r" not in got, "carriage returns survived into the loaded document"
    scoped = scope_forbidden_drift(got, pov="Marcus", present=["Marcus"], signals="")
    assert "Do the other thing." in scoped, "CRLF content loaded but scoped to nothing — the shipped bug"


def test_emitted_sql_contains_no_carriage_returns():
    """Guards the emitter end. `--sql` writes bytes precisely so the shell cannot translate them."""
    sql = push_style.emit_sql([("style/x", "series/style/x.md", "line one\nline two\n")])
    assert "\r" not in sql, "the emitter produced CRLF; it will corrupt every structured reader"


async def test_disk_content_is_normalised_too(tmp_path, db_factory):
    """A CRLF file in the working tree is the same trap by a different route — a repo cloned with
    `core.autocrlf=true` produces exactly this on Windows."""
    crlf = chr(13) + chr(10)
    body = crlf.join(["# Drift", "", "### 1. Thing  ·  `[PROSE]`", "", "**Correction:** Do it.", ""])
    doc = tmp_path / "forbidden_drift.md"
    doc.write_bytes(body.encode("utf-8"))

    async with db_factory() as s:
        got = await load_style_document(s, str(doc))

    assert got is not None, "the disk fallback did not find the file"
    assert chr(13) not in got, "carriage returns survived the disk fallback"


# --- the fail-closed half ---------------------------------------------------------------------
# Resolving from Postgres stops the box drafting against nothing. This stops it drafting SILENTLY
# against nothing: the absence becomes a blocker the Desk renders, not a warning nobody queries.


async def test_required_documents_present_in_the_database_report_nothing_missing(db_factory, tmp_path, monkeypatch):
    from dominion.shared.config import settings
    from dominion.workers.context.style_source import missing_required_style_documents

    drift = tmp_path / "forbidden_drift.md"
    dialogue = tmp_path / "dialogue_rules.md"
    monkeypatch.setattr(settings, "forbidden_drift_path", str(drift))
    monkeypatch.setattr(settings, "dialogue_rules_path", str(dialogue))

    async with db_factory() as s:
        s.add(StyleDocument(slug=slug_for(str(drift)), content=DOC_A, source_path="pushed"))
        s.add(StyleDocument(slug=slug_for(str(dialogue)), content=DOC_A, source_path="pushed"))
        await s.commit()

    async with db_factory() as s2:
        assert await missing_required_style_documents(s2) == ()


async def test_a_required_document_in_neither_source_is_reported(db_factory, tmp_path, monkeypatch):
    """The deploy box's actual state on 2026-08-28: drift pushed, dialogue rules not."""
    from dominion.shared.config import settings
    from dominion.workers.context.style_source import missing_required_style_documents

    drift = tmp_path / "forbidden_drift.md"
    dialogue = tmp_path / "dialogue_rules.md"  # never written, never pushed
    monkeypatch.setattr(settings, "forbidden_drift_path", str(drift))
    monkeypatch.setattr(settings, "dialogue_rules_path", str(dialogue))

    async with db_factory() as s:
        s.add(StyleDocument(slug=slug_for(str(drift)), content=DOC_A, source_path="pushed"))
        await s.commit()

    async with db_factory() as s2:
        assert await missing_required_style_documents(s2) == (slug_for(str(dialogue)),)


async def test_a_document_present_only_on_disk_satisfies_the_requirement(db_factory, tmp_path, monkeypatch):
    """Local development must not be blocked by a gate written for the deploy box."""
    from dominion.shared.config import settings
    from dominion.workers.context.style_source import missing_required_style_documents

    drift = tmp_path / "forbidden_drift.md"
    dialogue = tmp_path / "dialogue_rules.md"
    drift.write_text(DOC_A, encoding="utf-8")
    dialogue.write_text(DOC_A, encoding="utf-8")
    monkeypatch.setattr(settings, "forbidden_drift_path", str(drift))
    monkeypatch.setattr(settings, "dialogue_rules_path", str(dialogue))

    async with db_factory() as s:
        assert await missing_required_style_documents(s) == ()


async def test_a_blank_pushed_row_counts_as_missing(db_factory, tmp_path, monkeypatch):
    """A failed push is not permission to draft unconstrained — same rule the loader applies."""
    from dominion.shared.config import settings
    from dominion.workers.context.style_source import missing_required_style_documents

    drift = tmp_path / "forbidden_drift.md"
    dialogue = tmp_path / "dialogue_rules.md"
    monkeypatch.setattr(settings, "forbidden_drift_path", str(drift))
    monkeypatch.setattr(settings, "dialogue_rules_path", str(dialogue))

    async with db_factory() as s:
        s.add(StyleDocument(slug=slug_for(str(drift)), content="   \n", source_path="pushed"))
        s.add(StyleDocument(slug=slug_for(str(dialogue)), content=DOC_A, source_path="pushed"))
        await s.commit()

    async with db_factory() as s2:
        assert await missing_required_style_documents(s2) == (slug_for(str(drift)),)
