"""Tests for the canon-docs endpoints (filesystem-backed, no DB — run everywhere).

The router reads the real Markdown under series/ and book1/. These call the router functions directly
and assert the listing, round-trip, and the sandbox (no traversal, .md-only, allowed categories only).
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from dominion.api.routers import docs as docs_router


async def test_list_docs_returns_categorised_markdown() -> None:
    docs = await docs_router.list_docs()
    assert docs, "expected canon/planning/style docs on disk"
    for d in docs:
        assert d.path.endswith(".md")
        assert d.category in {"canon", "planning", "style"}
        assert d.path.split("/")[0] == d.category
        assert d.title  # never empty — heading or humanised filename


async def test_read_doc_roundtrips_a_real_file() -> None:
    docs = await docs_router.list_docs()
    target = next(d for d in docs if d.category == "canon")
    out = await docs_router.read_doc(target.path)
    assert out.path == target.path
    assert out.title == target.title
    assert out.content.strip()


async def test_read_doc_rejects_traversal() -> None:
    for bad in ("../../README.md", "../pyproject.toml", "canon/../../setup.py", "../series/../README.md"):
        with pytest.raises(HTTPException) as ei:
            await docs_router.read_doc(bad)
        assert ei.value.status_code == 404


async def test_read_doc_rejects_non_markdown_and_excluded_paths() -> None:
    # manuscript is Domain A (excluded), .txt isn't markdown, and a bare category is a directory
    for bad in (
        "manuscript/_DRAFT_ORIGINAL_prerewrite.md",
        "canon/canon_index.txt",
        "canon",
        "does/not/exist.md",
    ):
        with pytest.raises(HTTPException) as ei:
            await docs_router.read_doc(bad)
        assert ei.value.status_code == 404


async def test_title_falls_back_to_filename(tmp_path) -> None:
    f = tmp_path / "no_heading_here.md"
    f.write_text("Just a paragraph, no H1.\n", encoding="utf-8")
    assert docs_router._title_of(f, f.read_text()) == "no heading here"
