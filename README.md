# Simple TMS

An open source, self-hosted transport management system for businesses that
deliver with their own trucks and also send freight with outside carriers.
It sits beside Simple WMS and Platen and works without either.

Status: **build step 2, own fleet.** Runs planned on a map-and-drawer
desktop, run sheets printed through Platen, and Simple WMS told each delivery
shipped when its run leaves; on top of step 1 (sign-in, vehicles and drivers,
consignments from Simple WMS's `delivery.packed`). The driver app, fleet care,
live location and carriers come with the next steps in
`docs/plans/2026-10-10-simple-tms-design.md`.

## What is in this repo

```
CLAUDE.md            rules for Claude Code when working here
docs/plans/          the design and the implementation plans
docs/api.md          every endpoint
.github/workflows/   CI: the tests, and a Compose start
design/              static HTML of each screen, and a gallery
api/                 FastAPI app, migrations, tests
apps/desktop/        the dispatcher's desktop app (Vite + React)
deploy/Caddyfile     the only door in
docker-compose.yml   api, worker, db, caddy
```

## Getting started

```
git clone https://github.com/q7-technology/simple-tms
cd simple-tms
cp .env.example .env            # set POSTGRES_PASSWORD, and TMS_SECRET_KEY to: openssl rand -hex 32
(cd apps/desktop && npm ci && npm run build)
docker compose up -d
docker compose exec api tms create-user --username you --role admin
```

The desktop is at `https://<TMS_DOMAIN>/` (default `https://localhost/`,
self-signed): sign in, then **Plan runs**. The API runs its migrations each
time it starts; it is at `/v1`, its interactive docs at `/docs`, and it is
also published on `127.0.0.1:8022` (`TMS_API_PORT`) for local work. The
`worker` makes the calls to Simple WMS and sends run sheets to Platen, taking
one of each in turn so neither queue holds up the other; it needs no port. Rebuild the desktop (`npm run build`) after pulling changes to
`apps/desktop`; Caddy serves `apps/desktop/dist` as it is.

## Production on a real domain

Let's Encrypt only checks a domain on ports 80 and 443, so Caddy gets a
certificate for a real domain only when it listens on those two ports. There
are two supported set-ups.

**On its own server.** Leave `TMS_HTTP_PORT` and `TMS_HTTPS_PORT` at their
defaults (80 and 443), set `TMS_DOMAIN=tms.example.com` with its DNS pointing
at the server, and `docker compose up -d`. Caddy gets the certificate itself.
Give Simple WMS the subscriber URL `https://tms.example.com/v1/wms/events`.

**Beside Simple WMS on one server.** Only one program can hold 80 and 443,
and Simple WMS's Caddy already does. Put the TMS domain on that Caddy and do
not run the TMS one: start Simple TMS with
`docker compose up -d db api worker` (the TMS caddy stays down), so the TMS API listens on `127.0.0.1:8022` only.

- If the Caddy that holds 80 and 443 runs on the host (a shared front Caddy,
  installed from a package), add a site to its Caddyfile:

  ```
  tms.example.com {
  	encode gzip
  	header {
  		X-Content-Type-Options nosniff
  		Referrer-Policy same-origin
  	}
  	@api path /v1/* /docs* /openapi.json
  	handle @api {
  		reverse_proxy 127.0.0.1:8022
  	}
  	handle {
  		header Content-Security-Policy "default-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
  		root * /path/to/simple-tms/apps/desktop/dist
  		try_files {path} /index.html
  		file_server
  	}
  }
  ```

- If it is Simple WMS's own Caddy, which runs in Docker, `127.0.0.1` inside
  that container is the container itself, not the host. Join it to the TMS
  network with a `docker-compose.override.yml` beside Simple WMS's
  `docker-compose.yml`:

  ```yaml
  services:
    caddy:
      networks: [default, simple-tms]
      volumes:
        # the TMS desktop build, served by this Caddy too
        - /path/to/simple-tms/apps/desktop/dist:/srv/tms-desktop:ro
  networks:
    simple-tms:
      external: true
      name: simple-tms_default   # <TMS compose project>_default
  ```

  and add a site to Simple WMS's `deploy/Caddyfile` that names the TMS API
  container (`docker ps` shows its name; `simple-tms-api-1` when the TMS
  project is called `simple-tms`). Not `api:8000`: that is Simple WMS's own
  API.

  ```
  tms.example.com {
  	encode gzip
  	header {
  		X-Content-Type-Options nosniff
  		Referrer-Policy same-origin
  	}
  	@api path /v1/* /docs* /openapi.json
  	handle @api {
  		reverse_proxy simple-tms-api-1:8000
  	}
  	handle {
  		header Content-Security-Policy "default-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
  		root * /srv/tms-desktop
  		try_files {path} /index.html
  		file_server
  	}
  }
  ```

  Then `docker compose up -d caddy` in the Simple WMS folder. Its Caddy gets
  the certificate for `tms.example.com` as it does for its own domain.

Either way, give Simple WMS the subscriber URL
`https://tms.example.com/v1/wms/events`.

**Other ports are for local work and a LAN only.** `TMS_HTTP_PORT` and
`TMS_HTTPS_PORT` (say 8180 and 8543) let the TMS caddy run beside Simple
WMS's on one machine, but no real domain gets a certificate there: Caddy
uses its own local certificate, which browsers and Simple WMS do not trust
unless you install its root. For a Simple WMS on the same machine, the
subscriber URL is the published API port instead:
`http://host.docker.internal:8022/v1/wms/events` with Docker Desktop (Mac,
Windows).

## Connecting Simple WMS

Simple TMS is an ordinary subscriber to Simple WMS's signed events. In `.env`
set `TMS_WMS_WEBHOOK_SECRET` (a long random string), `TMS_WMS_URL` (Simple
WMS's address) and `TMS_WMS_API_KEY` (a Simple WMS key with `tasks:read` and
`tasks:write`: `tasks:read` to read a packed delivery's ship-to, `tasks:write`
to say it shipped), then register a subscriber in Simple WMS with the same
secret, pointing at `/v1/wms/events`. Each packed delivery becomes a
consignment. Details are in `docs/api.md`.

When a run leaves, Simple TMS calls `POST /v1/deliveries/{ref}/ship` for each
consignment with carrier "Own fleet" and the run number as the tracking
number, retrying after 1 min, 5, 30 and 2 h if Simple WMS is away; Simple WMS
then fires `delivery.shipped` as usual.

## Connecting Platen

Run sheets print through Platen (or any print service that takes the same
job). In Platen, make a page template with the id `run-sheet` and publish it
(the fields it binds to are in `docs/api.md` under "Run sheets"), and make an
operator key under People → Keys. In Simple TMS's `.env` set
`TMS_PLATEN_URL=https://<platen>/intake/jobs`, `TMS_PLATEN_KEY` to that key
and `TMS_RUN_SHEET_PRINTER` to the Platen printer's id or name, then
`docker compose up -d api worker`.

So that Platen can say each run sheet printed, give the Platen key a callback:
the address of Simple TMS (`https://tms.example.com`) and a Simple TMS key
made with

```
docker compose exec api tms create-api-key --name platen --scopes printing:write
```

(Platen's keys screen has the two fields; Platen 0014 or later.)

## Run the API outside Docker

```
docker compose up -d db         # Postgres on 127.0.0.1:5434
cd api
uv venv -p 3.12 && uv pip install -e ".[dev]"
export TMS_DATABASE_URL=postgresql+psycopg://tms:<POSTGRES_PASSWORD>@127.0.0.1:5434/tms
.venv/bin/alembic upgrade head
.venv/bin/tms create-user --username you --role admin
.venv/bin/uvicorn tms.api.app:app --reload --port 8022
.venv/bin/python -m tms.worker      # in another terminal: ship calls and run sheets
```

## Run the desktop

```
cd apps/desktop
npm ci
npm run dev                     # http://localhost:5174
```

The dev server sends `/v1` to the API on `http://127.0.0.1:8022`;
`TMS_API_URL=https://… npm run dev` points it at another. `npm test` runs the
desktop's tests, `npm run typecheck` its type check, and `npm run build`
writes `dist/` for Caddy.

## Tests

pytest against a real PostgreSQL, so the append-only trigger is exercised for
real. The suite creates and resets a `tms_test` database on the compose `db`
service, expecting `POSTGRES_PASSWORD=tms-dev` locally; point
`TEST_DATABASE_URL` elsewhere to use another server.

```
docker compose up -d db
cd api && .venv/bin/pytest
```

The desktop's tests are Vitest, in jsdom, with the API stubbed:

```
cd apps/desktop && npm test
```

## Stack

Python 3.12 + FastAPI · PostgreSQL 16 · Vite + React + TypeScript +
Tailwind · Docker Compose · Caddy. The same
choices as Simple WMS, so one person can look after both.

## Licence

MIT © 2026 Q7 Technology
