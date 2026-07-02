"""Deterministic ChapterPacket roster-consistency validation (contract-first drafting, Phase 1).

QA is an LLM attacker — good at semantic risk, unreliable at hard facts. This module catches decidable
roster contradictions a checker should never need to guess at: a character double-bucketed across the
four roster categories (`characters_present` / `characters_absent` / `characters_mentioned_only` /
`characters_forbidden`) in a way that is a TRUE self-contradiction, or a forbidden name bleeding into the
chapter's own scene seeds. It does NOT try to judge whether a bucket assignment is factually correct for
the story — that requires authorial knowledge no packet field carries, and is out of reach for a
deterministic checker.

Not every co-membership is a contradiction. `characters_mentioned_only` means "off-page but referenced
on the page", so it IMPLIES physical absence — a name in both `characters_absent` and
`characters_mentioned_only` is redundant, not contradictory, and must never block (blocking it was the
reported bug). `evaluate_chapter_packet` therefore NORMALIZES that overlap (dropping the name from
`characters_absent`, keeping the more specific surface-reference bucket) before validating, so the
downstream scene-packet absence check — which reads `characters_absent` and blocks absent names in
on-page fields — does not false-block a legitimate on-page mention. Only the TRUE-opposite bucket pairs
in `_CONTRADICTORY_ROSTER_PAIRS` block.

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

# Roster buckets sit on two independent axes: physical presence (present vs absent) and on-page surface
# reference (mentioned_only = referenced on-page; forbidden = must NOT be referenced on-page). Only TRUE
# opposites contradict. `present` excludes every not-present state. `mentioned_only` and `forbidden`
# exclude each other (a name can't be both referenced and forbidden-from-reference on-page). But `absent`
# is pure physical absence and is COMPATIBLE with the surface-reference states that presuppose it —
# "absent AND mentioned only" is the normal way to say "off-page but referenced", and "absent AND
# forbidden" is "off-page and must not be named"; neither is a contradiction and neither must ever block.
# (The scene-packet layer already enforces that a mentioned_only/forbidden name doesn't leak into on-page
# or reader/POV fields — that hidden-truth layering is not this roster gate's job.)
_CONTRADICTORY_ROSTER_PAIRS: frozenset[frozenset[str]] = frozenset(
    {
        frozenset({"characters_present", "characters_absent"}),
        frozenset({"characters_present", "characters_mentioned_only"}),
        frozenset({"characters_present", "characters_forbidden"}),
        frozenset({"characters_mentioned_only", "characters_forbidden"}),
    }
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

    # 1. Roster double-bucketing: the same character identifier appearing in two roster fields blocks ONLY
    # when the pair is a true opposite (see `_CONTRADICTORY_ROSTER_PAIRS`). "present + absent" can't both
    # be true; "mentioned_only + forbidden" can't both be true. But "absent + mentioned_only" and
    # "absent + forbidden" are compatible (both surface states presuppose absence) and never block — that
    # overlap is normalized upstream by `normalize_chapter_packet_roster`, not flagged here. Compare the
    # extracted leading identifier for EXACT (case-insensitive) equality across bucket pairs, never a
    # substring/whole-word scan of another bucket's full prose.
    names_by_field: dict[str, list[str]] = {
        field_name: [_leading_name(entry) for entry in as_str_list(body.get(field_name)) if _leading_name(entry)]
        for field_name in _ROSTER_FIELDS
    }

    # Group the fields each identifier lands in (roster order, deduped), then flag only contradictory pairs.
    fields_by_name: dict[str, tuple[str, list[str]]] = {}  # lower name -> (first-seen display name, [fields])
    for field_name in _ROSTER_FIELDS:
        for name in names_by_field[field_name]:
            _display, fields = fields_by_name.setdefault(name.lower(), (name, []))
            if field_name not in fields:
                fields.append(field_name)
    for display, fields in fields_by_name.values():
        for i in range(len(fields)):
            for j in range(i + 1, len(fields)):
                if frozenset({fields[i], fields[j]}) not in _CONTRADICTORY_ROSTER_PAIRS:
                    continue
                violations.append(
                    ChapterPacketViolation(
                        kind="roster_double_bucketed",
                        field=f"{fields[i]},{fields[j]}",
                        detail=(
                            f"{display!r} appears in both {fields[i]!r} and {fields[j]!r} — these are "
                            "mutually exclusive roster states; resolve which one is correct"
                        ),
                        severity="block",
                    )
                )

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


def normalize_chapter_packet_roster(
    body: dict[str, Any],
) -> tuple[dict[str, Any], list[ChapterPacketViolation]]:
    """Resolve the redundant (non-contradictory) roster overlap before validation.

    `characters_mentioned_only` means "off-page but referenced on the page", so it IMPLIES physical
    absence. A name in BOTH `characters_absent` and `characters_mentioned_only` is therefore redundant,
    not a contradiction. We drop it from `characters_absent` (keeping the more specific surface-reference
    bucket) so that (a) the deterministic roster check never fires on it and (b) the downstream
    scene-packet absence check — which reads `characters_absent` and blocks absent names appearing in
    on-page fields — does not false-block a legitimate on-page *mention* of an absent-but-referenced
    character. Returns the normalized body plus at most ONE collapsed `roster_normalized` warning. Matching
    is by the same EXACT (case-insensitive) leading identifier the validator uses, so "Seb" (absent) is
    never confused with "Seb's brother" (mentioned_only)."""
    if not isinstance(body, dict):
        return body, []
    absent_entries = as_str_list(body.get("characters_absent"))
    mentioned = {
        _leading_name(entry).lower()
        for entry in as_str_list(body.get("characters_mentioned_only"))
        if _leading_name(entry)
    }
    if not absent_entries or not mentioned:
        return body, []

    kept: list[str] = []
    removed: list[str] = []
    for entry in absent_entries:
        if _leading_name(entry).lower() in mentioned:
            removed.append(_leading_name(entry) or entry)
        else:
            kept.append(entry)
    if not removed:
        return body, []

    normalized = {**body, "characters_absent": kept}
    seen: list[str] = []
    for name in removed:
        if name not in seen:
            seen.append(name)
    detail = (
        f"{len(removed)} name(s) were listed in both characters_absent and characters_mentioned_only "
        "(mentioned_only already implies physical absence) and were removed from characters_absent: "
        f"{', '.join(seen)}"
    )
    return normalized, [
        ChapterPacketViolation(kind="roster_normalized", field="characters_absent", detail=detail, severity="warn")
    ]


@dataclass(frozen=True)
class ChapterPacketValidationResult:
    """The outcome of evaluating one authored ChapterPacket body: the server-normalized body the packet
    should persist/QA/draft from, plus every violation found. `draftable` is true when nothing hard-blocks —
    warnings do not affect it. Mirrors `ScenePacketValidationResult` so the two packet layers read the
    same way."""

    normalized_body: dict[str, Any]
    violations: list[ChapterPacketViolation]

    @property
    def draft_blockers(self) -> list[ChapterPacketViolation]:
        return [v for v in self.violations if v.severity == "block"]

    @property
    def warnings(self) -> list[ChapterPacketViolation]:
        return [v for v in self.violations if v.severity == "warn"]

    @property
    def draftable(self) -> bool:
        return not self.draft_blockers


def evaluate_chapter_packet(body: dict[str, Any]) -> ChapterPacketValidationResult:
    """Normalize the redundant absent∩mentioned_only overlap, then run the deterministic roster checks on
    the normalized body, in one place. The caller persists `normalized_body` and blocks on
    `draft_blockers`. Only true-opposite double-bucketing and forbidden-name bleed end up as blockers; the
    normalization is surfaced as an advisory warning."""
    if not isinstance(body, dict):
        return ChapterPacketValidationResult(
            normalized_body={},
            violations=[
                ChapterPacketViolation("invalid_body", None, "chapter packet body is not a JSON object", "block")
            ],
        )
    normalized, warnings = normalize_chapter_packet_roster(body)
    return ChapterPacketValidationResult(
        normalized_body=normalized,
        violations=[*warnings, *validate_chapter_packet_contract(normalized)],
    )
