r"""Load and scope the authoritative dialogue rules: Postgres first, disk second.

This module used to read `settings.dialogue_rules_path` off the filesystem and nothing else. That
works on the author's machine and is **silently inert in production**: `series/` is gitignored by
deliberate policy, deploy is a `git pull`, so the file simply is not on the box. The loader returned
None, `assemble_context` put None into `SceneContext.dialogue_rules`, and `drafter.py`'s
`if ctx.dialogue_rules:` skipped the block — every deployed draft ran with no dialogue rules at all,
announced by one unstructured `print()` per process and nothing else.

`style_source.load_style_document` already solved exactly this for `forbidden_drift`. Routing through
it means the rules resolve from `style_documents` in production, still prefer the working copy on the
author's disk, and normalise their line endings on the way out — which this module needs even more
than drift does, because `_CHAR_BLOCK_RE` anchors on "\n" and a CRLF-pushed document would scope every
character block away while looking perfectly healthy.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from sqlalchemy.ext.asyncio import AsyncSession

from dominion.shared.config import settings
from dominion.workers.context.style_source import load_style_document

_CHAR_BLOCK_RE = re.compile(r"^### (?P<header>[^\n]+)\n.*?(?=^### |^## |\Z)", re.MULTILINE | re.DOTALL)
_BLOCK_ALIASES: dict[str, set[str]] = {
    "illyri": {"illyri", "marcus"},
    "illyristranthe": {"illyri", "marcus"},
    "ayla": {"illyri", "marcus"},
}


def _header_names(header: str) -> set[str]:
    names = {n.strip().lower() for n in re.split(r"[(),/]| and ", header) if n.strip()}
    for name in list(names):
        names |= _BLOCK_ALIASES.get(name, set())
    return names


def _scope_dialogue_rules(text: str, present: Iterable[str]) -> str:
    present_l = {p.strip().lower() for p in present if p and p.strip()}
    if not present_l:
        return text

    def _keep(match: re.Match[str]) -> str:
        return match.group(0) if _header_names(match.group("header")) & present_l else ""

    scoped = _CHAR_BLOCK_RE.sub(_keep, text)
    return re.sub(r"\n{3,}", "\n\n", scoped).strip()


async def load_dialogue_rules(session: AsyncSession, present: Iterable[str]) -> str | None:
    """Read dialogue rules fresh for each draft and scope per-character profiles to the cast on page.

    Not cached, for the same reason `load_style_document` is not: the author edits the rules between
    drafts, and a process-lifetime cache would silently serve the old ruleset. `load_style_document`
    warns once per slug when neither Postgres nor disk has the document, so an absence is reported
    through the same structured `style.document_missing` event as every other style document rather
    than through a bare `print` this module owned alone.
    """
    text = await load_style_document(session, settings.dialogue_rules_path)
    if text is None:
        return None
    return _scope_dialogue_rules(text.strip(), present) or None
