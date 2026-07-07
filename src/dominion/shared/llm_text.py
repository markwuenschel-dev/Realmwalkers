"""Shared tolerant-parsing primitives for LLM responses.

Models routinely wrap their output in Markdown code fences (```lang ... ```), even when asked not to.
Every worker that reads a model response — planner, packet parser, reviewers — needs the same
fence-stripping primitive. Kept here once so those call sites can't drift on the semantics.
"""

from __future__ import annotations


def strip_fences(s: str) -> str:
    """Drop a leading ```lang fence and trailing ``` from a model response, if present."""
    s = s.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else ""
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
    return s.strip()
