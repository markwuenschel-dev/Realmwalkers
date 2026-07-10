# SceneFidelity merge gate

Lane 8 owns this gate. It runs from T0 and validates **every** merge in the SceneFidelity build. A lane
is not mergeable until the boxes it touches are checked here and the fixture corpus
(`tests/fixtures/scene_fidelity/`) still passes at its required tier.

## Fixture tiers (ADR 0015)

| Tier | Directory | Promotion rule |
|---|---|---|
| `hard` | `tests/fixtures/scene_fidelity/hard/` | Must pass **exactly**. A regression blocks the merge. |
| `delta_reviewed` | `.../delta/` | May change only with a written false-positive / false-negative delta review recorded in the promotion log below. |
| `exploratory` | `.../exploratory/` | Non-blocking. A regression is reviewed, never gates a merge. |

No SceneFidelity **model, prompt, fallback, schema, merger, or policy** change is promotable without
re-running the versioned corpus (ADR 0015). Each promotion records: the change, corpus version, hard
result, false-positive delta, false-negative delta, and human rationale.

## Cross-lane invariants (from the plan's Global Constraints)

Each must be verified before the owning lane merges; check the box when a test enforces it.

- [ ] SceneFidelity is one facade behind ScenePacket, never a parallel runtime agent framework. *(Lane 3B)*
- [ ] The closed mode registry is exactly `relationship_turn`, `intimacy_blocking`, `combat_blocking`, `spatial_affordance`, `reader_movie`. *(Lane 1)*
- [ ] No active requirement may be malformed when a ScenePacket is approved. *(Lane 1/2)*
- [ ] Only author-approved `fidelity_requirements[]` are active; suggestions never reach drafting, evaluation, Critiques, Issues, or export holds. *(Lane 2/3A)*
- [ ] `post_draft_policy` is `advisory` or `export_required`; structural validity is unconditional for active requirements. *(Lane 1/2)*
- [ ] LLMs report evidence. Deterministic code owns packet approval, policy mapping, currentness, and export readiness. *(Lane 5)*
- [ ] A hard clause requires one typed `satisfaction_criterion`; "no finding" is never proof of satisfaction. *(Lane 1 + Lane 5)*
- [ ] A satisfied hard-clause result requires positive prose evidence; missing/stale/blocked/indeterminate/failed evaluation is an operational hold, never a prose failure. *(Lane 3B/5)*
- [ ] SceneFidelity is forward-only and inert unless `fidelity_contract_version: 1` and active requirements are present in an approved packet. *(Lane 1/2)*
- [ ] Existing packets, scenes, drafts, and exports are never backfilled or retroactively held. *(Lane 1/5)*
- [ ] Every fidelity-derived Production Run RepairTask uses `HUMAN_REQUIRED`; only bounded RepairPreview Artifacts may be generated automatically. *(Lane 5/6)*

## Locked policy mapping (ADR 0019) — the row every Lane 5 test must match

Policy evaluates **every cited clause independently**.

| Clause enforcement | Result / condition | Requirement policy | Outcome |
|---|---|---|---|
| — | active requirement malformed | — | **blocks packet approval** (not a Critique) |
| any | evidence anchor invalid | any | **report-only diagnostic** (no Critique) |
| any | any finding, requirement is `advisory` | advisory | **warning** Critique |
| `standard` | any finding | any | **warning** Critique (author may explicitly upgrade) |
| `hard` | `lost` = direct contradiction OR corroborated omission | `export_required` | **repair-eligible** Critique + Repair Preview |
| `hard` | `satisfied` with positive prose evidence | `export_required` | verifies the clause / clears its Issue |
| `hard` | `indeterminate` / `blocked_by_dependency` / `adapter_failed` / `not_evaluated` | `export_required` | **operational hold** (incomplete evaluation) |
| any | ambiguous mixed-clause evidence | any | **downgraded** |
| any | missing / stale / failed export-required evaluation | `export_required` | **operational hold** (not a Critique, not a prose failure) |

## Issue lifecycle (ADR 0020)

`OVERRIDDEN` and `SUPERSEDED` are additive statuses.

- → `VERIFIED`: only when a fresh **current** evaluation passes the Issue's hard clause with positive evidence.
- → `OVERRIDDEN`: only via an author-recorded exception; cancels the human-required task; never inherits to later drafts.
- → `SUPERSEDED`: only after a newer current eligible Critique materializes a **successor** Issue; the superseded Issue references its successor.
- Missing / stale / incomplete evaluation: the Issue stays unresolved and a **separate** operational hold is raised (no automatic transition). "No finding" never clears an Issue.

## Promotion log

_Record each model/prompt/fallback/schema/merger/policy change here: change, corpus version, hard result, FP delta, FN delta, rationale._

| Date | Change | Corpus ver | Hard | FP Δ | FN Δ | Rationale |
|---|---|---|---|---|---|---|
| 2026-07-09 | Lane 8A: corpus skeleton established | 1 | n/a (no evaluator yet) | — | — | Baseline fixture contracts before any adapter exists. |
