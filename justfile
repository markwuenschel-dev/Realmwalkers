# task runner (bash). `just <task>`
set shell := ["bash", "-uc"]

install:
    uv sync --frozen --extra dev

db-up:
    docker compose up -d

db-init:
    uv run python scripts/init_db.py

api:
    uv run uvicorn dominion.api.main:app --reload --port 8000

worker-once:
    uv run python -m dominion.workers.worker --once

enqueue-first:
    uv run python -m dominion.workers.enqueue --book "Dominion Realm" --chapter 1 --scene 1

test:
    uv run pytest -q

lint:
    uv run ruff check src tests

typecheck:
    uv run mypy src

# PR-scoped pyright — same as CI static job (see scripts/ci_pyright_changed.sh).
typecheck-changed:
    bash scripts/ci_pyright_changed.sh

# Backend gates matching CI static + tests (Postgres required; see scripts/verify.sh).
verify:
    bash scripts/verify.sh

openapi:
    uv run python scripts/export_openapi.py

fe-install:
    cd frontend && pnpm install

fe-dev:
    cd frontend && pnpm dev
