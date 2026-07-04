# UI performance — tab switching & heavy payloads (Lane 9)

Mission: near-instant tab switching on cached data; heavy payloads load only when opened.
Scope: `frontend/src/desk/` only. No backend changes were required (see "Declared schema
additions" — the planned summary endpoint turned out to be unnecessary).

## 1. Trace — what each tab fired on mount (before this change)

Route pages unmount/remount their screen on every tab switch; the `DeskDataProvider` (app layout)
survives. "Every visit" therefore means every tab switch.

| Tab | Fired on mount (before) | Payload class | After this change |
| --- | --- | --- | --- |
| Inbox `/` | provider data only (already loaded) | — | unchanged + load timing |
| Chapters | provider data only | — | unchanged + load timing |
| Manuscript | `GET /books/{id}/manuscript` full compile on EVERY visit | heavy — full book prose (grows with the book; 100s of KB → MB) | warm-cache **no-op** (zero fetch); refetches only after a scene action / chapter edit marked it stale |
| Packets | slim scene-packet summaries (fixed in an earlier lane) | small | untouched |
| Production | `GET /chapters/{id}/production-runs` (small) + `GET /production-runs/{id}` full detail on EVERY visit and after EVERY action | **~669KB observed** — every artifact's prose inlined | detail cached per session, staleness keyed on the list row's `updated_at`; unchanged run = **zero** detail refetch; triage/verify refetch only slim `issues`/`repair-tasks`/`events` |
| Telemetry | `GET /books/{id}/telemetry` + `GET /runs/{id}/telemetry` on EVERY visit, blank spinner meanwhile | medium (SQL aggregates, 10s of KB) | session cache paints instantly, `load()` revalidates in the background; spinner only when cold |
| (every book load) | slim canon index **+ full-body canon corpus (~2MB) in the background on EVERY `loadCollections`** — i.e. every book load, every queue-clear | **~2MB** | full bodies download **once per book per session**; later loads merge known bodies onto the fresh slim index; ingest/rebuild invalidates the guard |

### Bonus finding (fixed): unstable `onBookChange` re-armed the bootstrap

`useDeskDataState` passed an inline `() => {}` as `onBookChange` to `useDeskCollections`. That
callback is in the bootstrap effect's dependency array, so **every provider render** (each drafting
poll tick that changed jobs state, every action) re-ran the effect → a full `loadCollections`
fan-out (N+1 chapter-scenes + canon). The busy-poll progress-gating fixed `useDeskJobs`' own calls,
but this re-trigger path was still open. Fixed by hoisting a module-level `noopOnBookChange`
(`frontend/src/desk/api/data.tsx`).

## 2. Fixes (value order)

1. **ProductionScreen slim path** (`screens/ProductionScreen.tsx`, `api/client.ts`)
   - Module-level `runDetailCache` (session cache of `ProductionRunDetailOut` keyed by run id).
     Selecting a run paints from cache instantly; the cheap list row's `updated_at` is the
     staleness token (every backend action bumps it), so an unchanged run costs zero detail fetch
     on a tab revisit.
   - Header metrics (`Status`, `Issues`, `Repair tasks`) render from the list row
     (`ProductionRunOut.summary_json`) before/without the heavy detail; the blocked-run gate also
     keys off the list row.
   - Post-action refresh split: `triage` and `verify` cannot change artifacts/prose → they refetch
     only the existing slim sub-resource endpoints (`/production-runs/{id}/issues`,
     `/repair-tasks`, `/events`) and merge into the cached detail. `assemble`/`apply`/`start` can
     change artifacts → full reload (as before). New client methods: `api.productionRunIssues`,
     `api.productionRunRepairTasks`, `api.productionRunEvents`.
   - Known tradeoff: `repair_attempts`/`repair_verifications`/`issue_decisions` in the raw
     Run-JSON inspector refresh on full reloads only (documented in-code).
2. **Manuscript caching** (`api/hooks/useDeskCollections.ts`, `api/data.tsx`,
   `screens/ManuscriptScreen.tsx`)
   - `manuscriptFresh` flag in the provider: `refreshManuscript` is a pure no-op while the cached
     compile is still current, so a Manuscript tab revisit renders with zero network.
   - Staleness marks: `refreshScenes` (the reconciliation path every scene action funnels through)
     and `updateChapter` (title/epigraph/POV surface in the compile). `loadCollections` re-marks
     fresh when its compile lands.
3. **Telemetry caching** (`screens/TelemetryScreen.tsx`)
   - Module-level session cache keyed by book (`data` + paged `runs` + `latestRun`). Tab revisits
     hydrate instantly, then `load()` revalidates in the background (telemetry only grows; every
     in-app delete already funnels through `onDataChanged → load()`, which rewrites the cache).
4. **Canon body upgrade once per book per session** (`api/hooks/useDeskCollections.ts`)
   - Module-level `canonBodiesLoaded` guard. Later `loadCollections` runs fetch only the slim
     index and merge previously downloaded bodies onto it by id (no downgrade to bodiless rows).
     Failure clears the guard (next load retries). `invalidateCanonBodies(bookId)` is called on
     canon ingest (`data.tsx`) and clean rebuild (`LedgerScreen.tsx`) since those replace the
     corpus (new ids).
5. **Tab-load instrumentation** (`lib/useTabLoadTiming.ts`)
   - `useTabLoadTiming(screen, ready)` logs `console.debug("[desk:tab-load] <screen> first data
     render in <n>ms")` once per mount — cached revisits log ~0ms next to cold loads. Wired into
     Inbox, Chapters, Manuscript, Production, Telemetry.

## 3. Declared schema additions

**None.** The plan reserved `GET /production-runs/{id}/summary` (counts + statuses), but it is not
needed:

- run headers/counters already ride on the list endpoint (`ProductionRunOut.summary_json` carries
  `issue_count` / `repair_task_count`, plus `status` / `current_stage` / `updated_at`), and
- the slim post-action refresh reuses three endpoints that already exist in the OpenAPI schema and
  `generated.ts`: `GET /production-runs/{run_id}/issues`, `GET /production-runs/{run_id}/repair-tasks`,
  `GET /production-runs/{run_id}/events` (`src/dominion/api/routers/production.py`).

No backend files were touched; no codegen run (per lane constraints).

## 4. Tests (written, not run — worktree has no node_modules; integrator runs frontend gates)

- `screens/ProductionScreen.test.tsx`: cache reset in `beforeEach`
  (`resetRunDetailCacheForTests`); new — cached remount refetches the list but **not** the ~670KB
  detail; triage refetches only slim sub-resources while the full detail stays at one fetch.
- `screens/ManuscriptScreen.test.tsx` (new): real `DeskDataProvider` + mocked client; screen
  unmount/remount around a surviving provider — cached compile renders with zero new
  `api.manuscript` calls.
- `api/hooks/__tests__/desk-hooks.test.ts`: new `useDeskCollections` describe — full canon bodies
  downloaded once across two `loadCollections` calls (bodies survive the slim refresh); manuscript
  warm no-op → stale-after-`refreshScenes` → exactly one refetch.

Test-only module-state resets exported: `resetRunDetailCacheForTests`,
`resetTelemetryCacheForTests`, `resetCanonBodyGuardForTests`.
