# Simple TMS — instructions for Claude Code

Read `docs/plans/2026-10-10-simple-tms-design.md` before touching anything. It
is the design, and the decisions in it are settled unless a human changes them
there first.

## What this is
An open source, self-hosted transport management system by Q7 Technology, for
businesses that deliver with their own trucks and also send freight with
outside carriers. Desktop web app (the dispatcher), driver PWA (on the road),
one Python API, one PostgreSQL database. It sits beside Simple WMS and Platen
and works without either. Other systems only ever talk to the API.

## Four rules that never bend
1. **A consignment's history is append-only.** Never UPDATE or DELETE a
   `consignment_event` row. A mistake is corrected by a new row. Cancel, never
   delete, anywhere else too. Pre-start checks are kept the same way.
2. **A delivery is recorded once.** Every confirmation from the driver app,
   and every call to Simple WMS, carries a `message_id`; a repeat returns the
   original reply and does nothing. A Simple WMS event id already seen is
   ignored. A dropped signal must never deliver, or ship, anything twice.
3. **A grounded or overdue truck does not go on a run.** A failed critical
   pre-start item, or rego or service overdue, blocks it until a person clears
   it with a note.
4. **The API is the only door.** The desktop and the driver app are ordinary
   API clients with a session token. If the UI can do it, a partner can too.

## Stack (decided)
- API: Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2, Alembic.
- Database: PostgreSQL 16.
- Queue: a Postgres table drained by a Python worker with backoff (1 min, 5,
  30, 2 h), for outbound events, calls to Simple WMS and print jobs. No Redis
  or RabbitMQ.
- Front ends: Vite + React + TypeScript + Tailwind. One repo, two entry
  points: `apps/desktop` and `apps/driver`. The driver app is a PWA with a
  service worker that caches the app shell, and the offline queue from the
  Simple WMS scanner.
- Hosting: Ubuntu + Docker Compose: `api`, `worker`, `db`, `caddy`, static
  builds for both apps. Secrets in `.env` on the host.

## Repo layout
```
CLAUDE.md            this file
docs/plans/          the design and the implementation plans
docs/api.md          every endpoint: keep it in step with the routes
.github/workflows/   CI: the tests, and a Compose start
design/index.html    gallery of every screen; open a screen's HTML
design/screens/      static HTML of each screen: build to these
api/                 FastAPI app (package tms), migrations, tests
apps/desktop/        the dispatcher's desktop app
apps/driver/         the driver's phone app, a PWA served at /driver/
deploy/Caddyfile     the only door in
docker-compose.yml   api, worker, db, caddy
```
`npm` commands run from the app's folder (`apps/desktop/` or `apps/driver/`):
`npm test`, `npm run typecheck`, `npm run build` (Caddy serves each app's
`dist`).

When building a screen, open its file in `design/screens/` and match layout,
copy and states. Do not edit anything under `design/` unless asked.

## Conventions
- Simple WMS: Simple TMS is an ordinary subscriber to its signed events, and
  tells it what shipped through its API. Nothing in Simple WMS changes for it.
- Printing: Simple TMS never renders a document. It sends `template` + JSON
  `data` to Platen (or any print service) through the same queue.
- Location: the driver app shares its position only during a run, and says
  so on screen. Positions are deleted after the retention period.
- Build order is in the design under "Build order". Finish a step so it is
  usable before starting the next.
- Tests: pytest for the API against a real PostgreSQL, Vitest for the apps.
- Australian English in copy. No "utilise", no "leverage".
- Git: no `Co-Authored-By` lines, no tooling or generated-by trailers, no
  session links. A commit message is the subject line and, if needed, a
  short body about the change. Nothing else.
