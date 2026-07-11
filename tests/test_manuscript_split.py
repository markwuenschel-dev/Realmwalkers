"""Unit tests for the best-effort manuscript splitter (pure — no DB, runs anywhere).

Cases are modeled on the real, messy input in `book1/manuscript/_DRAFT_ORIGINAL_prerewrite.md`:
inconsistent chapter headers and `***` scene breaks that collide with inline bold.
"""

from __future__ import annotations

from dominion.workers.memory.manuscript_split import split_files, split_manuscript


def test_headers_and_star_breaks_split_into_chapters_and_scenes():
    text = (
        "Chapter 1\n\nAlpha opens the scene here.\n\n***\n\nBeta continues after the break.\n\n"
        "Chapter 2\n\nGamma is the whole of chapter two.\n"
    )
    m = split_manuscript(text)
    assert [c.chapter_no for c in m.chapters] == [1, 2]
    assert all(c.detected for c in m.chapters)
    assert [len(c.scenes) for c in m.chapters] == [2, 1]
    assert m.chapters[0].scenes[0].scene_no == 1
    assert "Alpha" in m.chapters[0].scenes[0].prose
    assert "Beta" in m.chapters[0].scenes[1].prose
    assert m.chapters[0].scenes[0].word_count == 5


def test_inline_bold_is_not_a_scene_break():
    # A system-message line that merely STARTS with *** must stay in the prose, not split the scene.
    text = "Chapter 1\n\nHe cast the spell.\n***Weak Holy Light** heals you for 23 hit points.\nThen silence.\n"
    m = split_manuscript(text)
    assert len(m.chapters) == 1
    assert len(m.chapters[0].scenes) == 1
    assert "Weak Holy Light" in m.chapters[0].scenes[0].prose


def test_escaped_star_break_and_dash_rule_split():
    assert len(split_manuscript("Chapter 1\n\nA.\n\n\\*\\*\\*\n\nB.\n").chapters[0].scenes) == 2
    assert len(split_manuscript("Chapter 1\n\nA.\n\n---\n\nB.\n").chapters[0].scenes) == 2


def test_roman_and_spelled_chapter_numbers():
    assert split_manuscript("Chapter IV\n\nprose.\n").chapters[0].chapter_no == 4
    assert split_manuscript("Chapter Ten — The Fall\n\nprose.\n").chapters[0].chapter_no == 10
    assert split_manuscript("Chapter Ten — The Fall\n\nprose.\n").chapters[0].title == "The Fall"


def test_headerless_leading_text_is_one_inferred_chapter():
    m = split_manuscript("Just prose, no header at all.\n")
    assert len(m.chapters) == 1
    assert m.chapters[0].chapter_no == 1
    assert m.chapters[0].detected is False
    assert any("no" in w.lower() and "header" in w.lower() for w in m.chapters[0].warnings)
    assert any("one chapter" in w.lower() for w in m.warnings)


def test_number_gap_warns_about_missing_headers():
    # Headers on 1, 2, 6 — chapters 3-5 have no header, so a gap warning fires (mirrors the draft).
    text = "Chapter 1\n\nA.\n\nChapter 2\n\nB.\n\nChapter 6\n\nF.\n"
    m = split_manuscript(text)
    assert [c.chapter_no for c in m.chapters] == [1, 2, 6]
    assert any("3–5" in w or "3-5" in w for w in m.warnings)


def test_out_of_order_header_warns():
    m = split_manuscript("Chapter 2\n\nA.\n\nChapter 1\n\nB.\n")
    assert any("out of order" in w for w in m.warnings)


def test_multi_file_merge_continues_numbering():
    # Two files, each a single headerless chapter → merged, ordered, numbered 1 then 2.
    m = split_files([("a.md", "First file prose.\n"), ("b.md", "Second file prose.\n")])
    assert [c.chapter_no for c in m.chapters] == [1, 2]
    assert "First" in m.chapters[0].scenes[0].prose
    assert "Second" in m.chapters[1].scenes[0].prose


def test_empty_text_reports_nothing_detected():
    m = split_manuscript("   \n\n")
    assert m.chapters == []
    assert any("no chapters or scenes" in w.lower() for w in m.warnings)


def test_prose_sentence_starting_with_chapter_is_not_a_header():
    # A long line that merely opens with "Chapter" is prose, not a header (length guard).
    text = (
        "Chapter 1\n\nChapter and verse, he recited the long passage aloud while the whole room "
        "listened in a hush that stretched on.\n"
    )
    m = split_manuscript(text)
    assert len(m.chapters) == 1  # the second "Chapter ..." line stayed prose
    assert "verse" in m.chapters[0].scenes[0].prose
