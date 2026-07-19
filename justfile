# task runner (bash). `just <task>`
set shell := ["bash", "-uc"]

install:
    uv sync --frozen --extra dev

db-init:
    uv run python scripts/init_db.py

# Full suite — Postgres required; fails loud if the DB is down (matches verify.sh + CI).
test:
    DOMINION_REQUIRE_DB=1 uv run pytest -q -rs

# Fast unit-only loop — DB-gated tests skip when Postgres is down (opt-in, no infra).
test-nodb:
    uv run pytest -q -rs

lint:
    uv run ruff check src tests

typecheck:
    uv run pyright

# PR-scoped pyright — fast changed-files subset; full parity via `just verify`/`just typecheck`.
typecheck-changed:
    bash scripts/ci_pyright_changed.sh

# Backend gates matching CI static + tests (Postgres required; see scripts/verify.sh).
verify:
    bash scripts/verify.sh

openapi:
    uv run python scripts/export_openapi.py

fe-install:
    cd frontend && pnpm install
