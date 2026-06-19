---
description: Commit, push, open a detailed PR, merge it, and delete merged branches (local + GitHub)
argument-hint: "[optional: PR title or notes; 'squash'/'rebase' to override merge method]"
---

Run the full ship flow for the current working tree. Default merge method is **merge commit**;
if `$ARGUMENTS` contains `squash` or `rebase`, use that instead. Any other text in `$ARGUMENTS`
is a hint for the PR title/description.

This repo is in WSL where **git over SSH fails** and remote-tracking refs go stale, so use the
token mechanism below for every network op. See the `git-push-mechanism` memory for full context.

## Steps

1. **Survey.** `git status`, `git branch -vv`, and the real remote state via the REST API (local
   refs lie). Read the diff so the commits and PR body are accurate.

2. **Branch.** If on `main` (the default branch), create a `feat/…` / `fix/…` / `docs/…` branch
   first — never commit straight to `main`. If already on a feature branch, stay on it.

3. **Don't commit junk.** Build artifacts / caches (e.g. `frontend/.vite/`, `dist/`, coverage)
   belong in `.gitignore`, not in a commit. Add ignores rather than staging them.

4. **Commit** in logical, conventional-commit-style groups (`feat:`, `test:`, `docs:`, `chore:`),
   one concern per commit, with a body explaining the *why*. End each message with:
   `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`

5. **Verify before merge.** Run the affected tests (`uv run pytest -q <paths>`). Don't merge red.

6. **Push** the branch (token-over-HTTPS, **Basic** auth):
   ```bash
   TOKEN=$(grep -m1 '^GH_TOKEN=' .env | cut -d= -f2- | tr -d '\r\n')
   B64=$(printf 'x-access-token:%s' "$TOKEN" | base64 -w0)
   git -c http.extraheader="AUTHORIZATION: Basic $B64" push \
     https://github.com/markwuenschel-dev/Realmwalkers.git HEAD:<branch>
   ```

7. **Open a detailed PR** via REST (`Authorization: Bearer $TOKEN`) — sections: Summary, What's
   included, Testing (with results), Notes. Build the JSON payload in python to avoid escaping.

8. **Merge** the PR via REST (`PUT …/pulls/<n>/merge`) with the chosen `merge_method`.

9. **Clean up:** delete the merged remote branch (`DELETE …/git/refs/heads/<branch>`), fetch +
   fast-forward local `main` to the merge commit, delete the local feature branch, then
   `git branch --merged main` to catch any other stale branches and remove them local + remote.

10. **Report** PR number/URL, merge SHA, and what was deleted.

**Never** echo the token or write it anywhere outside `.env`. Confirm before merging if tests fail
or the diff looks unexpected.
