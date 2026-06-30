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

openapi:
    uv run python scripts/export_openapi.py

fe-install:
    cd frontend && pnpm install

fe-dev:
    cd frontend && pnpm dev
