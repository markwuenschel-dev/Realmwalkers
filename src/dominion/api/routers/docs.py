"""Canon / planning / style docs — read-only access to the on-disk Markdown the author maintains.

These are the Domain-B documents (story bible, timelines, style guides) that live as Markdown under
`novel/`. The Desk's canon viewer lists them and renders one through the shared block/inline renderer
(`frontend/src/desk/components/ProseBlocks.tsx`). Strictly read-only and sandboxed: only files under
the allowed category roots, only `.md`, and no path traversal outside them. The manuscript drafts
under `novel/manuscript/` are Domain A (the reading view owns them) and are deliberately excluded.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException

from dominion.shared.schemas import DocMeta, DocOut

router = APIRouter(tags=["library"])

# …/src/dominion/api/routers/docs.py -> repo root
_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_DOCS_ROOT = (_PROJECT_ROOT / "novel").resolve()
_CATEGORIES = ("canon", "planning", "style")


def _title_of(path: Path, text: str) -> str:
    """The doc's first '# ' heading, or a humanised filename if it has no leading H1."""
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("# "):
            return s[2:].strip()
        if s:  # first non-blank line isn't an H1 -> there's no title heading
            break
    return path.stem.replace("_", " ").replace("-", " ").strip() or path.name


def _safe_doc(rel: str) -> Path:
    """Resolve a request path to a real .md file inside an allowed category, or 404 (blocks traversal)."""
    candidate = (_DOCS_ROOT / rel).resolve()
    if not candidate.is_relative_to(_DOCS_ROOT):
        raise HTTPException(status_code=404, detail="doc not found")
    parts = candidate.relative_to(_DOCS_ROOT).parts
    if not parts or parts[0] not in _CATEGORIES or candidate.suffix != ".md" or not candidate.is_file():
        raise HTTPException(status_code=404, detail="doc not found")
    return candidate


@router.get("/library", response_model=list[DocMeta])
async def list_docs() -> list[DocMeta]:
    """Every Domain-B markdown doc, grouped category-first then by path. Read-only; no DB."""
    out: list[DocMeta] = []
    for category in _CATEGORIES:
        base = _DOCS_ROOT / category
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.md")):
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            rel = path.relative_to(_DOCS_ROOT).as_posix()
            out.append(DocMeta(path=rel, title=_title_of(path, text), category=category))
    return out


@router.get("/library/{doc_path:path}", response_model=DocOut)
async def read_doc(doc_path: str) -> DocOut:
    """One doc's raw markdown + metadata. Sandboxed to the allowed category roots."""
    path = _safe_doc(doc_path)
    text = path.read_text(encoding="utf-8")
    rel = path.relative_to(_DOCS_ROOT)
    return DocOut(path=rel.as_posix(), title=_title_of(path, text), category=rel.parts[0], content=text)
