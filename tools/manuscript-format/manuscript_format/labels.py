"""Port of ``frontend/src/desk/manuscript/labels.ts`` + ``metadata.ts``.

The single label contract for the spine and all three emitters. NO consumer resolves a structural
label independently — they all call these functions. This is what stops Reader DOCX, Shunn DOCX,
and Markdown from drifting on how a Prologue or a Part is titled.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

KNOWN_CHAPTER_KINDS: tuple[str, ...] = (
    "chapter",
    "prologue",
    "interlude",
    "epilogue",
    "front_matter",
    "back_matter",
)

#: The kinds that render as a titled section rather than a numbered chapter.
SECTION_KINDS: tuple[str, ...] = ("front_matter", "back_matter")


def is_known_chapter_kind(kind: str | None) -> bool:
    return (kind or "") in KNOWN_CHAPTER_KINDS


def is_section_kind(kind: str | None) -> bool:
    return (kind or "") in SECTION_KINDS


# Reader display names for non-'chapter' kinds.
KIND_LABEL: dict[str, str] = {
    "prologue": "Prologue",
    "interlude": "Interlude",
    "epilogue": "Epilogue",
    "front_matter": "Front Matter",
    "back_matter": "Back Matter",
}



_NUMBER_WORDS: dict[int, str] = {
    1: "One", 2: "Two", 3: "Three", 4: "Four", 5: "Five",
    6: "Six", 7: "Seven", 8: "Eight", 9: "Nine", 10: "Ten",
    11: "Eleven", 12: "Twelve", 13: "Thirteen", 14: "Fourteen",
    15: "Fifteen", 16: "Sixteen", 17: "Seventeen", 18: "Eighteen",
    19: "Nineteen", 20: "Twenty",
}

_ROMAN: tuple[tuple[int, str], ...] = (
    (1000, "M"),
    (900, "CM"),
    (500, "D"),
    (400, "CD"),
    (100, "C"),
    (90, "XC"),
    (50, "L"),
    (40, "XL"),
    (10, "X"),
    (9, "IX"),
    (5, "V"),
    (4, "IV"),
    (1, "I"),
)


def to_roman(n: int) -> str:
    """Roman numeral for Part labels. Falls back to the arabic number out of range — never raises."""
    if not isinstance(n, int) or isinstance(n, bool) or n <= 0:
        return str(n)
    out = ""
    rem = n
    for value, sym in _ROMAN:
        while rem >= value:
            out += sym
            rem -= value
    return out


def part_kind_word(kind: str | None) -> str:
    """The label WORD for a Part-level grouping: ``act`` → "Act", anything else → "Part"."""
    return "Act" if kind == "act" else "Part"


def part_label(part_no: int, title: str | None = None, kind: str | None = None) -> str:
    """"Part I — The Gathering Storm" / "Act I — …" (title optional → just "Part I")."""
    head = f"{part_kind_word(kind)} {to_roman(part_no)}"
    t = (title or "").strip()
    return f"{head} — {t}" if t else head


def volume_label(volume_no: int, title: str | None = None) -> str:
    """"Volume I — The Long Winter" (title optional → just "Volume I")."""
    head = f"Volume {to_roman(volume_no)}"
    t = (title or "").strip()
    return f"{head} — {t}" if t else head


def chapter_label(kind: str | None = None, chapter_no: int | None = None) -> str:
    """"Chapter Three" for ordinary chapters one through twenty; digits beyond that."""
    k = kind or "chapter"
    if k == "chapter" or not is_known_chapter_kind(k):
        if chapter_no is None:
            return "Chapter"
        return f"Chapter {_NUMBER_WORDS.get(chapter_no, str(chapter_no))}"
    return KIND_LABEL[k]


#: Display names for the AUTHORED front/back-matter section types.
SECTION_TYPES: dict[str, str] = {
    "copyright": "Copyright",
    "dedication": "Dedication",
    "epigraph": "Epigraph",
    "foreword": "Foreword",
    "preface": "Preface",
    "introduction": "Introduction",
    "dramatis_personae": "Dramatis Personae",
    "map": "Map",
    "timeline": "Timeline",
    "pronunciation": "Pronunciation Guide",
    "afterword": "Afterword",
    "acknowledgments": "Acknowledgments",
    "appendix": "Appendix",
    "glossary": "Glossary",
    "author_note": "Author's Note",
    "about_author": "About the Author",
    "author_bio": "Author Bio",
    "preview": "Preview",
}


class GENERATED_SECTION:
    """The generated (not authored) production pages the Reader export builds from metadata."""

    half_title = "half_title"
    title_page = "title_page"
    table_of_contents = "table_of_contents"


#: Canonical publishing order of every section slug (authored AND generated) within its band.
#: KEEP IN SYNC with the backend ``_SECTION_ORDER`` in shared/chapter_order.py.
SECTION_ORDER: tuple[str, ...] = (
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

_SECTION_RANK: dict[str, int] = {slug: i for i, slug in enumerate(SECTION_ORDER)}

#: JS ``Number.MAX_SAFE_INTEGER`` — an unknown/absent slug sorts after all known ones.
MAX_SAFE_INTEGER = 9007199254740991


def section_rank(section_type: str | None) -> int:
    return _SECTION_RANK.get((section_type or "").strip(), MAX_SAFE_INTEGER)


def _title_case_slug(slug: str) -> str:
    return " ".join(w[0].upper() + w[1:] for w in re.split(r"[_\s]+", slug) if w)


def section_type_label(section_type: str | None) -> str | None:
    """Display name for a section-type slug (known → catalog name, unknown → title-cased)."""
    t = (section_type or "").strip()
    if not t:
        return None
    return SECTION_TYPES.get(t) or _title_case_slug(t)


def section_label(
    kind: str | None = None,
    title: str | None = None,
    section_type: str | None = None,
    chapter_no: int | None = None,
) -> str:
    """Author's explicit title → the section-type display name → the generic kind label."""
    k = kind or "chapter"
    if k in ("front_matter", "back_matter"):
        return (title or "").strip() or section_type_label(section_type) or KIND_LABEL[k]
    return chapter_label(k, chapter_no)


def resolve_chapter_label(
    kind: str | None = None,
    title: str | None = None,
    section_type: str | None = None,
    chapter_no: int | None = None,
) -> str:
    """The one dispatcher the spine uses to resolve a chapter node's primary label."""
    if is_section_kind(kind):
        return section_label(kind, title, section_type, chapter_no)
    return chapter_label(kind, chapter_no)


# ── Export metadata (port of manuscript/metadata.ts) ─────────────────────────


@dataclass
class ExportMetadata:
    """Renderer-neutral export/provenance metadata, resolved ONCE and threaded through.

    NOTHING here defaults to a project identity, so a standalone book emits no series line
    rather than silently inheriting one.
    """

    title: str
    series: str | None = None
    book_number: int | None = None
    subtitle: str | None = None
    author: str | None = None


_ONES = (
    "Zero",
    "One",
    "Two",
    "Three",
    "Four",
    "Five",
    "Six",
    "Seven",
    "Eight",
    "Nine",
    "Ten",
    "Eleven",
    "Twelve",
)


def book_number_label(n: int | None) -> str | None:
    """"BOOK ONE" for 0..12 (spelled-out is the print convention), "BOOK 13" beyond."""
    if n is None or not isinstance(n, int) or isinstance(n, bool) or n < 0:
        return None
    word = _ONES[n] if n < len(_ONES) else str(n)
    return f"BOOK {word.upper()}"


def _clean(s: str | None) -> str | None:
    t = (s or "").strip()
    return t or None


def resolve_export_metadata(
    title: str | None,
    series: str | None = None,
    book_no: int | None = None,
    subtitle: str | None = None,
    author: str | None = None,
) -> ExportMetadata:
    """Empty/null fields collapse to ``None`` (never a placeholder string)."""
    return ExportMetadata(
        title=_clean(title) or "Untitled",
        series=_clean(series),
        book_number=book_no,
        subtitle=_clean(subtitle),
        author=_clean(author),
    )
