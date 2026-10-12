# Simple TMS

An open source, self-hosted transport management system for businesses that
deliver with their own trucks and also send freight with outside carriers.
It sits beside Simple WMS and Platen and works without either.

Status: **build step 3, the driver app.** Drivers sign in on their phones,
start the run, deliver each stop with a signature, a photo or both (or say
why they couldn't), and finish the run, with or without signal; on top of
step 2 (runs planned on a map-and-drawer desktop, run sheets printed through
Platen, Simple WMS told each delivery shipped when its run leaves) and step 1
(sign-in, vehicles and drivers, consignments from Simple WMS's
`delivery.packed`). Fleet care, live location and carriers come with the
next steps in `docs/plans/2026-10-10-simple-tms-design.md`.

## What is in this repo

```
CLAUDE.md            rules for Claude Code when working here
docs/plans/          the design and the implementation plans
docs/api.md          every endpoint
.github/workflows/   CI: the tests, and a Compose start
design/              static HTML of each screen, and a gallery
api/                 FastAPI app, migrations, tests
apps/desktop/        the dispatcher's desktop app (Vite + React)
apps/driver/         the driver's phone app, a PWA at /driver/ (Vite + React)
deploy/Caddyfile     the only door in
docker-compose.yml   api, worker, db, caddy
```

## Getting started

```
git clone https://github.com/q7-technology/simple-tms
cd simple-tms
cp .env.example .env            # set POSTGRES_PASSWORD, and TMS_SECRET_KEY to: openssl rand -hex 32
(cd apps/desktop && npm ci && npm run build)
(cd apps/driver && npm ci && npm run build)
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

The driver app is at `https://<TMS_DOMAIN>/driver/`. A driver signs in with
their code (in any case) and PIN (given when the driver is made, `POST /v1/drivers`, or
changed by a manager, `PATCH /v1/drivers/{code}` with a new `pin`), starts
the day's run, and works through it one stop at a time; it keeps working with
no signal and sends what waited when the signal is back, each confirmation
once however often it is sent. A consignment that couldn't be delivered goes
back to the **Plan runs** tray, marked "Came back: not delivered", to be
planned again. A driver who is back but didn't finish on the phone can be
finished from **Plan runs** (**Finish run**, once every stop is delivered or
coming back). Rebuild it (`npm run build` in `apps/driver`) after pulling
changes; Caddy serves `apps/driver/dist` as it is. Signatures and photos are
kept in the `pod_files` volume: back it up with the database.

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
  	@driverbare path /driver
  	redir @driverbare /driver/ 308
  	handle_path /driver/* {
  		header Content-Security-Policy "default-src 'self'; img-src 'self' blob:; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
  		header /sw.js Cache-Control "no-cache"
  		@driverpage not path /assets/* /sw.js /manifest.webmanifest /favicon.svg /icon-192.png /icon-512.png
  		header @driverpage Cache-Control "no-cache"
  		root * /path/to/simple-tms/apps/driver/dist
  		try_files {path} /index.html
  		file_server
  	}
  	handle {
  		header Content-Security-Policy "default-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
  		@desktoppage not path /assets/* /favicon.svg /region.svg
  		header @desktoppage Cache-Control "no-cache"
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
        # the TMS desktop and driver app builds, served by this Caddy too
        - /path/to/simple-tms/apps/desktop/dist:/srv/tms-desktop:ro
        - /path/to/simple-tms/apps/driver/dist:/srv/tms-driver:ro
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
  	@driverbare path /driver
  	redir @driverbare /driver/ 308
  	handle_path /driver/* {
  		header Content-Security-Policy "default-src 'self'; img-src 'self' blob:; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
  		header /sw.js Cache-Control "no-cache"
  		@driverpage not path /assets/* /sw.js /manifest.webmanifest /favicon.svg /icon-192.png /icon-512.png
  		header @driverpage Cache-Control "no-cache"
  		root * /srv/tms-driver
  		try_files {path} /index.html
  		file_server
  	}
  	handle {
  		header Content-Security-Policy "default-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
  		@desktoppage not path /assets/* /favicon.svg /region.svg
  		header @desktoppage Cache-Control "no-cache"
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

## Run the driver app

```
cd apps/driver
npm ci
npm run dev                     # http://localhost:5175/driver/, and on this computer's LAN address
npm run build && npm run preview   # the built app with its service worker, on 4175
```

Both send `/v1` to the API on `http://127.0.0.1:8022` (`TMS_API_URL=…` for
another). `npm test` runs its tests and `npm run typecheck` its type check.
The service worker (the app opening with no signal) is only in the build,
not in `npm run dev`.

**A phone two drivers share.** What a driver confirms waits on the phone
for that driver: it is sent only while they are signed in, with their own
sign-in. If someone else signs in meanwhile, Today says whose confirmations
are waiting, and they go when that driver signs in again. Each driver's copy
of their run is kept apart, so nobody sees another driver's run.

**Through Caddy on this computer.** At `https://localhost:8543/driver/` (with
`TMS_HTTPS_PORT=8543`) the app works, but the browser won't register its
service worker over a certificate it doesn't trust, and Caddy's local one
isn't, so the app won't open with no signal. To try that, trust Caddy's
root: copy it out with
`docker compose cp caddy:/data/caddy/pki/authorities/local/root.crt caddy-root.crt`,
then on a Mac
`sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain caddy-root.crt`
(on Ubuntu, copy it into `/usr/local/share/ca-certificates/` and run
`sudo update-ca-certificates`; Firefox keeps its own list, under Settings →
Privacy & Security → Certificates). Restart the browser. Or use
`npm run preview` at `http://localhost:4175/driver/`: `localhost` counts as
secure without a certificate.

**On a phone.** A phone's browser gives the camera to any page, but location
and the service worker only to a secure one: `https://` with a certificate
the phone trusts, or `http://localhost`.

- **Android:** turn on USB debugging, plug the phone in, open
  `chrome://inspect/#devices` in Chrome on the computer and under **Port
  forwarding** forward `4175` to `localhost:4175`. Run `npm run build && npm
  run preview` and open `http://localhost:4175/driver/` in Chrome on the
  phone. Everything works, black spots included (aeroplane mode).
- **iPhone:** serve it through Caddy on the computer's LAN address. Set
  `TMS_DOMAIN` to that address (say `192.168.1.20`) in `.env` and
  `docker compose up -d caddy`; copy Caddy's local root certificate out as
  above, AirDrop it to the phone, install the profile (**Settings → Profile
  Downloaded**) and turn on full trust for it (**Settings → General → About →
  Certificate Trust Settings**). Open `https://192.168.1.20:8543/driver/`.
  Put `TMS_DOMAIN` back afterwards: the desktop answers on that one name only.
- **Any phone, just to look:** `npm run dev` and open
  `http://<the computer's LAN address>:5175/driver/`. No location and no
  offline start, but the screens, the camera and the queue all work.

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

The driver app's tests are Vitest too, with the API stubbed and IndexedDB
stood in for by `fake-indexeddb`: `cd apps/driver && npm test`.

## Stack

Python 3.12 + FastAPI · PostgreSQL 16 · Vite + React + TypeScript +
Tailwind · Docker Compose · Caddy. The same
choices as Simple WMS, so one person can look after both.

## Licence

MIT © 2026 Q7 Technology
