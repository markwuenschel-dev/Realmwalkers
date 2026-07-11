"""Best-effort manuscript splitter for the drag-and-drop uploader.

Turns a dropped file (whole-book, whole-chapter, or multi-scene) into a chapter -> scene structure
for the uploader's preview. **Pure** — no DB, no LLM, no I/O — so `/books/{id}/manuscript/parse` can
call it and the unit tests can exercise it on any string.

The real input is messy (see `book1/manuscript/_DRAFT_ORIGINAL_prerewrite.md`): chapter headers are
inconsistent (present on some chapters, absent on others) and scene breaks (`***` / `---`) collide
with inline bold. So detection is deliberately forgiving and every ambiguity is surfaced as a
warning — the human corrects boundaries in the preview (a later slice). This slice only *detects and
reports*; nothing here writes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Reuse the scene-template prose cleanup so a per-scene frontmatter file and a raw dump both land
# clean (strips HTML writer-notes, the trailing "Scene-local notes" block, and leading heading/rule
# lines). Same package — the leading underscore marks it package-internal, not off-limits here.
from dominion.workers.memory.seed import _extract_prose

# A line is a chapter header when — after optional leading `#`s — it starts with the word "chapter".
# The whole (stripped) line must be short, so a prose sentence that merely opens with "Chapter" does
# not qualify. Whatever follows "chapter" is parsed for a number + trailing title.
_CHAPTER_RE = re.compile(r"^#{0,3}\s*chapter\b[\s:.\-]*(.*)$", re.IGNORECASE)
_CHAPTER_MAX_LEN = 60

# A scene break is a line whose ENTIRE stripped content is a divider marker. Requiring the whole line
# to match is what stops inline bold (`***Weak Holy Light**`) from being read as a break. Asterisks
# tolerate optional backslashes and spaces, so `***`, `* * *`, and the draft's escaped `\*\*\*` all
# match; `---`/`___`/`⁂` rules and a `# Scene`/`Scene N` heading also count.
_SCENE_BREAK_RE = re.compile(
    r"^(?:(?:\\?\*\s*){3,}|-{3,}|_{3,}|⁂|#{1,4}\s*scene\b.*|scene\s+\d+\.?)$",
    re.IGNORECASE,
)

_SPELLED = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
}
_ROMAN_RE = re.compile(r"^m{0,4}(cm|cd|d?c{0,3})(xc|xl|l?x{0,3})(ix|iv|v?i{0,3})$", re.IGNORECASE)
_ROMAN_VALUES = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}


@dataclass
class ParsedScene:
    scene_no: int
    prose: str
    word_count: int


@dataclass
class ParsedChapter:
    chapter_no: int
    title: str | None
    detected: bool  # True when an explicit "Chapter N" header was found; False = we inferred it
    scenes: list[ParsedScene] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class ParsedManuscript:
    chapters: list[ParsedChapter] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class _RawChapter:
    """A chapter mid-parse: number may be None (headerless) until the finalize pass assigns it."""

    number: int | None
    title: str | None
    detected: bool
    scene_proses: list[str] = field(default_factory=list)


def _roman_to_int(token: str) -> int | None:
    """Convert a roman-numeral token to an int, or None if it isn't a well-formed roman numeral."""
    if not _ROMAN_RE.match(token):
        return None
    total = 0
    prev = 0
    for ch in reversed(token.lower()):
        val = _ROMAN_VALUES[ch]
        total += -val if val < prev else val
        prev = max(prev, val)
    return total or None


def _clean_title(raw: str) -> str | None:
    """Strip the separators that sit between a chapter number and its title (``3 — The Lobby``)."""
    title = raw.strip().lstrip("-—:.·|").strip()
    return title or None


def _parse_leading_number(rest: str) -> tuple[int | None, str | None]:
    """Pull a leading chapter number (arabic, spelled, or roman) off ``rest``; return (num, title)."""
    m = re.match(r"^(\d+)\b(.*)$", rest)
    if m:
        return int(m.group(1)), _clean_title(m.group(2))
    m = re.match(r"^([A-Za-z]+)\b(.*)$", rest)
    if m:
        word, tail = m.group(1), m.group(2)
        if word.lower() in _SPELLED:
            return _SPELLED[word.lower()], _clean_title(tail)
        roman = _roman_to_int(word)
        if roman is not None:
            return roman, _clean_title(tail)
    return None, _clean_title(rest)


def _match_chapter(line: str) -> tuple[int | None, str | None] | None:
    """Return (chapter_no|None, title|None) if the line is a chapter header, else None."""
    s = line.strip()
    if not s or len(s) > _CHAPTER_MAX_LEN:
        return None
    m = _CHAPTER_RE.match(s)
    if not m:
        return None
    return _parse_leading_number(m.group(1))


def _is_scene_break(line: str) -> bool:
    return bool(_SCENE_BREAK_RE.match(line.strip()))


def _parse_raw(text: str) -> tuple[list[_RawChapter], list[str]]:
    """Walk the text once, cutting it into raw chapters and scene proses at detected boundaries."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    chapters: list[_RawChapter] = []
    warnings: list[str] = []
    current: _RawChapter | None = None
    scene_buf: list[str] = []

    def flush_scene() -> None:
        nonlocal current, scene_buf
        prose = _extract_prose("\n".join(scene_buf)).strip()
        scene_buf = []
        if not prose:
            return
        if current is None:  # prose before any header — an inferred leading chapter
            current = _RawChapter(number=None, title=None, detected=False)
            chapters.append(current)
        current.scene_proses.append(prose)

    for line in lines:
        header = _match_chapter(line)
        if header is not None:
            flush_scene()  # trailing scene of the previous chapter closes here
            current = _RawChapter(number=header[0], title=header[1], detected=True)
            chapters.append(current)
            continue
        if _is_scene_break(line):
            flush_scene()
            continue
        scene_buf.append(line)
    flush_scene()
    return chapters, warnings


def _finalize(raw: list[_RawChapter], warnings: list[str]) -> ParsedManuscript:
    """Assign chapter/scene numbers, roll up warnings, and materialize the public structure.

    Headerless chapters inherit the next sequential number continuing from the previous chapter, so a
    file with headers on 1/2/6/7 and none on 3/4/5 numbers cleanly as 1..7. An explicit number that
    doesn't advance (<= the previous) is surfaced as a warning rather than silently reordered.
    """
    out = ParsedManuscript(warnings=list(warnings))
    next_no = 1
    prev_no = 0
    headerless = 0
    for rc in raw:
        chapter_warnings: list[str] = []
        if rc.number is not None:
            chapter_no = rc.number
            if chapter_no <= prev_no:
                out.warnings.append(
                    f'Chapter {chapter_no} ("{rc.title or "untitled"}") is out of order (follows chapter {prev_no}).'
                )
            elif prev_no and chapter_no > prev_no + 1:
                # A jump in header numbers means the skipped chapters have no header of their own, so
                # their prose has merged into the previous chapter — the human adds breaks in preview.
                lo, hi = prev_no + 1, chapter_no - 1
                span = f"chapter {lo}" if lo == hi else f"chapters {lo}–{hi}"
                out.warnings.append(
                    f"Header numbers jump {prev_no} → {chapter_no}: {span} have no header — their "
                    f"prose likely merged into chapter {prev_no}. Add chapter breaks in the preview."
                )
        else:
            chapter_no = next_no
            headerless += 1
            chapter_warnings.append('No "Chapter" header found — number assigned by position.')
        scenes = [
            ParsedScene(scene_no=i, prose=p, word_count=len(p.split())) for i, p in enumerate(rc.scene_proses, start=1)
        ]
        if not scenes:
            chapter_warnings.append("Chapter has no scene prose — nothing to import here.")
        out.chapters.append(
            ParsedChapter(
                chapter_no=chapter_no,
                title=rc.title,
                detected=rc.detected,
                scenes=scenes,
                warnings=chapter_warnings,
            )
        )
        prev_no = max(prev_no, chapter_no)
        next_no = prev_no + 1

    if not out.chapters:
        out.warnings.append("No chapters or scenes could be detected in the dropped text.")
    elif not any(c.detected for c in out.chapters):
        out.warnings.append('No "Chapter" headers were found anywhere — treated the text as one chapter.')
    if headerless:
        out.warnings.append(f"{headerless} chapter(s) had no header and were auto-numbered.")
    return out


def split_manuscript(text: str) -> ParsedManuscript:
    """Split one manuscript text into a chapter -> scene preview structure (see module docstring)."""
    raw, warnings = _parse_raw(text)
    return _finalize(raw, warnings)


def split_files(files: list[tuple[str, str]]) -> ParsedManuscript:
    """Split several dropped files into ONE merged, ordered preview.

    ``files`` is [(filename, text), ...] in drop order. Chapters concatenate in that order and share a
    single numbering pass, so headerless chapters continue from the previous file's last chapter.
    """
    all_raw: list[_RawChapter] = []
    warnings: list[str] = []
    for filename, text in files:
        raw, warns = _parse_raw(text)
        if not raw:
            warnings.append(f"{filename}: no chapters or scenes detected.")
        all_raw.extend(raw)
        warnings.extend(f"{filename}: {w}" for w in warns)
    return _finalize(all_raw, warnings)
