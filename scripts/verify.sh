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
# The local test Postgres is the `realmwalkers-db` container, which publishes 5433 — NOT 5432.
# Other projects routinely run their own Postgres on 5432, so the old 5432 default silently pointed
# the suite at a foreign database instead of failing.
export DOMINION_TEST_DATABASE_URL="${DOMINION_TEST_DATABASE_URL:-postgresql+asyncpg://dominion:dominion@127.0.0.1:5433/dominion_test}"
export DOMINION_REQUIRE_DB="${DOMINION_REQUIRE_DB:-1}"
export ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-sk-ant-ci-not-a-real-key}"

# Preflight: never run the suite against an invisible target. Print the resolved URL, then fail
# loudly if nothing is listening on it. This does NOT prove it is the *right* Postgres — only a real
# connection can, and conftest's DOMINION_REQUIRE_DB already turns a bad one into a failure rather
# than a skip. What it removes is the silence that let the wrong-port default go unnoticed.
_hostport="${DOMINION_TEST_DATABASE_URL#*@}"   # drop scheme + credentials
_hostport="${_hostport%%/*}"                   # drop /dbname and anything after
_hostport="${_hostport%%\?*}"                  # drop any ?query
db_host="${_hostport%%:*}"
db_port="${_hostport##*:}"
if [ "${db_port}" = "${db_host}" ]; then db_port=5432; fi   # URL carried no explicit port
echo "    test DB target: ${DOMINION_TEST_DATABASE_URL}"
if ! (exec 3<>"/dev/tcp/${db_host}/${db_port}") 2>/dev/null; then
    echo "verify: FATAL — nothing is listening on ${db_host}:${db_port}." >&2
    echo "  The test Postgres is the 'realmwalkers-db' container, published on 5433." >&2
    echo "  Start it, or set DOMINION_TEST_DATABASE_URL to the correct target." >&2
    exit 1
fi

uv run --no-sync pytest -q -rs

echo "verify: all gates passed"
