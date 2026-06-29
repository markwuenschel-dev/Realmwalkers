"""Load and scope authoritative dialogue rules from disk."""
from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

from dominion.shared.config import settings

_PROJECT_ROOT = Path(__file__).resolve().parents[4]  # …/workers/context/dialogue_rules.py -> repo root
_dialogue_rules_warned = False

_CHAR_BLOCK_RE = re.compile(
    r"^### (?P<header>[^\n]+)\n.*?(?=^### |^## |\Z)", re.MULTILINE | re.DOTALL
)
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


def load_dialogue_rules(present: Iterable[str]) -> str | None:
    """Read dialogue rules fresh for each draft and scope per-character profiles to the cast on page."""
    global _dialogue_rules_warned
    configured = Path(settings.dialogue_rules_path)
    candidates = [configured] if configured.is_absolute() else [
        _PROJECT_ROOT / configured,
        Path.cwd() / configured,
    ]
    for path in candidates:
        try:
            text = path.read_text(encoding="utf-8").strip()
        except (FileNotFoundError, NotADirectoryError):
            continue
        return _scope_dialogue_rules(text, present) or None
    if not _dialogue_rules_warned:
        print(f"[context] dialogue rules not found at {settings.dialogue_rules_path!r}; "
              "drafts will run without them", flush=True)
        _dialogue_rules_warned = True
    return None
