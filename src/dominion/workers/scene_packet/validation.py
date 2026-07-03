"""Deterministic ScenePacket contract validation (scene-packet contract system).

QA is an LLM attacker — good at semantic risk, unreliable at hard facts. This module is the
deterministic gate that runs BEFORE QA and decides the facts a checker should never guess at.

WRITER-FIRST POLICY: the product is drafting, so this gate hard-BLOCKS only true blockers — a malformed
body, an unrecoverable word budget or scene number, a scene-number contradiction with the seed. Every
FIXABLE contract defect is a REPAIR task instead: a model-overridden word budget, an absent character
placed ON-PAGE (acting in a scene they're not in) or leaked into a READER/POV-KNOWLEDGE field (the
reader/POV already effectively "knows" someone declared absent from the roster) are real defects the
author agent (or human) can fix — they block final export, never drafting or human review. Optional
provenance hygiene is normalized or WARNED: an invalid `claim_sources.source_id` (an outline label, a
UUID, an out-of-range/fabricated handle) is rewritten to null by `normalize_provenance` and surfaced as
a single collapsed warning. Naming an absent character in an author-only/protective field
(`known_before_scene.omniscient_author`, `must_remain_hidden.*`, `intentional_mysteries`,
`forbidden_beats`) is CORRECT layering — hidden truth may exist author-side as long as it stays out of
reader/POV/surface-prose fields — so it only warns, informationally. The orchestrator is
`evaluate_scene_packet`, which returns a `ScenePacketValidationResult` whose `draftable` reflects only the
hard blockers and whose `repair_tasks`/`export_blockers` carry the fix-it queue.

Pure and import-light (no DB, no models): inputs are plain dicts, output is a result object / list of
violations the derive persists on the packet. `block` severity stops drafting; `repair` blocks final
export only; `warn` is surfaced but advisory. Absence checks use exact, case-insensitive, whole-word name
matching ONLY — deliberately NOT a general NER system (DESIGN: do not overreach into fuzzy NLP without
tests).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any

from dominion.shared.severity import Severity, issue_gates
from dominion.shared.text_match import as_str_list, collect_strings, get_dotted, names_present

# Meta "sources" the model reaches for when it has no real snippet handle: the outline, the packet
# it was localizing, the seed, the budget, or a bare "inference/canon" label. These are NOT retrieved
# handles — they are normalized to a null source_id (author inference) rather than blocking the packet.
_META_SOURCE_LABELS: frozenset[str] = frozenset(
    {"outline", "chapter_packet", "scene_seed", "seed", "word_budget", "packet", "canon", "inference"}
)
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)

# Body fields that imply a character is ACTING on-page (block an absent character here) vs. fields that
# are about knowledge/reveals/off-page references (only warn — naming an absent character is plausible).
# reviewer_instructions is deliberately EXCLUDED: it is meta-guidance TO A REVIEWER ("check that X is NOT
# named", "flag if X surfaces on-page"), never an assertion that a character is on-page. Checking it here
# produced the exact regression this contract exists to prevent: a reviewer trap protecting a hidden
# character ("she must stay unmentioned/hidden") was itself blocked for naming that character.
_ON_PAGE_FIELDS: tuple[str, ...] = ("required_beats", "exit_state")
# pov_permissions is a dict: only the perception sub-keys imply presence; must_not_know / may_be_wrong
# legitimately reference an absent character, so they are excluded.
_POV_ON_PAGE_SUBKEYS: tuple[str, ...] = ("may_notice", "may_infer")

# Dotted body paths where naming an absent/roster-hidden character means the READER or POV already
# effectively knows them — the exact reader/POV-knowledge collapse this contract exists to prevent (e.g.
# a character resolved as a hidden threat, still correctly absent from the on-page cast, but leaked into
# `known_before_scene.reader` as if the reader already knows). This is a real leak, not a plausible
# off-page mention, so it BLOCKS — same severity as an on-page appearance. Previously these dotted paths
# were lumped into one generic off-page-warn bucket alongside genuinely safe author-only fields, which is
# why a leaked reveal only ever warned instead of blocking (the reported collapse bug).
_READER_POV_KNOWLEDGE_PATHS: tuple[str, ...] = (
    "known_before_scene.reader",
    "known_before_scene.pov",
    "learned_during_scene.reader_must_learn",
    "learned_during_scene.reader_may_learn",
    "learned_during_scene.reader_may_infer_only",
)

# Dotted body paths (or whole-field names) where naming an absent/hidden character is CORRECT usage —
# author-only bookkeeping, a declaration of what must stay hidden, an intentional mystery, or a protective
# forbidden-beat. These are never a leak by construction, so they only ever WARN (informational — "here is
# where the hidden fact lives, confirm it's protected"), never block.
_AUTHOR_ONLY_REFERENCE_PATHS: tuple[str, ...] = (
    "known_before_scene.omniscient_author",
    "must_remain_hidden.reader",
    "must_remain_hidden.pov",
    "must_remain_hidden.all_surface_prose",
)
_AUTHOR_ONLY_REFERENCE_FIELDS: tuple[str, ...] = ("intentional_mysteries", "forbidden_beats")


@dataclass(frozen=True)
class ScenePacketViolation:
    """One deterministic contract breach. `field` is the dotted body path when one applies (else None),
    so the editor can point the human straight at it. `block` fails the packet closed; `repair` is a
    machine-readable fix-it task (blocks final export only); `warn` is shown but blocks nothing."""

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


def _beat_represented(seed_beat: str, body_beats: list[str]) -> bool:
    """Lenient check that a seed required beat survives into the packet's required_beats: substring
    either direction, or a majority of its content words land in some packet beat. Lenient on purpose —
    this is a WARN, and the packet may legitimately reword a beat."""
    sb = seed_beat.strip().lower()
    if not sb:
        return True
    for bb in body_beats:
        b = bb.strip().lower()
        if sb in b or b in sb:
            return True
    sb_words = {w for w in re.findall(r"\w+", sb) if len(w) > 3}
    if sb_words:
        for bb in body_beats:
            b_words = set(re.findall(r"\w+", bb.lower()))
            if len(sb_words & b_words) >= max(1, len(sb_words) // 2):
                return True
    return False


def validate_scene_packet_contract(
    *,
    body: dict[str, Any],
    chapter_packet_body: dict[str, Any],
    scene_seed: dict[str, Any],
    word_budget: dict[str, Any],
    sources: list[dict[str, Any]],
) -> list[ScenePacketViolation]:
    """Deterministic checks on an assembled ScenePacket body. Returns every violation found (block +
    warn); the caller blocks the packet when any is `block`. Decidable facts only — never semantic
    judgement (that is QA's job, which runs after this passes)."""
    if not isinstance(body, dict):
        return [ScenePacketViolation("invalid_body", None, "scene packet body is not a JSON object", "block")]

    violations: list[ScenePacketViolation] = []

    # 1/7. Source-handle validity: a cited source_id that does not resolve to a retrieved handle (the
    # `sources` legend) is invalid provenance. This is optional metadata, so it only WARNS — it never
    # blocks drafting. The derive path runs `normalize_provenance` first (which nulls these and emits one
    # collapsed warning), so this per-claim check is effectively a defensive fallback for direct callers.
    valid_handles = {str(s.get("handle")) for s in sources if isinstance(s, dict) and s.get("handle")}
    claim_sources = body.get("claim_sources")
    if isinstance(claim_sources, list):
        for i, claim in enumerate(claim_sources):
            if not isinstance(claim, dict):
                continue
            sid = claim.get("source_id")
            if sid is None or str(sid).strip().lower() in ("", "null"):
                continue  # null source_id = the author's own inference, allowed
            if str(sid) not in valid_handles:
                violations.append(
                    ScenePacketViolation(
                        kind="invalid_source_handle",
                        field=f"claim_sources[{i}].source_id",
                        detail=(
                            f"claim cites source_id {sid!r}, which is not a retrieved source handle "
                            f"(valid handles: {sorted(valid_handles) or 'none'})"
                        ),
                        severity="warn",
                    )
                )

    # 2. Word-budget authority: the model must not override the deterministic planner's budget. A
    # mis-bucketed echo is fixable (re-stamp from the planner), so it is a repair task, not a hard
    # block — `evaluate_scene_packet` normally stamps the budget server-side before this check runs,
    # making this a defensive fallback for direct callers.
    if body.get("word_budget") != word_budget:
        violations.append(
            ScenePacketViolation(
                kind="word_budget_override",
                field="word_budget",
                detail="word_budget does not match the deterministic planner's budget — the model must not override it",
                severity="repair",
            )
        )

    # 3. Scene-number consistency with the seed.
    body_scene_no = body.get("scene_no")
    seed_scene_no = scene_seed.get("scene_no") if isinstance(scene_seed, dict) else None
    if isinstance(body_scene_no, int) and isinstance(seed_scene_no, int) and body_scene_no != seed_scene_no:
        violations.append(
            ScenePacketViolation(
                kind="scene_no_mismatch",
                field="scene_no",
                detail=f"body scene_no {body_scene_no} does not match seed scene_no {seed_scene_no}",
                severity="block",
            )
        )

    # 4. Required-beat consistency: a seed required beat that the packet silently drops is a WARN (the
    # packet may reword, and an intentionally-empty list is allowed — only flag when the packet HAS beats).
    seed_beats = as_str_list(scene_seed.get("required_beats")) if isinstance(scene_seed, dict) else []
    body_beats = as_str_list(body.get("required_beats"))
    if seed_beats and body_beats:
        for sb in seed_beats:
            if not _beat_represented(sb, body_beats):
                violations.append(
                    ScenePacketViolation(
                        kind="required_beat_dropped",
                        field="required_beats",
                        detail=f"seed required beat is not represented in the scene packet: {sb!r}",
                        severity="warn",
                    )
                )

    # 5/6. Roster / absence: an absent character placed in an on-page field is acting in a scene they are
    # not in → repair. One named in a reader/POV-knowledge field means the reader/POV already effectively
    # knows them despite being declared absent → also repair (the reader/POV-knowledge collapse this
    # contract exists to prevent — a hidden reveal leaked early is not "plausible off-page color", it is
    # a real defect). Both are fixable roster/beat mis-buckets the author agent can repair, so they gate
    # final export, never drafting. One referenced only in an author-only/protective field (declaring what
    # must stay hidden, an intentional mystery, a forbidden beat) is CORRECT layering → warn only,
    # informational. Known absent names only, whole-word, case-insensitive.
    absent = as_str_list(chapter_packet_body.get("characters_absent")) if isinstance(chapter_packet_body, dict) else []
    if absent:
        for field_name in _ON_PAGE_FIELDS:
            for name in names_present(collect_strings(body.get(field_name)), absent):
                violations.append(
                    ScenePacketViolation(
                        kind="absent_character_on_page",
                        field=field_name,
                        detail=f"absent character {name!r} appears in on-page field {field_name!r}",
                        severity="repair",
                    )
                )
        pov_perms = body.get("pov_permissions")
        if isinstance(pov_perms, dict):
            for sub in _POV_ON_PAGE_SUBKEYS:
                for name in names_present(collect_strings(pov_perms.get(sub)), absent):
                    violations.append(
                        ScenePacketViolation(
                            kind="absent_character_on_page",
                            field=f"pov_permissions.{sub}",
                            detail=f"absent character {name!r} is marked perceivable in pov_permissions.{sub}",
                            severity="repair",
                        )
                    )
        for dotted in _READER_POV_KNOWLEDGE_PATHS:
            for name in names_present(collect_strings(get_dotted(body, dotted)), absent):
                violations.append(
                    ScenePacketViolation(
                        kind="absent_character_reader_pov_leak",
                        field=dotted,
                        detail=(
                            f"absent character {name!r} appears in reader/POV-knowledge field {dotted!r} — "
                            "this asserts the reader or POV already knows them despite being declared absent "
                            "from this chapter's roster"
                        ),
                        severity="repair",
                    )
                )
        for dotted in _AUTHOR_ONLY_REFERENCE_PATHS:
            for name in names_present(collect_strings(get_dotted(body, dotted)), absent):
                violations.append(
                    ScenePacketViolation(
                        kind="absent_character_author_only_reference",
                        field=dotted,
                        detail=(
                            f"absent character {name!r} referenced in author-only/protective field {dotted!r} "
                            "— fine as long as they stay out of reader/POV/surface-prose fields"
                        ),
                        severity="warn",
                    )
                )
        for field_name in _AUTHOR_ONLY_REFERENCE_FIELDS:
            for name in names_present(collect_strings(body.get(field_name)), absent):
                violations.append(
                    ScenePacketViolation(
                        kind="absent_character_author_only_reference",
                        field=field_name,
                        detail=(
                            f"absent character {name!r} referenced in author-only/protective field "
                            f"{field_name!r} — fine as long as they stay out of reader/POV/surface-prose fields"
                        ),
                        severity="warn",
                    )
                )

    return violations


def _classify_source_id(sid: str, valid_handles: set[str]) -> str:
    """Why a non-null, non-valid source_id was rejected — used only to make the collapsed warning
    legible ("out-of-range handle", "meta label", "uuid", "arbitrary label"). Never blocks."""
    if _UUID_RE.match(sid):
        return "uuid"
    if sid.lower() in _META_SOURCE_LABELS:
        return "meta label"
    if re.match(r"^C\d+$", sid):
        return "out-of-range handle"  # shaped like C7 but not in the retrieved legend
    return "arbitrary label"


def normalize_provenance(
    body: dict[str, Any], sources: list[dict[str, Any]]
) -> tuple[dict[str, Any], list[ScenePacketViolation]]:
    """Rewrite every invalid `claim_sources[i].source_id` to null (the author-inference value) and return
    the cleaned body plus at most ONE collapsed `provenance_normalized` warning.

    Optional provenance must never block drafting: the model routinely cites the outline, the scene seed,
    a UUID, or an out-of-range handle instead of a real retrieved [C#] handle. Keeping the claim TEXT but
    nulling the bad handle preserves the (advisory) provenance intent while making the body draft-safe.
    Warnings are deduplicated at the source — one summary line with examples, never a wall per claim."""
    if not isinstance(body, dict):
        return body, []
    claim_sources = body.get("claim_sources")
    if not isinstance(claim_sources, list):
        return body, []

    valid_handles = {str(s.get("handle")) for s in sources if isinstance(s, dict) and s.get("handle")}
    normalized_claims: list[Any] = []
    rewritten: list[str] = []
    for claim in claim_sources:
        if not isinstance(claim, dict):
            normalized_claims.append(claim)
            continue
        sid = claim.get("source_id")
        if sid is None or str(sid).strip().lower() in ("", "null"):
            normalized_claims.append(claim)  # keep null — author inference
            continue
        s = str(sid)
        if s in valid_handles:
            normalized_claims.append(claim)  # keep a real retrieved handle
            continue
        rewritten.append(s)
        normalized_claims.append({**claim, "source_id": None})

    if not rewritten:
        return body, []

    # Dedup examples preserving first-seen order; group counts by category for a legible one-liner.
    seen: list[str] = []
    for s in rewritten:
        if s not in seen:
            seen.append(s)
    examples = ", ".join(seen[:3])
    more = f" (+{len(seen) - 3} more)" if len(seen) > 3 else ""
    categories = sorted({_classify_source_id(s, valid_handles) for s in seen})
    detail = (
        f"{len(rewritten)} claim source id(s) did not match a retrieved handle "
        f"(valid: {sorted(valid_handles) or 'none'}) and were normalized to null "
        f"[{', '.join(categories)}]. Examples: {examples}{more}"
    )
    normalized_body = {**body, "claim_sources": normalized_claims}
    return normalized_body, [
        ScenePacketViolation(kind="provenance_normalized", field="claim_sources", detail=detail, severity="warn")
    ]


@dataclass(frozen=True)
class ScenePacketValidationResult:
    """The outcome of evaluating one authored ScenePacket body: the server-normalized body the packet
    should persist/draft from, plus every violation found. `draftable` is true when nothing hard-blocks —
    repair tasks and warnings do not affect it (repairs gate final export via `export_blockers`, never
    drafting). This is the single object the derive/policy layer reads instead of re-deriving
    severity buckets from a flat list."""

    normalized_body: dict[str, Any]
    violations: list[ScenePacketViolation]

    @property
    def draft_blockers(self) -> list[ScenePacketViolation]:
        return [v for v in self.violations if v.severity == "block"]

    @property
    def repair_tasks(self) -> list[ScenePacketViolation]:
        return [v for v in self.violations if v.severity == "repair"]

    @property
    def export_blockers(self) -> list[ScenePacketViolation]:
        return [v for v in self.violations if v.severity in ("block", "repair")]

    @property
    def warnings(self) -> list[ScenePacketViolation]:
        return [v for v in self.violations if v.severity == "warn"]

    @property
    def draftable(self) -> bool:
        return not self.draft_blockers


def evaluate_scene_packet(
    *,
    body: dict[str, Any],
    chapter_packet_body: dict[str, Any],
    scene_seed: dict[str, Any],
    word_budget: dict[str, Any],
    scene_no: int | None,
    sources: list[dict[str, Any]],
    block_on_provenance: bool = False,
) -> ScenePacketValidationResult:
    """Normalize → validate → draftability, in one place. Stamps the deterministic facts the planner/seed
    own (word_budget, scene_no) server-side so a sloppy model echo can't block, normalizes provenance,
    then runs the deterministic contract checks on the normalized body. Only true draft-safety failures
    end up as `draft_blockers`. `block_on_provenance=True` (a safety valve, default off) promotes the
    provenance warning to a blocker — writer-first defaults keep it a warning."""
    if not isinstance(body, dict):
        return ScenePacketValidationResult(
            normalized_body={},
            violations=[ScenePacketViolation("invalid_body", None, "scene packet body is not a JSON object", "block")],
        )

    violations: list[ScenePacketViolation] = []
    normalized: dict[str, Any] = dict(body)

    # Deterministic facts are authoritative: stamp them from the planner/seed, or hard-block if they
    # cannot be recovered (drafting from a packet with no word budget / no scene number is unsafe).
    if word_budget:
        normalized["word_budget"] = word_budget
    else:
        violations.append(
            ScenePacketViolation(
                "word_budget_unrecoverable",
                "word_budget",
                "the deterministic length planner produced no word budget for this scene — cannot draft",
                "block",
            )
        )
    if scene_no is not None:
        normalized["scene_no"] = scene_no
    else:
        violations.append(
            ScenePacketViolation(
                "scene_no_unrecoverable",
                "scene_no",
                "scene_no could not be recovered from the seed — cannot draft",
                "block",
            )
        )

    normalized, provenance_warnings = normalize_provenance(normalized, sources)
    if block_on_provenance:
        provenance_warnings = [replace(w, severity="block") for w in provenance_warnings]
    violations.extend(provenance_warnings)

    # Contract checks run on the normalized body: provenance is already nulled (so invalid_source_handle
    # is quiet here) and word_budget/scene_no already match (so those checks are quiet too) — what remains
    # that can fire is the repair-task set (absent-character-on-page, etc.) plus warn advisories.
    violations.extend(
        validate_scene_packet_contract(
            body=normalized,
            chapter_packet_body=chapter_packet_body,
            scene_seed=scene_seed,
            word_budget=word_budget,
            sources=sources,
        )
    )
    return ScenePacketValidationResult(normalized_body=normalized, violations=violations)
