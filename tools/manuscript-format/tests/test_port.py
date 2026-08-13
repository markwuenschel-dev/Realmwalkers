"""Parity tests for the standalone port of ``frontend/src/desk/lib/docx.ts``.

The DOCX assertions deliberately mirror the shape of the repo's own suites — structural labels and
named-style ids as in ``frontend/src/desk/manuscript/docxXml.test.ts``, panel text and hex colours
as in ``frontend/src/desk/lib/docxInterface.test.ts`` — so a regression here means the port has
drifted from the TypeScript, not merely that a byte moved.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from manuscript_format.beautify import beautify
from manuscript_format.ingest import (
    parse_semantic_markdown,
    read_docx,
    read_markdown,
    split_plain_markdown,
)
from manuscript_format.labels import (
    book_number_label,
    chapter_label,
    part_label,
    resolve_chapter_label,
    resolve_export_metadata,
    section_rank,
    to_roman,
)
from manuscript_format.presets import resolve_policy
from manuscript_format.prose import InterfaceSpec, parse_blocks, parse_inline, time_marker, word_count
from manuscript_format.render_markdown import docx_filename, markdown_filename, render_markdown
from manuscript_format.render_reader import build_doc_doc, parse_deltas, render_reader_doc
from manuscript_format.render_shunn import render_shunn_doc
from manuscript_format.spine import (
    Manuscript,
    ManuscriptChapter,
    ManuscriptPart,
    ManuscriptScene,
    ManuscriptVolume,
    build_spine,
    spine_counts,
)
from manuscript_format.surfaces import (
    AA_SMALL_TEXT,
    CREATURE_STYLES,
    DOMAIN_STYLES,
    ROLE_STYLES,
    contrast_ratio,
    format_interface_header,
    race_style,
    resolve_surface,
)

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


# ── prose.py ─────────────────────────────────────────────────────────────────


def test_parse_inline_flat_precedence():
    toks = parse_inline("a `code` **b** *c* [d](http://x) e")
    assert [(t.t, t.s) for t in toks] == [
        ("text", "a "),
        ("code", "code"),
        ("text", " "),
        ("strong", "b"),
        ("text", " "),
        ("em", "c"),
        ("text", " "),
        ("link", "d"),
        ("text", " e"),
    ]
    assert toks[7].href == "http://x"


def test_parse_inline_underscore_not_in_snake_case():
    """JS `\\w` is ASCII-only and emphasis needs a word boundary — snake_case must survive."""
    assert [(t.t, t.s) for t in parse_inline("call some_var_name now")] == [
        ("text", "call some_var_name now")
    ]
    assert [(t.t, t.s) for t in parse_inline("_yes_")] == [("em", "yes")]


def test_time_marker_forms_and_length_cap():
    assert time_marker("@day 3") == "Day 3"
    assert time_marker("@date March 3rd") == "March 3rd"
    assert time_marker("Day 47") == "Day 47"
    assert time_marker("Day 3 — Morning") == "Day 3 — Morning"
    assert time_marker("Monday — Dusk") == "Monday — Dusk"
    assert time_marker("the 4th of Emberfall") == "the 4th of Emberfall"
    assert time_marker("He waited until Monday, then left") is None
    assert time_marker("Day 47 " + "x" * 60) is None  # terse-only length cap


def test_parse_blocks_kinds():
    text = (
        "# Head\n\npara one\n\n- a\n- b\n\n1. x\n2. y\n\n> [!WARNING]\n> careful\n\n"
        "| A | B |\n|---|---|\n| 1 | 2 |\n\n---\n\n```\n@interface role=system\nhello\n```\n"
    )
    kinds = [b.kind for b in parse_blocks(text)]
    assert kinds == ["heading", "p", "ul", "ol", "callout", "table", "hr", "interface"]


def test_style_directive_binds_to_next_block_only():
    blocks = parse_blocks("@style domain=fire\n\n| A |\n|---|\n| 1 |\n\n| C |\n|---|\n| 2 |\n")
    assert blocks[0].spec is not None and blocks[0].spec.domain == "fire"
    assert blocks[1].spec is None


def test_interface_spec_rejects_out_of_enum_values():
    b = parse_blocks("```\n@interface role=bogus domain=fire skill=\"Ember Lash\"\nx\n```\n")[0]
    assert b.spec.role is None  # unknown role is dropped, not passed through
    assert b.spec.domain == "fire"
    assert b.spec.skill == "Ember Lash"


def test_hr_not_confused_with_one_column_table():
    assert [b.kind for b in parse_blocks("---\n")] == ["hr"]
    assert [b.kind for b in parse_blocks("- - -\n")] == ["hr"]


def test_word_count():
    assert word_count("  one two   three ") == 3
    assert word_count(None) == 0


# ── beautify.py ──────────────────────────────────────────────────────────────


def test_beautify_typesets_and_reflows():
    """Hard-wrapped lines re-flow into one paragraph; quotes curl, `--` becomes an em dash."""
    assert beautify("don't\n") == "don’t"
    out = beautify('He said "no" --\nand left...\n')
    assert out == "He said “no” — and left…"


def test_beautify_preserves_structural_runs_verbatim():
    table = "| A | B |\n|---|---|\n| 1 | 2 |"
    assert beautify(table) == table
    fence = "```\n@interface role=system\n\"quoted\" stays\n```"
    assert beautify(fence) == fence


def test_beautify_leaves_inline_code_alone():
    assert beautify('use `a--b` here -- ok') == "use `a--b` here — ok"


# ── labels.py ────────────────────────────────────────────────────────────────


def test_roman_and_labels():
    assert to_roman(1) == "I" and to_roman(4) == "IV" and to_roman(1994) == "MCMXCIV"
    assert to_roman(0) == "0"  # defensive fallback, never raises
    assert part_label(1, "The Gathering Storm") == "Part I — The Gathering Storm"
    assert part_label(2, None, "act") == "Act II"
    assert chapter_label("chapter", 3) == "Chapter Three"
    assert chapter_label("prologue", None) == "Prologue"
    assert chapter_label("bogus", 5) == "Chapter Five"  # unknown kind never leaks the raw enum


def test_section_label_priority_and_rank():
    assert resolve_chapter_label(kind="back_matter", section_type="glossary") == "Glossary"
    assert resolve_chapter_label(kind="front_matter", title="My Map", section_type="map") == "My Map"
    assert resolve_chapter_label(kind="front_matter", section_type="dramatis_personae") == "Dramatis Personae"
    assert section_rank("half_title") < section_rank("title_page") < section_rank("table_of_contents")


def test_book_number_label():
    assert book_number_label(1) == "BOOK ONE"
    assert book_number_label(13) == "BOOK 13"
    assert book_number_label(None) is None


# ── surfaces.py ──────────────────────────────────────────────────────────────


def test_every_surface_label_meets_wcag_aa():
    """The invariant interface-markup.md advertises: label text always clears 4.5:1 on its fill."""
    for role in ROLE_STYLES:
        for domain in [None, *DOMAIN_STYLES]:
            for creature in [None, *CREATURE_STYLES]:
                for intensity in ("subtle", "standard", "strong", "apex"):
                    s = resolve_surface(
                        InterfaceSpec(role=role, domain=domain, creature=creature, intensity=intensity)
                    )
                    assert contrast_ratio(s.label_color, s.fill) >= AA_SMALL_TEXT, (
                        role, domain, creature, intensity, s.label_color, s.fill,
                    )


def test_intensity_drives_spine_weight():
    weights = {
        i: resolve_surface(InterfaceSpec(domain="fire", intensity=i)).left_border_size
        for i in ("subtle", "standard", "strong", "apex")
    }
    assert weights == {"subtle": 8, "standard": 16, "strong": 24, "apex": 32}


def test_interface_header_forms():
    assert format_interface_header(InterfaceSpec(role="system")) == "[ SYSTEM ]"
    assert format_interface_header(InterfaceSpec(domain="fire")) == "[ INTERFACE ] FIRE"
    assert (
        format_interface_header(InterfaceSpec(role="xp", domain="fire"))
        == "[ XP ] PROGRESSION · FIRE"
    )
    assert (
        format_interface_header(InterfaceSpec(role="insight", creature="demon", domain="blood"))
        == "[ INSIGHT ] CREATURE SCAN · DEMON · BLOOD"
    )
    assert format_interface_header(InterfaceSpec(creature="nhal")) == "[ WARNING ] CREATURE SCAN · N'HAL"


def test_race_surfaces_include_author_locked_archdemon_and_archangel_palettes():
    archdemon = resolve_surface(InterfaceSpec(race="Archdemon"))
    archangel = resolve_surface(InterfaceSpec(race="Archangel"))

    assert (archdemon.accent, archdemon.fill, archdemon.header_fill) == (
        "B3132A",
        "FCEBED",
        "9B111E",
    )
    assert (archangel.accent, archangel.fill, archangel.header_fill) == (
        "C9A227",
        "FFFDF2",
        "F4E6A6",
    )


def test_unknown_race_gets_a_stable_non_neutral_palette():
    first = race_style("Veyrkin")
    second = race_style("Veyrkin")
    assert first == second
    assert first != ROLE_STYLES["system"]
    surface = resolve_surface(InterfaceSpec(race="Veyrkin"))
    assert contrast_ratio(surface.label_color, surface.fill) >= AA_SMALL_TEXT


def test_race_inference_and_health_change_styling_render_to_docx(tmp_path):
    content = """```
[ INTERFACE ]
INSIGHT Partial success.
NAME Xazzidiuk
RACE Archdemon
HEALTH 6,623 / 6,623
```

```
[ INTERFACE ]
HEALTH 37 / 50.
Bleeding detected.
```

```
[ INTERFACE ]
Restorative effect received.
HEALTH RESTORED 50 / 50.
Bleeding halted.
```

```
[ INTERFACE ]
INSIGHT Partial success.
NAME Zazriel
SPECIES Archangel
HEALTH 8,212 / 8,212
```
"""
    out = tmp_path / "race-health.docx"
    build_doc_doc("Race Health", content).save(out)
    xml = _xml(out)

    # Race/species lines colour the existing interface box without requiring source directives.
    assert "Xazzidiuk" in xml and "Zazriel" in xml
    assert 'w:fill="9B111E"' in xml and 'w:fill="FCEBED"' in xml
    assert 'w:fill="F4E6A6"' in xml and 'w:fill="FFFDF2"' in xml

    loss_pos = xml.index("HEALTH 37 / 50.")
    loss_run = xml[max(0, loss_pos - 500) : loss_pos]
    assert 'w:color w:val="B4231F"' in loss_run
    assert "<w:i" in loss_run

    gain_pos = xml.index("HEALTH RESTORED 50 / 50.")
    gain_run = xml[max(0, gain_pos - 500) : gain_pos]
    assert 'w:color w:val="1A9D3F"' in gain_run
    assert "<w:i" in gain_run


# ── level-up delta parsing ───────────────────────────────────────────────────


def test_parse_deltas_signs_gain_and_loss():
    body, deltas = parse_deltas(["prose line", "- Health: 72 -> 84", "- Corruption: 40 → 12"])
    assert body == ["prose line"]
    assert [(d.label, d.value, d.delta) for d in deltas] == [
        ("Health", "72", "→ 84"),
        ("Corruption", "40", "→ 12"),
    ]
    assert deltas[0].color == "1A9D3F"  # GAIN
    assert deltas[1].color == "B4231F"  # LOSS


# ── spine ────────────────────────────────────────────────────────────────────


def _fixture() -> Manuscript:
    return Manuscript(
        title="The Glass Aqueduct",
        series="Sample Saga",
        book_no=1,
        subtitle="A Reckoning",
        volumes=[ManuscriptVolume(id="v1", volume_no=1, title="The Long Winter")],
        parts=[
            ManuscriptPart(id="p1", part_no=1, title="Storm", volume_id="v1"),
            ManuscriptPart(id="p2", part_no=2, title="After", volume_id="v1", kind="act"),
        ],
        chapters=[
            ManuscriptChapter(position=0, kind="prologue", pov="Wren",
                              scenes=[ManuscriptScene(1, "Before the vault, there was the door.")]),
            ManuscriptChapter(position=1, chapter_no=2, title="The Channel", pov="Wren", part_id="p1",
                              epigraph="Every door remembers.",
                              scenes=[ManuscriptScene(
                                  1,
                                  "One.\n\nA second paragraph remains ordinary body text.\n\n"
                                  "```\n@interface role=levelup from=6 to=7\n"
                                  "Up.\n- Health: 72 -> 84\n- Corruption: 40 -> 12\n```\n",
                              ),
                                      ManuscriptScene(2, "Two.")]),
            ManuscriptChapter(position=2, chapter_no=4, pov="Wren", part_id="p2",
                              scenes=[ManuscriptScene(1, "Four.")]),
        ],
    )


def _spine():
    ms = _fixture()
    return build_spine(ms, resolve_export_metadata(ms.title, ms.series, ms.book_no, ms.subtitle,
                                                   author="Mark Wuenschel"))


def test_spine_groups_and_counts():
    spine = _spine()
    counts = spine_counts(spine)
    assert (counts.volumes, counts.parts, counts.chapters, counts.scenes) == (1, 2, 3, 4)
    assert counts.words > 0
    # The prologue is ungrouped and reads first; the volume is emitted at its first member.
    assert [type(n).__name__ for n in spine.nodes] == ["SpineChapterNode", "SpineVolumeNode"]


def test_spine_labels_a_prologue_as_prologue():
    """The bug the shared label contract exists to kill: a prologue must never read "Chapter N"."""
    from manuscript_format.spine import spine_chapters

    labels = [c.label for c in spine_chapters(_spine())]
    assert labels == ["Prologue", "Chapter Two", "Chapter Four"]


# ── emitters ─────────────────────────────────────────────────────────────────


def _xml(path: Path, part: str = "word/document.xml") -> str:
    with zipfile.ZipFile(path) as z:
        return z.read(part).decode("utf8")


def test_reader_docx_structure_and_panels(tmp_path):
    out = tmp_path / "r.docx"
    render_reader_doc(_spine(), resolve_policy("reader_proof"), "draft compile").save(out)
    xml = _xml(out)

    for label in ("VOLUME I", "PART I", "ACT II", "PROLOGUE", "CHAPTER TWO", "CHAPTER FOUR", "CONTENTS"):
        assert label in xml, label
    # The regression this whole label contract exists to kill.
    assert "CHAPTER ONE" not in xml
    assert "CHAPTER THREE" not in xml

    for style_ref in ('w:val="RWBookTitle"', 'w:val="RWChapterLabel"', 'w:val="RWBodyFirst"',
                      'w:val="RWBody"', 'w:val="RWEpigraph"', 'w:val="RWPovLine"', 'w:val="RWSceneBreak"'):
        assert style_ref in xml, style_ref

    assert "The Glass Aqueduct" in xml
    assert "Written By: Mark Wuenschel" in xml
    assert "draft compile" in xml
    assert "Level Up" in xml and "Vitals restored &amp; grown" in xml
    assert 'w:fill="1C1608"' in xml           # level-up gold band
    assert 'w:color w:val="1A9D3F"' in xml    # GAIN
    assert 'w:color w:val="B4231F"' in xml    # LOSS
    assert "⁂" in xml                         # scene-break glyph from the policy


def test_every_declared_style_is_a_real_paragraph_style(tmp_path):
    """Regression: Word ships built-in styles named "Book Title", "Title", "Quote"…

    python-docx's ``add_style`` raises on such a name collision. Falling back to the built-in binds
    a CHARACTER style where a paragraph style was meant, so ``w:pStyle w:val="BookTitle"`` resolves
    to nothing and the title page renders unstyled — visible only when the file is opened, never in
    a document.xml probe. Assert every declared id exists in styles.xml as type="paragraph".
    """
    from manuscript_format.styles import STYLE, reader_style_defs

    out = tmp_path / "styles.docx"
    render_reader_doc(_spine(), resolve_policy("reader_proof")).save(out)
    styles_xml = _xml(out, "word/styles.xml")

    declared = {d.style_id for d in reader_style_defs(resolve_policy("reader_proof"))}
    assert declared == {v for k, v in vars(STYLE).items() if not k.startswith("_")}
    for style_id in sorted(declared):
        needle = f'w:type="paragraph" w:styleId="{style_id}"'
        assert needle in styles_xml, f"{style_id} is not a paragraph style in styles.xml"

    # And the title page must actually reference the big centred title style.
    assert 'w:val="RWBookTitle"' in _xml(out)


def test_reader_honours_policy_overrides(tmp_path):
    from manuscript_format.presets import with_overrides

    policy = with_overrides(
        resolve_policy("reader_proof"),
        render_parts=False, include_half_title=False, include_table_of_contents=False,
        scene_break_glyph="§",
    )
    out = tmp_path / "r2.docx"
    render_reader_doc(_spine(), policy).save(out)
    xml = _xml(out)
    assert "PART I" not in xml and "VOLUME I" not in xml and "CONTENTS" not in xml
    assert "§" in xml


def test_shunn_docx_is_submission_safe(tmp_path):
    out = tmp_path / "s.docx"
    render_shunn_doc(_spine(), resolve_policy("submission_shunn")).save(out)
    xml = _xml(out)
    assert "PROLOGUE" in xml and "CHAPTER TWO" in xml and "CHAPTER ONE" not in xml
    assert "Courier New" in xml
    assert "Bahnschrift" not in xml       # no LitRPG label font
    assert "<w:tbl>" not in xml           # rich blocks flattened, never a table
    assert "by Mark Wuenschel" in xml
    header = _xml(out, "word/header1.xml")
    assert "Wuenschel / THE GLASS AQUEDUCT /" in header and "w:fldSimple" in header


def test_markdown_grammar():
    md = render_markdown(_spine(), resolve_policy("editorial_review"),
                         draft=True, exported_at="2026-07-27T00:00:00Z")
    assert "schema: dominion-manuscript/v1" in md
    assert 'title: "The Glass Aqueduct"' in md and "book: 1" in md
    assert "draft: true" in md            # JS lowercase boolean, not Python's True
    assert "<!-- chapter kind=prologue pov=\"Wren\" -->" in md
    assert '<!-- chapter number=2 title="The Channel" pov="Wren" -->' in md
    assert "<!-- scene index=2 scene_no=2 -->" in md
    assert "<!-- part number=1 kind=part -->" in md
    # Markdown carries RAW prose — beautify's curly quotes must NOT appear in the source text.
    assert "```\n@interface role=levelup from=6 to=7" in md


def test_filenames():
    assert docx_filename("The Glass Aqueduct!") == "The_Glass_Aqueduct.docx"
    assert markdown_filename("  ") == "manuscript.md"
    assert docx_filename("") == "document.docx"


# ── ingest ───────────────────────────────────────────────────────────────────


def test_semantic_markdown_round_trips_byte_identical():
    policy = resolve_policy("editorial_review")
    first = render_markdown(_spine(), policy, draft=False, exported_at="2026-07-27T00:00:00Z")
    reparsed = parse_semantic_markdown(first)
    assert reparsed is not None
    spine2 = build_spine(
        reparsed,
        resolve_export_metadata(reparsed.title, reparsed.series, reparsed.book_no, reparsed.subtitle),
    )
    second = render_markdown(spine2, policy, draft=False, exported_at="2026-07-27T00:00:00Z")
    assert first == second


def test_semantic_markdown_preserves_prose_verbatim():
    ms = _fixture()
    md = render_markdown(_spine(), resolve_policy("editorial_review"),
                         draft=False, exported_at="2026-07-27T00:00:00Z")
    back = parse_semantic_markdown(md)
    assert back is not None
    assert back.chapters[1].scenes[0].prose == ms.chapters[1].scenes[0].prose


def test_parse_semantic_markdown_returns_none_for_plain():
    assert parse_semantic_markdown("# Just a doc\n\nSome prose.\n") is None


def test_split_plain_markdown_infers_kinds():
    ms = split_plain_markdown(
        "# Prologue\n\nBefore.\n\n# Chapter 2 — The Channel\n\nOne.\n\n***\n\nTwo.\n\n# An Odd Name\n\nThree.\n",
        title="Inferred",
    )
    assert [(c.kind, c.chapter_no, c.title) for c in ms.chapters] == [
        ("prologue", None, None),
        ("chapter", 2, "The Channel"),
        ("chapter", 3, "An Odd Name"),
    ]
    assert len(ms.chapters[1].scenes) == 2  # *** is a scene break


def test_horizontal_rule_splits_scenes():
    """A `---` between prose blocks is a scene break, matching the repo's own importer
    (`_SCENE_BREAK_RE`, src/dominion/workers/memory/manuscript_split.py:34-37) and how the drafts
    in book1/manuscript/chapters/ are actually written."""
    ms = split_plain_markdown(
        "# Chapter Two\n\nOne.\n\n---\n\nTwo.\n\n***\n\nThree.\n", title="T"
    )
    assert len(ms.chapters) == 1
    assert [s.prose for s in ms.chapters[0].scenes] == ["One.", "Two.", "Three."]


def test_spelled_out_chapter_number_and_lifted_subtitle():
    """`# Chapter Two` / `## The Facility` — the level-2 line is the chapter's name, not prose."""
    ms = split_plain_markdown("# Chapter Two\n\n## The Facility\n\nProse.\n", title="T")
    ch = ms.chapters[0]
    assert (ch.kind, ch.chapter_no, ch.title) == ("chapter", 2, "The Facility")
    assert ch.scenes[0].prose == "Prose."  # the ## line is consumed, not rendered


def test_non_semantic_front_matter_is_metadata_not_prose():
    """Scene files under book1/manuscript/scenes/ carry title/chapter/scene/pov in YAML."""
    text = (
        "---\n"
        "scene_id: SCENE-002\n"
        "title: Lobby, Dead Hand Duel\n"
        "chapter: 1\n"
        "scene: 2\n"
        "pov: Marcus\n"
        'location: "a place — with punctuation"\n'
        "---\n\n# Chapter 1 — Scene 2\n\nThe duel began.\n"
    )
    src = read_markdown(text)
    assert src.structured is False
    assert src.title == "Lobby, Dead Hand Duel"
    assert src.front_matter["pov"] == "Marcus"
    assert "scene_id" not in src.raw_text  # stripped from the body entirely

    ms = split_plain_markdown(src.raw_text, title=src.title, front_matter=src.front_matter)
    assert len(ms.chapters) == 1  # not two, with the YAML block as a phantom first chapter
    ch = ms.chapters[0]
    assert (ch.chapter_no, ch.pov) == (1, "Marcus")
    assert ch.scenes[0].scene_no == 2
    assert ch.scenes[0].prose == "The duel began."


def test_split_plain_markdown_without_headings():
    ms = split_plain_markdown("Just prose.\n\nMore prose.\n", title="Flat")
    assert len(ms.chapters) == 1 and len(ms.chapters[0].scenes) == 1


def test_docx_round_trip_recovers_text(tmp_path):
    out = tmp_path / "d.docx"
    build_doc_doc("Canon", "# Head\n\npara\n\n| A | B |\n|---|---|\n| 1 | 2 |\n").save(out)
    src = read_docx(out)
    assert "Head" in src.raw_text and "para" in src.raw_text
    assert "| A | B |" in src.raw_text and "| 1 | 2 |" in src.raw_text
    assert src.structured is False  # panel semantics are NOT recoverable from a rendered DOCX


# ── end-to-end ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("target", ["reader", "shunn", "md"])
def test_cli_on_example_book(tmp_path, target):
    from manuscript_format.__main__ import main

    rc = main([str(EXAMPLES / "sample-book.md"), "--to", target, "-o", str(tmp_path),
               "--author", "Mark Wuenschel", "--exported-at", "2026-07-27T00:00:00Z", "-q"])
    assert rc == 0
    written = list(tmp_path.iterdir())
    assert len(written) == 1 and written[0].stat().st_size > 0


def test_cli_doc_mode_renders_character_sheet(tmp_path):
    from manuscript_format.__main__ import main

    assert main([str(EXAMPLES / "sample-doc.md"), "--to", "doc", "-o", str(tmp_path), "-q"]) == 0
    xml = _xml(next(tmp_path.glob("*.docx")))
    # The doc path skips beautify(), so quoted directive attributes survive intact.
    assert "Wren Calloway" in xml
    assert "SPELL POWER BONUSES" in xml and "RESISTANCES" in xml
    assert "■ " in xml                       # ~domain pip
    assert 'w:fill="EFEF39"' in xml          # sheet identity band
    assert 'w:color w:val="D23A17"' in xml   # fire pip accent


def test_cli_rejects_unknown_target(tmp_path, capsys):
    from manuscript_format.__main__ import main

    assert main([str(EXAMPLES / "sample-book.md"), "--to", "pdf", "-o", str(tmp_path)]) == 2
    assert "unknown target" in capsys.readouterr().err

# ── multi-file assembly ──────────────────────────────────────────────────────


def test_plain_front_and_back_matter_heading_inference():
    front = split_plain_markdown("# Dedication\n\nFor the readers.\n", title="T")
    back = split_plain_markdown("# Acknowledgements\n\nThank you.\n", title="T")
    assert (front.chapters[0].kind, front.chapters[0].section_type) == (
        "front_matter",
        "dedication",
    )
    assert (back.chapters[0].kind, back.chapters[0].section_type) == (
        "back_matter",
        "acknowledgments",
    )


def test_yaml_kind_and_section_type_are_preserved():
    text = (
        "---\nkind: back_matter\nsection_type: glossary\ntitle: Terms\n---\n\n"
        "Definitions.\n"
    )
    src = read_markdown(text)
    ms = split_plain_markdown(src.raw_text, title=src.title, front_matter=src.front_matter)
    ch = ms.chapters[0]
    assert (ch.kind, ch.section_type, ch.title, ch.chapter_no) == (
        "back_matter",
        "glossary",
        "Terms",
        None,
    )


def test_merge_sources_orders_front_body_back_and_natural_filenames(tmp_path):
    from manuscript_format.merge import expand_inputs, merge_sources

    (tmp_path / "99_acknowledgments.md").write_text("# Acknowledgments\n\nBack.\n")
    (tmp_path / "10_chapter.md").write_text("# Chapter Ten\n\nTen.\n")
    (tmp_path / "2_chapter.md").write_text("# Chapter Two\n\nTwo.\n")
    (tmp_path / "00_dedication.md").write_text("# Dedication\n\nFront.\n")

    paths = expand_inputs([tmp_path])
    ms, _ = merge_sources(paths, title="Novel")
    assert [(c.kind, c.chapter_no, c.section_type) for c in ms.chapters] == [
        ("front_matter", None, "dedication"),
        ("chapter", 2, None),
        ("chapter", 10, None),
        ("back_matter", None, "acknowledgments"),
    ]


def test_cli_merges_multiple_sources_into_one_reader(tmp_path):
    from manuscript_format.__main__ import main

    src = tmp_path / "src"
    out = tmp_path / "out"
    src.mkdir()
    (src / "00_dedication.md").write_text("# Dedication\n\nFor someone.\n")
    (src / "01_chapter.md").write_text("# Chapter One\n\nChapter body.\n")
    (src / "99_afterword.md").write_text("# Afterword\n\nAfter.\n")

    rc = main([str(src), "--to", "reader", "--title", "Merged Novel", "-o", str(out), "-q"])
    assert rc == 0
    files = list(out.glob("*.reader.docx"))
    assert len(files) == 1
    xml = _xml(files[0])
    assert "Merged Novel" in xml
    assert xml.index("DEDICATION") < xml.index("CHAPTER ONE") < xml.index("AFTERWORD")


def test_reader_docx_recovery_preserves_known_status_and_readout_panels(tmp_path):
    """Formatted chapter DOCX files can be merged without flattening the known Realmwalkers UI."""
    raw = """# Chapter One
## Test Chapter

WORKING DRAFT — PROVISIONAL

## June 27th, 2040
## Charlotte, North Carolina

Opening.

```stat
CURRENT STATUS
Name: Marcus
Race: Human
Level: 1
Class: Unassigned

Health: 50 / 50
Mana: 50 / 50
Stamina: 50 / 50

Skills: None
```

```
[ INTERFACE ]
Insight acquired.
Cost: 5 Mana.
Focus on a being to discern available information.
```
"""
    manuscript = split_plain_markdown(raw, title="Test Chapter")
    metadata = resolve_export_metadata("Test Chapter", None, None, None)
    chapter_doc = tmp_path / "chapter.docx"
    render_reader_doc(build_spine(manuscript, metadata), resolve_policy("reader_proof")).save(chapter_doc)

    recovered = read_docx(chapter_doc).raw_text
    assert "```stat" in recovered
    assert "Name: Marcus" in recovered
    assert "[ INTERFACE ]" in recovered
    assert "Cost: 5 Mana." in recovered
    assert "# CHAPTER ONE" in recovered
    assert "## Test Chapter" in recovered

    recovered_ms = split_plain_markdown(recovered, title="Recovered")
    out = tmp_path / "recovered.docx"
    render_reader_doc(
        build_spine(recovered_ms, resolve_export_metadata("Recovered", None, None, None)),
        resolve_policy("reader_proof"),
    ).save(out)
    xml = _xml(out)
    assert 'w:fill="EFEF39"' in xml     # status identity band
    assert 'w:fill="EEF2F6"' in xml     # pale readout body
    with zipfile.ZipFile(out) as z:
        header_xml = "".join(
            z.read(name).decode("utf8")
            for name in z.namelist()
            if name.startswith("word/header") and name.endswith(".xml")
        )
    assert 'w:color w:val="B4231F"' in header_xml  # working-draft running header


def test_reader_title_style_is_unique_and_chapters_page_break(tmp_path):
    ms = Manuscript(
        title="A Book",
        chapters=[
            ManuscriptChapter(chapter_no=1, scenes=[ManuscriptScene(1, "One.")]),
            ManuscriptChapter(chapter_no=2, scenes=[ManuscriptScene(1, "Two.")]),
        ],
    )
    out = tmp_path / "book.docx"
    render_reader_doc(
        build_spine(ms, resolve_export_metadata("A Book", None, None, None)),
        resolve_policy("reader_proof"),
    ).save(out)
    xml = _xml(out)
    styles = _xml(out, "word/styles.xml")
    assert 'w:val="RWBookTitle"' in xml
    assert 'w:styleId="RWBookTitle"' in styles
    assert 'w:name w:val="RW Book Title"' in styles
    assert xml.count('w:type="page"') >= 3
    assert "CHAPTER ONE" in xml and "CHAPTER TWO" in xml
