#!/usr/bin/env bash
# One-command deploy of Realmwalkers to the shared AWS box (docs/DEPLOY.md has the full story).
#
#   ./scripts/deploy.sh                 # deploy latest main
#   ./scripts/deploy.sh <sha>           # roll back / deploy a specific commit or tag
#   DRY_RUN=1 ./scripts/deploy.sh       # print the remote script instead of running it
#
# This is exactly the manual loop from DEPLOY.md (ssh -> git pull -> compose rebuild -> logs),
# plus a public-URL health check at the end. A deploy costs nothing per run: the pull and the
# docker build happen on the box (flat EC2 bill), and DNS is not involved.
set -euo pipefail

REF="${1:-main}"
KEY="${SSH_KEY:-$HOME/.ssh/shared-box.pem}"
BOX="${BOX:-ubuntu@44.198.76.44}"
URL="${URL:-https://realmwalkers.44-198-76-44.nip.io}"
TAIL="${TAIL:-40}"

# The box's clone is deploy-only (never edited in place), so main can be hard-synced to origin.
# Any other ref — a rollback SHA, a tag — is checked out detached; `git checkout main` restores.
if [ "$REF" = "main" ]; then
  SYNC="git checkout -q main && git reset --hard origin/main"
else
  SYNC="git checkout -q --detach '$REF'"
fi

REMOTE=$(cat <<EOF
set -eu
cd /opt/stack/Realmwalkers
git fetch -q origin
$SYNC
echo "deploying \$(git rev-parse --short HEAD): \$(git log -1 --format=%s)"
cd /opt/stack/infra
docker compose up -d --build realmwalkers
docker compose logs --tail=$TAIL realmwalkers || echo "(log tail failed - check manually; deploy itself already succeeded)"
EOF
)

if [ -n "${DRY_RUN:-}" ]; then
  printf '%s\n' "$REMOTE"
  exit 0
fi

[ -f "$KEY" ] || { echo "SSH key not found: $KEY" >&2; exit 1; }
# tr guards against CRLF sneaking into the stream (e.g. a CRLF checkout of this file) — a stray \r
# makes bash on the box see 'realmwalkers\r' and compose reports "no such service".
printf '%s\n' "$REMOTE" | ssh -i "$KEY" "$BOX" "tr -d '\r' | bash -s"

# Prove the public URL serves the new build — logs alone don't show what Caddy is fronting.
code=$(curl -fsSL -o /dev/null -w '%{http_code}' --max-time 30 "$URL/") ||
  { echo "deployed, but the health check against $URL failed" >&2; exit 1; }
echo "$URL -> HTTP $code"
