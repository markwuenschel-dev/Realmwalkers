# Agent instructions — Realmwalkers

## Git workflow (mandatory)

**Commit and push directly to `main`.** This is the owner's working branch.

- Do **not** create `cursor/*`, `feat/*`, or other feature branches unless the user **explicitly** asks for a PR or a separate branch.
- Do **not** leave completed work on a side branch for the user to fetch/checkout/merge.
- After changes: `git add` → `git commit` → `git push origin main`.
- One branch (`main`) should always match what's on disk after `git pull`.

### PRs (optional, only when asked)

If the user explicitly wants a PR for review history:

1. Do the work on `main` first (or merge to `main` immediately when done).
2. A branch/PR is documentation only — **never** the only place code lives.
3. Tell the user: `git pull` on `main` is enough; no fetch/checkout of agent branches.

### Never

- Never tell the user to checkout an agent branch to get code.
- Never require stash/merge gymnastics for normal agent deliverables.
- Never commit unrelated file deletions (e.g. `.agents/skills`) mixed with task work.
