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
  parent. The authored canon under `novel/` stays in Git; the generated manuscript lives in Postgres.

## Layout (system portion of the monorepo)

```
src/dominion/
  shared/     config, enums, async DB session, ORM schema (models.py), Pydantic DTOs (schemas.py)
  api/        FastAPI app + routers (health, scenes, reviews, runs)
  workers/    worker.py (claim→draft→exit), pipeline.py, router.py, context.py, oracle.py,
              budget.py, llm.py, enqueue.py
              specialists/  drafter + combat/sensory/dialogue enrichment passes
              reviewers/    continuity (always) + pacing/voice
              memory/       canon_rag, summaries, ledger
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

> Drafting one scene (`python -m dominion.workers.enqueue --book "Dominion Realm" --chapter 1 --scene 1`
> then `python -m dominion.workers.worker --once`) is **Phase 1** — the Drafter raises
> `NotImplementedError` until then, by design (no fake prose written to the DB).

## State: what's real vs. scaffolded

| Real now | Stubbed (raises `NotImplementedError`, phase-tagged) |
|---|---|
| Full ORM schema + Pydantic DTOs | Drafter (Phase 1) |
| Deterministic router (`passes_for` / `reviewers_for`) — tested | Continuity reviewer (Phase 1) |
| Worker loop: atomic job claim, wall-clock budget, claim→draft→exit | Enrichment passes: combat/sensory/dialogue (Phase 3) |
| Token-budget + LLM wrapper (usage-charged) | Pacing/voice reviewers (Phase 3) |
| Oracle read-authority over `character_state` | Canon RAG / summaries / ledger (Phase 2) |
| FastAPI: health, `/scenes/pending`, `/scenes/{id}` | `/runs` + continuity-resolve endpoints (Phase 1/2) |
| Decision endpoint: approve/deny/revise + hand-edit | Memory hooks on approval (Phase 2) |
| React inbox, scene review, continuity panel | History/version browsing (Phase 2) |

## Build phases (DESIGN §14)

1. **One approved scene, end to end** — implement the Drafter + continuity reviewer; draft a scene
   from a hand-written beat, review it in the inbox, approve it.
2. **Auto-advance + memory** — RAG over `novel/canon/`, per-POV + omniscient summaries, the stat
   ledger, pause-each auto-enqueue of the next scene.
3. **Enrichment specialists** — combat/sensory/dialogue passes + pacing/voice reviewers, by beat tags.
4. **`draft_ahead` + parallelism** — provisional ledger, multiple workers.

## Checks (all currently clean)

```bash
pytest -q              # tests
ruff check src tests   # lint (F-codes catch real bugs: undefined names, unused vars)
mypy src               # strict type check
```

Biggest current gap: nothing exercises code that touches Postgres yet (no test database wired).
Closing that — a pytest fixture that stands up an ephemeral Postgres — is the first thing to add
alongside Phase 1, so a scene landing in the DB is *proven*, not just plausible.
