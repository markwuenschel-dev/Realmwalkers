"""Unit tests for read-through anchoring (pure: no DB, no model). Synthetic prose only.

Non-ASCII characters are written as named escapes so a decomposed "e" + combining accent, a
precomposed "é" and a no-break space stay distinguishable when reading the source.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable

import pytest

from dominion.shared.schemas import ReadThroughAnchorOut
from dominion.workers.read_through.anchors import (
    AMBIGUOUS,
    LOCATED,
    MAX_CANDIDATES,
    UNLOCATED,
    Segment,
    locate,
    scene_spans,
    utf16_offset,
)
from dominion.workers.reviewers.base import quote_is_supported

ELLIPSIS = "\N{HORIZONTAL ELLIPSIS}"
COMBINING_ACUTE = "\N{COMBINING ACUTE ACCENT}"
E_ACUTE = "\N{LATIN SMALL LETTER E WITH ACUTE}"
LDQUO = "\N{LEFT DOUBLE QUOTATION MARK}"
RDQUO = "\N{RIGHT DOUBLE QUOTATION MARK}"
RSQUO = "\N{RIGHT SINGLE QUOTATION MARK}"
EM_DASH = "\N{EM DASH}"
EN_DASH = "\N{EN DASH}"
NBSP = "\N{NO-BREAK SPACE}"
FI_LIGATURE = "\N{LATIN SMALL LIGATURE FI}"
SHARP_S = "\N{LATIN SMALL LETTER SHARP S}"
GRINNING = "\U0001f600"
DRAGON = "\U0001f409"
SCRIPT_A = "\U0001d49c"
BELL = "\U0001f514"


def _js_slice(raw: str, start: int, end: int) -> str:
    """What the Desk does: `text.slice(start, end)` over UTF-16 code units."""
    return raw.encode("utf-16-le")[2 * start : 2 * end].decode("utf-16-le")


def _assert_slices_match(raw: str, segments: Iterable[Segment]) -> None:
    for segment in segments:
        assert _js_slice(raw, segment.start, segment.end) == segment.text


def test_repeated_passage_is_ambiguous_with_all_candidates():
    raw = (
        "The lantern swung twice above the gate.\n\n"
        "Mara counted the steps down to the water.\n\n"
        "The lantern swung twice above the gate.\n"
    )
    anchor = locate("the lantern swung twice above the gate", raw, chapter_id=None)

    assert anchor.state == AMBIGUOUS
    assert anchor.segments == ()  # never silently chooses one
    assert anchor.candidate_count == 2
    assert len(anchor.candidates) == 2
    assert [placement[0].start for placement in anchor.candidates] == [0, raw.rindex("The lantern")]
    assert all(placement[0].text == "The lantern swung twice above the gate" for placement in anchor.candidates)
    for placement in anchor.candidates:
        _assert_slices_match(raw, placement)


def test_utf16_offsets_with_astral_and_combining_chars():
    # Three astral code points (two emoji, a mathematical letter) and a DECOMPOSED é before the quote.
    decomposed_cafe = "cafe" + COMBINING_ACUTE
    raw = (
        f"Tavi grinned {GRINNING}{DRAGON} at the {decomposed_cafe} sign.\n\n"
        f"{SCRIPT_A} gull shrieked, and the {decomposed_cafe} stayed shut all morning.\n"
    )
    anchor = locate(f"the caf{E_ACUTE} stayed shut all morning", raw, chapter_id=None)  # PRECOMPOSED é

    assert anchor.state == LOCATED
    (segment,) = anchor.segments
    py_start = raw.index(f"the {decomposed_cafe} stayed")
    assert segment.text == f"the {decomposed_cafe} stayed shut all morning"
    assert segment.start == py_start + 3 == utf16_offset(raw, py_start)
    assert segment.end == segment.start + len(segment.text)
    _assert_slices_match(raw, anchor.segments)

    # Astral characters INSIDE the quoted segment widen it by one code unit each.
    inner = locate(f"grinned {GRINNING}{DRAGON} at the caf{E_ACUTE} sign", raw, chapter_id=None)
    assert inner.state == LOCATED
    (inner_segment,) = inner.segments
    assert inner_segment.text == f"grinned {GRINNING}{DRAGON} at the {decomposed_cafe} sign"
    assert inner_segment.end - inner_segment.start == len(inner_segment.text) + 2
    _assert_slices_match(raw, inner.segments)

    assert utf16_offset(f"a{GRINNING}b", 2) == 3
    assert utf16_offset(f"a{GRINNING}b", 3) == 4
    with pytest.raises(ValueError):
        utf16_offset("abc", 4)


def test_ellipsis_segments_ordered_within_one_scene():
    raw = (
        "Ren lifted the brass key from the table. The clock ticked on. "
        "Ren set the key into the lock and turned it slowly.\n"
    )

    for quote in (
        f"Ren lifted the brass key {ELLIPSIS} turned it slowly",
        "Ren lifted the brass key ... turned it slowly",
    ):
        anchor = locate(quote, raw, chapter_id=None)
        assert anchor.state == LOCATED
        assert [segment.text for segment in anchor.segments] == ["Ren lifted the brass key", "turned it slowly"]
        assert anchor.segments[0].end <= anchor.segments[1].start
        _assert_slices_match(raw, anchor.segments)

    # Segments must match IN ORDER.
    assert locate(f"turned it slowly {ELLIPSIS} Ren lifted the brass key", raw, chapter_id=None).state == UNLOCATED


@pytest.mark.parametrize("divider", ["* * *", "***", "---", "# Scene 2"])
def test_ellipsis_across_scene_break_unlocated(divider: str):
    raw = f"Ren lifted the brass key from the table.\n\n{divider}\n\nLater, Ren turned it in the lock.\n"
    quote = f"Ren lifted the brass key {ELLIPSIS} Ren turned it in the lock"

    assert locate(quote, raw, chapter_id=None).state == UNLOCATED
    # The plain in-order matcher accepts it: the one-scene rule is what refuses the placement.
    assert quote_is_supported(quote, raw) is True
    assert locate("Ren lifted the brass key", raw, chapter_id=None).state == LOCATED
    assert locate("Ren turned it in the lock", raw, chapter_id=None).state == LOCATED

    spans = scene_spans(raw)
    assert len(spans) == 2
    assert all(divider not in raw[start:end] for start, end in spans)


def test_markdown_emphasis_ignored_in_projection_raw_text_preserved():
    raw = "The wind said *nothing* at all, and __Kell__ waited by the _far_ door.\n"

    plain = locate("The wind said nothing at all", raw, chapter_id=None)
    assert plain.state == LOCATED
    assert plain.segments[0].text == "The wind said *nothing* at all"  # raw substring, markers kept

    verbatim = locate("The wind said *nothing* at all", raw, chapter_id=None)
    assert verbatim.segments == plain.segments

    kell = locate("Kell waited by the far door", raw, chapter_id=None)
    assert kell.state == LOCATED
    assert kell.segments[0].text == "Kell__ waited by the _far_ door"
    _assert_slices_match(raw, [*plain.segments, *kell.segments])


@pytest.mark.parametrize("quote", ["", "   ", ELLIPSIS, f"... {ELLIPSIS}", "**", " _ "])
def test_blank_quote_unlocated(quote: str):
    anchor = locate(quote, "Some prose that exists. *** More prose.", chapter_id="c1")
    assert anchor.state == UNLOCATED
    assert anchor.segments == ()
    assert anchor.candidates == ()
    assert anchor.candidate_count == 0
    assert anchor.text_quoted == quote
    assert anchor.chapter_id == "c1"


def test_curly_quotes_dashes_and_whitespace_fold():
    raw = f"{LDQUO}Don{RSQUO}t,{RDQUO} she said{EM_DASH}quietly,{NBSP}and   then\n  louder.\n"
    anchor = locate('"Don\'t," she said-quietly, and then louder.', raw, chapter_id=None)
    assert anchor.state == LOCATED
    assert anchor.segments[0].text == raw.rstrip("\n")
    _assert_slices_match(raw, anchor.segments)

    # And the other direction: the model's typography, the author's plain characters.
    plain = "SHE SAID-quietly-no."
    reverse = locate(f"she said{EN_DASH}quietly", plain, chapter_id=None)
    assert reverse.state == LOCATED
    assert reverse.segments[0].text == "SHE SAID-quietly"


def test_candidates_capped_but_count_true():
    raw = "Bell rang out over the square.\n" * 7
    anchor = locate("bell rang out over the square", raw, chapter_id=None)

    assert anchor.state == AMBIGUOUS
    assert len(anchor.candidates) == MAX_CANDIDATES
    assert anchor.candidate_count == 7
    step = len("Bell rang out over the square.\n")
    assert [placement[0].start for placement in anchor.candidates] == [i * step for i in range(MAX_CANDIDATES)]


def test_agrees_with_quote_is_supported_on_shared_cases():
    # No emphasis markers and no scene breaks: the two matchers must agree on "does it occur".
    prose = (
        f"Ilse wiped the counter twice before the ferry horn sounded. {LDQUO}Not tonight,{RDQUO} she told the "
        f"dog. The dog, as ever, disagreed. He paused{EM_DASH}then{NBSP}ran. Ilse wiped the counter twice "
        "before the ferry  horn\n sounded."
    )
    quotes = [
        "wiped the counter twice",  # ambiguous
        '"Not tonight," she told the dog',  # located
        "NOT TONIGHT, SHE TOLD",  # the closing quote mark is missing: absent
        f"she told the dog {ELLIPSIS} as ever, disagreed",
        f"as ever, disagreed {ELLIPSIS} she told the dog",  # out of order: absent
        "the cat, as ever, disagreed",
        "Ilse wiped ... horn sounded",
        "paused-then ran",
        "the ferry horn sounded",
    ]
    outcomes = []
    for quote in quotes:
        placed = locate(quote, prose, chapter_id=None).state != UNLOCATED
        assert placed == quote_is_supported(quote, prose), quote
        outcomes.append(placed)
    assert True in outcomes and False in outcomes


def test_ligature_and_sharp_s_map_to_whole_raw_characters():
    raw = f"The {FI_LIGATURE}nal stra{SHARP_S}e was quiet."
    anchor = locate("the final strasse was quiet.", raw, chapter_id=None)
    assert anchor.state == LOCATED
    assert anchor.segments[0].text == raw

    # A match starting or ending INSIDE an expanded character widens to the whole raw character.
    partial = locate("inal strass", raw, chapter_id=None)
    assert partial.state == LOCATED
    assert partial.segments[0].text == f"{FI_LIGATURE}nal stra{SHARP_S}"


def test_scene_spans_split_at_break_lines():
    raw = "One.\r\n***\r\nTwo.\n---\nThree."
    assert [raw[start:end].strip() for start, end in scene_spans(raw)] == ["One.", "Two.", "Three."]
    assert scene_spans("Plain prose.") == [(0, len("Plain prose."))]
    assert scene_spans("One.\n***") == [(0, 5)]
    assert scene_spans("***\n\n   \n---") == []


def test_to_json_round_trips_the_wire_schema():
    chapter_id = str(uuid.uuid4())
    raw = f"Bell rang. Bell rang. The {BELL} fell silent at last."
    for quote in ("bell rang", "fell silent at last", "never written"):
        payload = locate(quote, raw, chapter_id=chapter_id).to_json()
        assert ReadThroughAnchorOut.model_validate(payload).model_dump(mode="json") == payload
