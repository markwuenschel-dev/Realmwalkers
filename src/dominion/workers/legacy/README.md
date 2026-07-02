# workers/legacy/

**Non-active surface.**

Code moved here during contraction pass (2026-07) to reduce cognitive load in the primary worker tree.

## What belongs here
- Pre-contract / beat-first bootstrap and enqueue paths (`enqueue.py`).
- Separate non-drafting responsibilities (learning distillation, voice/exemplar curation CLIs).
- Legacy transition shims once they are cut (currently kept in place for behavior freeze).

## What does NOT belong here
- Anything required for: approved ChapterPacket → surface contract → approved ScenePacket → draft job → `pipeline.generate_one_scene` → prose.
- Memory, telemetry, llm, context, specialists, reviewers, length guard, packet/scene_packet layers, draft_queue, production orchestration, etc.

## Active path reminder
See `../WORKER_INVENTORY.md` (or the MAP) and `docs/contract_first_drafting.md`.

Imports from here are **not** part of the production drafting contract. If you find yourself reaching into legacy/ for new work, stop and use the surface contracts.

## Reversal
Files can be moved back if classification was wrong. Update this note + the parent inventory + all call sites.

This directory reduces the "junk drawer" surface. The active files in the parent `workers/` (and its packet/, scene_packet/, context/, specialists/, reviewers/, length/, memory/, etc.) are the current production surface.
