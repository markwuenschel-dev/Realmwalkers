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
  api/        FastAPI app + routers (health, scenes, reviews, runs)
  workers/    worker.py (claim→draft→exit), pipeline.py, router.py, context.py, oracle.py,
              budget.py, llm.py, enqueue.py
              specialists/  drafter + combat/sensory/dialogue enrichment passes
              reviewers/    continuity (always) + pacing/voice
              memory/       canon_rag, summaries, ledger, seed (manuscript -> approved prior state)
frontend/     Vite + React + TS review app (Inbox → Scene → continuity panel)
scripts/      init_db.py
tests/        deterministic router tests + import smoke
docs/         DESIGN.md
```

## Quickstart (bash)

```bash
cp .env.example .env                 # fill in ANTHROPIC_API_KEY
docker compose up -d                 # Postgres + pgvector on :5432
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python scripts/init_db.py            # create extension + tables

uvicorn dominion.api.main:app --reload --port 8000   # terminal 1: API
cd frontend && npm install && npm run dev            # terminal 2: review app on :5173
```

A `justfile` wraps these (`just install`, `just db-up`, `just api`, `just worker-once`, …) if you use `just`.

> Drafting one scene works end to end: enqueue a beat
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
| React inbox, scene review, continuity panel, history, manuscript, plan | |

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
