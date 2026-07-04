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
from dominion.shared.text_match import as_str_list

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

# LLM-authored roster annotations the normalizer must interpret (the packet author is TOLD not to emit
# these, but a model that does must not corrupt downstream gating):
#   surface presence — "Roth (named form absent; surface form present)" in characters_absent: the
#     entity PARTICIPATES via a surface form, so this is not a roster absence at all.
#   hedged presence — "Dead Hand leader (may be present ...)": conditional presence is not absence;
#     it is at most a mention until a human resolves it.
_SURFACE_PRESENCE_NOTE_RE = re.compile(r"surface\s+form\s+present|named?\s+form\s+absent", re.IGNORECASE)
_HEDGED_PRESENCE_RE = re.compile(
    r"\b(?:may|might|could)\s+(?:be\s+present|appear|be\s+heard)\b|\bpossibly\s+(?:present|appears?)\b"
    r"|\bpresence\s+(?:unclear|unresolved|conditional|uncertain)\b",
    re.IGNORECASE,
)


def _surface_term_labels(body: dict[str, Any]) -> dict[str, str]:
    """canonical_term (lower-cased) -> surface_label from the packet's `surface_terms` policies.
    Read defensively straight off the body: normalization runs before the SurfaceContractBuilder, so
    malformed entries are simply skipped here (the builder warns about them itself)."""
    labels: dict[str, str] = {}
    entries = body.get("surface_terms")
    if isinstance(entries, list):
        for entry in entries:
            if isinstance(entry, dict):
                term = str(entry.get("canonical_term") or "").strip()
                if term:
                    labels[term.lower()] = str(entry.get("surface_label") or "").strip()
    return labels


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

    # 2. Conditional roster locks: a lock that hedges presence ("may be present") is an unresolvable
    # directive — every downstream consumer (production ROSTER_LOCK contract items, the drafter) needs a
    # decided state. Decidable textually, so it is flagged here as a REPAIR task: resolve it — if the
    # character appears at all (even brief comms/voice), move them to characters_present (or
    # characters_mentioned_only if only referenced); if they do not appear, delete the lock.
    for lock in as_str_list(body.get("roster_locks")):
        if _HEDGED_PRESENCE_RE.search(lock):
            violations.append(
                ChapterPacketViolation(
                    kind="roster_lock_conditional",
                    field="roster_locks",
                    detail=(
                        f"roster lock {lock!r} hedges presence ('may be present') — resolve it: if the "
                        "character appears (even as brief comms/voice) put them in characters_present or "
                        "characters_mentioned_only; if they do not appear, remove this lock"
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
    """Collapse the REDUNDANT (non-contradictory) roster overlaps — and the two known LLM mis-bucketing
    patterns (surface presence filed as absence; hedged conditional presence filed as absence) — with
    deterministic dominance rules so a checker never blocks on them and the persisted roster is coherent.
    All rules are surfaced as advisory `roster_normalized` warnings, never blockers:

    1. present dominates mentioned_only — a physically-present character redundantly echoed in
       `characters_mentioned_only` (the common masked / late-reveal mis-bucket: "she's present but her
       identity is hidden, so I'll also list her as mentioned") is kept in `characters_present` and dropped
       from `characters_mentioned_only`. Reveal timing belongs in reader/POV-knowledge + forbidden fields,
       not a second roster bucket.
    2. mentioned_only implies absence — a name in both `characters_absent` and `characters_mentioned_only`
       is redundant; drop it from `characters_absent` (keeping the more specific surface-reference bucket)
       so the downstream scene-packet absence check does not false-block a legitimate on-page *mention*.
    3. surface presence is presence — an absent entry whose name has a `surface_terms` policy (or a
       "surface form present" note) leaves `characters_absent`; its surface label is ensured in
       `characters_present`. A withheld NAME is never a roster absence.
    4. conditional presence is not absence — an absent entry hedged with "may be present" moves to
       `characters_mentioned_only` pending an explicit human ruling.

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

    # 3. Surface presence is presence — an absent entry whose name has a surface_terms policy (or that
    # carries a "surface form present"/"named form absent" annotation) describes an entity that
    # PARTICIPATES this chapter under a surface label. Roster presence is about entity participation,
    # not whether the true name is spoken, so the entry leaves characters_absent; the surface label is
    # ensured in characters_present (the canonical name never is — it stays withheld). Without this,
    # the scene-level absent-character checks raise false repair tasks and beats drop the entity.
    surface_labels = _surface_term_labels(body)
    absent_now = as_str_list(normalized.get("characters_absent"))
    if absent_now:
        kept_entries: list[str] = []
        surface_present: list[str] = []
        for entry in absent_now:
            name = leading_roster_name(entry)
            if name.lower() in surface_labels or _SURFACE_PRESENCE_NOTE_RE.search(entry):
                surface_present.append(name or entry)
                label = surface_labels.get(name.lower(), "")
                if label:
                    present_entries = as_str_list(normalized.get("characters_present"))
                    label_name = leading_roster_name(label).lower()
                    already = {leading_roster_name(e).lower() for e in present_entries}
                    if label_name not in already:
                        normalized["characters_present"] = [*present_entries, label]
            else:
                kept_entries.append(entry)
        if surface_present:
            normalized["characters_absent"] = kept_entries
            warnings.append(
                _roster_normalized_warning(
                    "characters_absent",
                    surface_present,
                    "participate this chapter via a surface form (surface_terms policy / 'surface form "
                    "present' note) — a withheld name is not a roster absence; the surface label carries "
                    "the presence",
                )
            )

    # 4. Conditional presence is not absence — an absent entry hedged with "may be present"/"possibly
    # appears" is an unresolved maybe, not a fact. It moves (verbatim, annotation preserved) to
    # characters_mentioned_only — the lightweight bucket — so downstream absence checks stop treating a
    # maybe as a hard absence. The human resolves it from there (present, or truly absent, unhedged).
    absent_now = as_str_list(normalized.get("characters_absent"))
    if absent_now:
        kept_entries = []
        hedged_moved: list[str] = []
        for entry in absent_now:
            if _HEDGED_PRESENCE_RE.search(entry):
                hedged_moved.append(leading_roster_name(entry) or entry)
                mentioned_entries = as_str_list(normalized.get("characters_mentioned_only"))
                already = {leading_roster_name(e).lower() for e in mentioned_entries}
                if leading_roster_name(entry).lower() not in already:
                    normalized["characters_mentioned_only"] = [*mentioned_entries, entry]
            else:
                kept_entries.append(entry)
        if hedged_moved:
            normalized["characters_absent"] = kept_entries
            warnings.append(
                _roster_normalized_warning(
                    "characters_absent",
                    hedged_moved,
                    "carry a conditional presence note ('may be present') — conditional presence is not "
                    "absence; moved to characters_mentioned_only pending an explicit ruling",
                )
            )

    return (normalized, warnings) if warnings else (body, [])


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
    # Surface projection (surface_terms + characters_forbidden) is owned entirely by the generic
    # SurfaceContractBuilder (surface_contract.py); no seed-label rewriting happens at this layer.
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
