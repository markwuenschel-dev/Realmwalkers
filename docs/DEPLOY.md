# Deploy

No deploy target; shared-box decommissioned 2026-11-26.

## What this repo provides

The app still ships as **one container**: the Dockerfile builds the Next.js frontend (standalone output)
and runs FastAPI alongside it. The browser loads the desk from Next and calls same-origin `/api/desk/*`,
which the Next BFF proxies to FastAPI — so there's no separate API URL, no CORS, and no `localhost`.

## What the container reads

- **Database** — `config.py` reads `DOMINION_DATABASE_URL`, falling back to a bare `DATABASE_URL`. This
  repo's `scripts/init_db.py` creates the `vector` extension and all tables on **every** container boot
  (idempotent — it's the Dockerfile's `CMD`), so the schema self-heals; only the database itself has to
  pre-exist.
- **Secrets** — `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, etc. `OPENAI_API_KEY` / `XAI_API_KEY` are
  **required** to pick an OpenAI (`gpt-*`) or Grok (`grok-*`) model in the Settings picker;
  `OPENAI_API_KEY` also switches embeddings from the hash fallback to real OpenAI vectors. Optional model
  overrides: `DOMINION_DRAFT_MODEL`, `DOMINION_REVIEW_MODEL`, `DOMINION_ENRICH_MODEL`.

## Notes

- **Drafting** runs as a background task in the web service (the browser-driven `/jobs/draft-next` drain),
  so no separate worker service is required.
- **Push the style documents, or drafting refuses to run.** `series/` is gitignored and excluded from the
  image, so in a deployed container the style guidance exists only in the `style_documents` table.
  `python -m dominion.tools.push_style` writes them there. Two of them — `forbidden_drift` and
  `dialogue_rules` — are a fail-closed draft gate (`workers/context/style_source.py`): without them
  `can_draft` is false and the Desk's Draft button stays disabled, because a draft generated without its
  guidance looks identical to a good one at every other surface. The Edit desk's audit reads those plus
  `prose_clarity_rules`, `prose_contract` and `voice_guide`, and returns 503 when none are present. This is
  a **data** step, not a code step: a deploy alone never carries it.
