"""The ImportSceneEvidence extraction seam (ADR 0028).

A narrow, fakeable adapter that turns one imported scene snapshot into a span-anchored fact ledger.
The adoption worker owns snapshots, leases, checkpoints, and persistence; the extractor NEVER touches
the database — it is pure `source -> ValidatedEvidence`. The real adapter (LlmImportEvidenceExtractor)
reuses the established LLM transport (llm.complete + attempt_with_escalation + JSON extraction +
telemetry stage "import_scene_evidence") under its own `import_evidence_model` role; the deterministic
fake lets CI prove checkpoint/resume, retry, stale invalidation, and chunk+merge without a provider.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

# Bump to invalidate reuse of every previously-extracted shard when the ledger schema or prompt changes.
# It is part of the ImportSceneEvidence identity key (scene_id, scene_version, prose_hash, this).
EVIDENCE_SCHEMA_VERSION = "1"

# The span-anchored ledger sections. Every extracted item points back to a [start, end) span of the
# immutable snapshot prose (an omission anchors to the nearest relevant span). Kept as a flat contract
# so the ChapterPacket author reads a stable M# bundle regardless of extractor internals.
LEDGER_SECTIONS: tuple[str, ...] = (
    "entities",  # present/referenced characters, with role
    "pov",  # narrating viewpoint
    "setting",  # place/time
    "events",  # what happens, ordered
    "asserted_facts",  # claims the prose states as true
    "state_changes",  # relationship / inventory / status deltas
    "reveals",  # reader-visible reveals
    "withholds",  # things deliberately still withheld
    "entry_state",  # scene entry state
    "exit_state",  # scene exit state
    "continuity_anchors",  # anchors later scenes must respect
    "ambiguities",  # unresolved/ambiguous points
    "canon_conflicts",  # apparent conflicts with locked canon (feed ADR 0029 open questions)
)


class EvidenceExtractionError(Exception):
    """The extractor could not produce a valid ledger (bad/unparseable model output, exhausted retries,
    or span validation failure). The adoption worker treats this as a per-scene failure and retries the
    shard under its lease policy; it never partially commits an invalid shard."""


@dataclass(frozen=True)
class SceneSource:
    """The immutable snapshot of one scene handed to the extractor. No DB handles — just the identity
    the shard is keyed by plus the exact prose and light chapter context for the prompt."""

    scene_id: uuid.UUID
    scene_version: int
    prose_hash: str  # sha256 of `prose`; the extractor asserts it matches before trusting the snapshot
    chapter_id: uuid.UUID
    scene_no: int
    prose: str
    pov: str | None = None
    book_id: uuid.UUID | None = None  # for telemetry tagging only
    # Optional prior-scene tail / chapter framing the prompt may use; never the whole chapter.
    context_note: str | None = None


@dataclass
class ExtractionBudget:
    """Per-scene extraction budget. `max_chars_per_chunk` triggers deterministic chunk+merge for an
    oversized scene (never raw-text truncation); `max_tokens` bounds a single model call."""

    max_tokens: int = 4000
    max_chars_per_chunk: int = 24000


@dataclass
class ValidatedEvidence:
    """The extractor's validated output for one scene. `ledger` is the span-anchored fact ledger;
    `merged_shard_ids` records the per-chunk shards a bounded merge combined (empty for a single-pass
    extraction). `schema_version` echoes the identity key component. No DB rows here — the worker
    persists this as an ImportSceneEvidence row."""

    ledger: dict[str, Any]
    schema_version: str = EVIDENCE_SCHEMA_VERSION
    # For an oversized scene: the per-chunk sub-ledgers the bounded merge combined. The WORKER persists
    # each as a retained shard and records their DB ids on `merged_shard_ids` of the merged row (the
    # extractor never touches the DB, so it cannot know ids). Empty for a single-pass extraction.
    chunk_ledgers: list[dict[str, Any]] = field(default_factory=list)
    merged_shard_ids: list[str] = field(default_factory=list)  # worker-filled after persisting chunk shards
    token_usage: int | None = None


def validate_ledger(ledger: dict[str, Any], prose_len: int) -> dict[str, Any]:
    """Structural + span validation shared by the real adapter and the fake.

    - every LEDGER_SECTION key is present (missing → filled with an empty list / None, so the author
      sees an explicit "nothing here" rather than a KeyError);
    - any `span` on an item is a 2-int [start, end] within [0, prose_len]; out-of-range spans raise,
      because a fabricated anchor breaks the "traceable to the snapshot" guarantee.

    Returns the normalized ledger. Raises EvidenceExtractionError on an unfixable violation.
    """
    if not isinstance(ledger, dict):
        raise EvidenceExtractionError("ledger is not an object")
    normalized: dict[str, Any] = {}
    for section in LEDGER_SECTIONS:
        value = ledger.get(section)
        if section in ("pov", "setting", "entry_state", "exit_state"):
            normalized[section] = value if isinstance(value, str) or value is None else str(value)
            continue
        items = value if isinstance(value, list) else ([] if value is None else [value])
        for item in items:
            if isinstance(item, dict) and "span" in item and item["span"] is not None:
                span = item["span"]
                if (
                    not isinstance(span, (list, tuple))
                    or len(span) != 2
                    or not all(isinstance(n, int) for n in span)
                    or not (0 <= span[0] <= span[1] <= prose_len)
                ):
                    raise EvidenceExtractionError(f"{section} item has an out-of-range span {span!r}")
        normalized[section] = items
    return normalized


class ImportEvidenceExtractor(Protocol):
    """`extract_scene(source, budget) -> ValidatedEvidence`. Hides prompt construction, structured
    parsing, span validation, and oversized-scene chunk+merge. Implementations must never touch the DB."""

    async def extract_scene(self, source: SceneSource, budget: ExtractionBudget) -> ValidatedEvidence: ...


class FakeImportEvidenceExtractor:
    """Deterministic, scripted extractor for tests — no provider calls. Script a ledger per source
    identity (by scene_id or by scene_no); optionally script transient failures to prove the worker's
    retry/resume, and a forced chunk count to prove chunk+merge bookkeeping.

    Determinism: identical (scene_id/scene_no) always yields the identical ledger, so a re-adoption of
    unchanged prose is a no-op and a changed prose_hash (new SceneSource) can be scripted separately.
    """

    def __init__(
        self,
        *,
        by_scene_id: dict[uuid.UUID, dict[str, Any]] | None = None,
        by_scene_no: dict[int, dict[str, Any]] | None = None,
        fail_times: dict[uuid.UUID, int] | None = None,
        chunk_ledgers: dict[uuid.UUID, list[dict[str, Any]]] | None = None,
    ) -> None:
        self._by_scene_id = by_scene_id or {}
        self._by_scene_no = by_scene_no or {}
        self._fail_remaining = dict(fail_times or {})
        self._chunk_ledgers = chunk_ledgers or {}
        self.calls: list[uuid.UUID] = []

    async def extract_scene(self, source: SceneSource, budget: ExtractionBudget) -> ValidatedEvidence:
        self.calls.append(source.scene_id)
        remaining = self._fail_remaining.get(source.scene_id, 0)
        if remaining > 0:
            self._fail_remaining[source.scene_id] = remaining - 1
            raise EvidenceExtractionError(f"scripted transient failure for scene {source.scene_id}")
        ledger = self._by_scene_id.get(source.scene_id) or self._by_scene_no.get(source.scene_no)
        if ledger is None:
            ledger = {"events": [{"summary": f"scene {source.scene_no}", "span": [0, min(len(source.prose), 1)]}]}
        normalized = validate_ledger(dict(ledger), len(source.prose))
        return ValidatedEvidence(
            ledger=normalized,
            chunk_ledgers=[validate_ledger(dict(c), len(source.prose)) for c in self._chunk_ledgers.get(source.scene_id, [])],
            token_usage=0,
        )


def _deterministic_chunks(prose: str, max_chars: int) -> list[tuple[int, str]]:
    """Split oversized prose into (char_offset, text) chunks at paragraph boundaries where possible,
    deterministically (no overlap, no truncation). Every character lands in exactly one chunk, so the
    union merge reconstructs the whole scene's evidence."""
    if len(prose) <= max_chars:
        return [(0, prose)]
    chunks: list[tuple[int, str]] = []
    start = 0
    n = len(prose)
    while start < n:
        end = min(start + max_chars, n)
        if end < n:
            # Prefer a paragraph break, then a line break, then a space, within the window.
            for sep in ("\n\n", "\n", " "):
                cut = prose.rfind(sep, start + max_chars // 2, end)
                if cut != -1:
                    end = cut + len(sep)
                    break
        chunks.append((start, prose[start:end]))
        start = end
    return chunks


def _merge_chunk_ledgers(chunk_ledgers: list[tuple[int, dict[str, Any]]], prose_len: int) -> dict[str, Any]:
    """Bounded, deterministic union of per-chunk ledgers. List sections concatenate with spans shifted
    back into whole-scene coordinates; scalar sections take entry_state from the first chunk, exit_state
    from the last, and the first non-empty pov/setting. Never a raw-text merge."""
    merged: dict[str, Any] = {}
    for section in LEDGER_SECTIONS:
        if section in ("pov", "setting", "entry_state", "exit_state"):
            merged[section] = None
        else:
            merged[section] = []
    for idx, (offset, ledger) in enumerate(chunk_ledgers):
        for section in LEDGER_SECTIONS:
            value = ledger.get(section)
            if section in ("pov", "setting"):
                if merged[section] is None and value:
                    merged[section] = value
            elif section == "entry_state":
                if idx == 0:
                    merged[section] = value
            elif section == "exit_state":
                if idx == len(chunk_ledgers) - 1:
                    merged[section] = value
            else:
                for item in value if isinstance(value, list) else []:
                    if isinstance(item, dict) and isinstance(item.get("span"), (list, tuple)) and len(item["span"]) == 2:
                        item = {**item, "span": [item["span"][0] + offset, item["span"][1] + offset]}
                    merged[section].append(item)
    return validate_ledger(merged, prose_len)


class LlmImportEvidenceExtractor:
    """Production extractor over the established LLM transport (llm.complete + attempt_with_escalation +
    extract_object), under the `import_evidence_model` role and telemetry stage `import_scene_evidence`.
    Mirrors scene_packet/author.py's single-object pattern. Never touches the DB.

    An oversized scene is split into deterministic chunks; each chunk is extracted independently and the
    shard ledgers are combined by a bounded deterministic union (spans shifted to whole-scene
    coordinates). The per-chunk ledgers are returned on `chunk_ledgers` for the worker to retain.
    """

    async def extract_scene(self, source: SceneSource, budget: ExtractionBudget) -> ValidatedEvidence:
        chunks = _deterministic_chunks(source.prose, budget.max_chars_per_chunk)
        if len(chunks) == 1:
            ledger, usage = await self._extract_one(source, source.prose, budget)
            return ValidatedEvidence(ledger=validate_ledger(ledger, len(source.prose)), token_usage=usage)

        chunk_ledgers: list[tuple[int, dict[str, Any]]] = []
        total_usage = 0
        for offset, text in chunks:
            ledger, usage = await self._extract_one(source, text, budget)
            chunk_ledgers.append((offset, validate_ledger(ledger, len(text))))
            total_usage += usage or 0
        merged = _merge_chunk_ledgers(chunk_ledgers, len(source.prose))
        return ValidatedEvidence(
            ledger=merged,
            chunk_ledgers=[cl for _off, cl in chunk_ledgers],
            token_usage=total_usage,
        )

    async def _extract_one(self, source: SceneSource, text: str, budget: ExtractionBudget) -> tuple[dict[str, Any], int]:
        # Local imports keep the module importable (and the fake usable) without the heavy LLM stack.
        # Telemetry (stage "import_scene_evidence") is established by the CALLER via telemetry.call_context
        # around this extractor, exactly as scene_packet/derive.py wraps its author call — llm.complete's
        # internal telemetry.record() then tags every call/chunk with that stage and sink.
        from dominion.shared.config import settings
        from dominion.workers import llm
        from dominion.workers.budget import TokenBudget, Usage
        from dominion.workers.llm_escalation import attempt_with_escalation, policy_for_setting
        from dominion.workers.scene_packet.parse import extract_object

        token_budget = TokenBudget(max_tokens=budget.max_tokens)
        user = _build_extract_prompt(source, text)

        async def _attempt(model: str, max_tokens: int) -> tuple[Any, Usage]:
            raw, usage = await llm.complete(
                model=model,
                system=_EXTRACT_SYSTEM,
                user=user,
                max_tokens=max_tokens,
                budget=token_budget,
                input_budget=settings.import_evidence_prompt_budget,
                setting_key="import_evidence_model",
            )
            return extract_object(raw), usage

        body, _model_used, _escalated = await attempt_with_escalation(
            setting_key="import_evidence_model",
            primary_model=settings.import_evidence_model,
            primary_max_tokens=settings.import_evidence_max_tokens,
            attempt_fn=_attempt,
            is_success=lambda b: isinstance(b, dict),
            policy=policy_for_setting("import_evidence_model"),
        )
        if not isinstance(body, dict):
            raise EvidenceExtractionError(f"import evidence extraction returned no JSON object for scene {source.scene_no}")
        return body, 0


_EXTRACT_SYSTEM = (
    "You extract a span-anchored FACT LEDGER from one scene of an existing manuscript. The prose is "
    "evidence, not canon: report only what THIS scene's text supports, never invent. Every list item "
    "must carry a 2-int character span [start, end) into the provided prose. Reply with ONE JSON object "
    "only — no prose, no code fences — with keys: " + ", ".join(LEDGER_SECTIONS) + "."
)


def _build_extract_prompt(source: SceneSource, text: str) -> str:
    header = f"Scene {source.scene_no}"
    if source.pov:
        header += f" (POV: {source.pov})"
    note = f"\nContext: {source.context_note}" if source.context_note else ""
    return f"{header}{note}\n\nPROSE (char offsets are 0-based into this exact text):\n{text}"

