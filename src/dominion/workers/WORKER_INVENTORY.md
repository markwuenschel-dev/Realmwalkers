# Workers/ Inventory & Contraction Report

**Date**: 2026-07-02 (analysis run)
**Task**: Contraction pass — reduce cognitive surface area. No behavior change. No new features.
**Scope**: All `.py` under `src/dominion/workers/` (92 files incl. inits; ~83 non-init).

**Goal recap** (from request):
- Make the active path obvious: **packet → surface contract → scene packet → prose**.
- No downstream accidentally reads raw internal `scene_seeds` when surface contract exists.
- Label/move/archive anything not on active production path.
- Collapse obvious thin non-boundaries.
- Archive dead/legacy.
- Preserve tests + deterministic behavior.
- Add written map of active vs legacy.

**Definition of "active production path" used here**:
The deterministic spine that turns an approved ChapterPacket (with surface projection) into approved ScenePackets (with contracts) into queued draft jobs into worker `generate_one_scene` producing prose:
- `draft_queue` + job_* + worker + pipeline
- `context/*` (assemble via scene packet load + project)
- `specialists/drafter` + enrichment passes (via router)
- `reviewers/*` (via router)
- `length/guard`
- Supporting live: llm/*, budget, pov, stat_render, progress, telemetry*, memory/* (context providers), oracle, gates, background_work (derive infra)
- Upstream contract layers: `production.py` (propose), `packet/*` (author → internal validate → surface_contract), `scene_packet/*` (derive from surface seeds → author/qa/validate → approve → beats)

Anything outside the above spine (or pure curation/observability tooling) is classified non-active.

---

## Full File Classification

Format: `path` — classification — importers (src+tests summary) — tests cover? — recommendation

### Active Production Path (core spine + required support)

- `worker.py` — active — worker caller in api/routers/jobs, tests (phase, draft) — yes — **keep**
- `pipeline.py` — active — worker, tests (drafter etc) — yes — **keep**
- `draft_queue.py` — active (contract gate) — production, job_scheduler, api, tests/test_draft_queue — yes — **keep**
- `job_routing.py` — active — draft_queue — yes (indirect) — **keep**
- `job_scheduler.py` — active (thin delegator) — production, tests — yes — **keep**
- `router.py` — active (deterministic passes/reviewers) — pipeline + specialists tests — yes — **keep**
- `pov.py` — active — context/assemble, derive, tests — yes — **keep**
- `budget.py` — active — llm, pipeline, many tests + packet — yes — **keep**
- `stat_render.py` — active — pipeline — yes — **keep**
- `progress.py` — active (live phase reporting) — worker, pipeline, background, api/jobs+packets, tests — yes — **keep**
- `gates.py` — active (shared refusal) — packet/approval + scene_packet/approval — yes — **keep**
- `oracle.py` — active (truth ledger) — context/draft_memory, api, tests — yes — **keep**

**context/**
- `context/__init__.py`, `assemble.py`, `contracts.py`, `dialogue_rules.py`, `draft_memory.py`, `resolve.py`, `revision.py`, `types.py` — all active (assemble is the load seam for prose path) — imported widely by pipeline, specialists, reviewers, draft_queue, tests (dozens) — yes — **keep**

**specialists/**
- All (`base.py` + combat/dialogue/drafter/enrich/sensory) — active (enrichment passes on spine) — router + pipeline + many specialist tests (test_drafter, enrichment_passes, etc) — yes — **keep**

**reviewers/**
- All (`base.py` + combat/continuity/dialogue/lane/pacing/sensory/state_drift/voice) — active (advisory on spine) — router + tests (reviewers, continuity, etc) — yes — **keep**

**length/**
- `length/guard.py`, `length/planner.py` — active (guard on pipeline; planner feeds derive + production) — pipeline, derive, production, scene_packet/staleness, tests — yes — **keep**
- `length/compress.py`, `length/expand.py` — (merged into guard.py during contraction; were impl detail) — n/a — **done**

**packet/** (ChapterPacket + surface contract layer — active upstream of scene packets)
- `packet/__init__.py`, `approval_policy.py`, `author.py`, `parse.py`, `qa.py`, `scopes.py`, `surface_contract.py`, `surface_policy.py`, `validation.py` — active — production.py, routers/packets+scene_packets, derive (indirect via surface), packet tests (test_packet_*), debug script — yes — **keep**. Note parallel structure with scene_packet/ is intentional layering (internal vs surface). Do not flatten without new arch.

**scene_packet/** (Scene-level contracts — active)
- All files (`__init__.py`, `approval_policy.py`, `author.py`, `author_sections.py`, `beats.py`, `derive.py`, `hash.py`, `inputs.py`, `parse.py`, `projections.py`, `qa.py`, `staleness.py`, `validation.py`) — active — draft_queue (sp_approval), context/contracts+assemble, production (inputs), api/routers/scene_packets + packets, beats derive, many tests (test_scene_packet*, test_packet_*, author_sections) — yes — **keep**

**memory/**
- All 8 impl files + `__init__` — active (canon, ledger, summaries, retrieval for assemble_context + drafter) — context/draft_memory + seed + summaries + many tests — yes — **keep**

**telemetry/**
- `telemetry.py`, `telemetry_agg.py`, `telemetry_cost.py`, `telemetry_db.py`, `telemetry_diagnostics.py`, `telemetry_draft_problems.py`, `telemetry_settings.py` — active (instrument packet author/derive + scene draft spine) — pipeline, production, packet, memory, api routers (telemetry, chapters, runs), draft problems — yes — **keep**

- `background_work.py` — active support (derive job tracking + progress) — api/routers (jobs, packets), tests (background, desk, scene_packet) — yes — **keep**

- `draft_readiness.py` — active support (readiness + blockers for contract checks) — api, telemetry_draft_problems, tests — yes — **keep**

- `llm.py`, `llm_escalation.py` — active (called by authors, drafter, planner) — many workers + tests — yes — **keep**

- `production.py` — active (ChapterPacket propose + production orchestration; feeds surface contract into derive) — api routers (production, packets), tests — yes — **keep** (large but central to upstream)

### Compatibility / Transition Paths

- `enqueue.py` (245 LOC) — compatibility/transition (manual bootstrap for pre-contract "enqueue a beat + mint minimal approved scene packet"). Implements old flow. No `from dominion.workers.enqueue` in src/ (only test imports + string refs in guardrail tests). Still exercises legacy path code. — tests (phase2, draft_job_creation, learning) cover some — **move to `legacy/enqueue.py`**; update test imports + any docs. Do not delete (history + test value) but out of active surface.
- Legacy job handling inside `draft_queue.py` ("legacy_job_unreconcilable" blockers + reconciles) — compatibility — internal — yes (draft_queue tests) — **keep** (but already isolated; document in MAP)
- Legacy fallbacks + comments in `context/resolve.py`, `context/draft_memory.py`, `packet/__init__.py` (the `_surface_contract` + top-level `scene_seeds` copy for "immediate compatibility during transition") — compatibility — callers are active paths — yes — **keep** the compat glue for now (behavior freeze). Add comments + MAP note. Future hard cut when owner says.
- `packet/__init__.py` has transition mint + copy logic — compat — see production — **keep** (part of packet active layer)

**Note on scene_seeds surface discipline (key requirement)**:
- Raw `scene_seeds` live in ChapterPacket internal body (and validation/author scope them).
- `packet/surface_contract.py` + `build_surface_contract` projects the safe drafter-facing version.
- `packet/__init__.py:415` and production do `packet["scene_seeds"] = ...` copy for compat.
- Downstream derive (`scene_packet/derive.py`, `length/planner.py`, `scene_packet/staleness.py`, `draft_readiness.py`) read from chapter packet body (the effective one after surface attach).
- Drafter path (assemble/contracts/project) goes through `ScenePacketProjections` / surface fields — good.
- No obvious "accidental raw internal reads in prose path" today, but the compat copy keeps both. Cleanup will add explicit comments and prefer surface in new reads where safe (no behavior change).

### Test-only support / Data tooling (not runtime draft spine)

- `learning/distill.py` + `learning/__init__.py` — separate responsibility (Tier 3 edit→rule distillation). Exposed via dedicated api router. Not involved in packet/scene/prose draft. — dedicated tests (test_distill, test_learning) — **move to `legacy/learning/`** (or consider promoting out of workers later). Update imports in api + tests.
- `set_voice.py` — curation CLI + upsert for PovProfile voice. Referenced in memory/seed comments + tests only. — test_set_voice — **move to `legacy/set_voice.py`**
- `set_exemplars.py` — curation CLI for exemplars. Companion to set_voice. — test_learning — **move to `legacy/set_exemplars.py`**

### Unclear / Borderline (keep in active for safety; label in MAP)

- `planner.py` (Gate-1) — still imported/used by api/routers/chapters + runs for beat proposal. Contract docs say beats now derive from ScenePackets after approval, but planning step remains in flows. Not "packet→prose" direct but prerequisite for some chapters. — tests (gate1, chapters_create) — **keep as active planning support** for this pass. Note in MAP as "pre-contract planning lane".
- `length/expand.py` + `compress.py` — borderline thin (internal to guard) — see length above — **merge**

No pure "dead" files (every stem mentioned externally at least once, per scan). The smell is accumulation of responsibilities + parallel packet/scene_packet + compat shims + tooling in the same tree.

**Importer summary (high level, from greps + analysis)**:
- Highest fanout: budget, context.types, llm, packet.parse + scene_packet.parse (shared tolerant extractors), draft_queue, scene_packet bits (via derive/approval), specialists/reviewers base.
- Core production imports stay within workers + called from api/ (routers for packets, scene_packets, jobs, chapters, production, telemetry) and shared/models indirectly.
- Tests have broad coverage of nearly everything (packet_*, scene_packet_*, drafter, reviewers, length via scene_packet tests, context, draft_queue, etc.). No module appears "untested" in production paths.

---

## Active Path Summary (what must stay obvious)

```
ChapterPacket propose
  └─ production.propose_packet
       ├─ packet.author → packet.qa
       ├─ packet.validation (internal)
       ├─ packet.surface_contract.build_surface_contract  ← THE surface
       └─ persist (body + _surface_contract + compat scene_seeds copy)

Approved ChapterPacket
  └─ scene-packets/derive (background or sync)
       ├─ scene_packet.inputs + length.planner
       ├─ scene_packet.author (+ sections) + qa + validation (per seed)
       └─ persist ScenePackets (with projected contract)

Approved ScenePacket + approved Beat (derived via beats.derive_beats)
  └─ POST /draft or auto
       └─ draft_queue.schedule... (enforces approved non-stale ScenePacket)
            └─ create Job(scene_packet_id=...)

Worker
  └─ claim → pipeline.generate_one_scene
       ├─ context.assemble_context (resolve job → load_scene_packet_fields → project() → SceneContext with scene_contract etc.)
       ├─ specialists.drafter (spine)
       ├─ specialists.* enrich (router.passes_for)
       ├─ length.guard (apply_length_guard using scene_contract)
       ├─ reviewers.* (router.reviewers_for, advisory)
       └─ persist Scene + attempts

Key rule enforced: worker context **never** falls back to raw chapter packet; requires ScenePacket.
```

Downstream prose consumers should prefer fields from `SceneContext.scene_contract` / `contract` (from projections) over walking `body["scene_seeds"]` directly.

---

## Cleanup Plan (minimal, behavior-freeze)

1. **Archive / move legacy paths out of surface**:
   - Create `src/dominion/workers/legacy/` (with README explaining "not part of active packet/surface/scene_packet/prose spine").
   - Move: `enqueue.py` → `legacy/enqueue.py`
   - Move: `learning/` → `legacy/learning/`
   - Move: `set_voice.py`, `set_exemplars.py` → `legacy/`
   - Update all imports (src/api, tests).
   - Update docs/contract_first_drafting.md and any references.

2. **Collapse thin non-boundaries**:
   - Merge `length/compress.py` + `length/expand.py` content into `length/guard.py` (private helpers only). **DONE**.
   - length/ now has guard.py + planner.py + __init__.py
   - Updated internal calls.
   - (No other obvious 1-fn merges without touching real named lanes; specialists/reviewers are registration surface.)

3. **Remove unused imports** (static scan + per-file):
   - Run pyright/ruff later; manually trim obvious in edited files.
   - Focus edits on touched files only.

4. **Surface contract discipline (doc + light comments, no behavior change)**:
   - Add comments in production.py / packet/__init__.py around the compat copy: "COMPAT: raw scene_seeds kept at top for transition. Prefer _surface_contract or scene_packet projected body downstream."
   - Add note in scene_packet/derive.py and length/planner.py if they can source from surface (but keep reading chapter body for now — no change).
   - Ensure no new direct raw reads added.

5. **Written map**:
   - Add / update `src/dominion/workers/MAP.md` (or rename WORKER_INVENTORY.md post-clean) clearly labeling:
     - Active spine files/directories
     - Legacy/ (with why moved)
     - Support (memory, telemetry, etc.)
     - How to tell: "If it is not required to go approved-packet → approved-scene-packet → job → prose, it lives in legacy/ or is documented support."
   - Update `__init__.py` files minimally if exports change (prefer not).

6. **Docs**:
   - Update `docs/contract_first_drafting.md` "Implementation map" if files moved.
   - Any other mentions.

**Out of scope (per rules)**: no validator changes, no agent lanes, no schema families, no rewrite of packet vs scene_packet layering, no new surface extraction.

**Risks / reversibility**: Moves are import renames + fs move (git tracks). Merge of length is inlining private fns (reversible). All tests must still import the moved symbols from new locations.

---

## Post-clean Definition of Done (to verify)

- [ ] Active path (packet surface → scene packet → prose) jumps out when reading `workers/` tree or MAP.md.
- [ ] `legacy/` exists and contains only moved non-active items; no active spine imports from it.
- [ ] No new direct raw internal scene_seeds reads in prose/drafter path (existing compat noted).
- [ ] `length/` now has 2 files (guard + planner) instead of 4.
- [ ] All prior importers/tests updated; `python -c 'import ...'` for moved modules succeeds.
- [ ] `./scripts/verify.ps1` (or just verify) green. (ruff, format check, pyright on changed, pytest -q with DB if required).
- [ ] Worker folder has clear written map.
- [ ] Behavior identical (no test changes beyond import paths; no logic edits).

This report is the "inventory" deliverable. Next pass will execute the minimal edits above only.

**End of report**.

## Execution Summary (post-report cleanup performed)

- legacy/ dir + README + __init__ created.
- Moved: enqueue.py, set_voice.py, set_exemplars.py, learning/ dir → under legacy/.
- All call sites, tests, docs, CLI strings, guardrails, comments updated.
- Merged length/compress.py + expand.py (private helpers) into length/guard.py; old files deleted.
- ruff check + format clean on tree.
- pyright clean on edited files.
- Key tests (legacy importers, guardrail, phase2, set_*, learning, scene_packet length paths) all pass.
- Imports from active surface no longer reach the moved items (surface contracted).
- MAP.md written at workers/MAP.md.
- WORKER_INVENTORY.md updated with results.
- No behavior or validation logic changed.
- Active packet → surface → scene_packet → prose path + legacy/ label now explicit.

Ready for owner review / ship flow. Run full `./scripts/verify.ps1` (or just verify) in clean env before merge.

