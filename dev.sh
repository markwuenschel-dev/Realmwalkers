#!/usr/bin/env bash
# One terminal: FastAPI API + background worker + React inbox.
# Ctrl-C stops all three (and their children). The worker drafts any queued beat automatically,
# so a drafted scene shows up in the inbox without you running anything else.
set -uo pipefail
cd "$(dirname "$0")"

# Always use the clean Linux venv (an external UV_PROJECT_ENVIRONMENT still wins); ignore any
# activated .venv so `uv run` never falls back to the broken .venv on /mnt/c.
export UV_PROJECT_ENVIRONMENT="${UV_PROJECT_ENVIRONMENT:-$HOME/.venvs/realmwalkers}"
unset VIRTUAL_ENV

cleanup(){ trap - EXIT INT TERM; kill 0; }
trap cleanup EXIT INT TERM

[ -d frontend/node_modules ] || ( cd frontend && npm install )

# Bind to 0.0.0.0 so the API and inbox are reachable on the LAN (machine IP / hostname), not just
# localhost. Next prints the Network URL; its BFF proxies /api/desk/* to the API on 127.0.0.1:8000.
echo "[dev] API    -> http://0.0.0.0:8000   (reachable at http://<machine-ip>:8000)"
echo "[dev] inbox  -> http://0.0.0.0:3000   (reachable at http://<machine-ip>:3000 — Ctrl-C stops everything)"
echo "[dev] worker -> drafting queued beats every 2s"
echo

uv run uvicorn dominion.api.main:app --reload --host 0.0.0.0 --port 8000 &
uv run python -m dominion.workers.worker --loop &
npm --prefix frontend run dev &   # next dev -H 0.0.0.0 on :3000 (BFF -> API at 127.0.0.1:8000)
wait -n
