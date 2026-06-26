# Writers' Desk → Next.js App Router migration

This is **slice 1** of the Vite → Next.js migration: the durable cutover (skeleton, URL-native
routing, API boundary). Behavior and visual language are preserved; structural refactors (hook/
provider extraction, UI primitives, screen splits) are deliberately **deferred** — see "Follow-up
work" below.

## What changed

### Build system
- **Vite → Next.js 14 (App Router)**, still React 18.3 + TypeScript.
- Removed: `vite.config.ts`, `tsconfig.node.json`, `index.html`, `src/main.tsx`, `src/vite-env.d.ts`,
  `src/index.css`, `src/App.tsx`.
- Added: `next.config.mjs`, `next-env.d.ts`, `src/app/**`. `src/index.css` → `src/app/globals.css`
  (the Google-Fonts `<link>`s moved into `src/app/layout.tsx`).
- `package.json` scripts: `dev` (`next dev -H 0.0.0.0`), `build` (`next build`),
  `start` (`next start -H 0.0.0.0`), `typecheck` (`tsc --noEmit`), `lint` (`next lint`).
  Dev/start bind `0.0.0.0` so the LAN URL keeps working.

### Routing — page identity now lives in the URL
- The internal `screen` string and `go(screen)` are **gone**. `focusSceneId` is **gone** (it is now the
  `/scene/[sceneId]` route param).
- One **route registry** is the single source of truth: `src/desk/routes.ts` (`DESK_ROUTES`,
  `CHORD_TO_HREF`, `activeRouteId`). TopBar, CommandPalette, and the global keyboard shortcuts all
  read from it — no duplicated nav arrays remain.
- Routes:
  | URL | Screen | Notes |
  |-----|--------|-------|
  | `/` | — | redirects to `/scene` (the prototype default) |
  | `/inbox` | Inbox | |
  | `/scene` | Scene | pending review queue |
  | `/scene/[sceneId]` | Scene | focused/out-of-queue scene (e.g. an approved one) |
  | `/chapters` | Chapters | board + timeline still toggle via internal state (deferred) |
  | `/packets` | Packets | |
  | `/diff` | Versions | compares the currently-loaded scene |
  | `/diff/[sceneId]` | Versions | shareable, refresh-safe history; loads the scene itself |
  | `/manuscript` | Manuscript | |
  | `/ledger` | Ledger | category still internal state (deferred) |
  | `/docs` | Canon | |
- TopBar uses `<Link>` + `usePathname()`; the active item is computed by `activeRouteId()` (longest
  matching href wins, so `/scene/<id>` still highlights "Scene").
- Keyboard: `⌘/Ctrl+K` palette, `Esc` close, `g _` chord nav (from the registry), `j`/`k` queue.
  Shortcuts are now also suppressed inside `select` and `contenteditable` (previously only
  input/textarea).
- `useDeskState` lives in `state.ts` (now `"use client"`) and navigates via `next/navigation`'s
  router. `nextScene`/`prevScene`/`openScene`/`openSceneId` are kept as domain actions that push
  routes (not a parallel routing system).

### API boundary — same-origin BFF proxy
- `src/desk/api/client.ts` no longer uses `import.meta.env`. `BASE = "/api/desk"`.
- New route handler `src/app/api/desk/[...path]/route.ts` proxies every method/path/query/body to
  FastAPI at **`process.env.API_BASE`** (default `http://127.0.0.1:8000`). Status + body pass through
  unchanged so the typed client keeps its error semantics; an unreachable backend returns **502**,
  which the existing poll-failure counter reads as "backend unreachable" (BackendBanner).
- The browser never needs the FastAPI host or CORS.

### Client/Server boundary
- Route pages (`src/app/**/page.tsx`) are thin **Server Components** that render the existing screens.
- `"use client"` is on: `providers.tsx`, `DeskShell`, `state.ts`, `api/data.tsx`, all 8 screens, and
  the interactive components touched (TopBar, CommandPalette, DecisionToast). Everything else is in the
  client graph transitively.
- Lazy `docx.ts` import (manuscript/docs export) is unchanged and still code-split.

## Preserved behavior
Adaptive job polling + backend-unreachable banner, scene autosave/restore per (scene, version), the
active-scene stale-response guard, Planner proposed-beat rehydration, decision/revise/deny commit
(try/finally), version diff + revert, manuscript approved/draft compile, lazy DOCX export.

## How to run
```bash
cd frontend
npm install
# point the proxy at your FastAPI (optional; defaults to http://127.0.0.1:8000)
export API_BASE=http://127.0.0.1:8000
npm run dev      # http://<lan-ip>:3000
npm run build && npm start
npm run typecheck
```
Backend is unchanged (FastAPI via the usual WSL `dev.sh`).

### Ops note (pm2)
The pm2 `desk` process previously ran Vite (port 5173). It now runs `next dev` (default port **3000**).
Update the pm2 process/ecosystem command and port mapping when this lands.

### Deploy note (Railway)
Previously FastAPI served the built bundle same-origin. With Next as its own server, the deployment
shape changes: run the Next server and set `API_BASE` to the FastAPI URL (internal service URL if
co-located). This is **not** wired in this slice — it's the main deployment follow-up.

## Verification
`tsc --noEmit` clean; `next build` green (12 routes); runtime smoke: `/`→307, `/scene` `/inbox`
`/scene/<id>` `/ledger`→200 on direct load, BFF→502 JSON with no backend.

## Follow-up work (deferred from this slice)
- Deeper param routes: `/chapters/timeline`, `/packets/[chapterId]`, `/ledger/[category]`,
  `/docs/[...path]` (today these are internal state on the base routes).
- Hook/provider extraction: `useJobStatus`, `useActiveScene`, `useBookCollections`, `useAsyncAction`.
- UI primitives under `components/ui/` + screen refactors (Inbox/Chapters/Planner first).
- Unified toast system; safer suggestion anchoring (quote + block/occurrence index).
- Tests (no runner yet): route registry, palette command generation, `useSelection`, `latestScenes`,
  active-scene stale-response, API base.
- SSR nicety: localStorage-seeded layout state can cause a first-render hydration mismatch (cosmetic;
  guarded by try/catch). Move to an effect-based read when convenient.
- Deployment (Railway) + pm2 command updates.
