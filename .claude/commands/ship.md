---
description: Ship it — branch, PR, wait for CI, merge, delete stale branches/refs
argument-hint: "[optional: PR title or notes; 'squash'/'rebase' to override merge method]"
---

Run the full **ship it** flow. This command is **only** for when the owner wants to ship — not for normal day-to-day commits (those go straight to `main`).

Default merge method is **merge commit**; if `$ARGUMENTS` contains `squash` or `rebase`, use that instead. Any other text in `$ARGUMENTS` is a hint for the PR title/description.

This repo is in WSL where **git over SSH fails** and remote-tracking refs go stale, so use the token mechanism below for every network op. See the `git-push-mechanism` memory for full context.

## Steps

1. **Survey.** `git status`, `git branch -vv`, and the real remote state via the REST API (local refs lie). Read the diff so the commits and PR body are accurate.

2. **Branch.** Create a `feat/…` / `fix/…` / `docs/…` branch from the current work. Move uncommitted or main-only commits onto this branch if needed. **Do not** open the PR from `main` directly.

3. **Don't commit junk.** Build artifacts / caches (e.g. `frontend/.vite/`, `dist/`, coverage) belong in `.gitignore`, not in a commit. Add ignores rather than staging them.

4. **Commit** on the branch in logical, conventional-commit-style groups (`feat:`, `test:`, `docs:`, `chore:`), one concern per commit, with a body explaining the *why*. End each message with:
   `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`

5. **Verify before PR.** Run the affected tests (`uv run pytest -q <paths>`, frontend `pnpm test`, etc.). Don't open a PR on red.

6. **Push** the branch (token-over-HTTPS, **Basic** auth):
   ```bash
   TOKEN=$(grep -m1 '^GH_TOKEN=' .env | cut -d= -f2- | tr -d '\r\n')
   B64=$(printf 'x-access-token:%s' "$TOKEN" | base64 -w0)
   git -c http.extraheader="AUTHORIZATION: Basic $B64" push \
     https://github.com/markwuenschel-dev/Realmwalkers.git HEAD:<branch>
   ```

7. **Open a detailed PR** via REST (`Authorization: Bearer $TOKEN`) — sections: Summary, What's included, Testing (with results), Notes. Build the JSON payload in python to avoid escaping.

8. **Wait for CI** on the PR to finish. Do **not** merge until checks are green (unless the owner explicitly says to override).

9. **Merge** the PR via REST (`PUT …/pulls/<n>/merge`) with the chosen `merge_method`.

10. **Clean up the ship branch:** delete the merged remote branch (`DELETE …/git/refs/heads/<branch>`), delete the local branch, `git fetch --prune`.

11. **Scan for stale refs:** `git branch --merged main` and remove other merged local branches; list remote branches via API and delete merged/stale agent branches (`cursor/*`, old `feat/*`, etc.) on GitHub; prune remote-tracking refs locally.

12. **Sync main:** fetch + fast-forward local `main` to the merge commit on GitHub.

13. **Report** PR number/URL, merge SHA, CI result, and every branch/ref deleted.

**Never** echo the token or write it anywhere outside `.env`. Confirm before merging if tests or CI fail unless the owner overrides.
