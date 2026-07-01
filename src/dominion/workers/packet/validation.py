"""Deterministic ChapterPacket roster-consistency validation (contract-first drafting, Phase 1).

QA is an LLM attacker — good at semantic risk, unreliable at hard facts. This module catches decidable
roster contradictions a checker should never need to guess at: a character double-bucketed across the
four roster categories (`characters_present` / `characters_absent` / `characters_mentioned_only` /
`characters_forbidden`), or a forbidden name bleeding into the chapter's own scene seeds. It does NOT
try to judge whether a bucket assignment is factually correct for the story — that requires authorial
knowledge no packet field carries, and is out of reach for a deterministic checker.

Pure and import-light (no DB, no models): input is the packet body dict, output is a list of
violations the caller persists. `block` severity fails the packet closed; `warn` is advisory. Roster
entries are free text ("Brent (404 guild member, present in the scrim)"), so name extraction takes only
the text before the first parenthetical/comma/dash as the candidate identifier and compares it for
EXACT (case-insensitive) equality across buckets — never a fuzzy substring/NER match against another
bucket's full prose, which produces false positives (e.g. "Seb" present vs. "Seb's brother" mentioned-
only are different entities, but a naive whole-word scan of the *other bucket's raw text* collides on
"Seb" inside "Seb's"). Forbidden-name bleed into scene seeds reuses the same whole-word matcher already
proven safe for scene-packet absence checks, since scene-seed prose won't accidentally contain an exact
multi-word forbidden name/entity.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from dominion.shared.text_match import as_str_list, collect_strings, names_present

Severity = Literal["warn", "block"]

_ROSTER_FIELDS: tuple[str, ...] = (
    "characters_present",
    "characters_absent",
    "characters_mentioned_only",
    "characters_forbidden",
)

# Scene-seed fields that describe what actually happens on-page this chapter — a forbidden name/entity
# appearing here is a real, decidable violation (not a plausible background reference).
_SCENE_SEED_FIELDS: tuple[str, ...] = ("scene_job", "required_beats", "exit_state")

_LEADING_NAME_RE = re.compile(r"^[^(,;—-]+")


def _leading_name(entry: str) -> str:
    """The candidate identifier from a free-text roster entry: everything before the first
    parenthetical/comma/semicolon/em-dash/hyphen, trimmed. "Brent (404 guild member, ...)" -> "Brent"."""
    m = _LEADING_NAME_RE.match(entry.strip())
    return (m.group(0) if m else entry).strip()


@dataclass(frozen=True)
class ChapterPacketViolation:
    """One deterministic roster contradiction. `field` is the roster field(s) involved, so the editor
    can point the human straight at it. `block` fails the packet closed; `warn` is shown but does not
    block."""

    kind: str
    field: str | None
    detail: str
    severity: Severity

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "field": self.field, "detail": self.detail, "severity": self.severity}


def validate_chapter_packet_contract(body: dict[str, Any]) -> list[ChapterPacketViolation]:
    """Deterministic checks on an authored ChapterPacket body. Returns every violation found (block +
    warn); the caller blocks the packet when any is `block`. Decidable facts only — never semantic
    judgement about whether a roster assignment is actually right for the story (that is QA's job, or
    ultimately the human's)."""
    if not isinstance(body, dict):
        return [ChapterPacketViolation("invalid_body", None, "chapter packet body is not a JSON object", "block")]

    violations: list[ChapterPacketViolation] = []

    # 1. Roster double-bucketing: the same character identifier appearing in more than one of the four
    # roster fields is a direct self-contradiction — "this character is absent" and "this character is
    # present" can't both be true. Compare the extracted leading identifier for EXACT (case-insensitive)
    # equality across bucket pairs, never a substring/whole-word scan of another bucket's full prose.
    names_by_field: dict[str, list[str]] = {
        field_name: [_leading_name(entry) for entry in as_str_list(body.get(field_name)) if _leading_name(entry)]
        for field_name in _ROSTER_FIELDS
    }

    seen: dict[str, str] = {}  # normalized leading name -> field it was first seen in
    for field_name in _ROSTER_FIELDS:
        for name in names_by_field[field_name]:
            key = name.lower()
            prior_field = seen.get(key)
            if prior_field is not None and prior_field != field_name:
                violations.append(
                    ChapterPacketViolation(
                        kind="roster_double_bucketed",
                        field=f"{prior_field},{field_name}",
                        detail=(
                            f"{name!r} appears in both {prior_field!r} and {field_name!r} — a character "
                            "cannot be simultaneously in two roster categories; resolve which one is correct"
                        ),
                        severity="block",
                    )
                )
            else:
                seen[key] = field_name

    # 2. Forbidden-name bleed: a name/entity the chapter marks FORBIDDEN (must not be named or referenced
    # at all) appearing anywhere in the chapter's own scene seeds is a direct contradiction the packet
    # author introduced against itself.
    forbidden_names = names_by_field.get("characters_forbidden", [])
    if forbidden_names:
        for seed in body.get("scene_seeds") or []:
            if not isinstance(seed, dict):
                continue
            scene_no = seed.get("scene_no")
            for field_name in _SCENE_SEED_FIELDS:
                for name in names_present(collect_strings(seed.get(field_name)), forbidden_names):
                    violations.append(
                        ChapterPacketViolation(
                            kind="forbidden_name_in_scene_seed",
                            field=f"scene_seeds[scene_no={scene_no}].{field_name}",
                            detail=(
                                f"forbidden name/entity {name!r} appears in scene_seeds[scene_no={scene_no}]."
                                f"{field_name} despite being listed in characters_forbidden"
                            ),
                            severity="block",
                        )
                    )

    return violations
