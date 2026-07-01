"""Deterministic ScenePacket contract validation (scene-packet contract system).

QA is an LLM attacker — good at semantic risk, unreliable at hard facts. This module is the
deterministic gate that runs BEFORE QA and catches the failure modes a checker should never guess at:
a claim citing a source handle that was never retrieved (fabricated provenance), the model overriding
the deterministic word budget, a scene-number/required-beat mismatch with the seed, and an absent
character placed on-page. These are decidable from the data, so they are decided here — not left to a
model that can hallucinate the verdict.

Pure and import-light (no DB, no models): inputs are plain dicts, output is a list of violations the
derive persists on a blocked packet. `block` severity stops drafting; `warn` is surfaced but advisory.
Absence checks use exact, case-insensitive, whole-word name matching ONLY — deliberately NOT a general
NER system (DESIGN: do not overreach into fuzzy NLP without tests).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

Severity = Literal["warn", "block"]

# Body fields that imply a character is ACTING on-page (block an absent character here) vs. fields that
# are about knowledge/reveals/off-page references (only warn — naming an absent character is plausible).
_ON_PAGE_FIELDS: tuple[str, ...] = ("required_beats", "exit_state", "reviewer_instructions")
_OFF_PAGE_FIELDS: tuple[str, ...] = (
    "forbidden_beats",
    "known_before_scene",
    "learned_during_scene",
    "must_remain_hidden",
    "intentional_mysteries",
)
# pov_permissions is a dict: only the perception sub-keys imply presence; must_not_know / may_be_wrong
# legitimately reference an absent character, so they are excluded.
_POV_ON_PAGE_SUBKEYS: tuple[str, ...] = ("may_notice", "may_infer")


@dataclass(frozen=True)
class ScenePacketViolation:
    """One deterministic contract breach. `field` is the dotted body path when one applies (else None),
    so the editor can point the human straight at it. `block` fails the packet closed; `warn` is shown
    but does not block."""

    kind: str
    field: str | None
    detail: str
    severity: Severity

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "field": self.field, "detail": self.detail, "severity": self.severity}


def _as_str_list(value: Any) -> list[str]:
    return [str(v).strip() for v in value if str(v).strip()] if isinstance(value, list) else []


def _collect_strings(value: Any) -> list[str]:
    """Flatten any string content reachable from a body field (str / list / nested dict) into a flat
    list, so an absence scan can look at a field regardless of its shape."""
    out: list[str] = []
    if isinstance(value, str):
        if value.strip():
            out.append(value)
    elif isinstance(value, dict):
        for v in value.values():
            out.extend(_collect_strings(v))
    elif isinstance(value, list):
        for v in value:
            out.extend(_collect_strings(v))
    return out


def _names_present(text_items: list[str], names: list[str]) -> list[str]:
    """Names that appear as a whole word (case-insensitive) anywhere in `text_items`. Whole-word, not
    bare substring, so a short name can't match inside an unrelated word — but still no fuzzy NER."""
    if not text_items or not names:
        return []
    blob = "\n".join(text_items).lower()
    found: list[str] = []
    for name in names:
        n = name.strip().lower()
        if n and re.search(rf"\b{re.escape(n)}\b", blob):
            found.append(name)
    return found


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

    # 1/7. Source-handle validity + no fabricated provenance: every cited source_id must resolve to a
    # handle that was actually retrieved (the `sources` legend). A claim citing e.g. "C99" is fabricated.
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
                        severity="block",
                    )
                )

    # 2. Word-budget authority: the model must not override the deterministic planner's budget.
    if body.get("word_budget") != word_budget:
        violations.append(
            ScenePacketViolation(
                kind="word_budget_override",
                field="word_budget",
                detail="word_budget does not match the deterministic planner's budget — the model must not override it",
                severity="block",
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
    seed_beats = _as_str_list(scene_seed.get("required_beats")) if isinstance(scene_seed, dict) else []
    body_beats = _as_str_list(body.get("required_beats"))
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
    # not in → block; one referenced only in a knowledge/off-page field is plausible (reader learns of
    # them, they stay hidden) → warn. Known absent names only, whole-word, case-insensitive.
    absent = _as_str_list(chapter_packet_body.get("characters_absent")) if isinstance(chapter_packet_body, dict) else []
    if absent:
        for field_name in _ON_PAGE_FIELDS:
            for name in _names_present(_collect_strings(body.get(field_name)), absent):
                violations.append(
                    ScenePacketViolation(
                        kind="absent_character_on_page",
                        field=field_name,
                        detail=f"absent character {name!r} appears in on-page field {field_name!r}",
                        severity="block",
                    )
                )
        pov_perms = body.get("pov_permissions")
        if isinstance(pov_perms, dict):
            for sub in _POV_ON_PAGE_SUBKEYS:
                for name in _names_present(_collect_strings(pov_perms.get(sub)), absent):
                    violations.append(
                        ScenePacketViolation(
                            kind="absent_character_on_page",
                            field=f"pov_permissions.{sub}",
                            detail=f"absent character {name!r} is marked perceivable in pov_permissions.{sub}",
                            severity="block",
                        )
                    )
        for field_name in _OFF_PAGE_FIELDS:
            for name in _names_present(_collect_strings(body.get(field_name)), absent):
                violations.append(
                    ScenePacketViolation(
                        kind="absent_character_off_page",
                        field=field_name,
                        detail=f"absent character {name!r} referenced in off-page field {field_name!r}",
                        severity="warn",
                    )
                )

    return violations
