"""The canonical `chapter_master_packet` artifact (schema_version 1) — ONE source of truth.

Before this module the chapter's planning truth lived in 5+ competing shapes (raw AuthorPacketInternal
body, a projected copy embedded at `body._surface_contract`, top-level `scene_seeds` overwritten with
that projection, the sibling `open_questions` column, and per-consumer re-copies). This module collapses
them: `ChapterPacket.body` IS the artifact, and everything else is an explicitly-derived view.

Canonical shape (see chapter_master_packet.schema.json next to this file):

- ``schema_version`` (int, 1), ids (``book_id``/``chapter_id``/``chapter_no``), ``pov``, ``status``,
  ``source_inputs``, ``lineage`` — identity + provenance stamps.
- ``chapter_contract`` — job/spine/entry/exit/emotional spine, ``locks`` (canon/roster/relationship/
  timeline), ``claims`` (provenance-carrying), and ``open_questions`` ({items, resolved}) — the IN-BODY
  source of truth for open questions (the ChapterPacket.open_questions column is a derived sync kept
  for API back-compat; writers update both from this section).
- ``cast[]`` — the structured roster replacing the four flat arrays: each entry is
  {name, presence: present|absent|mentioned_only|forbidden, reader_must_notice,
  minimum_visible_evidence, notes}.
- ``scene_seeds[]`` — the canonical scenes array (the established key name is kept so every existing
  consumer keeps working; it is the brief's `scenes[]`). Authoritative RAW planning data — may contain
  hidden canonical truth — each seed carries a ``visible_character_evidence[]`` slot. It exists exactly
  ONCE at top level: the drafter-safe projection lives ONLY under the derived ``_surface_contract`` key.
- ``qa`` — {verdict, blocking_issues, warnings, repair_tasks, graded_by, last_checked_at}; every issue
  uses the machine-readable severity shape (severity + blocks_* facts from shared.severity).
- ``_surface_contract`` — DERIVED, never authoritative: the drafter-safe projection built by
  ``surface_contract.build_surface_contract``; writers (propose/update) rebuild it from the raw body.

Compatibility contract (tolerant reader, NO hard migration — prod rows keep their legacy JSONB):

- ``to_master_packet`` accepts BOTH the legacy AuthorPacketInternal shape and the canonical shape and
  returns the canonical shape. It is pure and idempotent: ``to_master_packet(to_master_packet(x)) ==
  to_master_packet(x)``.
- The legacy flat fields (the four roster arrays, ``chapter_job``/``one_sentence_spine``/
  ``entry_state``/``exit_state``/``emotional_spine``, the lock arrays, the ``open_questions`` string
  list) remain present in the canonical body as COMPAT MIRRORS, regenerated from the structured
  sections by this reader on every normalize. They are also the human editing surface (the Desk packet
  editor writes them), so when mirrors and structured sections disagree the mirrors win and the
  structured sections are rebuilt (per-name ``cast`` extras such as ``reader_must_notice`` are
  preserved by name). Consumers that only read those flat fields therefore work unchanged on BOTH
  legacy and canonical bodies; consumers of the structured sections must go through this reader.
"""

from __future__ import annotations

from typing import Any

from dominion.shared.severity import issue_gates
from dominion.shared.text_match import as_str_list
from dominion.workers.packet.validation import leading_roster_name

SCHEMA_VERSION = 1

#: presence value -> legacy flat roster field (order = legacy roster order, kept stable for mirrors).
PRESENCE_FIELDS: tuple[tuple[str, str], ...] = (
    ("present", "characters_present"),
    ("absent", "characters_absent"),
    ("mentioned_only", "characters_mentioned_only"),
    ("forbidden", "characters_forbidden"),
)
PRESENCE_VALUES: frozenset[str] = frozenset(presence for presence, _field in PRESENCE_FIELDS)

_LOCK_KEYS: tuple[str, ...] = ("canon_locks", "roster_locks", "relationship_locks", "timeline_locks")

_CONTRACT_TEXT_FIELDS: tuple[tuple[str, str], ...] = (
    ("job", "chapter_job"),
    ("spine", "one_sentence_spine"),
    ("entry_state", "entry_state"),
    ("exit_state", "exit_state"),
    ("emotional_spine", "emotional_spine"),
)


def _str_or_none(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _normalize_open_questions(param: Any, src: dict[str, Any]) -> dict[str, Any]:
    """{items: [str], resolved: [...]} from (in priority order): the explicit param (the sibling column
    / API payload — writers keep it in sync, and for legacy rows it is the adjudicated state), the
    canonical body section, then the legacy author list at body.open_questions."""
    contract = src.get("chapter_contract")
    body_section = contract.get("open_questions") if isinstance(contract, dict) else None
    for candidate in (param, body_section):
        if isinstance(candidate, dict):
            resolved = candidate.get("resolved")
            return {
                "items": as_str_list(candidate.get("items")),
                "resolved": resolved if isinstance(resolved, list) else [],
            }
        if isinstance(candidate, list):
            return {"items": as_str_list(candidate), "resolved": []}
    return {"items": as_str_list(src.get("open_questions")), "resolved": []}


def _cast_entry(*, name: str, presence: str, raw: str | None, prev: dict[str, Any]) -> dict[str, Any]:
    notes = prev.get("notes") if isinstance(prev.get("notes"), str) and str(prev.get("notes")).strip() else None
    if raw is not None and raw != name:
        notes = raw  # the full authored roster entry, annotations intact ("Brent (404 guild member)")
    evidence = prev.get("minimum_visible_evidence")
    return {
        "name": name,
        "presence": presence,
        "reader_must_notice": bool(prev.get("reader_must_notice", False)),
        "minimum_visible_evidence": evidence if isinstance(evidence, str) and evidence.strip() else None,
        "notes": notes,
    }


def _reconcile_cast(src: dict[str, Any]) -> list[dict[str, Any]]:
    """The structured cast, reconciled from the flat roster arrays (the editing surface — they win on
    conflict) with per-name extras preserved from any existing cast. A body with a cast but none of the
    flat arrays (a pure-canonical writer) keeps its cast as the source. Duplicate names across
    contradictory buckets are PRESERVED (one entry per roster entry) so validation still sees them."""
    existing = src.get("cast")
    extras: dict[str, dict[str, Any]] = {}
    if isinstance(existing, list):
        for entry in existing:
            if isinstance(entry, dict) and str(entry.get("name") or "").strip():
                extras.setdefault(str(entry["name"]).strip().lower(), entry)

    has_roster_keys = any(field in src for _presence, field in PRESENCE_FIELDS)
    cast: list[dict[str, Any]] = []
    if has_roster_keys or not isinstance(existing, list):
        for presence, field in PRESENCE_FIELDS:
            for raw in as_str_list(src.get(field)):
                name = leading_roster_name(raw) or raw
                cast.append(_cast_entry(name=name, presence=presence, raw=raw, prev=extras.get(name.lower(), {})))
        return cast
    for entry in existing:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        if not name:
            continue
        # An unknown presence is kept verbatim (never guessed toward "present" — that could put a
        # forbidden character on the page); validate_master_packet flags it as a repair task.
        presence = str(entry.get("presence") or "")
        cast.append(_cast_entry(name=name, presence=presence, raw=None, prev=entry))
    return cast


def _mirror_entry(entry: dict[str, Any]) -> str:
    """The flat-array form of one cast entry: the full authored notes string when it still leads with
    the name (round-trips annotations), else the bare name."""
    name = str(entry.get("name") or "")
    notes = entry.get("notes")
    if isinstance(notes, str) and notes.strip() and leading_roster_name(notes).strip().lower() == name.strip().lower():
        return notes
    return name


def _normalize_seed(seed: Any) -> Any:
    if not isinstance(seed, dict):
        return seed
    out = dict(seed)
    evidence = out.get("visible_character_evidence")
    out["visible_character_evidence"] = evidence if isinstance(evidence, list) else []
    return out


def _issue_list(value: Any) -> list[dict[str, Any]]:
    return [issue for issue in value if isinstance(issue, dict)] if isinstance(value, list) else []


def _normalize_qa(value: Any) -> dict[str, Any]:
    qa = value if isinstance(value, dict) else {}
    return {
        "verdict": _str_or_none(qa.get("verdict")),
        "blocking_issues": _issue_list(qa.get("blocking_issues")),
        "warnings": _issue_list(qa.get("warnings")),
        "repair_tasks": _issue_list(qa.get("repair_tasks")),
        "graded_by": _str_or_none(qa.get("graded_by")),
        "last_checked_at": _str_or_none(qa.get("last_checked_at")),
    }


def to_master_packet(
    body: Any,
    open_questions: Any = None,
    *,
    book_id: Any = None,
    chapter_id: Any = None,
    chapter_no: int | None = None,
    pov: str | None = None,
    status: Any = None,
) -> dict[str, Any]:
    """Tolerant reader: legacy AuthorPacketInternal OR canonical body -> the canonical
    chapter_master_packet shape. Pure (no DB, no clock) and idempotent.

    `open_questions` is the sibling-column value (or an API payload) to fold into
    chapter_contract.open_questions; when None the body's own value (canonical section, else the legacy
    author list) is used. The identity kwargs stamp ids/lifecycle when the caller knows them; existing
    body values are preserved otherwise. Unknown keys pass through untouched (internal planning fields
    are never dropped)."""
    src: dict[str, Any] = body if isinstance(body, dict) else {}
    out = dict(src)
    out["schema_version"] = SCHEMA_VERSION

    for key, value in (("book_id", book_id), ("chapter_id", chapter_id), ("status", status)):
        if value is not None:
            out[key] = str(getattr(value, "value", value))
        else:
            out.setdefault(key, None)
    for key, value in (("chapter_no", chapter_no), ("pov", pov)):
        if value is not None:
            out[key] = value
        else:
            out.setdefault(key, None)

    source_inputs = out.get("source_inputs")
    out["source_inputs"] = source_inputs if isinstance(source_inputs, dict) else {}
    lineage = out.get("lineage")
    out["lineage"] = lineage if isinstance(lineage, dict) else {"source": "author_packet_internal"}

    cast = _reconcile_cast(src)
    out["cast"] = cast
    for presence, field in PRESENCE_FIELDS:
        out[field] = [_mirror_entry(entry) for entry in cast if entry.get("presence") == presence]

    seeds = src.get("scene_seeds")
    out["scene_seeds"] = [_normalize_seed(seed) for seed in seeds] if isinstance(seeds, list) else []

    claims = src.get("claims")
    out["claims"] = claims if isinstance(claims, list) else []

    oq = _normalize_open_questions(open_questions, src)
    out["chapter_contract"] = {
        **{canonical: _str_or_none(src.get(legacy)) for canonical, legacy in _CONTRACT_TEXT_FIELDS},
        "locks": {key: as_str_list(src.get(key)) for key in _LOCK_KEYS},
        "claims": [claim for claim in out["claims"] if isinstance(claim, dict)],
        "open_questions": oq,
    }
    out["open_questions"] = list(oq["items"])  # legacy mirror (author-shape string list)

    out["qa"] = _normalize_qa(src.get("qa"))
    return out


def master_open_questions(body: Any, column: Any = None) -> dict[str, Any]:
    """The canonical open-questions dict ({items, resolved}) for a packet, folding in the sibling
    column when provided (the column wins — writers keep it in sync for canonical rows, and for legacy
    rows it is the adjudicated state)."""
    return _normalize_open_questions(column, body if isinstance(body, dict) else {})


def with_open_questions(body: Any, open_questions: Any) -> Any:
    """Fold an adjudicated open_questions payload into an already-canonical body (question-only edits).
    Legacy bodies are returned untouched — they are canonicalized on their next body edit / propose,
    and the column remains their adjudicated source until then."""
    if not (isinstance(body, dict) and isinstance(body.get("chapter_contract"), dict)):
        return body
    oq = _normalize_open_questions(open_questions, {})
    contract = {**body["chapter_contract"], "open_questions": oq}
    return {**body, "chapter_contract": contract, "open_questions": list(oq["items"])}


def drafter_view(body: Any) -> dict[str, Any]:
    """The drafter-safe projection of a packet body: the derived ``_surface_contract`` when present,
    else the body itself (legacy rows authored before surface projection existed). Consumers that hand
    packet text toward drafting agents MUST read through this, never the raw top-level seeds."""
    if isinstance(body, dict):
        surface = body.get("_surface_contract")
        if isinstance(surface, dict):
            return surface
        return body
    return {}


def _violation(kind: str, field: str | None, detail: str, severity: str) -> dict[str, Any]:
    return {"kind": kind, "field": field, "detail": detail, "severity": severity, **issue_gates(severity)}


def validate_master_packet(body: Any) -> list[dict[str, Any]]:
    """Pure structural validation of a canonical chapter_master_packet body. Returns machine-readable
    violation dicts (severity + blocks_* facts). ``block`` is reserved for the true-blocker list
    (unusable body, wrong schema, no draftable scene purpose); every fixable gap is ``repair``.
    Validate the output of ``to_master_packet`` — legacy bodies should be normalized first."""
    if not isinstance(body, dict):
        return [_violation("invalid_body", None, "master packet body is not a JSON object", "block")]
    violations: list[dict[str, Any]] = []

    if body.get("schema_version") != SCHEMA_VERSION:
        violations.append(
            _violation(
                "schema_version_invalid",
                "schema_version",
                f"schema_version must be {SCHEMA_VERSION} (run to_master_packet to normalize)",
                "block",
            )
        )

    contract = body.get("chapter_contract")
    if not isinstance(contract, dict):
        violations.append(
            _violation("missing_chapter_contract", "chapter_contract", "chapter_contract section is missing", "block")
        )
    else:
        if not _str_or_none(contract.get("job")):
            violations.append(
                _violation(
                    "contract_job_missing",
                    "chapter_contract.job",
                    "the chapter contract has no job — state what this chapter must accomplish",
                    "repair",
                )
            )
        if not isinstance(contract.get("locks"), dict):
            violations.append(
                _violation("locks_malformed", "chapter_contract.locks", "locks must be an object", "repair")
            )
        if not isinstance(contract.get("claims"), list):
            violations.append(
                _violation(
                    "claims_missing",
                    "chapter_contract.claims",
                    "claims must be a list (provenance is required)",
                    "block",
                )
            )
        oq = contract.get("open_questions")
        if not isinstance(oq, dict) or not isinstance(oq.get("items"), list):
            violations.append(
                _violation(
                    "open_questions_malformed",
                    "chapter_contract.open_questions",
                    "open_questions must be an object with an items list",
                    "repair",
                )
            )

    cast = body.get("cast")
    if not isinstance(cast, list):
        violations.append(_violation("cast_missing", "cast", "cast must be a list of roster entries", "repair"))
    else:
        for i, entry in enumerate(cast):
            if not isinstance(entry, dict) or not str(entry.get("name") or "").strip():
                violations.append(
                    _violation("cast_entry_invalid", f"cast[{i}]", "cast entry must be an object with a name", "repair")
                )
            elif entry.get("presence") not in PRESENCE_VALUES:
                violations.append(
                    _violation(
                        "cast_presence_invalid",
                        f"cast[{i}].presence",
                        f"presence {entry.get('presence')!r} is not one of {sorted(PRESENCE_VALUES)}",
                        "repair",
                    )
                )

    seeds = body.get("scene_seeds")
    if not isinstance(seeds, list) or not any(isinstance(seed, dict) for seed in seeds):
        violations.append(
            _violation("no_scenes", "scene_seeds", "the packet carries no scene seeds to draft from", "block")
        )
    else:
        jobs = 0
        for i, seed in enumerate(seeds):
            if not isinstance(seed, dict):
                violations.append(
                    _violation("scene_seed_invalid", f"scene_seeds[{i}]", "scene seed must be an object", "repair")
                )
                continue
            if _str_or_none(seed.get("scene_job")):
                jobs += 1
            else:
                violations.append(
                    _violation(
                        "scene_purpose_missing",
                        f"scene_seeds[{i}].scene_job",
                        "this scene seed has no scene_job",
                        "repair",
                    )
                )
            if not isinstance(seed.get("visible_character_evidence", []), list):
                violations.append(
                    _violation(
                        "visible_character_evidence_malformed",
                        f"scene_seeds[{i}].visible_character_evidence",
                        "visible_character_evidence must be a list",
                        "repair",
                    )
                )
        if jobs == 0:
            violations.append(
                _violation(
                    "no_draftable_scene_purpose",
                    "scene_seeds",
                    "no scene seed carries a usable scene_job — nothing is draftable",
                    "block",
                )
            )

    if not isinstance(body.get("qa"), dict):
        violations.append(_violation("qa_section_missing", "qa", "qa section must be an object", "repair"))
    surface = body.get("_surface_contract")
    if surface is not None and not isinstance(surface, dict):
        violations.append(
            _violation("surface_contract_invalid", "_surface_contract", "_surface_contract must be an object", "repair")
        )

    if isinstance(cast, list) and all(field in body for _presence, field in PRESENCE_FIELDS):
        for presence, field in PRESENCE_FIELDS:
            expected = [_mirror_entry(e) for e in cast if isinstance(e, dict) and e.get("presence") == presence]
            actual = body.get(field)
            if isinstance(actual, list) and [str(x) for x in actual] != expected:
                violations.append(
                    _violation(
                        "roster_mirror_drift",
                        field,
                        f"{field} disagrees with cast[] — run to_master_packet to re-sync the mirrors",
                        "repair",
                    )
                )

    return violations
