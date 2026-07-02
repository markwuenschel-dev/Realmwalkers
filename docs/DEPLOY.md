# Deploy — Railway (single service)

The whole app ships as **one container**: the Dockerfile builds the Next.js frontend (standalone
output) and runs FastAPI alongside it. The browser loads the desk from Next and calls same-origin
`/api/desk/*`, which the Next BFF proxies to FastAPI — so there's no separate API URL, no CORS, and
no `localhost`. You get one Railway URL that just works.

## What's in the repo for this
- `Dockerfile` — builds the Next.js standalone server (`pnpm build`), installs Python deps via
  `uv sync --frozen`, runs FastAPI + Next in one container (Python 3.14, Node 24).
- `railway.json` — tells Railway to build the Dockerfile; runs `init_db.py` on boot.
- `.dockerignore` — keeps the build context lean.
- `shared/config.py` accepts a bare `DATABASE_URL` (Railway-style) and normalizes the scheme to asyncpg.

## One-time setup on Railway
1. **New Project → Deploy from GitHub repo →** pick `Realmwalkers`. Railway detects the Dockerfile.
2. **Add a database:** New → Database → **PostgreSQL**. (Railway's Postgres includes `pgvector`; the
   app runs `CREATE EXTENSION IF NOT EXISTS vector` on boot. If that ever fails, deploy the
   `pgvector/pgvector:pg16` image as the DB service instead.)
3. **Set variables** on the app service (Variables tab):
   - `DATABASE_URL` = `${{Postgres.DATABASE_URL}}` — reference the Postgres service's **private** URL
     (no SSL needed inside Railway). The app converts `postgresql://` → `postgresql+asyncpg://`.
   - `ANTHROPIC_API_KEY` = your key.
   - *(optional)* `OPENAI_API_KEY` / `XAI_API_KEY` — **required** to pick an OpenAI (`gpt-*`) or Grok
     (`grok-*`) model in the Settings model picker. The deploy does **not** read a `.env` file (it isn't
     in the image); env vars come from this tab. `OPENAI_API_KEY` also switches embeddings from the hash
     fallback to real OpenAI vectors.
   - *(optional)* `DOMINION_DRAFT_MODEL`, `DOMINION_REVIEW_MODEL`, `DOMINION_ENRICH_MODEL` to override
     the defaults in `config.py`.
4. **Deploy.** Railway builds the image, runs `init_db.py` (pgvector extension + tables), and serves on
   the generated URL. Open it — the desk loads and talks to the API same-origin.

## Notes
- **Drafting** runs as a background task in the web service (the browser-driven `/jobs/draft-next`
  drain), so no separate worker service is required. Railway services don't sleep, so background
  drafts finish.
- **A fresh Railway database starts empty** — scenes/books you created locally are **not** copied up
  automatically. To migrate existing data: `pg_dump` the local volume and restore into the Railway
  Postgres (ask and this can be scripted once the local DB is running).
- **Cost:** Railway is usage-based (~$5/mo hobby tier) on top of your own Anthropic API usage.
