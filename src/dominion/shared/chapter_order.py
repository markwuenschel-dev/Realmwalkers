"""The single source of truth for chapter READING ORDER.

Ordering is decoupled from the display number. `Chapter.position` is the ONLY sort key any reader,
export, or chapter list uses, and it is computed HERE from the chapter's structural `kind` and — for a
plain numbered chapter — its `chapter_no`. This is the ordering analogue of the shared label contract
(frontend `manuscript/labels.ts`): no consumer re-derives order, so a Prologue always leads and an
Epilogue / back-matter always trails regardless of import order or whether the section carries a number.

A numberless kind (prologue / interlude / epilogue / front_matter / back_matter) needs no `chapter_no`;
a plain `chapter` sorts by its number. `seq` disambiguates same-band sections that share no number (a
second front-matter section, an interlude): pass a per-book monotonic index — e.g. the book's existing
chapter count plus the section's index within an imported batch — so two numberless siblings never tie.

The absolute values are arbitrary sort keys; only their ORDER is meaningful. The band scheme below is
mirrored by the one-time SQL backfill in `migrations.py` (existing rows) so old and new rows interleave
correctly — keep the two in sync if the bands ever change.
"""

from __future__ import annotations

# Reader bands: front matter → prologue → chapters/interludes → epilogue → back matter. A kind's band
# dominates the sort, so the structure is globally correct before `chapter_no` is even consulted.
_BAND: dict[str, int] = {
    "front_matter": 0,
    "prologue": 1,
    "chapter": 2,
    "interlude": 2,
    "epilogue": 3,
    "back_matter": 4,
}
_DEFAULT_BAND = 2  # unknown / legacy kinds sort among ordinary chapters

_BAND_STRIDE = 1_000_000  # headroom for any realistic chapter_no within one band
# Numberless band-2 sections (interludes) sort AFTER all numbered chapters in their band; `seq` then
# orders them among themselves. Numbered chapters use their chapter_no directly (< this base). Section
# ranks (below) are also < this base, so a KNOWN front/back-matter section sorts ahead of an untyped one.
_NUMBERLESS_BASE = 100_000

# Canonical publishing order of front/back-matter section types WITHIN their band, so a Copyright page
# sorts before a Dedication before a Table of Contents before a Preface, and (in back matter) an Afterword
# before Acknowledgments before an Appendix/Glossary before the Author Bio. The band (from `kind`) already
# separates front from back; this only orders siblings inside one band. The generated pages (half_title/
# title_page/table_of_contents) are listed too so the exporter and this ordering agree on where they slot.
# KEEP IN SYNC with the frontend catalog order in manuscript/labels.ts (SECTION_ORDER).
_SECTION_ORDER: tuple[str, ...] = (
    # front matter
    "half_title",
    "title_page",
    "copyright",
    "dedication",
    "epigraph",
    "table_of_contents",
    "foreword",
    "preface",
    "introduction",
    "dramatis_personae",
    "map",
    "timeline",
    "pronunciation",
    # back matter
    "afterword",
    "acknowledgments",
    "appendix",
    "glossary",
    "author_note",
    "about_author",
    "author_bio",
    "about",
    "preview",
)
_SECTION_RANK: dict[str, int] = {slug: i for i, slug in enumerate(_SECTION_ORDER)}


def chapter_position(kind: str | None, chapter_no: int | None, seq: int = 0, section_type: str | None = None) -> int:
    """Deterministic global reading-order key for one chapter (see module docstring).

    `kind` is a ChapterKind value (or None → treated as a plain chapter). `chapter_no` is the display
    number for a plain chapter (ignored for other kinds, which are numberless). `section_type` orders
    front/back-matter siblings by the canonical publishing sequence (`_SECTION_ORDER`). `seq` breaks ties
    between numberless same-band sections that share no rank — pass a per-book monotonic index.
    """
    band = _BAND.get(kind or "chapter", _DEFAULT_BAND)
    # In the chapter band, anything WITH a number sorts by it (a plain chapter, or a numbered interlude/
    # legacy kind) — matching the SQL backfill's `ELSE 2000000 + chapter_no`.
    if band == _DEFAULT_BAND and chapter_no is not None:
        within = chapter_no
    # A recognized front/back-matter section sorts by its canonical publishing rank within the band.
    elif section_type in _SECTION_RANK:
        within = _SECTION_RANK[section_type]
    # Everything else numberless (a prologue/epilogue, an untyped/interlude section) falls after the ranked
    # ones, ordered by `seq`.
    else:
        within = _NUMBERLESS_BASE + seq
    return band * _BAND_STRIDE + within
