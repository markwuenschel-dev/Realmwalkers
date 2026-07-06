"""Production run sequence and assembly lane.

This module is implementation detail behind ``dominion.workers.production``. The public
production-run facade stays in ``production.py``; this file owns chapter sequence derivation,
chapter assembly, draft-run timeline, and draft queueing for missing sequence scenes.
"""

from __future__ import annotations

import re
import uuid
from collections import Counter, defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dominion.shared.enums import (
    ChapterSequenceStatus,
    IssueStatus,
    JobKind,
    JobStatus,
    ProductionRunStatus,
    SceneStatus,
)
from dominion.shared.models import (
    Beat,
    Chapter,
    ChapterPacket,
    ChapterSequence,
    DraftRunTimeline,
    Issue,
    Job,
    ProductionRun,
    RepairTask,
    Scene,
    ScenePacket,
)
from dominion.shared.severity import issue_gates
from dominion.shared.text_match import as_str_list, names_present
from dominion.workers.canon_guards import scan_packet_prose
from dominion.workers.draft_queue import schedule_contract_first_draft_jobs
from dominion.workers.length import planner as length_planner
from dominion.workers.packet import latest_approved as latest_approved_chapter_packet
from dominion.workers.packet import master as packet_master
from dominion.workers.packet.validation import leading_roster_name
from dominion.workers.production_support import (
    create_artifact as _create_artifact,
)
from dominion.workers.production_support import (
    create_issue as _create_issue,
)
from dominion.workers.production_support import (
    hash_payload as _hash_payload,
)
from dominion.workers.production_support import (
    issue_signature as _issue_signature,
)
from dominion.workers.production_support import (
    latest_approved_packet as _latest_approved_packet,
)
from dominion.workers.production_support import (
    now as _now,
)
from dominion.workers.production_support import (
    record_event as _record_event,
)
from dominion.workers.scene_packet import inputs as scene_packet_inputs
from dominion.workers.scene_scope import DUPLICATE_IRREVERSIBLE_BEAT, SCENE_SCOPE_BLEED, evaluate_scene_scope

# L6 (run orchestration): pure stage machine — pinned stage strings + deterministic gates that must
# fail BEFORE any LLM spend. Persistence stays here; decisions live in run_stages (DB-free, tested).
from dominion.workers import run_stages  # isort: skip


async def latest_chapter_sequence(session: AsyncSession, chapter_id: uuid.UUID) -> ChapterSequence | None:
    return (
        (
            await session.execute(
                select(ChapterSequence)
                .where(ChapterSequence.chapter_id == chapter_id)
                .order_by(ChapterSequence.updated_at.desc())
            )
        )
        .scalars()
        .first()
    )


async def latest_draft_timeline(session: AsyncSession, production_run_id: uuid.UUID) -> DraftRunTimeline | None:
    return (
        (
            await session.execute(
                select(DraftRunTimeline)
                .where(DraftRunTimeline.production_run_id == production_run_id)
                .order_by(DraftRunTimeline.updated_at.desc())
            )
        )
        .scalars()
        .first()
    )


def _contract_item(
    *,
    text: str,
    classification: str,
    blocks_drafting: bool,
    reader_visibility: str,
    drafting_rule: str,
    source_reference: str,
    confidence: float = 1.0,
) -> dict[str, Any]:
    return {
        "text": text,
        "classification": classification,
        "blocks_drafting": blocks_drafting,
        "reader_visibility": reader_visibility,
        "drafting_rule": drafting_rule,
        "source_reference": source_reference,
        "confidence": confidence,
    }


def derive_contract_classification(
    packet_body: dict[str, Any], open_questions: dict[str, Any] | None
) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    human_decisions = [q for q in as_str_list((open_questions or {}).get("items")) if q]
    intentional_mysteries = [q for q in as_str_list(packet_body.get("required_unanswered_questions")) if q]
    author_only_facts = [q for q in as_str_list(packet_body.get("forbidden_knowledge")) if q]
    forbidden_on_page_facts = [q for q in as_str_list(packet_body.get("forbidden_reveals")) if q]
    surface_mechanisms = [q for q in as_str_list(packet_body.get("canon_locks")) if q]
    deep_mechanisms_withheld = [q for q in as_str_list(packet_body.get("allowed_knowledge")) if q]
    character_behavior_locks = [q for q in as_str_list(packet_body.get("relationship_locks")) if q]
    reader_knowledge_limits = [q for q in as_str_list(packet_body.get("timeline_locks")) if q]
    roster_locks = [
        q
        for q in [*as_str_list(packet_body.get("roster_locks")), *as_str_list(packet_body.get("characters_forbidden"))]
        if q
    ]
    style_constraints = [
        q
        for q in [packet_body.get("emotional_spine"), packet_body.get("one_sentence_spine")]
        if isinstance(q, str) and q.strip()
    ]

    items.extend(
        _contract_item(
            text=q,
            classification="HUMAN_DECISION_REQUIRED",
            blocks_drafting=True,
            reader_visibility="blocked",
            drafting_rule="Do not draft past this unresolved author decision.",
            source_reference="chapter_packet.open_questions",
        )
        for q in human_decisions
    )
    items.extend(
        _contract_item(
            text=q,
            classification="INTENTIONAL_MYSTERY",
            blocks_drafting=False,
            reader_visibility="withheld",
            drafting_rule="Preserve as an intentional mystery; do not resolve it on page.",
            source_reference="chapter_packet.required_unanswered_questions",
        )
        for q in intentional_mysteries
    )
    items.extend(
        _contract_item(
            text=q,
            classification="AUTHOR_ONLY_FACT",
            blocks_drafting=False,
            reader_visibility="author_only",
            drafting_rule="Treat as true internally but keep it off the page.",
            source_reference="chapter_packet.forbidden_knowledge",
        )
        for q in author_only_facts
    )
    items.extend(
        _contract_item(
            text=q,
            classification="FORBIDDEN_ON_PAGE_FACT",
            blocks_drafting=False,
            reader_visibility="forbidden",
            drafting_rule="Do not reveal this fact on page.",
            source_reference="chapter_packet.forbidden_reveals",
        )
        for q in forbidden_on_page_facts
    )
    items.extend(
        _contract_item(
            text=q,
            classification="SURFACE_MECHANISM_LOCKED",
            blocks_drafting=False,
            reader_visibility="visible",
            drafting_rule="Use this as a locked surface rule; do not improvise around it.",
            source_reference="chapter_packet.canon_locks",
        )
        for q in surface_mechanisms
    )
    items.extend(
        _contract_item(
            text=q,
            classification="DEEP_MECHANISM_WITHHELD",
            blocks_drafting=False,
            reader_visibility="withheld",
            drafting_rule="Allow surface effects only; keep the deep mechanism withheld.",
            source_reference="chapter_packet.allowed_knowledge",
        )
        for q in deep_mechanisms_withheld
    )
    items.extend(
        _contract_item(
            text=q,
            classification="CHARACTER_BEHAVIOR_LOCK",
            blocks_drafting=False,
            reader_visibility="visible",
            drafting_rule="Do not change the underlying relationship or behavior lock.",
            source_reference="chapter_packet.relationship_locks",
        )
        for q in character_behavior_locks
    )
    items.extend(
        _contract_item(
            text=q,
            classification="READER_KNOWLEDGE_LIMIT",
            blocks_drafting=False,
            reader_visibility="withheld",
            drafting_rule="Keep the reader's knowledge bounded to this limit.",
            source_reference="chapter_packet.timeline_locks",
        )
        for q in reader_knowledge_limits
    )
    items.extend(
        _contract_item(
            text=q,
            classification="ROSTER_LOCK",
            blocks_drafting=False,
            reader_visibility="visible",
            drafting_rule="Respect the roster lock; do not add or reintroduce blocked participants.",
            source_reference="chapter_packet.roster_locks",
        )
        for q in roster_locks
    )
    items.extend(
        _contract_item(
            text=q,
            classification="STYLE_CONSTRAINT",
            blocks_drafting=False,
            reader_visibility="visible",
            drafting_rule="Preserve this high-level chapter style constraint while repairing prose.",
            source_reference="chapter_packet.style",
        )
        for q in style_constraints
    )

    return {
        "items": items,
        "human_decisions_required": human_decisions,
        "intentional_mysteries": intentional_mysteries,
        "author_only_facts": author_only_facts,
        "forbidden_on_page_facts": forbidden_on_page_facts,
        "surface_mechanisms": surface_mechanisms,
        "deep_mechanisms_withheld": deep_mechanisms_withheld,
        "character_behavior_locks": character_behavior_locks,
        "reader_knowledge_limits": reader_knowledge_limits,
        "style_constraints": style_constraints,
        "roster_locks": roster_locks,
    }


def derive_chapter_sequence(packet_body: dict[str, Any]) -> dict[str, Any]:
    seeds = [s for s in (packet_body.get("scene_seeds") or []) if isinstance(s, dict) and s.get("seed_id")]
    chapter_target, chapter_max = scene_packet_inputs.chapter_targets(packet_body, seeds)
    budgets = length_planner.plan_word_budgets(
        chapter_target_words=chapter_target,
        chapter_max_words=chapter_max,
        scene_seeds=seeds,
        chapter_packet_body=packet_body,
    )
    scenes: list[dict[str, Any]] = []
    beat_ownership: dict[str, int] = {}
    duplicates: list[str] = []
    scene_numbers = [int(s.get("scene_no") or 0) for s in seeds if isinstance(s.get("scene_no"), int)]
    ordered = sorted(seeds, key=lambda s: (int(s.get("scene_no") or 0), str(s.get("seed_id"))))
    for index, seed in enumerate(ordered):
        scene_no = int(seed.get("scene_no") or 0)
        seed_id = str(seed.get("seed_id"))
        required = [x for x in as_str_list(seed.get("required_beats")) if x]
        forbidden = [x for x in as_str_list(seed.get("forbidden_beats")) if x]
        for beat in required:
            if beat in beat_ownership and beat_ownership[beat] != scene_no:
                duplicates.append(beat)
            else:
                beat_ownership[beat] = scene_no
        entry = {
            "seed_id": seed_id,
            "scene_no": scene_no,
            "scene_function": str(seed.get("scene_job") or ""),
            "scene_type": str(seed.get("scene_type") or ""),
            "entry_state": str(seed.get("entry_state") or packet_body.get("entry_state") or ""),
            "exit_state": str(seed.get("exit_state") or ""),
            "owned_beats": required,
            "required_beats": required,
            "forbidden_beats": forbidden,
            "reader_knows_at_start": [],
            "reader_learns": [],
            "reader_may_infer_only": [],
            "reader_must_not_know": [],
            "pov_knows_at_start": [],
            "pov_must_not_know": [],
            "must_not_repeat": [],
            "forbidden_restarts": [],
            "word_budget": budgets.get(seed_id, seed.get("word_budget") or {}),
            "depends_on_scene_no": scene_numbers[index - 1] if index > 0 else None,
            "unlocks_scene_no": scene_numbers[index + 1] if index + 1 < len(scene_numbers) else None,
            "independent_draft_allowed": False,
        }
        scenes.append(entry)

    # Compute disciplined scene counts. Never default hard_max_scene_count to len(seeds).
    # Prefer explicit packet composition policy, else estimate from target words (avg ~1200 words/scene).
    explicit_target = packet_body.get("target_scene_count")
    explicit_hard_max = packet_body.get("hard_max_scene_count")
    avg_scene_words = 1200
    estimated_target = max(1, round(chapter_target / avg_scene_words)) if chapter_target else len(seeds)
    # Allow modest headroom; hard cap should come from settings/user or policy, fallback conservatively.
    estimated_hard = max(estimated_target + 2, round(estimated_target * 1.6)) if estimated_target else len(seeds)

    target_scene_count = (
        int(explicit_target) if isinstance(explicit_target, int) and explicit_target > 0 else estimated_target
    )
    hard_max_scene_count = (
        int(explicit_hard_max) if isinstance(explicit_hard_max, int) and explicit_hard_max > 0 else estimated_hard
    )

    # Do NOT inflate hard_max with len(scenes). If the seeds are bloated, the hard_max (derived from
    # words or explicit policy) is the authority; excess scenes must trigger merge/cut required actions.
    target_scene_count = max(target_scene_count, 0)
    hard_max_scene_count = max(hard_max_scene_count, target_scene_count)

    return chain_scene_entry_states(
        {
            "chapter_no": packet_body.get("chapter_no"),
            "chapter_job": packet_body.get("chapter_job") or "",
            "chapter_spine": packet_body.get("one_sentence_spine") or "",
            "target_words": chapter_target,
            "max_words": chapter_max or chapter_target,
            "hard_max_words": chapter_max or chapter_target,
            "target_scene_count": target_scene_count,
            "hard_max_scene_count": hard_max_scene_count,
            "global_entry_state": packet_body.get("entry_state") or "",
            "global_exit_state": packet_body.get("exit_state") or "",
            "scenes": scenes,
            "beat_ownership": beat_ownership,
            "forbidden_duplicate_functions": sorted(set(duplicates)),
            "composition_notes": {"must_merge": [], "must_cut": [], "must_expand": []},
        }
    )


def chain_scene_entry_states(body: dict[str, Any]) -> dict[str, Any]:
    """Deterministic post-pass enforcing the entry/exit chaining contract on a sequence body.

    The rule: scene 1 opens at the chapter's ``global_entry_state``; a dependent scene
    (``independent_draft_allowed`` false) opens exactly where the scene it ``depends_on`` exited.
    Seed/LLM-authored entry_states are rewritten to honor it — the Ch1 failure mode was every scene
    carrying the identical global entry, so each drafter restarted the whole chapter arc.
    ``depends_on_scene_no`` must reference an earlier scene (invalid/missing defaults to the previous
    scene) and ``unlocks_scene_no`` must reference a later one (invalid/missing defaults to the next).
    A scene with ``independent_draft_allowed`` true keeps its authored entry_state. Mutates ``body``'s
    scenes in place and returns ``body``.
    """
    scenes = sorted(
        [scene for scene in (body.get("scenes") or []) if isinstance(scene, dict)],
        key=lambda scene: (int(scene.get("scene_no") or 0), str(scene.get("seed_id") or "")),
    )
    if not scenes:
        return body
    global_entry = str(body.get("global_entry_state") or "").strip()
    by_scene_no = {int(scene.get("scene_no") or 0): scene for scene in scenes}
    scene_nos = [int(scene.get("scene_no") or 0) for scene in scenes]
    for index, scene in enumerate(scenes):
        scene_no = scene_nos[index]
        unlocks = _int_or_none(scene.get("unlocks_scene_no"))
        if unlocks is None or unlocks <= scene_no or unlocks not in by_scene_no:
            scene["unlocks_scene_no"] = scene_nos[index + 1] if index + 1 < len(scenes) else None
        if index == 0:
            scene["depends_on_scene_no"] = None
            if global_entry:
                scene["entry_state"] = global_entry
            continue
        dep_no = _int_or_none(scene.get("depends_on_scene_no"))
        if dep_no is None or dep_no >= scene_no or dep_no not in by_scene_no:
            dep_no = scene_nos[index - 1]
        scene["depends_on_scene_no"] = dep_no
        if scene.get("independent_draft_allowed"):
            continue
        dep_exit = str(by_scene_no[dep_no].get("exit_state") or "").strip()
        if dep_exit:
            scene["entry_state"] = dep_exit
    return body


def _int_or_none(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


_ROSTER_NAME_STOPWORDS: frozenset[str] = frozenset({"the", "a", "an", "of"})


def _roster_name_tokens(entry: str) -> list[str]:
    """Substantive name tokens of a roster entry's leading identifier — the whole-word candidates a
    prose visibility check may match on. "Serra Hawthorne (Dead Hand rogue)" -> ["Serra", "Hawthorne"];
    "The Broker" -> ["Broker"]. A match on ANY token counts as a named reference."""
    lead = leading_roster_name(entry)
    return [t for t in re.findall(r"\w+", lead) if len(t) >= 3 and t.lower() not in _ROSTER_NAME_STOPWORDS]


def run_chapter_draft_qa(
    sequence_body: dict[str, Any] | None,
    scene_rows: list[dict[str, Any]],
    full_prose: str,
    packet_body: dict[str, Any] | None = None,
    open_questions: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Structural ChapterDraftQA performed after chapter assembly.

    Checks for one timeline, duplicate functions/starts, entry/exit continuity,
    repeated onboarding signals, required beats presence (via sequence), forbidden reveals,
    chapter word budget, (when the chapter packet is supplied) that every characters_present
    roster entry is actually visible in the prose, and canon_contract_leak — prohibited on-page
    terms derived from the packet's prohibition fields and resolved rulings appearing in the
    assembled prose (see workers.canon_guards). This is the gate that can block final_chapter
    status. Findings carry `severity` + the derived `blocks_*` facts: "block" gates the final
    chapter, "repair" gates final export only (drafting and human review proceed), "warn" is
    advisory.
    """
    findings: list[dict[str, Any]] = []
    verdict = "pass"

    # Duplicate scene functions among drafted
    func_count: dict[str, list[int]] = defaultdict(list)
    for row in scene_rows:
        fn = str(row.get("scene_function") or row.get("function") or "").strip().lower()
        if fn:
            func_count[fn].append(int(row.get("scene_no") or 0))
    for fn, nos in func_count.items():
        if len(nos) > 1:
            findings.append(
                {
                    "kind": "duplicate_scene_function",
                    "scene_nos": sorted(set(nos)),
                    "function": fn,
                    "severity": "block",
                    **issue_gates("block"),
                }
            )
            verdict = "block"

    # Entry/exit continuity (re-check at assembly time)
    ordered = sorted(scene_rows, key=lambda r: int(r.get("scene_no") or 0))
    for i in range(1, len(ordered)):
        prev_exit = str(ordered[i - 1].get("exit_state") or "").strip()
        this_entry = str(ordered[i].get("entry_state") or "").strip()
        if prev_exit and this_entry and prev_exit != this_entry:
            findings.append(
                {
                    "kind": "entry_exit_mismatch",
                    "from_scene": ordered[i - 1].get("scene_no"),
                    "to_scene": ordered[i].get("scene_no"),
                    "severity": "warn",
                    **issue_gates("warn"),
                }
            )
            if verdict != "block":
                verdict = "warn"

    # Budget
    total_words = sum(int(r.get("word_count") or 0) for r in scene_rows)
    hard_max = (sequence_body or {}).get("hard_max_words")
    if isinstance(hard_max, int) and hard_max > 0 and total_words > hard_max:
        findings.append(
            {
                "kind": "word_budget_exceeded",
                "total": total_words,
                "hard_max": hard_max,
                "severity": "block",
                **issue_gates("block"),
            }
        )
        verdict = "block"

    # Very rough duplicate start / repeated onboarding detection (string level)
    starts = [((r.get("prose") or "")[:120].strip().lower()) for r in scene_rows if (r.get("prose") or "").strip()]
    if len(starts) != len(set(starts)) and len(starts) > 1:
        findings.append(
            {
                "kind": "similar_scene_openings",
                "count": len(starts),
                "severity": "warn",
                **issue_gates("warn"),
            }
        )
        if verdict == "pass":
            verdict = "warn"

    # PRESENT_CHARACTER_NOT_VISIBLE (draft-time positive check): the chapter contract lists a character
    # as present, but the assembled prose never names them — no visible evidence the reader can see.
    # Deterministic proxy for evidence: an exact whole-word reference to any substantive token of the
    # roster entry's leading name (no fuzzy NER). Repair-level: the fix is routed back to the drafter,
    # so it gates final export only — the verdict escalates at most to "warn", never "block".
    present = as_str_list((packet_body or {}).get("characters_present"))
    if present and full_prose.strip():
        for entry in present:
            display = leading_roster_name(entry) or entry
            tokens = _roster_name_tokens(entry)
            if not tokens or names_present([full_prose], tokens):
                continue
            findings.append(
                {
                    "kind": "PRESENT_CHARACTER_NOT_VISIBLE",
                    "character": display,
                    "detail": (
                        f"{display!r} is listed in characters_present but never visibly appears in the "
                        "assembled prose (no named reference found) — add visible evidence or move them "
                        "out of characters_present"
                    ),
                    "severity": "repair",
                    **issue_gates("repair"),
                }
            )
            if verdict == "pass":
                verdict = "warn"

    # CANON_CONTRACT_LEAK (deterministic): scan the assembled prose against on-page prohibitions
    # derived from the chapter packet's OWN contract fields (forbidden_surface_terms / forbidden_*
    # prohibition sentences) and resolved author rulings in `open_questions`. This is the check the
    # Ch1 bad run lacked: the "No Eyes notification in Chapter 1" ruling lived only in free text
    # while surface_terms listed "Neurochromatic Eyes" as the ALLOWED name, so no layer ever
    # compared prose to the prohibition. Deterministic, so "block" findings may gate final_chapter.
    if full_prose.strip():
        for leak in scan_packet_prose(full_prose, packet_body, open_questions):
            findings.append(leak)
            if leak.get("severity") == "block":
                verdict = "block"
            elif verdict == "pass":
                verdict = "warn"

    # Beat-ownership scope guards (recovery L2): deterministic keyword detection derived from the
    # sequence body's beat_ownership. A scene performing a LATER scene's owned beat is
    # scene_scope_bleed; an irreversible beat staged in more than one scene is
    # duplicate_irreversible_beat. Both severities come from scene_scope ("block" for irreversible
    # leaks/duplicates, "repair" otherwise — deterministic checks may block, per shared/severity.py).
    if sequence_body:
        prose_by_no = {int(r.get("scene_no") or 0): str(r.get("prose") or "") for r in scene_rows}
        for scope_issue in evaluate_scene_scope(prose_by_no, sequence_body):
            severity = str(scope_issue.get("severity") or "repair")
            findings.append({**scope_issue, **issue_gates(severity)})
            if severity == "block":
                verdict = "block"
            elif verdict == "pass":
                verdict = "warn"

    return {
        "verdict": verdict,
        "findings": findings,
        "total_words": total_words,
        "scene_count": len(scene_rows),
    }


def evaluate_chapter_sequence(body: dict[str, Any]) -> dict[str, Any]:
    scenes = sorted(
        [scene for scene in (body.get("scenes") or []) if isinstance(scene, dict)],
        key=lambda scene: (int(scene.get("scene_no") or 0), str(scene.get("seed_id") or "")),
    )
    beat_owners: dict[str, list[int]] = defaultdict(list)
    function_owners: dict[str, list[int]] = defaultdict(list)
    function_labels: dict[str, str] = {}
    entry_exit_mismatches: list[dict[str, Any]] = []
    planned_total_words = 0
    planned_max_words = 0
    planned_hard_max_words = 0

    for index, scene in enumerate(scenes):
        scene_no = _int_or_none(scene.get("scene_no"))
        function = str(scene.get("scene_function") or "").strip()
        if function and scene_no is not None:
            key = function.casefold()
            function_labels.setdefault(key, function)
            function_owners[key].append(scene_no)
        for beat in [beat for beat in as_str_list(scene.get("owned_beats") or scene.get("required_beats")) if beat]:
            if scene_no is not None and scene_no not in beat_owners[beat]:
                beat_owners[beat].append(scene_no)

        budget = scene.get("word_budget") if isinstance(scene.get("word_budget"), dict) else {}
        planned_total_words += _int_or_none(budget.get("target")) or 0  # type: ignore[arg-type]
        planned_max_words += _int_or_none(budget.get("max")) or 0  # type: ignore[arg-type]
        planned_hard_max_words += _int_or_none(budget.get("hard_max")) or 0  # type: ignore[arg-type]

        if index == 0 or scene.get("independent_draft_allowed"):
            # An independent scene may open away from the previous exit by design.
            continue
        # A dependent scene must open where the scene it depends_on exited (default: the previous
        # scene) — the same contract chain_scene_entry_states enforces.
        dep_no = _int_or_none(scene.get("depends_on_scene_no"))
        previous = next(
            (candidate for candidate in scenes[:index] if _int_or_none(candidate.get("scene_no")) == dep_no),
            scenes[index - 1],
        )
        previous_exit = str(previous.get("exit_state") or "").strip()
        entry_state = str(scene.get("entry_state") or "").strip()
        if previous_exit and entry_state and previous_exit != entry_state:
            entry_exit_mismatches.append(
                {
                    "previous_scene_no": previous.get("scene_no"),
                    "scene_no": scene_no,
                    "previous_exit_state": previous_exit,
                    "entry_state": entry_state,
                }
            )

    duplicate_beats = [
        {"beat": beat, "scene_nos": scene_nos} for beat, scene_nos in sorted(beat_owners.items()) if len(scene_nos) > 1
    ]
    duplicate_functions = [
        {"scene_function": function_labels[key], "scene_nos": scene_nos}
        for key, scene_nos in sorted(function_owners.items())
        if len(scene_nos) > 1
    ]

    scene_count = len(scenes)
    target_words = _int_or_none(body.get("target_words")) or planned_total_words
    max_words = _int_or_none(body.get("max_words")) or planned_max_words
    hard_max_words = _int_or_none(body.get("hard_max_words")) or planned_hard_max_words
    # Prefer the (now disciplined) values from derive; do not silently fall back to current scene_count
    # for hard_max. Fall back to planned only when explicit missing.
    target_scene_count = _int_or_none(body.get("target_scene_count")) or scene_count
    # Consistent with derive: hard max does not auto-inflate to current scene count.
    hard_max_scene_count = _int_or_none(body.get("hard_max_scene_count")) or max(target_scene_count, scene_count)

    budget_verdict = "pass"
    if scene_count > hard_max_scene_count or (hard_max_words and planned_total_words > hard_max_words):
        budget_verdict = "block"
    elif (max_words and planned_total_words > max_words) or scene_count > target_scene_count:
        budget_verdict = "warn"

    required_actions: list[dict[str, Any]] = []
    required_actions.extend(
        {
            "kind": "merge_scenes",
            "scenes": finding["scene_nos"][:2],
            "reason": f'Beat "{finding["beat"]}" is owned by multiple scenes.',
        }
        for finding in duplicate_beats
    )
    required_actions.extend(
        {
            "kind": "merge_scenes",
            "scenes": finding["scene_nos"][:2],
            "reason": f'Scene function "{finding["scene_function"]}" is duplicated.',
        }
        for finding in duplicate_functions
    )
    if scene_count > hard_max_scene_count:
        required_actions.append(
            {
                "kind": "cut_scene",
                "scenes": [],
                "reason": f"Scene count {scene_count} exceeds hard max {hard_max_scene_count}.",
            }
        )
    if hard_max_words and planned_total_words > hard_max_words:
        required_actions.append(
            {
                "kind": "chapter_compression",
                "scenes": [],
                "reason": f"Planned words {planned_total_words} exceed hard max {hard_max_words}.",
            }
        )

    budget_guard = {
        "verdict": budget_verdict,
        "planned_total_words": planned_total_words,
        "actual_total_words": None,
        "target_words": target_words,
        "hard_max_words": hard_max_words,
        "scene_count": scene_count,
        "target_scene_count": target_scene_count,
        "required_actions": required_actions,
        "warnings": [],
    }

    warnings: dict[str, Any] = {}
    if duplicate_beats:
        warnings["duplicate_beat_ownership"] = duplicate_beats
    if duplicate_functions:
        warnings["duplicate_scene_functions"] = duplicate_functions
    if entry_exit_mismatches:
        warnings["entry_exit_mismatches"] = entry_exit_mismatches
    if budget_verdict != "pass":
        warnings["budget_guard"] = budget_guard

    verdict = "approve"
    if duplicate_beats or duplicate_functions or entry_exit_mismatches or budget_verdict == "block":
        verdict = "block_drafting"
    elif budget_verdict == "warn":
        verdict = "approve_warn"

    return {
        "verdict": verdict,
        "warnings": warnings or None,
        "required_actions": required_actions,
    }


async def ensure_chapter_sequence(session: AsyncSession, packet: ChapterPacket) -> ChapterSequence:
    # The sequence's scene text feeds draft jobs, so it must come from the DERIVED drafter-safe view
    # (`_surface_contract`), never the raw top-level seeds — those are authoritative internal planning
    # data and may carry hidden canonical truth (master packet rule; legacy rows fall back to the body).
    body = derive_chapter_sequence(packet_master.drafter_view(packet.body or {}))
    source_hash = _hash_payload({"chapter_packet_id": str(packet.id), "body": body})
    latest = await latest_chapter_sequence(session, packet.chapter_id)
    evaluation = evaluate_chapter_sequence(body)
    status = (
        ChapterSequenceStatus.BLOCKED if evaluation["verdict"] == "block_drafting" else ChapterSequenceStatus.APPROVED
    )
    qa_verdict = evaluation["verdict"]
    qa_warnings = evaluation["warnings"]
    if latest is None:
        latest = ChapterSequence(
            book_id=packet.book_id,
            chapter_id=packet.chapter_id,
            chapter_packet_id=packet.id,
            status=status,
            target_words=body.get("target_words"),
            max_words=body.get("max_words"),
            hard_max_words=body.get("hard_max_words"),
            target_scene_count=body.get("target_scene_count"),
            hard_max_scene_count=body.get("hard_max_scene_count"),
            body=body,
            qa_verdict=qa_verdict,
            qa_warnings=qa_warnings,
            source_hash=source_hash,
        )
        session.add(latest)
    else:
        latest.chapter_packet_id = packet.id
        latest.status = status
        latest.target_words = body.get("target_words")
        latest.max_words = body.get("max_words")
        latest.hard_max_words = body.get("hard_max_words")
        latest.target_scene_count = body.get("target_scene_count")
        latest.hard_max_scene_count = body.get("hard_max_scene_count")
        latest.body = body
        latest.qa_verdict = qa_verdict
        latest.qa_warnings = qa_warnings
        latest.source_hash = source_hash
        latest.stale_reason = None
    await session.flush()
    return latest


async def _latest_scene_map(session: AsyncSession, chapter_id: uuid.UUID) -> dict[int, Scene]:
    rows = (
        await session.execute(
            select(Scene).where(Scene.chapter_id == chapter_id).order_by(Scene.scene_no, Scene.version.desc())
        )
    ).scalars()
    latest: dict[int, Scene] = {}
    for scene in rows:
        if scene.scene_no not in latest:
            if scene.status != SceneStatus.SUPERSEDED:
                latest[scene.scene_no] = scene
            else:
                latest[scene.scene_no] = scene
    return {scene_no: scene for scene_no, scene in latest.items() if scene.status != SceneStatus.SUPERSEDED}


async def _scene_packet_map(session: AsyncSession, chapter_id: uuid.UUID) -> dict[int, ScenePacket]:
    rows = (
        await session.execute(
            select(ScenePacket)
            .where(ScenePacket.chapter_id == chapter_id)
            .order_by(ScenePacket.scene_no, ScenePacket.created_at.desc())
        )
    ).scalars()
    latest: dict[int, ScenePacket] = {}
    for packet in rows:
        latest.setdefault(packet.scene_no, packet)
    return latest


async def assemble_run(session: AsyncSession, run: ProductionRun) -> None:
    latest_scenes = await _latest_scene_map(session, run.chapter_id)
    chapter = await session.get(Chapter, run.chapter_id)
    sequence = await latest_chapter_sequence(session, run.chapter_id)
    if chapter is None:
        return

    # L6 assembly gate — assembly REFUSES (structured run event + parked stage, never an exception
    # dump and never a chapter_draft "pretending it could succeed") when the sequence is QA-blocked
    # or when sequence scenes lack prose. The ch1 failure assembled 2 of 4 broken scenes anyway and
    # spent QA + a repair swarm on a chapter that could never be valid.
    scenes_with_prose = {no for no, sc in latest_scenes.items() if (sc.prose or "").strip()}
    gate = run_stages.evaluate_assembly_readiness(
        sequence.body if sequence is not None else None,
        scenes_with_prose,
        sequence_blocked=(
            sequence is not None
            and (sequence.status == ChapterSequenceStatus.BLOCKED or sequence.qa_verdict == "block_drafting")
        ),
    )
    if not gate.ok:
        run.current_stage = gate.next_stage or run_stages.STAGE_WAITING_FOR_SCENE_DRAFTS
        await _record_event(
            session,
            run_id=run.id,
            event_type="assembly_refused",
            stage=run.current_stage,
            message=f"Chapter assembly refused: {gate.reason}.",
            payload={"reason": gate.reason, "violations": gate.violations},
        )
        return
    run.current_stage = run_stages.STAGE_ASSEMBLING_CHAPTER

    seq_by_no = {}
    if sequence and sequence.body:
        for it in sequence.body.get("scenes") or []:
            if isinstance(it, dict):
                seq_by_no[int(it.get("scene_no") or 0)] = it

    scene_rows = []
    for scene in sorted(latest_scenes.values(), key=lambda s: s.scene_no):
        seq_item = seq_by_no.get(scene.scene_no, {})
        row = {
            "scene_id": str(scene.id),
            "scene_no": scene.scene_no,
            "version": scene.version,
            "status": str(scene.status),
            "word_count": scene.word_count,
            "scene_packet_id": str(scene.scene_packet_id) if scene.scene_packet_id else None,
            "prose": scene.prose or "",
            # Enrich for ChapterDraftQA and downstream consumers
            "scene_function": seq_item.get("scene_function") or seq_item.get("scene_job"),
            "entry_state": seq_item.get("entry_state"),
            "exit_state": seq_item.get("exit_state"),
            "owned_beats": seq_item.get("owned_beats") or seq_item.get("required_beats"),
            "required_beats": seq_item.get("required_beats"),
            "forbidden_beats": seq_item.get("forbidden_beats"),
            "reader_learns": seq_item.get("reader_learns"),
            "reader_must_not_know": seq_item.get("reader_must_not_know"),
            "word_budget": seq_item.get("word_budget"),
        }
        scene_rows.append(row)
    chapter_text = "\n\n".join((row["prose"] or "").strip() for row in scene_rows if (row["prose"] or "").strip())
    scene_count_expected = len((sequence.body or {}).get("scenes") or []) if sequence is not None else len(scene_rows)
    missing_scene_nos = []
    if sequence is not None:
        expected = {
            int(item.get("scene_no") or 0) for item in (sequence.body.get("scenes") or []) if isinstance(item, dict)
        }
        missing_scene_nos = sorted(expected - set(latest_scenes))

    # Keep DraftRunTimeline live as scenes are added during the run
    if sequence is not None:
        await ensure_draft_run_timeline(session, run)

    approved_packet = await latest_approved_chapter_packet(session, run.chapter_id)
    packet_body = approved_packet.body if approved_packet is not None else None
    chapter_draft_qa = run_chapter_draft_qa(
        sequence.body if sequence else None,
        scene_rows,
        chapter_text,
        packet_body=packet_body if isinstance(packet_body, dict) else None,
        open_questions=approved_packet.open_questions if approved_packet is not None else None,
    )

    issues = (
        (await session.execute(select(Issue).where(Issue.production_run_id == run.id).order_by(Issue.created_at)))
        .scalars()
        .all()
    )
    tasks = (
        (
            await session.execute(
                select(RepairTask).where(RepairTask.production_run_id == run.id).order_by(RepairTask.created_at)
            )
        )
        .scalars()
        .all()
    )
    severities = Counter(issue.severity for issue in issues)
    open_issue_statuses = {
        IssueStatus.PROPOSED,
        IssueStatus.ACCEPTED,
        IssueStatus.REPAIR_QUEUED,
        IssueStatus.REPAIRED,
        IssueStatus.ESCALATED,
    }
    open_issues = [issue for issue in issues if issue.status in open_issue_statuses]
    qa_block = chapter_draft_qa.get("verdict") == "block"
    ready_for_human = not open_issues and not missing_scene_nos and not qa_block

    chapter_artifact = await _create_artifact(
        session,
        run=run,
        artifact_type="chapter_draft",
        body={
            "chapter_id": str(run.chapter_id),
            "chapter_no": chapter.chapter_no,
            "title": chapter.title,
            "pov": chapter.pov,
            "scene_count": len(scene_rows),
            "scenes": scene_rows,
            "prose": chapter_text,
        },
        domain_table="chapters",
        domain_id=run.chapter_id,
    )
    qa_artifact = await _create_artifact(
        session,
        run=run,
        artifact_type="chapter_draft_qa",
        body={
            "scene_count_actual": len(scene_rows),
            "scene_count_expected": scene_count_expected,
            "missing_scene_nos": missing_scene_nos,
            "issue_counts_by_severity": dict(severities),
            "open_issue_count": len(open_issues),
            "repair_task_count": len(tasks),
            "latest_scene_statuses": {str(k): str(v.status) for k, v in latest_scenes.items()},
            "chapter_draft_qa": chapter_draft_qa,
        },
        dependencies=[(chapter_artifact.id, "source", chapter_artifact.content_hash)],
    )
    # Persist beat-ownership scope findings as Issue rows so triage can cluster
    # scene_scope_bleed / duplicate_irreversible_beat (recovery L2). Signature-deduped so
    # re-assembly never duplicates them. Severity passes through unchanged — the finding already
    # speaks the unified vocabulary (warn/repair/block), so `repair` survives into triage.
    scope_signatures = {
        str((issue.payload_json or {}).get("signature")) for issue in issues if isinstance(issue.payload_json, dict)
    }
    for finding in chapter_draft_qa.get("findings") or []:
        kind = str(finding.get("kind") or "")
        if kind not in (SCENE_SCOPE_BLEED, DUPLICATE_IRREVERSIBLE_BEAT):
            continue
        claim = str(finding.get("detail") or finding.get("beat") or kind)
        scene_no = finding.get("scene_no") if isinstance(finding.get("scene_no"), int) else None
        signature = _issue_signature(
            validator="scene_scope", issue_kind=kind, claim=claim, quote=None, scene_no=scene_no
        )
        if signature in scope_signatures:
            continue
        scope_signatures.add(signature)
        bleed_scene = latest_scenes.get(scene_no) if scene_no is not None else None
        await _create_issue(
            session,
            run=run,
            artifact_type="chapter_draft_qa",
            artifact_id=qa_artifact.id,
            scene_id=bleed_scene.id if bleed_scene is not None else None,
            scene_no=scene_no,
            validator="scene_scope",
            issue_kind=kind,
            severity=str(finding.get("severity") or "warn"),
            quote=None,
            span_start=None,
            span_end=None,
            claim=claim,
            contract_reference=str(sequence.id) if sequence is not None else None,
            recommended_action=(
                "Cut the leaked beat from this scene; only its owning scene may stage it."
                if kind == SCENE_SCOPE_BLEED
                else "Keep the irreversible beat only in its owning scene and remove the repeats."
            ),
            confidence=1.0,
            auto_repair_allowed=False,
            payload={**finding, "signature": signature},
        )
    await _create_artifact(
        session,
        run=run,
        artifact_type="reader_simulation",
        body={
            "missing_scene_nos": missing_scene_nos,
            "likely_confusions": [issue.claim for issue in issues if issue.severity in ("hard", "block")][:5],
            "open_issues": [issue.claim for issue in open_issues[:10]],
        },
        dependencies=[(chapter_artifact.id, "source", chapter_artifact.content_hash)],
    )
    await _create_artifact(
        session,
        run=run,
        artifact_type="agent_evaluation",
        body={
            "ready_for_human": ready_for_human,
            "blocking_issues": [issue.claim for issue in open_issues if issue.severity in ("hard", "block")],
            "issue_count": len(issues),
            "repair_task_count": len(tasks),
            "missing_scene_nos": missing_scene_nos,
        },
        dependencies=[
            (chapter_artifact.id, "source", chapter_artifact.content_hash),
            (qa_artifact.id, "verification_target", qa_artifact.content_hash),
        ],
    )
    if ready_for_human:
        final_status = "fully_validated" if chapter_draft_qa.get("verdict") == "pass" else "validated_with_warnings"
        await _create_artifact(
            session,
            run=run,
            artifact_type="final_chapter",
            body={
                "chapter_id": str(run.chapter_id),
                "chapter_no": chapter.chapter_no,
                "title": chapter.title,
                "pov": chapter.pov,
                "prose": chapter_text,
                "scene_count": len(scene_rows),
                "final_chapter_status": final_status,
            },
            dependencies=[(chapter_artifact.id, "source", chapter_artifact.content_hash)],
        )
        run.status = ProductionRunStatus.COMPLETED
        run.current_stage = "final_ready"
        await _record_event(
            session,
            run_id=run.id,
            event_type="final_ready",
            stage="final_ready",
            message="Final chapter is ready for human review.",
            payload={"chapter_id": str(run.chapter_id)},
        )
    else:
        # L6 chapter-QA routing — QA runs strictly AFTER a full assembly. STRUCTURAL blocking issues
        # (sequence_budget_mismatch, scene_scope_bleed, duplicate_irreversible_beat,
        # canon_contract_leak) park the run in structural_repair_required as one root-cause state
        # instead of scattering downstream symptoms into repair_execution.
        qa_outcome = run_stages.classify_qa_outcome(
            [issue.issue_kind for issue in open_issues]
            + [str(f.get("kind") or "") for f in chapter_draft_qa.get("findings") or []]
        )
        run.current_stage = qa_outcome.next_stage or run_stages.STAGE_CHAPTER_QA
        if not qa_outcome.ok:
            await _record_event(
                session,
                run_id=run.id,
                event_type="structural_repair_required",
                stage=run.current_stage,
                message="Chapter QA found structural blocking issues; prose repair is gated until they are fixed.",
                payload={"reason": qa_outcome.reason, "violations": qa_outcome.violations},
            )
        if run.status == ProductionRunStatus.RUNNING:
            run.status = ProductionRunStatus.WAITING_FOR_HUMAN


async def queue_draft_jobs_for_missing_sequence_scenes(session: AsyncSession, run: ProductionRun) -> list[uuid.UUID]:
    """Queue DRAFT jobs for ChapterSequence scenes that have an approved ScenePacket but lack prose.

    All draft paths go through dominion.workers.draft_queue (contract-first).
    Production drives by identifying the targets from ChapterSequence and delegating to the scheduler.
    """
    if run.current_stage == "timeline_failed":
        await _record_event(
            session,
            run_id=run.id,
            event_type="draft_blocked",
            stage=run.current_stage or "draft_missing",
            message="Production blocked due to prior timeline update failure.",
            payload={"production_run_id": str(run.id)},
        )
        return []

    sequence = await latest_chapter_sequence(session, run.chapter_id)
    if not sequence or not sequence.body:
        return []
    scene_packets = await _scene_packet_map(session, run.chapter_id)

    # L6 drafting gate — structural preconditions fail BEFORE any LLM call: valid derived sequence
    # (not QA-blocked), non-contradictory budget arithmetic, and an approved NON-STALE ScenePacket
    # for every sequence scene. The ch1 failure queued four drafters against budgets that already
    # guaranteed a 34% chapter overrun.
    gate = run_stages.evaluate_drafting_readiness(
        sequence_status=str(sequence.status),
        sequence_qa_verdict=sequence.qa_verdict,
        sequence_body=sequence.body,
        scene_packets={
            no: {"status": str(p.status), "word_budget": (p.body or {}).get("word_budget")}
            for no, p in scene_packets.items()
        },
    )
    if not gate.ok:
        if gate.next_stage:
            run.current_stage = gate.next_stage
        await _record_event(
            session,
            run_id=run.id,
            event_type="draft_blocked",
            stage=run.current_stage or "draft_missing",
            message=f"Draft queueing refused before LLM spend: {gate.reason}.",
            payload={"reason": gate.reason, "violations": gate.violations},
        )
        return []

    seq_scenes = sorted(
        [s for s in (sequence.body.get("scenes") or []) if isinstance(s, dict)],
        key=lambda s: int(s.get("scene_no") or 0),
    )
    existing_scenes = await _latest_scene_map(session, run.chapter_id)

    chapter = await session.get(Chapter, run.chapter_id)

    for item in seq_scenes:
        sno = int(item.get("scene_no") or 0)
        if sno <= 0:
            continue
        existing = existing_scenes.get(sno)
        if existing and (existing.prose or "").strip():
            continue  # already has prose

        # Dependency gate: only queue the next if its depends_on is satisfied
        dep_no = item.get("depends_on_scene_no")
        if dep_no is not None:
            dep = existing_scenes.get(int(dep_no))
            if not (dep and (dep.prose or "").strip()):
                continue

        sp = scene_packets.get(sno)
        if sp is None or getattr(sp, "status", None) != "approved":
            await _record_event(
                session,
                run_id=run.id,
                event_type="draft_blocked",
                stage=run.current_stage or "draft_missing",
                message=f"Scene {sno} requires an approved ScenePacket before drafting.",
                payload={"scene_no": sno, "required_action": "derive/approve ScenePacket for sequence scene"},
            )
            return []

        beat = (
            await session.execute(select(Beat).where(Beat.chapter_id == run.chapter_id, Beat.scene_no == sno))
        ).scalar_one_or_none()
        if beat is None:
            await _record_event(
                session,
                run_id=run.id,
                event_type="draft_blocked",
                stage=run.current_stage or "draft_missing",
                message=f"No approved Beat for sequence scene {sno}.",
                payload={"scene_no": sno},
            )
            return []

        # Queue *only* this next one
        if chapter:
            await schedule_contract_first_draft_jobs(
                session,
                chapter=chapter,
                beats=[beat],
                run=None,
                skip_drafted=True,
                production_run_id=run.id,
            )
            recent = (
                (
                    await session.execute(
                        select(Job)
                        .where(
                            Job.chapter_id == run.chapter_id,
                            Job.kind == JobKind.DRAFT,
                            Job.status == JobStatus.QUEUED,
                            Job.scene_no == sno,
                        )
                        .order_by(Job.created_at.desc())
                        .limit(1)
                    )
                )
                .scalars()
                .all()
            )
            created_job_ids = [j.id for j in recent]
            for jid in created_job_ids:
                await _record_event(
                    session,
                    run_id=run.id,
                    event_type="draft_queued",
                    stage="draft_missing",
                    message=f"Draft job queued (sequentially) for sequence scene {sno}.",
                    payload={"job_id": str(jid), "scene_no": sno, "production_run_id": str(run.id)},
                )
            if created_job_ids:
                # L6: pinned stage string — drafts are in flight for this run.
                run.current_stage = run_stages.STAGE_DRAFTING_SCENES
            return created_job_ids

    return []


async def ensure_draft_run_timeline(session: AsyncSession, run: ProductionRun) -> DraftRunTimeline:
    """Ensure a durable live DraftRunTimeline exists for this production run.

    Seeds from sequence globals and current scene state. This becomes the source of truth for
    sequential drafting memory across scenes in the run.
    """
    sequence = await latest_chapter_sequence(session, run.chapter_id)
    latest_scenes_map = await _latest_scene_map(session, run.chapter_id)

    seq_body = (sequence.body or {}) if sequence else {}
    seq_scenes = seq_body.get("scenes") or []

    drafted_scenes: list[dict[str, Any]] = []
    spent_beats: list[str] = []
    reader_learned: list[str] = []
    current_exit = seq_body.get("global_entry_state") or seq_body.get("global_exit_state")

    for item in seq_scenes:
        if not isinstance(item, dict):
            continue
        sno = int(item.get("scene_no") or 0)
        sc = latest_scenes_map.get(sno)
        entry = {
            "scene_no": sno,
            "scene_function": item.get("scene_function"),
            "status": str(sc.status) if sc else "missing",
            "word_count": sc.word_count if sc else None,
            "has_prose": bool((sc.prose or "").strip()) if sc else False,
        }
        drafted_scenes.append(entry)
        if sc and sc.prose:
            # Seed naive aggregates from owned beats on sequence (real extraction would parse prose too)
            for b in as_str_list(item.get("owned_beats") or item.get("required_beats")):
                if b not in spent_beats:
                    spent_beats.append(b)

    tl = await latest_draft_timeline(session, run.id)
    if tl is None:
        tl = DraftRunTimeline(
            production_run_id=run.id,
            chapter_id=run.chapter_id,
            current_scene_no=None,
            chapter_so_far_summary=seq_body.get("chapter_spine"),
            current_exit_state=current_exit,
            spent_beats=spent_beats or [],
            reader_learned=reader_learned or [],
            pov_learned={},
            must_not_repeat_after=[],
            drafted_scenes=drafted_scenes,
        )
        session.add(tl)
    else:
        tl.drafted_scenes = drafted_scenes
        tl.spent_beats = spent_beats or tl.spent_beats
        tl.current_exit_state = current_exit or tl.current_exit_state
        tl.updated_at = _now()

    await session.flush()

    # Keep artifact in sync for UI (the model is the live one)
    await _create_artifact(
        session,
        run=run,
        artifact_type="draft_run_timeline",
        body={
            "production_run_id": str(run.id),
            "current_scene_no": tl.current_scene_no,
            "current_exit_state": tl.current_exit_state,
            "spent_beats": tl.spent_beats or [],
            "reader_learned": tl.reader_learned or [],
            "must_not_repeat_after": tl.must_not_repeat_after or [],
            "chapter_so_far_summary": tl.chapter_so_far_summary,
            "drafted_scenes": tl.drafted_scenes or [],
        },
        dependencies=[],
    )
    return tl


async def update_timeline_after_scene(
    session: AsyncSession, production_run_id: uuid.UUID | None, scene: Scene
) -> DraftRunTimeline | None:
    """Update (or create) the DraftRunTimeline immediately after a scene for this production run persists.

    Consumes the just-drafted Scene + its ScenePacket + the ChapterSequence item to compute
    the new cumulative state. This is the critical post-persist step for sequential memory.
    """
    if production_run_id is None:
        return None
    run = await session.get(ProductionRun, production_run_id)
    if run is None:
        return None
    sequence = await latest_chapter_sequence(session, run.chapter_id)
    sp = await session.get(ScenePacket, scene.scene_packet_id) if scene.scene_packet_id else None

    seq_item: dict[str, Any] = {}
    if sequence and sequence.body:
        for it in sequence.body.get("scenes") or []:
            if isinstance(it, dict) and int(it.get("scene_no") or 0) == scene.scene_no:
                seq_item = it
                break

    tl = await latest_draft_timeline(session, production_run_id)
    if tl is None:
        tl = DraftRunTimeline(
            production_run_id=production_run_id,
            chapter_id=run.chapter_id,
            current_scene_no=scene.scene_no,
            chapter_so_far_summary=(sequence.body or {}).get("chapter_spine") if sequence else None,
            current_exit_state=None,
            spent_beats=[],
            reader_learned=[],
            pov_learned={},
            must_not_repeat_after=[],
            drafted_scenes=[],
        )
        session.add(tl)

    # Compute updates
    tl.current_scene_no = scene.scene_no

    exit_state = None
    if sp and isinstance(sp.body, dict):
        exit_state = sp.body.get("exit_state")
    if not exit_state:
        exit_state = seq_item.get("exit_state")
    if exit_state:
        tl.current_exit_state = exit_state

    # spent_beats union
    owned = as_str_list(seq_item.get("owned_beats") or seq_item.get("required_beats"))
    spent = list(tl.spent_beats or [])
    for b in owned:
        if b and b not in spent:
            spent.append(b)
    tl.spent_beats = spent

    # reader learned from packet
    learned = list(tl.reader_learned or [])
    if sp and isinstance(sp.body, dict):
        learned_d = (sp.body.get("learned_during_scene") or {}).get("reader_must_learn") or []
        for item in as_str_list(learned_d):
            if item and item not in learned:
                learned.append(item)
    tl.reader_learned = learned

    # must_not_repeat
    mnr = list(tl.must_not_repeat_after or [])
    for item in as_str_list(seq_item.get("must_not_repeat")):
        if item and item not in mnr:
            mnr.append(item)
    tl.must_not_repeat_after = mnr

    # drafted_scenes list
    ds = list(tl.drafted_scenes or [])
    entry = {
        "scene_no": scene.scene_no,
        "scene_id": str(scene.id),
        "version": scene.version,
        "word_count": scene.word_count,
        "status": str(scene.status),
        "exit_state": exit_state,
    }
    # replace if exists
    ds = [d for d in ds if d.get("scene_no") != scene.scene_no]
    ds.append(entry)
    ds.sort(key=lambda d: d.get("scene_no") or 0)
    tl.drafted_scenes = ds

    if not tl.chapter_so_far_summary and sequence:
        tl.chapter_so_far_summary = (sequence.body or {}).get("chapter_spine")

    tl.updated_at = _now()
    # L6: a scene just persisted with its critiques — the run is in per-scene QA until the next
    # draft is queued (drafting_scenes) or assembly is attempted (assembling_chapter / refusal).
    run.current_stage = run_stages.STAGE_SCENE_QA
    await session.flush()

    # Refresh artifact for visibility (best effort)
    try:
        await _create_artifact(
            session,
            run=run,
            artifact_type="draft_run_timeline",
            body={
                "production_run_id": str(production_run_id),
                "current_scene_no": tl.current_scene_no,
                "current_exit_state": tl.current_exit_state,
                "spent_beats": tl.spent_beats,
                "reader_learned": tl.reader_learned,
                "drafted_scenes": tl.drafted_scenes,
            },
        )
    except Exception:
        pass

    return tl


async def _block_production_on_timeline_failure(
    session: AsyncSession, production_run_id: uuid.UUID, error: str
) -> None:
    """Block the production run from advancing when timeline memory update fails after a scene.

    Do not rollback the drafted prose. Emit a hard event so the UI and queue logic see the failure.
    Subsequent attempts to queue the next scene in sequence will see the blocked state.
    """
    run = await session.get(ProductionRun, production_run_id)
    if run is None:
        return
    run.status = ProductionRunStatus.WAITING_FOR_HUMAN
    run.current_stage = "timeline_failed"
    await _record_event(
        session,
        run_id=run.id,
        event_type="timeline_update_failed",
        stage="timeline_failed",
        message="Timeline update failed after scene draft. Production blocked.",
        payload={"error": error, "scene_no": getattr(run, "current_scene_no", None)},
    )
    await session.flush()


async def mark_run_provider_rate_limited(
    session: AsyncSession, production_run_id: uuid.UUID, error: str
) -> ProductionRun | None:
    """L6: a provider 429 that survived retries is transient infrastructure — park the run in the
    retryable provider_rate_limited stage, NEVER in a contract/author-failure state. Status is left
    untouched so resume/re-queue re-enters the draft loop without ceremony."""
    run = await session.get(ProductionRun, production_run_id)
    if run is None:
        return None
    run.current_stage = run_stages.STAGE_PROVIDER_RATE_LIMITED
    await _record_event(
        session,
        run_id=run.id,
        event_type="provider_rate_limited",
        stage=run.current_stage,
        message="Provider rate limit (429) persisted past automatic retries; the run is retryable.",
        payload={"error": error[:2000], "retryable": True},
    )
    await session.flush()
    return run


async def derive_chapter_sequence_for_chapter(session: AsyncSession, chapter_id: uuid.UUID) -> ChapterSequence:
    packet = await _latest_approved_packet(session, chapter_id)
    if packet is None:
        raise ValueError("no approved chapter packet for this chapter")
    return await ensure_chapter_sequence(session, packet)


async def chapter_sequence_qa(session: AsyncSession, sequence_id: uuid.UUID) -> dict[str, Any]:
    sequence = await session.get(ChapterSequence, sequence_id)
    if sequence is None:
        raise ValueError("chapter sequence not found")
    evaluation = evaluate_chapter_sequence(sequence.body or {})
    sequence.qa_verdict = evaluation["verdict"]
    sequence.qa_warnings = evaluation["warnings"]
    sequence.status = (
        ChapterSequenceStatus.BLOCKED if evaluation["verdict"] == "block_drafting" else ChapterSequenceStatus.APPROVED
    )
    await session.flush()
    return evaluation


async def update_chapter_sequence(
    session: AsyncSession, sequence_id: uuid.UUID, body: dict[str, Any], reason: str | None = None
) -> ChapterSequence:
    sequence = await session.get(ChapterSequence, sequence_id)
    if sequence is None:
        raise ValueError("chapter sequence not found")
    # Manual edits must keep the entry/exit chaining contract — rewrite before persisting/QA.
    body = chain_scene_entry_states(body)
    sequence.body = body
    sequence.target_words = _int_or_none(body.get("target_words"))
    sequence.max_words = _int_or_none(body.get("max_words"))
    sequence.hard_max_words = _int_or_none(body.get("hard_max_words"))
    sequence.target_scene_count = _int_or_none(body.get("target_scene_count"))
    sequence.hard_max_scene_count = _int_or_none(body.get("hard_max_scene_count"))
    sequence.source_hash = _hash_payload({"chapter_sequence_id": str(sequence.id), "body": body})
    if reason is not None:
        sequence.stale_reason = reason
    await chapter_sequence_qa(session, sequence.id)
    return sequence


async def align_sequence_scene_count(session: AsyncSession, sequence_id: uuid.UUID) -> ChapterSequence:
    """One-click reconcile for `sequence_scene_count_mismatch`: set the sequence's PLANNING TARGET
    to the packet's actual seed count. The sequence's scenes[] is already one-per-seed — only the
    scalar target diverges (an explicit packet policy or a words/1200 estimate that re-derive would
    just reproduce). Seed count is derived server-side, never client-supplied. Delegates to
    update_chapter_sequence so entry-state chaining and sequence QA re-run as on any manual edit."""
    sequence = await session.get(ChapterSequence, sequence_id)
    if sequence is None:
        raise ValueError("chapter sequence not found")
    packet = await session.get(ChapterPacket, sequence.chapter_packet_id) if sequence.chapter_packet_id else None
    if packet is None:
        raise ValueError("chapter packet for this sequence not found")
    seed_count = len((packet.body or {}).get("scene_seeds") or [])
    if not seed_count:
        raise ValueError("chapter packet has no scene seeds — author seeds before aligning the plan")
    body = dict(sequence.body or {})
    body["target_scene_count"] = seed_count
    existing_hard_max = _int_or_none(body.get("hard_max_scene_count"))
    body["hard_max_scene_count"] = max(seed_count, existing_hard_max or 0)
    return await update_chapter_sequence(
        session, sequence_id, body, reason=f"aligned target_scene_count to {seed_count} seeded scenes"
    )


async def approve_chapter_sequence(session: AsyncSession, sequence_id: uuid.UUID) -> ChapterSequence:
    sequence = await session.get(ChapterSequence, sequence_id)
    if sequence is None:
        raise ValueError("chapter sequence not found")
    evaluation = await chapter_sequence_qa(session, sequence_id)
    if evaluation["verdict"] == "block_drafting":
        raise ValueError("chapter sequence is blocked by QA")
    sequence.status = ChapterSequenceStatus.APPROVED
    await session.flush()
    return sequence
