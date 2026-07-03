"""Deterministic ChapterPacket INTERNAL validation (roster + structural contradictions).

Per the scope-aware contract architecture:

- This module (and `evaluate_chapter_packet`) validates AuthorPacketInternal / raw ChapterPacket.
- Raw scene seeds and internal planning fields MAY contain hidden canonical terms.
- Surface leakage / forbidden surface terms are detected ONLY after SurfaceContractBuilder projection.
- No validator here scans raw scene seeds for characters_forbidden (that was the old design error).
- Roster matrix rules remain: present∩absent etc are REPAIR tasks (fixable data-entry contradictions
  — they block final export, never drafting); redundant overlaps are normalized (warn only). Only a
  structurally unusable body (not a JSON object) hard-blocks.

Field scope lives in scopes.py. A term may be forbidden from the reader without being forbidden from
the system.

See surface_contract.py for the projection stage that produces the drafter-safe contract.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from dominion.shared.severity import Severity, issue_gates
from dominion.shared.text_match import (
    DRAFTER_TEXT_FIELDS,
    as_str_list,
    binding_replacements,
    project_drafter_fields,
)

_ROSTER_FIELDS: tuple[str, ...] = (
    "characters_present",
    "characters_absent",
    "characters_mentioned_only",
    "characters_forbidden",
)

# Roster buckets sit on two independent axes: physical presence (present vs absent) and on-page surface
# reference (mentioned_only = referenced on-page; forbidden = must NOT be referenced on-page). Only pairs
# that cannot BOTH be resolved to a single coherent state contradict — and a redundant overlap that a
# server-side dominance rule can safely collapse is NOT one of them (see `normalize_chapter_packet_roster`).
#
# REPAIR (impossible as data — no dominance rule can pick a winner without discarding a hard claim, but
# it is a fixable data-entry contradiction, not a canon contradiction: route it back to the packet
# author as a repair task; drafting stays reachable, final export waits on the fix):
#   present ∩ absent      — physically here and physically not here
#   present ∩ forbidden    — on-page yet must never be named/referenced on-page
#   mentioned_only ∩ forbidden — referenced on-page yet must never be referenced on-page
# NORMALIZE (redundant — a dominance rule collapses it, never flags):
#   absent ∩ mentioned_only  — mentioned_only implies absence, so mentioned_only wins (drop from absent)
#   present ∩ mentioned_only — a physically present character is not "merely mentioned", so present wins
#                              (drop from mentioned_only); this is the common masked/late-reveal mis-bucket
# ALLOWED (both coherent, no action): absent ∩ forbidden — "off-page and must not be named".
#
# Per the new architecture:
#   Raw `scene_seeds.*` live in INTERNAL_PLANNING (see RAW_SCENE_SEED_FIELD_SCOPES).
#   Forbidden surface leakage is checked on the *projected* SurfaceContract (DRAFTER_SURFACE), not here.
#   A canonical term listed in characters_forbidden may legitimately exist in raw internal planning fields.
_CONTRADICTORY_ROSTER_PAIRS: frozenset[frozenset[str]] = frozenset(
    {
        frozenset({"characters_present", "characters_absent"}),
        frozenset({"characters_present", "characters_forbidden"}),
        frozenset({"characters_mentioned_only", "characters_forbidden"}),
    }
)

_LEADING_NAME_RE = re.compile(r"^[^(,;—-]+")


def leading_roster_name(entry: str) -> str:
    """The candidate identifier from a free-text roster entry: everything before the first
    parenthetical/comma/semicolon/em-dash/hyphen, trimmed. "Brent (404 guild member, ...)" -> "Brent"."""
    m = _LEADING_NAME_RE.match(entry.strip())
    return (m.group(0) if m else entry).strip()


@dataclass(frozen=True)
class ChapterPacketViolation:
    """One deterministic roster contradiction. `field` is the roster field(s) involved, so the editor
    can point the human straight at it. `block` fails the packet closed; `repair` is a machine-readable
    fix-it task (blocks final export only); `warn` is shown but blocks nothing."""

    kind: str
    field: str | None
    detail: str
    severity: Severity

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "field": self.field,
            "detail": self.detail,
            "severity": self.severity,
            **issue_gates(self.severity),
        }


def validate_chapter_packet_contract(body: dict[str, Any]) -> list[ChapterPacketViolation]:
    """Deterministic checks on an authored ChapterPacket body. Returns every violation found (block +
    repair + warn); the caller blocks the packet only when one is `block`. Decidable facts only — never
    semantic judgement about whether a roster assignment is actually right for the story (that is QA's
    job, or ultimately the human's)."""
    if not isinstance(body, dict):
        return [ChapterPacketViolation("invalid_body", None, "chapter packet body is not a JSON object", "block")]

    violations: list[ChapterPacketViolation] = []

    # 1. Roster double-bucketing: the same character identifier appearing in two roster fields is flagged
    # ONLY when the pair is a true opposite (see `_CONTRADICTORY_ROSTER_PAIRS`). "present + absent" can't
    # both be true; "mentioned_only + forbidden" can't both be true. It is a REPAIR task, not a hard
    # block: a name in two buckets is a fixable data-entry contradiction the packet author can resolve,
    # not a canon contradiction — drafting stays reachable, final export waits on the fix. "absent +
    # mentioned_only" and "absent + forbidden" are compatible (both surface states presuppose absence)
    # and never flag — that overlap is normalized upstream by `normalize_chapter_packet_roster`. Compare
    # the extracted leading identifier for EXACT (case-insensitive) equality across bucket pairs, never a
    # substring/whole-word scan of another bucket's full prose.
    names_by_field: dict[str, list[str]] = {
        field_name: [
            leading_roster_name(entry) for entry in as_str_list(body.get(field_name)) if leading_roster_name(entry)
        ]
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
                        severity="repair",
                    )
                )

    # NOTE: Forbidden surface bleed is no longer checked here.
    # Raw ChapterPacket.scene_seeds (and other INTERNAL_PLANNING fields) may contain canonical terms
    # that are listed in characters_forbidden. SurfaceContractBuilder + validate_surface_contract
    # are responsible for ensuring DRAFTER_SURFACE fields are clean (or blocked when unprojectable).
    #
    # Old "forbidden_name_in_scene_seed" checks on raw seeds have been removed from internal validation.

    return violations


def _leading_name_set(entries: list[str]) -> set[str]:
    """Lower-cased leading identifiers of a roster bucket (empties dropped)."""
    return {name for entry in entries if (name := leading_roster_name(entry).lower())}


def _partition_by_names(entries: list[str], drop_names: set[str]) -> tuple[list[str], list[str]]:
    """Split a bucket's entries into (kept, removed-display-names) by whole leading-name membership."""
    kept: list[str] = []
    removed: list[str] = []
    for entry in entries:
        if leading_roster_name(entry).lower() in drop_names:
            removed.append(leading_roster_name(entry) or entry)
        else:
            kept.append(entry)
    return kept, removed


def _roster_normalized_warning(field: str, removed: list[str], reason: str) -> ChapterPacketViolation:
    seen: list[str] = []
    for name in removed:
        if name not in seen:
            seen.append(name)
    detail = f"{len(removed)} name(s) {reason} and were removed from {field}: {', '.join(seen)}"
    return ChapterPacketViolation(kind="roster_normalized", field=field, detail=detail, severity="warn")


def normalize_chapter_packet_roster(
    body: dict[str, Any],
) -> tuple[dict[str, Any], list[ChapterPacketViolation]]:
    """Collapse the REDUNDANT (non-contradictory) roster overlaps with deterministic dominance rules so a
    checker never blocks on them and the persisted roster is coherent. Two rules, both surfaced as
    advisory `roster_normalized` warnings, never blockers:

    1. present dominates mentioned_only — a physically-present character redundantly echoed in
       `characters_mentioned_only` (the common masked / late-reveal mis-bucket: "she's present but her
       identity is hidden, so I'll also list her as mentioned") is kept in `characters_present` and dropped
       from `characters_mentioned_only`. Reveal timing belongs in reader/POV-knowledge + forbidden fields,
       not a second roster bucket.
    2. mentioned_only implies absence — a name in both `characters_absent` and `characters_mentioned_only`
       is redundant; drop it from `characters_absent` (keeping the more specific surface-reference bucket)
       so the downstream scene-packet absence check does not false-block a legitimate on-page *mention*.

    This never resolves a TRUE contradiction: `present ∩ absent` (and the forbidden pairs) are left intact
    for `validate_chapter_packet_contract` to block. `evaluate_chapter_packet` therefore validates the
    ORIGINAL body, so a name triple-bucketed as present+absent+mentioned_only still blocks on present∩absent
    instead of being silently normalized away. Matching is by the same EXACT (case-insensitive) leading
    identifier the validator uses, so "Seb" (absent) is never confused with "Seb's brother" (mentioned_only)."""
    if not isinstance(body, dict):
        return body, []

    present_names = _leading_name_set(as_str_list(body.get("characters_present")))
    mentioned_names = _leading_name_set(as_str_list(body.get("characters_mentioned_only")))

    normalized = dict(body)
    warnings: list[ChapterPacketViolation] = []

    if present_names and mentioned_names:
        kept, removed = _partition_by_names(as_str_list(body.get("characters_mentioned_only")), present_names)
        if removed:
            normalized["characters_mentioned_only"] = kept
            warnings.append(
                _roster_normalized_warning(
                    "characters_mentioned_only",
                    removed,
                    "were listed in both characters_present and characters_mentioned_only "
                    "(a physically present character is not merely mentioned; present wins)",
                )
            )

    if mentioned_names and as_str_list(body.get("characters_absent")):
        kept, removed = _partition_by_names(as_str_list(body.get("characters_absent")), mentioned_names)
        if removed:
            normalized["characters_absent"] = kept
            warnings.append(
                _roster_normalized_warning(
                    "characters_absent",
                    removed,
                    "were listed in both characters_absent and characters_mentioned_only "
                    "(mentioned_only already implies physical absence)",
                )
            )

    return (normalized, warnings) if warnings else (body, [])


def normalize_forbidden_surface_labels(
    body: dict[str, Any],
) -> tuple[dict[str, Any], list[ChapterPacketViolation]]:
    """Project drafter-facing scene-seed scaffolding through the packet's `entity_bindings`, replacing each
    bound forbidden canonical name with its surface label so the persisted seeds (and everything derived
    from them — ScenePackets, Beat.beat_text, the drafter prompt) never carry the forbidden name into
    reader-facing instructions. Internal fields (claims, author notes, canon_locks) are untouched — the
    author brain may still know "Roth". Returns the normalized body plus at most one collapsed
    `forbidden_surface_normalized` warning. No bindings, or nothing to rewrite → body returned unchanged."""
    if not isinstance(body, dict):
        return body, []
    replacements = binding_replacements(body.get("entity_bindings"))
    seeds = body.get("scene_seeds")
    if not replacements or not isinstance(seeds, list):
        return body, []

    new_seeds: list[Any] = []
    changed_scenes: list[str] = []
    for seed in seeds:
        if isinstance(seed, dict):
            new_seed, changed = project_drafter_fields(seed, replacements, DRAFTER_TEXT_FIELDS)
            if changed:
                changed_scenes.append(str(seed.get("scene_no")))
            new_seeds.append(new_seed)
        else:
            new_seeds.append(seed)
    if not changed_scenes:
        return body, []

    labels = ", ".join(sorted({label for _term, label in replacements}))
    detail = (
        f"forbidden canonical name(s) in drafter-facing scene-seed fields were projected to surface "
        f"label(s) [{labels}] in scene(s) {', '.join(changed_scenes)} — the canon name stays in internal "
        "fields only, never in reader-facing scaffolding"
    )
    return {**body, "scene_seeds": new_seeds}, [
        ChapterPacketViolation(kind="forbidden_surface_normalized", field="scene_seeds", detail=detail, severity="warn")
    ]


@dataclass(frozen=True)
class ChapterPacketValidationResult:
    """The outcome of evaluating one authored ChapterPacket body: the server-normalized body the packet
    should persist/QA/draft from, plus every violation found. `draftable` is true when nothing hard-blocks —
    repair tasks and warnings do not affect it (repairs gate final export via `export_blockers`, never
    drafting). Mirrors `ScenePacketValidationResult` so the two packet layers read the same way."""

    normalized_body: dict[str, Any]
    violations: list[ChapterPacketViolation]

    @property
    def draft_blockers(self) -> list[ChapterPacketViolation]:
        return [v for v in self.violations if v.severity == "block"]

    @property
    def repair_tasks(self) -> list[ChapterPacketViolation]:
        return [v for v in self.violations if v.severity == "repair"]

    @property
    def export_blockers(self) -> list[ChapterPacketViolation]:
        return [v for v in self.violations if v.severity in ("block", "repair")]

    @property
    def warnings(self) -> list[ChapterPacketViolation]:
        return [v for v in self.violations if v.severity == "warn"]

    @property
    def draftable(self) -> bool:
        return not self.draft_blockers


def evaluate_chapter_packet_internal(body: dict[str, Any]) -> ChapterPacketValidationResult:
    """Internal-only validation for raw AuthorPacketInternal / ChapterPacket body.

    Performs:
      - structural sanity (JSON object) — the only hard block at this layer
      - roster true-contradiction detection (present∩absent etc) — repair tasks
      - roster redundant normalization (warns only)

    IMPORTANT:
    - Does NOT scan raw scene seeds or internal fields for forbidden surface leakage.
    - Surface leakage (DRAFTER_SURFACE etc) is the job of build_surface_contract + validate_surface_contract.
    - Raw packet may contain hidden canonical truth in INTERNAL_PLANNING / AUTHOR_ONLY_CANON scopes.
    """
    if not isinstance(body, dict):
        return ChapterPacketValidationResult(
            normalized_body={},
            violations=[
                ChapterPacketViolation("invalid_body", None, "chapter packet body is not a JSON object", "block")
            ],
        )
    normalized, roster_warnings = normalize_chapter_packet_roster(body)
    # NOTE: We intentionally no longer call the old normalize_forbidden_surface_labels here for the
    # internal path. The generic SurfaceContractBuilder now owns projection for all surface_terms +
    # characters_forbidden. Legacy normalize is kept only for back-compat in beats/derive paths
    # that have not yet been fully migrated.
    return ChapterPacketValidationResult(
        normalized_body=normalized,
        violations=[*roster_warnings, *validate_chapter_packet_contract(body)],
    )


# Backwards-compatible alias. Current callers in packet/__init__.py will be updated to the two-stage
# internal + surface flow. Tests may continue to call evaluate_chapter_packet for the internal gate.
def evaluate_chapter_packet(body: dict[str, Any]) -> ChapterPacketValidationResult:
    """Legacy name for evaluate_chapter_packet_internal (roster + structure only).

    New code should prefer the explicit internal + surface pipeline.
    """
    return evaluate_chapter_packet_internal(body)
