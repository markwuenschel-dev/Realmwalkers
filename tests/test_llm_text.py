"""Unit tests for the shared LLM-response fence stripper. Pure function — NO DB."""

from __future__ import annotations

from dominion.shared.llm_text import strip_fences


def test_leading_lang_fence_and_trailing_fence() -> None:
    raw = '```json\n{"a": 1}\n```'
    assert strip_fences(raw) == '{"a": 1}'


def test_no_fence_is_returned_stripped() -> None:
    assert strip_fences('  {"a": 1}  ') == '{"a": 1}'


def test_fence_with_no_language_tag() -> None:
    raw = "```\nhello\n```"
    assert strip_fences(raw) == "hello"


def test_interior_backticks_not_at_edges_are_preserved() -> None:
    raw = "before ```not a fence``` after"
    assert strip_fences(raw) == "before ```not a fence``` after"


def test_leading_fence_without_closing_fence() -> None:
    # A leading fence with no trailing fence: the fence line is dropped, remainder kept.
    raw = "```json\n{\"a\": 1}"
    assert strip_fences(raw) == '{"a": 1}'


def test_empty_and_bare_fence() -> None:
    assert strip_fences("") == ""
    # A lone ``` with no newline collapses to empty.
    assert strip_fences("```") == ""
