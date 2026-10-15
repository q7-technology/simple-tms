# Simple TMS

An open source, self-hosted transport management system for businesses that
deliver with their own trucks and also send freight with outside carriers.
It sits beside Simple WMS and Platen and works without either.

Status: **build step 6, carriers**, the last step of version one in
`docs/plans/2026-10-10-simple-tms-design.md`. A dispatcher sends a
consignment with an outside carrier from **Plan runs**, types or scans its
con note in **Carriers**, prints the carrier's manifest through Platen, and
marks it collected when the carrier calls: Simple WMS is then told it
shipped, with the carrier's name and the con note. **Consignments** finds
any consignment by its WMS delivery, customer or con note, with its history
and proof of delivery. On top of step 5 (live location: the trucks on the
map, ETAs, what just happened, **Where is it**), step 4 (pre-start checks,
grounding, rego and service due), step 3 (the driver app: start the run,
deliver each stop with a signature, a photo or both, or say why not, and
finish, with or without signal), step 2 (runs planned on a map-and-drawer
desktop, run sheets printed through Platen, Simple WMS told each delivery
shipped when its run leaves) and step 1 (sign-in, vehicles and drivers,
consignments from Simple WMS's `delivery.packed`).

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
self-signed): sign in, add your trucks and drivers in **Fleet** and the
carriers you use in **Carriers**, then **Plan runs**. The API runs its migrations each
time it starts; it is at `/v1`, its interactive docs at `/docs`, and it is
also published on `127.0.0.1:8022` (`TMS_API_PORT`) for local work. The
`worker` makes the calls to Simple WMS and sends run sheets and carrier
manifests to Platen, taking
one of each in turn so neither queue holds up the other, and deletes positions
past the retention period; it needs no port. Rebuild the desktop (`npm run build`) after pulling changes to
`apps/desktop`; Caddy serves `apps/desktop/dist` as it is.

The driver app is at `https://<TMS_DOMAIN>/driver/`. A driver signs in with
their code (in any case) and PIN (given when a fleet manager adds them in
**Fleet** → **Drivers**, and set again there, which also unlocks a driver
locked out by five wrong PINs), starts
the day's run, and works through it one stop at a time; it keeps working with
no signal and sends what waited when the signal is back, each confirmation
once however often it is sent. A consignment that couldn't be delivered goes
back to the **Plan runs** tray, marked "Came back: not delivered", to be
planned again. A driver who is back but didn't finish on the phone can be
finished from **Plan runs** (**Finish run**, once every stop is delivered or
coming back). Rebuild it (`npm run build` in `apps/driver`) after pulling
changes; Caddy serves `apps/driver/dist` as it is. Signatures and photos are
kept in the `pod_files` volume: back it up with the database.

**Fleet care.** Before starting a run the driver does the truck's pre-start
check on the phone: the odometer (the phone shows the last reading and won't
take a lower one), then each item passed or failed, a failed one with a
photo and a note. It goes through the phone's queue like a delivery, so it
can be done with no signal, but the run starts only once the office has it.
A failed critical item (Lights and Brakes, to start with) grounds the truck;
a fleet manager (the `manager` role, or an admin) opens **Fleet**, looks at
the check with its photos and notes, and marks it fixed with what was done.
The check that grounded the truck never counts as the run's pre-start: once
it is marked fixed, the driver does a fresh check of the truck before
starting. A check is kept against the truck the driver was shown: if the
office gives the run another truck meanwhile, the check (and any grounding)
stays with the truck inspected, and the phone asks for a fresh check of the
run's new truck. In **Fleet** a fleet manager also records each new rego expiry and
each service (with its odometer reading), and puts a wrong odometer reading
right, and adds, changes (type, limits, how often it is serviced),
deactivates and reactivates trucks. A truck with nothing recorded is never
due, so record each truck's rego expiry and last service when you start. The
checklists start as the design's six items for each type of vehicle; a fleet
manager changes them in **Fleet** → **Pre-start checklists** (a line taken out
is retired, and checks already done keep it). A truck or a driver with a run
planned or on the road can't be deactivated: **Fleet** names the run to give
another truck or driver, or to finish, first.

**Settings.** An admin changes some of `.env`'s settings in **Settings**
(the account menu, top right) without editing `.env` or restarting:
printers, the Simple WMS and Platen addresses and keys, waits, how long
positions are kept and the ETA's first guess. `.env` gives each its default
and **Use .env's** puts one back; the API and the worker take a change within
5 seconds. Keys and secrets can be replaced there but are never shown again.
An address and its key go together, so a key is only ever sent where it was
given for: a new address needs its key typed with it, **Use .env's** on the
address puts the key back too, and the key alone goes back to `.env`'s only
while `.env`'s address is in force.
The database, `TMS_SECRET_KEY`, the proof-of-delivery folder, the map tiles
(Caddy builds the desktop's Content-Security-Policy from them when it
starts), the ports and the domain stay in `.env` only. Each change is
recorded with who made it (`docs/api.md`, "Settings"). Compose gives every
`TMS_*` setting in `.env` to both the API and the worker; one left empty or
out is Simple TMS's own default.

**Live location.** From **Start run** until the run is finished, the driver's
phone tells the office where it is about every 30 seconds, and its top bar
says **Sharing location**; never before Start or after the run is finished.
It needs the phone's permission (asked once, at Start) and the app open: a
web app can't share from the background. In a black spot nothing piles up:
once the signal is back only the newest position goes. On the desktop's map
each run's truck is drawn on a road of its own, further along as its stops
are done (the drawn region is a picture, not real geography); pick one to
see when it was last heard from, its next stop and when it should get there,
the load left and when it should be back, all estimates from the stop order
and the driver's average time per stop so far (`TMS_ETA_MINUTES_PER_STOP`,
20, until the first stop is done). **Where is it** shows each truck where its
phone last said, on a real map once map tiles are set up (below), and as a
list either way. Only the latest position is shown; there are no trails.
Dispatchers, managers and admins see it (`location:read`); a system needs a
key made with `location:read`. Only the run's driver sends positions
(`location:write`); a tracker in the truck needs a key made with
`location:write` named, and may then post for any run on the road.

**Positions are deleted after 90 days** (`TMS_POSITION_RETENTION_DAYS`):
once an hour the worker deletes older positions and clears the position kept
with each proof of delivery; the deliveries themselves are kept. Each go
logs a line in `docker compose logs worker` ("forgot 12 positions, and where
3 deliveries were, older than 90 days", zeros included). Some states
regulate tracking employees (for example the NSW Workplace Surveillance Act,
which asks for notice to staff before tracking starts): check your own
obligations, tell your drivers, and set the retention period to suit.

## A demo day

`tms seed-demo` fills an empty Simple TMS with a working day at a made-up
Ballarat depot, for trying it out, showing it or taking screenshots: four
trucks (a ute grounded by its pre-start, a van's service due soon), four
drivers, two sample carriers, a run finished this morning with a stop that
came back, a run on the road with its truck's position, a van nearly full for
this afternoon, one carrier pickup collected and another on the carrier bay,
and three consignments in the tray. Every business, person, phone number and
picture in it is invented.

It works through the API as the people who would do it, so every rule
applies and the history says who did what. The freight is real Simple WMS
freight: it creates, picks and packs 19 deliveries in Simple WMS with
`TMS_WMS_API_KEY`, or the key in **Settings** (it already has
`tasks:write`), and waits for each `delivery.packed`, so Simple WMS must be
connected (see "Connecting Simple WMS") with its worker running. The stop
that came back is returned to Simple WMS when its run is finished, so the
warehouse needs its `returns` zone. Times are when it runs, as the API takes
nothing back-dated: the run on the road drives for about 7 minutes, sending
its position as the driver's phone would (`--pace`, minutes a stop, 3 unless
given).

    docker compose exec api tms seed-demo --skus JKL012,FG-900

`--skus` names Simple WMS SKUs with stock in `BAL-WH01` (`--warehouse`),
picked and packed by `op-017` (`--operator`); the defaults suit a Simple WMS
filled with `wms seed-demo`. It refuses a database with any trucks, drivers,
carriers, runs or consignments in it. It makes the desk users `dee`
(dispatcher) and `sam` (manager) with the password `demo-password-1`; the
drivers `tom`, `kim`, `ana` and `raj` sign in with PIN `2468`. It prints what
it made and never a key. With printing set up, the run sheets and manifests
go to Platen. To start again, empty the database and the `pod_files` volume.

## Map tiles

**Where is it** draws a real map from raster map tiles. Simple TMS doesn't
come with any, and won't use OpenStreetMap's own tile servers (their policy
rules out apps), so until you set them up it lists each truck's position as
coordinates. Two ways:

- **A tile provider** (MapTiler, Stadia Maps, Thunderforest and others have
  free tiers for small use; each needs an account). Set
  `TMS_MAP_TILE_URL` to its raster address with `{z}`, `{x}` and `{y}` and
  your key (one host: not `{s}` subdomains), `TMS_MAP_ATTRIBUTION` to the
  credit it asks for (HTML: the map shows it as it is, so a provider's credit
  with a link in it works), and `TMS_MAP_TILE_ORIGIN` to its `https://host`
  (with `:port` if it has one, and nothing more) so the desktop's
  Content-Security-Policy lets the tiles in. For example:

  ```
  TMS_MAP_TILE_URL=https://api.maptiler.com/maps/streets-v2/256/{z}/{x}/{y}.png?key=<your key>
  TMS_MAP_ATTRIBUTION=© MapTiler © OpenStreetMap contributors
  TMS_MAP_TILE_ORIGIN=https://api.maptiler.com
  ```

  The key is in every tile address, so browsers see it: restrict it to your
  domain in the provider's settings.
- **Your own tile server** (for example `tileserver-gl` serving an extract
  for your state). On another host, as above. Behind this Caddy, give it a
  path, say `/tiles/*`, with `TMS_MAP_TILE_URL=/tiles/{z}/{x}/{y}.png`; it is
  then the same origin and `TMS_MAP_TILE_ORIGIN` stays empty.

Then `docker compose up -d api` and `docker compose up -d --force-recreate
caddy`. A tile address the API can't use (no `{z}`, `{x}` and `{y}`, not
`https`, `{s}` in it, or OpenStreetMap's own), or a `TMS_MAP_TILE_ORIGIN`
that isn't exactly the tile address's own origin (or empty, for a path on
this server), stops it starting, with the reason in
`docker compose logs api`. If the tiles don't load, **Where is it** says so.

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
  		# add your map tile provider's https://host after blob: (see Map tiles)
  		header Content-Security-Policy "default-src 'self'; img-src 'self' blob:; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
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
  		# add your map tile provider's https://host after blob: (see Map tiles)
  		header Content-Security-Policy "default-src 'self'; img-src 'self' blob:; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
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
(or as an admin, in **Settings**) set `TMS_WMS_WEBHOOK_SECRET` (a long random string), `TMS_WMS_URL` (Simple
WMS's address) and `TMS_WMS_API_KEY` (a Simple WMS key with `tasks:read` and
`tasks:write`: `tasks:read` to read a packed delivery's ship-to, `tasks:write`
to say it shipped or came back), then register a subscriber in Simple WMS with the same
secret, pointing at `/v1/wms/events`. Each packed delivery becomes a
consignment. Details are in `docs/api.md`.

When a run leaves, Simple TMS calls `POST /v1/deliveries/{ref}/ship` for each
consignment with carrier "Own fleet" and the run number as the tracking
number, retrying after 1 min, 5, 30 and 2 h if Simple WMS is away; Simple WMS
then fires `delivery.shipped` as usual. When a carrier collects, the same
call goes with the carrier's name and the con note as the tracking number.

What a driver couldn't deliver comes back on the truck. When the run is
finished (by the driver, or **Finish run** in the office), Simple TMS calls
`POST /v1/deliveries/{ref}/return` for each consignment that came back, with
the driver's reason and the run number; Simple WMS puts it on its ledger at
the warehouse's returns dock and fires `delivery.returned`. When it goes out
again, on a run or with a carrier, it is shipped again with the new run
number or con note, and Simple WMS fires `delivery.shipped` again. Each
warehouse in Simple WMS needs a zone of kind `returns` (its Locations
screen): without one, Simple WMS refuses the return and **Plan runs** says
so beside **Try again**. A consignment's calls go in the order they were
made, so Simple WMS never hears it went again before it hears it came back.
A call that gave up holds the later ones for its consignment: **Plan runs**
and **Carriers** say which call ("Waiting on R-0007's return to Simple WMS,
which failed: …"), and **Try again** on either starts that call again too.

## Connecting Platen

Run sheets print through Platen (or any print service that takes the same
job). In Platen, make a page template with the id `run-sheet` and publish it
(the fields it binds to are in `docs/api.md` under "Run sheets"), and make an
operator key under People → Keys. In Simple TMS's `.env` (or as an admin, in
**Settings**, with no restart) set
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

Carrier manifests print the same way, from a page template with the id
`carrier-manifest` (the fields are in `docs/api.md` under "Manifests"), on
the printer `TMS_MANIFEST_PRINTER` (empty: the run sheets' printer). A
starting point, made with Platen's admin session (or in its studio's page
editor) and then published:

```
curl -s -b "$PLATEN_COOKIE" -X PUT https://<platen>/pages/carrier-manifest \
  -H 'Content-Type: application/json' -d @- <<'JSON'
{
  "id": "carrier-manifest",
  "name": "Carrier manifest (Simple TMS)",
  "folder": "Simple TMS",
  "paper": "A4",
  "margin_mm": 15,
  "header": [
    {"kind": "text", "value": "{{ carrier }} · manifest {{ manifest_no }}", "size_pt": 18, "bold": true},
    {"kind": "text", "value": "{{ date }} · account {{ account }} · pickup {{ pickup_time }}", "size_pt": 11},
    {"kind": "text", "value": "Consignments: {{ consignment_count }} · packages: {{ package_count }} · {{ total_kg }} kg", "size_pt": 10},
    {"kind": "rule"}
  ],
  "body": [
    {"kind": "barcode", "value": "{{ manifest_no }}", "height_mm": 10},
    {"kind": "table", "size_pt": 9, "columns": [
      {"title": "#", "value": "{{ line }}", "width_mm": 8, "align": "right"},
      {"title": "Con note", "value": "{{ con_note }}", "width_mm": 34},
      {"title": "Customer", "value": "{{ customer }}"},
      {"title": "Address", "value": "{{ address }}, {{ suburb }} {{ state }} {{ postcode }}"},
      {"title": "Pkgs", "value": "{{ packages }}", "width_mm": 12, "align": "right"},
      {"title": "kg", "value": "{{ weight_kg }}", "width_mm": 16, "align": "right"}
    ]}
  ],
  "footer": [
    {"kind": "text", "value": "Received by the carrier's driver: name ______________ signed ______________", "size_pt": 9},
    {"kind": "text", "value": "{{ manifest_no }} · page {{ page }} of {{ pages }}", "size_pt": 8, "align": "right"}
  ]
}
JSON
curl -s -b "$PLATEN_COOKIE" -X POST https://<platen>/templates/carrier-manifest/publish
```

## Carriers

Freight going with outside carriers is booked by hand in this version: book
with the carrier as you always have (its website or a phone call), then in
Simple TMS:

1. **Add the carriers** you use, once, as a fleet manager or admin: in
   **Carriers** → **Your carriers**, **Add a carrier** with its name as Simple
   WMS should record it, your account number and when it usually calls.
   Rename or retire one there too (a retired carrier takes no new bookings;
   what is on its bay stays, to be collected).
2. In **Plan runs**, send a consignment **With a carrier…** (from the tray,
   or off a planned run with **Move to…**). It goes on that carrier's
   manifest for the day being planned, and waits on the carrier bay.
3. In **Carriers**, type the con note, or scan the carrier's label with a
   desk scanner (any USB or Bluetooth scanner that types what it reads and
   presses Enter). Put a wrong one right the same way; each is kept in the
   consignment's history.
4. **Print manifest** once every con note is in, for the carrier's driver to
   sign.
5. When the carrier has been, **Mark collected**: each consignment is marked
   shipped in Simple WMS with the carrier's name and its con note.

## Upgrading

**From a version without the Settings drawer**, `.env` is checked more
strictly when the API and the worker start, so a value the old version let
by can now stop them starting. Check these before upgrading:

- `TMS_WMS_URL` and `TMS_PLATEN_URL`: an `http://` or `https://` address
  (with the scheme), or empty.
- `TMS_WMS_TIMEOUT_SECONDS`: more than 0, at most 60.
- `TMS_WORKER_POLL_SECONDS`: 1 to 300.
- `TMS_WORKER_HTTP_TIMEOUT_SECONDS`: more than 0, at most 120.
- A setting left empty (`TMS_RUN_SHEET_PRINTER=`) is now Simple TMS's own
  default, not empty.

If one is wrong, `docker compose logs api` shows a validation error that
names the setting (`worker_poll_seconds` for `TMS_WORKER_POLL_SECONDS`) and
says what it must be. Put it right in `.env` and `docker compose up -d`.

**From a version before what comes back is handed back to Simple WMS**, a
consignment's calls to Simple WMS go in order, so one that has given up
holds every later call for that consignment. Before upgrading, look for
calls that gave up:

```
docker compose exec db psql -U tms tms -c "select c.wms_ref, o.kind, o.last_error
  from outbound_call o join consignment c on c.id = o.consignment_id
  where o.status = 'failed' order by o.id"
```

Put each right (**Try again** on its run or manifest), or sort it out in
Simple WMS, so it doesn't hold that consignment's next call.

## Run the API outside Docker

```
docker compose up -d db         # Postgres on 127.0.0.1:5434
cd api
uv venv -p 3.12 && uv pip install -e ".[dev]"
export TMS_DATABASE_URL=postgresql+psycopg://tms:<POSTGRES_PASSWORD>@127.0.0.1:5434/tms
.venv/bin/alembic upgrade head
.venv/bin/tms create-user --username you --role admin
.venv/bin/uvicorn tms.api.app:app --reload --port 8022
.venv/bin/python -m tms.worker      # in another terminal: ship calls, run sheets and manifests
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

Leaflet (pinned at 1.9.4) is loaded only by **Where is it**. Leaflet points a
tile it drops at a `data:` image, which the desktop's CSP refuses, so
`src/lib/slippy.ts` overrides three of its internal tile methods
(`_abortLoading`, `_removeTile`, `_tileReady`) to use a `blob:` copy instead,
and the CSP needs no `data:`. Check those three against the new version's
`GridLayer` and `TileLayer` before upgrading Leaflet.

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

The suite signs in with tokens that last 15 minutes and takes a few minutes
to run. On a Mac on battery, maintenance sleep can pause it long enough for
those tokens to expire mid-run, and tests fail with `401`s that have nothing
to do with the code. Keep the Mac awake while it runs:
`caffeinate -i .venv/bin/pytest -q`.

The desktop's tests are Vitest, in jsdom, with the API stubbed:

```
cd apps/desktop && npm test
```

The driver app's tests are Vitest too, with the API stubbed and IndexedDB
stood in for by `fake-indexeddb`: `cd apps/driver && npm test`.

## Known limitations

- **The phone shares only with the app open.** A web app can't use location
  in the background, so a driver who switches to another app, or locks the
  phone, isn't heard from until they come back to Simple TMS; the map says
  how long it has been ("Last heard 12 min ago", in amber after 5 minutes).
  A native app could share in the background; that is not planned.
- **Estimates are plain.** ETAs come from the stop order and the driver's
  average time per stop so far on the run, with no routing service and no
  distances, so a long drive to one far stop throws them out until it is
  done. They are labelled as estimates.
- **The map's region is a picture.** Trucks on the home map are placed along
  drawn roads by how many stops they have done, not by where they are; **Where
  is it** shows true positions.
- **A run left open stops sharing the day after.** A run the driver never
  finished stops taking positions once its day is over by the server's clock
  (UTC: mid-morning the next day in Australia), and the phone stops at its
  own midnight. The map and **Plan runs** show it as "Still open from Tue 13
  Oct" until someone finishes it.

- **The office's Leaves now doesn't ask for a pre-start.** The driver app
  won't start a run until the truck's pre-start is in, but **Leaves now** on
  the desktop (for a driver without a phone, who does a paper check) sends it
  without one. **Plan runs** says "No pre-start yet" on such a run. A
  grounded or overdue truck is refused either way.
- **Desktop users are made on the command line.** Dispatchers, fleet
  managers and admins are added with `tms create-user` (see Getting started);
  there is no screen for them yet.
- **Due dates are on screen only.** The `vehicle.due` event in the design, for
  another system to send email or SMS, comes with the first system that
  subscribes.

- **Carriers are booked by hand.** Simple TMS doesn't talk to any carrier:
  book with the carrier as you always have and type or scan the con note.
  It doesn't follow the freight after it is collected either: a carrier's
  consignment stays "Collected" in Simple TMS, and the carrier's own
  tracking says where it is. A carrier's own API can come later as an
  adapter (`api/tms/services/carrier_adapters.py`) without changing the data.
- **What a carrier brings back isn't recorded.** If a carrier returns
  freight it couldn't deliver, Simple TMS has no step for it: the
  consignment stays collected, and shipped in Simple WMS. (Simple WMS can
  take it back through `POST /v1/deliveries/{ref}/return`; Simple TMS doesn't
  call it for a carrier yet.)

- **What came back leaves again once its run is finished.** A consignment a
  driver couldn't deliver is back in the tray at once and can be planned,
  but it can't leave on another run, or go with a carrier, until the run it
  came back on is finished: only then is it off the truck, and Simple WMS
  to be told. Finish the run from the office if the driver didn't.
- **All of it comes back.** A consignment is delivered whole or not at all,
  so Simple WMS is told every package came back.
- **A call that can never go through holds the rest.** If Simple WMS
  refuses a call for good (it answers `not_that_shipment`, say), **Try
  again** fails again, and every later call for that consignment waits
  behind it until someone sorts the delivery out in Simple WMS.

## Stack

Python 3.12 + FastAPI · PostgreSQL 16 · Vite + React + TypeScript +
Tailwind · Docker Compose · Caddy. The same
choices as Simple WMS, so one person can look after both.

## Licence

MIT © 2026 Q7 Technology
