# task runner (bash). `just <task>`
set shell := ["bash", "-uc"]

install:
    pip install -e ".[dev]"

db-up:
    docker compose up -d

db-init:
    python scripts/init_db.py

api:
    uvicorn dominion.api.main:app --reload --port 8000

worker-once:
    python -m dominion.workers.worker --once

enqueue-first:
    python -m dominion.workers.enqueue --book "Dominion Realm" --chapter 1 --scene 1

test:
    pytest -q

lint:
    ruff check src tests

typecheck:
    mypy src

openapi:
    uv run python scripts/export_openapi.py

fe-install:
    cd frontend; npm install

fe-dev:
    cd frontend; npm run dev
