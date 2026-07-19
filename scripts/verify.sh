#!/usr/bin/env bash
# Local gate matching CI static + tests jobs (see .github/workflows/ci.yml).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT}"

export UV_PYTHON="${UV_PYTHON:-3.14}"

echo "==> ruff check"
uv run --no-sync ruff check src tests

echo "==> ruff format --check"
uv run --no-sync ruff format --check src tests

echo "==> pyright (full src, matching CI)"
uv run --no-sync pyright

echo "==> pytest"
export DOMINION_TEST_DATABASE_URL="${DOMINION_TEST_DATABASE_URL:-postgresql+asyncpg://dominion:dominion@127.0.0.1:5432/dominion_test}"
export DOMINION_REQUIRE_DB="${DOMINION_REQUIRE_DB:-1}"
export ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-sk-ant-ci-not-a-real-key}"
uv run --no-sync pytest -q -rs

echo "verify: all gates passed"
