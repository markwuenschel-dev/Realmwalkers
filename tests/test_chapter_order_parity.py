"""ORDER-BACKFILL: the chapters.position backfill CASE in migrations.py and chapter_order.chapter_position
are two encodings of the same reading-order band scheme, kept aligned only by a 'keep in sync' comment.
This pins them together so a one-sided band edit fails here."""

from __future__ import annotations

from dominion.shared import migrations
from dominion.shared.chapter_order import chapter_position


def test_migration_position_backfill_matches_chapter_order():
    sql = "\n".join(migrations._BACKFILLS)
    # Each numberless kind's band value must appear in the CASE exactly as chapter_position computes it.
    for kind in ("front_matter", "prologue", "epilogue", "back_matter"):
        assert f"WHEN '{kind}' THEN {chapter_position(kind, None)}" in sql, kind
    # Plain chapters: the CASE's ELSE base + chapter_no must equal chapter_position for a numbered chapter.
    assert "2000000 + COALESCE(chapter_no, 0)" in sql
    for chapter_no in (0, 1, 42):
        assert chapter_position("chapter", chapter_no) == 2000000 + chapter_no
