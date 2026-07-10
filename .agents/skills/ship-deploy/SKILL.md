---
name: ship-deploy
description: Ship current work (full /ship flow — commit, push, PR, merge when green, cleanup), then deploy the merged main to the shared AWS box and verify it live.
disable-model-invocation: true
---

# ship-deploy

`/ship` followed by a production deploy, as one flow. Invoking `/ship-deploy` authorizes both
halves: the entire ship flow (including the merge to `main`) **and** the deploy of the merged
result to the shared AWS box.

## Phase A — Ship

Execute the **`ship`** skill (`~/.claude/skills/ship/SKILL.md`) in full: preflight → branch →
commit → push → detailed PR → green gate → merge → resync `main` → scoped cleanup → report.

All of ship's guardrails apply unchanged, with two clarifications:

- The commit trailer / PR footer name the **current model** (e.g. `Co-Authored-By: Claude Fable 5
  <noreply@anthropic.com>`) — ship's hardcoded model name may be stale.
- **Do not proceed to Phase B unless Phase A fully succeeded**: PR merged AND post-merge CI on
  `main` observed green. If ship stopped anywhere (nothing to ship, CI red, merge blocked),
  stop — deploying would ship the *previous* main, which is misleading.

## Phase B — Deploy

The deploy target and mechanics are documented in `docs/DEPLOY.md`. From the repo root, on the
freshly resynced `main`:

```powershell
./scripts/deploy.ps1          # PowerShell (bash twin: ./scripts/deploy.sh)
```

The script ssh-es to the box, hard-syncs the box's clone to `origin/main`, rebuilds only the
`realmwalkers` Compose service, tails its logs, and health-checks the public URL
(`https://realmwalkers.44-198-76-44.nip.io`). It throws on any failure.

- **Verify the SHA:** the script echoes `deploying <short-sha>: <subject>`. That SHA must equal
  `git rev-parse --short origin/main` locally (the merge commit just shipped). If it doesn't,
  the box deployed something else — investigate before reporting success.
- **Skim the log tail** for the boot sequence: `init_db` (tables) → hypercorn (FastAPI) → next.
  A passing health check with a crash-looping log tail is not a good deploy.
- **On failure:** do not retry blindly. Read the error (ssh key missing, compose build failure,
  health check timeout), fix the cause, re-run. To roll back:
  `./scripts/deploy.ps1 -Ref <previous-sha>`.

## Phase C — Content note (Realmwalkers-specific)

Deploying puts repo files on the box; it does **not** touch the production database. If the
shipped change includes manuscript scenes (`book1/manuscript/scenes/`) or canon
(`series/canon/`), the seed/ingest step (`python -m dominion.workers.memory.seed`, run inside
the container — it is idempotent) is what makes them live in the app. Don't run it silently as
part of this skill — it spends Anthropic tokens on the summary fold. Say in the report whether
it's needed, and run it only if the user asked for it (in this invocation or a standing
instruction).

## Phase D — Report

One summary: commit SHA, PR URL + merged state, cleanup list (from ship), deployed SHA +
health-check result (HTTP code), and the Phase C note if content changed.
