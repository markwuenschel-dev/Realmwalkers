"""Audit author-written prose against the style rules — the Edit desk's endpoint.

Sibling to `enrich.py`, and the same stateless shape for the same reason: the audit takes a plain
string, so none of the contract-first machinery applies, and it EDITS no prose. The difference is
direction. Enrich hands back transformed prose and the author reads the result; this hands back
suggestions the author accepts or rejects one at a time, and the text on the page is never changed by
anything but a human.

It takes a session for two purposes. The first is reading the style documents: they live in
`style_documents` because `series/` never reaches the deploy box, and an audit without its standards
is not a weaker audit but a different tool entirely — a general writing coach, which is precisely what
the author's rules exist to not be. `standards_loaded` reports which ones arrived, so a degraded run
is legible instead of silent.

The second is telemetry, and it is why this module owns an orchestration concern the audit does not.
Every audit is a real, paid model call, and spend that no surface can see is spend nobody governs.
`audit_prose` owns audit policy and budgeting; this route owns the request-scoped sink, its
persistence, its commit, and how a persistence failure is presented. The split follows the deep-module
boundary: the worker decides what to judge, the HTTP orchestrator decides what the request leaves
behind.
"""

from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from dominion.api.deps import SessionDep
from dominion.workers import telemetry, telemetry_db
from dominion.workers.budget import BudgetExceeded
from dominion.workers.reviewers.style_audit import audit_prose

log = structlog.get_logger()

router = APIRouter(tags=["style-review"])

# The telemetry stage this endpoint stamps on every call it makes. It matches the `stages` tuple on
# the `style_audit_model` agent, which is what `STAGE_TO_SETTING` reads to attribute the row to the
# Style audit agent in Agent Operations (`agent_ops.build_agent_stats`). Deliberately NOT added to
# `PIPELINE_STAGE_ORDER`: that orders the scene pipeline's stages, and this is a stateless operation
# that is not part of it — the telemetry aggregate already carries unknown stages as extras.
_STAGE = "style_audit"


class StyleReviewIn(BaseModel):
    prose: str = Field(min_length=1)
    # Optional, exactly as in `enrich`: a prologue or omniscient interlude has no POV character, and
    # naming a false one skews which drift patterns load.
    pov: str | None = None


class StyleSuggestionOut(BaseModel):
    """One rule-cited suggestion, shaped as the `Suggestion` row the Desk already knows how to render.

    Named `StyleSuggestionOut`, not `SuggestionOut`: `shared/schemas.py` already exports a
    `SuggestionOut` for the human markup API, and two Pydantic classes sharing a name make FastAPI
    fully-qualify BOTH in the OpenAPI components. That silently renames the existing schema, and
    `types.ts`'s `S["SuggestionOut"]` stops resolving — a frontend build failure whose cause is
    nowhere near the file that caused it.

    `quote` anchors by substring — matching `prose.ts` `tokenize`, which finds markers with `indexOf`
    rather than character offsets. Returning offsets instead would need new client machinery for no
    gain, and would break the moment the author edited a character ahead of the span.
    """

    rule: str
    rule_source: str
    severity: str
    quote: str
    # None = diagnosis with no proposed replacement. "" = the fix is a deletion. The two are different
    # answers and the UI renders them differently, so the distinction is preserved rather than
    # collapsed into a falsy check.
    new_text: str | None
    why: str


class StyleReviewOut(BaseModel):
    suggestions: list[StyleSuggestionOut]
    standards_loaded: list[str]
    standards_missing: list[str]
    # The characters named IN the passage that activated cast-scoped drift rules. Not family tags —
    # the families themselves (GENRE, VOICE, PROSE, …) are derived downstream of this list inside
    # `scope_forbidden_drift`. Named for what it holds because the UI shows it to the author, and an
    # empty list is a real signal: prose written entirely in pronouns activates no cast-scoped rules.
    drift_scope_characters: list[str]
    # Findings whose quote was not in the prose, dropped by the deterministic evidence check. Reported
    # rather than hidden: a non-zero count is the author's signal that this run was inventing
    # evidence, which no amount of plausible-sounding findings would otherwise reveal.
    fabricated_dropped: int
    # Whether this call's cost reached `llm_calls`. False means the audit succeeded but its spend is
    # invisible to Agent Operations — an operational fault the author should see rather than a silent
    # accounting hole. Never a reason to fail the request: the money was already spent.
    telemetry_recorded: bool
    model: str
    source_chars: int
    tokens_used: int


async def _persist_telemetry(session: AsyncSession, sink: telemetry.TelemetrySink, run_id: uuid.UUID) -> bool:
    """Flush this request's captured calls to `llm_calls` and commit. True iff the rows landed.

    Never raises. This runs in a `finally`, including the path where the provider itself failed, and a
    telemetry error thrown from there would replace the original exception with a bookkeeping one —
    the author would see a database message for what was actually a model outage. So a failure here is
    rolled back on its own, logged with structure, and reported through `telemetry_recorded` instead.
    """
    if not sink.records:
        # No call was made (empty passage, or no standards loaded). Nothing to record, and an empty
        # commit would still be a lie in the return value.
        return False
    try:
        telemetry_db.persist_sink(session, sink, run_id=run_id, book_id=None)
        await session.commit()
    except Exception:
        # The session dependency does not commit, so a half-applied flush must be actively discarded
        # or the next user of this session inherits it.
        await session.rollback()
        log.exception(
            "style_review.telemetry_persist_failed",
            run_id=str(run_id),
            stage=_STAGE,
            records=len(sink.records),
            detail="the audit succeeded; its cost is not in llm_calls and will not appear in Agent Operations",
        )
        return False
    return True


@router.post("/style-review", response_model=StyleReviewOut)
async def style_review(body: StyleReviewIn, session: SessionDep) -> StyleReviewOut:
    # One sink and one run id per request. A fresh uuid4 makes each audit its own run row rather than
    # accumulating into a shared bucket, which is what lets Agent Operations show recent audits
    # separately — it keeps only the most recent distinct run ids.
    sink = telemetry.TelemetrySink()
    run_id = uuid.uuid4()
    telemetry_recorded = False

    try:
        with telemetry.call_context(telemetry.CallContext(sink=sink, stage=_STAGE)):
            result = await audit_prose(session, body.prose, pov=(body.pov or "").strip())
    except BudgetExceeded as exc:
        # The passage plus its rule documents did not fit. This is a real, reachable outcome — the
        # standards alone are ~69k characters — and it is the author's cue to audit a shorter passage,
        # so it gets its own status rather than a generic 502.
        raise HTTPException(413, f"passage too long to audit against the style rules: {exc}") from exc
    finally:
        # Deliberately in `finally`: a provider failure is still a billable call, and `llm.py` records
        # it to the sink with its error. Dropping that row would make exactly the failures worth
        # investigating the ones with no telemetry.
        telemetry_recorded = await _persist_telemetry(session, sink, run_id)

    if not result.standards_loaded:
        # No standards means no audit. Returning an empty suggestion list here would read as "your
        # prose is clean", which is the one wrong answer available.
        raise HTTPException(
            503,
            "No style documents could be loaded, so there is nothing to audit against. "
            "Push them with `python -m dominion.tools.push_style`.",
        )

    return StyleReviewOut(
        suggestions=[
            StyleSuggestionOut(
                rule=f.rule,
                rule_source=f.rule_source,
                severity=f.severity.value,
                quote=f.quote,
                new_text=f.new_text,
                why=f.why,
            )
            for f in result.findings
        ],
        standards_loaded=result.standards_loaded,
        standards_missing=result.standards_missing,
        drift_scope_characters=result.drift_scope_characters,
        fabricated_dropped=result.fabricated_dropped,
        telemetry_recorded=telemetry_recorded,
        model=result.model,
        source_chars=len(body.prose),
        tokens_used=result.tokens_used,
    )
