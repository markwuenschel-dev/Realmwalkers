"""Unit tests for the pure stat-window renderer (no I/O, no DB, no model)."""
from __future__ import annotations

from dominion.workers.stat_render import render_stat_blocks

_BOX_EDGES = "┌┐└┘├┤│─"


def _box_lines(rendered: str) -> list[str]:
    return [ln for ln in rendered.splitlines() if ln and ln[0] in "┌│├└"]


def _is_rectangle(rendered: str) -> bool:
    """Every box line is the same width — the property the ragged-border bug violated."""
    widths = {len(ln) for ln in _box_lines(rendered)}
    return len(widths) == 1


def test_unequal_widths_align_into_a_clean_rectangle():
    out = render_stat_blocks("```stat\nPerception: 15\nReflexes: 11\nResolve: 9\n```")
    assert _is_rectangle(out)
    assert out == (
        "┌────────────────┐\n"
        "│ Perception  15 │\n"
        "│ Reflexes    11 │\n"
        "│ Resolve     9  │\n"
        "└────────────────┘"
    )


def test_values_longer_than_labels_still_align():
    out = render_stat_blocks("```stat\nA: longvalue123\nBB: x\n```")
    assert _is_rectangle(out)
    rows = [ln for ln in _box_lines(out) if "longvalue123" in ln or " x " in ln]
    # both values begin at the same column (the value column starts after label_w + 2-space gap)
    starts = {ln.index("longvalue123") if "longvalue123" in ln else ln.rindex("x") for ln in rows}
    assert len(starts) == 1


def test_labels_longer_than_values_still_align():
    out = render_stat_blocks("```stat\nPerception: 1\nResolve: 9\n```")
    assert _is_rectangle(out)
    assert "│ Perception  1 │" in out
    assert "│ Resolve     9 │" in out


def test_header_line_is_centered_with_a_rule_below():
    out = render_stat_blocks("```stat\nLEVEL UP\nPerception: 15\nReflexes: 11\n```")
    assert _is_rectangle(out)
    assert out == (
        "┌────────────────┐\n"
        "│    LEVEL UP    │\n"
        "├────────────────┤\n"
        "│ Perception  15 │\n"
        "│ Reflexes    11 │\n"
        "└────────────────┘"
    )


def test_explicit_dash_divider_becomes_a_rule():
    out = render_stat_blocks("```stat\nHP: 10\n---\nMP: 5\n```")
    assert _is_rectangle(out)
    lines = _box_lines(out)
    assert lines[0].startswith("┌") and lines[-1].startswith("└")
    assert any(ln.startswith("├") for ln in lines)  # the --- produced an interior rule


def test_multiple_blocks_in_one_text_each_render():
    src = "Intro.\n\n```stat\nHP: 10\n```\n\nMiddle.\n\n```stat\nMP: 5\n```\n\nEnd."
    out = render_stat_blocks(src)
    assert out.count("┌") == 2 and out.count("└") == 2
    assert "Intro." in out and "Middle." in out and "End." in out
    assert "```stat" not in out


def test_prose_outside_blocks_is_unchanged():
    src = "Para one.\n\n```stat\nHP: 10\n```\n\nPara two."
    out = render_stat_blocks(src)
    assert out.startswith("Para one.\n\n")
    assert out.endswith("\n\nPara two.")
    assert "┌" in out and "```stat" not in out


def test_marker_is_case_insensitive_and_trimmed():
    assert "┌" in render_stat_blocks("```STAT\nHP: 10\n```")
    assert "┌" in render_stat_blocks("```  Stat  \nHP: 10\n```")


def test_non_stat_fenced_block_is_left_byte_for_byte():
    src = "```python\nx = 1\n```"
    assert render_stat_blocks(src) == src
    src2 = "```\nplain fence\n```"
    assert render_stat_blocks(src2) == src2


def test_malformed_or_empty_blocks_are_left_as_written_and_never_raise():
    for src in (
        "```stat\n```",            # empty
        "```stat\n\n   \n```",     # only blanks
        "```stat\n---\n```",       # only a divider, no header/row
        "```stat\nHP: 10",         # unterminated fence
    ):
        assert render_stat_blocks(src) == src


def test_text_with_no_blocks_is_returned_unchanged():
    src = "Just narrative prose.\nNo system windows here.\n"
    assert render_stat_blocks(src) == src


def test_box_uses_only_box_drawing_characters_on_borders():
    out = render_stat_blocks("```stat\nHP: 10\n```")
    for ln in _box_lines(out):
        assert ln[0] in _BOX_EDGES and ln[-1] in _BOX_EDGES
