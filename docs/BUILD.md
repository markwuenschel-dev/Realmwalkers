# Building Dominion Realm

Developer guide for the writing system. For the project itself see the [root README](../README.md);
for the full architecture and rationale see [`DESIGN.md`](DESIGN.md).

The system is a human-gated, scene-by-scene **workflow, not an agent**: a worker drafts one scene,
writes it to Postgres as `pending_review`, and exits. Nothing runs between approvals.

## Architecture

```
React (Vite) ──HTTP──> FastAPI ──> Postgres (+pgvector) <── Python worker (drafts scenes)
  review inbox          thin boundary   source of truth        the ~minutes of real work
```

- **Coordination is deterministic code** (`workers/router.py`): a lookup table + a loop decide which
  passes run. No LLM sits in the control path.
- **Reviewers advise; they never block.** The human inbox is the only gate.
- **Versioning is rows, not Git branches.** A revision inserts a new `scenes` row and supersedes its
  parent. The authored canon under `series/` (+ this book's planning under `book1/`) stays in Git; the generated manuscript lives in Postgres.

## Layout (system portion of the monorepo)

```
src/dominion/
  shared/     config, enums, async DB session, ORM schema (models.py), Pydantic DTOs (schemas.py)
  api/        FastAPI app + routers (health, scenes, reviews, runs, beats, chapters, books)
  workers/    worker.py (claim→draft→exit), pipeline.py, planner.py, router.py, context.py, oracle.py,
              budget.py, llm.py, enqueue.py
              specialists/  drafter + combat/sensory/dialogue enrichment passes
              reviewers/    continuity (always) + pacing/voice/state-drift
              memory/       canon_rag, summaries, ledger, seed
frontend/     Vite + React + TS review app (Inbox → Scene → continuity panel)
scripts/      init_db.py
tests/        deterministic router tests + import smoke
docs/         DESIGN.md (spec), BUILD.md (this file)
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

A `justfile` wraps these (`just install`, `just db-up`, `just api`, `just worker-once`, …).

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

The concrete, checkable execution plan for the current effort (finish Phase 3, then wire the Writers'
Desk to the live API) lives in [`ROADMAP.md`](ROADMAP.md).

1. **One approved scene, end to end** — implement the Drafter + continuity reviewer; draft a scene
   from a hand-written beat, review it in the inbox, approve it.
2. **Auto-advance + memory** — RAG over `series/canon/`, per-POV + omniscient summaries, the stat
   ledger, pause-each auto-enqueue of the next scene.
3. **Enrichment specialists** — combat/sensory/dialogue passes + their review-lane reviewers, by beat
   tags. Pacing/voice/state-drift reviewers already live.
4. **`draft_ahead` + parallelism** — provisional ledger, multiple workers.

## Checks (all currently clean)

```bash
pytest -q              # tests
ruff check src tests   # lint (F-codes catch real bugs: undefined names, unused vars)
mypy src               # strict type check
```

DB-backed tests get a real database from the `db_factory` fixture (`tests/conftest.py`), which forces
a dedicated `dominion_test` DB, creates the `vector` extension + schema, and truncates between tests.
Locally, if Postgres isn't running those tests **skip** (they're opt-in) — run `just db-up` first to
exercise them. Tests that don't need a DB run regardless.

## Continuous integration

`.github/workflows/ci.yml` runs on every push to `main` and every pull request, in three parallel jobs:

| Job | What runs | Notes |
|---|---|---|
| **lint + types** | `ruff check src tests`, `mypy src` (strict) | one interpreter; config targets py312 |
| **tests** | `pytest -q` against a real `pgvector/pgvector:pg16` service | matrix: Python 3.12 (the supported floor) + 3.14 (the pinned dev version) |
| **frontend build** | `npm ci && npm run build` (`tsc -b && vite build`) | Node 20; reproducible install from `package-lock.json` |

Installs are reproducible: backend via `uv sync --frozen` (honours `uv.lock`), frontend via `npm ci`.
The CI sets **`DOMINION_REQUIRE_DB=1`**, which flips the conftest "Postgres unreachable → skip" into a
hard failure — so a broken DB service can never produce a falsely-green run (the gap this CI closes).
`ANTHROPIC_API_KEY` is set to a deliberately-fake value; tests mock the model, and a real call would
fail fast rather than spend tokens.

Recommended: protect `main` so these checks are **required** to merge (Settings → Branches, or
`gh api -X PUT repos/:owner/:repo/branches/main/protection …`). The repo already merges via PRs, so
required checks make the gate real.
