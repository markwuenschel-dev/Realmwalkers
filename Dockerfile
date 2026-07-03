# syntax=docker/dockerfile:1.7
# Single-service image: Next.js (public) + FastAPI (internal), one container. The browser loads the
# desk from Next and calls same-origin /api/desk/*, which the Next BFF proxies to FastAPI on
# 127.0.0.1:8000 — so there is still no separate API host, no CORS, and no "localhost" in the client.

# --- 1) build the Next.js frontend -> standalone server -----------------------------------------
# Node 24 (current LTS). Keep this major in sync with .github/workflows/ci.yml (node-version "24").
FROM node:24-slim AS frontend
WORKDIR /app/frontend
ENV PNPM_HOME=/pnpm PATH=/pnpm:$PATH
RUN corepack enable && corepack prepare pnpm@10.11.0 --activate
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
# output: "standalone" => .next/standalone/server.js + traced node_modules; static assets separate.
RUN pnpm build

# --- 2) python + node runtime --------------------------------------------------------------------
FROM python:3.14-slim AS app
WORKDIR /app
# Next serves the public $PORT (Railway sets PORT=8000); FastAPI runs on a distinct internal port so
# the two never collide. API_BASE points the BFF at that internal port.
ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    NODE_ENV=production \
    HOSTNAME=0.0.0.0 \
    API_BASE=http://127.0.0.1:8001

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Node 24 runtime (runs the Next standalone server) — copied from the frontend build stage.
COPY --from=frontend /usr/local/bin/node /usr/local/bin/node

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
ENV PATH="/app/.venv/bin:$PATH"

COPY src/ ./src/
COPY scripts/ ./scripts/
COPY series/ ./series/
COPY book1/ ./book1/

# Next standalone server (+ static assets). Lives under /app/frontend; run with `node server.js`.
COPY --from=frontend /app/frontend/.next/standalone ./frontend/
COPY --from=frontend /app/frontend/.next/static ./frontend/.next/static

# Boot: provision schema (idempotent), start FastAPI on the internal port (8001), then the Next server
# on the public $PORT. `wait -n` exits (so Railway's ON_FAILURE restart kicks in) if either proc dies.
CMD ["bash", "-c", "python scripts/init_db.py && { hypercorn dominion.api.main:app --bind 127.0.0.1:8001 & (cd frontend && HOSTNAME=0.0.0.0 PORT=${PORT:-3000} exec node server.js) & wait -n; }"]
