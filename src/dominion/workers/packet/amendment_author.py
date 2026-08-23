"""Amendment mode's copy-on-write AUTHOR pass — the model half, run strictly OUTSIDE the chapter lock
(#261, ADR-0034 W2a).

WHERE THIS SITS. `amendment.assess_chapter` decides a chapter is in the genuine no-seed state, and
`amendment.approve_amendment` performs the ONE locked approve+supersede transition. Between them sits this
pass: it authors the AMENDMENT PACKET — a new `chapter_packets` row, `status=proposed`,
`origin_mode='amendment'`, naming the approved predecessor in `supersedes_packet_id` — from the imported
evidence, and persists it BESIDE (never over) that predecessor. It is the tier-C author pass of the
adoption worker's phase 4, and it is the only writer of an amendment row before approval.

WHY COPY-ON-WRITE IS THE POINT, NOT A STYLE CHOICE. The amendment body STARTS from `approved_packet.body`
and every existing `scene_seeds[]` entry is carried over with its `seed_id` UNCHANGED. `seed_id` is the
sync key for everything already derived from the chapter:
  * `scene_packet/derive.py:484-494` builds `{ScenePacket.scene_seed_id: ScenePacket}` and matches a seed
    to its existing scene contract by one dict lookup at `:601`. A regenerated seed_id misses that lookup
    and `:740-747` INSERTS A SECOND ScenePacket for the same scene — `ScenePacket` carries no unique
    constraint on `(chapter_id, scene_seed_id)` (`shared/models.py:368-390`), so the duplicate lands with
    no IntegrityError and the chapter silently ends up with two live contracts for one scene;
  * `derive.py:591-599` resolves the producing adoption's `seed_bindings` by `str(seed_id)`, and a miss
    fails that scene CLOSED at `:673-684` with no author call at all;
  * `scene_packet/staleness.py:101-109` re-matches the same way, so a renamed seed force-stales exactly
    the packet it was supposed to keep.
A "re-author the contract from scratch" amendment would therefore orphan every already-derived scene
contract in the chapter — the precise damage amendment mode exists to avoid.

THE MERGE RULE (`_merge_amendment_body`): the approved contract WINS on everything it already asserts
(chapter job/spine/entry+exit state, cast, locks, and its own seeds); the amendment may only ADD — seeds
for the scenes the approved packet does not cover, the claims that carry provenance for those seeds, and
open questions. No human-approved lock or roster is rewritten by model output. `_surface_contract` is the
one section REBUILT rather than copied, because `derive.py:456-457` enumerates seeds through
`master.drafter_view` — i.e. out of `_surface_contract` — so a carried-over projection would hide the new
seeds and the amendment would repair nothing.

LOCK DISCIPLINE. Nothing here holds the per-chapter workflow lock across a model call
(`shared/chapter_lock.py:20-22`). The author and QA calls run unlocked; the single short WRITE at the tail
goes through `packet._persist` (`workers/packet/__init__.py:843-918`), the ONE ChapterPacket
insert/replace writer, which acquires the lock itself with a bounded retry and can therefore raise
`ChapterWorkflowBusy` — the caller re-queues. `replace=False` is mandatory: `replace=True` deletes every
packet for the chapter (`:913-915`), the approved predecessor included, and `preserve_approved=True` would
RETURN that predecessor (`:904-912`) instead of inserting the amendment.

APPROVAL PROVENANCE IS NOT OURS TO WRITE. `approval_source`, `approved_at`, `superseded_by_packet_id`,
`superseded_at` and `amendment_scope` stay NULL here; they belong to `amendment.apply_authority_locked`.
Invariant 8 is that no model output may approve or supersede a chapter contract, and the CHECK at
`shared/migrations.py:396` admits no autonomous `approval_source` value at all — so this pass leaving it
NULL is the enforcement, not a convention.
"""

from __future__ import annotations

import asyncio
import copy
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from dominion.shared.config import settings
from dominion.shared.enums import ImportAdoptionMode, PacketStatus
from dominion.shared.grading import build_grade
from dominion.shared.models import Chapter, ChapterPacket
from dominion.workers import packet as packet_pipeline
from dominion.workers import telemetry, telemetry_db
from dominion.workers.packet import amendment, approval_policy, canon_conflict, master
from dominion.workers.packet import author as author_mod
from dominion.workers.packet import evidence as evidence_mod
from dominion.workers.packet import open_questions as open_questions_policy
from dominion.workers.packet import qa as qa_mod
from dominion.workers.packet.surface_contract import build_surface_contract
from dominion.workers.packet.validation import evaluate_chapter_packet_internal

log = structlog.get_logger()

#: `lineage.source` for an amendment body. Distinct from the initial evidence path's `import_adoption`
#: (`workers/packet/__init__.py:833`) so a later reader can tell which lifecycle produced a body without
#: joining back to the row's `origin_mode`.
LINEAGE_SOURCE = "import_adoption_amendment"

_AMENDMENT_RECOVERY_ACTIONS = [
    "Check the amendment's blocked reason below, fix the named input, then re-run the amendment.",
    "If the imported prose has moved on, re-run the amendment against the current prose.",
]


# ------------------------------------- the author-time scope record -------------------------------- #


@dataclass(frozen=True)
class _Scope:
    """WHY this amendment existed and WHAT it added — the author pass's half of invariant 7's record.

    It is persisted in the BODY (`source_inputs.amendment`), never in the `amendment_scope` COLUMN: that
    column is an AUTHORITY_FIELD written once at approval by `amendment.apply_authority_locked`,
    and a second writer for it is exactly the seam the #259 writer guard exists to keep shut. Recording it
    here also closes the write-only gap ADR-0034 flags in its Consequences (`:523-529`): the model comment
    at `shared/models.py:359-360` documents `unseeded_scene_ids` and `new_seed_ids` on `amendment_scope`,
    which the locked transition does not write — but they are only knowable HERE, at author time.
    """

    predecessor_packet_id: uuid.UUID
    unseeded_scene_ids: tuple[uuid.UUID, ...]
    unseeded_scene_nos: tuple[int, ...]
    preserved_seed_ids: tuple[str, ...]
    new_seed_ids: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "predecessor_packet_id": str(self.predecessor_packet_id),
            "unseeded_scene_ids": [str(x) for x in self.unseeded_scene_ids],
            "unseeded_scene_nos": list(self.unseeded_scene_nos),
            "preserved_seed_ids": list(self.preserved_seed_ids),
            "new_seed_ids": list(self.new_seed_ids),
        }


# --------------------------------------- the copy-on-write merge ---------------------------------- #


def _claim_key(claim: Any) -> str | None:
    """The de-duplication key for one claim: its normalized assertion text, or None when it has none."""
    if not isinstance(claim, dict):
        return None
    text = str(claim.get("claim") or "").strip().lower()
    return text or None


def _merge_claims(approved: Any, authored: Any) -> list[Any]:
    """Approved claims FIRST and unchanged, then every authored claim not already asserted.

    Claims are the only chapter-level section the amendment may grow, because the new seeds need
    traceable provenance and `master.validate_master_packet:311-319` hard-blocks a body whose
    `chapter_contract.claims` is not a list. Append-only and order-preserving: an approved claim is a
    human-reviewed assertion and is never rewritten or dropped by a later model pass."""
    out: list[Any] = list(approved) if isinstance(approved, list) else []
    seen = {key for claim in out if (key := _claim_key(claim)) is not None}
    for claim in authored if isinstance(authored, list) else []:
        key = _claim_key(claim)
        if key is not None and key in seen:
            continue
        out.append(claim)
        if key is not None:
            seen.add(key)
    return out


def _preserved_seed_ids(body: Any) -> tuple[str, ...]:
    """The seed ids the approved packet already carries, using `amendment._seed_index`'s definition of a
    seed so this pass, eligibility, and `derive.py:456-457` all agree on what counts."""
    seeds_by_id, _scene_nos = amendment._seed_index(body if isinstance(body, dict) else {})
    return tuple(seeds_by_id)


def _merge_amendment_body(
    *,
    approved_body: Any,
    authored: dict[str, Any],
    uncovered_scene_nos: set[int],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """THE copy-on-write merge. Returns `(merged raw body, the seeds actually added)`.

    DEEP-COPIED, not shallow: the returned body is handed on to `mint_seed_ids` and the canonicalization
    pipeline, both of which mutate seed dicts in place. A shallow copy would alias the live
    `approved_packet.body` sub-objects and corrupt the identity-mapped predecessor row in memory (JSONB
    columns here are not `Mutable`, so the corruption would not even round-trip to disk to be noticed).

    Four sections are DROPPED rather than carried:
      * `_surface_contract` and `qa` are DERIVED — the projection is rebuilt from the merged seeds by the
        caller (mandatory: `derive.py:456-457` reads seeds out of it) and the predecessor's QA verdict must
        never ride along as if it had graded this amendment;
      * `source_inputs` and `lineage` are provenance stamps for the pass that authored them, restamped by
        the caller.
    `status` is left to `master.to_master_packet`, which stamps the row's lifecycle into the body.

    Everything else the approved packet asserts is preserved verbatim. Only `scene_seeds` grows (for the
    uncovered scenes), `claims` grows (provenance for those seeds), and `confidence` is replaced with the
    author's fresh self-assessment — the predecessor's confidence graded a contract that did not contain
    these seeds, so inheriting it would present unreviewed material as already-assessed.
    """
    merged: dict[str, Any] = copy.deepcopy(approved_body) if isinstance(approved_body, dict) else {}
    for derived in ("_surface_contract", "qa", "source_inputs", "lineage", "status"):
        merged.pop(derived, None)

    # Every existing entry survives, in order, seed_id untouched — including a seed that carries no id
    # (`mint_seed_ids` will stamp one, which only ever ADDS linkability) and a malformed non-dict entry
    # (dropping approved content is a silent loss; `validate_master_packet:357-362` flags it as a repair).
    preserved: list[Any] = list(merged.get("scene_seeds") or [])

    added: list[dict[str, Any]] = []
    taken: set[int] = set()
    for seed in authored.get("scene_seeds") or []:
        if not isinstance(seed, dict):
            continue
        scene_no = seed.get("scene_no")
        # Three filters, all fail-closed toward the approved contract: a seed for a scene the approved
        # packet ALREADY covers is discarded (that is what makes this additive regardless of what the
        # author chose to re-emit — the prompt is not the enforcement), a scene_no that is not an int or
        # not in the uncovered set has nothing to bind to, and only the FIRST seed per uncovered scene is
        # taken so `import_adoption._seed_bindings` cannot bind two seeds to one scene.
        if not isinstance(scene_no, int) or scene_no not in uncovered_scene_nos or scene_no in taken:
            continue
        new_seed = dict(seed)
        new_seed.pop("seed_id", None)  # server-minted only — a model-supplied id is never trusted (#R2)
        added.append(new_seed)
        taken.add(scene_no)

    seeds: list[Any] = [*preserved, *added]
    # Re-sort into reading order when every seed can be ordered. Safe because identity is `seed_id`, not
    # position (`derive.py:601`), and `mint_seed_ids` documents reordering as a supported edit
    # (`workers/packet/__init__.py:97-98`); without it a new seed for scene 3 would render after scene 5.
    if all(isinstance(seed, dict) and isinstance(seed.get("scene_no"), int) for seed in seeds):
        seeds = sorted(seeds, key=lambda seed: int(seed["scene_no"]))
    merged["scene_seeds"] = seeds
    merged["claims"] = _merge_claims(merged.get("claims"), authored.get("claims"))
    if authored.get("confidence") is not None:
        merged["confidence"] = authored["confidence"]
    return merged, added


def _uncovered_scene_nos(
    evidence: Sequence[evidence_mod.SceneEvidence], unseeded_scene_ids: Sequence[uuid.UUID]
) -> tuple[int, ...]:
    """The display `scene_no` of every unseeded scene THIS pass holds evidence for.

    A vocabulary join, and it has to happen somewhere: the verdict names unseeded scenes by scene ID
    (`amendment.assess_chapter`) while the author and `import_adoption._seed_bindings:180-190` both key seeds
    by `scene_no`. The evidence bundle is the only place both appear.

    A scene the verdict calls unseeded but for which this pass extracted NO evidence is deliberately
    EXCLUDED: there is nothing to reconstruct its contract from, so admitting its scene_no would license
    the author to invent a seed from nothing. If that empties the set, the pass fails closed."""
    wanted = {str(scene_id) for scene_id in unseeded_scene_ids}
    return tuple(sorted({se.scene_no for se in evidence if str(se.scene_id) in wanted}))


# ----------------------------------------- the row factory ---------------------------------------- #


def _amendment_row(
    *,
    packet_id: uuid.UUID,
    chapter: Chapter,
    approved_packet: ChapterPacket,
    adoption_id: uuid.UUID,
    source_fingerprint: str,
    evidence_manifest_fingerprint: str,
    status: Any,
    confidence: Any,
    qa_verdict: Any,
    qa_warnings: dict[str, Any] | None,
    body: dict[str, Any],
    open_questions: dict[str, Any] | None,
) -> ChapterPacket:
    """Build the amendment row IN MEMORY. Pure — no session, no clock, no commit.

    The lineage and provenance columns are set HERE, as constructor arguments, rather than assigned onto
    the row by the caller: an attribute store to any of them is a ChapterPacket authority write, and this
    factory is the single place the writer guard has to verify. `packet._persist` is what actually inserts
    it, under the chapter workflow lock.

    NULL BY CONSTRUCTION, and each for a different reason:
      * `approval_source` / `approved_at` — only `amendment.apply_authority_locked` may set them,
        and `ck_chapter_packets_approval_source` (`migrations.py:396`) admits no autonomous value;
      * `superseded_by_packet_id` / `superseded_at` — an amendment is the SUCCESSOR; it is superseded only
        if some later amendment replaces it;
      * `amendment_scope` — written once at approval, when the consequence set (which ScenePackets the
        supersession staled) is finally known. The author-time half lives in `body.source_inputs.amendment`.

    Legal at both `proposed` and `blocked`: `ck_chapter_packets_amendment_names_predecessor`
    (`migrations.py:377`) only requires the predecessor link once `status='approved'`, and
    `uq_chapter_packets_active_chapter` (`:326-328`) indexes `status='approved'` only — which is what lets
    a proposed amendment coexist with the approved predecessor instead of racing it for the active slot.
    """
    return ChapterPacket(
        id=packet_id,
        book_id=chapter.book_id,
        chapter_id=chapter.id,
        status=status,
        confidence=confidence,
        qa_verdict=qa_verdict,
        qa_warnings=qa_warnings,
        body=body,
        open_questions=open_questions,
        origin_mode=ImportAdoptionMode.AMENDMENT.value,
        supersedes_packet_id=approved_packet.id,
        source_fingerprint=source_fingerprint,
        evidence_manifest_fingerprint=evidence_manifest_fingerprint,
        origin_adoption_id=adoption_id,
    )


def _make_amendment_fail_closed(
    session: AsyncSession,
    *,
    chapter: Chapter,
    approved_packet: ChapterPacket,
    adoption_id: uuid.UUID,
    source_fingerprint: str,
    evidence_manifest_fingerprint: str,
    sink: telemetry.TelemetrySink,
    run_id: uuid.UUID,
) -> packet_pipeline.FailClosed:
    """The amendment path's fail-closed tail: persist a BLOCKED AMENDMENT row and return it.

    It deliberately does NOT reuse `packet._make_fail_closed` (`workers/packet/__init__.py:243-291`).
    That closure returns the chapter's existing APPROVED packet whenever one exists — correct for the
    initial path, where an approved packet means a re-propose failed and must not clobber it, but wrong
    here by construction: in amendment mode an approved packet ALWAYS exists (it is the predecessor), so
    that closure would hand the predecessor back and the pass would report success having authored
    nothing. `preserve_approved=True` is refused in `_persist` for the same reason.

    A blocked amendment is the DESIGNED terminal diagnostic, not a leak:
    `uq_chapter_packets_open_amendment` is partial over `proposed` only, precisely *"a `blocked` amendment
    is terminal diagnostic evidence, and including it would let one failed attempt bar every future
    amendment of that chapter forever"* (`shared/migrations.py:330-332`). The adoption worker finalizes it
    to `failed` with the packet linked as diagnostic (Q14, `import_adoption.publish_adoption:475-479`), and
    a drifted pass deletes it (`_delete_pass_packet:416-421`) while the approved predecessor is untouched.
    """

    async def fail_closed(
        reason: str,
        body: dict[str, Any] | None = None,
        violations: list[dict[str, Any]] | None = None,
        open_questions: dict[str, Any] | None = None,
        blocker_source: str | None = None,
        blocker_kind: str | None = None,
        recovery_actions: list[str] | None = None,
        blocker_diagnostics: dict[str, Any] | None = None,
    ) -> ChapterPacket:
        # Reuse the propose path's fail-closed VOCABULARY rather than re-inventing it: `_blocked_row`
        # assembles the exact `qa_warnings` shape the Desk reads — blocker_source / blocker_kind /
        # recovery_actions / violations (`workers/packet/__init__.py:206-222`) — so a blocked amendment
        # renders identically to a blocked initial proposal. Its row is a TRANSIENT template that is
        # never added to the session; only its computed fields are carried onto the amendment row, because
        # `_blocked_row` cannot know the amendment lineage columns.
        template = packet_pipeline._blocked_row(
            book_id=chapter.book_id,
            chapter_id=chapter.id,
            reason=reason,
            body=body,
            violations=violations,
            open_questions=open_questions,
            blocker_source=blocker_source,
            blocker_kind=blocker_kind,
            recovery_actions=recovery_actions or _AMENDMENT_RECOVERY_ACTIONS,
            blocker_diagnostics=blocker_diagnostics,
        )
        log.warning(
            "amendment.author_blocked",
            chapter=str(chapter.id),
            predecessor=str(approved_packet.id),
            adoption=str(adoption_id),
            blocker_kind=blocker_kind,
        )
        row = _amendment_row(
            packet_id=uuid.uuid4(),
            chapter=chapter,
            approved_packet=approved_packet,
            adoption_id=adoption_id,
            source_fingerprint=source_fingerprint,
            evidence_manifest_fingerprint=evidence_manifest_fingerprint,
            status=template.status,
            confidence=template.confidence,
            qa_verdict=template.qa_verdict,
            qa_warnings=template.qa_warnings,
            body=template.body,
            open_questions=template.open_questions,
        )
        # Telemetry AFTER the write, for the reason `_qa_and_persist` gives at `__init__.py:515-518`:
        # `_persist` may roll back to retry a busy chapter lock, which would discard this run's
        # `llm_calls` rows even on a pass that then succeeds.
        persisted = await packet_pipeline._persist(session, chapter_id=chapter.id, row=row, replace=False)
        telemetry_db.persist_sink(session, sink, run_id=run_id, book_id=chapter.book_id, chapter_id=chapter.id)
        return persisted

    return fail_closed


# ------------------------------- validate -> QA -> persist (the tail) ------------------------------ #


async def _qa_and_persist_amendment(
    session: AsyncSession,
    *,
    chapter: Chapter,
    approved_packet: ChapterPacket,
    adoption_id: uuid.UUID,
    source_fingerprint: str,
    evidence_manifest_fingerprint: str,
    merged: dict[str, Any],
    scope: _Scope,
    source_inputs: dict[str, Any],
    extra_open_questions: Sequence[str],
    sink: telemetry.TelemetrySink,
    run_id: uuid.UUID,
    budget: Any,
    fail_closed: packet_pipeline.FailClosed,
) -> ChapterPacket:
    """The amendment's validate -> canonicalize -> QA -> persist tail.

    It runs the SAME deterministic pipeline as both propose paths — internal validation, surface
    projection, canonical master packet, advisory QA, the Workstream-G grade — because an amendment IS a
    ChapterPacket and must not acquire *"its own body schema, its own QA path"* (ADR-0034:479-485). QA
    stays advisory here too: only the deterministic stages may block.

    It is a SECOND tail rather than a call into `packet._qa_and_persist` because that function hardcodes
    the two things amendment mode must invert — `_persist(..., replace=True)`
    (`workers/packet/__init__.py:519`), which would delete the approved predecessor, and its own
    `ChapterPacket(...)` construction (`:495-507`), which has no way to carry the amendment lineage
    columns. Closing that gap needs an additive parameter pair on `_qa_and_persist`; see this module's
    report rather than a second divergent pipeline growing here.
    """
    packet_id = uuid.uuid4()

    # (1) Internal validation: structure + roster contradictions on the merged body. A hard blocker here
    # means the MERGE produced an unusable contract, so it fails closed instead of proposing one.
    internal_result = evaluate_chapter_packet_internal(merged)
    body_internal = internal_result.normalized_body
    violations = internal_result.violations
    if internal_result.draft_blockers:
        return await fail_closed(
            "deterministic validation failed: " + "; ".join(v.detail for v in internal_result.draft_blockers),
            body=body_internal,
            violations=[v.as_dict() for v in violations],
            blocker_source="validation",
            blocker_kind="contract_validation",
            blocker_diagnostics={"stage": "internal_validation", "amendment_of": str(approved_packet.id)},
        )

    # (2) The drafter-safe projection, REBUILT from the merged seeds. Mandatory, not tidiness:
    # `derive.py:456-457` enumerates seeds out of `master.drafter_view(body)` — i.e. `_surface_contract` —
    # so carrying the predecessor's projection over would leave the new seeds invisible to derivation and
    # the amendment would repair nothing. `_merge_amendment_body` drops the stale one for this reason.
    surface_result = build_surface_contract(body_internal)
    violations.extend(surface_result.violations)
    if surface_result.blockers:
        return await fail_closed(
            "deterministic validation failed (surface): " + "; ".join(v.detail for v in surface_result.blockers),
            body=body_internal,
            violations=[v.as_dict() for v in violations],
            blocker_source="validation",
            blocker_kind="contract_validation",
            blocker_diagnostics={"stage": "surface_validation", "amendment_of": str(approved_packet.id)},
        )

    body = master.to_master_packet(
        body_internal,
        book_id=chapter.book_id,
        chapter_id=chapter.id,
        chapter_no=chapter.chapter_no,
        pov=chapter.pov,
        status=PacketStatus.PROPOSED,
    )
    body["source_inputs"] = {**source_inputs, "amendment": scope.as_dict()}
    body["lineage"] = {
        "source": LINEAGE_SOURCE,
        "packet_id": str(packet_id),
        "supersedes_packet_id": str(approved_packet.id),
        "origin_adoption_id": str(adoption_id),
    }
    body["_surface_contract"] = surface_result.surface_body  # DERIVED projection, never authoritative

    # Fold the manuscript-vs-canon conflict questions (and the author's own) into the canonical section
    # AND its top-level mirror, after canonicalization — `to_master_packet` resolves open questions from
    # the canonical section in preference to the legacy list (`master.py:77-92`), so folding earlier into
    # the mirror alone would drop them. Any open-question item blocks APPROVAL (approval_policy), never
    # adoption (Q14), which is the review gate a superseding contract must clear. De-duplicated,
    # append-only, order-preserving — the approved packet's own unresolved questions carry over first.
    if extra_open_questions:
        # Minted ids (#277). THIS is the path that generates manuscript-vs-canon conflict questions, and
        # the comment above once claimed they block APPROVAL — which was false on this very route until
        # the gate moved to the shared authority seam. They block it now, so they must also be rulable.
        folded = open_questions_policy.append_open_questions(
            body["chapter_contract"]["open_questions"], extra_open_questions
        )
        body["chapter_contract"]["open_questions"] = folded
        body["open_questions"] = master.open_question_texts(folded)

    # (3) Structural canary on the canonical body — true blockers only (e.g. no seed carries a usable
    # scene_job); fixable gaps ride along as repair tasks.
    master_violations = master.validate_master_packet(body)
    master_blockers = [v for v in master_violations if v["severity"] == "block"]
    if master_blockers:
        return await fail_closed(
            "canonical packet validation failed: " + "; ".join(v["detail"] for v in master_blockers),
            body=body,
            violations=[*(v.as_dict() for v in violations), *master_violations],
            open_questions=body["chapter_contract"]["open_questions"],
            blocker_source="validation",
            blocker_kind="contract_validation",
            blocker_diagnostics={"stage": "master_validation", "amendment_of": str(approved_packet.id)},
        )

    try:
        with telemetry.call_context(
            telemetry.CallContext(
                sink=sink, stage="packet_qa", book_id=str(chapter.book_id), chapter_id=str(chapter.id)
            )
        ):
            qa = await asyncio.wait_for(qa_mod.qa_packet(body, budget=budget), timeout=settings.packet_time_budget_s)
    except Exception as exc:  # noqa: BLE001 — any QA failure (timeout/budget/API) must fail closed
        log.error("amendment.qa_failed", chapter=str(chapter.id), error=str(exc))
        qa = None

    if qa is None:
        return await fail_closed(
            "Packet QA returned no usable verdict for the amendment.",
            body=body,
            blocker_source="qa",
            blocker_kind="no_usable_verdict",
            blocker_diagnostics={
                "stage": "packet_qa",
                "timeout_s": settings.packet_time_budget_s,
                "model": settings.packet_qa_model,
                "amendment_of": str(approved_packet.id),
            },
        )

    confidence, status = approval_policy.status_from_qa(body, qa)
    violation_dicts = [*(v.as_dict() for v in violations), *master_violations]
    grade = build_grade(
        artifact_id=packet_id,
        artifact_type="chapter_packet",
        grader=settings.packet_qa_model,
        qa=qa,
        violations=violation_dicts,
    )
    body["qa"] = {
        "verdict": str(getattr(qa["verdict"], "value", qa["verdict"])),
        "blocking_issues": grade["blocking_issues"],
        "warnings": grade["warnings"],
        "repair_tasks": grade["repair_tasks"],
        "graded_by": settings.packet_qa_model,
        "last_checked_at": datetime.now(UTC).isoformat(),
    }
    qa_warnings: dict[str, Any] = {"residual_risks": qa["residual_risks"], "issues": qa["issues"], "grade": grade}
    if violation_dicts:
        qa_warnings["violations"] = violation_dicts

    row = _amendment_row(
        packet_id=packet_id,
        chapter=chapter,
        approved_packet=approved_packet,
        adoption_id=adoption_id,
        source_fingerprint=source_fingerprint,
        evidence_manifest_fingerprint=evidence_manifest_fingerprint,
        status=status,
        confidence=confidence,
        qa_verdict=qa["verdict"],
        qa_warnings=qa_warnings,
        body=body,
        # Derived sync of body.chapter_contract.open_questions, kept for API/UI back-compat exactly as the
        # propose path does (`workers/packet/__init__.py:504-506`); the body section is the truth.
        open_questions=body["chapter_contract"]["open_questions"],
    )
    log.info(
        "amendment.authored",
        chapter=str(chapter.id),
        packet=str(packet_id),
        predecessor=str(approved_packet.id),
        status=str(status),
        confidence=str(confidence),
        preserved_seeds=len(scope.preserved_seed_ids),
        new_seeds=len(scope.new_seed_ids),
    )
    # `replace=False`, always: the whole point of amendment mode is that the approved predecessor survives
    # to be superseded (never deleted) by the locked approve transition.
    persisted = await packet_pipeline._persist(session, chapter_id=chapter.id, row=row, replace=False)
    telemetry_db.persist_sink(session, sink, run_id=run_id, book_id=chapter.book_id, chapter_id=chapter.id)
    return persisted


# ---------------------------------------- the public entry point ---------------------------------- #


async def author_amendment_from_evidence(
    session: AsyncSession,
    *,
    chapter: Chapter,
    evidence: Sequence[evidence_mod.SceneEvidence],
    approved_packet: ChapterPacket,
    adoption_id: uuid.UUID,
    source_fingerprint: str,
    evidence_manifest_fingerprint: str,
    retrieve: canon_conflict.CanonRetriever | None = None,
) -> ChapterPacket:
    """Author a copy-on-write AMENDMENT packet for `chapter` from its imported evidence.

    Returns a persisted `ChapterPacket` — `status=proposed` on success, `status=blocked` on any
    fail-closed path — with `origin_mode='amendment'`, `supersedes_packet_id=approved_packet.id`, and the
    four provenance values the caller supplies. The row is flushed, never committed: the caller owns the
    commit, exactly as `propose_packet_from_evidence` does.

    RAISES `amendment.AmendmentError` (never returns a packet) when the chapter is not in a state an
    amendment may be authored for at all — no approved packet, no unseeded scene, an amendment already
    open, or a predecessor that is no longer the chapter's authority. That distinction is deliberate: a
    blocked packet is a diagnostic about WORK THAT WAS ATTEMPTED, while these four are refusals to attempt
    it, and burning a model call (or opening a lineage the DB would reject) on them would be wrong.
    `ChapterWorkflowBusy` may escape from the tail's `_persist`; the caller re-queues.

    `source_fingerprint` is the chapter prose hash the CALLER captured before this pass began — the value
    `amendment.apply_authority_locked`'s drift gate recomputes under the lock and fails closed against. It
    is passed in rather than computed here on purpose: the value that matters is the one the whole pass
    (evidence extraction included) ran against, which is older than this function.

    `retrieve` is the live locked-canon seam (author C# handles and conflict re-anchoring both flow
    through it); it defaults to the session-bound retriever at the author's broad-canon `k`.
    """
    budget = packet_pipeline._propose_budget()
    sink = telemetry.TelemetrySink()
    run_id = uuid.uuid4()

    # (1) Revalidate eligibility from LIVE state. The verdict that authorized this adoption was computed
    # at entry time (`shared/adoption_entry.py:361-364`) and the evidence phase has committed several
    # checkpoints since, so it is advisory by now. Cheap (reads only, no model) and it saves the pass from
    # authoring against a chapter that has moved on. `assess_chapter` writes nothing and takes no lock, so
    # this is safe outside it (`amendment.assess_chapter`'s docstring says so explicitly).
    verdict = await amendment.assess_chapter(session, chapter_id=chapter.id)
    if verdict.approved_packet_id is None:
        raise amendment.AmendmentNotEligible(
            amendment.REASON_NO_APPROVED_PACKET,
            amendment.REFUSAL_MESSAGES[amendment.REASON_NO_APPROVED_PACKET],
        )
    if verdict.approved_packet_id != approved_packet.id:
        # The caller named a predecessor that is no longer the authority — this amendment would be authored
        # from, and would supersede, a contract that no longer governs (invariant 3's author-time half).
        raise amendment.AmendmentPredecessorMissing(
            f"chapter {chapter.id}'s approved authority is now {verdict.approved_packet_id}, not the "
            f"{approved_packet.id} this pass was asked to amend — another operation changed it first"
        )
    if not verdict.eligible:
        # Includes the idempotent `amendment_already_open` case: a second proposed amendment for one
        # chapter is what `uq_chapter_packets_open_amendment` rejects, and a typed refusal beats an
        # IntegrityError raised from inside `_persist` — the reasoning `amendment.assess_chapter` gives in
        # its own ALREADY_OPEN branch.
        raise amendment.AmendmentNotEligible(
            verdict.reason,
            amendment.REFUSAL_MESSAGES.get(verdict.reason, f"chapter {chapter.id} is not amendable"),
        )

    fail_closed = _make_amendment_fail_closed(
        session,
        chapter=chapter,
        approved_packet=approved_packet,
        adoption_id=adoption_id,
        source_fingerprint=source_fingerprint,
        evidence_manifest_fingerprint=evidence_manifest_fingerprint,
        sink=sink,
        run_id=run_id,
    )

    manuscript_handles = evidence_mod.build_manuscript_handles(evidence)
    if not manuscript_handles:
        return await fail_closed(
            "No imported-scene evidence to author this amendment from. Extract scene evidence, then "
            "re-run the amendment.",
            blocker_source="input",
            blocker_kind="no_evidence",
        )
    uncovered = _uncovered_scene_nos(evidence, verdict.unseeded_scene_ids)
    if not uncovered:
        return await fail_closed(
            "This pass holds no evidence for any scene the approved contract leaves unseeded, so there is "
            "nothing to author a seed from. Extract evidence for those scenes, then re-run the amendment.",
            blocker_source="input",
            blocker_kind="no_evidence_for_unseeded_scenes",
            blocker_diagnostics={
                "unseeded_scene_ids": [str(x) for x in verdict.unseeded_scene_ids],
                "evidence_scene_ids": [str(se.scene_id) for se in evidence],
            },
        )

    retrieve = retrieve or canon_conflict.session_retriever(session, chapter.book_id, k=packet_pipeline._CANON_K)
    omniscient = await packet_pipeline._omniscient_summary(session, chapter.book_id)
    prior_exit = await packet_pipeline._prior_exit_state(session, chapter=chapter)
    canon_query = evidence_mod.evidence_query(evidence)
    canon_hits = list(await retrieve(canon_query)) if canon_query.strip() else []
    handles: dict[str, dict[str, Any]] = {f"C{i}": dict(hit) for i, hit in enumerate(canon_hits, start=1)}

    # (2) The model call, on the FULL evidence bundle rather than only the uncovered scenes: a seed that
    # has to sit between two already-contracted scenes is unwritable without their context. What keeps the
    # pass additive is the MERGE, which discards any seed for an already-covered scene — the enforcement is
    # the filter, never the prompt.
    author_exc: Exception | None = None
    try:
        with telemetry.call_context(
            telemetry.CallContext(
                sink=sink, stage="packet_author", book_id=str(chapter.book_id), chapter_id=str(chapter.id)
            )
        ):
            authored = await asyncio.wait_for(
                author_mod.author_packet_from_evidence(
                    chapter_no=chapter.chapter_no,
                    pov=chapter.pov,
                    omniscient_summary=omniscient,
                    prior_exit_state=prior_exit,
                    next_entry_intent=None,
                    canon_handles=handles,
                    manuscript_handles=evidence_mod.rendered_bundle(manuscript_handles),
                    budget=budget,
                ),
                timeout=settings.packet_time_budget_s,
            )
    except Exception as exc:  # noqa: BLE001 — any author failure (timeout/budget/API) must fail closed
        log.error("amendment.author_failed", chapter=str(chapter.id), error=str(exc), error_type=type(exc).__name__)
        author_exc = exc
        authored = None

    if author_exc is not None:
        return await packet_pipeline._handle_author_failure(
            author_exc, chapter=chapter, fail_closed=fail_closed, context="a chapter-packet amendment"
        )
    if authored is None:
        return await fail_closed(
            "Packet Author response could not be parsed, possibly because the JSON was truncated.",
            blocker_source="author",
            blocker_kind="unparsable",
            blocker_diagnostics={"stage": "packet_author", "model": settings.packet_author_model},
        )
    if not packet_pipeline._valid_packet(authored):
        return await fail_closed(
            "Packet Author returned an incomplete packet with no scene seeds or no claims list.",
            body=authored,
            blocker_source="author",
            blocker_kind="thin_packet",
            blocker_diagnostics={"stage": "packet_author", "model": settings.packet_author_model},
        )

    evidence_mod.resolve_evidence_provenance(authored, canon_handles=handles, manuscript_handles=manuscript_handles)

    # (3) Author-time manuscript-vs-canon conflict detection, same seam as the initial path. Both a
    # re-anchored conflict and a non-re-anchorable one become open questions that block APPROVAL only.
    candidates = evidence_mod.candidate_conflicts(manuscript_handles)
    canon_handle_by_id = {str(hit.get("id")): handle for handle, hit in handles.items() if hit.get("id") is not None}
    conflict_result = await canon_conflict.detect_manuscript_canon_conflicts(
        candidates, retrieve=retrieve, canon_handle_by_id=canon_handle_by_id
    )
    extra_open_questions = [
        *(str(q) for q in authored.get("open_questions") or [] if str(q).strip()),
        *conflict_result.open_questions(),
        *(
            evidence_mod.fail_closed_question(fc.manuscript_handle, fc.scene_id, str(fc.reason), fc.detail)
            for fc in conflict_result.fail_closed
        ),
    ]

    # (4) The copy-on-write merge, then seed minting. Minting must run AFTER the merge and BEFORE the
    # scope record: it preserves every carried-over `seed_id` and stamps only the added seeds
    # (`workers/packet/__init__.py:95-101`), and the ids it stamps are what the scope reports.
    merged, added = _merge_amendment_body(
        approved_body=approved_packet.body, authored=authored, uncovered_scene_nos=set(uncovered)
    )
    packet_pipeline.mint_seed_ids(merged)
    if not added:
        # An amendment that adds no seed repairs nothing, yet approving it would still supersede a
        # human-approved contract and stale every ScenePacket derived from it
        # (`amendment._stale_children_of`). Fail closed rather than propose a supersession that costs history and
        # buys nothing.
        return await fail_closed(
            "The Packet Author produced no scene seed for any scene the approved contract leaves unseeded "
            f"(scene {', '.join(str(n) for n in uncovered)}), so this amendment would change nothing.",
            body=merged,
            blocker_source="author",
            blocker_kind="no_new_seeds",
            blocker_diagnostics={
                "stage": "amendment_merge",
                "uncovered_scene_nos": list(uncovered),
                "authored_scene_nos": [
                    seed.get("scene_no") for seed in authored.get("scene_seeds") or [] if isinstance(seed, dict)
                ],
            },
        )

    scope = _Scope(
        predecessor_packet_id=approved_packet.id,
        unseeded_scene_ids=tuple(verdict.unseeded_scene_ids),
        unseeded_scene_nos=uncovered,
        preserved_seed_ids=_preserved_seed_ids(approved_packet.body),
        new_seed_ids=tuple(str(seed["seed_id"]) for seed in added if seed.get("seed_id")),
    )
    source_inputs = {
        "prior_exit_state": bool(prior_exit),
        "omniscient_summary": bool(omniscient),
        "canon_handles": [
            {"handle": handle, "id": str(hit.get("id")), "name": hit.get("name")} for handle, hit in handles.items()
        ],
        "manuscript_handles": [
            {
                "handle": handle,
                "scene_id": str(se.scene_id),
                "scene_no": se.scene_no,
                "scene_version": se.scene_version,
                "prose_hash": se.prose_hash,
            }
            for handle, se in manuscript_handles.items()
        ],
        # Advisory precedence audit (ADR 0029), never a gate — same status as on the initial path.
        "precedence": evidence_mod.precedence_adjudication(merged.get("claims", [])),
    }
    return await _qa_and_persist_amendment(
        session,
        chapter=chapter,
        approved_packet=approved_packet,
        adoption_id=adoption_id,
        source_fingerprint=source_fingerprint,
        evidence_manifest_fingerprint=evidence_manifest_fingerprint,
        merged=merged,
        scope=scope,
        source_inputs=source_inputs,
        extra_open_questions=extra_open_questions,
        sink=sink,
        run_id=run_id,
        budget=budget,
        fail_closed=fail_closed,
    )
