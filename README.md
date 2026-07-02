# Dominion Realm — novel-writing system 

A human-gated, scene-by-scene writing system for *The Dominion Realm*. It is a **workflow, not an
agent**: a worker drafts exactly one scene, writes it to Postgres as `pending_review`, and exits.
Nothing runs between approvals — so there is nothing to boot and nothing to re-verify. You approve
(or edit, or reject) each scene from a small React inbox. Full rationale: [`docs/DESIGN.md`](docs/DESIGN.md).

## Architecture

```
React (Vite) ──HTTP──> FastAPI ──> Postgres (+pgvector) <── Python worker (drafts scenes)
  review inbox          thin boundary   source of truth        the ~minutes of real work
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
              jobs [browser draft trigger], world [characters/canon], threads, markup [notes/suggestions])
  workers/    worker.py (claim→draft→exit), pipeline.py, router.py, context.py, oracle.py,
              budget.py, llm.py, enqueue.py
              specialists/  drafter + combat/sensory/dialogue enrichment passes
              reviewers/    continuity (always) + pacing/voice
              memory/       canon_rag, summaries, ledger, seed (manuscript -> approved prior state)
frontend/     Vite + React + TS — the Writers' Desk (Inbox, Scene review, Chapters, Versions,
              Manuscript, Ledger). All screens read live data via desk/api/ (client + polling data
              layer); no fixtures. src/legacy/ is the superseded review app, kept for reference.
scripts/      init_db.py
tests/        deterministic router tests + import smoke
docs/         DESIGN.md
```

## Running it

The whole app ships as a **single container** (Next.js + FastAPI) deployed on Railway — see
[`docs/DEPLOY.md`](docs/DEPLOY.md). The browser loads the desk from Next and calls same-origin
`/api/desk/*`, which the Next BFF proxies to FastAPI, so there's no separate API host and no CORS.

Backend gates run via `just verify` (or `scripts/verify.sh`); the frontend gates (`pnpm typecheck` /
`lint` / `format:check` / `test`) run from `frontend/`.

> **The whole loop is now browser-driven — no terminal needed.** In the Writers' Desk: create a book,
> outline a chapter (the planner proposes beats), approve them, and the API drafts each scene in a
> single-flight background task (`POST /jobs/draft-next`); review/approve/revise from the Inbox. A draft
> runs *only* when you act, so the "nothing runs between approvals" guarantee holds. The CLI path still
> works for scripting: enqueue a beat
> (`python -m dominion.workers.enqueue --book "Dominion Realm" --chapter 1 --scene 1`) then
> `python -m dominion.workers.worker --once`. The combat/sensory/dialogue enrichment passes and their
> review lanes are live; a pass that fails still fails *soft* (`PassError`) — the drafted spine lands in
> the inbox, flagged, rather than hard-failing the job. The only remaining worker stub is Phase 4
> (`draft_ahead` + parallelism).

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
| In-browser gate-1 planner (create book → outline chapter → approve beats → draft) | |
| Scene markup: human Annotations (margin notes) + track-changes Suggestions (accept/reject → folded into `edited_prose` on approve) | |

## Build phases (DESIGN §14)

The current execution plan — finishing Phase 3, then wiring the Writers' Desk to the live API — is
tracked in [`docs/ROADMAP.md`](docs/ROADMAP.md).

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
pytest -q              # tests
ruff check src tests   # lint (F-codes catch real bugs: undefined names, unused vars)
mypy src               # strict type check — currently clean
```
