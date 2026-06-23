# Single-service image: build the React app, then serve it + the FastAPI API from one container.
# Designed for Railway (or any Docker host). The frontend talks to the API same-origin, so there is
# no separate API URL, no CORS, and no "localhost".

# --- 1) build the frontend -> /app/frontend/dist ------------------------------------------------
FROM node:20-slim AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install
COPY frontend/ ./
# PROD build => the client uses relative (same-origin) API paths (see desk/api/client.ts).
RUN npm run build

# --- 2) python runtime ---------------------------------------------------------------------------
FROM python:3.12-slim AS app
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONPATH=/app/src

# Runtime deps only (we run from source, not pip-installed, so the app's repo-relative paths —
# novel/, frontend/dist, novel/style/dialogue_rules.md — resolve under /app). Keep in sync with
# pyproject.toml [project.dependencies].
RUN pip install --no-cache-dir \
    "fastapi>=0.115" "uvicorn[standard]>=0.32" "sqlalchemy[asyncio]>=2.0" "asyncpg>=0.30" \
    "pgvector>=0.3" "pydantic>=2.9" "pydantic-settings>=2.6" "anthropic>=0.40" "structlog>=24.4" \
    "python-dotenv>=1.0" "httpx>=0.27"

COPY src/ ./src/
COPY scripts/ ./scripts/
COPY novel/ ./novel/
COPY --from=frontend /app/frontend/dist ./frontend/dist

# Railway provides $PORT. Create the pgvector extension + tables (idempotent) before serving.
CMD ["sh", "-c", "python scripts/init_db.py && uvicorn dominion.api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
