# task runner (bash). `just <task>`
set shell := ["bash", "-uc"]

# The local test Postgres is the `realmwalkers-db` container, which publishes 5433 — NOT 5432.
# Other projects routinely run their own Postgres on 5432, so a 5432 default silently points the
# suite at a foreign database instead of failing. Override by exporting DOMINION_TEST_DATABASE_URL.
test-db-url := env_var_or_default("DOMINION_TEST_DATABASE_URL", "postgresql+asyncpg://dominion:dominion@127.0.0.1:5433/dominion_test")

install:
    uv sync --frozen --extra dev

fe-install:
    cd frontend && pnpm install

db-init:
    uv run python scripts/init_db.py

# Full suite — Postgres required; fails loud if the DB is down (matches verify.sh + CI).
test:
    DOMINION_TEST_DATABASE_URL="{{test-db-url}}" DOMINION_REQUIRE_DB=1 uv run pytest -q -rs

# Fast unit-only loop — DB-gated tests skip when Postgres is down (opt-in, no infra).
test-nodb:
    DOMINION_TEST_DATABASE_URL="{{test-db-url}}" uv run pytest -q -rs

lint:
    uv run ruff check src tests

typecheck:
    uv run pyright

# PR-scoped pyright — fast changed-files subset; full parity via `just verify`/`just typecheck`.
typecheck-changed:
    bash scripts/ci_pyright_changed.sh

openapi:
    uv run python scripts/export_openapi.py

# API-contract drift gate. `pnpm codegen:check` CANNOT fail on its own: it regenerates the TS
# client FROM openapi.json and then diffs both, so a backend change that never re-exported
# openapi.json always passes. Re-exporting first is what makes the diff meaningful. This is the
# local mirror of what CI already does (ci.yml runs export_openapi.py before codegen:check).
# Part of #275.
[doc("API-contract drift: re-export openapi.json, THEN diff schema + generated TS client.")]
contract-check: openapi
    cd frontend && pnpm codegen:check

# Frontend gates matching CI's `frontend` job. `pnpm build` (next build) is the only one that
# catches server-only imports (node:fs, node:child_process, ...) leaking into a client bundle —
# `tsc --noEmit` and vitest both pass happily on that mistake.
[doc("Frontend gates matching CI: contract + typecheck + lint + format + unit + production build.")]
fe-verify: contract-check
    cd frontend && pnpm typecheck
    cd frontend && pnpm lint
    cd frontend && pnpm format:check
    cd frontend && pnpm test
    cd frontend && pnpm build

# Just the Next.js production build — the client/server boundary gate, on its own.
fe-build:
    cd frontend && pnpm build

# Backend-only gates matching CI's static + tests jobs (Postgres required; see scripts/verify.sh).
verify-backend:
    DOMINION_TEST_DATABASE_URL="{{test-db-url}}" bash scripts/verify.sh

# THE local gate: contract + frontend + backend. Ordered cheapest-first, so contract drift or a
# broken client bundle surfaces in seconds rather than after the full Postgres suite. Use
# `just verify-backend` for the backend-only loop.
[doc("THE local gate: API contract + frontend + backend. Cheapest checks fail first.")]
verify: contract-check fe-verify verify-backend
    @echo "verify: all gates passed (contract + frontend + backend)"
