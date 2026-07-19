<div align="center">

# Dominion Realm

### A human-gated, scene-by-scene writing system for *The Dominion Realm*

*Not an agent — a workflow.* A worker drafts exactly one scene, writes it to Postgres as
`pending_review`, and exits. Nothing runs between approvals. You approve, edit, or reject every scene
from the Writers' Desk.

<br />

![Python](https://img.shields.io/badge/Python-3.12+-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![Next.js](https://img.shields.io/badge/Next.js-16-000000?logo=nextdotjs&logoColor=white)
![React](https://img.shields.io/badge/React-19-61DAFB?logo=react&logoColor=black)
![Postgres](https://img.shields.io/badge/Postgres-+pgvector-4169E1?logo=postgresql&logoColor=white)
![Deploy](https://img.shields.io/badge/deploy-AWS%20·%20Docker%20·%20Caddy-232F3E?logo=amazonaws&logoColor=white)

<br />

[**Design rationale**](docs/DESIGN.md) · [**Contract-first drafting**](docs/contract_first_drafting.md) · [**Roadmap**](docs/ROADMAP.md) · [**Deploy**](docs/DEPLOY.md)

</div>

---

## Why it's built this way

The previous build let an LLM sit in the control path, and it spiraled. This one doesn't.

- 🧭 **Coordination is deterministic code.** `workers/router.py` is a lookup table and a loop that
  decide which passes run. No model in the control path.
- ✋ **Reviewers advise; they never block.** The human inbox is the only gate.
- 🗂️ **Versioning is rows, not Git branches.** A revision inserts a new `scenes` row that supersedes
  its parent. Runtime exhaust — logs, job state — never enters the repo.
- ⏸️ **Nothing runs between approvals.** A draft happens *only* when you act, so there's nothing to
  boot and nothing to re-verify.

## Architecture

```
Next.js (BFF) ──HTTP──> FastAPI ──> Postgres (+pgvector) <── Python worker (drafts scenes)
 Writers' Desk          thin boundary   source of truth        the ~minutes of real work
```

The whole app ships as a **single container** (Next.js + FastAPI), deployed as one service in the
shared AWS box's Docker Compose stack behind Caddy. The browser loads the desk from Next and calls
same-origin `/api/desk/*`, which the Next BFF proxies to FastAPI — so there's no separate API host
and no CORS. See [`docs/DEPLOY.md`](docs/DEPLOY.md).

## The loop is contract-first — and browser-driven

No terminal needed. In the Writers' Desk:

1. **Create a book** and outline a chapter.
2. The system proposes a **ChapterPacket** — the chapter's knowledge contract — for you to edit and approve.
3. **ScenePackets** are derived and approved per scene; **beats** derive from the approved ScenePackets.
4. **Draft Chapter** (`POST /chapters/{id}/draft`) queues one job per scene, each stamped with its `scene_packet_id`.
5. Jobs drain in a single-flight background task (`POST /jobs/draft-next`); you **review, approve, or revise** from the Inbox.

Enrichment passes and their review lanes are live. A pass that fails still fails *soft* (`PassError`)
— the drafted spine lands in the inbox flagged, rather than hard-failing the job. Full detail:
[`docs/contract_first_drafting.md`](docs/contract_first_drafting.md).

## What's real vs. scaffolded

Phases 1–3 are **built and tested**. The only stub left in the worker tree is Phase 4 (`draft_ahead`
+ parallelism), deferred until throughput actually hurts.

| ✅ Real now | 🚧 Stubbed |
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
| FastAPI: health, scenes, reviews, runs, beats, chapters, books, world, threads, telemetry | |
| Approve/deny/revise + hand-edit; ledger + summary hooks; `pause_each` auto-advance | |
| Browser draft trigger (`POST /jobs/draft-next` + `GET /jobs/status`) — single-flight drain | |
| Writers' Desk fully wired to the live API (no fixtures) | |
| In-browser contract-first planning (ChapterPacket → ScenePackets → beats → Draft Chapter) | |
| Scene markup: Annotations (margin notes) + track-changes Suggestions (accept/reject → folded on approve) | |

### Build phases ([DESIGN §14](docs/DESIGN.md))

1. ✅ **One approved scene, end to end** — Drafter + continuity reviewer.
2. ✅ **Auto-advance + memory** — canon RAG, per-POV + omniscient summaries, the stat ledger, `pause_each` auto-enqueue, and a manuscript seed-importer.
3. ✅ **Enrichment specialists** — combat/sensory/dialogue passes + their review lanes, routed by beat tags.
4. ⬜ **`draft_ahead` + parallelism** — provisional ledger, batch invalidation, multiple workers.

## Repository layout

<details>
<summary><strong>src/dominion/</strong> — backend (API + workers)</summary>

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
```

</details>

<details>
<summary><strong>frontend/</strong> · <strong>scripts/</strong> · <strong>tests/</strong> · <strong>docs/</strong></summary>

```
frontend/     Next.js (App Router) + TS — the Writers' Desk (Inbox, Scene review, Chapters, Packets,
              Diff, Manuscript, Ledger, Production, Telemetry, Settings). Screens read live data via
              desk/api/; the Next BFF proxies same-origin /api/desk/* to FastAPI.
scripts/      init_db.py, export_openapi.py, verify.sh, ci_pyright_changed.sh
tests/        pytest suite (router, packets, draft queue, API) — runs against real Postgres in CI
docs/         DESIGN.md, BUILD.md, ROADMAP.md, DEPLOY.md, contract_first_drafting.md
```

</details>

## Development

```bash
just verify                          # all backend gates, matching CI (see scripts/verify.sh)
pytest -q                            # tests
ruff check src tests                 # lint (F-codes catch real bugs: undefined names, unused vars)
bash scripts/ci_pyright_changed.sh   # types — fast pyright over changed files (a subset; use `just verify`/`just typecheck` for full-tree CI parity)
```

Frontend gates (`pnpm typecheck` / `lint` / `format:check` / `test`) run from `frontend/`.
