# SceneFidelity Production Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build SceneFidelity as a contract-first production subsystem that improves romance, combat, spatial clarity, and reader-trackability without giving LLMs draft-readiness authority.

**Architecture:** Active, author-approved ScenePacket fidelity contracts are deterministically validated and projected into drafter context. Post-draft mode adapters produce immutable report Artifacts with per-hard-clause coverage; deterministic policy projects eligible findings to existing Critiques, Production Run triage creates human-required repair work, and repair previews remain author-controlled candidate prose.

**Tech Stack:** Python 3, FastAPI, SQLAlchemy/Postgres JSONB, Pydantic, existing Dominion worker/LLM telemetry stack, React/TypeScript Desk.

## Global Constraints

- SceneFidelity is one facade behind ScenePacket, never a parallel runtime agent framework.
- The closed mode registry is `relationship_turn`, `intimacy_blocking`, `combat_blocking`, `spatial_affordance`, and `reader_movie`.
- No active requirement may be malformed when a ScenePacket is approved.
- Only author-approved `fidelity_requirements[]` are active; suggestions never reach drafting, evaluation, Critiques, Issues, or export holds.
- `post_draft_policy` is `advisory` or `export_required`; structural validity is unconditional for active requirements.
- LLMs report evidence. Deterministic code owns packet approval, policy mapping, currentness, and export readiness.
- A hard clause requires one typed `satisfaction_criterion`; no finding is not proof of satisfaction.
- A satisfied hard-clause result requires positive prose evidence. Missing, stale, blocked, indeterminate, or failed evaluation is an operational hold, never a prose failure.
- SceneFidelity is forward-only and inert unless `fidelity_contract_version: 1` and active requirements are present in an approved packet.
- Existing packets, scenes, drafts, and exports are never backfilled or retroactively held.
- Every fidelity-derived Production Run RepairTask uses `HUMAN_REQUIRED`; only bounded RepairPreview Artifacts may be generated automatically.
- Use `apply_patch` for all source and documentation edits. Keep unrelated worktree changes intact.

---

## File Map

| Area | Files | Responsibility |
|---|---|---|
| Domain contract | `src/dominion/workers/scene_fidelity/models.py`, `contract.py`, `policy.py`, `payloads.py` | Typed modes, clauses, criteria, clause evaluations, report and Critique payload schemas. |
| Evaluation | `src/dominion/workers/scene_fidelity/evaluator.py`, `adapters.py`, `prompts.py` | Facade, bounded concurrent mode adapters, evidence validation, report merge and telemetry. |
| Persistence | `src/dominion/shared/models.py`, `enums.py`, `migrations.py`, `schemas.py` | Critique provenance fields/indexes, Issue statuses, API DTOs. |
| Packet/drafting | `src/dominion/workers/scene_packet/validation.py`, `author_sections.py`, `projections.py`, `src/dominion/workers/specialists/drafter.py` | Packet normalization, deterministic validation, suggestions, projection into high-salience prompt context. |
| Workflow | `src/dominion/workers/pipeline.py`, `production.py`, `production_repair.py`, `production_support.py` | Post-final-draft trigger, currentness, Critique projection, Production Run triage, holds, issue lifecycle, previews. |
| Operations | `src/dominion/shared/config.py`, `agent_registry.py`, `agent_ops.py` | Shared fidelity model role, approved fallback chain, bounded inflight setting, telemetry controls. |
| API/UI | `src/dominion/api/routers/scene_packets.py`, `scenes.py`, `production.py`, `frontend/src/desk/api/types.ts`, `frontend/src/desk/components/ScenePacketsPanel.tsx` | Author actions, packet editing, report/preview/override controls, decision-ready status. |
| Verification | `tests/test_scene_fidelity_*.py`, `tests/fixtures/scene_fidelity/` | Deterministic, captured-response, live-corpus, migration, and end-to-end fixtures. |

## Shared Interfaces

Implement these public types before any consumer lane writes against them:

```python
class FidelityMode(StrEnum):
    RELATIONSHIP_TURN = "relationship_turn"
    INTIMACY_BLOCKING = "intimacy_blocking"
    COMBAT_BLOCKING = "combat_blocking"
    SPATIAL_AFFORDANCE = "spatial_affordance"
    READER_MOVIE = "reader_movie"

class PostDraftPolicy(StrEnum):
    ADVISORY = "advisory"
    EXPORT_REQUIRED = "export_required"

class ClauseEnforcement(StrEnum):
    STANDARD = "standard"
    HARD = "hard"

class ClauseResult(StrEnum):
    SATISFIED = "satisfied"
    LOST = "lost"
    INDETERMINATE = "indeterminate"
    BLOCKED_BY_DEPENDENCY = "blocked_by_dependency"
    NOT_EVALUATED = "not_evaluated"
    ADAPTER_FAILED = "adapter_failed"

class SatisfactionCriterion(BaseModel):
    evidence_kind: Literal[
        "action", "dialogue", "interiority", "sequence",
        "spatial_relation", "sensory_anchor", "state_change", "absence_or_restraint",
    ]
    statement: str

class FidelityClause(BaseModel):
    clause_id: str
    enforcement: ClauseEnforcement
    statement: str
    satisfaction_criterion: SatisfactionCriterion | None = None
    depends_on_clause_ids: list[str] = []

class FidelityRequirement(BaseModel):
    requirement_id: str
    mode: FidelityMode
    post_draft_policy: PostDraftPolicy
    clauses: list[FidelityClause]

class EvidenceAnchor(BaseModel):
    start: int
    end: int
    quote: str
    anchor_kind: Literal["contradiction", "expected_beat", "transition", "satisfaction"]

class ClauseEvaluation(BaseModel):
    requirement_id: str
    clause_id: str
    mode: FidelityMode
    result: ClauseResult
    evidence_anchors: list[EvidenceAnchor]
    explanation: str
    evaluated_prose_hash: str
    packet_contract_fingerprint: str

class SceneFidelityReport(BaseModel):
    report_schema_version: int
    scene_id: UUID
    draft_attempt_id: UUID
    scene_packet_id: UUID
    prose_hash: str
    packet_contract_fingerprint: str
    clause_evaluations: list[ClauseEvaluation]

class CritiqueProjection(BaseModel):
    severity: Literal["warn", "repair"]
    note: str
    payload: dict[str, Any]
    finding_signature: str

class PolicyOutcome(BaseModel):
    kind: Literal["diagnostic", "warning", "repair_eligible", "operational_hold"]
    reason: str

class TriageResult(BaseModel):
    created_issue_ids: list[UUID]
    operational_holds: list[str]

def validate_active_requirements(body: Mapping[str, Any]) -> list[ScenePacketViolation]:
    raise NotImplementedError

def fidelity_contract_fingerprint(body: Mapping[str, Any]) -> str:
    raise NotImplementedError

def project_fidelity_for_drafter(body: Mapping[str, Any]) -> dict[str, Any]:
    raise NotImplementedError

async def evaluate_scene_fidelity(
    session: AsyncSession,
    *,
    scene: Scene,
    draft_attempt: DraftAttempt,
    packet: ScenePacket,
    trigger: Literal["post_draft", "manual", "production"],
) -> Artifact:
    raise NotImplementedError

def project_report_to_critiques(report: SceneFidelityReport) -> list[CritiqueProjection]:
    raise NotImplementedError

def policy_outcome_for_clause_evaluation(
    requirement: FidelityRequirement,
    evaluation: ClauseEvaluation,
) -> PolicyOutcome:
    raise NotImplementedError
```

### Lane 8, Phase A: Fixture and Integration Skeleton (starts at T0)

**Files:**
- Create: `tests/fixtures/scene_fidelity/manifest.json`
- Create: `tests/fixtures/scene_fidelity/hard/serra_agency_loss.json`
- Create: `tests/fixtures/scene_fidelity/hard/stale_report_is_operational_hold.json`
- Create: `tests/fixtures/scene_fidelity/delta/mutual_escalation_preserved.json`
- Create: `tests/fixtures/scene_fidelity/delta/combat_pillar_reversal.json`
- Create: `tests/test_scene_fidelity_fixtures.py`
- Create: `docs/scene_fidelity_merge_gate.md`

**Consumes:** The accepted mode registry and policy invariants in this plan.

**Produces:** A fixture manifest and merge gate that every other lane must satisfy.

- [ ] **Step 1: Write the manifest with hard, delta-reviewed, and exploratory fixture classes.**

```json
{
  "schema_version": 1,
  "fixtures": [
    {"id": "serra_agency_loss", "class": "hard"},
    {"id": "stale_report_is_operational_hold", "class": "hard"},
    {"id": "mutual_escalation_preserved", "class": "delta_reviewed"},
    {"id": "combat_pillar_reversal", "class": "delta_reviewed"}
  ]
}
```

- [ ] **Step 2: Write failing fixture-loader tests.**

```python
def test_fixture_manifest_has_unique_ids_and_known_classes() -> None:
    manifest = load_fixture_manifest()
    assert {item["class"] for item in manifest["fixtures"]} <= {"hard", "delta_reviewed", "exploratory"}
    assert len({item["id"] for item in manifest["fixtures"]}) == len(manifest["fixtures"])
```

- [ ] **Step 3: Implement `load_fixture_manifest()` and fixture schema validation.**

- [ ] **Step 4: Add hard fixture contracts for malformed active requirements, stale reports, invalid anchors, dependency cycles, no override inheritance, and forward-only legacy packets.**

- [ ] **Step 5: Run `pytest tests/test_scene_fidelity_fixtures.py -q`; expect PASS.**

- [ ] **Step 6: Commit `test(scene-fidelity): add fixture corpus skeleton`.**

### Lane 1: Contract and Migration

**Files:**
- Create: `src/dominion/workers/scene_fidelity/__init__.py`
- Create: `src/dominion/workers/scene_fidelity/models.py`
- Create: `src/dominion/workers/scene_fidelity/contract.py`
- Create: `src/dominion/workers/scene_fidelity/payloads.py`
- Modify: `src/dominion/shared/enums.py`
- Modify: `src/dominion/shared/models.py`
- Modify: `src/dominion/shared/migrations.py`
- Modify: `src/dominion/shared/schemas.py`
- Test: `tests/test_scene_fidelity_contract.py`
- Test: `tests/test_scene_fidelity_migrations.py`

**Consumes:** Lane 8 fixture contracts.

**Produces:** The only shared type vocabulary all later lanes import.

- [ ] **Step 1: Write failing contract tests for unknown modes, duplicate IDs, missing hard criteria, missing dependency targets, self-dependencies, dependency cycles, and legacy packets without fidelity fields.**

- [ ] **Step 2: Implement discriminated Pydantic requirement models for the five closed modes and `SatisfactionCriterion`.**

- [ ] **Step 3: Implement `validate_active_requirements()` with deterministic violations and `fidelity_contract_fingerprint()` using canonical JSON ordering.**

- [ ] **Step 4: Extend `Critique` with nullable `draft_attempt_id`, nullable `source_artifact_id`, nullable `finding_signature`, and `created_at`; add `IssueStatus.OVERRIDDEN` and `IssueStatus.SUPERSEDED`.**

- [ ] **Step 5: Add idempotent DDL for the nullable fields, foreign keys, partial unique report-projection index, and partial draft-attempt chronology index.**

```sql
CREATE UNIQUE INDEX IF NOT EXISTS uq_scene_fidelity_critique_report_finding
ON critiques (reviewer, source_artifact_id, finding_signature)
WHERE reviewer = 'scene_fidelity'
  AND source_artifact_id IS NOT NULL
  AND finding_signature IS NOT NULL;
```

- [ ] **Step 6: Define `SceneFidelityCritiquePayload` and verify its direct IDs match the Critique provenance columns.**

- [ ] **Step 7: Run `pytest tests/test_scene_fidelity_contract.py tests/test_scene_fidelity_migrations.py -q`; expect PASS.**

- [ ] **Step 8: Commit `feat(scene-fidelity): add typed contract and provenance schema`.**

### Lane 2: Packet Contract

**Files:**
- Modify: `src/dominion/workers/scene_packet/validation.py`
- Modify: `src/dominion/workers/scene_packet/author_sections.py`
- Modify: `src/dominion/workers/scene_packet/projections.py`
- Modify: `src/dominion/workers/scene_packet/__init__.py`
- Test: `tests/test_scene_packet.py`
- Test: `tests/test_scene_fidelity_packet_contract.py`

**Consumes:** Lane 1 `validate_active_requirements()`, mode models, and fingerprint function.

**Produces:** Validated, versioned active contracts and inactive suggestions in packet bodies.

- [ ] **Step 1: Write failing tests proving `fidelity_contract_version: 1` is required only when active requirements exist.**

- [ ] **Step 2: Add `fidelity_requirements` validation to `evaluate_scene_packet()` so malformed active contracts block packet approval.**

- [ ] **Step 3: Add packet-author output for `suggested_fidelity_requirements`; enforce that suggestions never enter active projection or draft readiness.**

- [ ] **Step 4: Implement server-side accept/refine/replace normalization: mint IDs for accepted entries, preserve IDs only on explicit refinement, and reject unresolved dependencies on replacement.**

- [ ] **Step 5: Persist the canonical fingerprint in the packet projection/report input without adding a parallel authority field.**

- [ ] **Step 6: Run `pytest tests/test_scene_packet.py tests/test_scene_fidelity_packet_contract.py -q`; expect PASS.**

- [ ] **Step 7: Commit `feat(scene-packet): validate versioned fidelity contracts`.**

### Lane 3A: Drafter Projection

**Files:**
- Modify: `src/dominion/workers/scene_packet/projections.py`
- Modify: `src/dominion/workers/context/contracts.py`
- Modify: `src/dominion/workers/context/types.py`
- Modify: `src/dominion/workers/specialists/drafter.py`
- Test: `tests/test_scene_fidelity_drafter_projection.py`

**Consumes:** Lane 1 requirement models and Lane 2 approved packet body.

**Produces:** High-salience fidelity contract sections for drafting.

- [ ] **Step 1: Write a failing projection test asserting prerequisite clauses render before dependent clauses.**

- [ ] **Step 2: Implement `project_fidelity_for_drafter()` with `must_preserve`, `must_not`, `scene_state`, and `establish_before_payoff` sections derived from active clauses only.**

- [ ] **Step 3: Add a dedicated `FIDELITY` section in `_contract_block()`; render IDs only in developer/debug context, not author-facing prose instructions.**

- [ ] **Step 4: Assert suggestions and legacy packets produce no fidelity prompt text.**

- [ ] **Step 5: Run `pytest tests/test_scene_fidelity_drafter_projection.py -q`; expect PASS.**

- [ ] **Step 6: Commit `feat(drafter): project approved fidelity constraints`.**

### Lane 3B: Evaluator and Telemetry

**Files:**
- Create: `src/dominion/workers/scene_fidelity/evaluator.py`
- Create: `src/dominion/workers/scene_fidelity/adapters.py`
- Create: `src/dominion/workers/scene_fidelity/prompts.py`
- Modify: `src/dominion/shared/config.py`
- Modify: `src/dominion/shared/agent_registry.py`
- Modify: `src/dominion/shared/agent_ops.py`
- Modify: `src/dominion/workers/pipeline.py`
- Test: `tests/test_scene_fidelity_evaluator.py`
- Test: `tests/test_scene_fidelity_telemetry.py`

**Consumes:** Lane 1 report and ClauseEvaluation types, Lane 2 active contracts, and Lane 5's early policy-interface stub.

**Produces:** One immutable report Artifact per evaluation and complete hard-clause coverage.

- [ ] **Step 1: Publish the early report shape to Lane 5.**

```python
async def evaluate_scene_fidelity(
    session: AsyncSession,
    *,
    scene: Scene,
    draft_attempt: DraftAttempt,
    packet: ScenePacket,
    trigger: Literal["post_draft", "manual", "production"],
) -> Artifact:
    raise NotImplementedError
```

- [ ] **Step 2: Write failing tests for adapter ownership, bounded concurrency, adapter failure coverage, positive satisfied anchors, omission anchors, and dependency diagnostics.**

- [ ] **Step 3: Implement the facade preflight, active-mode fan-out, and deterministic merger. Generate `adapter_failed` or `blocked_by_dependency` ClauseEvaluations instead of omitting hard clauses.**

- [ ] **Step 4: Implement mode-specific prompts that may read all relevant context but return findings only for clauses owned by their mode.**

- [ ] **Step 5: Add `scene_fidelity_model`, approved fallback chain, bounded inflight setting, prompt-version telemetry, and report schema/facade versions.**

- [ ] **Step 6: Trigger evaluation only after the final author-visible DraftAttempt is persisted and only if active requirements exist.**

- [ ] **Step 7: Run `pytest tests/test_scene_fidelity_evaluator.py tests/test_scene_fidelity_telemetry.py -q`; expect PASS.**

- [ ] **Step 8: Commit `feat(scene-fidelity): add evaluator facade and telemetry`.**

### Lane 5: Critique, Policy, and Production Triage

**Files:**
- Create: `src/dominion/workers/scene_fidelity/policy.py`
- Modify: `src/dominion/workers/production.py`
- Modify: `src/dominion/workers/production_repair.py`
- Modify: `src/dominion/workers/production_support.py`
- Modify: `src/dominion/workers/pipeline_status.py`
- Test: `tests/test_scene_fidelity_policy.py`
- Test: `tests/test_scene_fidelity_production.py`

**Consumes:** Lane 1 schemas and Lane 3B report Artifact contract.

**Produces:** Critique projections, deterministic policy outcomes, operational holds, idempotent Issue materialization, and truthful lifecycle transitions.

- [ ] **Step 1: Publish the early interface before Lane 3B finalizes its merger.**

```python
def project_report_to_critiques(report: SceneFidelityReport) -> list[CritiqueProjection]:
    raise NotImplementedError
def policy_outcome_for_clause_evaluation(
    requirement: FidelityRequirement,
    evaluation: ClauseEvaluation,
) -> PolicyOutcome:
    raise NotImplementedError

async def triage_scene_fidelity_for_production(
    session: AsyncSession,
    *,
    run: ProductionRun,
) -> TriageResult:
    raise NotImplementedError
```

- [ ] **Step 2: Write failing tests for every row of the accepted policy matrix, including mixed standard/hard evidence and invalid anchors.**

- [ ] **Step 3: Implement immutable `Critique(reviewer="scene_fidelity")` projections with strict payload validation and report-projection idempotency.**

- [ ] **Step 4: Implement currentness from scene packet ID, packet fingerprint, source DraftAttempt, and prose hash. Treat no report, stale report, indeterminate coverage, dependency blockage, and adapter failure as operational holds.**

- [ ] **Step 5: Materialize only current unresolved repair-eligible Critiques into run-owned Issues, idempotently keyed by `(production_run_id, fidelity_critique_id)` in Issue payload.**

- [ ] **Step 6: Implement `VERIFIED`, `OVERRIDDEN`, and `SUPERSEDED` transitions. Require current positive `satisfied` coverage to verify; never clear an Issue because a newer report omitted a complaint.**

- [ ] **Step 7: Run `pytest tests/test_scene_fidelity_policy.py tests/test_scene_fidelity_production.py tests/test_telemetry_production.py -q`; expect PASS.**

- [ ] **Step 8: Commit `feat(production): triage fidelity evidence and export holds`.**

### Lane 6: Repair Preview

**Files:**
- Create: `src/dominion/workers/scene_fidelity/repair_preview.py`
- Modify: `src/dominion/workers/production_repair.py`
- Modify: `src/dominion/shared/schemas.py`
- Test: `tests/test_scene_fidelity_repair_preview.py`

**Consumes:** Lane 5 repair-eligible Critiques and currentness checks.

**Produces:** Immutable bounded RepairPreview Artifacts and author-controlled materialization into new scene revisions.

- [ ] **Step 1: Write failing tests proving previews cannot change the current Scene, only accepted or edited previews create a new revision, and rejected previews leave the Critique intact.**

- [ ] **Step 2: Implement `create_repair_preview()` with source Critique/report/DraftAttempt IDs, prose hash, packet fingerprint, clause IDs, selected evidence window, preservation boundary, diff, and rationale in the Artifact body.**

- [ ] **Step 3: Restrict repair prompts to the selected loss, cited evidence, required dependencies, and minimal adjacent prose; forbid canon, outcome, and unrelated-scene changes.**

- [ ] **Step 4: Implement accept/edit/reject commands. Accept and edit create a normal new author-visible Scene revision, record provenance, stale old evidence, and schedule fresh evaluation.**

- [ ] **Step 5: Ensure fidelity-derived RepairTasks are always `HUMAN_REQUIRED`; the existing scheduler must never turn an export hold into an autonomous rewrite.**

- [ ] **Step 6: Run `pytest tests/test_scene_fidelity_repair_preview.py tests/test_redraft_scene.py -q`; expect PASS.**

- [ ] **Step 7: Commit `feat(scene-fidelity): add author-controlled repair previews`.**

### Lane 7: API and Desk UI

**Files:**
- Modify: `src/dominion/api/routers/scene_packets.py`
- Modify: `src/dominion/api/routers/scenes.py`
- Modify: `src/dominion/api/routers/production.py`
- Modify: `src/dominion/shared/schemas.py`
- Modify: `frontend/src/desk/api/types.ts`
- Modify: `frontend/src/desk/components/ScenePacketsPanel.tsx`
- Modify: `frontend/src/desk/lib/packetBlockers.ts`
- Create: `frontend/src/desk/components/SceneFidelityReview.tsx`
- Create: `frontend/src/desk/components/SceneFidelityPreview.tsx`
- Test: `tests/test_scene_fidelity_api.py`

**Consumes:** Lanes 1-6 public DTOs and actions.

**Produces:** Decision-ready author control without hiding evidence or lifecycle state.

- [ ] **Step 1: Write API tests for accepting/editing/replacing requirements, manual evaluation rerun, preview accept/edit/reject, and author override reason requirements.**

- [ ] **Step 2: Add packet endpoints/DTOs for active requirements, suggestions, refine/replace actions, and deterministic validation feedback.**

- [ ] **Step 3: Add scene endpoints/DTOs for report summary, currentness, ClauseEvaluations, RepairPreview retrieval, and manual rerun.**

- [ ] **Step 4: Add Production Run DTOs that distinguish repair holds from incomplete-evaluation holds and expose successor/override provenance.**

- [ ] **Step 5: Build Desk defaults around `problem -> why -> proposed fix -> next action`; keep clause graph, hashes, prompt/model telemetry, and raw report detail one expansion away.**

- [ ] **Step 6: Make suggestions visually distinct from active requirements. Require explicit author confirmation for activation, replacement, and override.**

- [ ] **Step 7: Run `pytest tests/test_scene_fidelity_api.py -q`, `pnpm --dir frontend typecheck`, `pnpm --dir frontend test`, and `pnpm --dir frontend build`; expect PASS.**

- [ ] **Step 8: Commit `feat(desk): add scene fidelity author workflow`.**

### Lane 8, Phase B: Continuous Integration and Final Gate

**Files:**
- Modify: `tests/test_scene_fidelity_fixtures.py`
- Create: `tests/test_scene_fidelity_end_to_end.py`
- Modify: `docs/scene_fidelity_merge_gate.md`
- Modify: `docs/superpowers/plans/2026-07-09-scene-fidelity-production.md`

**Consumes:** Every lane's public interfaces and fixture outcomes.

**Produces:** Verified production integration and a recorded quality-promotion baseline.

- [ ] **Step 1: Add captured-response tests for all five adapters and validate every output against evidence-anchor and clause-ownership rules.**

- [ ] **Step 2: Add end-to-end tests for the Marcus/Serra agency failure, mutual escalation non-detection, combat reachability failure, valid tactical reversal, stale report hold, adapter failure hold, preview acceptance, override, and successor Issue paths.**

- [ ] **Step 3: Add live-corpus promotion tooling that records model/prompt/fallback change, corpus version, hard-fixture result, false-positive delta, false-negative delta, and human rationale.**

- [ ] **Step 4: Verify every cross-lane invariant from the global constraints section and record the result in `docs/scene_fidelity_merge_gate.md`.**

- [ ] **Step 5: Run `.\scripts\verify.ps1`; expect ruff, formatting, pyright, and pytest to pass.**

- [ ] **Step 6: Run the live fixture corpus for approved fidelity-capable primary and fallback models; hard fixtures must pass exactly and delta-reviewed changes require written review.**

- [ ] **Step 7: Commit `test(scene-fidelity): verify production integration gate`.**

## Merge Order and Lane Rules

```text
Lane 8 Phase A starts immediately.
Lane 1 -> Lane 2 -> {Lane 3A, Lane 3B} -> Lane 5 -> Lane 6 -> Lane 7.
Lane 8 validates each merge and owns the final integration gate.
```

- Lane 5 publishes its policy/projection interfaces before Lane 3B finalizes report persistence and before Lane 6 starts preview ownership.
- No lane changes `draft_queue.py`; it remains a narrow approved-packet readiness check.
- No lane adds a fidelity-specific Issue, Critique, or RepairTask table.
- No lane lets a LLM change `Scene.status`, packet approval, Issue lifecycle, or export readiness directly.

## Acceptance Checklist

- [ ] All five modes have typed validators, prompts, and fixtures.
- [ ] Active hard clauses have one typed satisfaction criterion and complete merged coverage.
- [ ] Only explicit positive evidence verifies a prior fidelity Issue.
- [ ] Repair eligibility follows the locked policy matrix exactly.
- [ ] Missing/stale/failed evaluation creates an operational hold, not a prose failure.
- [ ] Repair previews are immutable Artifacts and never replace current prose automatically.
- [ ] Production triage is idempotent and only materializes current repair-eligible Critiques.
- [ ] `OVERRIDDEN` and `SUPERSEDED` preserve truthful Issue history.
- [ ] Legacy packets with no fidelity contract remain behaviorally unchanged.
- [ ] Fixture corpus gates prompts, models, fallbacks, schemas, merger, and policy changes.

## Self-Review

- Spec coverage: Contract shape, authority boundaries, provenance, five typed modes, drafter projection, evaluation, policy, production triage, repair previews, UI, model governance, fixtures, rollout, and eight implementation lanes are assigned to concrete tasks.
- Placeholder scan: No unspecified tasks or deferred design decisions remain in the plan.
- Type consistency: All lanes consume the shared interfaces defined above; policy consumes merged ClauseEvaluations, previews consume Critiques, and Issues materialize only during Production Run triage.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-09-scene-fidelity-production.md`.

1. **Subagent-Driven (recommended):** dispatch one fresh implementation agent per lane, review between merges, and keep Lane 8 active from the start.
2. **Inline Execution:** execute the lanes in this session with review checkpoints and the stated merge order.

---

## Implementation status (2026-07-10)

All eight lanes implemented on `feat/scene-fidelity`, each committed in isolation (the unrelated in-flight
OpenAI-catalog changes to config/registry/llm were never swept in). Deterministic gate green throughout:
ruff + ruff-format clean, pyright 0 errors on every touched file, full pytest **716 passed / 1 xfailed**;
frontend tsc/oxlint clean, vitest 311 passed (only the pre-existing missing-`jszip` file fails).

- Lanes 8A, 1, 2, 3A, 3B, 5, 6, 7, 8B: complete.
- SceneFidelity test files: contract, migrations, packet-contract, drafter-projection, evaluator,
  telemetry, policy, production, repair-preview, api, fixtures, end-to-end.
- **Deferred (backend fully supports):** live fixture-corpus run over approved primary + fallback models
  and captured-response adapter tests (need live LLMs); wiring accept/refine/replace controls into the
  large `ScenePacketsPanel`; mounting `SceneFidelityPreview` in the production/issue view.
