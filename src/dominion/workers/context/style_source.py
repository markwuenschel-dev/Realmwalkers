"""Resolve a style document from Postgres first, then from disk.

THE PROBLEM THIS EXISTS TO FIX. The style guides live under `series/`, which is gitignored by
deliberate policy — creative content does not go to GitHub or the deploy box. Deploy is a `git pull`.
So every loader that reads a style guide from disk works perfectly on the author's machine and is
**silently inert in production**: `load_forbidden_drift` returns None, the drafter runs without the
constraint, and nothing anywhere reports a problem. That is the worst shape a failure can take — it
looks identical to working.

Reading the database first closes it. Postgres is already where the canon RAG index lives, it is
already per-environment, and content can be pushed into it without a file ever landing on the box.

DISK REMAINS THE FALLBACK, and the order matters. On the author's machine the file is the thing he
edits; if the database copy is stale, he would rather draft against what he just wrote than against
last week's upload — but that is only true when he is running locally, where the disk copy exists at
all. In production there is no disk copy, so the database is the only answer and the fallback never
fires. One rule serves both: prefer the database, fall back to disk, warn when neither has it.

Deliberately NOT cached. A style guide is edited between drafts, and a process-lifetime cache would
mean the author fixes a drift pattern, redrafts, and gets the old constraint with no way to tell. The
read is one indexed primary-key lookup against a table with a handful of rows.
"""

from __future__ import annotations

from pathlib import Path

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dominion.shared.models import StyleDocument

log = structlog.get_logger()

_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_warned: set[str] = set()


def slug_for(path: str) -> str:
    """`series/style/forbidden_drift.md` -> `style/forbidden_drift`.

    The slug drops the `series/` root and the extension so the key survives a repository reorganisation
    that moves the tree without changing what the document IS.
    """
    p = Path(path)
    parts = [x for x in p.with_suffix("").parts if x not in ("series", ".", "")]
    return "/".join(parts)


def normalise_newlines(text: str) -> str:
    """Line endings to LF, whatever the content crossed to get here.

    Content reaches `style_documents` through a push that may have travelled a Windows shell, and every
    structured reader downstream anchors on "\n" — `forbidden_drift`'s block regex matches a closing
    backtick followed by a newline. One stray carriage return makes that match nothing, and it does not
    fail: the document loads, the scoper returns empty, the drafter runs unconstrained, and the logs are
    clean. Normalising on read means no caller has to know where the bytes came from.
    """
    return text.replace("\r\n", "\n").replace("\r", "\n")


def read_from_disk(path: str) -> str | None:
    """Read a style document from the working tree. None when absent — which is the normal state on
    the deploy box, not an error."""
    configured = Path(path)
    candidates = [configured] if configured.is_absolute() else [_PROJECT_ROOT / configured, Path.cwd() / configured]
    for candidate in candidates:
        try:
            return candidate.read_text(encoding="utf-8")
        except (FileNotFoundError, NotADirectoryError):
            continue
    return None


def required_style_document_paths() -> tuple[str, ...]:
    """The style documents a draft is not allowed to run without.

    Scoped deliberately to the two that reach the drafter's prompt — `forbidden_drift` via
    `assemble.py` and `dialogue_rules` via `load_dialogue_rules`. The other slugs in the table
    (`voice_guide`, `prose_contract`, …) have no consuming code, so requiring them would block
    drafting on documents whose absence changes nothing about the generated prose.
    """
    from dominion.shared.config import settings

    return (settings.forbidden_drift_path, settings.dialogue_rules_path)


async def missing_required_style_documents(session: AsyncSession) -> tuple[str, ...]:
    """Slugs of the required style documents present in NEITHER Postgres nor disk.

    This is the fail-closed half of the fix. Reading Postgres first stops the deploy box drafting
    against nothing; this stops it drafting *silently* against nothing, by turning the absence into a
    blocker the Desk renders instead of a warning nobody queries. Returns slugs (not paths) because
    the slug is what `push_style` writes and what the operator must go create.
    """
    wanted = {slug_for(p): p for p in required_style_document_paths()}
    rows = (await session.execute(select(StyleDocument).where(StyleDocument.slug.in_(wanted)))).scalars().all()
    present = {r.slug for r in rows if r.content and r.content.strip()}
    missing = [
        slug for slug, path in wanted.items() if slug not in present and not ((d := read_from_disk(path)) and d.strip())
    ]
    return tuple(sorted(missing))


async def load_style_document(session: AsyncSession, path: str) -> str | None:
    """The content of the style document at `path`: database first, disk second, None if neither.

    `path` is the configured *disk* path (e.g. `settings.forbidden_drift_path`); the database key is
    derived from it, so a caller only ever names the document one way.
    """
    slug = slug_for(path)
    row = (await session.execute(select(StyleDocument).where(StyleDocument.slug == slug))).scalar_one_or_none()
    if row is not None and row.content.strip():
        return normalise_newlines(row.content)

    on_disk = read_from_disk(path)
    if on_disk is not None and on_disk.strip():
        if row is None:
            # Local development: the file is present and nothing has been pushed. Normal, and worth
            # saying once, because the same code path in production would mean the drafter is running
            # unconstrained.
            log.debug("style.disk_fallback", slug=slug, path=path)
        return normalise_newlines(on_disk)

    if slug not in _warned:
        _warned.add(slug)
        log.warning(
            "style.document_missing",
            slug=slug,
            path=path,
            detail=(
                "not in style_documents and not on disk; the guidance it carries will not be applied. "
                "Push it with `python -m dominion.tools.push_style`."
            ),
        )
    return None
