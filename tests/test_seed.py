"""Seed-importer tests (DESIGN §14, Phase 2).

The pure parsing/extraction is unit-tested with no DB (always runs). The import itself is exercised
against real Postgres via `db_factory`, with the LLM summary fold turned off so no API key is needed;
it skips when Postgres isn't reachable (see conftest)."""
from __future__ import annotations

from sqlalchemy import select

from dominion.shared.enums import SceneStatus
from dominion.shared.models import Chapter, Scene
from dominion.workers.memory import canon_rag, seed

# A scene file in the manuscript template's shape: frontmatter, H1 title, a `>` brief, a `---` rule,
# then prose containing an internal `---` scene break, then the trailing editorial notes block.
_SCENE_FILE = """\
---
scene_id: SCENE-001
title: Aim Not Found
pov: Marcus (POV)
characters_present: [Marcus, Serra]
status: draft
---

# Aim Not Found

> **Scene goal:** establish the ordinary world.
> **Exiting state:** the first wrongness lands.

<!-- WRITER NOTES: delete before drafting. -->

---

The model was lying to him, and it was being polite about it.

---

The monitors went to snow, and nothing resolved.

## Scene-local notes (for the Orchestrator, not the reader)

- Continuity risks: the anomaly must recur in a later scene.
"""


# --- pure parsing (no DB) --------------------------------------------------------------------------

def test_split_frontmatter_reads_scalar_keys():
    meta, body = seed._split_frontmatter(_SCENE_FILE)
    assert meta["scene_id"] == "SCENE-001"
    assert meta["title"] == "Aim Not Found"
    assert meta["pov"] == "Marcus (POV)"
    assert body.lstrip().startswith("# Aim Not Found")


def test_split_frontmatter_absent_returns_body_unchanged():
    meta, body = seed._split_frontmatter("No frontmatter here.\n")
    assert meta == {}
    assert body == "No frontmatter here.\n"


def test_extract_prose_strips_scaffold_keeps_internal_breaks():
    _meta, body = seed._split_frontmatter(_SCENE_FILE)
    prose = seed._extract_prose(body)
    assert prose.startswith("The model was lying to him")
    # internal scene-break rule survives; title / brief / writer-note / trailing notes are gone
    assert "\n---\n" in prose
    assert "Aim Not Found" not in prose
    assert "Scene goal" not in prose
    assert "WRITER NOTES" not in prose
    assert "Scene-local notes" not in prose
    assert "Continuity risks" not in prose
    assert prose.endswith("nothing resolved.")


def test_extract_prose_empty_when_only_scaffold():
    assert seed._extract_prose("# Title\n\n> just a brief\n\n---\n") == ""


def test_normalize_pov_drops_parenthetical():
    assert seed._normalize_pov("Marcus (POV)") == "Marcus"
    assert seed._normalize_pov("Marcus Vye") == "Marcus Vye"
    assert seed._normalize_pov(None) == "Unknown"


def test_scene_and_chapter_numbers():
    from pathlib import Path
    p = Path("SCENE-001_earth-opening.md")
    assert seed._scene_no({"scene_id": "SCENE-001"}, p, fallback=9) == 1
    assert seed._scene_no({"scene": "4"}, p, fallback=9) == 4          # explicit wins
    assert seed._scene_no({}, Path("untitled.md"), fallback=9) == 9    # fallback to run position
    assert seed._chapter_no({"chapter": "3"}, default=1) == 3
    assert seed._chapter_no({}, default=1) == 1


# --- import against Postgres (skips without a DB) --------------------------------------------------

async def test_seed_imports_scenes_and_is_idempotent(db_factory, tmp_path):
    scenes = tmp_path / "scenes"
    scenes.mkdir()
    (scenes / "SCENE-001_open.md").write_text(_SCENE_FILE, encoding="utf-8")
    (scenes / "_SCENE_TEMPLATE.md").write_text("# template\n\n> ignore me\n", encoding="utf-8")

    async with db_factory() as s:
        report = await seed.seed_manuscript(
            s, book_title="Dominion Realm", scenes_dir=scenes, canon_dir=None, build_summaries=False,
        )
        await s.commit()

    assert len(report.imported) == 1
    assert report.skipped == []  # the _-prefixed template is excluded, not "skipped (no prose)"

    async with db_factory() as s:
        # _-prefixed files dropped, so exactly one chapter + one approved seed scene exist.
        chapters = (await s.execute(select(Chapter))).scalars().all()
        assert [c.pov for c in chapters] == ["Marcus"]
        scene = (await s.execute(select(Scene))).scalar_one()
        assert scene.status == SceneStatus.APPROVED
        assert scene.scene_no == 1
        assert scene.prose_source == "human"
        assert scene.prose.startswith("The model was lying to him")

    # Re-running updates in place rather than inserting a duplicate.
    async with db_factory() as s:
        report2 = await seed.seed_manuscript(
            s, book_title="Dominion Realm", scenes_dir=scenes, canon_dir=None, build_summaries=False,
        )
        await s.commit()
    assert report2.imported == [] and len(report2.updated) == 1

    async with db_factory() as s:
        assert len((await s.execute(select(Scene))).scalars().all()) == 1


async def test_seed_warns_when_file_pov_differs_from_existing_chapter(db_factory, tmp_path):
    scenes = tmp_path / "scenes"
    scenes.mkdir()
    # Both files land in chapter 1; the second declares a different POV than the chapter now has.
    (scenes / "SCENE-001.md").write_text(
        "---\nscene: 1\npov: Marcus\n---\n\nMarcus stares at the model.\n", encoding="utf-8"
    )
    (scenes / "SCENE-002.md").write_text(
        "---\nscene: 2\npov: Serra\n---\n\nSerra sets her feet.\n", encoding="utf-8"
    )

    async with db_factory() as s:
        report = await seed.seed_manuscript(
            s, book_title="Dominion Realm", scenes_dir=scenes, canon_dir=None, build_summaries=False,
        )
        await s.commit()

    assert len(report.imported) == 2
    assert len(report.warnings) == 1
    assert "Serra" in report.warnings[0] and "Marcus" in report.warnings[0]


async def test_seed_builds_canon_index_when_canon_dir_given(db_factory, tmp_path):
    scenes = tmp_path / "scenes"
    scenes.mkdir()
    canon = tmp_path / "canon"
    canon.mkdir()
    (canon / "serra.md").write_text(
        "Serra is a duelist who commits past the point a careful player would bail.", encoding="utf-8"
    )

    async with db_factory() as s:
        report = await seed.seed_manuscript(
            s, book_title="Dominion Realm", scenes_dir=scenes, canon_dir=canon, build_summaries=False,
        )
        await s.commit()
        assert report.canon_chunks >= 1

    # the indexed passage is retrievable by a semantically-overlapping query
    async with db_factory() as s:
        from dominion.shared.models import Book
        book = (await s.execute(select(Book))).scalar_one()
        hits = await canon_rag.retrieve(s, book_id=book.id, query="duelist who commits", k=3)
        assert any("Serra" in h for h in hits)
