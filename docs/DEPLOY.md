# Deploy — shared AWS box (Docker Compose + Caddy)

Realmwalkers no longer runs on Railway. It's now **one service in a shared single-box stack**: an AWS
EC2 instance running one Docker Compose project, a Caddy reverse proxy on the one public IP, and one
Postgres+pgvector shared across the box's apps. All app↔DB traffic stays on the box's internal Docker
network, so there's no metered egress (the ~$138 Railway egress surprise is structurally impossible here).

The app still ships as **one container**: the Dockerfile builds the Next.js frontend (standalone output)
and runs FastAPI alongside it. The browser loads the desk from Next and calls same-origin `/api/desk/*`,
which the Next BFF proxies to FastAPI — so there's no separate API URL, no CORS, and no `localhost`.

The deploy config lives in the **[`infra`](https://github.com/markwuenschel-dev/infra)** repo, not here.
This repo only provides the `Dockerfile` that Compose builds.

## Where it runs
- **Box:** EC2 `i-018796c951839031d` (t4g.small, us-east-1), Elastic IP `44.198.76.44`. Current IP via
  `aws ec2 describe-instances --filters Name=tag:Name,Values=shared-box --query 'Reservations[].Instances[].PublicIpAddress'`.
- **On-box layout:** apps are cloned as siblings under `/opt/stack/` (`/opt/stack/Realmwalkers`), with the
  Compose project + `Caddyfile` + env files in `/opt/stack/infra`.
- **URL:** `https://realmwalkers.44-198-76-44.nip.io`. nip.io is wildcard DNS — `*.44-198-76-44.nip.io`
  always resolves to the Elastic IP — so Caddy auto-issues a real Let's Encrypt cert per hostname (HTTP-01
  over port 80) with **no registered domain**. When a domain is registered, point its A record at the IP
  and swap the host in `infra/Caddyfile`.

## How Realmwalkers is wired (in `infra/docker-compose.yml`)
- **Service `realmwalkers`** — `build: ../Realmwalkers` (this repo's `Dockerfile`), listens on `:3000`, on
  both the `edge` (↔ Caddy) and `data` (↔ Postgres) networks. Only Caddy publishes ports (80/443); the app
  is never directly exposed.
- **Database** — the shared `postgres` service is `pgvector/pgvector:pg16`. Compose injects
  `DOMINION_DATABASE_URL=postgresql+asyncpg://app:<pw>@postgres:5432/realmwalkers` (private, internal-only —
  `config.py` reads `DOMINION_DATABASE_URL`, falling back to a bare `DATABASE_URL`). The `realmwalkers`
  database and its `vector` extension are created **once** by `infra/initdb/01-create-databases.sh` on the
  first boot of an empty Postgres volume; this repo's `scripts/init_db.py` then creates the tables on
  **every** container boot (idempotent — it's the Dockerfile's `CMD`).
- **Secrets** — `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, etc. live in `infra/env/realmwalkers.env` on the box
  (gitignored). `DOMINION_DATABASE_URL` and `PORT` are injected by Compose — do **not** set them there.
  `OPENAI_API_KEY` / `XAI_API_KEY` are **required** to pick an OpenAI (`gpt-*`) or Grok (`grok-*`) model in
  the Settings picker; `OPENAI_API_KEY` also switches embeddings from the hash fallback to real OpenAI
  vectors. Optional model overrides: `DOMINION_DRAFT_MODEL`, `DOMINION_REVIEW_MODEL`, `DOMINION_ENRICH_MODEL`.

## Deploy / redeploy (the day-to-day loop)
One command from your machine — it runs the whole loop below over ssh and ends with a health check
against the public URL:
```powershell
./scripts/deploy.ps1                # deploy latest main   (bash twin: ./scripts/deploy.sh)
./scripts/deploy.ps1 -Ref <sha>     # roll back to a specific commit (deploy.sh: pass the sha as $1)
```
Deploys are free per-run: the pull + docker build happen on the box (flat EC2 bill, no registry,
no metered egress), and DNS is not involved.

What the script does (the manual loop, if you'd rather ssh in yourself):
```bash
ssh -i ~/.ssh/shared-box.pem ubuntu@44.198.76.44          # current Elastic IP
cd /opt/stack/Realmwalkers && git pull                    # latest main
cd /opt/stack/infra && docker compose up -d --build realmwalkers
docker compose logs --tail=80 realmwalkers                # init_db (tables) → hypercorn (FastAPI) → next
```
Only `realmwalkers` rebuilds; Postgres, Caddy, and the other apps stay up. Roll back with
`git checkout <prev-sha>` then the same `up -d --build`. First-time box standup is in the infra repo's
[`PROVISION.md`](https://github.com/markwuenschel-dev/infra/blob/main/PROVISION.md).

## Notes
- **Drafting** runs as a background task in the web service (the browser-driven `/jobs/draft-next` drain),
  so no separate worker service is required. The container is `restart: unless-stopped` and the box stays
  warm, so background drafts finish.
- **A fresh box starts with an empty `realmwalkers` db** — scenes/books are **not** copied up automatically.
  To migrate existing data: `pg_dump` the old source and restore into the `postgres` container's
  `realmwalkers` db.
- **Cost:** a flat EC2 bill (t4g.small) shared across all four apps on the box, plus your own Anthropic API
  usage — no per-service or egress metering.
