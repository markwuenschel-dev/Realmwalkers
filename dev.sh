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
# localhost. Vite prints the Network URL; the client derives the API host from wherever it's loaded.
echo "[dev] API    -> http://0.0.0.0:8000   (reachable at http://<machine-ip>:8000)"
echo "[dev] inbox  -> http://0.0.0.0:5173   (reachable at http://<machine-ip>:5173 — Ctrl-C stops everything)"
echo "[dev] worker -> drafting queued beats every 2s"
echo

uv run uvicorn dominion.api.main:app --reload --host 0.0.0.0 --port 8000 &
uv run python -m dominion.workers.worker --loop &
npm --prefix frontend run dev -- --host 0.0.0.0 &
wait -n
