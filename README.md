# Simple TMS

An open source, self-hosted transport management system for businesses that
deliver with their own trucks and also send freight with outside carriers.
It sits beside Simple WMS and Platen and works without either.

Status: **build step 1, the skeleton.** Sign-in for dispatchers and drivers,
vehicles and drivers, and consignments created from Simple WMS's
`delivery.packed` events. Runs, the planning screen and the driver app come
with the next steps in `docs/plans/2026-10-10-simple-tms-design.md`.

## What is in this repo

```
CLAUDE.md            rules for Claude Code when working here
docs/plans/          the design and the implementation plans
docs/api.md          every endpoint
design/              static HTML of each screen, and a gallery
api/                 FastAPI app, migrations, tests
deploy/Caddyfile     the only door in
docker-compose.yml   api, db, caddy
```

## Getting started

```
git clone https://github.com/q7-technology/simple-tms
cd simple-tms
cp .env.example .env            # set POSTGRES_PASSWORD and TMS_SECRET_KEY
docker compose up -d
docker compose exec api tms create-user --username you --role admin
```

The API runs its migrations each time it starts. It is at
`https://<TMS_DOMAIN>/v1` (default `https://localhost/`, self-signed) and its
interactive docs at `/docs`. Sign in with `POST /v1/auth/login`. The API is
also published on `127.0.0.1:8021` (`TMS_API_PORT`) for local work and health
checks. On a machine that already runs Simple WMS, its Caddy has ports 80 and
443: set `TMS_HTTP_PORT` and `TMS_HTTPS_PORT` in `.env` (say 8180 and 8543).
On a Mac, the system's ftp-proxy holds 8021: set `TMS_API_PORT=8022`.

## Connecting Simple WMS

Simple TMS is an ordinary subscriber to Simple WMS's signed events. In `.env`
set `TMS_WMS_WEBHOOK_SECRET` (a long random string), `TMS_WMS_URL` (Simple
WMS's address) and `TMS_WMS_API_KEY` (a Simple WMS key with `tasks:read`),
then register a subscriber in Simple WMS with the same secret, pointing at
`/v1/wms/events`. Each packed delivery becomes a consignment. Details are in
`docs/api.md`.

## Run the API outside Docker

```
docker compose up -d db         # Postgres on 127.0.0.1:5434
cd api
uv venv -p 3.12 && uv pip install -e ".[dev]"
export TMS_DATABASE_URL=postgresql+psycopg://tms:<POSTGRES_PASSWORD>@127.0.0.1:5434/tms
.venv/bin/alembic upgrade head
.venv/bin/tms create-user --username you --role admin
.venv/bin/uvicorn tms.api.app:app --reload --port 8021   # 8022 on a Mac
```

## Tests

pytest against a real PostgreSQL, so the append-only trigger is exercised for
real. The suite creates and resets a `tms_test` database on the compose `db`
service, expecting `POSTGRES_PASSWORD=tms-dev` locally; point
`TEST_DATABASE_URL` elsewhere to use another server.

```
docker compose up -d db
cd api && .venv/bin/pytest
```

## Stack

Python 3.12 + FastAPI · PostgreSQL 16 · Docker Compose · Caddy. The same
choices as Simple WMS, so one person can look after both.
