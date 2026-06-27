# Single-service image: Next.js (public) + FastAPI (internal), one container. The browser loads the
# desk from Next and calls same-origin /api/desk/*, which the Next BFF proxies to FastAPI on
# 127.0.0.1:8000 — so there is still no separate API host, no CORS, and no "localhost" in the client.

# --- 1) build the Next.js frontend -> standalone server -----------------------------------------
FROM node:20-slim AS frontend
WORKDIR /app/frontend
# The frontend uses pnpm (pnpm-lock.yaml, no package-lock.json); install pnpm 10 to match CI.
RUN npm install -g pnpm@10
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
# output: "standalone" => .next/standalone/server.js + traced node_modules; static assets separate.
RUN pnpm build

# --- 2) python + node runtime --------------------------------------------------------------------
FROM python:3.12-slim AS app
WORKDIR /app
# Next serves the public $PORT (Railway sets PORT=8000); FastAPI runs on a distinct internal port so
# the two never collide. API_BASE points the BFF at that internal port.
ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    NODE_ENV=production \
    HOSTNAME=0.0.0.0 \
    API_BASE=http://127.0.0.1:8001

# Node 20 runtime (runs the Next standalone server) alongside Python.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && apt-get purge -y curl && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

# Runtime deps only (we run from source, so repo-relative paths — series/, book1/ — resolve under
# /app). Keep in sync with pyproject.toml [project.dependencies].
RUN pip install --no-cache-dir \
    "fastapi>=0.115" "uvicorn[standard]>=0.32" "sqlalchemy[asyncio]>=2.0" "asyncpg>=0.30" \
    "pgvector>=0.3" "pydantic>=2.9" "pydantic-settings>=2.6" "anthropic>=0.40" "structlog>=24.4" \
    "python-dotenv>=1.0" "httpx>=0.27"

COPY src/ ./src/
COPY scripts/ ./scripts/
COPY series/ ./series/
COPY book1/ ./book1/

# Next standalone server (+ static assets). Lives under /app/frontend; run with `node server.js`.
COPY --from=frontend /app/frontend/.next/standalone ./frontend/
COPY --from=frontend /app/frontend/.next/static ./frontend/.next/static

# Boot: provision schema (idempotent), start FastAPI on the internal port (8001), then the Next server
# on the public $PORT. `wait -n` exits (so Railway's ON_FAILURE restart kicks in) if either proc dies.
CMD ["bash", "-c", "python scripts/init_db.py && { uvicorn dominion.api.main:app --host 127.0.0.1 --port 8001 & (cd frontend && HOSTNAME=0.0.0.0 PORT=${PORT:-3000} exec node server.js) & wait -n; }"]
