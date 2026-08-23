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


# Marker key written onto a ledger item whose character span failed validation. The item keeps its
# content and loses its (fabricated) anchor; this records why, so a quarantined fact is auditable
# rather than either silently dropped or silently trusted. Deliberately NOT a LEDGER_SECTION — it
# lives on the ITEM, so the ledger's top-level shape is unchanged for every existing consumer.
SPAN_QUARANTINE_KEY = "span_quarantined"


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


def _settings() -> Any:
    """Lazy settings accessor. The module deliberately avoids a top-level config/LLM import so it stays
    importable (and FakeImportEvidenceExtractor usable) without the heavy stack — but the envelope below
    must still come from configuration, not from literals duplicated here. Reading it in a
    `default_factory` gets both: a light module import, and one operator-tunable source of truth."""
    from dominion.shared.config import settings

    return settings


def default_work_token_budget() -> int:
    """The per-call WORK ceiling (weighted input + output) for one extraction call.

    Derived, never a literal. `TokenBudget` charges input AND output against one ceiling and raises
    AFTER a successful, already-paid provider call, so a ceiling below what the chunker guarantees to
    send is not a budget — it is a guaranteed loss. The previous literal 4000 sat below the ~6000 input
    tokens a full 24k-char chunk produced, so every scene over ~14k chars paid for its call and then
    died on BudgetExceeded (which is not an EvidenceExtractionError, so it escaped this seam's declared
    error contract entirely). Deriving it from the same input gate and output allowance the call actually
    uses makes that arithmetically impossible while keeping the ceiling genuinely binding: a call that
    burns materially more than its estimated input still trips it.
    """
    s = _settings()
    return s.import_evidence_prompt_budget + s.import_evidence_max_tokens


@dataclass
class ExtractionBudget:
    """Per-scene extraction size envelope, defaulted from configuration (see `Settings`'s
    import-evidence block, whose validator enforces that these values cohere).

    - `max_chars_per_chunk` triggers deterministic chunk+merge for an oversized scene (never raw-text
      truncation) and is the one knob that sets per-call input cost;
    - `max_tokens` is the per-call WORK ceiling charged by `TokenBudget` (input + output);
    - `max_scene_chars` is the HARD ceiling refused before any provider traffic;
    - `max_chunks` caps the fan-out if the chunker itself misbehaves;
    - `max_quarantine_ratio` is how much span quarantine a ledger may carry before the extraction is
      treated as a failure rather than a partially-anchored success.
    """

    max_tokens: int = field(default_factory=default_work_token_budget)
    max_chars_per_chunk: int = field(default_factory=lambda: _settings().import_evidence_max_chars_per_chunk)
    max_chunks: int = field(default_factory=lambda: _settings().import_evidence_max_chunks)
    max_scene_chars: int = field(default_factory=lambda: _settings().import_evidence_max_scene_chars)
    max_quarantine_ratio: float = field(default_factory=lambda: _settings().import_evidence_max_quarantine_ratio)


@dataclass(frozen=True)
class EvidenceChunk:
    """One retained chunk of an oversized-scene extraction (R2). A COHESIVE value — its chunk-LOCAL
    span-anchored `ledger` plus the [char_offset, char_end) window it covers in whole-scene coordinates
    — never parallel arrays. The worker persists each as an ImportSceneEvidenceChunk row keyed by
    `chunk_index`; the parent's merged ledger is the offset-shifted union of these."""

    chunk_index: int
    char_offset: int
    char_end: int
    ledger: dict[str, Any]


@dataclass
class ValidatedEvidence:
    """The extractor's validated output for one scene. `ledger` is the merged, whole-scene span-anchored
    fact ledger; `chunks` carries the retained per-chunk shards of an oversized extraction (empty for a
    single-pass one). No DB rows and no DB ids here — the worker persists this as an ImportSceneEvidence
    parent plus one ImportSceneEvidenceChunk child per `chunks` entry, and derives the parent's
    `merged_shard_ids` from the children it wrote."""

    ledger: dict[str, Any]
    schema_version: str = EVIDENCE_SCHEMA_VERSION
    chunks: list[EvidenceChunk] = field(default_factory=list)
    token_usage: int | None = None


def _span_violation(span: Any, prose_len: int) -> str | None:
    """Why `span` is not a usable anchor into prose of `prose_len` chars, or None if it is fine.

    Returned as a human-readable reason rather than a bool so a quarantined item carries an explanation
    the author (and an operator reading the persisted ledger) can act on. `bool` is excluded explicitly
    because it is an `int` subclass in Python, and `[True, False]` is not an anchor.
    """
    if not isinstance(span, (list, tuple)):
        return f"span is {type(span).__name__}, expected a 2-element list"
    if len(span) != 2:
        return f"span has {len(span)} elements, expected 2"
    if not all(isinstance(n, int) and not isinstance(n, bool) for n in span):
        return "span members are not both integers"
    start, end = span
    if not (0 <= start <= end <= prose_len):
        return f"span [{start}, {end}] is not within [0, {prose_len}]"
    return None


def quarantined_span_count(ledger: dict[str, Any]) -> int:
    """How many items in a normalized ledger carry a quarantined span. Instrumentation seam: an import
    whose quarantine count is climbing is a prompt or model regression, and that is only visible if
    something counts it."""
    total = 0
    for section in LEDGER_SECTIONS:
        for item in ledger.get(section) or []:
            if isinstance(item, dict) and item.get(SPAN_QUARANTINE_KEY) is not None:
                total += 1
    return total


def validate_ledger(
    ledger: dict[str, Any],
    prose_len: int,
    *,
    max_quarantine_ratio: float | None = None,
) -> dict[str, Any]:
    """Structural + span validation shared by the real adapter and the fake.

    - every LEDGER_SECTION key is present (missing → filled with an empty list / None, so the author
      sees an explicit "nothing here" rather than a KeyError);
    - any `span` on an item must be a 2-int [start, end] within [0, prose_len].

    A bad span is QUARANTINED, not fatal. One fabricated anchor used to raise and kill the whole scene's
    extraction — discarding a dozen good facts, then re-running the identical call under the worker's
    retry policy to get the identical result. So the offending item is KEPT with its content intact, its
    fabricated `span` replaced by None, and a `span_quarantined` marker recording the reason and the
    rejected span. Nothing is silently dropped, and nothing untraceable is silently trusted.

    The quarantine is BOUNDED, because "quarantine everything" is its own failure mode: past
    `max_quarantine_ratio` of the span-bearing items (default from
    `settings.import_evidence_max_quarantine_ratio`) the model has not anchored its evidence at all, and
    that must fail closed so retry/escalation runs instead of persisting an unanchored ledger.

    Deterministic: sections are walked in `LEDGER_SECTIONS` order, items keep their input order, and
    quarantining is idempotent — re-validating an already-quarantined ledger (which the chunk merge
    does) counts the same items and never resurrects a rejected span.

    Returns the normalized ledger. Raises EvidenceExtractionError on a non-object ledger, or when the
    quarantine ratio is over the limit.
    """
    if not isinstance(ledger, dict):
        raise EvidenceExtractionError("ledger is not an object")
    normalized: dict[str, Any] = {}
    span_bearing = 0
    quarantined = 0
    for section in LEDGER_SECTIONS:
        value = ledger.get(section)
        if section in ("pov", "setting", "entry_state", "exit_state"):
            normalized[section] = value if isinstance(value, str) or value is None else str(value)
            continue
        items = value if isinstance(value, list) else ([] if value is None else [value])
        kept: list[Any] = []
        for item in items:
            if not isinstance(item, dict):
                kept.append(item)
                continue
            if item.get(SPAN_QUARANTINE_KEY) is not None:
                # Already quarantined by an upstream pass (the per-chunk validation before a merge).
                # Count it toward the ratio, but never re-derive it and never resurrect the rejected span.
                span_bearing += 1
                quarantined += 1
                kept.append(item)
                continue
            span = item.get("span")
            if "span" not in item or span is None:
                kept.append(item)  # an anchorless item is a legitimate shape, not a violation
                continue
            span_bearing += 1
            reason = _span_violation(span, prose_len)
            if reason is None:
                kept.append(item)
                continue
            quarantined += 1
            kept.append(
                {
                    **item,
                    "span": None,
                    SPAN_QUARANTINE_KEY: {
                        "section": section,
                        "reason": reason,
                        "rejected_span": list(span) if isinstance(span, (list, tuple)) else span,
                    },
                }
            )
        normalized[section] = kept
    if quarantined:
        limit = (
            max_quarantine_ratio
            if max_quarantine_ratio is not None
            else _settings().import_evidence_max_quarantine_ratio
        )
        ratio = quarantined / span_bearing
        if ratio > limit:
            raise EvidenceExtractionError(
                f"span quarantine ratio {quarantined}/{span_bearing} = {ratio:.2f} exceeds the "
                f"{limit:.2f} limit — the extraction did not anchor its evidence to the prose"
            )
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
        # Checked before `calls` is recorded: an over-ceiling scene is refused, not attempted, and the
        # fake must model that or a test could not tell a refusal from a silent success.
        assert_scene_within_ceiling(source, budget)
        self.calls.append(source.scene_id)
        remaining = self._fail_remaining.get(source.scene_id, 0)
        if remaining > 0:
            self._fail_remaining[source.scene_id] = remaining - 1
            raise EvidenceExtractionError(f"scripted transient failure for scene {source.scene_id}")
        ledger = self._by_scene_id.get(source.scene_id) or self._by_scene_no.get(source.scene_no)
        if ledger is None:
            ledger = {"events": [{"summary": f"scene {source.scene_no}", "span": [0, min(len(source.prose), 1)]}]}
        normalized = validate_ledger(dict(ledger), len(source.prose), max_quarantine_ratio=budget.max_quarantine_ratio)
        # Scripted chunks get deterministic, monotonic, non-overlapping synthetic windows so the persist
        # oracle can assert interval integrity + order without a real chunker run (that is covered by
        # _deterministic_chunks' own tests). Stride 1000, half-width → gaps, never overlaps.
        scripted = self._chunk_ledgers.get(source.scene_id, [])
        chunks = [
            EvidenceChunk(
                chunk_index=i,
                char_offset=i * 1000,
                char_end=i * 1000 + 500,
                ledger=validate_ledger(dict(c), len(source.prose), max_quarantine_ratio=budget.max_quarantine_ratio),
            )
            for i, c in enumerate(scripted)
        ]
        return ValidatedEvidence(ledger=normalized, chunks=chunks, token_usage=0)


def assert_scene_within_ceiling(source: SceneSource, budget: ExtractionBudget) -> None:
    """Refuse an over-ceiling scene BEFORE any provider traffic.

    A snapshot past `max_scene_chars` is not a scene — it is a chapter or a whole manuscript pasted into
    one row. Extracting it would fan out into a long series of paid calls and produce a merged ledger
    with tens of thousands of items that no author can read, and the failure would only surface after the
    money was spent. So the size check happens here, first, with a message that names the concrete next
    human action (re-split the import). Honoured by BOTH adapters, so a test using the deterministic fake
    proves the same guarantee the production adapter gives.
    """
    prose_chars = len(source.prose)
    if prose_chars > budget.max_scene_chars:
        raise EvidenceExtractionError(
            f"scene {source.scene_no} prose is {prose_chars} chars, over the "
            f"{budget.max_scene_chars}-char hard ceiling for a single scene — re-split the import so "
            "each snapshot is one scene. No provider call was made."
        )


def _deterministic_chunks(prose: str, max_chars: int, *, max_chunks: int | None = None) -> list[tuple[int, str]]:
    """Split oversized prose into (char_offset, text) chunks at paragraph boundaries where possible,
    deterministically (no overlap, no truncation). Every character lands in exactly one chunk, so the
    union merge reconstructs the whole scene's evidence.

    `max_chunks` caps the fan-out. Boundary-seeking can cut as early as `max_chars // 2`, so the chunk
    count is not simply `ceil(len / max_chars)` — it can be up to twice that. The cap is therefore
    checked against chunks as they are produced (stopping the walk immediately) rather than derived from
    the length up front. `assert_scene_within_ceiling` is the guard expected to fire in practice; this
    one exists so that a bug in the boundary-seeking above can never become unbounded provider spend.
    """
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
        if max_chunks is not None and len(chunks) > max_chunks:
            raise EvidenceExtractionError(
                f"chunking {n} chars at {max_chars} chars/chunk exceeded the {max_chunks}-chunk cap — "
                "refusing to fan out further. Lower the scene size or raise "
                "DOMINION_IMPORT_EVIDENCE_MAX_CHUNKS."
            )
        start = end
    return chunks


def _merge_chunk_ledgers(
    chunk_ledgers: list[tuple[int, dict[str, Any]]],
    prose_len: int,
    *,
    max_quarantine_ratio: float | None = None,
) -> dict[str, Any]:
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
                    if (
                        isinstance(item, dict)
                        and isinstance(item.get("span"), (list, tuple))
                        and len(item["span"]) == 2
                    ):
                        item = {**item, "span": [item["span"][0] + offset, item["span"][1] + offset]}
                    merged[section].append(item)
    # Re-validated in WHOLE-SCENE coordinates. Items quarantined per chunk arrive with span=None and
    # their marker intact, so they pass through the shift untouched and are counted once more here —
    # the ratio is therefore judged against the whole scene, not chunk by chunk.
    return validate_ledger(merged, prose_len, max_quarantine_ratio=max_quarantine_ratio)


class LlmImportEvidenceExtractor:
    """Production extractor over the established LLM transport (llm.complete + attempt_with_escalation +
    extract_object), under the `import_evidence_model` role and telemetry stage `import_scene_evidence`.
    Mirrors scene_packet/author.py's single-object pattern. Never touches the DB.

    An oversized scene is split into deterministic chunks; each chunk is extracted independently and the
    shard ledgers are combined by a bounded deterministic union (spans shifted to whole-scene
    coordinates). The per-chunk ledgers are returned on `chunk_ledgers` for the worker to retain.
    """

    async def extract_scene(self, source: SceneSource, budget: ExtractionBudget) -> ValidatedEvidence:
        assert_scene_within_ceiling(source, budget)
        chunks = _deterministic_chunks(source.prose, budget.max_chars_per_chunk, max_chunks=budget.max_chunks)
        if len(chunks) == 1:
            ledger, usage = await self._extract_one(source, source.prose, budget)
            return ValidatedEvidence(
                ledger=validate_ledger(ledger, len(source.prose), max_quarantine_ratio=budget.max_quarantine_ratio),
                token_usage=usage,
            )

        evidence_chunks: list[EvidenceChunk] = []
        total_usage = 0
        for i, (offset, text) in enumerate(chunks):
            ledger, usage = await self._extract_one(source, text, budget)
            evidence_chunks.append(
                EvidenceChunk(
                    chunk_index=i,
                    char_offset=offset,
                    char_end=offset + len(text),
                    ledger=validate_ledger(ledger, len(text), max_quarantine_ratio=budget.max_quarantine_ratio),
                )
            )
            total_usage += usage or 0
        merged = _merge_chunk_ledgers(
            [(c.char_offset, c.ledger) for c in evidence_chunks],
            len(source.prose),
            max_quarantine_ratio=budget.max_quarantine_ratio,
        )
        return ValidatedEvidence(ledger=merged, chunks=evidence_chunks, token_usage=total_usage)

    async def _extract_one(
        self, source: SceneSource, text: str, budget: ExtractionBudget
    ) -> tuple[dict[str, Any], int]:
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
            raise EvidenceExtractionError(
                f"import evidence extraction returned no JSON object for scene {source.scene_no}"
            )
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
