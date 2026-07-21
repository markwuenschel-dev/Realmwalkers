"""Author a ChapterPacket from imported-scene EVIDENCE ledgers (ADR 0028 Slice 3b, Lane A2).

`workers/packet.propose_packet_from_evidence` authors a chapter contract from the `M#` manuscript
evidence bundle instead of an outline. This module is the evidence-specific machinery it composes,
kept out of `packet/__init__` so it stays pure and unit-testable without the DB / LLM stack:

  * `SceneEvidence` — the per-scene evidence input the adoption worker (Lane A4) hands in: one
    `ImportSceneEvidence` ledger plus the immutable scene identity it is keyed by. NO DB coupling — A4
    loads the rows and constructs these (`snapshot_prose_len = len(row.snapshot_prose)`).
  * `manuscript_handles` — the stable `M1..M#` bundle (deterministic, sorted by `scene_no`) the author
    cites as a claim `source_id`, mirroring the `C#` canon handles the Packet Author already uses.
  * `render_ledger` — the compact, drafter-free digest of one scene's fact ledger placed in the author
    prompt under its `M#` handle. Raw prose never enters the prompt (ADR 0028); only the evidence does.
  * `candidate_conflicts` — the manuscript-vs-locked-canon conflict candidates fed to Lane A3's
    re-anchoring detector (`canon_conflict.detect_manuscript_canon_conflicts`). Extraction is GATED by
    `shared/claim_precedence` (the ADR 0029 asserted-fact precedence policy): a
    `DERIVED_FROM_MANUSCRIPT × LOCKED_CANON` pair is exactly the conflict the precedence order cannot
    auto-resolve, so it MUST become a human open question — `claim_precedence`, not a hardcoded rule
    here, decides that. A different source pair the order CAN resolve yields no candidate.
  * `resolve_evidence_provenance` — resolves each authored claim's `M#`/`C#` handle back to real
    provenance (manuscript → the immutable scene identity; canon → the canon row), mirroring the
    outline path's `_resolve_provenance` but adding the `M#` case.
  * `precedence_adjudication` — a persisted, human-readable audit of how the authored claims rank under
    the precedence order (`claim_precedence.rank`/`outranks`), recording which asserted-fact sources
    outrank which so a reviewer can see the adjudication the packet was built under.

The candidate-conflict SOURCE (which ledger facts become conflict candidates) is a deliberately small,
swappable seam: `candidate_conflicts` reads each ledger's `canon_conflicts` hint section — the
manuscript-side span + assertion — and re-anchors the CANON side live (Lane A3). A hint is only ever a
*candidate*: it becomes a real open question solely when A3 re-anchors both sides, and a hint that
cannot re-anchor is a fail-closed approval block, never a silently-dropped conflict.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from dominion.shared import claim_precedence
from dominion.shared.enums import ClaimSource
from dominion.workers.packet.canon_conflict import ManuscriptClaim

_EXCERPT_CHARS = 240
#: The precedence order as source-strength values, strongest first, derived from `claim_precedence`
#: itself (never a local re-listing). `FORBIDDEN` is excluded — it is a surface prohibition, not a rank,
#: and sorts last with unknown values (`rank` returns the sentinel index for it).
_PRECEDENCE_ORDER: tuple[ClaimSource, ...] = tuple(
    s for s in sorted(ClaimSource, key=claim_precedence.rank) if s is not ClaimSource.FORBIDDEN
)
#: Ledger sections rendered into the author prompt, in reading order. Scalars first, then the lists.
_SCALAR_SECTIONS: tuple[str, ...] = ("pov", "setting", "entry_state", "exit_state")
_LIST_SECTIONS: tuple[str, ...] = (
    "entities",
    "events",
    "asserted_facts",
    "state_changes",
    "reveals",
    "withholds",
    "continuity_anchors",
    "ambiguities",
    "canon_conflicts",
)
#: The ledger section whose items seed manuscript-vs-canon conflict candidates (see module docstring).
_CONFLICT_SECTION = "canon_conflicts"
#: Keys probed (in order) for the free-text assertion of a ledger item.
_ASSERTION_KEYS: tuple[str, ...] = ("assertion", "claim", "conflict", "summary", "detail", "text", "note")


@dataclass(frozen=True)
class SceneEvidence:
    """One imported scene's evidence, as the adoption worker hands it to the author (ADR 0028 Slice 3b).

    A pure value object — no DB row, no ORM. Lane A4 builds it from an `ImportSceneEvidence` parent:
    `scene_id`/`scene_version`/`prose_hash`/`ledger` come straight off the row; `scene_no` off the owning
    `Scene`; `snapshot_prose_len = len(row.snapshot_prose)` bounds the immutable-span check. The tuple
    `(scene_id, scene_version, prose_hash)` is the immutable manuscript anchor every `M#` claim traces to.
    """

    scene_id: uuid.UUID
    scene_no: int
    scene_version: int
    prose_hash: str
    ledger: Mapping[str, Any]
    snapshot_prose_len: int | None = None
    pov: str | None = None


def _item_text(item: Any) -> str:
    """A display string for one ledger item: its main free-text field if a dict, else the value itself."""
    if isinstance(item, Mapping):
        for key in _ASSERTION_KEYS:
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        # A dict with no known text key — fall back to a compact, deterministic key/value render so the
        # author still sees the fact rather than an opaque "{...}".
        parts = [f"{k}={item[k]!r}" for k in sorted(item) if k != "span" and item[k] not in (None, "", [])]
        return "; ".join(parts)
    return str(item).strip()


def _item_span(item: Any) -> tuple[int, int] | None:
    """The `[start, end)` span of a ledger item as a validated int pair, or None if absent/malformed."""
    if not isinstance(item, Mapping):
        return None
    span = item.get("span")
    if isinstance(span, (list, tuple)) and len(span) == 2 and all(isinstance(n, int) for n in span):
        return (int(span[0]), int(span[1]))
    return None


def _as_items(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    return [] if value in (None, "") else [value]


def build_manuscript_handles(evidence: Sequence[SceneEvidence]) -> dict[str, SceneEvidence]:
    """The stable `M1..M#` bundle for a chapter's evidence, ordered by `scene_no` (then `scene_id` to
    break ties deterministically). Handle → the scene's evidence, so a resolver can map an authored
    `M#` source_id back to the immutable scene identity."""
    ordered = sorted(evidence, key=lambda se: (se.scene_no, str(se.scene_id)))
    return {f"M{i}": se for i, se in enumerate(ordered, start=1)}


def render_ledger(ledger: Mapping[str, Any]) -> str:
    """Compact, drafter-free digest of one scene's fact ledger for the author prompt. Scalars as
    `pov: …` lines; list sections as `events:` blocks of `- text [start,end]`. Empty sections are
    skipped so the author sees signal, not a wall of empty keys. Never includes raw prose."""
    lines: list[str] = []
    for section in _SCALAR_SECTIONS:
        value = ledger.get(section)
        if isinstance(value, str) and value.strip():
            lines.append(f"{section}: {value.strip()}")
    for section in _LIST_SECTIONS:
        items = _as_items(ledger.get(section))
        rendered = []
        for item in items:
            text = _item_text(item)
            if not text:
                continue
            span = _item_span(item)
            rendered.append(f"  - {text}" + (f" [{span[0]},{span[1]}]" if span else ""))
        if rendered:
            lines.append(f"{section}:")
            lines.extend(rendered)
    return "\n".join(lines) if lines else "(no evidence extracted for this scene)"


def rendered_bundle(manuscript_handles: Mapping[str, SceneEvidence]) -> dict[str, str]:
    """The `{M#: rendered-digest}` map handed to the author prompt (keeps `author.py` decoupled from
    `SceneEvidence`)."""
    return {handle: render_ledger(se.ledger) for handle, se in manuscript_handles.items()}


def evidence_query(evidence: Sequence[SceneEvidence]) -> str:
    """A broad canon-retrieval query built from the evidence (there is no outline). Concatenates each
    scene's setting + asserted-fact / event text so the author is shown canon relevant to what the
    manuscript actually asserts. Bounded; empty when the ledgers carry nothing retrievable."""
    parts: list[str] = []
    for se in sorted(evidence, key=lambda s: s.scene_no):
        ledger = se.ledger
        setting = ledger.get("setting")
        if isinstance(setting, str) and setting.strip():
            parts.append(setting.strip())
        for section in ("asserted_facts", "events", "entities"):
            for item in _as_items(ledger.get(section)):
                text = _item_text(item)
                if text:
                    parts.append(text)
    query = " ".join(parts)
    return query[:4000]


def candidate_conflicts(manuscript_handles: Mapping[str, SceneEvidence]) -> list[ManuscriptClaim]:
    """The manuscript-vs-locked-canon conflict CANDIDATES to hand Lane A3's re-anchoring detector.

    GATED by `claim_precedence`: only build candidates for a source pair the precedence order cannot
    auto-resolve (`conflict_needs_open_question` is true for `DERIVED_FROM_MANUSCRIPT × LOCKED_CANON`).
    That keeps the routing policy in one place (ADR 0029), not hardcoded here — if the order ever
    started auto-resolving this pair, no candidates (and so no open questions) would be produced.

    Each candidate is one `canon_conflicts` ledger hint: the manuscript side is anchored to the scene's
    immutable identity + the hint's span; the canon side is left un-fingerprinted for A3 to re-anchor
    against LIVE locked canon. A hint with no usable span still becomes a candidate — A3 fails it closed
    (an unanchored span), which is an approval block, never a dropped conflict.
    """
    if not claim_precedence.conflict_needs_open_question(ClaimSource.DERIVED_FROM_MANUSCRIPT, ClaimSource.LOCKED_CANON):
        return []
    candidates: list[ManuscriptClaim] = []
    for handle, se in manuscript_handles.items():
        for hint in _as_items(se.ledger.get(_CONFLICT_SECTION)):
            assertion = _item_text(hint)
            if not assertion:
                continue
            candidates.append(
                ManuscriptClaim(
                    handle=handle,
                    scene_id=str(se.scene_id),
                    scene_version=se.scene_version,
                    prose_hash=se.prose_hash,
                    span=_item_span(hint),
                    assertion=assertion,
                    snapshot_prose_len=se.snapshot_prose_len,
                )
            )
    return candidates


def fail_closed_question(handle: str, scene_id: str, reason: str, detail: str) -> str:
    """A plain, approval-blocking open-question string for a conflict candidate that could NOT be
    re-anchored (Lane A3 `FailClosedConflict`). It is NOT an encoded `manuscript_canon_conflict` (there
    is no canon fingerprint to encode), but ANY open-question item blocks ChapterPacket approval — so a
    non-re-anchorable conflict is surfaced to the human rather than proceeding as if there were none."""
    return (
        f"Unresolved manuscript-vs-canon conflict ({reason}) for imported evidence {handle} "
        f"(scene {scene_id}): {detail}. Re-adopt after fixing the evidence anchor or resolve manually."
    )


def resolve_evidence_provenance(
    packet: dict[str, Any],
    *,
    canon_handles: Mapping[str, Mapping[str, Any]],
    manuscript_handles: Mapping[str, SceneEvidence],
) -> None:
    """Resolve each authored claim's source handle to real provenance, in place (evidence path).

    Mirrors the outline path's `_resolve_provenance` and ADDS the `M#` case:
      * `C#` → the live canon row's id + title + excerpt;
      * `M#` → the imported scene's immutable identity (id + `scene N` label; no excerpt — the evidence
        span, not a canon body, is the anchor) with the `M#` handle retained for audit;
      * `OUTLINE` → labelled (harmless here; the evidence author is not given an outline);
      * anything else (inference / unresolved / unknown) → nulled, exactly as the outline path does.
    """
    for claim in packet.get("claims", []):
        if not isinstance(claim, dict):
            continue
        handle = str(claim.get("source_id") or "").strip()
        canon = canon_handles.get(handle)
        scene = manuscript_handles.get(handle)
        if canon is not None:
            body = str(canon.get("body") or "")
            claim["source_id"] = str(canon.get("id"))
            claim["source_title_or_file"] = canon.get("name")
            claim["excerpt"] = body[:_EXCERPT_CHARS]
        elif scene is not None:
            claim["source_handle"] = handle  # keep the M# label so the manuscript span stays traceable
            claim["source_id"] = str(scene.scene_id)
            claim["source_title_or_file"] = f"imported scene {scene.scene_no}"
            claim["excerpt"] = None
        elif handle.upper() == "OUTLINE":
            claim["source_id"] = "OUTLINE"
            claim["source_title_or_file"] = "chapter outline"
            claim["excerpt"] = None
        else:
            claim["source_id"] = None
            claim["source_title_or_file"] = None
            claim["excerpt"] = None


def _claim_source(claim: Any) -> ClaimSource | None:
    if not isinstance(claim, Mapping):
        return None
    try:
        return ClaimSource(str(claim.get("source_strength") or "").strip().lower())
    except ValueError:
        return None


def precedence_adjudication(claims: Iterable[Any]) -> dict[str, Any]:
    """A persisted audit of the asserted-fact precedence the packet was authored under (ADR 0029).

    Uses `claim_precedence.rank`/`outranks` (not a local re-implementation) to record, per known source
    strength, its precedence rank and how many claims carry it — strongest first — plus the single
    `strongest_source` present. Advisory provenance for a reviewer/agent; it never gates anything. An
    equal-strength or manuscript×canon disagreement is adjudicated as an open question elsewhere (the
    detector), not here — this is the ranking view, not the conflict resolver.
    """
    counts: dict[ClaimSource, int] = {}
    for claim in claims:
        source = _claim_source(claim)
        if source is not None:
            counts[source] = counts.get(source, 0) + 1
    ordered = sorted(counts, key=claim_precedence.rank)
    strongest = ordered[0] if ordered else None
    return {
        "policy": "claim_precedence",
        "order": [s.value for s in _PRECEDENCE_ORDER],
        "strongest_source": strongest.value if strongest is not None else None,
        "by_source": [{"source": s.value, "rank": claim_precedence.rank(s), "claims": counts[s]} for s in ordered],
    }
