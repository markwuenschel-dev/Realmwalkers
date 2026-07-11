"""The shared chapter reading-order key (shared/chapter_order.py): the ONE place ordering is derived,
so a numberless section sorts into the right band without a chapter_no to collide on."""

from __future__ import annotations

from dominion.shared.chapter_order import chapter_position


def test_bands_order_front_prologue_chapters_epilogue_back():
    front = chapter_position("front_matter", None, seq=0)
    prologue = chapter_position("prologue", None, seq=0)
    ch1 = chapter_position("chapter", 1)
    ch2 = chapter_position("chapter", 2)
    epilogue = chapter_position("epilogue", None, seq=0)
    back = chapter_position("back_matter", None, seq=0)
    assert front < prologue < ch1 < ch2 < epilogue < back


def test_numbered_chapters_sort_by_number():
    assert chapter_position("chapter", 3) < chapter_position("chapter", 10)


def test_numberless_siblings_are_disambiguated_by_seq():
    a = chapter_position("prologue", None, seq=0)
    b = chapter_position("prologue", None, seq=1)
    assert a != b and a < b


def test_unknown_kind_sorts_with_chapters():
    # A legacy/unknown kind carrying a number sits in the chapter band by that number.
    assert chapter_position("chapter", 5) == chapter_position("weird_legacy_kind", 5)


def test_prologue_leads_chapter_one_matches_backfill_scheme():
    # Mirrors migrations.py's SQL backfill constants so old and new rows interleave identically.
    assert chapter_position("prologue", None, seq=0) == 1_100_000
    assert chapter_position("chapter", 1) == 2_000_001


def test_front_matter_sections_sort_in_canonical_publishing_order():
    order = ["half_title", "title_page", "copyright", "dedication", "table_of_contents", "preface"]
    positions = [chapter_position("front_matter", None, section_type=st) for st in order]
    assert positions == sorted(positions)  # strictly increasing in the canonical sequence


def test_back_matter_sections_sort_afterword_ack_appendix_glossary_bio():
    order = ["afterword", "acknowledgments", "appendix", "glossary", "author_bio"]
    positions = [chapter_position("back_matter", None, section_type=st) for st in order]
    assert positions == sorted(positions)


def test_full_sequence_orders_front_prologue_body_epilogue_back():
    copyright_ = chapter_position("front_matter", None, section_type="copyright")
    toc = chapter_position("front_matter", None, section_type="table_of_contents")
    prologue = chapter_position("prologue", None)
    ch1 = chapter_position("chapter", 1)
    epilogue = chapter_position("epilogue", None)
    afterword = chapter_position("back_matter", None, section_type="afterword")
    author_bio = chapter_position("back_matter", None, section_type="author_bio")
    assert copyright_ < toc < prologue < ch1 < epilogue < afterword < author_bio


def test_known_section_sorts_ahead_of_untyped_sibling():
    typed = chapter_position("back_matter", None, section_type="glossary")
    untyped = chapter_position("back_matter", None, seq=0)  # no section_type
    assert typed < untyped
