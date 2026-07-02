# workers/ — Active Surface Map

This file makes the production surface obvious after the contraction pass.

## The One Active Path (packet → surface contract → scene packet → prose)

1. **Upstream contract (ChapterPacket)**
   - `production.py:propose_packet`
   - `packet/` (author → qa → internal validation → `surface_contract.build_surface_contract`)
   - Persist ChapterPacket (body carries internal + `_surface_contract`; compat seeds copy is noted)

2. **Scene contract layer**
   - `scene_packet/derive.py` (length planner + per-scene author/sections + qa + validation)
   - `scene_packet/approval_policy.py`, projections, inputs, parse, beats, etc.
   - `scene_packet/staleness.py`
   - Approve → `beats.derive_beats`

3. **Queue**
   - `draft_queue.py` (the single contract gate — every job must have approved non-stale ScenePacket)
   - `job_scheduler.py`, `job_routing.py`

4. **Draft execution (the worker spine)**
   - `worker.py` (claim + run_once + bounded generate)
   - `pipeline.py:generate_one_scene`
     - `context.assemble_context` → `contracts.load_scene_packet_fields` → `scene_packet.projections.project`
     - `specialists.drafter`
     - enrichment via `router.passes_for`
     - `length.guard` (uses scene_contract)
     - advisory `router.reviewers_for`
   - persist + telemetry + DraftAttempts

**Rule**: if changing something does **not** affect the above flow from an approved packet all the way to prose, it should not live at the top level of the active `workers/` surface.

## Active directories / files (keep obvious)

- `worker.py`, `pipeline.py`
- `draft_queue.py`, `job_*`
- `router.py`
- `context/` (assemble + contracts + types + supporting)
- `specialists/` (the passes)
- `reviewers/` (the lanes)
- `length/` (only `guard.py` + `planner.py` now)
- `packet/` (Chapter + surface contract machinery)
- `scene_packet/` (derive + per-scene contracts)
- `production.py`
- `memory/`, `oracle.py` (context providers)
- `llm*`, `budget.py`
- `telemetry*` (all)
- `progress.py`, `pov.py`, `stat_render.py`, `gates.py`, `background_work.py`, `draft_readiness.py`

## legacy/

Moved here (non-active surface):
- `enqueue.py` (pre-contract manual bootstrap)
- `learning/` (Tier 3 distillation, separate API surface)
- `set_voice.py`, `set_exemplars.py` (curation CLIs)

See `legacy/README.md`.

## Notes on surface_contract vs raw scene_seeds

- Authoring + validation operate on internal (may contain forbidden names etc. in seeds).
- `surface_contract` produces the drafter-safe projection.
- Derive / planner / readiness still read chapter packet body (with attached surface + compat seeds) — this is the transition compat layer.
- The **prose path** (assemble → drafter) receives `scene_contract` via projections. Do not add new code that walks raw ChapterPacket `scene_seeds` for drafter input.
- The compat copy in `packet/__init__.py` and `production.py` is labeled; do not expand it.

## Other notes
- `planner.py` (Gate-1) is still wired for initial beat proposals in some flows. Documented as pre-packet planning.
- No behavior changes in this contraction. All tests updated only for moved modules.
- Before adding anything new to workers/, ask: "Does this sit on the active packet/surface/scene-packet/prose spine?"

See `WORKER_INVENTORY.md` for the full classification table from the contraction analysis.
