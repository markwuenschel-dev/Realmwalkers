# Dominion Realm — novel-writing system 

A human-gated, scene-by-scene writing system for *The Dominion Realm*. It is a **workflow, not an
agent**: a worker drafts exactly one scene, writes it to Postgres as `pending_review`, and exits.
Nothing runs between approvals — so there is nothing to boot and nothing to re-verify. You approve
(or edit, or reject) each scene from the Writers' Desk (a Next.js app). Full rationale:
[`docs/DESIGN.md`](docs/DESIGN.md).

## Architecture

```
Next.js (BFF) ──HTTP──> FastAPI ──> Postgres (+pgvector) <── Python worker (drafts scenes)
 Writers' Desk          thin boundary   source of truth        the ~minutes of real work
```

- **Coordination is deterministic code** (`workers/router.py`): a lookup table + a loop decide which
  passes run. No LLM sits in the control path — that seat is what spiraled in the previous build.
- **Reviewers advise; they never block.** The human inbox is the only gate.
- **Versioning is rows, not Git branches.** A revision inserts a new `scenes` row and supersedes its
  parent. Runtime exhaust (logs, job state) never enters the repo.

## Layout

```
src/dominion/
  shared/     config, enums, async DB session, ORM schema (models.py), Pydantic DTOs (schemas.py)
  api/        FastAPI app + routers (health, scenes, reviews, runs, books, chapters, beats,
              packets + scene_packets [contract-first gates], jobs [browser draft trigger],
              production, world [characters/canon], threads, markup, telemetry, settings, learning)
  workers/    worker.py (claim→draft→exit), pipeline.py, router.py, draft_queue.py (contract-first
              scheduler + requeue), planner.py, oracle.py, budget.py, llm.py, production.py, telemetry
              packet/       ChapterPacket author + QA + approval policy (contract-first gate 1)
              scene_packet/ ScenePacket derive/author/QA, staleness, beat derivation
              context/      POV-scoped context assembly    length/  length planner + guard
              specialists/  drafter + enrichment passes    reviewers/  continuity (always) + lanes
              memory/       canon_rag, summaries, ledger, seed      learning/  edit distillation
frontend/     Next.js (App Router) + TS — the Writers' Desk (Inbox, Scene review, Chapters, Packets,
              Diff, Manuscript, Ledger, Production, Telemetry, Settings). Screens read live data via
              desk/api/; the Next BFF proxies same-origin /api/desk/* to FastAPI.
scripts/      init_db.py, export_openapi.py, verify.sh, ci_pyright_changed.sh
tests/        pytest suite (router, packets, draft queue, API) — runs against real Postgres in CI
docs/         DESIGN.md, BUILD.md, ROADMAP.md, DEPLOY.md, contract_first_drafting.md
```

## Running it

The whole app ships as a **single container** (Next.js + FastAPI), deployed as one service in the
shared AWS box's Docker Compose stack (behind Caddy) — see
[`docs/DEPLOY.md`](docs/DEPLOY.md). The browser loads the desk from Next and calls same-origin
`/api/desk/*`, which the Next BFF proxies to FastAPI, so there's no separate API host and no CORS.

Backend gates run via `just verify` (or `scripts/verify.sh`); the frontend gates (`pnpm typecheck` /
`lint` / `format:check` / `test`) run from `frontend/`.

> **The whole loop is browser-driven — no terminal needed — and it is contract-first** (see
> [`docs/contract_first_drafting.md`](docs/contract_first_drafting.md)). In the Writers' Desk: create a
> book, outline a chapter, and the system proposes a **ChapterPacket** (the chapter's knowledge
> contract) for you to edit and approve; **ScenePackets** are then derived and approved per scene,
> beats derive from the approved ScenePackets, and **Draft Chapter** (`POST /chapters/{id}/draft`)
> queues one job per scene, each stamped with its `scene_packet_id`. Jobs drain in a single-flight
> background task (`POST /jobs/draft-next`); review/approve/revise from the Inbox. A draft runs *only*
> when you act, so the "nothing runs between approvals" guarantee holds. Beat-first drafting (approve
> beats → queue) is disabled, and the legacy `workers/enqueue` CLI was removed — for scripting, hit
> the API, or run `python -m dominion.workers.worker --once` to drain one already-queued job. The
> enrichment passes and their review lanes are live; a pass that fails still fails *soft*
> (`PassError`) — the drafted spine lands in the inbox, flagged, rather than hard-failing the job. The
> only remaining worker stub is Phase 4 (`draft_ahead` + parallelism).

## State: what's real vs. scaffolded

Phases 1–3 are built and tested. The only stub left in the worker tree is Phase 4 (`draft_ahead` +
parallelism, deferred until throughput hurts); a failed enrichment pass still fails *soft* (`PassError`
→ the spine lands, flagged) rather than hard.

| Real now | Stubbed |
|---|---|
| Full ORM schema + Pydantic DTOs | `draft_ahead` + provisional ledger + parallel workers (Phase 4) |
| Drafter — POV-voiced spine + revise prompt | |
| Combat / sensory / dialogue **enrichment passes** (transform-only, stat-safe, soft-fail) | |
| Combat / sensory / dialogue **review lanes** (advisory, tag-gated) | |
| Continuity reviewer (hard-number + POV-knowledge asymmetry) | |
| Pacing / voice / state-drift reviewers (advisory, token-gated) | |
| Deterministic router (`passes_for` / `reviewers_for`) — tested | |
| Worker loop: atomic claim, wall-clock + token budget, claim→draft→exit | |
| Canon RAG (`retrieve` + `ingest_path`) over pgvector | |
| Per-POV + omniscient rolling summaries; stat ledger commit-on-approval | |
| Oracle read-authority over `character_state` | |
| FastAPI: health, scenes, reviews (decision + continuity-resolve), runs, beats, chapters, books | |
| Approve/deny/revise + hand-edit; ledger + summary hooks; `pause_each` auto-advance | |
| Browser draft trigger (`POST /jobs/draft-next` + `GET /jobs/status`) — single-flight background drain | |
| World endpoints: `/books/{id}/characters`, `/books/{id}/canon`, `Thread`/`ThreadBeat` CRUD | |
| Writers' Desk fully wired to the live API (no fixtures) — Inbox, Scene, Chapters, Versions, Manuscript, Ledger | |
| In-browser contract-first planning (ChapterPacket → ScenePackets → derived beats → Draft Chapter) | |
| Scene markup: human Annotations (margin notes) + track-changes Suggestions (accept/reject → folded into `edited_prose` on approve) | |

## Build phases (DESIGN §14)

The phases below shipped. The current plan — the post-scrub drafting unblock on the contract-first
packet flow — is tracked in [`docs/ROADMAP.md`](docs/ROADMAP.md).

1. ✅ **One approved scene, end to end** — Drafter + continuity reviewer; draft from a beat, review in
   the inbox, approve.
2. ✅ **Auto-advance + memory** — RAG over canon, per-POV + omniscient summaries, the stat ledger,
   `pause_each` auto-enqueue, and a manuscript seed-importer (`dominion-seed`) that loads drafted
   scene files as `approved` prior state + rebuilds the canon index. *(Operational step remaining:
   run it on the real manuscript and fold summaries — needs `ANTHROPIC_API_KEY`.)*
3. ✅ **Enrichment specialists** — combat/sensory/dialogue passes + their review-lane reviewers, routed
   by beat tags. Pacing/voice/state-drift reviewers already live.
4. ⬜ **`draft_ahead` + parallelism** — provisional ledger, batch invalidation, multiple workers.
   Deferred until throughput actually hurts.

## Dev

```bash
just verify                          # all backend gates, matching CI (see scripts/verify.sh)
pytest -q                            # tests
ruff check src tests                 # lint (F-codes catch real bugs: undefined names, unused vars)
bash scripts/ci_pyright_changed.sh   # types — pyright over changed files, same as the CI static job
```
