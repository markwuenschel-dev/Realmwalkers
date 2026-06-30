# Agent instructions — Realmwalkers

## Contract-first drafting

Draft job queueing is contract-first: see [docs/contract_first_drafting.md](docs/contract_first_drafting.md). All draft paths must go through `dominion.workers.draft_queue`.

## Git workflow (mandatory)

Two modes. Do not mix them up.

### Daily work — stay on `main`

- Do all implementation on **`main`**.
- Commit and push to **`origin/main`** when work is done.
- The owner gets changes with **`git pull`** on `main` — nothing else.
- **Do not** create `cursor/*`, `feat/*`, or other side branches during normal tasks.
- **Do not** leave completed work only on a branch the owner must fetch/checkout.
- **Do not** tell the owner to checkout an agent branch to get code.

### Ship it — only when the owner says "ship it"

When the owner explicitly says **ship it** (or runs the ship command), run the full release flow — **not** during ordinary tasks:

1. **Survey** — `git status`, `git branch -vv`, read the diff.
2. **Branch** — create a `feat/…` / `fix/…` / `docs/…` branch from current work (do not ship from `main` directly).
3. **Commit** — stage only task-related files; logical conventional commits with clear messages.
4. **Push** the branch to GitHub.
5. **Open a detailed PR** — Summary, what's included, testing (with results), notes.
6. **Wait for CI** to finish green before merging.
7. **Merge** the PR (default: merge commit unless owner asked for squash/rebase).
8. **Delete** the merged branch — local and on GitHub.
9. **Scan and clean stale refs** — `git fetch --prune`, delete any other merged/stale local and remote branches tied to the work.
10. **Fast-forward local `main`** to match GitHub after merge.
11. **Report** — PR URL, merge SHA, branches deleted.

See [`.claude/commands/ship.md`](.claude/commands/ship.md) for token/auth details in this environment.

### Never

- Never open random PR branches mid-task without the owner asking to ship.
- Never commit unrelated deletions (e.g. `.agents/skills`) mixed with task work.
- Never merge a PR before CI passes (unless the owner explicitly overrides).
