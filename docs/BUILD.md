# Building Dominion Realm

Developer guide for the writing system. For the project itself see the [root README](../README.md);
for the full architecture and rationale see [`DESIGN.md`](DESIGN.md).

The system is a human-gated, scene-by-scene **workflow, not an agent**: a worker drafts one scene,
writes it to Postgres as `pending_review`, and exits. Nothing runs between approvals.

## OpenAI generation

OpenAI text generation uses the Python SDK's Responses API behind `dominion.workers.llm.complete()`.
The deterministic queue, approval gates, budgets, and telemetry remain provider-neutral; Gemini and
xAI continue through their OpenAI-compatible Chat Completions endpoints. OpenAI authoring requests
explicitly set `store: true` (the account's default storage policy), so canon and manuscript prompts
may be retained by OpenAI. Do not enable provider-hosted tools, Agents SDK, or Realtime in this
workflow. Embeddings remain on `text-embedding-3-small` until a separately approved re-index.

## Architecture

```
Next.js (BFF) ──HTTP──> FastAPI ──> Postgres (+pgvector) <── Python worker (drafts scenes)
 Writers' Desk          thin boundary   source of truth        the ~minutes of real work
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
  api/        FastAPI app + routers (health, scenes, reviews, runs, beats, chapters, books,
              packets + scene_packets [contract-first gates], jobs, production, world, threads,
              markup, telemetry, settings, learning)
  workers/    worker.py (claim→draft→exit), pipeline.py, planner.py, router.py, draft_queue.py
              (contract-first scheduler + requeue), oracle.py, budget.py, llm.py, production.py
              packet/       ChapterPacket author + QA + approval policy
              scene_packet/ ScenePacket derive/author/QA, staleness, beat derivation
              context/      POV-scoped context assembly    length/  length planner + guard
              specialists/  drafter + enrichment passes
              reviewers/    continuity (always) + pacing/voice/state-drift + tag-gated lanes
              memory/       canon_rag, summaries, ledger, seed    learning/  edit distillation
frontend/     Next.js (App Router) + TS — the Writers' Desk (Inbox → Scene → continuity panel, plus
              Packets, Chapters, Manuscript, Ledger, Production, Telemetry, Settings)
scripts/      init_db.py, export_openapi.py, verify.sh, ci_pyright_changed.sh
tests/        pytest suite (router, packets, draft queue, API)
docs/         DESIGN.md (spec), contract_first_drafting.md (drafting contract), BUILD.md (this file)
```

## Running it

The app ships as a **single container** (Next.js standalone + FastAPI), deployed as one service in the
shared AWS box's Docker Compose stack (behind Caddy) — see
[`DEPLOY.md`](DEPLOY.md). There is no separate local run target: the browser loads the desk from Next
and calls same-origin `/api/desk/*`, which the BFF proxies to FastAPI (no separate API host, no CORS).

> Drafting is **contract-first** (see [`contract_first_drafting.md`](contract_first_drafting.md)):
> propose + approve the **ChapterPacket**, derive + approve **ScenePackets** (beats derive from them),
> then **Draft Chapter** (`POST /chapters/{id}/draft`) queues one job per scene stamped with its
> `scene_packet_id`, and the single-flight background drain (`POST /jobs/draft-next`) drafts them.
> Beat-first drafting (approve beats → queue) is disabled and the legacy `workers/enqueue` CLI was
> removed; `python -m dominion.workers.worker --once` still drains one already-queued job for
> scripting. The enrichment passes and their review lanes are live; a pass that fails still fails
> *soft* (`PassError`) — the drafted spine lands in the inbox, flagged, rather than hard-failing the
> job. The only remaining worker stub is Phase 4 (`draft_ahead` + parallelism).

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
| Writers' Desk (Next.js): inbox, scene review, continuity panel, packets, manuscript, ledger | |

## Build phases (DESIGN §14)

The phases below shipped. The concrete, checkable plan for the current effort (the post-scrub
drafting unblock on the contract-first packet flow) lives in [`ROADMAP.md`](ROADMAP.md).

1. **One approved scene, end to end** — implement the Drafter + continuity reviewer; draft a scene
   from a hand-written beat, review it in the inbox, approve it.
2. **Auto-advance + memory** — RAG over `series/canon/`, per-POV + omniscient summaries, the stat
   ledger, pause-each auto-enqueue of the next scene.
3. **Enrichment specialists** — combat/sensory/dialogue passes + their review-lane reviewers, by beat
   tags. Pacing/voice/state-drift reviewers already live.
4. **`draft_ahead` + parallelism** — provisional ledger, multiple workers.

## Checks (all currently clean)

```bash
just verify                          # all backend gates, matching CI (see scripts/verify.sh)
uv run pytest -q                     # tests
uv run ruff check src tests          # lint
uv run ruff format --check src tests # formatting
bash scripts/ci_pyright_changed.sh   # types — fast pyright over changed files (a subset; use `just verify`/`just typecheck` for full-tree CI parity)
```

DB-backed tests get a real database from the `db_factory` fixture (`tests/conftest.py`), which forces
a dedicated `dominion_test` DB, creates the `vector` extension + schema, and truncates between tests.
If Postgres isn't reachable those tests **skip** (they're opt-in) — point `DOMINION_TEST_DATABASE_URL`
at a Postgres+pgvector instance to exercise them. Tests that don't need a DB run regardless.

## Continuous integration

`.github/workflows/ci.yml` runs on every push to `main` and every pull request. It is **path-aware**:
a `changes` gate (dorny/paths-filter) decides which jobs a PR pays for, so a frontend-only or
docs-only PR skips the full Postgres pytest; a single `ci-passed` aggregator job is the stable
required check for branch protection.

| Job | What runs | Notes |
|---|---|---|
| **lint + types** | `ruff check src tests`, `ruff format --check`, pyright over changed files (`scripts/ci_pyright_changed.sh`) | Python 3.14 via uv |
| **tests** | full `pytest -q` against a real `pgvector/pgvector:pg16` service | Python 3.14 via uv |
| **frontend** | OpenAPI codegen drift, typecheck, lint, format, unit tests; Playwright e2e only on the full tier (main push, manual dispatch, `full-ci` label, or shared-core changes) | Node 24 + pnpm (`pnpm-lock.yaml`) |

Installs are reproducible: backend via `uv sync --frozen` (honours `uv.lock`), frontend via `pnpm install --frozen-lockfile`.
The CI sets **`DOMINION_REQUIRE_DB=1`**, which flips the conftest "Postgres unreachable → skip" into a
hard failure — so a broken DB service can never produce a falsely-green run (the gap this CI closes).
`ANTHROPIC_API_KEY` is set to a deliberately-fake value; tests mock the model, and a real call would
fail fast rather than spend tokens.

Recommended: protect `main` so these checks are **required** to merge (Settings → Branches, or
`gh api -X PUT repos/:owner/:repo/branches/main/protection …`). The repo already merges via PRs, so
required checks make the gate real.
