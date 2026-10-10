# Simple TMS step 2: own fleet — implementation plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build step 2 of `docs/plans/2026-10-10-simple-tms-design.md`: runs, the drag-to-plan screen, run sheet printing through Platen, and the ship call to Simple WMS when a run departs; with the Platen change that lets Platen report a print job back to whoever sent it.

**Architecture:** Platen first (its own repo and branch): each Platen key may carry a callback address and credential, every run remembers the key that sent it, and the printed/failed report goes to that key's callback, or to `WMS_URL` as today when the key has none. Then the TMS API grows `run` and `stop` (planning serialised by one Postgres advisory lock), an `outbound_call` queue for the Simple WMS ship call and a `print_job` queue for Platen, both drained by a new `worker` service with Simple WMS's backoff, an API key for Platen's callback, and `POST /v1/print-jobs/{job_id}/status` with the same body Simple WMS takes. Last, the dispatcher desktop (`apps/desktop`, Vite + React + TypeScript + Tailwind, as Simple WMS's): sign-in, the map shell from `Main.html`, and the Plan runs drawer from `PlanRuns.html` with real drag and drop, a keyboard way for every drag, and live weight and volume bars; built on the host and served by Caddy.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2, Alembic, psycopg 3, httpx, pytest on a real PostgreSQL 16; Platen's FastAPI/SQLAlchemy/rq with pytest on SQLite and fakeredis; Vite 6, React 19, TypeScript 5, Tailwind 4, react-router 7, Vitest 3 with Testing Library; Docker Compose and Caddy 2.

---

## Context

Paths are relative to each repo: Simple WMS `/Users/leightonlauton/Q7 Technology/Software/q7/simple-wms/`, Platen `/Users/leightonlauton/Q7 Technology/Software/q7/platen/`, Simple TMS `/Users/leightonlauton/Q7 Technology/Software/q7/simple-tms/`. Everything in this plan was checked against the code as it is today: the Platen changes were built in a scratch copy and pass Platen's whole suite (437 tests); the TMS API changes were built in a scratch copy on a throwaway PostgreSQL 16 and pass the whole suite (184 tests), each task's tests passing at that task's stage; the desktop was built in a scratch copy, type-checks, passes Vitest (38 tests), and was looked at in Chrome against a seeded API.

### 1. The ship call to Simple WMS

- Route: `POST /v1/deliveries/{ref}/ship`, `202`, scope **`tasks:write`** (`api/wms/api/routes/outbound.py:369-370`). Body `ShipIn` (`:362-366`) = the shared `Envelope` (`api/wms/api/envelope.py:19-27`: `message_id` required uuid, `external_ref`, `warehouse`, `owner` default `DEFAULT`, `extra="forbid"`) plus `operator`, `device` (`api/wms/api/schemas_tasks.py:102-104`), `carrier`, `tracking_no`, `shipped_by` (each at most 64 characters). So Simple TMS sends `{ message_id, owner, carrier, tracking_no, shipped_by }` and nothing else.
- The delivery is found by `owner` + ref, and the key must allow its warehouse and owner (`outbound.py:194-203`, `api/wms/api/deps.py:54-58`: a key's owner `*` allows every owner).
- `carrier` is free text: `delivery.carrier = carrier or delivery.carrier or delivery.carrier_hint` (`api/wms/services/outbound.py:237`). Simple TMS sends `"Own fleet"` and the run number as `tracking_no`, as the design says ("own fleet, run R-…").
- Idempotency: `envelope.handle` (`envelope.py:65-93`) stores the reply with the work in one transaction; a repeated `message_id` within `WMS_MESSAGE_TTL_HOURS` (default 24, `api/wms/config.py:11`) gets the original reply back and does nothing; two at once, the loser replays the winner. Only a success is stored, so a refusal can be retried.
- Refusals: `409 { "detail", "code" }` (`api/wms/api/errors.py:30-37`, `:74-76`) with `already_shipped`, `cancelled`, `not_packed`, `short_not_allowed` (`outbound.py:214-221`). Simple TMS treats `already_shipped` as done: the reply to a try that worked was lost and the 24 h window has passed, or someone shipped it in Simple WMS. Every other refusal follows the backoff.
- On success Simple WMS emits `delivery.shipped` with `carrier` and `tracking_no` (`outbound.py:244-249`) to every subscriber, as today: nothing in Simple WMS changes.

### 2. The queue and backoff to copy

- `BACKOFF = (60, 300, 1800, 7200)` (`api/wms/worker.py:28`). After a failed try `attempts` is counted, then `_retry_later` (`:91-99`): if `attempts > len(BACKOFF)` the row is `failed`, else `next_attempt_at = now + BACKOFF[attempts - 1]`. So tries 1 to 4 wait 1, 5, 30 and 120 minutes; the 5th try, the one after the 2 h wait, fails it for good.
- Rows are claimed `FOR UPDATE SKIP LOCKED` (`:106-112`), so two workers never take the same row. Print jobs go to Platen as `{ job_id, template, version, printer, copies, reference, data }` with `Authorization: Bearer <WMS_PLATEN_KEY>` (`:143-151`); a 2xx makes the job `accepted`; Platen later says `printed` or `failed`.
- Platen reports on `POST /v1/print-jobs/{job_id}/status` with `{ "status": "accepted" | "printed" | "failed", "message"?, "printer"? }`, `extra="forbid"`, scope `printing:write` (`api/wms/api/routes/printing.py:270-293`). Simple TMS takes the same body on the same path, so Platen needs no format per sender.
- The worker is `python -m wms.worker` in its own Compose service built from the api image (`docker-compose.yml:50-61`), waiting for the api's migrations first (`worker.py:194-208`).

### 3. Simple WMS's desktop, to mirror

- `apps/desktop`: Vite + React 19 + TypeScript + Tailwind 4 (`apps/desktop/package.json`), the dev server proxying `/v1` to the API (`apps/desktop/vite.config.ts`), Vitest in jsdom with `@testing-library/jest-dom` (`apps/desktop/src/test/setup.ts`).
- `src/api/client.ts`: a fetch wrapper with the bearer token, one refresh on a `401` (one refresh at a time), the refresh token in `localStorage`, and `ApiError` carrying the API's `detail`, `code` and field errors. `src/auth/AuthContext.tsx` resumes a session from the stored refresh token on load; `RequireAuth` sends anyone signed out to `/sign-in`.
- Built on the host (`npm run build` → `dist/`) and served by Caddy: Compose mounts `./apps/desktop/dist:/srv/desktop:ro` (`docker-compose.yml:73`) and the Caddyfile serves it with `try_files {path} /index.html` after the `/v1/*` handlers (`deploy/Caddyfile:23-27`).

### 4. Platen today

- `POST /intake/wms` (`app/main.py:1266-1295`) takes `WmsJob` (`:1192-1200`: `job_id`, `template`, `version`, `printer`, `copies`, `reference`, `data`) from any caller `current_user` lets in (`:112-130`): a key becomes a stand-in user `key:<name>` carrying the key's sites. The template is looked up by id (`s.get(db.Template, body.template)`), so the Platen template must have the id `run-sheet`. A page template gets one record per entry in `data.lines`, the rest of `data` beside each (`_wms_records`, `:1224-1231`). A resent `job_id` prints nothing new (`print_run.wms_job_id` unique, `db/models.py:301`).
- `jobs.settled()` (`app/jobs.py:304-313`) queues `report_to_wms(run_id)` on the `platen-reports` queue with `REPORT_RETRY` (`:294`); `report_to_wms` (`:316-357`) posts to `settings.wms_url + /v1/print-jobs/{wms_job_id}/status` with `Bearer settings.wms_key` (`app/settings.py:29-32`, `:43-44`, from `WMS_URL` and `WMS_KEY`), raises on `401`, `408`, `429` and `5xx` so rq retries, and logs and leaves anything else (`:298-301`).
- Keys: `ApiToken` (`db/models.py:96-111`), only the SHA-256 of the token kept (`app/auth.py:162-165`), shown once by `POST /keys` (`app/main.py:1996-2017`), listed by `GET /keys` (`:1987-1993`) and revoked by `DELETE /keys/{key_id}` by id prefix (`:2020-2030`); all `admin` only. A data source's password is masked as `***` (`app/datasources.py:84-93`) and a value saved back as `***` keeps the stored one (`:101-118`).
- Platen's rules that bind the change (`CLAUDE.md`): branch off `main`, never commit to it, no trailers; a key is shown once (14); the log never carries a secret (17); every route names who may call it, and `tests/test_auth.py:105` walks the route table to prove it (11); a migration for every schema change, portable to SQLite (the suite runs on SQLite). The latest migration is `db/migrations/versions/0013_wms_intake.py`.
- Locally Platen's API is at `http://localhost:18000` (`PLATEN_PORT=18000`), its studio at `/studio`, `WMS_URL=http://host.docker.internal:8011`, and it has a page printer `office` (a stand-in on host port 9103) and the WMS's page templates. Its database is at migration `0013`.

### 5. The local Simple WMS

`http://127.0.0.1:8011` (Caddy `https://localhost`). Its `api_client` table has `simple-tms` (id 3) with scopes `["tasks:read"]`, warehouses `*`, owner `*`: enough to read a delivery, not to ship one. Shipping needs **`tasks:write`**. Simple WMS has no endpoint that changes a key's scopes (`POST /v1/api-clients` creates, `/rotate` swaps the secret, `/revoke` deactivates: `docs/api.md:954-960`), so the manual check makes a new key with `tasks:read,tasks:write` (`wms create-api-client`, `api/wms/cli.py:14-24`) and revokes the old one. The subscriber `simple-tms` (`delivery.*` → `http://host.docker.internal:8022/v1/wms/events`) is already registered. Warehouses `BAL-WH01`, `MEL-WH01`; owners `DEFAULT`, `ACME`.

### 6. The TMS as built in step 1

Read before planning: the code went past the step-1 plan. The API is published on `127.0.0.1:8022` (not 8021); Caddy runs on `TMS_HTTP_PORT`/`TMS_HTTPS_PORT` (8180/8543 locally) with a redirect that names the port; `TMS_SECRET_KEY` must be 32+ characters; `FieldError`/`NotFound`/`Unauthorised`/`Forbidden` in `api/tms/api/errors.py`; `require(scope)` and `Principal` in `api/tms/api/deps.py`; `Dec` (decimals out as strings) and `Page` in `api/tms/api/schemas.py`; logins lock rows `FOR UPDATE` so tries count one at a time; tests in `api/tests` use `all_at_once`, `fresh_client`, the `wms` stand-in and `make_user` from `conftest.py`, and the `db` fixture truncates every table after each test. Consignments carry `status` (`packed` only so far) and an append-only `consignment_event` history (trigger `block_change`).

### Decisions this plan makes that were not settled

- **Platen keeps `/intake/wms` and adds `/intake/jobs`** as the same handler (two route decorators on one function). Simple TMS is configured with `/intake/jobs`, which reads right for any sender; every Simple WMS install keeps working untouched. The function, the `wms_job_id` column and `report_to_wms` keep their names: renaming them would churn tests, a column and job names already sitting in rq queues for nothing a user sees.
- **Per-key callback details.** Columns `api_token.callback_url` and `api_token.callback_key` (text, nullable) and `print_run.sent_by_key` (the key's id, its SHA-256, which is not a secret). A callback address must be `http` or `https` with a host and no `user:password@` in it (the address is shown on the keys screen; the credential goes in `callback_key`), at most 500 characters, stored without a trailing slash. `callback_key` is never returned (`"***"` when set, `null` when not) and never logged; saved back as `***` it keeps the stored one; a `callback_key` without an address is refused. Set at creation (`POST /keys`) or later (`PATCH /keys/{key_id}`, `admin`); no address clears both. A run whose key has since been **revoked** reports to nobody and logs a warning, rather than falling back to `WMS_URL`: that would send a Simple TMS job's report to Simple WMS. Agent keys take no callback. The People → Keys screen gets the two fields at creation and a "Reports to" column.
- **The TMS's credential for Platen's callback is a TMS API key.** Step 1 had none ("a partner key comes when something needs it"); this needs it. Table `api_key` (name, SHA-256 of the key, scopes, active), keys `tms_k.…` shown once by `tms create-api-key --name platen --scopes printing:write`, revoked (never deleted) by `tms revoke-api-key`. `get_principal` accepts them as `kind: "key"`. A key may only hold `printing:write`, `runs:read`, `runs:write`, `consignments:read`, `fleet:read`. No HTTP endpoints for keys yet.
- **Scopes:** `runs:read` and `runs:write` for dispatchers and managers (admins have `*`); drivers get nothing new until step 3. The callback needs `printing:write`, which only `admin` and a key have.
- **Planning is serialised by one transaction-scoped advisory lock** (`pg_advisory_xact_lock`) taken before anything is read, in every change: create, change, cancel, assign, unassign, reorder, depart. Planning edits take milliseconds and a depot has a handful of dispatchers, so one lock costs nothing; with one lock there is no lock order and no deadlock (row locks on two runs and a consignment would need a careful order, since a move touches two runs). Partial unique indexes back it: one live stop per consignment, one live run per truck per day. The concurrency tests fail without the lock (checked).
- **Reorder takes the whole order** (`consignment_ids`) and is refused with `409 stale` if it is not exactly the run's live stops, so a dispatcher dragging an out-of-date board never scrambles someone else's change; the screen reloads and says so. **Assign** takes a 1-based `position` (none means last) and is idempotent: already there, nothing happens; on another planned run, it moves (an `unassigned` then an `assigned` history row). **Unassign** of something not on the run is a no-op.
- **Stops are never deleted.** Taken off a run, a stop keeps its row with `removed_at` set (rule: cancel, never delete). A run is cancelled, not deleted, and only when empty.
- **One run per truck per day** (a cancelled run frees the truck). The board shows a lane per run; a second trip in a day can come later if wanted. Drivers are not limited.
- **Run numbers are `R-` and the id, four digits at least** (`R-0042`), not stored.
- **Capacity:** weight is the sum of package weights; volume is the sum of length × width × height ÷ 1,000,000 for packages with all three; a package without them adds nothing and is counted (`unknown_weight`, `unknown_volume`) so the screen can say the bars may read low. Nearly full is 90 % or more of either limit; over is more than either. Assigning past a limit is allowed (the design shows a red, over lane); **printing and departing an over run are refused** (`409 over_capacity`) with the design's message: "280 kg too heavy. Move a stop to another run before printing the run sheet." What is over rounds up (kg to whole, m³ to 0.1), what is left rounds down. The same rules run in the desktop with BigInt millionths, so a bar moves the moment a card drops; the API decides.
- **Departing** is a button on each planned lane ("Leaves now", with a confirm), since in step 2 nobody else can say a run left; step 3's driver app takes over. Departing refuses an empty run, a deactivated truck and an over run, marks each consignment `departed` with a history row, and queues one ship call per consignment with its own `message_id`, all in one transaction. Departing again does nothing. `POST /v1/runs/{id}/retry-ship` puts calls that gave up back to pending with `attempts` 0 and the **same** `message_id`.
- **Printing** is `POST /v1/runs/{id}/print`: a new `print_job` each time (a reprint is a press), template `run-sheet` version `v1`, printer `TMS_RUN_SHEET_PRINTER` (default `dispatch`; locally `office`), the data snapshotted when queued. Refused while `TMS_PLATEN_URL` is empty (`409 printing_not_set_up`, so the dispatcher hears it at once rather than the job waiting silently), for an empty, cancelled or over run. The worker sends it to `TMS_PLATEN_URL` (the full intake address) with `Bearer TMS_PLATEN_KEY`; Platen's own refusal (`template: Platen has no template 'run-sheet'…`) is kept as `last_error` in Platen's words and shown on the lane. A late `accepted` does not undo `printed` or `failed`.
- **Two queue tables, not one:** `outbound_call` (the design's name) holds calls to Simple WMS (`pending`, `done`, `failed`); `print_job` holds Platen jobs (`pending`, `accepted`, `printed`, `failed`), because a print job has its own lifecycle and a callback, as in Simple WMS. The worker claims one row per transaction (`FOR UPDATE SKIP LOCKED`, `LIMIT 1`), so a slow answer holds up only that row.
- **The desktop:** routes `/sign-in`, `/` (the map) and drawers `/plan`, `/consignments`, `/carriers`, `/fleet` (the last three say plainly which build step brings them). The design's CSS classes are carried over as they are (in `@layer components`), so the screens match `Main.html` and `PlanRuns.html` closely; Tailwind is for small layout. Drag and drop is the browser's own (no library). The keyboard way: every tray card and every stop is focusable and shows, on focus or hover, the controls a drag stands for (a "Put on a run…" menu; "Move up", "Move down" and a "Move to…" menu with "Back to the tray"). The board reloads after every change and every 20 s, so another dispatcher's work turns up. The map uses a copy of `design/screens/region.svg` with its sample pins and truck tags taken out (they name runs and a grounded truck that do not exist; live ones come with step 5). The map's goal, selected-truck panel, feed and game toggles are left out until there is something real to show. The Plan runs eyebrow reads "OWN FLEET" (there is no depot name setting). The keyboard controls and "Leaves now" are additions to the drawn screen; everything else follows it.
- **The day picker** offers today and the next six days, defaulting to tomorrow as drawn.
- **A truck that cannot take a run** is shown in the design's dashed column. In step 2 that is a deactivated vehicle ("Deactivated · Can't take a run until a fleet manager reactivates it"); the component takes the reason and the sentence, so step 4 passes "Grounded" and the failed item.

### Ports and addresses, locally

Simple TMS API `127.0.0.1:8022`, Caddy `8180`/`8543` (`https://localhost:8543`), database `127.0.0.1:5434`. Simple WMS API `127.0.0.1:8011`. Platen `http://localhost:18000`. Containers reach the host as `host.docker.internal`: the TMS worker calls `http://host.docker.internal:8011` and `http://host.docker.internal:18000/intake/jobs`; Platen's worker calls back `http://host.docker.internal:8022`.

---

## Before you start

Platen (Part A) comes first: Simple TMS's printing reports back through it.

```
cd "/Users/leightonlauton/Q7 Technology/Software/q7/platen"
git switch main && git pull --ff-only
git switch -c per-key-callback
.venv/bin/pytest -q                          # expect: all pass before touching anything

cd "/Users/leightonlauton/Q7 Technology/Software/q7/simple-tms"
git switch main && git pull --ff-only
git switch -c step-2-own-fleet
docker compose up -d db                      # the tests' PostgreSQL on 127.0.0.1:5434
cd api && .venv/bin/pytest -q                # expect: 127 passed
```

The plan has four parts: **A**, Platen (Tasks 1–5); **B**, the Simple TMS API and worker (Tasks 6–18); **C**, the desktop app (Tasks 19–24); **D**, the checks, ending with a manual run from a packed delivery to a shipped one (Tasks 25–26). Part C needs Part B's API; Part B's printing needs Part A only for the manual check.

Platen's `CLAUDE.md`: never commit to `main`, subject in the imperative under 72 characters, no trailers. Simple TMS's `CLAUDE.md`: a subject line and, if needed, a short body; no trailers, no tool attribution. All TMS `pytest` commands run from `api/`; all `npm` commands from `apps/desktop/`.

## Part A — Platen: report a print job to whoever sent it

All of Part A is in the Platen repo, on the branch `per-key-callback`. Commands run from `/Users/leightonlauton/Q7 Technology/Software/q7/platen`.

### Task 1: A key carries a callback (Platen)

**Files:**
- Create: `db/migrations/versions/0014_key_callbacks.py`
- Modify: `db/models.py` (`ApiToken`, `PrintRun`)
- Modify: `app/main.py` (imports; `KeyBody` and the `/keys` routes, around `:1855-2030`)
- Create: `tests/test_key_callbacks.py`

**Step 1: Write the failing tests**

`tests/test_key_callbacks.py`:
```python
"""A print job is reported back to whoever sent it: the callback set on the
key that sent the job, or WMS_URL for a key without one."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from db import session as dbsession
from db.models import ApiToken

TMS = {"callback_url": "http://tms.test:8022/", "callback_key": "tms_k.callback-secret"}


def _make(admin: TestClient, **body) -> dict:
    r = admin.post("/keys", json={"name": "simple-tms", "role": "operator", **body})
    assert r.status_code == 201, r.text
    return r.json()


def _stored(name: str) -> ApiToken:
    with dbsession.SessionLocal() as s:
        return s.scalars(select(ApiToken).where(ApiToken.name == name)).one()


def test_a_key_carries_a_callback_and_never_shows_its_credential(client):
    made = _make(client, **TMS)
    assert made["callback_url"] == "http://tms.test:8022"
    assert made["callback_key"] == "***"
    listed = client.get("/keys")
    assert listed.json()[0]["callback_url"] == "http://tms.test:8022"
    assert listed.json()[0]["callback_key"] == "***"
    assert "callback-secret" not in listed.text and "callback-secret" not in str(made)
    assert _stored("simple-tms").callback_key == "tms_k.callback-secret"


def test_a_key_without_a_callback_says_so(client):
    made = _make(client)
    assert made["callback_url"] is None and made["callback_key"] is None
    assert client.get("/keys").json()[0]["callback_url"] is None


def test_a_callback_credential_needs_an_address(client):
    r = client.post("/keys", json={"name": "x", "callback_key": "secret"})
    assert r.status_code == 422 and "callback_url" in r.json()["detail"]
    assert client.get("/keys").json() == []


def test_a_callback_address_is_http_and_carries_no_credential(client):
    for bad in ("ftp://tms.test", "tms.test", "http://", "http://u:secret@tms.test"):
        r = client.post("/keys", json={"name": "x", "callback_url": bad})
        assert r.status_code == 422, bad
        assert "callback_url" in r.json()["detail"]
        assert "secret" not in r.json()["detail"]
    assert client.get("/keys").json() == []


def test_an_existing_key_is_given_a_callback(client):
    made = _make(client)
    r = client.patch(f"/keys/{made['id']}", json=TMS)
    assert r.status_code == 200, r.text
    assert r.json()["callback_url"] == "http://tms.test:8022"
    assert r.json()["callback_key"] == "***"
    assert _stored("simple-tms").callback_key == "tms_k.callback-secret"


def test_a_credential_saved_back_masked_keeps_the_stored_one(client):
    made = _make(client, **TMS)
    r = client.patch(f"/keys/{made['id']}", json={"callback_url": "https://tms.example.com",
                                                  "callback_key": "***"})
    assert r.status_code == 200
    row = _stored("simple-tms")
    assert row.callback_url == "https://tms.example.com"
    assert row.callback_key == "tms_k.callback-secret"


def test_no_address_clears_the_callback(client):
    made = _make(client, **TMS)
    assert client.patch(f"/keys/{made['id']}", json={"callback_url": None}).status_code == 200
    row = _stored("simple-tms")
    assert row.callback_url is None and row.callback_key is None


def test_only_an_administrator_sets_a_callback(client, operator):
    made = _make(client)
    assert operator.patch(f"/keys/{made['id']}", json=TMS).status_code == 403
    assert client.patch("/keys/nosuchkey", json=TMS).status_code == 404
```

**Step 2: Run them to see them fail**

Run: `.venv/bin/pytest tests/test_key_callbacks.py -q`
Expected: FAIL — `KeyError: 'callback_url'` on the first, `405` on the `PATCH` tests, and `assert 201 == 422` where a bad address is still taken.

**Step 3: The columns and the migration**

In `db/models.py`, at the end of `class ApiToken` (after `last_used_at`):
```python
    # where a job sent with this key is reported back to, and the credential
    # to send there; none, and the report goes to WMS_URL as it always has.
    # The credential is a secret: never logged, never returned by a read.
    callback_url: Mapped[str | None] = mapped_column(Text)
    callback_key: Mapped[str | None] = mapped_column(Text)
```
In `class PrintRun`, straight after `wms_job_id`:
```python
    # the key that sent that job (its hash, as api_token.id), so the report
    # goes back to whoever sent it
    sent_by_key: Mapped[str | None] = mapped_column(String(64))
```
(`Text` and `String` are already imported there.)

`db/migrations/versions/0014_key_callbacks.py`:
```python
"""Report a print job back to whoever sent it: a callback on each key

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-10
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("api_token", sa.Column("callback_url", sa.Text))
    op.add_column("api_token", sa.Column("callback_key", sa.Text))
    op.add_column("print_run", sa.Column("sent_by_key", sa.String(64)))


def downgrade() -> None:
    with op.batch_alter_table("print_run") as batch:
        batch.drop_column("sent_by_key")
    with op.batch_alter_table("api_token") as batch:
        batch.drop_column("callback_key")
        batch.drop_column("callback_url")
```

**Step 4: The routes**

In `app/main.py`, add to the imports after `from typing import Any, Literal`:
```python
from urllib.parse import urlsplit
```

Replace `class KeyBody` with:
```python
class KeyBody(BaseModel):
    name: str
    role: Literal["admin", "operator"] = "operator"
    sites: list[str] = []          # an operator key's sites; none means every site
    # where a print job sent with this key is reported back to, and the
    # credential to send there; none, and it goes to WMS_URL
    callback_url: str | None = None
    callback_key: str | None = None


class CallbackBody(BaseModel):
    callback_url: str | None = None
    callback_key: str | None = None     # "***" keeps the one stored


def _callback_url(value: str | None) -> str | None:
    """http or https with a host, and no credential in it: the address is
    shown on the keys screen, the credential never is."""
    value = (value or "").strip()
    if not value:
        return None
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.hostname or len(value) > 500:
        raise HTTPException(422, "callback_url: give the address of the system that sends "
                                 "the jobs, starting http:// or https://")
    if parts.username or parts.password:
        raise HTTPException(422, "callback_url: put the credential in callback_key, not in "
                                 "the address; the address is shown on the keys screen")
    return value.rstrip("/")


def _key_json(k: db.ApiToken) -> dict[str, Any]:
    return {"id": k.id[:12], "name": k.name, "role": k.role, "agent_id": k.agent_id,
            "sites": k.sites or [], "created_by": k.created_by, "created_at": k.created_at,
            "last_used_at": k.last_used_at, "callback_url": k.callback_url,
            "callback_key": datasources.MASK if k.callback_key else None}
```

Replace the body of `list_keys` with:
```python
    return [_key_json(k)
            for k in s.scalars(select(db.ApiToken).order_by(db.ApiToken.created_at.desc()))]
```

In `make_key`, replace from `token = auth.create_token(` to the end of the function with (the checks come before the key is made, because `create_token` commits):
```python
    callback_url = _callback_url(body.callback_url)
    callback_key = (body.callback_key or "").strip() or None
    if callback_key and not callback_url:
        raise HTTPException(422, "callback_url: a callback key needs the address to send it to")
    token = auth.create_token(s, name=body.name, role=body.role, created_by=user.username)
    row = s.get(db.ApiToken, auth.token_id(token))
    row.sites, row.callback_url, row.callback_key = chosen, callback_url, callback_key
    audit(s, "create", "key", body.name, actor=user, role=body.role, sites=chosen,
          callback_url=callback_url)
    s.commit()
    return {**_key_json(row), "token": token}


@app.patch("/keys/{key_id}")
def set_key_callback(key_id: str, body: CallbackBody, s: Session = Depends(get_session),
                     user: AppUser = Depends(admin)) -> dict[str, Any]:
    """Where jobs sent with this key are reported back to. A credential sent
    back as *** keeps the one stored; no address clears both."""
    rows = [k for k in s.scalars(select(db.ApiToken))
            if key_id and k.id.startswith(key_id) and k.role != "agent"]
    if len(rows) != 1:
        raise HTTPException(404, f"no key {key_id!r}")
    row = rows[0]
    url = _callback_url(body.callback_url)
    submitted = (body.callback_key or "").strip()
    if url is None:
        row.callback_url = row.callback_key = None
    else:
        row.callback_url = url
        if submitted != datasources.MASK:
            row.callback_key = submitted or None
    audit(s, "callback", "key", row.name, actor=user, callback_url=row.callback_url)
    s.commit()
    return _key_json(row)
```
The audit log records the address, never the credential. `set_key_callback` carries `Depends(admin)`, so `tests/test_auth.py::test_every_route_says_who_may_call_it` stays green.

**Step 5: Run the tests**

Run: `.venv/bin/pytest tests/test_key_callbacks.py tests/test_users.py tests/test_sites.py tests/test_auth.py -q`
Expected: all pass.

**Step 6: The migration goes up and down**

Run:
```
D=$(mktemp -d)
DATABASE_URL=sqlite:///$D/m.sqlite .venv/bin/alembic upgrade head
DATABASE_URL=sqlite:///$D/m.sqlite .venv/bin/alembic downgrade 0013
DATABASE_URL=sqlite:///$D/m.sqlite .venv/bin/alembic upgrade head
```
Expected: the last line of each is `Running upgrade 0013 -> 0014, Report a print job back to whoever sent it: a callback on each key` (or `downgrade 0014 -> 0013`), with no error.

**Step 7: Commit**

```bash
git add db/models.py db/migrations/versions/0014_key_callbacks.py app/main.py tests/test_key_callbacks.py
git commit -m "Let a key carry the address its print jobs report back to"
```

### Task 2: The intake remembers the key, and takes jobs at /intake/jobs too (Platen)

**Files:**
- Modify: `app/main.py` (`current_user` `:112-130`, `_queue_run` `:1023-1066`, `wms_intake` `:1266-1295`)
- Modify: `tests/test_key_callbacks.py`

**Step 1: Write the failing tests**

In `tests/test_key_callbacks.py`, replace the imports (from `from fastapi.testclient` to `from db.models import ApiToken`) with:
```python
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from db import session as dbsession
from db.models import ApiToken, PrintRun
from tests.test_wms_intake import _job
```
add after `_make`:
```python
def _as(made: dict) -> TestClient:
    return TestClient(app, headers={"authorization": f"Bearer {made['token']}"})
```
and append:
```python
def test_the_run_remembers_which_key_sent_it(seeded):
    made = _make(seeded, **TMS)
    run_id = _as(made).post("/intake/jobs", json=_job()).json()["id"]
    with dbsession.SessionLocal() as s:
        assert s.get(PrintRun, run_id).sent_by_key == _stored("simple-tms").id


def test_the_neutral_intake_and_the_wms_one_are_the_same_door(seeded, queue):
    c, job = _as(_make(seeded)), _job()
    first = c.post("/intake/jobs", json=job)
    assert first.status_code == 202 and first.json()["duplicate"] is False
    again = c.post("/intake/wms", json=job)
    assert again.json()["id"] == first.json()["id"] and again.json()["duplicate"] is True
    assert queue.count == 1
```

**Step 2: Run them to see them fail**

Run: `.venv/bin/pytest tests/test_key_callbacks.py -q -k "remembers or same_door"`
Expected: FAIL — `404` from `/intake/jobs`, and `sent_by_key` is `None`.

**Step 3: Carry the key's id from the bearer to the run**

In `current_user`, after `stand_in.key_sites = …`:
```python
        stand_in.key_id = token.id                       # read by the intake
```

`_queue_run`'s signature becomes:
```python
def _queue_run(s: Session, user: AppUser, printer: db.Printer, spec: RunSpec, *,
               records: list[dict[str, Any]] | None = None,
               wms_job_id: str | None = None, sent_by_key: str | None = None,
               **detail: Any) -> dict[str, Any]:
```
and in its `db.PrintRun(...)`, after `wms_job_id=wms_job_id,` add:
```python
        sent_by_key=sent_by_key,
```

Replace the decorator and docstring of `wms_intake` with:
```python
@app.post("/intake/jobs", status_code=202)
@app.post("/intake/wms", status_code=202)
def wms_intake(body: WmsJob, s: Session = Depends(get_session),
               user: AppUser = Depends(current_user)) -> dict[str, Any]:
    """A print job from Simple WMS, Simple TMS or anything else that sends the
    same shape. /intake/wms is the address Simple WMS installs already use;
    /intake/jobs is the same door under a name that fits every sender. The
    template is the one whose id is the job's template name; its saved query
    isn't run, the data comes with the job. How it went is reported to the
    callback of the key that sent it (see jobs.report_to_wms)."""
```
and in its `_queue_run(...)` call replace `wms_job_id=job_id, wms_version=body.version,` with:
```python
                         wms_job_id=job_id, sent_by_key=getattr(user, "key_id", None),
                         wms_version=body.version,
```
A person signed in with a cookie has no `key_id`, so their runs report to `WMS_URL` as before.

**Step 4: Run the tests**

Run: `.venv/bin/pytest tests/test_key_callbacks.py tests/test_wms_intake.py tests/test_auth.py -q`
Expected: all pass (the route-table test sees `/intake/jobs` guarded by `current_user`).

**Step 5: Commit**

```bash
git add app/main.py tests/test_key_callbacks.py
git commit -m "Remember which key sent a print job, and take jobs at /intake/jobs too"
```

### Task 3: The report goes to the key's callback (Platen)

**Files:**
- Modify: `app/jobs.py` (imports, `report_to_wms` `:316-357`)
- Modify: `tests/test_key_callbacks.py`

**Step 1: Write the failing tests**

In `tests/test_key_callbacks.py`, replace the imports with:
```python
import logging

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import jobs
from app.main import app
from db import session as dbsession
from db.models import ApiToken, PrintRun
from tests.test_wms_intake import _job, _settle, wms  # noqa: F401  (fixture)
```
(the `wms` fixture from `test_wms_intake.py` stands in for `httpx.post` and sets `WMS_URL=http://wms.test/`, `WMS_KEY=wms_live_secret`) and append:
```python
def test_a_job_is_reported_to_the_key_that_sent_it(seeded, wms, caplog):  # noqa: F811
    job = _job()
    run_id = _as(_make(seeded, **TMS)).post("/intake/jobs", json=job).json()["id"]
    _settle(run_id, "done")
    with caplog.at_level(logging.DEBUG):
        jobs.report_to_wms(run_id)
    call = wms.calls[-1]
    assert call["url"] == f"http://tms.test:8022/v1/print-jobs/{job['job_id']}/status"
    assert call["json"] == {"status": "printed"}
    assert call["headers"]["Authorization"] == "Bearer tms_k.callback-secret"
    assert "callback-secret" not in caplog.text


def test_a_key_without_a_callback_still_reports_to_wms_url(seeded, wms):  # noqa: F811
    job = _job()
    run_id = _as(_make(seeded)).post("/intake/wms", json=job).json()["id"]
    _settle(run_id, "done")
    jobs.report_to_wms(run_id)
    assert wms.calls[-1]["url"] == f"http://wms.test/v1/print-jobs/{job['job_id']}/status"
    assert wms.calls[-1]["headers"]["Authorization"] == "Bearer wms_live_secret"


def test_a_callback_without_a_credential_is_sent_bare_and_says_so(seeded, wms, caplog):  # noqa: F811
    made = _make(seeded, callback_url="http://tms.test:8022")
    run_id = _as(made).post("/intake/jobs", json=_job()).json()["id"]
    _settle(run_id, "done")
    with caplog.at_level(logging.WARNING):
        jobs.report_to_wms(run_id)
    assert "Authorization" not in wms.calls[-1]["headers"]
    assert "simple-tms" in caplog.text and "callback key" in caplog.text


def test_a_job_from_a_revoked_key_is_reported_to_nobody(seeded, wms, caplog):  # noqa: F811
    made = _make(seeded, **TMS)
    run_id = _as(made).post("/intake/jobs", json=_job()).json()["id"]
    assert seeded.delete(f"/keys/{made['id']}").status_code == 204
    _settle(run_id, "done")
    with caplog.at_level(logging.WARNING):
        jobs.report_to_wms(run_id)
    assert wms.calls == []
    assert "revoked" in caplog.text and run_id in caplog.text
```

**Step 2: Run them to see them fail**

Run: `.venv/bin/pytest tests/test_key_callbacks.py -q -k "reported or credential_is_sent or revoked or still_reports"`
Expected: FAIL — the report still goes to `http://wms.test/…` with the WMS key.

**Step 3: Pick the target**

In `app/jobs.py` the import line becomes:
```python
from db.models import ApiToken, PrintRun, RunLabel, Setting
from db.models import Printer as PrinterRow
```
(ruff's isort puts `ApiToken, …` first.) Replace the whole of `report_to_wms` with:
```python
def _report_target(s: Session, run: PrintRun) -> tuple[str, str, str] | None:
    """Where the report on this run goes: the address, the credential, and
    who that is, for the log. The callback on the key that sent the job
    decides; a key without one, or a run no key sent, goes to WMS_URL as
    every report did before keys had callbacks. Never the credential in a
    log line, never the address either: naming the key is enough."""
    from .settings import settings

    if run.sent_by_key:
        key = s.get(ApiToken, run.sent_by_key)
        if key is None:
            log.warning("the key that sent this job has been revoked, so nobody is told: %s",
                        where(run=run.id, job=run.wms_job_id, status=run.status))
            return None
        if key.callback_url:
            if not key.callback_key:
                log.warning("key %s has a callback address but no callback key, so the "
                            "sender will likely refuse the report: %s",
                            key.name, where(run=run.id, job=run.wms_job_id))
            return key.callback_url, key.callback_key or "", f"key {key.name}"
    if not settings.wms_url:
        log.warning("no WMS_URL, so the WMS was not told: %s",
                    where(run=run.id, wms_job=run.wms_job_id, status=run.status))
        return None
    if not settings.wms_key:
        log.warning("no WMS_KEY, so the WMS will likely refuse the report: %s",
                    where(run=run.id, wms_job=run.wms_job_id))
    return settings.wms_url, settings.wms_key, "the WMS"


def report_to_wms(run_id: str) -> None:
    """Runs in the worker. Tells whoever sent the job how it went: Simple
    WMS, Simple TMS, anything that sent it with a key. Raises on an answer
    that may change, or no answer at all, so rq tries again; any other
    refusal is logged and left. (The name is older than the other senders;
    queued reports still call it by it.)

    Reads the run as it is now, not as it was when the report was queued: a
    run put back on the queue since says nothing until it settles again, and
    that settle queues a report of its own."""
    dbsession.engine()
    with dbsession.SessionLocal() as s:
        run = s.get(PrintRun, run_id)
        if run is None or not run.wms_job_id or run.status not in SETTLED:
            return
        target = _report_target(s, run)
        if target is None:
            return
        base, key, who = target
        if run.status == "done":
            body: dict[str, str] = {"status": "printed"}
        elif run.status == "cancelled":
            body = {"status": "failed", "message": "cancelled in Platen"}
        else:
            body = {"status": "failed",
                    "message": (run.error or "the run failed in Platen")[:500]}
        url = f"{base.rstrip('/')}/v1/print-jobs/{run.wms_job_id}/status"
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        # a transport error goes up as it is: httpx doesn't put headers in its
        # message, so the key stays out of rq's log
        r = httpx.post(url, json=body, headers=headers, timeout=REPORT_TIMEOUT)
        if not 200 <= r.status_code < 300:
            again = _worth_another_go(r.status_code)
            log.warning("%s refused the report%s: %s", who,
                        "" if again else ", and won't be asked again",
                        where(run=run_id, wms_job=run.wms_job_id, http=r.status_code))
            if again:
                raise RuntimeError(f"{who} answered {r.status_code} to the report on {run_id}")
            return
        log.info("%s told: %s", who,
                 where(run=run_id, wms_job=run.wms_job_id, status=body["status"]))
```
Every log line names the key or "the WMS" and the run, never an address or a credential (invariant 17). The existing tests in `tests/test_wms_intake.py` still hold: a run with no key, or a key with no callback, reports to `WMS_URL` with the same messages (`no WMS_URL…`, `no WMS_KEY…`) and the same retry rules.

**Step 4: Run the tests**

Run: `.venv/bin/pytest tests/test_key_callbacks.py tests/test_wms_intake.py tests/test_logging.py -q`
Expected: all pass.

**Step 5: Commit**

```bash
git add app/jobs.py tests/test_key_callbacks.py
git commit -m "Report a print job to the callback of the key that sent it"
```

### Task 4: The keys screen asks for a callback (Platen)

**Files:**
- Modify: `web/studio/people.html` (the Keys card `:79-110`, `loadKeys` and `addKey` `:238-277`)
- Modify: `tests/test_key_callbacks.py`

**Step 1: Write the failing test**

Append to `tests/test_key_callbacks.py`:
```python
def test_the_keys_screen_asks_for_a_callback(client):
    r = client.get("/studio/people")
    assert 'id="k-callback-url"' in r.text and 'id="k-callback-key"' in r.text
```

Run: `.venv/bin/pytest tests/test_key_callbacks.py -q -k screen`
Expected: FAIL — `assert 'id="k-callback-url"' in …`.

**Step 2: The fields, and a "Reports to" column**

In `web/studio/people.html`, the keys table head becomes:
```html
              <thead><tr><th>Name</th><th>Role</th><th>Sites</th><th>Reports to</th><th>Made by</th><th>Last used</th><th></th></tr></thead>
```
After the `k-sites-field` `div` (the one ending "…as keys always have.</p>\n          </div>"), before `<div id="k-token"></div>`, add:
```html
          <div class="row">
            <div class="field"><label for="k-callback-url">Report printed jobs to</label><input id="k-callback-url" class="mono" type="url" placeholder="https://tms.example.com" autocapitalize="none" spellcheck="false"></div>
            <div class="field"><label for="k-callback-key">With this key of theirs</label><input id="k-callback-key" class="mono" type="password" autocomplete="off"></div>
          </div>
          <p class="mut" style="font-size: 12px; margin: 0;">For a system that sends print jobs, such as Simple TMS: Platen tells it each job printed or failed. Leave it empty and jobs are reported to WMS_URL, as before. Their key is never shown again.</p>
```
In `loadKeys`, after the Sites `td`:
```js
        el('td', { class: 'mut mono', text: k.role === 'agent' ? '—' : k.callback_url || 'WMS_URL' }),
```
In `addKey`, replace the `POST /keys` line and the field reset after it with:
```js
      const made = await api('POST', '/keys', {
        name, role: $('k-role').value, sites,
        callback_url: $('k-callback-url').value.trim() || null,
        callback_key: $('k-callback-key').value.trim() || null });
      clearError($('k-msg'));
      $('k-name').value = '';
      $('k-callback-url').value = '';
      $('k-callback-key').value = '';
```
The credential field is a password field and is cleared once sent; the list shows only the address.

**Step 3: Run the tests**

Run: `.venv/bin/pytest tests/test_key_callbacks.py tests/test_users.py -q`
Expected: all pass. Open `http://localhost:18000/studio/people` after Task 5's restart to see it.

**Step 4: Commit**

```bash
git add web/studio/people.html tests/test_key_callbacks.py
git commit -m "Ask for a key's callback on the keys screen"
```

### Task 5: Platen's docs, the whole suite, and the branch (Platen)

**Files:**
- Modify: `README.md` (section "From Simple WMS", `:93-115`)
- Modify: `.env.example` (`:27-30`)

**Step 1: README**

Replace the section `## From Simple WMS` (its heading through the paragraph ending "…and a printer at another site a 403.") with:
````markdown
## From Simple WMS and Simple TMS

Simple WMS and Simple TMS never draw a label or a page of their own: they
send a template name and a row of data, and Platen prints it.

1. Make an operator key under **People → Keys**, one per system. Tick the
   site if there is one, and the key keeps to it.
2. Point the system at Platen's job intake with that key:
   - Simple WMS: set the warehouse's `platen_url` setting to
     `https://<platen>/intake/wms` (or `/intake/jobs`: the same door), and
     `WMS_PLATEN_KEY` in the WMS `.env` to the key.
   - Simple TMS: set `TMS_PLATEN_URL=https://<platen>/intake/jobs` and
     `TMS_PLATEN_KEY` to the key, and `TMS_RUN_SHEET_PRINTER` to the printer.
3. Give each Platen template the id of the template the system names
   (`carton-label`, `pick-list`, `run-sheet`, …) and publish it. Bind its
   fields to the data the system sends; a nested field binds as
   `{{ ship_to.name }}`. The template's saved query isn't run: the job's data
   is the row.
4. The printer the system names must match a Platen printer's id or its name
   (case doesn't matter).
5. Tell Platen where to report how each job went. Each key can carry its own
   **callback**: the system's address and a key of the system's own, which
   Platen sends as `Authorization: Bearer <key>` to
   `POST <address>/v1/print-jobs/{job_id}/status` with
   `{"status": "printed"}` or `{"status": "failed", "message": "…"}`. Set it
   when making the key (the two fields under the sites) or later with
   `PATCH /keys/{id}` `{"callback_url": "https://tms.example.com",
   "callback_key": "tms_k.…"}`. The address is shown on the keys screen; the
   key is never shown again, nor logged (sending `***` back keeps it; no
   address clears both). For Simple TMS, the callback key is one made with
   `tms create-api-key --name platen --scopes printing:write`.
   A key with no callback reports to `WMS_URL` with `WMS_KEY` (a WMS API key
   with the `printing:write` scope), as before: an existing Simple WMS setup
   needs no change. A job from a key revoked since is reported to nobody.

A page template gets one row per entry in `lines`, with the job's header
fields beside each. A job sent twice prints once. An unknown template or
printer is a 422 naming it, and a printer at another site a 403.
````

**Step 2: `.env.example`**

Replace its Simple WMS lines (`# Simple WMS: where to say…` to `WMS_KEY=`) with:
```
# Simple WMS: where to say a print job it sent came out (or didn't), and a
# WMS API key with the printing:write scope. Used for jobs sent with a key
# that has no callback of its own (People > Keys); leave empty without a WMS.
WMS_URL=
WMS_KEY=
```

**Step 3: The whole suite and the linter**

Run: `.venv/bin/pytest -q && .venv/bin/ruff check app db tests`
Expected: `437 passed, 17 skipped` (the 422 before, plus Part A's 15) and `All checks passed!`.

**Step 4: Commit, and the branch**

```bash
git add README.md .env.example
git commit -m "Document per-key callbacks for Simple WMS and Simple TMS"
git push -u origin per-key-callback
```
Then use **superpowers:finishing-a-development-branch** (a pull request into `main`; never commit to `main` directly). The manual check (Task 26) runs this branch locally; once it is merged, rebuild from `main`.

## Part B — Simple TMS: runs, the ship call, run sheets, the worker

All of Part B is in the Simple TMS repo on the branch `step-2-own-fleet`. `pytest` runs from `api/` with the compose `db` up.

### Task 6: Settings, a 409 for "not as things stand", and the runs scopes

**Files:**
- Modify: `api/tms/config.py`
- Modify: `api/tms/api/errors.py`
- Modify: `api/tms/services/sessions.py` (`ROLE_SCOPES`, `:28-32`)
- Modify: `api/tests/test_config.py`, `api/tests/test_auth.py:14`

**Step 1: Write the failing tests**

Append to `api/tests/test_config.py`:
```python
def test_printing_and_the_worker_start_switched_off_with_safe_waits():
    s = Settings(secret_key="3f9c1e0a7b2d4c6e8f0a1b2c3d4e5f60")
    assert (s.platen_url, s.platen_key, s.run_sheet_printer) == ("", "", "dispatch")
    assert s.worker_poll_seconds == 5.0 and s.worker_http_timeout_seconds == 10.0
```
In `api/tests/test_auth.py`, the dispatcher's scopes (line 14) become:
```python
    assert r.json()["scopes"] == ["fleet:read", "consignments:read", "runs:read", "runs:write"]
```

**Step 2: Run them to see them fail**

Run: `.venv/bin/pytest tests/test_config.py tests/test_auth.py -q`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'platen_url'`, and the scopes differ.

**Step 3: Implement**

In `api/tms/config.py`, after `wms_timeout_seconds: float = 5.0`:
```python

    # Platen, or any print service that takes the same job: its intake
    # address (https://<platen>/intake/jobs), a Platen key, and the printer
    # run sheets go to (a Platen printer's id or name).
    platen_url: str = ""
    platen_key: str = ""
    run_sheet_printer: str = "dispatch"

    # The worker that sends the ship calls and print jobs.
    worker_poll_seconds: float = 5.0
    worker_http_timeout_seconds: float = 10.0
```

In `api/tms/api/errors.py`, before `class Unauthorised`:
```python
class Conflict(Exception):
    """Well formed, but not possible as things stand: a run that has left, a
    truck over its limit. `code` is for programs, `message` for people."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)
```
and in `install`, before the `Unauthorised` handler:
```python
    @app.exception_handler(Conflict)
    async def _conflict(request: Request, exc: Conflict):
        return JSONResponse(status_code=409, content={"detail": exc.message, "code": exc.code})
```
(the same `409 { "detail", "code" }` as Simple WMS's `Conflict`).

In `api/tms/services/sessions.py`:
```python
ROLE_SCOPES: dict[str, list[str]] = {
    "admin": ["*"],
    "manager": ["fleet:read", "fleet:write", "consignments:read", "runs:read", "runs:write"],
    "dispatcher": ["fleet:read", "consignments:read", "runs:read", "runs:write"],
}
```

**Step 4: Run the tests**

Run: `.venv/bin/pytest -q`
Expected: all pass (128).

**Step 5: Commit**

```bash
git add api/tms/config.py api/tms/api/errors.py api/tms/services/sessions.py api/tests/test_config.py api/tests/test_auth.py
git commit -m "Add the Platen and worker settings, a 409 with a code, and the runs scopes"
```

### Task 7: Runs, stops, the two queues and API keys in the database

**Files:**
- Create: `api/tms/models/runs.py`
- Modify: `api/tms/models/access.py` (`ApiKey`), `api/tms/models/integration.py` (`OutboundCall`, `PrintJob`), `api/tms/models/__init__.py`
- Create: `api/alembic/versions/0002_runs.py`
- Modify: `api/tests/test_migrations.py` (`TABLES`)

**Step 1: Write the failing test**

In `api/tests/test_migrations.py`:
```python
TABLES = {"user", "user_session", "driver", "vehicle", "consignment", "package",
          "consignment_event", "inbound_event", "run", "stop", "outbound_call", "print_job",
          "api_key", "alembic_version"}
```

**Step 2: Run it to see it fail**

Run: `.venv/bin/pytest tests/test_migrations.py -q`
Expected: FAIL — the new tables are missing.

**Step 3: The models**

`api/tms/models/runs.py`:
```python
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Index, Integer, String, and_, text
from sqlalchemy.orm import Mapped, foreign, mapped_column, relationship

from tms.models.access import Driver
from tms.models.base import Base, created_at_column
from tms.models.consignment import Consignment
from tms.models.fleet import Vehicle


class Run(Base):
    """One truck's day: a vehicle, a driver, and stops in the order the
    dispatcher chose. Cancelled, never deleted."""

    __tablename__ = "run"
    __table_args__ = (
        # one live run per truck per day; a cancelled one frees the truck
        Index("uq_run_vehicle_id_day", "vehicle_id", "day", unique=True,
              postgresql_where=text("status <> 'cancelled'")),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicle.id"), index=True)
    driver_id: Mapped[int] = mapped_column(ForeignKey("driver.id"), index=True)
    # planned, departed, done (with the driver app), cancelled
    status: Mapped[str] = mapped_column(String(16), default="planned")
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = created_at_column()
    departed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    vehicle: Mapped[Vehicle] = relationship()
    driver: Mapped[Driver] = relationship()
    # the stops on it now, in order; ones taken off keep their row
    stops: Mapped[list[Stop]] = relationship(
        primaryjoin=lambda: and_(Run.id == foreign(Stop.run_id), Stop.removed_at.is_(None)),
        order_by=lambda: Stop.sequence, viewonly=True)

    @property
    def number(self) -> str:
        """What the dispatcher, the driver and Simple WMS call it: R-0042."""
        return f"R-{self.id:04d}"


class Stop(Base):
    """A consignment's place on a run. Taken off the run, the row stays with
    removed_at set: cancel, never delete."""

    __tablename__ = "stop"
    __table_args__ = (
        # a consignment is on one run at a time, however many people drag it
        Index("uq_stop_consignment_id_live", "consignment_id", unique=True,
              postgresql_where=text("removed_at is null")),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("run.id"), index=True)
    consignment_id: Mapped[int] = mapped_column(ForeignKey("consignment.id"))
    sequence: Mapped[int] = mapped_column(Integer)
    added_by: Mapped[str] = mapped_column(String(64))
    added_at: Mapped[datetime] = created_at_column()
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    consignment: Mapped[Consignment] = relationship()
```
`stops` is read-only and shows only the live stops, in order; a stop is changed through its own row.

Append to `api/tms/models/access.py` (and add `from sqlalchemy.dialects.postgresql import JSONB` to its imports):
```python
class ApiKey(Base):
    """A key for a system, not a person: Platen saying a run sheet printed.
    Only its hash is kept; the key itself is shown once, when it is made.
    Revoked, never deleted."""

    __tablename__ = "api_key"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    scopes: Mapped[list] = mapped_column(JSONB, default=list)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = created_at_column()
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
```

In `api/tms/models/integration.py`, the imports become:
```python
from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
```
and append:
```python
class OutboundCall(Base):
    """A call to Simple WMS, waiting or made: today the ship call when a run
    departs. The worker makes it, retrying after 1 min, 5, 30 and 2 h; the
    same message_id goes with every try, so Simple WMS acts on it once."""

    __tablename__ = "outbound_call"
    __table_args__ = (Index("ix_outbound_call_due", "status", "next_attempt_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[uuid.UUID] = mapped_column(Uuid, unique=True)
    # ship
    kind: Mapped[str] = mapped_column(String(16))
    # the Simple WMS path, e.g. /v1/deliveries/0080012345/ship, and the body
    path: Mapped[str] = mapped_column(String(200))
    body: Mapped[dict] = mapped_column(JSONB)
    run_id: Mapped[int] = mapped_column(ForeignKey("run.id"), index=True)
    consignment_id: Mapped[int] = mapped_column(ForeignKey("consignment.id"), index=True)
    # pending, done, failed
    status: Mapped[str] = mapped_column(String(16), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(500))
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at_column()


class PrintJob(Base):
    """A document for Platen: a template name and JSON, never a rendered
    page. The worker sends it with the same backoff as the ship calls;
    Platen says printed or failed on POST /v1/print-jobs/{job_id}/status."""

    __tablename__ = "print_job"
    __table_args__ = (Index("ix_print_job_due", "status", "next_attempt_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[uuid.UUID] = mapped_column(Uuid, unique=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("run.id"), index=True)
    template: Mapped[str] = mapped_column(String(64))
    version: Mapped[str] = mapped_column(String(16))
    printer: Mapped[str] = mapped_column(String(64))
    copies: Mapped[int] = mapped_column(Integer, default=1)
    reference: Mapped[dict] = mapped_column(JSONB, default=dict)
    data: Mapped[dict] = mapped_column(JSONB, default=dict)
    requested_by: Mapped[str] = mapped_column(String(64))
    # pending, accepted, printed, failed
    status: Mapped[str] = mapped_column(String(16), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(500))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    printed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at_column()
```

`api/tms/models/__init__.py`:
```python
from tms.models.base import Base
from tms.models.access import ApiKey, Driver, User, UserSession
from tms.models.fleet import Vehicle
from tms.models.consignment import Consignment, ConsignmentEvent, Package
from tms.models.integration import InboundEvent, OutboundCall, PrintJob
from tms.models.runs import Run, Stop

__all__ = [
    "Base", "User", "UserSession", "Driver", "Vehicle", "Consignment", "Package",
    "ConsignmentEvent", "InboundEvent", "Run", "Stop", "OutboundCall", "PrintJob", "ApiKey",
]
```

**Step 4: The migration**

`api/alembic/versions/0002_runs.py`:
```python
"""runs and stops, the ship-call and print-job queues, API keys

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-10
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _at(name: str = "created_at") -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.text("now()"),
                     nullable=False)


def upgrade() -> None:
    op.create_table(
        "run",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("vehicle_id", sa.Integer(), nullable=False),
        sa.Column("driver_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        _at(),
        sa.Column("departed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["vehicle_id"], ["vehicle.id"],
                                name=op.f("fk_run_vehicle_id_vehicle")),
        sa.ForeignKeyConstraint(["driver_id"], ["driver.id"], name=op.f("fk_run_driver_id_driver")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_run")),
    )
    op.create_index(op.f("ix_run_day"), "run", ["day"])
    op.create_index(op.f("ix_run_vehicle_id"), "run", ["vehicle_id"])
    op.create_index(op.f("ix_run_driver_id"), "run", ["driver_id"])
    op.create_index("uq_run_vehicle_id_day", "run", ["vehicle_id", "day"], unique=True,
                    postgresql_where=sa.text("status <> 'cancelled'"))

    op.create_table(
        "stop",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("consignment_id", sa.Integer(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("added_by", sa.String(length=64), nullable=False),
        _at("added_at"),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["run.id"], name=op.f("fk_stop_run_id_run")),
        sa.ForeignKeyConstraint(["consignment_id"], ["consignment.id"],
                                name=op.f("fk_stop_consignment_id_consignment")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stop")),
    )
    op.create_index(op.f("ix_stop_run_id"), "stop", ["run_id"])
    op.create_index("uq_stop_consignment_id_live", "stop", ["consignment_id"], unique=True,
                    postgresql_where=sa.text("removed_at is null"))

    op.create_table(
        "outbound_call",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("path", sa.String(length=200), nullable=False),
        sa.Column("body", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("consignment_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_error", sa.String(length=500), nullable=True),
        sa.Column("done_at", sa.DateTime(timezone=True), nullable=True),
        _at(),
        sa.ForeignKeyConstraint(["run_id"], ["run.id"], name=op.f("fk_outbound_call_run_id_run")),
        sa.ForeignKeyConstraint(["consignment_id"], ["consignment.id"],
                                name=op.f("fk_outbound_call_consignment_id_consignment")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_outbound_call")),
        sa.UniqueConstraint("message_id", name=op.f("uq_outbound_call_message_id")),
    )
    op.create_index(op.f("ix_outbound_call_run_id"), "outbound_call", ["run_id"])
    op.create_index(op.f("ix_outbound_call_consignment_id"), "outbound_call", ["consignment_id"])
    op.create_index("ix_outbound_call_due", "outbound_call", ["status", "next_attempt_at"])

    op.create_table(
        "print_job",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("template", sa.String(length=64), nullable=False),
        sa.Column("version", sa.String(length=16), nullable=False),
        sa.Column("printer", sa.String(length=64), nullable=False),
        sa.Column("copies", sa.Integer(), nullable=False),
        sa.Column("reference", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("requested_by", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_error", sa.String(length=500), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("printed_at", sa.DateTime(timezone=True), nullable=True),
        _at(),
        sa.ForeignKeyConstraint(["run_id"], ["run.id"], name=op.f("fk_print_job_run_id_run")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_print_job")),
        sa.UniqueConstraint("job_id", name=op.f("uq_print_job_job_id")),
    )
    op.create_index(op.f("ix_print_job_run_id"), "print_job", ["run_id"])
    op.create_index("ix_print_job_due", "print_job", ["status", "next_attempt_at"])

    op.create_table(
        "api_key",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("scopes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        _at(),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_api_key")),
        sa.UniqueConstraint("name", name=op.f("uq_api_key_name")),
        sa.UniqueConstraint("key_hash", name=op.f("uq_api_key_key_hash")),
    )


def downgrade() -> None:
    op.drop_table("api_key")
    op.drop_index("ix_print_job_due", table_name="print_job")
    op.drop_index(op.f("ix_print_job_run_id"), table_name="print_job")
    op.drop_table("print_job")
    op.drop_index("ix_outbound_call_due", table_name="outbound_call")
    op.drop_index(op.f("ix_outbound_call_consignment_id"), table_name="outbound_call")
    op.drop_index(op.f("ix_outbound_call_run_id"), table_name="outbound_call")
    op.drop_table("outbound_call")
    op.drop_index("uq_stop_consignment_id_live", table_name="stop")
    op.drop_index(op.f("ix_stop_run_id"), table_name="stop")
    op.drop_table("stop")
    op.drop_index("uq_run_vehicle_id_day", table_name="run")
    op.drop_index(op.f("ix_run_driver_id"), table_name="run")
    op.drop_index(op.f("ix_run_vehicle_id"), table_name="run")
    op.drop_index(op.f("ix_run_day"), table_name="run")
    op.drop_table("run")
```

**Step 5: Run the tests**

Run: `.venv/bin/pytest tests/test_migrations.py -q && .venv/bin/pytest -q`
Expected: `2 passed` (up, down, up; and no drift between the models and the migration), then all pass.

**Step 6: Commit**

```bash
git add api/tms/models api/alembic/versions/0002_runs.py api/tests/test_migrations.py
git commit -m "Add runs, stops, the ship-call and print-job queues, and API keys"
```

### Task 8: How full a run is, in decimals

**Files:**
- Create: `api/tms/services/load.py`
- Create: `api/tests/test_load.py`

**Step 1: Write the failing tests**

`api/tests/test_load.py`:
```python
"""How full a run is, in decimals, with a plain message when it matters."""
from decimal import Decimal
from types import SimpleNamespace

from tms.services.load import Totals, consignment_totals, load, plain, total

D = Decimal


def pkg(kg=None, l=None, w=None, h=None, type="carton"):
    return SimpleNamespace(weight_kg=None if kg is None else D(kg),
                           length_cm=None if l is None else D(l),
                           width_cm=None if w is None else D(w),
                           height_cm=None if h is None else D(h), type=type)


def test_a_consignment_adds_up_its_packages_exactly():
    t = consignment_totals([pkg("9.5", "60", "40", "40"), pkg("0.1", "10", "10", "10")])
    assert t.weight_kg == D("9.6")
    assert t.volume_m3 == D("0.097")          # 0.096 + 0.001, no float drift
    assert (t.packages, t.cartons, t.unknown_weight, t.unknown_volume) == (2, 2, 0, 0)


def test_a_package_without_a_weight_or_size_adds_nothing_and_is_counted():
    t = consignment_totals([pkg(None, "60", "40", "40"),
                            pkg("2", "60", None, "40", type="satchel")])
    assert t.weight_kg == D("2") and t.volume_m3 == D("0.096")
    assert (t.unknown_weight, t.unknown_volume, t.cartons) == (1, 1, 1)


def test_totals_add_up():
    a = Totals(D("1.5"), D("0.2"), 1, 1, 0, 1)
    b = Totals(D("2"), D("0.05"), 3, 2, 1, 0)
    assert total([a, b]) == Totals(D("3.5"), D("0.25"), 4, 3, 1, 1)
    assert total([]) == Totals(D(0), D(0), 0, 0, 0, 0)


def test_well_under_the_limits_is_fine():
    out = load(Totals(D("2380"), D("11.2"), 5, 5, 0, 0), D("4500"), D("22"))
    assert out.state == "ok" and out.message is None


def test_ninety_per_cent_or_more_is_nearly_full():
    out = load(Totals(D("1120"), D("8.6"), 5, 5, 0, 0), D("1200"), D("9"))
    assert out.state == "near"
    assert out.message == "80 kg and 0.4 m³ left. Small drops only."
    exactly_full = load(Totals(D("1200"), D("1"), 1, 1, 0, 0), D("1200"), D("9"))
    assert exactly_full.state == "near"


def test_over_either_limit_is_over_and_says_by_how_much():
    heavy = load(Totals(D("4780"), D("19.4"), 4, 4, 0, 0), D("4500"), D("22"))
    assert heavy.state == "over"
    assert heavy.message == ("280 kg too heavy. Move a stop to another run before printing "
                             "the run sheet.")
    both = load(Totals(D("4500.2"), D("22.01"), 4, 4, 0, 0), D("4500"), D("22"))
    assert both.message.startswith("1 kg too heavy and 0.1 m³ too big.")


def test_left_rounds_down_and_over_rounds_up():
    out = load(Totals(D("1099.6"), D("8.15"), 1, 1, 0, 0), D("1200"), D("9"))
    assert out.message == "100 kg and 0.8 m³ left. Small drops only."


def test_plain_numbers_have_no_exponent_and_group_thousands():
    assert plain(D("280.000000")) == "280"
    assert plain(D("2380.5")) == "2,380.5"
    assert plain(D("0")) == "0"
    assert plain(D("-1200")) == "-1,200"
```

**Step 2: Run them to see them fail**

Run: `.venv/bin/pytest tests/test_load.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'tms.services.load'`.

**Step 3: Implement**

`api/tms/services/load.py`:
```python
"""How full a run is: weight and volume against the truck's limits. Decimals
all the way. A package with no weight, or without all three sides, adds
nothing and is counted, so the screen can say so."""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

# nearly full from here: amber on the plan, "small drops only"
NEAR = Decimal("0.9")
CM3_PER_M3 = Decimal(1_000_000)
KG = Decimal(1)
M3 = Decimal("0.1")


@dataclass(frozen=True)
class Totals:
    weight_kg: Decimal
    volume_m3: Decimal
    packages: int
    cartons: int
    unknown_weight: int
    unknown_volume: int


@dataclass(frozen=True)
class Load:
    weight_kg: Decimal
    volume_m3: Decimal
    max_kg: Decimal
    max_m3: Decimal
    unknown_weight: int
    unknown_volume: int
    state: str  # ok, near, over
    message: str | None


def consignment_totals(packages: Iterable) -> Totals:
    weight = volume = Decimal(0)
    count = cartons = no_weight = no_size = 0
    for p in packages:
        count += 1
        cartons += p.type == "carton"
        if p.weight_kg is None:
            no_weight += 1
        else:
            weight += p.weight_kg
        if p.length_cm is None or p.width_cm is None or p.height_cm is None:
            no_size += 1
        else:
            volume += p.length_cm * p.width_cm * p.height_cm / CM3_PER_M3
    return Totals(weight, volume, count, cartons, no_weight, no_size)


def total(parts: Iterable[Totals]) -> Totals:
    out = Totals(Decimal(0), Decimal(0), 0, 0, 0, 0)
    for t in parts:
        out = Totals(out.weight_kg + t.weight_kg, out.volume_m3 + t.volume_m3,
                     out.packages + t.packages, out.cartons + t.cartons,
                     out.unknown_weight + t.unknown_weight, out.unknown_volume + t.unknown_volume)
    return out


def plain(d: Decimal) -> str:
    """For people: 2380.500000 -> "2,380.5". Never an exponent."""
    text = format(d.normalize(), "f") if d else "0"
    whole, _, frac = text.partition(".")
    sign = "-" if whole.startswith("-") else ""
    return f"{sign}{int(whole.lstrip('-')):,}" + (f".{frac}" if frac else "")


def _up(d: Decimal, step: Decimal) -> str:
    return plain(d.quantize(step, rounding=ROUND_CEILING))


def _down(d: Decimal, step: Decimal) -> str:
    return plain(d.quantize(step, rounding=ROUND_FLOOR))


def load(t: Totals, max_kg: Decimal, max_m3: Decimal) -> Load:
    """Over either limit is over; 90 % or more of either is nearly full. What is
    over rounds up, so moving that much is enough; what is left rounds down."""
    over_kg, over_m3 = t.weight_kg - max_kg, t.volume_m3 - max_m3
    if over_kg > 0 or over_m3 > 0:
        parts = []
        if over_kg > 0:
            parts.append(f"{_up(over_kg, KG)} kg too heavy")
        if over_m3 > 0:
            parts.append(f"{_up(over_m3, M3)} m³ too big")
        state = "over"
        message = (" and ".join(parts)
                   + ". Move a stop to another run before printing the run sheet.")
    elif t.weight_kg >= max_kg * NEAR or t.volume_m3 >= max_m3 * NEAR:
        state = "near"
        message = (f"{_down(-over_kg, KG)} kg and {_down(-over_m3, M3)} m³ left. "
                   "Small drops only.")
    else:
        state, message = "ok", None
    return Load(t.weight_kg, t.volume_m3, max_kg, max_m3, t.unknown_weight, t.unknown_volume,
                state, message)
```

**Step 4: Run the tests**

Run: `.venv/bin/pytest tests/test_load.py -q`
Expected: `8 passed`.

**Step 5: Commit**

```bash
git add api/tms/services/load.py api/tests/test_load.py
git commit -m "Work out a run's weight and volume against its truck, in decimals"
```

### Task 9: Each consignment says what it weighs and takes up

The tray on the plan board needs each consignment's totals.

**Files:**
- Modify: `api/tms/api/routes/consignments.py`
- Modify: `api/tests/test_consignments.py`

**Step 1: Write the failing test**

Append to `api/tests/test_consignments.py`:
```python
def test_each_consignment_says_what_it_weighs_and_takes_up(client, wms, dispatcher_headers):
    cid = arrive(client, wms, "2044", "Ballarat Hardware")
    listed = client.get("/v1/consignments", headers=dispatcher_headers).json()["items"][0]
    # one 9.5 kg carton of 60 x 40 x 40 cm, and a satchel with no weight or size
    assert listed["weight_kg"] == "9.5" and listed["volume_m3"] == "0.096"
    one = client.get(f"/v1/consignments/{cid}", headers=dispatcher_headers).json()
    assert one["weight_kg"] == "9.5" and one["volume_m3"] == "0.096"
```

**Step 2: Run it to see it fail**

Run: `.venv/bin/pytest tests/test_consignments.py -q -k weighs`
Expected: FAIL — `KeyError: 'weight_kg'`.

**Step 3: Implement**

In `api/tms/api/routes/consignments.py` add `from tms.services.load import consignment_totals`; in `ConsignmentOut`, before `packages`:
```python
    # the packages added up: what it puts on a truck
    weight_kg: Dec
    volume_m3: Dec
```
and `consignment_out` becomes:
```python
def consignment_out(c: Consignment) -> dict:
    totals = consignment_totals(c.packages)
    return dict(
        id=c.id, wms_ref=c.wms_ref, warehouse=c.warehouse, owner=c.owner, customer=c.customer,
        ship_to=c.ship_to or {}, required_by=c.required_by, status=c.status,
        created_at=c.created_at, weight_kg=totals.weight_kg, volume_m3=totals.volume_m3,
        packages=[PackageOut(package_no=p.package_no, type=p.type, weight_kg=p.weight_kg,
                             length_cm=p.length_cm, width_cm=p.width_cm, height_cm=p.height_cm,
                             sscc=p.sscc) for p in c.packages],
    )
```

**Step 4: Run the tests**

Run: `.venv/bin/pytest tests/test_consignments.py -q`
Expected: all pass.

**Step 5: Commit**

```bash
git add api/tms/api/routes/consignments.py api/tests/test_consignments.py
git commit -m "Give each consignment its weight and volume"
```

### Task 10: Making runs: a truck and a driver for a day

**Files:**
- Create: `api/tms/services/runs.py`
- Create: `api/tms/api/routes/runs.py`
- Modify: `api/tms/api/app.py`
- Create: `api/tests/runs_samples.py`, `api/tests/test_runs.py`

**Step 1: Write the failing tests**

`api/tests/runs_samples.py` (trucks, drivers and packed consignments made straight in the database, so a planning test starts with freight waiting):
```python
"""Trucks, drivers and packed consignments made straight in the database,
so a planning test starts from a depot with freight waiting."""
from datetime import UTC, date, datetime
from decimal import Decimal

from tms.models import Consignment, ConsignmentEvent, Driver, Package, Vehicle
from tms.services.access import hash_password

DAY = date(2026, 10, 14)


def vehicle(db, rego="TMS041", max_kg="1200", max_m3="9", type="van", active=True) -> Vehicle:
    v = Vehicle(rego=rego, type=type, max_kg=Decimal(max_kg), max_m3=Decimal(max_m3),
                active=active)
    db.add(v)
    db.commit()
    return v


def driver(db, code="kim", name="Kim") -> Driver:
    d = Driver(code=code, name=name, pin_hash=hash_password("2468"), failed_attempts=0,
               locked=False, active=True)
    db.add(d)
    db.commit()
    return d


def consignment(db, ref="0080034519", customer="Station Deli", suburb="Ballan",
                kg="100", cm=("100", "100", "50"), owner="DEFAULT") -> Consignment:
    """One carton: 100 kg and 0.5 m³ unless told otherwise."""
    c = Consignment(wms_ref=ref, warehouse="BAL-WH01", owner=owner, customer=customer,
                    ship_to={"name": customer, "address": "1 Main St", "suburb": suburb,
                             "state": "VIC", "postcode": "3342"},
                    status="packed")
    c.packages.append(Package(package_no=1, type="carton", weight_kg=Decimal(kg),
                              length_cm=Decimal(cm[0]), width_cm=Decimal(cm[1]),
                              height_cm=Decimal(cm[2])))
    c.events.append(ConsignmentEvent(kind="packed", at=datetime.now(UTC), detail={}))
    db.add(c)
    db.commit()
    return c


def new_run(client, headers, rego="TMS041", code="kim", day=DAY) -> dict:
    r = client.post("/v1/runs", headers=headers,
                    json={"date": day.isoformat(), "vehicle": rego, "driver": code})
    assert r.status_code == 201, r.text
    return r.json()


def assign(client, headers, run_id: int, consignment_id: int, position: int | None = None):
    body = {"consignment_id": consignment_id}
    if position is not None:
        body["position"] = position
    return client.post(f"/v1/runs/{run_id}/assign", headers=headers, json=body)


def order(run: dict) -> list[int]:
    return [s["consignment"]["id"] for s in run["stops"]]
```

`api/tests/test_runs.py`:
```python
"""Making runs: a truck and a driver for a day."""
from runs_samples import DAY, driver, new_run, vehicle


def test_a_dispatcher_makes_a_run_for_a_day(client, db, dispatcher_headers):
    vehicle(db)
    driver(db)
    run = new_run(client, dispatcher_headers)
    assert run["run_no"] == f"R-{run['id']:04d}"
    assert run["date"] == "2026-10-14" and run["status"] == "planned"
    assert run["vehicle"] == {"rego": "TMS041", "type": "van", "max_kg": "1200", "max_m3": "9",
                              "active": True}
    assert run["driver"] == {"code": "kim", "name": "Kim"}
    assert run["created_by"] == "dee"
    assert run["stops"] == []
    assert run["load"] == {"weight_kg": "0", "volume_m3": "0", "max_kg": "1200", "max_m3": "9",
                           "unknown_weight": 0, "unknown_volume": 0, "state": "ok",
                           "message": None}
    assert run["run_sheet"] is None
    assert run["shipping"] == {"pending": 0, "done": 0, "failed": 0}


def test_the_rego_is_read_however_it_is_typed(client, db, dispatcher_headers):
    vehicle(db)
    driver(db)
    assert new_run(client, dispatcher_headers, rego="tms 041")["vehicle"]["rego"] == "TMS041"


def test_a_day_lists_its_runs(client, db, dispatcher_headers):
    vehicle(db)
    vehicle(db, rego="TMS018")
    driver(db)
    a = new_run(client, dispatcher_headers)
    b = new_run(client, dispatcher_headers, rego="TMS018")
    r = client.get("/v1/runs", params={"date": DAY.isoformat()}, headers=dispatcher_headers)
    assert r.status_code == 200
    assert [x["id"] for x in r.json()["items"]] == [a["id"], b["id"]]
    other = client.get("/v1/runs", params={"date": "2026-10-15"}, headers=dispatcher_headers)
    assert other.json()["total"] == 0
    assert client.get("/v1/runs", headers=dispatcher_headers).status_code == 422


def test_a_truck_takes_one_run_a_day(client, db, dispatcher_headers):
    vehicle(db)
    driver(db)
    driver(db, code="raj", name="Raj")
    first = new_run(client, dispatcher_headers)
    r = client.post("/v1/runs", headers=dispatcher_headers,
                    json={"date": DAY.isoformat(), "vehicle": "TMS041", "driver": "raj"})
    assert r.status_code == 422
    taken = f"TMS041 already has {first['run_no']} on 2026-10-14"
    assert r.json()["errors"] == [{"field": "vehicle", "message": taken}]
    tomorrow = client.post("/v1/runs", headers=dispatcher_headers,
                           json={"date": "2026-10-15", "vehicle": "TMS041", "driver": "raj"})
    assert tomorrow.status_code == 201


def test_a_deactivated_truck_or_driver_cannot_take_a_run(client, db, dispatcher_headers):
    vehicle(db, active=False)
    vehicle(db, rego="TMS018")
    d = driver(db)
    r = client.post("/v1/runs", headers=dispatcher_headers,
                    json={"date": DAY.isoformat(), "vehicle": "TMS041", "driver": "kim"})
    assert r.status_code == 422
    assert r.json()["errors"][0] == {"field": "vehicle",
                                     "message": "TMS041 is deactivated, so it can't take a run"}
    d.active = False
    db.commit()
    r = client.post("/v1/runs", headers=dispatcher_headers,
                    json={"date": DAY.isoformat(), "vehicle": "TMS018", "driver": "kim"})
    assert r.status_code == 422 and r.json()["errors"][0]["field"] == "driver"


def test_an_unknown_truck_or_driver_is_named(client, db, dispatcher_headers):
    r = client.post("/v1/runs", headers=dispatcher_headers,
                    json={"date": DAY.isoformat(), "vehicle": "NOPE", "driver": "kim"})
    assert r.status_code == 422 and r.json()["errors"][0]["message"] == "no vehicle NOPE"


def test_a_planned_run_changes_truck_or_driver(client, db, dispatcher_headers):
    vehicle(db)
    vehicle(db, rego="TMS052", type="rigid", max_kg="4500", max_m3="22")
    driver(db)
    driver(db, code="mel", name="Mel")
    run = new_run(client, dispatcher_headers)
    r = client.patch(f"/v1/runs/{run['id']}", headers=dispatcher_headers,
                     json={"vehicle": "TMS052", "driver": "mel"})
    assert r.status_code == 200, r.text
    assert r.json()["vehicle"]["rego"] == "TMS052" and r.json()["driver"]["code"] == "mel"
    assert r.json()["load"]["max_kg"] == "4500"


def test_an_empty_run_is_cancelled_and_frees_the_truck(client, db, dispatcher_headers):
    vehicle(db)
    driver(db)
    run = new_run(client, dispatcher_headers)
    r = client.post(f"/v1/runs/{run['id']}/cancel", headers=dispatcher_headers)
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    listed = client.get("/v1/runs", params={"date": DAY.isoformat()}, headers=dispatcher_headers)
    assert listed.json()["total"] == 0
    with_cancelled = client.get("/v1/runs", params={"date": DAY.isoformat(), "status": "cancelled"},
                                headers=dispatcher_headers)
    assert with_cancelled.json()["total"] == 1
    new_run(client, dispatcher_headers)     # the truck is free again
    again = client.post(f"/v1/runs/{run['id']}/cancel", headers=dispatcher_headers)
    assert again.status_code == 200 and again.json()["status"] == "cancelled"


def test_a_driver_cannot_plan_and_nobody_without_a_session_can_look(client, db):
    from conftest import bearer
    driver(db)
    signed_in = client.post("/v1/auth/driver-login", json={"code": "kim", "pin": "2468"})
    token = signed_in.json()["token"]
    day = {"date": DAY.isoformat()}
    assert client.get("/v1/runs", params=day, headers=bearer(token)).status_code == 403
    assert client.get("/v1/runs", params=day).status_code == 401
```

**Step 2: Run them to see them fail**

Run: `.venv/bin/pytest tests/test_runs.py -q`
Expected: FAIL — `404` from `POST /v1/runs`.

**Step 3: The service**

`api/tms/services/runs.py`:
```python
"""Planning runs: which consignments go on which truck, in what order.

Every change to a plan takes one lock first (a Postgres advisory lock held to
the end of the transaction), then reads what it changes. Two dispatchers
dragging at once are served one after the other, each seeing the other's
work, so a sequence never has a gap or a twin and a consignment is never on
two runs. Planning changes take milliseconds and a depot has a handful of
dispatchers, so one lock costs nothing, and with one lock there is no order
to get wrong and no deadlock. The partial unique indexes on run and stop
stand behind it."""
from __future__ import annotations

from datetime import date

from sqlalchemy import select, text
from sqlalchemy.orm import Session, selectinload

from tms.api.errors import Conflict, FieldError, NotFound
from tms.models import Consignment, Driver, Run, Stop, Vehicle
from tms.services.load import Load, consignment_totals, load, total

PLANNING_LOCK = 7_302_201


def lock_planning(db: Session) -> None:
    db.execute(text("select pg_advisory_xact_lock(:k)"), {"k": PLANNING_LOCK})


def get_run(db: Session, run_id: int) -> Run:
    run = db.execute(
        select(Run).where(Run.id == run_id)
        .options(selectinload(Run.stops).selectinload(Stop.consignment)
                 .selectinload(Consignment.packages),
                 selectinload(Run.vehicle), selectinload(Run.driver))
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if run is None:
        raise NotFound(f"no run {run_id}")
    return run


def _planned(db: Session, run_id: int) -> Run:
    """The run, read after the lock, and still open to changes."""
    run = get_run(db, run_id)
    if run.status != "planned":
        raise Conflict("run_not_planned",
                       f"{run.number} has {run.status}; its stops can't change")
    return run


def run_load(run: Run) -> Load:
    return load(total(consignment_totals(s.consignment.packages) for s in run.stops),
                run.vehicle.max_kg, run.vehicle.max_m3)


def _vehicle(db: Session, rego: str) -> Vehicle:
    v = db.execute(select(Vehicle).where(
        Vehicle.rego == "".join(rego.split()).upper())).scalar_one_or_none()
    if v is None:
        raise FieldError("vehicle", f"no vehicle {rego}")
    if not v.active:
        raise FieldError("vehicle", f"{v.rego} is deactivated, so it can't take a run")
    return v


def _driver(db: Session, code: str) -> Driver:
    d = db.execute(select(Driver).where(Driver.code == code)).scalar_one_or_none()
    if d is None:
        raise FieldError("driver", f"no driver {code}")
    if not d.active:
        raise FieldError("driver", f"{d.code} is deactivated, so they can't take a run")
    return d


def _truck_free(db: Session, vehicle: Vehicle, day: date, other_than: int | None = None) -> None:
    taken = db.execute(select(Run).where(
        Run.vehicle_id == vehicle.id, Run.day == day, Run.status != "cancelled",
        Run.id != (other_than or 0))).scalars().first()
    if taken is not None:
        raise FieldError("vehicle", f"{vehicle.rego} already has {taken.number} on {day}")


def create(db: Session, *, day: date, rego: str, driver_code: str, by: str) -> Run:
    lock_planning(db)
    vehicle, driver = _vehicle(db, rego), _driver(db, driver_code)
    _truck_free(db, vehicle, day)
    run = Run(day=day, vehicle_id=vehicle.id, driver_id=driver.id, status="planned",
              created_by=by)
    db.add(run)
    db.flush()
    return run


def change(db: Session, run_id: int, *, rego: str | None, driver_code: str | None) -> Run:
    lock_planning(db)
    run = _planned(db, run_id)
    if rego is not None:
        vehicle = _vehicle(db, rego)
        _truck_free(db, vehicle, run.day, other_than=run.id)
        run.vehicle_id = vehicle.id
    if driver_code is not None:
        run.driver_id = _driver(db, driver_code).id
    db.flush()
    return run


def cancel(db: Session, run_id: int) -> Run:
    """Only an empty planned run: take its stops off first, so each one's
    history says where it went."""
    lock_planning(db)
    run = get_run(db, run_id)
    if run.status == "cancelled":
        return run
    run = _planned(db, run_id)
    if run.stops:
        raise Conflict("run_not_empty",
                       f"{run.number} still has {len(run.stops)} stops; move them off first")
    run.status = "cancelled"
    db.flush()
    return run
```

**Step 4: The routes**

`api/tms/api/routes/runs.py`:
```python
"""Runs: a truck's day, its stops in order, and the trip out of the depot."""
from __future__ import annotations

import datetime as dt
import uuid

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from tms.api.deps import DB, Principal, require
from tms.api.schemas import Dec, Page
from tms.models import Consignment, OutboundCall, PrintJob, Run
from tms.services import runs
from tms.services.load import consignment_totals

router = APIRouter(tags=["runs"])


class RunIn(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    day: dt.date = Field(alias="date")
    vehicle: str = Field(min_length=1, max_length=12)
    driver: str = Field(min_length=1, max_length=32)


class RunPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    vehicle: str | None = Field(default=None, min_length=1, max_length=12)
    driver: str | None = Field(default=None, min_length=1, max_length=32)


class StopConsignment(BaseModel):
    id: int
    wms_ref: str
    customer: str
    suburb: str | None
    status: str
    packages: int
    cartons: int
    weight_kg: Dec
    volume_m3: Dec


class StopOut(BaseModel):
    sequence: int
    consignment: StopConsignment


class LoadOut(BaseModel):
    weight_kg: Dec
    volume_m3: Dec
    max_kg: Dec
    max_m3: Dec
    unknown_weight: int
    unknown_volume: int
    state: str
    message: str | None


class RunVehicle(BaseModel):
    rego: str
    type: str
    max_kg: Dec
    max_m3: Dec
    active: bool


class RunDriver(BaseModel):
    code: str
    name: str


class RunSheetOut(BaseModel):
    job_id: uuid.UUID
    status: str
    printer: str
    last_error: str | None
    created_at: dt.datetime
    printed_at: dt.datetime | None


class ShippingOut(BaseModel):
    pending: int = 0
    done: int = 0
    failed: int = 0


class RunOut(BaseModel):
    id: int
    run_no: str
    date: dt.date
    status: str
    vehicle: RunVehicle
    driver: RunDriver
    created_by: str
    created_at: dt.datetime
    departed_at: dt.datetime | None
    stops: list[StopOut]
    load: LoadOut
    run_sheet: RunSheetOut | None
    shipping: ShippingOut


def stop_consignment(c: Consignment) -> StopConsignment:
    t = consignment_totals(c.packages)
    return StopConsignment(id=c.id, wms_ref=c.wms_ref, customer=c.customer,
                           suburb=(c.ship_to or {}).get("suburb"), status=c.status,
                           packages=t.packages, cartons=t.cartons, weight_kg=t.weight_kg,
                           volume_m3=t.volume_m3)


def run_out(db, run: Run) -> RunOut:
    ld = runs.run_load(run)
    sheet = db.execute(select(PrintJob).where(PrintJob.run_id == run.id)
                       .order_by(PrintJob.id.desc()).limit(1)).scalar_one_or_none()
    calls = dict(db.execute(select(OutboundCall.status, func.count())
                            .where(OutboundCall.run_id == run.id)
                            .group_by(OutboundCall.status)).all())
    v = run.vehicle
    return RunOut(
        id=run.id, run_no=run.number, date=run.day, status=run.status,
        vehicle=RunVehicle(rego=v.rego, type=v.type, max_kg=v.max_kg, max_m3=v.max_m3,
                           active=v.active),
        driver=RunDriver(code=run.driver.code, name=run.driver.name),
        created_by=run.created_by, created_at=run.created_at, departed_at=run.departed_at,
        stops=[StopOut(sequence=s.sequence, consignment=stop_consignment(s.consignment))
               for s in run.stops],
        load=LoadOut(weight_kg=ld.weight_kg, volume_m3=ld.volume_m3, max_kg=ld.max_kg,
                     max_m3=ld.max_m3, unknown_weight=ld.unknown_weight,
                     unknown_volume=ld.unknown_volume, state=ld.state, message=ld.message),
        run_sheet=None if sheet is None else RunSheetOut(
            job_id=sheet.job_id, status=sheet.status, printer=sheet.printer,
            last_error=sheet.last_error, created_at=sheet.created_at,
            printed_at=sheet.printed_at),
        shipping=ShippingOut(**calls),
    )


def _fresh(db, run_id: int) -> RunOut:
    db.commit()
    return run_out(db, runs.get_run(db, run_id))


@router.get("/runs", response_model=Page[RunOut])
def list_runs(db: DB, day: dt.date = Query(alias="date"), status: str | None = None,
              who: Principal = require("runs:read")):
    """One day's runs, oldest first. Cancelled ones only when asked for."""
    query = select(Run.id).where(Run.day == day)
    if status:
        query = query.where(Run.status.in_(status.split(",")))
    else:
        query = query.where(Run.status != "cancelled")
    ids = db.execute(query.order_by(Run.id)).scalars().all()
    return Page(items=[run_out(db, runs.get_run(db, i)) for i in ids], total=len(ids))


@router.post("/runs", status_code=201, response_model=RunOut)
def create_run(body: RunIn, db: DB, who: Principal = require("runs:write")):
    run = runs.create(db, day=body.day, rego=body.vehicle, driver_code=body.driver, by=who.name)
    return JSONResponse(status_code=201,
                        content=_fresh(db, run.id).model_dump(mode="json"))


@router.get("/runs/{id}", response_model=RunOut)
def get_run(id: int, db: DB, who: Principal = require("runs:read")):
    return run_out(db, runs.get_run(db, id))


@router.patch("/runs/{id}", response_model=RunOut)
def change_run(id: int, body: RunPatch, db: DB, who: Principal = require("runs:write")):
    runs.change(db, id, rego=body.vehicle, driver_code=body.driver)
    return _fresh(db, id)


@router.post("/runs/{id}/cancel", response_model=RunOut)
def cancel_run(id: int, db: DB, who: Principal = require("runs:write")):
    runs.cancel(db, id)
    return _fresh(db, id)
```
`run_out` already reports the latest run sheet and the ship calls by status (the tables exist since Task 7); they fill in with Tasks 13 and 15. In `api/tms/api/app.py` import `runs` with the other routes and add `v1.include_router(runs.router)` after the consignments router.

**Step 5: Run the tests**

Run: `.venv/bin/pytest tests/test_runs.py -q`
Expected: `9 passed`.

**Step 6: Commit**

```bash
git add api/tms/services/runs.py api/tms/api/routes/runs.py api/tms/api/app.py api/tests/runs_samples.py api/tests/test_runs.py
git commit -m "Make, change and cancel runs: a truck and a driver for a day"
```

### Task 11: Planning stops: assign, take off, reorder, safely at once

**Files:**
- Modify: `api/tms/services/runs.py`
- Modify: `api/tms/api/routes/runs.py`
- Create: `api/tests/test_planning.py`

**Step 1: Write the failing tests**

`api/tests/test_planning.py`:
```python
"""Dragging consignments onto runs and stops into order: idempotent, and
safe with two dispatchers at it at once."""
from sqlalchemy import func, select

from conftest import all_at_once, fresh_client
from runs_samples import assign, consignment, driver, new_run, order, vehicle
from tms.models import Stop


def _depot(client, db, headers, n=3):
    vehicle(db)
    vehicle(db, rego="TMS018")
    driver(db)
    cons = [consignment(db, ref=f"00800345{i:02d}", customer=f"Customer {i}") for i in range(n)]
    return new_run(client, headers), new_run(client, headers, rego="TMS018"), cons


def _history(client, headers, cid) -> list[tuple[str, dict]]:
    h = client.get(f"/v1/consignments/{cid}", headers=headers).json()["history"]
    return [(e["kind"], e["detail"]) for e in h]


def test_a_consignment_goes_on_a_run_last_unless_told_where(client, db, dispatcher_headers):
    run, _, (a, b, c) = _depot(client, db, dispatcher_headers)
    assign(client, dispatcher_headers, run["id"], a.id)
    assign(client, dispatcher_headers, run["id"], b.id)
    r = assign(client, dispatcher_headers, run["id"], c.id, position=1)
    assert r.status_code == 200, r.text
    out = r.json()
    assert order(out) == [c.id, a.id, b.id]
    assert [s["sequence"] for s in out["stops"]] == [1, 2, 3]
    stop = out["stops"][0]["consignment"]
    assert stop == {"id": c.id, "wms_ref": c.wms_ref, "customer": "Customer 2", "suburb": "Ballan",
                    "status": "assigned", "packages": 1, "cartons": 1, "weight_kg": "100",
                    "volume_m3": "0.5"}
    assert out["load"]["weight_kg"] == "300" and out["load"]["volume_m3"] == "1.5"


def test_assigning_is_in_the_history_with_who_and_which_run(client, db, dispatcher_headers):
    run, _, (a, *_) = _depot(client, db, dispatcher_headers)
    assign(client, dispatcher_headers, run["id"], a.id)
    kind, detail = _history(client, dispatcher_headers, a.id)[-1]
    assert kind == "assigned"
    assert detail == {"run": run["run_no"], "run_id": run["id"], "by": "dee"}


def test_the_same_drop_twice_changes_nothing(client, db, dispatcher_headers):
    run, _, (a, b, _c) = _depot(client, db, dispatcher_headers)
    assign(client, dispatcher_headers, run["id"], a.id)
    assign(client, dispatcher_headers, run["id"], b.id)
    again = assign(client, dispatcher_headers, run["id"], a.id, position=1)
    assert order(again.json()) == [a.id, b.id]
    kinds = [k for k, _ in _history(client, dispatcher_headers, a.id)]
    assert kinds == ["packed", "assigned"]


def test_dropping_a_stop_lower_on_its_own_run_moves_it(client, db, dispatcher_headers):
    run, _, (a, b, c) = _depot(client, db, dispatcher_headers)
    for x in (a, b, c):
        assign(client, dispatcher_headers, run["id"], x.id)
    out = assign(client, dispatcher_headers, run["id"], a.id, position=3).json()
    assert order(out) == [b.id, c.id, a.id]
    assert [k for k, _ in _history(client, dispatcher_headers, a.id)] == ["packed", "assigned"]


def test_dropping_it_on_another_run_moves_it_there(client, db, dispatcher_headers):
    one, two, (a, b, _c) = _depot(client, db, dispatcher_headers)
    assign(client, dispatcher_headers, one["id"], a.id)
    assign(client, dispatcher_headers, one["id"], b.id)
    out = assign(client, dispatcher_headers, two["id"], a.id).json()
    assert order(out) == [a.id]
    left = client.get(f"/v1/runs/{one['id']}", headers=dispatcher_headers).json()
    assert order(left) == [b.id] and left["stops"][0]["sequence"] == 1
    kinds = [(k, d["run"]) for k, d in _history(client, dispatcher_headers, a.id)[1:]]
    assert kinds == [("assigned", one["run_no"]), ("unassigned", one["run_no"]),
                     ("assigned", two["run_no"])]


def test_back_to_the_tray(client, db, dispatcher_headers):
    run, _, (a, b, c) = _depot(client, db, dispatcher_headers)
    for x in (a, b, c):
        assign(client, dispatcher_headers, run["id"], x.id)
    r = client.post(f"/v1/runs/{run['id']}/unassign", headers=dispatcher_headers,
                    json={"consignment_id": b.id})
    assert r.status_code == 200
    assert order(r.json()) == [a.id, c.id]
    assert [s["sequence"] for s in r.json()["stops"]] == [1, 2]
    tray = client.get("/v1/consignments", params={"status": "packed"},
                      headers=dispatcher_headers).json()
    assert [x["id"] for x in tray["items"]] == [b.id]
    again = client.post(f"/v1/runs/{run['id']}/unassign", headers=dispatcher_headers,
                        json={"consignment_id": b.id})
    assert again.status_code == 200 and order(again.json()) == [a.id, c.id]
    assert [k for k, _ in _history(client, dispatcher_headers, b.id)] == [
        "packed", "assigned", "unassigned"]
    # the row stays, with removed_at: cancel, never delete
    assert db.execute(select(func.count()).select_from(Stop)).scalar_one() == 3


def test_reorder_takes_the_whole_order(client, db, dispatcher_headers):
    run, _, (a, b, c) = _depot(client, db, dispatcher_headers)
    for x in (a, b, c):
        assign(client, dispatcher_headers, run["id"], x.id)
    r = client.post(f"/v1/runs/{run['id']}/reorder", headers=dispatcher_headers,
                    json={"consignment_ids": [c.id, a.id, b.id]})
    assert r.status_code == 200 and order(r.json()) == [c.id, a.id, b.id]
    same = client.post(f"/v1/runs/{run['id']}/reorder", headers=dispatcher_headers,
                       json={"consignment_ids": [c.id, a.id, b.id]})
    assert order(same.json()) == [c.id, a.id, b.id]


def test_an_order_from_before_someone_else_changed_the_run_is_refused(client, db,
                                                                      dispatcher_headers):
    run, _, (a, b, c) = _depot(client, db, dispatcher_headers)
    assign(client, dispatcher_headers, run["id"], a.id)
    assign(client, dispatcher_headers, run["id"], b.id)
    assign(client, dispatcher_headers, run["id"], c.id)     # someone else, meanwhile
    r = client.post(f"/v1/runs/{run['id']}/reorder", headers=dispatcher_headers,
                    json={"consignment_ids": [b.id, a.id]})
    assert r.status_code == 409 and r.json()["code"] == "stale"
    assert "changed while you were looking" in r.json()["detail"]
    twice = client.post(f"/v1/runs/{run['id']}/reorder", headers=dispatcher_headers,
                        json={"consignment_ids": [a.id, a.id, b.id]})
    assert twice.status_code == 409


def test_an_unknown_consignment_is_named(client, db, dispatcher_headers):
    run, _, _ = _depot(client, db, dispatcher_headers)
    r = assign(client, dispatcher_headers, run["id"], 999)
    assert r.status_code == 422
    assert r.json()["errors"] == [{"field": "consignment_id", "message": "no consignment 999"}]
    assert assign(client, dispatcher_headers, 999, 1).status_code == 404


def test_a_run_on_a_deactivated_truck_takes_no_more_stops(client, db, manager_headers):
    run, _, (a, *_) = _depot(client, db, manager_headers)
    client.post("/v1/vehicles/TMS041/deactivate", headers=manager_headers)
    r = assign(client, manager_headers, run["id"], a.id)
    assert r.status_code == 409 and r.json()["code"] == "vehicle_unavailable"


def test_a_run_with_stops_is_not_cancelled(client, db, dispatcher_headers):
    run, _, (a, *_) = _depot(client, db, dispatcher_headers)
    assign(client, dispatcher_headers, run["id"], a.id)
    r = client.post(f"/v1/runs/{run['id']}/cancel", headers=dispatcher_headers)
    assert r.status_code == 409 and r.json()["code"] == "run_not_empty"


def test_two_dispatchers_dropping_at_once_get_one_clean_order(client, db, dispatcher_headers):
    run, _, cons = _depot(client, db, dispatcher_headers, n=8)
    ids = iter([c.id for c in cons])

    def drop():
        cid = next(ids)
        with fresh_client() as c:
            return assign(c, dispatcher_headers, run["id"], cid, position=1).status_code

    assert all_at_once(8, drop) == [200] * 8
    out = client.get(f"/v1/runs/{run['id']}", headers=dispatcher_headers).json()
    assert sorted(order(out)) == sorted(c.id for c in cons)
    assert [s["sequence"] for s in out["stops"]] == list(range(1, 9))


def test_one_consignment_dropped_on_two_runs_at_once_lands_on_one(client, db,
                                                                   dispatcher_headers):
    one, two, (a, *_) = _depot(client, db, dispatcher_headers)
    targets = iter([one["id"], two["id"]] * 3)

    def drop():
        run_id = next(targets)
        with fresh_client() as c:
            return assign(c, dispatcher_headers, run_id, a.id).status_code

    assert all_at_once(6, drop) == [200] * 6
    live = db.execute(select(func.count()).select_from(Stop)
                      .where(Stop.consignment_id == a.id, Stop.removed_at.is_(None))).scalar_one()
    assert live == 1
```

**Step 2: Run them to see them fail**

Run: `.venv/bin/pytest tests/test_planning.py -q`
Expected: FAIL — `404` from `POST /v1/runs/{id}/assign`.

**Step 3: The service**

In `api/tms/services/runs.py` the imports (from `from __future__` down to `from tms.services.load …`; `PLANNING_LOCK` stays under them) become:
```python
from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import select, text
from sqlalchemy.orm import Session, selectinload

from tms.api.errors import Conflict, FieldError, NotFound
from tms.models import Consignment, ConsignmentEvent, Driver, Run, Stop, Vehicle
from tms.services.load import Load, consignment_totals, load, total
```
and append:
```python
# ---- stops -------------------------------------------------------------------

# a consignment that may still go on a run, or move to another
PLANNABLE = ("packed", "assigned")


def _now() -> datetime:
    return datetime.now(UTC)


def _live_stops(db: Session, run_id: int) -> list[Stop]:
    return list(db.execute(
        select(Stop).where(Stop.run_id == run_id, Stop.removed_at.is_(None))
        .order_by(Stop.sequence, Stop.id).execution_options(populate_existing=True)
    ).scalars())


def _renumber(stops: list[Stop]) -> None:
    for i, stop in enumerate(stops, start=1):
        stop.sequence = i


def _event(c: Consignment, kind: str, **detail) -> None:
    c.events.append(ConsignmentEvent(kind=kind, at=_now(), detail=detail))


def _plannable(db: Session, consignment_id: int) -> Consignment:
    c = db.execute(select(Consignment).where(Consignment.id == consignment_id)
                   .execution_options(populate_existing=True)).scalar_one_or_none()
    if c is None:
        raise FieldError("consignment_id", f"no consignment {consignment_id}")
    if c.status not in PLANNABLE:
        raise Conflict("not_plannable",
                       f"{c.customer} ({c.wms_ref}) has {c.status}, so it can't be planned")
    return c


def assign(db: Session, run_id: int, consignment_id: int, position: int | None,
           by: str) -> Run:
    """Put a consignment on this run at a place in the order (1 is first; none
    is last), moving it off another planned run if it was on one. Asking for
    what is already so changes nothing."""
    lock_planning(db)
    run = _planned(db, run_id)
    if not run.vehicle.active:
        raise Conflict("vehicle_unavailable",
                       f"{run.vehicle.rego} is deactivated, so {run.number} can't take stops")
    c = _plannable(db, consignment_id)
    current = db.execute(select(Stop).where(
        Stop.consignment_id == c.id, Stop.removed_at.is_(None))).scalar_one_or_none()

    if current is not None and current.run_id != run.id:
        old = _planned(db, current.run_id)          # a departed run keeps its stops
        current.removed_at = _now()
        _event(c, "unassigned", run=old.number, run_id=old.id, by=by)
        db.flush()
        _renumber(_live_stops(db, old.id))
        current = None

    stops = [s for s in _live_stops(db, run.id) if s.consignment_id != c.id]
    place = len(stops) + 1 if position is None else max(1, min(position, len(stops) + 1))
    if current is None:
        current = Stop(run_id=run.id, consignment_id=c.id, sequence=place, added_by=by)
        db.add(current)
        c.status = "assigned"
        _event(c, "assigned", run=run.number, run_id=run.id, by=by)
    stops.insert(place - 1, current)
    _renumber(stops)
    db.flush()
    return run


def unassign(db: Session, run_id: int, consignment_id: int, by: str) -> Run:
    """Back to the tray. Not on this run already: nothing to do."""
    lock_planning(db)
    run = _planned(db, run_id)
    stop = db.execute(select(Stop).where(
        Stop.run_id == run.id, Stop.consignment_id == consignment_id,
        Stop.removed_at.is_(None))).scalar_one_or_none()
    if stop is None:
        return run
    stop.removed_at = _now()
    c = db.get(Consignment, consignment_id)
    c.status = "packed"
    _event(c, "unassigned", run=run.number, run_id=run.id, by=by)
    db.flush()
    _renumber(_live_stops(db, run.id))
    db.flush()
    return run


def reorder(db: Session, run_id: int, consignment_ids: list[int]) -> Run:
    """The whole order, as the dispatcher sees it. If the run has changed
    since (someone else added or took off a stop), it is refused rather than
    guessed at: the screen reloads and they drag again."""
    lock_planning(db)
    run = _planned(db, run_id)
    stops = {s.consignment_id: s for s in _live_stops(db, run.id)}
    if len(consignment_ids) != len(set(consignment_ids)) or set(consignment_ids) != set(stops):
        raise Conflict("stale", f"{run.number} changed while you were looking; it has been "
                                "reloaded, so try that again")
    _renumber([stops[cid] for cid in consignment_ids])
    db.flush()
    return run
```

**Step 4: The routes**

Append to `api/tms/api/routes/runs.py`:
```python
class AssignIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    consignment_id: int = Field(ge=1)
    # 1 is the first stop; left out, it goes last
    position: int | None = Field(default=None, ge=1)


class UnassignIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    consignment_id: int = Field(ge=1)


class ReorderIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    consignment_ids: list[int] = Field(max_length=500)


@router.post("/runs/{id}/assign", response_model=RunOut)
def assign(id: int, body: AssignIn, db: DB, who: Principal = require("runs:write")):
    runs.assign(db, id, body.consignment_id, body.position, by=who.name)
    return _fresh(db, id)


@router.post("/runs/{id}/unassign", response_model=RunOut)
def unassign(id: int, body: UnassignIn, db: DB, who: Principal = require("runs:write")):
    runs.unassign(db, id, body.consignment_id, by=who.name)
    return _fresh(db, id)


@router.post("/runs/{id}/reorder", response_model=RunOut)
def reorder(id: int, body: ReorderIn, db: DB, who: Principal = require("runs:write")):
    runs.reorder(db, id, body.consignment_ids)
    return _fresh(db, id)
```

**Step 5: Run the tests**

Run: `.venv/bin/pytest tests/test_planning.py tests/test_runs.py -q`
Expected: `22 passed`. The two "at once" tests fail if `lock_planning` is taken out (a sequence twin, or one consignment on two runs); put it back.

**Step 6: Commit**

```bash
git add api/tms/services/runs.py api/tms/api/routes/runs.py api/tests/test_planning.py
git commit -m "Plan stops onto runs and into order, one change at a time"
```

### Task 12: Departing a run queues a ship call per consignment

**Files:**
- Modify: `api/tms/services/runs.py`
- Modify: `api/tms/api/routes/runs.py`
- Create: `api/tests/test_depart.py`

**Step 1: Write the failing tests**

`api/tests/test_depart.py`:
```python
"""A run leaves: its consignments are departed and a ship call to Simple WMS
waits in the queue for each, made once however often depart is pressed."""
import uuid

from sqlalchemy import select

from runs_samples import assign, consignment, driver, new_run, vehicle
from tms.models import OutboundCall


def _loaded_run(client, db, headers, kg="100"):
    vehicle(db)
    driver(db)
    run = new_run(client, headers)
    a = consignment(db, ref="0080034519", kg=kg)
    b = consignment(db, ref="0080034522", customer="Paddock Produce", owner="ACME")
    assign(client, headers, run["id"], a.id)
    assign(client, headers, run["id"], b.id)
    return run, a, b


def test_departing_queues_one_ship_call_per_consignment(client, db, dispatcher_headers):
    run, a, b = _loaded_run(client, db, dispatcher_headers)
    r = client.post(f"/v1/runs/{run['id']}/depart", headers=dispatcher_headers)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["status"] == "departed" and out["departed_at"] is not None
    assert [s["consignment"]["status"] for s in out["stops"]] == ["departed", "departed"]
    assert out["shipping"] == {"pending": 2, "done": 0, "failed": 0}

    calls = db.execute(select(OutboundCall).order_by(OutboundCall.id)).scalars().all()
    assert [c.path for c in calls] == ["/v1/deliveries/0080034519/ship",
                                       "/v1/deliveries/0080034522/ship"]
    first = calls[0]
    assert first.kind == "ship" and first.status == "pending" and first.attempts == 0
    assert first.body == {"message_id": str(first.message_id), "owner": "DEFAULT",
                          "carrier": "Own fleet", "tracking_no": run["run_no"],
                          "shipped_by": "dee"}
    assert calls[1].body["owner"] == "ACME"
    assert first.message_id != calls[1].message_id
    assert isinstance(first.message_id, uuid.UUID)


def test_departing_is_in_each_history(client, db, dispatcher_headers):
    run, a, _ = _loaded_run(client, db, dispatcher_headers)
    client.post(f"/v1/runs/{run['id']}/depart", headers=dispatcher_headers)
    h = client.get(f"/v1/consignments/{a.id}", headers=dispatcher_headers).json()["history"]
    assert h[-1]["kind"] == "departed"
    assert h[-1]["detail"] == {"run": run["run_no"], "run_id": run["id"], "vehicle": "TMS041",
                               "driver": "kim", "by": "dee"}


def test_departing_twice_queues_nothing_more(client, db, dispatcher_headers):
    run, *_ = _loaded_run(client, db, dispatcher_headers)
    client.post(f"/v1/runs/{run['id']}/depart", headers=dispatcher_headers)
    again = client.post(f"/v1/runs/{run['id']}/depart", headers=dispatcher_headers)
    assert again.status_code == 200 and again.json()["status"] == "departed"
    assert len(db.execute(select(OutboundCall)).scalars().all()) == 2


def test_a_departed_run_keeps_its_stops(client, db, dispatcher_headers):
    run, a, _ = _loaded_run(client, db, dispatcher_headers)
    client.post(f"/v1/runs/{run['id']}/depart", headers=dispatcher_headers)
    r = client.post(f"/v1/runs/{run['id']}/unassign", headers=dispatcher_headers,
                    json={"consignment_id": a.id})
    assert r.status_code == 409 and r.json()["code"] == "run_not_planned"
    vehicle(db, rego="TMS018")
    other = new_run(client, dispatcher_headers, rego="TMS018")
    moved = assign(client, dispatcher_headers, other["id"], a.id)
    assert moved.status_code == 409 and moved.json()["code"] == "not_plannable"


def test_an_overloaded_run_does_not_leave(client, db, dispatcher_headers):
    run, *_ = _loaded_run(client, db, dispatcher_headers, kg="1150")
    r = client.post(f"/v1/runs/{run['id']}/depart", headers=dispatcher_headers)
    assert r.status_code == 409 and r.json()["code"] == "over_capacity"
    assert r.json()["detail"] == (f"{run['run_no']}: 50 kg too heavy. Move a stop to another "
                                  "run before printing the run sheet.")
    assert db.execute(select(OutboundCall)).first() is None


def test_an_empty_run_does_not_leave(client, db, dispatcher_headers):
    vehicle(db)
    driver(db)
    run = new_run(client, dispatcher_headers)
    r = client.post(f"/v1/runs/{run['id']}/depart", headers=dispatcher_headers)
    assert r.status_code == 409 and r.json()["code"] == "run_empty"
```

**Step 2: Run them to see them fail**

Run: `.venv/bin/pytest tests/test_depart.py -q`
Expected: FAIL — `404` from `POST /v1/runs/{id}/depart`.

**Step 3: The service**

In `api/tms/services/runs.py` the imports (from `from __future__` down to `from tms.services.load …`; `PLANNING_LOCK` stays under them) become:
```python
from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from urllib.parse import quote

from sqlalchemy import select, text
from sqlalchemy.orm import Session, selectinload

from tms.api.errors import Conflict, FieldError, NotFound
from tms.models import (
    Consignment, ConsignmentEvent, Driver, OutboundCall, Run, Stop, Vehicle,
)
from tms.services.load import Load, consignment_totals, load, total
```
and append:
```python
# ---- leaving -----------------------------------------------------------------

# what Simple WMS records as the carrier when our own truck takes it
OWN_FLEET = "Own fleet"


def depart(db: Session, run_id: int, by: str) -> Run:
    """The truck has left: each consignment is departed, and a ship call to
    Simple WMS waits in the queue for each, in the same transaction, so none
    is lost and none is made twice. A run that has already left is left as
    it is."""
    lock_planning(db)
    run = get_run(db, run_id)
    if run.status == "departed":
        return run
    run = _planned(db, run_id)
    if not run.stops:
        raise Conflict("run_empty", f"{run.number} has no stops")
    if not run.vehicle.active:
        raise Conflict("vehicle_unavailable", f"{run.vehicle.rego} is deactivated")
    over = run_load(run)
    if over.state == "over":
        raise Conflict("over_capacity", f"{run.number}: {over.message}")
    now = _now()
    for stop in run.stops:
        c = stop.consignment
        c.status = "departed"
        _event(c, "departed", run=run.number, run_id=run.id, vehicle=run.vehicle.rego,
               driver=run.driver.code, by=by)
        message_id = uuid.uuid4()
        db.add(OutboundCall(
            message_id=message_id, kind="ship", run_id=run.id, consignment_id=c.id,
            path=f"/v1/deliveries/{quote(c.wms_ref, safe='')}/ship",
            body={"message_id": str(message_id), "owner": c.owner, "carrier": OWN_FLEET,
                  "tracking_no": run.number, "shipped_by": by[:64]},
            status="pending", attempts=0, next_attempt_at=now))
    run.status = "departed"
    run.departed_at = now
    db.flush()
    return run
```

**Step 4: The route**

Append to `api/tms/api/routes/runs.py`:
```python
@router.post("/runs/{id}/depart", response_model=RunOut)
def depart(id: int, db: DB, who: Principal = require("runs:write")):
    runs.depart(db, id, by=who.name)
    return _fresh(db, id)
```

**Step 5: Run the tests**

Run: `.venv/bin/pytest tests/test_depart.py tests/test_planning.py tests/test_runs.py -q`
Expected: `28 passed`.

**Step 6: Commit**

```bash
git add api/tms/services/runs.py api/tms/api/routes/runs.py api/tests/test_depart.py
git commit -m "Depart a run and queue the ship call to Simple WMS for each consignment"
```

### Task 13: The worker makes the ship calls, with Simple WMS's backoff

**Files:**
- Modify: `api/tms/services/wms_client.py` (docstring, `ship`)
- Create: `api/tms/worker.py`
- Modify: `api/tms/api/routes/runs.py` (`retry-ship`)
- Create: `api/tests/fake_http.py`, `api/tests/test_ship_calls.py`

**Step 1: Write the failing tests**

`api/tests/fake_http.py`:
```python
"""An httpx client whose answers the test queues up, and which keeps every
request it was sent. For the worker's calls to Simple WMS and Platen."""
import json

import httpx


class FakeHttp:
    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.answers: list = []     # (status, body) or an exception; empty means 202 {}
        self.client = httpx.Client(transport=httpx.MockTransport(self._answer))

    def _answer(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        answer = self.answers.pop(0) if self.answers else (202, {})
        if isinstance(answer, Exception):
            raise answer
        status, body = answer
        return httpx.Response(status, json=body)

    def bodies(self) -> list[dict]:
        return [json.loads(r.content) for r in self.requests]
```

`api/tests/test_ship_calls.py`:
```python
"""The worker makes each ship call to Simple WMS, retrying after 1 min, 5, 30
and 2 h; the same message_id goes every time, so Simple WMS ships it once."""
import logging
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from fake_http import FakeHttp
from runs_samples import assign, consignment, driver, new_run, vehicle
from tms import worker
from tms.models import OutboundCall

T0 = datetime(2026, 10, 14, 7, 0, tzinfo=UTC)


@pytest.fixture
def http(monkeypatch):
    from tms.config import get_settings
    monkeypatch.setattr(get_settings(), "wms_url", "http://wms.test/")
    fake = FakeHttp()
    yield fake
    fake.client.close()


@pytest.fixture
def departed(client, db, dispatcher_headers):
    vehicle(db)
    driver(db)
    run = new_run(client, dispatcher_headers)
    c = consignment(db, ref="0080034519")
    assign(client, dispatcher_headers, run["id"], c.id)
    client.post(f"/v1/runs/{run['id']}/depart", headers=dispatcher_headers)
    db.execute(OutboundCall.__table__.update().values(next_attempt_at=T0))
    db.commit()
    return run


def _call(db) -> OutboundCall:
    db.expire_all()
    return db.execute(select(OutboundCall)).scalar_one()


def test_the_ship_call_goes_with_our_key_and_its_message_id(db, http, departed):
    assert worker.send_ship_calls(db, http.client, now=T0) == 1
    sent = http.requests[0]
    assert str(sent.url) == "http://wms.test/v1/deliveries/0080034519/ship"
    assert sent.headers["Authorization"] == "Bearer wms_test-key-for-the-tms"
    row = _call(db)
    assert http.bodies()[0] == {"message_id": str(row.message_id), "owner": "DEFAULT",
                                "carrier": "Own fleet", "tracking_no": departed["run_no"],
                                "shipped_by": "dee"}
    assert row.status == "done" and row.attempts == 1 and row.done_at == T0


def test_a_failed_try_waits_1_5_30_then_120_minutes_and_the_next_failure_gives_up(
        db, http, departed):
    now = T0
    for attempt, wait in enumerate((1, 5, 30, 120), start=1):
        http.answers.append((503, {"detail": "down"}))
        assert worker.send_ship_calls(db, http.client, now=now) == 1
        row = _call(db)
        assert (row.status, row.attempts) == ("pending", attempt)
        assert row.next_attempt_at == now + timedelta(minutes=wait)
        assert row.last_error.startswith("HTTP 503")
        # not due yet: nothing is tried
        assert worker.send_ship_calls(db, http.client, now=now + timedelta(seconds=30)) == 0
        now = row.next_attempt_at
    http.answers.append((503, {"detail": "still down"}))
    worker.send_ship_calls(db, http.client, now=now)
    row = _call(db)
    assert (row.status, row.attempts) == ("failed", 5)
    assert len({b["message_id"] for b in http.bodies()}) == 1     # the same every time


def test_a_network_error_is_retried_like_a_refusal(db, http, departed):
    http.answers.append(httpx.ConnectError("connection refused"))
    worker.send_ship_calls(db, http.client, now=T0)
    row = _call(db)
    assert row.status == "pending" and row.last_error.startswith("ConnectError")


def test_already_shipped_in_simple_wms_counts_as_done(db, http, departed):
    http.answers.append((409, {"detail": "0080034519 has already shipped",
                               "code": "already_shipped"}))
    worker.send_ship_calls(db, http.client, now=T0)
    assert _call(db).status == "done"


def test_a_key_without_the_scope_is_logged_as_an_error_and_retried(db, http, departed, caplog):
    http.answers.append((403, {"detail": "you do not have the tasks:write scope"}))
    with caplog.at_level(logging.ERROR, logger="tms.wms"):
        worker.send_ship_calls(db, http.client, now=T0)
    assert "tasks:write" in caplog.text
    assert "wms_test-key-for-the-tms" not in caplog.text
    assert _call(db).status == "pending"


def test_without_simple_wms_set_up_the_calls_wait_untried(db, http, departed, monkeypatch):
    from tms.config import get_settings
    monkeypatch.setattr(get_settings(), "wms_url", "")
    assert worker.send_ship_calls(db, http.client, now=T0) == 0
    assert http.requests == [] and _call(db).attempts == 0


def test_failed_calls_are_tried_again_on_request(client, db, http, departed,
                                                 dispatcher_headers):
    row = _call(db)
    row.status, row.attempts, row.last_error = "failed", 5, "HTTP 503: down"
    db.commit()
    r = client.post(f"/v1/runs/{departed['id']}/retry-ship", headers=dispatcher_headers)
    assert r.status_code == 200 and r.json()["shipping"] == {"pending": 1, "done": 0, "failed": 0}
    row = _call(db)
    assert row.attempts == 0 and row.status == "pending"
    worker.send_ship_calls(db, http.client, now=datetime.now(UTC) + timedelta(seconds=1))
    assert _call(db).status == "done"
    assert http.bodies()[0]["message_id"] == str(row.message_id)
```

**Step 2: Run them to see them fail**

Run: `.venv/bin/pytest tests/test_ship_calls.py -q`
Expected: FAIL — `ImportError: cannot import name 'worker' from 'tms'`.

**Step 3: The call**

In `api/tms/services/wms_client.py` the module docstring becomes:
```python
"""The calls Simple TMS makes to Simple WMS: read a delivery when it is packed,
and say it shipped when its run departs."""
```
and append:
```python
# A ship call refused like this will not mend itself either; it is retried
# anyway, so it goes through once someone fixes the key, and logged as an error.
_SHIP_REFUSALS = {
    401: "Simple WMS does not know our API key: check TMS_WMS_API_KEY is a live "
         "Simple WMS key",
    403: "Simple WMS will not let our API key ship this delivery: the key needs the "
         "tasks:write scope, and its warehouses and owner must cover the delivery",
    404: "Simple WMS has no such delivery for that owner",
}


def ship(http: httpx.Client, path: str, body: dict) -> str | None:
    """Make one ship call: POST {TMS_WMS_URL}{path} with our key. None when
    Simple WMS took it, or says the delivery had already shipped (a retry
    after a reply that was lost, once its message_id window has passed);
    otherwise why not, for last_error. Never the key in what it returns."""
    settings = get_settings()
    url = settings.wms_url.rstrip("/") + path
    try:
        resp = http.post(url, json=body,
                         headers={"Authorization": f"Bearer {settings.wms_api_key}",
                                  "User-Agent": "simple-tms/0.1"},
                         timeout=settings.worker_http_timeout_seconds)
    except httpx.HTTPError as exc:
        return f"{type(exc).__name__}: {exc}"[:500]
    if 200 <= resp.status_code < 300:
        return None
    try:
        code = resp.json().get("code")
    except (ValueError, AttributeError):
        code = None
    if resp.status_code == 409 and code == "already_shipped":
        log.info("%s: Simple WMS says it had already shipped; taken as done", path)
        return None
    if resp.status_code in _SHIP_REFUSALS:
        log.error("HTTP %s on %s. %s", resp.status_code, path, _SHIP_REFUSALS[resp.status_code])
    return f"HTTP {resp.status_code}: {resp.text[:200]}"
```

**Step 4: The worker**

`api/tms/worker.py`:
```python
"""Drains the queues: ship calls to Simple WMS and print jobs to Platen. Run
as `python -m tms.worker`.

A failed try is tried again after 1 min, 5, 30 and 2 h. The try after the
2 h wait is the last: if it fails too, the row is `failed` and waits for a
person (retry-ship on the run, or print again).
"""
from __future__ import annotations

import logging
import time
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from tms.config import get_settings
from tms.db import get_sessionmaker
from tms.models import OutboundCall
from tms.services import wms_client

log = logging.getLogger("tms.worker")

BACKOFF = (60, 300, 1800, 7200)


def retry_later(row, now: datetime, error: str) -> None:
    """After a failed try: wait the next step of the backoff, or give up once
    the try after the last wait has failed. attempts has counted this try."""
    row.last_error = error[:500]
    if row.attempts > len(BACKOFF):
        row.status = "failed"
    else:
        row.next_attempt_at = now + timedelta(seconds=BACKOFF[row.attempts - 1])


def _claim(session: Session, model, now: datetime):
    """The next row due, locked so a second worker passes it by. One row per
    transaction, so a slow answer holds up only that row."""
    return session.execute(
        select(model)
        .where(model.status == "pending", model.next_attempt_at <= now)
        .order_by(model.next_attempt_at, model.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    ).scalar_one_or_none()


def send_ship_calls(session: Session, http: httpx.Client, now: datetime | None = None,
                    limit: int = 50) -> int:
    """Make every ship call that is due. Returns how many were tried."""
    settings = get_settings()
    if not settings.wms_url or not settings.wms_api_key:
        return 0    # they wait, untried, until Simple WMS is set up
    now = now or datetime.now(UTC)
    tried = 0
    while tried < limit and (row := _claim(session, OutboundCall, now)) is not None:
        error = wms_client.ship(http, row.path, row.body)
        row.attempts += 1
        tried += 1
        if error is None:
            row.status = "done"
            row.done_at = now
            row.last_error = None
            log.info("ship call %s (%s) done", row.message_id, row.path)
        else:
            retry_later(row, now, error)
            log.warning("ship call %s (%s) try %d failed%s: %s", row.message_id, row.path,
                        row.attempts, "; given up" if row.status == "failed" else "", error)
        session.commit()
    return tried


def wait_for_schema(session: Session, timeout: float = 120, poll: float = 2) -> None:
    """Block until the api container has run the migrations."""
    from sqlalchemy.exc import ProgrammingError

    deadline = time.monotonic() + timeout
    while True:
        try:
            session.execute(select(OutboundCall.id).limit(1))
            return
        except ProgrammingError:
            session.rollback()
            if time.monotonic() >= deadline:
                raise TimeoutError("outbound_call never appeared; did the migrations run?")
            log.info("waiting for migrations")
            time.sleep(poll)


def run_once(session: Session, http: httpx.Client) -> int:
    return send_ship_calls(session, http)


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = get_settings()
    make_session = get_sessionmaker()
    with make_session() as session:
        wait_for_schema(session)
    log.info("worker started, polling every %ss", settings.worker_poll_seconds)
    with httpx.Client() as http:
        while True:
            try:
                with make_session() as session:
                    done = run_once(session, http)
            except Exception:  # keep the loop alive; log and back off
                log.exception("worker loop error")
                done = 0
            if done == 0:
                time.sleep(settings.worker_poll_seconds)


if __name__ == "__main__":
    main()
```

**Step 5: Retry on request**

In `api/tms/api/routes/runs.py` the sqlalchemy import becomes `from sqlalchemy import func, select, update`; append:
```python
@router.post("/runs/{id}/retry-ship", response_model=RunOut)
def retry_ship(id: int, db: DB, who: Principal = require("runs:write")):
    """Ship calls that gave up are tried again from the start, with the same
    message_id each, so Simple WMS still ships each delivery once."""
    runs.get_run(db, id)
    db.execute(update(OutboundCall)
               .where(OutboundCall.run_id == id, OutboundCall.status == "failed")
               .values(status="pending", attempts=0, next_attempt_at=func.now()))
    return _fresh(db, id)
```

**Step 6: Run the tests**

Run: `.venv/bin/pytest tests/test_ship_calls.py -q`
Expected: `7 passed`.

**Step 7: Commit**

```bash
git add api/tms/services/wms_client.py api/tms/worker.py api/tms/api/routes/runs.py api/tests/fake_http.py api/tests/test_ship_calls.py
git commit -m "Make the ship calls to Simple WMS from a worker, with backoff" -m "1 min, 5, 30, 2 h, then failed; the same message_id every try."
```

### Task 14: An API key, for Platen to report with

**Files:**
- Create: `api/tms/services/api_keys.py`
- Modify: `api/tms/api/deps.py` (`Principal`, `get_principal`)
- Modify: `api/tms/cli.py` (`create-api-key`, `revoke-api-key`)
- Create: `api/tests/test_api_keys.py`

**Step 1: Write the failing tests**

`api/tests/test_api_keys.py`:
```python
"""A key for a system: Platen saying a run sheet printed."""
from conftest import bearer
from tms.cli import main


def _key(capsys, name="platen", scopes="printing:write") -> str:
    assert main(["create-api-key", "--name", name, "--scopes", scopes]) == 0
    out = capsys.readouterr().out
    assert f"api key {name}" in out and "shown once" in out
    return out.strip().splitlines()[-1]


def test_a_key_is_shown_once_and_says_who_it_is(client, db, capsys):
    raw = _key(capsys)
    assert raw.startswith("tms_k.")
    me = client.get("/v1/auth/me", headers=bearer(raw)).json()
    assert me == {"kind": "key", "id": me["id"], "name": "platen", "display_name": "platen",
                  "role": "key", "scopes": ["printing:write"]}


def test_a_key_does_only_what_its_scopes_say(client, db, capsys):
    raw = _key(capsys)
    assert client.get("/v1/runs", params={"date": "2026-10-14"},
                      headers=bearer(raw)).status_code == 403


def test_a_revoked_or_made_up_key_is_refused(client, db, capsys):
    raw = _key(capsys)
    assert main(["revoke-api-key", "--name", "platen"]) == 0
    assert client.get("/v1/auth/me", headers=bearer(raw)).status_code == 401
    assert client.get("/v1/auth/me", headers=bearer("tms_k.made-up")).status_code == 401


def test_a_key_needs_known_scopes_and_its_own_name(db, capsys):
    assert main(["create-api-key", "--name", "x", "--scopes", "everything"]) == 1
    assert "scopes must be some of" in capsys.readouterr().err
    _key(capsys)
    assert main(["create-api-key", "--name", "platen", "--scopes", "printing:write"]) == 1
    assert "already exists" in capsys.readouterr().err
```

**Step 2: Run them to see them fail**

Run: `.venv/bin/pytest tests/test_api_keys.py -q`
Expected: FAIL — `argparse` exits: `invalid choice: 'create-api-key'`.

**Step 3: Implement**

`api/tms/services/api_keys.py`:
```python
"""Keys for systems, not people. The key is shown once; only its SHA-256 is
kept, which is enough for 256 random bits: there is nothing to guess back."""
from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from tms.models import ApiKey

PREFIX = "tms_k."
# what a key may be given; a person's role gives the rest
SCOPES = ("printing:write", "runs:read", "runs:write", "consignments:read", "fleet:read")


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def create(db: Session, name: str, scopes: list[str]) -> tuple[ApiKey, str]:
    unknown = [s for s in scopes if s not in SCOPES]
    if unknown or not scopes:
        raise ValueError(f"scopes must be some of {', '.join(SCOPES)}")
    raw = PREFIX + secrets.token_urlsafe(32)
    key = ApiKey(name=name, key_hash=_hash(raw), scopes=list(scopes), active=True)
    db.add(key)
    db.flush()
    return key, raw


def find(db: Session, raw: str) -> ApiKey | None:
    """The live key this is, noting that it was used."""
    key = db.execute(select(ApiKey).where(ApiKey.key_hash == _hash(raw))).scalar_one_or_none()
    if key is None or not key.active:
        return None
    key.last_used_at = datetime.now(UTC)
    db.commit()
    return key
```

In `api/tms/api/deps.py`: import `api_keys` beside `sessions` (`from tms.services import api_keys, sessions`); `Principal`'s docstring and `kind` become:
```python
    """Whoever is calling: a desktop user or a driver with a session, or a
    system with an API key."""

    kind: str  # user | driver | key
```
and before the last line of `get_principal` (`raise Unauthorised("not a Simple TMS token")`):
```python
    if raw.startswith(api_keys.PREFIX):
        key = api_keys.find(db, raw)
        if key is None:
            raise Unauthorised("unknown or revoked API key")
        return Principal(kind="key", id=key.id, name=key.name, display_name=key.name,
                         role="key", scopes=list(key.scopes))
```

In `api/tms/cli.py` the imports become:
```python
from tms.db import get_sessionmaker
from tms.models import ApiKey, User
from tms.services import access, api_keys, sessions
```
add before `def main`:
```python
def cmd_create_api_key(args) -> int:
    scopes = [x.strip() for x in args.scopes.split(",") if x.strip()]
    with get_sessionmaker()() as db:
        if db.execute(select(ApiKey).where(ApiKey.name == args.name)).scalar_one_or_none():
            print(f"create-api-key: {args.name} already exists", file=sys.stderr)
            return 1
        try:
            key, raw = api_keys.create(db, args.name, scopes)
        except ValueError as exc:
            print(f"create-api-key: {exc}", file=sys.stderr)
            return 1
        db.commit()
        print(f"api key {key.name} (id {key.id}) created with {', '.join(key.scopes)}")
        print("This key is shown once and never stored:")
        print(raw)
    return 0


def cmd_revoke_api_key(args) -> int:
    with get_sessionmaker()() as db:
        key = db.execute(select(ApiKey).where(ApiKey.name == args.name)).scalar_one_or_none()
        if key is None:
            print(f"revoke-api-key: no key {args.name}", file=sys.stderr)
            return 1
        key.active = False
        db.commit()
        print(f"api key {key.name} revoked")
    return 0
```
and in `main`, after the `create-user` parser:
```python
    p = sub.add_parser("create-api-key", help="create a key for a system (printed once)")
    p.add_argument("--name", required=True)
    p.add_argument("--scopes", required=True,
                   help=f"comma separated, some of {','.join(api_keys.SCOPES)}")
    p.set_defaults(func=cmd_create_api_key)

    p = sub.add_parser("revoke-api-key", help="stop a key working (it is kept, not deleted)")
    p.add_argument("--name", required=True)
    p.set_defaults(func=cmd_revoke_api_key)
```

**Step 4: Run the tests**

Run: `.venv/bin/pytest tests/test_api_keys.py tests/test_cli.py tests/test_auth.py -q`
Expected: all pass.

**Step 5: Commit**

```bash
git add api/tms/services/api_keys.py api/tms/api/deps.py api/tms/cli.py api/tests/test_api_keys.py
git commit -m "Add API keys for systems, made and revoked with the tms command"
```

### Task 15: Run sheets through Platen, and Platen saying how they went

**Files:**
- Create: `api/tms/services/printing.py`
- Create: `api/tms/api/routes/printing.py`
- Modify: `api/tms/api/app.py`
- Modify: `api/tms/worker.py` (`send_print_jobs`)
- Create: `api/tests/test_printing.py`

**Step 1: Write the failing tests**

`api/tests/test_printing.py`:
```python
"""Run sheets: queued as a template name and JSON, sent to Platen by the
worker, and Platen says how each one went."""
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from conftest import bearer
from fake_http import FakeHttp
from runs_samples import assign, consignment, driver, new_run, vehicle
from tms import worker
from tms.cli import main
from tms.models import PrintJob

T0 = datetime(2026, 10, 14, 6, 30, tzinfo=UTC)


@pytest.fixture
def platen(monkeypatch):
    from tms.config import get_settings
    s = get_settings()
    monkeypatch.setattr(s, "platen_url", "http://platen.test/intake/jobs")
    monkeypatch.setattr(s, "platen_key", "plt_test-key")
    monkeypatch.setattr(s, "run_sheet_printer", "Office")
    fake = FakeHttp()
    yield fake
    fake.client.close()


@pytest.fixture
def run(client, db, dispatcher_headers):
    vehicle(db)
    driver(db)
    run = new_run(client, dispatcher_headers)
    a = consignment(db, ref="0080034519", customer="Station Deli", suburb="Ballan")
    b = consignment(db, ref="0080034522", customer="Paddock Produce", suburb="Clunes", kg="210")
    assign(client, dispatcher_headers, run["id"], a.id)
    assign(client, dispatcher_headers, run["id"], b.id)
    return run


def _print(client, headers, run_id):
    return client.post(f"/v1/runs/{run_id}/print", headers=headers)


def _job(db) -> PrintJob:
    db.expire_all()
    return db.execute(select(PrintJob)).scalar_one()


def test_printing_queues_the_run_sheet(client, db, platen, run, dispatcher_headers):
    r = _print(client, dispatcher_headers, run["id"])
    assert r.status_code == 202, r.text
    assert r.json()["status"] == "pending" and r.json()["printer"] == "Office"
    job = _job(db)
    assert (job.template, job.version, job.copies) == ("run-sheet", "v1", 1)
    assert job.reference == {"type": "run", "ref": run["run_no"]}
    assert job.data == {
        "run_no": run["run_no"], "date": "2026-10-14", "vehicle": "TMS041",
        "vehicle_type": "van", "driver": "Kim", "driver_code": "kim", "stop_count": 2,
        "total_kg": "310", "total_m3": "1", "max_kg": "1,200", "max_m3": "9",
        "lines": [
            {"stop": 1, "customer": "Station Deli", "wms_ref": "0080034519",
             "address": "1 Main St", "suburb": "Ballan", "postcode": "3342", "phone": "",
             "packages": 1, "weight_kg": "100", "required_by": ""},
            {"stop": 2, "customer": "Paddock Produce", "wms_ref": "0080034522",
             "address": "1 Main St", "suburb": "Clunes", "postcode": "3342", "phone": "",
             "packages": 1, "weight_kg": "210", "required_by": ""},
        ]}
    sheet = client.get(f"/v1/runs/{run['id']}", headers=dispatcher_headers).json()["run_sheet"]
    assert sheet["status"] == "pending" and sheet["job_id"] == str(job.job_id)


def test_the_worker_hands_it_to_platen(client, db, platen, run, dispatcher_headers):
    _print(client, dispatcher_headers, run["id"])
    db.execute(PrintJob.__table__.update().values(next_attempt_at=T0))
    db.commit()
    assert worker.send_print_jobs(db, platen.client, now=T0) == 1
    sent = platen.requests[0]
    assert str(sent.url) == "http://platen.test/intake/jobs"
    assert sent.headers["Authorization"] == "Bearer plt_test-key"
    job = _job(db)
    assert sent.headers["X-TMS-Job-Id"] == str(job.job_id)
    assert platen.bodies()[0] == {"job_id": str(job.job_id), "template": "run-sheet",
                                  "version": "v1", "printer": "Office", "copies": 1,
                                  "reference": {"type": "run", "ref": run["run_no"]},
                                  "data": job.data}
    assert job.status == "accepted" and job.sent_at == T0


def test_platens_refusal_is_kept_in_its_words_and_retried(client, db, platen, run,
                                                          dispatcher_headers):
    _print(client, dispatcher_headers, run["id"])
    db.execute(PrintJob.__table__.update().values(next_attempt_at=T0))
    db.commit()
    platen.answers.append((422, {"detail": "template: Platen has no template 'run-sheet'; "
                                           "make one with that id and publish it"}))
    worker.send_print_jobs(db, platen.client, now=T0)
    job = _job(db)
    assert job.status == "pending" and job.next_attempt_at == T0 + timedelta(minutes=1)
    assert job.last_error == ("Platen answered 422: template: Platen has no template "
                              "'run-sheet'; make one with that id and publish it")


def test_without_platen_set_up_printing_says_so(client, db, run, dispatcher_headers):
    r = _print(client, dispatcher_headers, run["id"])
    assert r.status_code == 409 and r.json()["code"] == "printing_not_set_up"
    assert "TMS_PLATEN_URL" in r.json()["detail"]


def test_an_overloaded_run_does_not_print(client, db, platen, dispatcher_headers, run):
    heavy = consignment(db, ref="0080034599", kg="1000")
    assign(client, dispatcher_headers, run["id"], heavy.id)
    r = _print(client, dispatcher_headers, run["id"])
    assert r.status_code == 409 and r.json()["code"] == "over_capacity"
    assert "110 kg too heavy" in r.json()["detail"]
    assert db.execute(select(PrintJob)).first() is None


def _platen_key(capsys) -> dict:
    assert main(["create-api-key", "--name", "platen", "--scopes", "printing:write"]) == 0
    return bearer(capsys.readouterr().out.strip().splitlines()[-1])


def test_platen_says_it_printed(client, db, platen, run, dispatcher_headers, capsys):
    key = _platen_key(capsys)
    job_id = _print(client, dispatcher_headers, run["id"]).json()["job_id"]
    r = client.post(f"/v1/print-jobs/{job_id}/status", headers=key, json={"status": "printed"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "printed" and r.json()["printed_at"] is not None
    # a late "accepted" does not undo it
    late = client.post(f"/v1/print-jobs/{job_id}/status", headers=key,
                       json={"status": "accepted"})
    assert late.json()["status"] == "printed"


def test_platen_says_it_failed_and_why(client, db, platen, run, dispatcher_headers, capsys):
    job_id = _print(client, dispatcher_headers, run["id"]).json()["job_id"]
    r = client.post(f"/v1/print-jobs/{job_id}/status", headers=_platen_key(capsys),
                    json={"status": "failed", "message": "Office: out of paper"})
    assert r.json()["status"] == "failed" and r.json()["last_error"] == "Office: out of paper"
    sheet = client.get(f"/v1/runs/{run['id']}", headers=dispatcher_headers).json()["run_sheet"]
    assert sheet["status"] == "failed" and sheet["last_error"] == "Office: out of paper"


def test_only_a_key_with_printing_write_reports(client, db, platen, run, dispatcher_headers):
    job_id = _print(client, dispatcher_headers, run["id"]).json()["job_id"]
    r = client.post(f"/v1/print-jobs/{job_id}/status", headers=dispatcher_headers,
                    json={"status": "printed"})
    assert r.status_code == 403
    assert client.post(f"/v1/print-jobs/{job_id}/status",
                       json={"status": "printed"}).status_code == 401


def test_an_unknown_job_is_404_so_platen_stops_asking(client, db, capsys):
    r = client.post(f"/v1/print-jobs/{uuid.uuid4()}/status", headers=_platen_key(capsys),
                    json={"status": "printed"})
    assert r.status_code == 404
```

**Step 2: Run them to see them fail**

Run: `.venv/bin/pytest tests/test_printing.py -q`
Expected: FAIL — `404` from `POST /v1/runs/{id}/print`.

**Step 3: The run sheet**

`api/tms/services/printing.py`:
```python
"""Run sheets through Platen. Simple TMS renders nothing: it queues a
template name and JSON, the worker sends it, Platen prints it and says how
it went."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import httpx
from sqlalchemy.orm import Session

from tms.api.errors import Conflict
from tms.config import get_settings
from tms.models import PrintJob, Run
from tms.services.load import consignment_totals, plain
from tms.services.runs import get_run, run_load

# The Platen page template that prints it: make one with this id and publish it.
RUN_SHEET = "run-sheet"
RUN_SHEET_VERSION = "v1"


def run_sheet_data(run: Run) -> dict:
    """What the run-sheet template binds to: the run at the top, one line per
    stop (Platen gives a page template a row per entry in `lines`, with the
    rest beside it). Text only, never null, so every binding has something."""
    ld = run_load(run)
    lines = []
    for stop in run.stops:
        c = stop.consignment
        to = c.ship_to or {}
        t = consignment_totals(c.packages)
        lines.append({
            "stop": stop.sequence, "customer": c.customer, "wms_ref": c.wms_ref,
            "address": to.get("address") or "", "suburb": to.get("suburb") or "",
            "postcode": to.get("postcode") or "", "phone": to.get("phone") or "",
            "packages": t.packages, "weight_kg": plain(t.weight_kg),
            "required_by": c.required_by.isoformat() if c.required_by else "",
        })
    return {
        "run_no": run.number, "date": run.day.isoformat(), "vehicle": run.vehicle.rego,
        "vehicle_type": run.vehicle.type, "driver": run.driver.name,
        "driver_code": run.driver.code, "stop_count": len(run.stops),
        "total_kg": plain(ld.weight_kg), "total_m3": plain(ld.volume_m3),
        "max_kg": plain(ld.max_kg), "max_m3": plain(ld.max_m3), "lines": lines,
    }


def queue_run_sheet(db: Session, run_id: int, by: str) -> PrintJob:
    settings = get_settings()
    if not settings.platen_url:
        raise Conflict("printing_not_set_up",
                       "Printing is not set up: TMS_PLATEN_URL is empty. Ask whoever looks "
                       "after Simple TMS to connect Platen.")
    run = get_run(db, run_id)
    if run.status == "cancelled":
        raise Conflict("run_cancelled", f"{run.number} was cancelled")
    if not run.stops:
        raise Conflict("run_empty", f"{run.number} has no stops to print")
    ld = run_load(run)
    if ld.state == "over":
        raise Conflict("over_capacity", f"{run.number}: {ld.message}")
    job = PrintJob(job_id=uuid.uuid4(), run_id=run.id, template=RUN_SHEET,
                   version=RUN_SHEET_VERSION, printer=settings.run_sheet_printer, copies=1,
                   reference={"type": "run", "ref": run.number}, data=run_sheet_data(run),
                   requested_by=by, status="pending", attempts=0,
                   next_attempt_at=datetime.now(UTC))
    db.add(job)
    db.flush()
    return job


def send(http: httpx.Client, job: PrintJob) -> str | None:
    """Hand one job to Platen's intake. None when it took it; otherwise why
    not, in Platen's own words where it gave some. Never the key."""
    settings = get_settings()
    headers = {"X-TMS-Job-Id": str(job.job_id), "User-Agent": "simple-tms/0.1"}
    if settings.platen_key:
        headers["Authorization"] = f"Bearer {settings.platen_key}"
    body = {"job_id": str(job.job_id), "template": job.template, "version": job.version,
            "printer": job.printer, "copies": job.copies, "reference": job.reference,
            "data": job.data}
    try:
        resp = http.post(settings.platen_url, json=body, headers=headers,
                         timeout=settings.worker_http_timeout_seconds)
    except httpx.HTTPError as exc:
        return f"{type(exc).__name__}: {exc}"[:500]
    if 200 <= resp.status_code < 300:
        return None
    try:
        detail = resp.json().get("detail")
    except (ValueError, AttributeError):
        detail = None
    said = detail if isinstance(detail, str) else resp.text[:200]
    return f"Platen answered {resp.status_code}: {said}"[:500]
```
The data is text throughout (decimals as people write them: `"1,200"`), never `null`, so every binding in the Platen template finds something.

**Step 4: The routes**

`api/tms/api/routes/printing.py`:
```python
"""Run sheets, and Platen telling us how each one went."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from tms.api.deps import DB, Principal, require
from tms.api.errors import NotFound
from tms.models import PrintJob
from tms.services import printing

router = APIRouter(tags=["printing"])


class PrintJobOut(BaseModel):
    job_id: uuid.UUID
    run_id: int
    template: str
    printer: str
    status: str
    attempts: int
    last_error: str | None
    created_at: datetime
    sent_at: datetime | None
    printed_at: datetime | None


class JobStatusIn(BaseModel):
    """What Platen sends, the same body Simple WMS takes."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    status: Literal["accepted", "printed", "failed"]
    message: str | None = Field(default=None, max_length=500)
    printer: str | None = Field(default=None, max_length=64)


def job_out(job: PrintJob) -> PrintJobOut:
    return PrintJobOut(job_id=job.job_id, run_id=job.run_id, template=job.template,
                       printer=job.printer, status=job.status, attempts=job.attempts,
                       last_error=job.last_error, created_at=job.created_at,
                       sent_at=job.sent_at, printed_at=job.printed_at)


@router.post("/runs/{id}/print", status_code=202, response_model=PrintJobOut)
def print_run_sheet(id: int, db: DB, who: Principal = require("runs:write")):
    """Queue the run sheet for Platen. Each call is a new print."""
    job = printing.queue_run_sheet(db, id, by=who.name)
    db.commit()
    return job_out(job)


@router.post("/print-jobs/{job_id}/status", response_model=PrintJobOut)
def job_status(job_id: uuid.UUID, body: JobStatusIn, db: DB,
               who: Principal = require("printing:write")):
    """Platen says how a job went, with a key that has printing:write. A late
    `accepted` does not undo `printed` or `failed`."""
    job = db.execute(select(PrintJob).where(PrintJob.job_id == job_id)
                     .with_for_update()).scalar_one_or_none()
    if job is None:
        raise NotFound(f"no print job {job_id}")
    if not (body.status == "accepted" and job.status in ("printed", "failed")):
        job.status = body.status
    job.printer = body.printer or job.printer
    if body.status == "printed":
        job.printed_at = datetime.now(UTC)
        job.last_error = None
    elif body.status == "failed":
        job.last_error = body.message or "Platen said it failed"
    db.commit()
    return job_out(job)
```
In `api/tms/api/app.py`, import `printing` with the other routes and add `v1.include_router(printing.router)` after the runs router.

**Step 5: The worker sends them**

In `api/tms/worker.py` the imports become:
```python
from tms.models import OutboundCall, PrintJob
from tms.services import printing, wms_client
```
add before `def wait_for_schema`:
```python
def send_print_jobs(session: Session, http: httpx.Client, now: datetime | None = None,
                    limit: int = 50) -> int:
    """Hand every due print job to Platen. Platen answers that it has it, then
    says printed or failed on its own (POST /v1/print-jobs/{job_id}/status)."""
    if not get_settings().platen_url:
        return 0    # they wait until Platen is set up
    now = now or datetime.now(UTC)
    tried = 0
    while tried < limit and (job := _claim(session, PrintJob, now)) is not None:
        error = printing.send(http, job)
        job.attempts += 1
        tried += 1
        if error is None:
            job.status = "accepted"
            job.sent_at = now
            job.last_error = None
            log.info("print job %s (%s) accepted by Platen", job.job_id, job.template)
        else:
            retry_later(job, now, error)
            log.warning("print job %s try %d failed%s: %s", job.job_id, job.attempts,
                        "; given up" if job.status == "failed" else "", error)
        session.commit()
    return tried
```
and `run_once` becomes:
```python
def run_once(session: Session, http: httpx.Client) -> int:
    return send_ship_calls(session, http) + send_print_jobs(session, http)
```

**Step 6: Run the tests**

Run: `.venv/bin/pytest tests/test_printing.py tests/test_ship_calls.py -q && .venv/bin/pytest -q`
Expected: `16 passed`, then the whole suite: `184 passed`.

**Step 7: Commit**

```bash
git add api/tms/services/printing.py api/tms/api/routes/printing.py api/tms/api/app.py api/tms/worker.py api/tests/test_printing.py
git commit -m "Print run sheets through Platen, and take Platen's printed or failed"
```

### Task 16: The worker in Compose, the settings, and CI

**Files:**
- Modify: `docker-compose.yml`
- Modify: `.env.example`
- Modify: `.github/workflows/ci.yml` (the compose job)

**Step 1: Compose**

In `docker-compose.yml`, add to the `api` service's `environment` (after `TMS_WMS_API_KEY`):
```yaml
      TMS_PLATEN_URL: ${TMS_PLATEN_URL:-}
      TMS_RUN_SHEET_PRINTER: ${TMS_RUN_SHEET_PRINTER:-dispatch}
```
(the api refuses to print while Platen is not set up, and snapshots the printer into each job). Replace the comment `# The worker arrives with step 2, with the queue it drains.` with:
```yaml
  # Makes the ship calls to Simple WMS and sends run sheets to Platen, from
  # their queues in the database, with backoff. Waits for the api's migrations.
  worker:
    build: ./api
    restart: unless-stopped
    command: python -m tms.worker
    environment:
      TMS_DATABASE_URL: postgresql+psycopg://${POSTGRES_USER:-tms}:${POSTGRES_PASSWORD}@db:5432/${POSTGRES_DB:-tms}
      TMS_SECRET_KEY: ${TMS_SECRET_KEY:?set TMS_SECRET_KEY in .env}
      TMS_WMS_URL: ${TMS_WMS_URL:-}
      TMS_WMS_API_KEY: ${TMS_WMS_API_KEY:-}
      TMS_PLATEN_URL: ${TMS_PLATEN_URL:-}
      TMS_PLATEN_KEY: ${TMS_PLATEN_KEY:-}
      TMS_RUN_SHEET_PRINTER: ${TMS_RUN_SHEET_PRINTER:-dispatch}
    depends_on:
      db:
        condition: service_healthy
```

**Step 2: `.env.example`**

Replace the Simple WMS key lines (`# Where Simple WMS's API is…` to `TMS_WMS_API_KEY=`) with:
```
# Where Simple WMS's API is, and a WMS API key with the tasks:read and
# tasks:write scopes: tasks:read to read a packed delivery's ship-to,
# tasks:write to tell Simple WMS it shipped when its run departs.
TMS_WMS_URL=
TMS_WMS_API_KEY=

# Platen (optional: without it, run sheets are not printed)
# Its job intake, e.g. https://platen.example.com/intake/jobs, and an
# operator key from Platen's People > Keys. Give that Platen key a callback to
# this API (https://<tms>) with a Simple TMS key made by
#   docker compose exec api tms create-api-key --name platen --scopes printing:write
# so Platen can say each run sheet printed.
TMS_PLATEN_URL=
TMS_PLATEN_KEY=
# The Platen printer run sheets go to: its id or its name.
TMS_RUN_SHEET_PRINTER=dispatch
```

**Step 3: CI**

In `.github/workflows/ci.yml`'s `compose` job, start the worker too (`docker compose up -d --build db api worker`) and add after the trigger check:
```yaml
      - name: The worker is polling
        run: |
          for i in $(seq 1 30); do
            if docker compose logs worker | grep -q "worker started"; then ok=1; break; fi
            sleep 2
          done
          test "${ok:-0}" = 1 || (docker compose logs worker; exit 1)
```

**Step 4: Check**

Run (repo root):
```
docker compose --env-file .env.example config --quiet && echo ok
docker compose up -d --build api worker
docker compose logs worker | tail -3
```
Expected: `ok`; the worker logs `worker started, polling every 5.0s` (after `waiting for migrations` if the api was still starting).

**Step 5: Commit**

```bash
git add docker-compose.yml .env.example .github/workflows/ci.yml
git commit -m "Run the worker in Compose, with the Platen and run sheet settings"
```

### Task 17: `docs/api.md`

**Files:**
- Modify: `docs/api.md`

**Step 1: Shared rules and scopes**

In "Shared rules", the **Auth** bullet gains a sentence at the end: `A system (Platen reporting a run sheet) uses an API key, `tms_k.…`, made with `tms create-api-key`.` The **Errors** bullet gains: `Something well formed that cannot be done as things stand (a run that has left, a truck over its limit) is `409` with `{ "detail", "code" }`.` The **Never deleted** bullet becomes: `Drivers and vehicles are deactivated, runs cancelled and stops taken off, never deleted. A consignment's history is append-only.`

Replace the Scopes table with:
```markdown
| Who | Scopes |
| --- | --- |
| `admin` | `*` |
| `manager` | `fleet:read`, `fleet:write`, `consignments:read`, `runs:read`, `runs:write` |
| `dispatcher` | `fleet:read`, `consignments:read`, `runs:read`, `runs:write` |
| a driver | `fleet:read` (to pick a truck) |
| an API key | what it was made with: some of `printing:write`, `runs:read`, `runs:write`, `consignments:read`, `fleet:read` |
```

**Step 2: Consignments**

In the `GET /v1/consignments` example, after `"created_at": …,` add `"weight_kg": "9.5", "volume_m3": "0.096",` and after the paragraph about `ship_to` add: `weight_kg` and `volume_m3` are the packages added up (volume from length × width × height in cm); a package without a weight, or without all three sides, adds nothing.` Replace the last paragraph of `GET /v1/consignments/{id}` ("Statuses in step 1: …") with:
```markdown
Statuses: `packed` (in the tray), `assigned` (on a planned run), `departed`
(its run has left). `delivered` and `failed` come with the driver app. The
history has a row for each: `packed`, `assigned` and `unassigned` (with the
run and who), `departed` (with the run, vehicle, driver and who).
```

**Step 3: Runs, printing and the calls out**

Add before `## From Simple WMS`:
````markdown
## Runs

A run is one truck's day: a vehicle, a driver and stops in order. `runs:read`
to look, `runs:write` to change. Every change to any plan is made one at a
time, so two dispatchers dragging at once each see the other's work: stops
are always numbered 1, 2, 3… with no gap or twin, and a consignment is on one
run at most. Runs are cancelled, never deleted; a stop taken off a run keeps
its row.

Each run:
```json
{
  "id": 7, "run_no": "R-0007", "date": "2026-10-14", "status": "planned",
  "vehicle": { "rego": "TMS041", "type": "rigid", "max_kg": "4500", "max_m3": "22", "active": true },
  "driver": { "code": "kim", "name": "Kim" },
  "created_by": "dee", "created_at": "2026-10-13T22:01:09.114Z", "departed_at": null,
  "stops": [{ "sequence": 1, "consignment": {
      "id": 31, "wms_ref": "0080034519", "customer": "Station Deli", "suburb": "Ballan",
      "status": "assigned", "packages": 5, "cartons": 5, "weight_kg": "72", "volume_m3": "0.5" } }],
  "load": { "weight_kg": "72", "volume_m3": "0.5", "max_kg": "4500", "max_m3": "22",
            "unknown_weight": 0, "unknown_volume": 0, "state": "ok", "message": null },
  "run_sheet": null,
  "shipping": { "pending": 0, "done": 0, "failed": 0 }
}
```
`status` is `planned`, `departed`, `done` (with the driver app) or
`cancelled`. `load` adds up the stops' packages against the truck: `state` is
`ok`, `near` (90 % or more of either limit: "80 kg and 0.4 m³ left. Small
drops only.") or `over` (more than either: "280 kg too heavy. Move a stop to
another run before printing the run sheet."); what is over is rounded up, what
is left rounded down. `unknown_weight` and `unknown_volume` count packages
with no weight, or without all three sides, which add nothing. `run_sheet` is
the latest print of it (below); `shipping` counts its ship calls to Simple WMS
by state.

- `GET /v1/runs?date=2026-10-14` — that day's runs, oldest first, as
  `{ "items", "total" }`. Cancelled runs only with `status=cancelled` (or a
  comma list of statuses). `date` is required.
- `POST /v1/runs` — `{ "date", "vehicle": "TMS041", "driver": "kim" }` →
  `201` with the run. The rego is read as for vehicles (`tms 041` is
  `TMS041`). A truck has one run a day (a cancelled one frees it). A truck or
  driver that is deactivated, unknown or taken is a `422` naming `vehicle` or
  `driver`.
- `GET /v1/runs/{id}`
- `PATCH /v1/runs/{id}` — `vehicle` and/or `driver`, while it is planned.
- `POST /v1/runs/{id}/cancel` — only an empty planned run; `409 run_not_empty`
  otherwise. Cancelling a cancelled run is a no-op.

### Planning the stops

Each answers with the run as it now is.

- `POST /v1/runs/{id}/assign` — `{ "consignment_id", "position"? }`. Puts a
  `packed` consignment on the run at `position` (1 is first; left out, last;
  past the end, last), or moves one already on this run to that place, or
  takes it off another planned run and puts it here. Already there: nothing
  changes. The history gets `assigned` (and `unassigned` from the other run)
  with the run and who. Past the truck's limits is allowed: the run shows
  `over`, and cannot print or leave until a stop moves.
- `POST /v1/runs/{id}/unassign` — `{ "consignment_id" }`. Back to the tray
  (`packed`, history `unassigned`). Not on this run: nothing happens.
- `POST /v1/runs/{id}/reorder` — `{ "consignment_ids": [ … ] }`, every stop on
  the run in the new order. If that is not exactly the run's stops (someone
  else added or took one off meanwhile, or an id is twice), it is
  `409 stale` and nothing changes: reload and drag again.

| `409` code | When |
| --- | --- |
| `run_not_planned` | the run has departed or been cancelled; its stops are fixed |
| `not_plannable` | the consignment has departed (or is otherwise past planning) |
| `vehicle_unavailable` | the run's truck has been deactivated |
| `stale` | a reorder that is not the run's stops as they are |

An unknown consignment is a `422` on `consignment_id`; an unknown run `404`.

### POST /v1/runs/{id}/depart

The truck has left. Each consignment becomes `departed` (history `departed`
with the run, vehicle, driver and who), and a ship call to Simple WMS is
queued for each, all in one go. Departing a departed run changes nothing and
queues nothing more. Refused with `409`: `run_empty`, `vehicle_unavailable`,
`over_capacity` (with the message), `run_not_planned` for a cancelled run.

The worker then makes, for each consignment:
```
POST {TMS_WMS_URL}/v1/deliveries/{wms_ref}/ship      Authorization: Bearer {TMS_WMS_API_KEY}
{ "message_id": "<its own uuid>", "owner": "DEFAULT", "carrier": "Own fleet",
  "tracking_no": "R-0007", "shipped_by": "dee" }
```
The Simple WMS key needs `tasks:write` (and `tasks:read` for step 1's read),
with its warehouses and owner covering the delivery. Any `2xx` is done, and so
is `409 already_shipped`. Anything else, or no answer within 10 s, is tried
again after 1 min, 5, 30 and 2 h; if the try after the 2 h wait fails too the
call is `failed`. The same `message_id` goes every time, so Simple WMS ships
each delivery once however often it is asked. A `401`, `403` or `404` is also
logged as an error naming the likely cause. Without `TMS_WMS_URL` and
`TMS_WMS_API_KEY` the calls wait, untried.

- `POST /v1/runs/{id}/retry-ship` — calls that gave up start again from
  scratch, each with its same `message_id`.

### Run sheets

- `POST /v1/runs/{id}/print` → `202` with the print job. Each call prints
  again. Refused with `409`: `printing_not_set_up` (no `TMS_PLATEN_URL`),
  `run_empty`, `run_cancelled`, `over_capacity`.

```json
{ "job_id": "uuid", "run_id": 7, "template": "run-sheet", "printer": "dispatch",
  "status": "pending", "attempts": 0, "last_error": null,
  "created_at": "…", "sent_at": null, "printed_at": null }
```
The worker sends it to `TMS_PLATEN_URL` (Platen's `https://<platen>/intake/jobs`)
with `Authorization: Bearer {TMS_PLATEN_KEY}` and `X-TMS-Job-Id`:
```json
{ "job_id": "uuid", "template": "run-sheet", "version": "v1", "printer": "dispatch",
  "copies": 1, "reference": { "type": "run", "ref": "R-0007" },
  "data": {
    "run_no": "R-0007", "date": "2026-10-14", "vehicle": "TMS041", "vehicle_type": "rigid",
    "driver": "Kim", "driver_code": "kim", "stop_count": 2,
    "total_kg": "310", "total_m3": "1", "max_kg": "4,500", "max_m3": "22",
    "lines": [{ "stop": 1, "customer": "Station Deli", "wms_ref": "0080034519",
                "address": "1 Main St", "suburb": "Ballan", "postcode": "3342", "phone": "",
                "packages": 1, "weight_kg": "100", "required_by": "" }]
  } }
```
Every value is text or a whole number, never `null`. In Platen, `run-sheet` is a
page template: it gets one row per entry in `lines` with the rest beside it,
so a table binds `{{ stop }}`, `{{ customer }}`, `{{ address }}`… and a
heading `{{ run_no }}`, `{{ driver }}`. Status `pending` → `accepted` (Platen
has it) → `printed` or `failed`; refusals follow the same backoff as ship
calls, and `last_error` keeps Platen's answer in its own words.

### POST /v1/print-jobs/{job_id}/status

How a print went, from Platen: the body Simple WMS takes,
`{ "status": "accepted" | "printed" | "failed", "message"?, "printer"? }`.
Needs `printing:write` (an API key made for Platen). `printed` sets
`printed_at`; `failed` keeps `message` as `last_error`; a late `accepted` does
not undo `printed` or `failed`. An unknown `job_id` is `404`, which Platen
does not ask again.
````

**Step 4: API keys**

Add before `## System`:
````markdown
## API keys

For a system rather than a person; today, Platen saying how a run sheet
went. Made on the command line and shown once (only a hash is kept):
```
docker compose exec api tms create-api-key --name platen --scopes printing:write
docker compose exec api tms revoke-api-key --name platen
```
A key may be given some of `printing:write`, `runs:read`, `runs:write`,
`consignments:read`, `fleet:read`. `GET /v1/auth/me` with a key answers
`"kind": "key"`, its name and scopes. A revoked key is a `401` at once; it is
kept, not deleted.
````

**Step 5: Commit**

```bash
git add docs/api.md
git commit -m "Document runs, run sheets, the ship call and API keys"
```

### Task 18: README and the repo layout

**Files:**
- Modify: `README.md`
- Modify: `CLAUDE.md` (repo layout)

**Step 1: README**

- Status: `Status: **build step 2, own fleet.** Runs planned on a map-and-drawer desktop, run sheets printed through Platen, and Simple WMS told each delivery shipped when its run leaves; on top of step 1 (sign-in, vehicles and drivers, consignments from Simple WMS's delivery.packed). The driver app, fleet care, live location and carriers come with the next steps in docs/plans/2026-10-10-simple-tms-design.md.`
- In "What is in this repo" add `apps/desktop/         the dispatcher's desktop app (Vite + React)` and make the last line `docker-compose.yml   api, worker, db, caddy`.
- "Getting started" becomes:
````markdown
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
`worker` makes the calls to Simple WMS and sends run sheets to Platen; it
needs no port. Rebuild the desktop (`npm run build`) after pulling changes to
`apps/desktop`; Caddy serves `apps/desktop/dist` as it is.
````
- "Connecting Simple WMS": the key needs `tasks:read` and `tasks:write`: `tasks:read` to read a packed delivery's ship-to, `tasks:write` to say it shipped. Add: `When a run leaves, Simple TMS calls POST /v1/deliveries/{ref}/ship for each consignment with carrier "Own fleet" and the run number as the tracking number, retrying after 1 min, 5, 30 and 2 h if Simple WMS is away; Simple WMS then fires delivery.shipped as usual.`
- Add a section after it:
````markdown
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
````
- "Run the API outside Docker": add `.venv/bin/python -m tms.worker` as the worker, and a short "Run the desktop" block: `cd apps/desktop && npm ci && npm run dev` (http://localhost:5174, proxying `/v1` to `http://127.0.0.1:8022`; `TMS_API_URL=… npm run dev` for another API) and `npm test`.
- "Tests": add `cd apps/desktop && npm test` (Vitest).

**Step 2: CLAUDE.md**

In the repo layout replace the last two lines (`docker-compose.yml   api, db, caddy` and the sentence under the block) with:
```
apps/desktop/        the dispatcher's desktop app; apps/driver comes with step 3
docker-compose.yml   api, worker, db, caddy
```
The driver app arrives with its build step.

**Step 3: Commit**

```bash
git add README.md CLAUDE.md
git commit -m "Document step 2: the desktop, the worker, and connecting Platen"
```

## Part C — the dispatcher desktop (`apps/desktop`)

Built to `design/screens/Main.html` and `design/screens/PlanRuns.html`; open them beside the app while working. `npm` commands run from `apps/desktop/`. The versions are Simple WMS's desktop's, so one person keeps both.

### Task 19: The app's skeleton and its API client

**Files:**
- Create: `apps/desktop/package.json`, `vite.config.ts`, `tsconfig.json`, `index.html`
- Create: `apps/desktop/public/favicon.svg`, `apps/desktop/public/region.svg`
- Create: `apps/desktop/src/main.tsx`, `src/vite-env.d.ts`, `src/index.css`, `src/test/setup.ts`
- Create: `apps/desktop/src/api/client.ts`, `src/api/types.ts`
- Create: `apps/desktop/src/test/client.test.ts`
- Modify: `.gitignore`

**Step 1: The project**

`apps/desktop/package.json`:
```json
{
  "name": "simple-tms-desktop",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "preview": "vite preview",
    "test": "vitest run",
    "typecheck": "tsc -b"
  },
  "dependencies": {
    "@fontsource/manrope": "^5.3.0",
    "react": "^19.1.0",
    "react-dom": "^19.1.0",
    "react-router-dom": "^7.6.0"
  },
  "devDependencies": {
    "@tailwindcss/vite": "^4.1.0",
    "@testing-library/jest-dom": "^6.6.0",
    "@testing-library/react": "^16.3.0",
    "@testing-library/user-event": "^14.6.0",
    "@types/react": "^19.1.0",
    "@types/react-dom": "^19.1.0",
    "@vitejs/plugin-react": "^4.5.0",
    "jsdom": "^26.1.0",
    "tailwindcss": "^4.1.0",
    "typescript": "^5.8.0",
    "vite": "^6.3.0",
    "vitest": "^3.2.0"
  }
}
```

`apps/desktop/vite.config.ts` (port 5174, so it runs beside Simple WMS's on 5173):
```ts
/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Vite runs this under Node; the app itself never sees process.
declare const process: { env: Record<string, string | undefined> };

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5174,
    proxy: {
      "/v1": {
        target: process.env.TMS_API_URL ?? "http://127.0.0.1:8022",
        changeOrigin: true,
      },
    },
  },
  build: { outDir: "dist", sourcemap: false },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    css: false,
    testTimeout: 20000,
  },
});
```

`apps/desktop/tsconfig.json`:
```json
{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["ES2022", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "moduleResolution": "bundler",
    "jsx": "react-jsx",
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noFallthroughCasesInSwitch": true,
    "skipLibCheck": true,
    "isolatedModules": true,
    "noEmit": true,
    "types": ["vitest/globals", "@testing-library/jest-dom"]
  },
  "include": ["src", "vite.config.ts"]
}
```

`apps/desktop/index.html`:
```html
<!doctype html>
<html lang="en-AU">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <meta name="color-scheme" content="light" />
    <title>Simple TMS</title>
    <link rel="icon" href="/favicon.svg" type="image/svg+xml" />
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

`apps/desktop/public/favicon.svg` (the mark from the drawn header):
```svg
<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 32 32"><path d="M16 3 28 10 16 17 4 10Z" fill="#5BC28F"/><path d="M4 10 16 17V30L4 23Z" fill="#1F8A5B"/><path d="M28 10 16 17V30L28 23Z" fill="#146B45"/><path d="M10 6.5 22 13.5v4" fill="none" stroke="#E7F4EC" stroke-width="2"/></svg>
```

`apps/desktop/src/vite-env.d.ts`:
```ts
/// <reference types="vite/client" />
```

`apps/desktop/src/test/setup.ts`:
```ts
import "@testing-library/jest-dom/vitest";
```

`apps/desktop/src/main.tsx`:
```tsx
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "@fontsource/manrope/latin-400.css";
import "@fontsource/manrope/latin-500.css";
import "@fontsource/manrope/latin-600.css";
import "@fontsource/manrope/latin-700.css";
import "@fontsource/manrope/latin-800.css";
import "./index.css";
import { App } from "./App";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
```

Append to `.gitignore`:
```
node_modules/
dist/
*.tsbuildinfo
```

**Step 2: The map picture**

The map is `design/screens/region.svg` without its sample pins and truck tags (lines 111 to 123: numbered stop pins and "R-2207 · Kim", "TMS-027 · grounded" and the like, which name runs and trucks that do not exist; live ones come with step 5). `design/` is not edited; the app gets a copy:
```
grep -v -e 'filter="url(#lift)"><g transform' -e '^<g transform="translate([0-9. ]*)"><ellipse cx="0" cy="4"' \
  ../../design/screens/region.svg > public/region.svg
wc -l public/region.svg && xmllint --noout public/region.svg && echo valid
```
Expected: `111 public/region.svg` (124 less 13) and `valid`.

**Step 3: The look**

`apps/desktop/src/index.css` carries the design's tokens (design/README.md) and its CSS classes as drawn, so the screens match the HTML:
```css
@import "tailwindcss";

/* Simple TMS colours (design/README.md). Simple WMS's bones, road green for
 * its own. Amber and red text use their darker ink, never the fill. */
@theme {
  --color-page: #F3F6F4;
  --color-ink: #17251F;
  --color-ink-2: #4B5A53;
  --color-muted: #5B6B63;
  --color-green: #1F8A5B;
  --color-green-dark: #146B45;
  --color-tint: #E7F4EC;
  --color-line: #DCE7DF;
  --color-line-2: #C9DBCF;
  --color-card-line: #E3EDE6;
  --color-row: #EDF3EF;
  --color-field: #F7FAF8;
  --color-amber: #E8A317;
  --color-amber-ink: #8A5A00;
  --color-amber-bg: #FFF3D6;
  --color-amber-line: #F0D08A;
  --color-red: #C9343D;
  --color-red-ink: #A3242C;
  --color-red-bg: #FCE9EA;
  --color-red-line: #F1B9BD;
  --color-blue: #2F6FB8;
  --color-violet: #7B5CC4;
  --font-sans: Manrope, ui-sans-serif, system-ui, sans-serif;
  --shadow-soft: 0 6px 20px rgba(23, 37, 31, 0.08);
  --shadow-lift: 0 10px 30px rgba(23, 37, 31, 0.12);
}

@layer base {
  html, body, #root { min-height: 100%; }
  body { margin: 0; background: var(--color-page); color: var(--color-ink); font-family: var(--font-sans); -webkit-font-smoothing: antialiased; }
  a { color: #146B45; }
  a:hover { color: #0E5034; }
  :focus-visible { outline: 3px solid rgba(31, 138, 91, 0.45); outline-offset: 2px; }
  button, input, select, textarea { font: inherit; color: inherit; }
}

/* The classes of design/screens/Main.html and PlanRuns.html, as drawn. */
@layer components {
  .sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
  .eyebrow { font-size: 12px; font-weight: 800; letter-spacing: 1.2px; color: var(--color-muted); text-transform: uppercase; }
  .pill { display: inline-flex; align-items: center; gap: 5px; padding: 4px 10px; border-radius: 999px; font-size: 12px; font-weight: 800; white-space: nowrap; line-height: 1.3; }
  .p-green { background: #E3F3EA; color: #146B45; }
  .p-amber { background: var(--color-amber-bg); color: var(--color-amber-ink); }
  .p-red { background: var(--color-red-bg); color: var(--color-red-ink); }
  .p-grey { background: #EEF2EF; color: #4B5A53; }
  .btn { height: 44px; padding: 0 16px; border-radius: 12px; border: 1px solid var(--color-line-2); background: #fff; color: var(--color-ink); font-weight: 700; font-size: 14px; cursor: pointer; display: inline-flex; align-items: center; justify-content: center; gap: 6px; text-decoration: none; white-space: nowrap; }
  .btn:hover { color: var(--color-ink); border-color: var(--color-green); }
  .btn:disabled { opacity: 0.55; cursor: default; }
  .btn.primary { border: 0; background: var(--color-green); color: #fff; font-weight: 800; }
  .btn.primary:hover { background: var(--color-green-dark); color: #fff; }
  .btn.sm { height: 36px; padding: 0 12px; font-size: 13px; border-radius: 10px; }
  .field { height: 44px; padding: 0 12px; border: 1px solid var(--color-line-2); border-radius: 12px; background: #fff; font-size: 14px; color: var(--color-ink); width: 100%; }
  .search { display: flex; align-items: center; gap: 8px; height: 44px; padding: 0 14px; border: 1px solid var(--color-line); border-radius: 12px; background: var(--color-field); color: var(--color-muted); font-size: 14px; }
  .search input { flex: 1; border: 0; background: transparent; outline: none; min-width: 0; color: var(--color-ink); }
  .search input::placeholder { color: var(--color-muted); }
  .dot { width: 10px; height: 10px; border-radius: 50%; flex: none; display: inline-block; }
  .num { font-variant-numeric: tabular-nums; }
  .small { font-size: 12px; color: var(--color-muted); }

  /* the map */
  .app { height: 100vh; min-height: 680px; display: flex; flex-direction: column; }
  .hdr { display: flex; flex-wrap: wrap; align-items: center; gap: 12px 20px; padding: 14px 24px; background: #fff; border-bottom: 1px solid var(--color-line); }
  .brand { display: flex; align-items: center; gap: 10px; font-size: 19px; font-weight: 800; letter-spacing: -0.2px; }
  .hdr .search { flex: 1 1 280px; max-width: 460px; }
  .hright { display: flex; align-items: center; gap: 8px; margin-left: auto; flex-wrap: wrap; }
  .live { display: flex; align-items: center; gap: 6px; height: 36px; padding: 0 12px; border-radius: 999px; background: #E3F3EA; color: #146B45; font-size: 13px; font-weight: 700; }
  .avatar { width: 44px; height: 44px; border-radius: 50%; border: 0; background: var(--color-green); color: #fff; font-weight: 800; font-size: 14px; cursor: pointer; }
  .stage { position: relative; flex: 1; overflow: hidden; }
  .stage > img { display: block; width: 100%; max-width: 1540px; height: auto; margin: 0 auto; }
  .ov { position: absolute; }
  .hud { padding: 10px 12px; border-radius: 14px; background: #fff; box-shadow: var(--shadow-soft); text-decoration: none; color: inherit; }
  .hud .eyebrow { font-size: 11px; }
  .hud .v { font-size: 24px; font-weight: 800; }
  .nav { display: flex; flex-wrap: wrap; gap: 6px; padding: 6px; border-radius: 16px; background: #17251F; box-shadow: 0 10px 30px rgba(23, 37, 31, 0.25); }
  .nav a { height: 48px; padding: 0 16px; border-radius: 12px; color: #fff; font-weight: 700; font-size: 14px; text-decoration: none; display: flex; align-items: center; gap: 8px; }
  .nav a:hover { background: rgba(255, 255, 255, 0.08); color: #fff; }
  .nav a.on { background: var(--color-green); font-weight: 800; }
  .nav a i { font-style: normal; padding: 1px 7px; border-radius: 999px; background: rgba(255, 255, 255, 0.22); font-size: 12px; font-weight: 800; }

  /* a drawer over the map */
  .dim { position: fixed; inset: 0; background: rgba(23, 37, 31, 0.38); backdrop-filter: blur(2px); }
  .drawer { position: fixed; top: 0; right: 0; bottom: 0; width: 72%; background: #fff; border-radius: 24px 0 0 24px; box-shadow: -20px 0 50px rgba(23, 37, 31, 0.25); display: flex; flex-direction: column; }
  .drawer.wide { width: 88%; }
  .dhead { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; padding: 22px 28px 14px; }
  .dhead h1 { margin: 0; font-size: 26px; font-weight: 800; letter-spacing: -0.3px; }
  .dhead .sub { font-size: 13px; color: var(--color-muted); }
  .closebtn { width: 44px; height: 44px; border-radius: 12px; background: var(--color-page); color: var(--color-ink); display: grid; place-items: center; text-decoration: none; flex: none; border: 0; cursor: pointer; }
  .dbody { flex: 1; min-height: 0; overflow: auto; padding: 0 28px 40px; display: flex; flex-direction: column; gap: 16px; }
  .dbody > * { flex: none; }

  /* the plan board */
  .boardwrap { overflow-x: auto; padding-bottom: 6px; }
  .board { display: grid; gap: 12px; align-items: start; }
  .lane { border-radius: 18px; background: #F7FAF8; border: 1px solid var(--color-card-line); padding: 12px; display: flex; flex-direction: column; gap: 8px; min-width: 0; }
  .lane.tray { background: #FFFBEF; border-color: var(--color-amber-line); }
  .lane.near { border-color: var(--color-amber-line); }
  .lane.over { border-color: var(--color-red-line); background: #FFF8F8; }
  .lane.out { background: #F1F3F2; border-style: dashed; border-color: #C9D2CD; }
  .lane.target { border-color: var(--color-green); box-shadow: 0 0 0 2px var(--color-tint); }
  .lhead { display: flex; align-items: center; gap: 8px; }
  .swatch { width: 12px; height: 12px; border-radius: 4px; flex: none; }
  .lhead .t { font-size: 15px; font-weight: 800; }
  .lhead .s { font-size: 12px; color: var(--color-muted); }
  .meter { display: flex; flex-direction: column; gap: 3px; font-size: 12px; }
  .meter .row { display: flex; justify-content: space-between; gap: 6px; color: var(--color-ink-2); }
  .meter .row b { color: var(--color-ink); }
  .meter .track { height: 8px; border-radius: 999px; background: #E3EDE6; overflow: hidden; position: relative; }
  .meter .track span { display: block; height: 100%; border-radius: 999px; background: var(--color-green); }
  .meter.a .track span { background: var(--color-amber); }
  .meter.a .row b { color: var(--color-amber-ink); }
  .meter.r .track span { background: var(--color-red); }
  .meter.r .row b { color: var(--color-red-ink); }
  .stop { display: flex; align-items: center; gap: 8px; padding: 8px 10px 8px 4px; border-radius: 12px; background: #fff; border: 1px solid var(--color-card-line); font-size: 13px; min-height: 52px; }
  .stop .h { width: 24px; height: 36px; display: grid; place-items: center; color: #9AA8A0; cursor: grab; flex: none; }
  .stop .n { width: 24px; height: 24px; border-radius: 50%; display: grid; place-items: center; font-size: 12px; font-weight: 800; color: #fff; flex: none; }
  .stop .c { flex: 1; min-width: 0; }
  .stop .c b { display: block; line-height: 1.3; }
  .stop .c span { color: var(--color-muted); font-size: 12px; }
  .con { padding: 10px 12px 10px 4px; border-radius: 12px; background: #fff; border: 1px solid var(--color-card-line); display: flex; gap: 6px; font-size: 13px; cursor: grab; }
  .con .c { flex: 1; min-width: 0; }
  .con b { display: block; }
  .con .meta { display: flex; flex-wrap: wrap; gap: 4px 10px; color: var(--color-ink-2); font-size: 12px; margin-top: 3px; }
  .con .ref { font-size: 11px; color: var(--color-muted); margin-top: 3px; }
  .drop { border: 2px dashed var(--color-green); border-radius: 12px; background: var(--color-tint); color: var(--color-green-dark); font-size: 12px; font-weight: 800; text-align: center; padding: 10px; }
  .drop.r { border-color: var(--color-red); background: var(--color-red-bg); color: var(--color-red-ink); }
  .dragging { transform: rotate(-2.5deg); box-shadow: 0 14px 30px rgba(23, 37, 31, 0.22); border-color: var(--color-green); }
  .msg { font-size: 12px; border-radius: 10px; padding: 8px 10px; display: flex; gap: 6px; align-items: flex-start; }
  .msg.r { background: var(--color-red-bg); color: var(--color-red-ink); }
  .msg.a { background: var(--color-amber-bg); color: var(--color-amber-ink); }
  .msg.g { background: var(--color-tint); color: var(--color-green-dark); }
  /* The keyboard's way to do what a drag does. Tab to a card or a stop and
   * its controls show over its right-hand end; so does pointing at it. */
  .stop, .con { position: relative; }
  .kb { display: none; gap: 2px; align-items: center; position: absolute; right: 6px; top: 50%; transform: translateY(-50%); padding: 3px; border-radius: 10px; background: #fff; box-shadow: var(--shadow-soft); }
  .stop:hover .kb, .stop:focus-within .kb, .con:hover .kb, .con:focus-within .kb { display: flex; }
  .kb button, .kb select { height: 30px; border-radius: 8px; border: 1px solid var(--color-line); background: #fff; font-size: 12px; cursor: pointer; }
  .kb button { width: 30px; display: grid; place-items: center; padding: 0; }
  .kb select { max-width: 120px; padding: 0 4px; }
}

@media (max-width: 960px) {
  .app { height: auto; }
  .ov { position: static; width: auto !important; max-width: none !important; transform: none !important; }
  .stage { display: flex; flex-direction: column; gap: 12px; padding: 12px; }
  .drawer, .drawer.wide { position: fixed; width: 100%; border-radius: 0; }
}

@keyframes tms-drawer-in { from { transform: translateX(40px); opacity: 0; } to { transform: none; opacity: 1; } }
.drawer { animation: tms-drawer-in 0.22s ease-out; }
@media (prefers-reduced-motion: reduce) { .drawer { animation: none; } }
```

**Step 4: Install**

Run: `npm install`
Expected: `package-lock.json` is written; `npx tsc -v` prints a 5.x version.

**Step 5: Write the failing client tests**

`apps/desktop/src/test/client.test.ts`:
```ts
import { ApiClient, ApiError } from "../api/client";

function json(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

async function refusal(call: Promise<unknown>): Promise<ApiError> {
  try {
    await call;
  } catch (e) {
    return e as ApiError;
  }
  throw new Error("it was not refused");
}

describe("ApiClient", () => {
  let calls: { url: string; init: RequestInit }[];
  let answers: Response[];
  let client: ApiClient;

  beforeEach(() => {
    calls = [];
    answers = [];
    const fetcher = async (url: string, init: RequestInit) => {
      calls.push({ url, init });
      return answers.shift() ?? json(500, { detail: "no answer queued" });
    };
    client = new ApiClient({ fetcher, storage: new Map() });
  });

  it("sends the bearer token and reads JSON", async () => {
    client.setSession({ token: "tms_s.abc", refresh_token: "tms_r.xyz", expires_in: 900 });
    answers.push(json(200, { items: [], total: 0 }));
    const body = await client.get<{ total: number }>("/v1/runs", { date: "2026-10-14", status: undefined });
    expect(body.total).toBe(0);
    expect(calls[0].url).toBe("/v1/runs?date=2026-10-14");
    expect(new Headers(calls[0].init.headers).get("Authorization")).toBe("Bearer tms_s.abc");
  });

  it("refreshes once on a 401 and tries again", async () => {
    client.setSession({ token: "old", refresh_token: "r1", expires_in: 900 });
    answers.push(json(401, { detail: "session expired or invalid" }));
    answers.push(json(200, { token: "new", refresh_token: "r2", expires_in: 900 }));
    answers.push(json(200, { ok: true }));
    expect(await client.get<{ ok: boolean }>("/v1/auth/me")).toEqual({ ok: true });
    expect(calls.map((c) => c.url)).toEqual(["/v1/auth/me", "/v1/auth/refresh", "/v1/auth/me"]);
    expect(new Headers(calls[2].init.headers).get("Authorization")).toBe("Bearer new");
    expect(client.session?.refresh_token).toBe("r2");
  });

  it("signs out when the refresh fails too", async () => {
    const signedOut = vi.fn();
    client.onSignedOut = signedOut;
    client.setSession({ token: "old", refresh_token: "r1", expires_in: 900 });
    answers.push(json(401, {}), json(401, {}));
    await expect(client.get("/v1/auth/me")).rejects.toBeInstanceOf(ApiError);
    expect(client.session).toBeNull();
    expect(signedOut).toHaveBeenCalled();
  });

  it("keeps the API's plain message and its code", async () => {
    client.setSession({ token: "t", refresh_token: "r", expires_in: 900 });
    answers.push(json(409, { detail: "R-0003: 280 kg too heavy. Move a stop to another run before printing the run sheet.", code: "over_capacity" }));
    const err = await refusal(client.post("/v1/runs/3/print"));
    expect(err.status).toBe(409);
    expect(err.code).toBe("over_capacity");
    expect(err.message).toBe("R-0003: 280 kg too heavy. Move a stop to another run before printing the run sheet.");
  });

  it("gives a field error as its message alone", async () => {
    client.setSession({ token: "t", refresh_token: "r", expires_in: 900 });
    answers.push(json(422, { errors: [{ field: "vehicle", message: "TMS041 already has R-0001 on 2026-10-14" }] }));
    const err = await refusal(client.post("/v1/runs", {}));
    expect(err.message).toBe("TMS041 already has R-0001 on 2026-10-14");
  });

  it("copes with an answer that is not JSON, such as a proxy's error page", async () => {
    client.setSession({ token: "t", refresh_token: "r", expires_in: 900 });
    answers.push(new Response("<html>Bad gateway</html>", { status: 502 }));
    const err = await refusal(client.get("/v1/runs"));
    expect(err.status).toBe(502);
    expect(err.message).toBe("<html>Bad gateway</html>");
  });

  it("keeps the refresh token so a reload can pick up", () => {
    const storage = new Map<string, string>();
    new ApiClient({ fetcher: async () => json(200, {}), storage })
      .setSession({ token: "t", refresh_token: "keep-me", expires_in: 900 });
    expect(new ApiClient({ fetcher: async () => json(200, {}), storage }).storedRefreshToken()).toBe("keep-me");
  });
});
```

Run: `npm test`
Expected: FAIL — `Failed to resolve import "../api/client"`.

**Step 6: The client and the shapes**

`apps/desktop/src/api/client.ts` (Simple WMS's client, keyed `tms.refresh`; a field error's message alone, as the screens show it; and a non-JSON answer such as a proxy's error page read as text rather than thrown):
```ts
/** The desktop is an ordinary API client with a session token. Nothing here
 * a partner could not do with the same API. (As Simple WMS's desktop.) */

export interface Session {
  token: string;
  refresh_token: string;
  expires_in: number;
}

export interface FieldError {
  field: string;
  message: string;
}

export class ApiError extends Error {
  status: number;
  errors: FieldError[];
  /** The API's reason when it gives one: stale, over_capacity, wrong_password, locked, ... */
  code: string | null;
  body: unknown;
  constructor(status: number, body: unknown) {
    const errors = ((body as { errors?: FieldError[] })?.errors ?? []) as FieldError[];
    const detail = (body as { detail?: string })?.detail;
    const message =
      errors.length > 0
        ? errors.map((e) => e.message).join("; ")
        : typeof detail === "string"
          ? detail
          : `Simple TMS answered ${status}`;
    super(message);
    this.status = status;
    this.errors = errors;
    this.code = typeof (body as { code?: unknown })?.code === "string" ? (body as { code: string }).code : null;
    this.body = body;
  }
}

type Fetcher = (url: string, init: RequestInit) => Promise<Response>;
interface Storage {
  get(key: string): string | undefined;
  set(key: string, value: string): unknown;
  delete(key: string): unknown;
}

const REFRESH_KEY = "tms.refresh";
const NO_REFRESH = new Set(["/v1/auth/login", "/v1/auth/refresh", "/v1/auth/logout"]);

function browserStorage(): Storage {
  return {
    get: (k) => {
      try { return window.localStorage.getItem(k) ?? undefined; } catch { return undefined; }
    },
    set: (k, v) => { try { window.localStorage.setItem(k, v); } catch { /* private mode */ } },
    delete: (k) => { try { window.localStorage.removeItem(k); } catch { /* ignore */ } },
  };
}

export class ApiClient {
  session: Session | null = null;
  onSignedOut: (() => void) | null = null;
  private fetcher: Fetcher;
  private storage: Storage;
  private refreshing: Promise<boolean> | null = null;

  constructor(opts: { fetcher?: Fetcher; storage?: Storage } = {}) {
    this.fetcher = opts.fetcher ?? ((url, init) => fetch(url, init));
    this.storage = opts.storage ?? browserStorage();
  }

  setSession(session: Session | null) {
    this.session = session;
    if (session) this.storage.set(REFRESH_KEY, session.refresh_token);
    else this.storage.delete(REFRESH_KEY);
  }

  storedRefreshToken(): string | undefined {
    return this.storage.get(REFRESH_KEY);
  }

  /** Pick up where a reload left off, from the stored refresh token. */
  async resume(): Promise<boolean> {
    const refresh = this.storedRefreshToken();
    if (!refresh) return false;
    this.session = { token: "", refresh_token: refresh, expires_in: 0 };
    return this.refresh();
  }

  private async refresh(): Promise<boolean> {
    // one refresh at a time: the API spends a refresh token once
    if (this.refreshing) return this.refreshing;
    this.refreshing = (async () => {
      const refresh = this.session?.refresh_token;
      if (!refresh) return false;
      const res = await this.fetcher("/v1/auth/refresh", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: refresh }),
      });
      if (!res.ok) {
        this.setSession(null);
        this.onSignedOut?.();
        return false;
      }
      this.setSession((await res.json()) as Session);
      return true;
    })();
    try {
      return await this.refreshing;
    } finally {
      this.refreshing = null;
    }
  }

  async request<T>(method: string, path: string, body?: unknown, retry = true): Promise<T> {
    const headers: Record<string, string> = { Accept: "application/json" };
    if (body !== undefined) headers["Content-Type"] = "application/json";
    if (this.session?.token) headers.Authorization = `Bearer ${this.session.token}`;
    const res = await this.fetcher(path, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (res.status === 401 && retry && this.session?.refresh_token && !NO_REFRESH.has(path)) {
      if (await this.refresh()) return this.request<T>(method, path, body, false);
    }
    if (res.status === 204) return undefined as T;
    const text = await res.text();
    let parsed: unknown = null;
    try { parsed = text ? JSON.parse(text) : null; } catch { parsed = { detail: text.slice(0, 200) }; }
    if (!res.ok) throw new ApiError(res.status, parsed);
    return parsed as T;
  }

  get<T>(path: string, params?: Record<string, string | number | boolean | null | undefined>) {
    const qs = params
      ? Object.entries(params)
          .filter(([, v]) => v !== undefined && v !== null && v !== "")
          .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`)
          .join("&")
      : "";
    return this.request<T>("GET", qs ? `${path}?${qs}` : path);
  }
  post<T>(path: string, body?: unknown) { return this.request<T>("POST", path, body ?? {}); }
  patch<T>(path: string, body: unknown) { return this.request<T>("PATCH", path, body); }
}

export const api = new ApiClient();
```

`apps/desktop/src/api/types.ts`:
```ts
/** Response shapes, as docs/api.md gives them. Decimals are strings. */

export interface Page<T> { items: T[]; total: number }

export interface Me {
  kind: "user" | "driver" | "key";
  id: number;
  name: string;
  display_name: string;
  role: string;
  scopes: string[];
}

export interface Package {
  package_no: number;
  type: string;
  weight_kg: string | null;
  length_cm: string | null;
  width_cm: string | null;
  height_cm: string | null;
  sscc: string | null;
}

export interface Consignment {
  id: number;
  wms_ref: string;
  warehouse: string;
  owner: string;
  customer: string;
  ship_to: Record<string, string | null>;
  required_by: string | null;
  status: string;
  created_at: string;
  weight_kg: string;
  volume_m3: string;
  packages: Package[];
}

export interface Vehicle {
  id: number;
  rego: string;
  type: string;
  max_kg: string;
  max_m3: string;
  active: boolean;
}

export interface Driver {
  id: number;
  code: string;
  name: string;
  active: boolean;
}

/** A consignment as the plan shows it, on a run or in the tray. */
export interface Card {
  id: number;
  wms_ref: string;
  customer: string;
  suburb: string | null;
  status: string;
  packages: number;
  cartons: number;
  weight_kg: string;
  volume_m3: string;
}

export interface RunSheet {
  job_id: string;
  status: "pending" | "accepted" | "printed" | "failed";
  printer: string;
  last_error: string | null;
  created_at: string;
  printed_at: string | null;
}

export interface Run {
  id: number;
  run_no: string;
  date: string;
  status: "planned" | "departed" | "done" | "cancelled";
  vehicle: { rego: string; type: string; max_kg: string; max_m3: string; active: boolean };
  driver: { code: string; name: string };
  created_by: string;
  created_at: string;
  departed_at: string | null;
  stops: { sequence: number; consignment: Card }[];
  load: {
    weight_kg: string; volume_m3: string; max_kg: string; max_m3: string;
    unknown_weight: number; unknown_volume: number; state: "ok" | "near" | "over"; message: string | null;
  };
  run_sheet: RunSheet | null;
  shipping: { pending: number; done: number; failed: number };
}
```

**Step 7: Run the tests**

Run: `npm test`
Expected: `7 passed`.

**Step 8: Commit**

```bash
git add .gitignore apps/desktop
git commit -m "Start the desktop app: Vite, React, Tailwind and the API client"
```
(`git status` must not list `node_modules` or `dist`.)

### Task 20: Decimals without floats, and how full a run is

**Files:**
- Create: `apps/desktop/src/lib/decimal.ts`, `src/lib/load.ts`
- Create: `apps/desktop/src/test/decimal.test.ts`, `src/test/load.test.ts`

**Step 1: Write the failing tests**

`apps/desktop/src/test/decimal.test.ts`:
```ts
import { ONE, plain, ratio, roundDown, roundUp, sum, toMicro } from "../lib/decimal";

describe("decimals without floats", () => {
  it("reads decimal strings into whole millionths", () => {
    expect(toMicro("9.5")).toBe(9_500_000n);
    expect(toMicro("1200")).toBe(1_200_000_000n);
    expect(toMicro("0.000001")).toBe(1n);
    expect(toMicro(null)).toBe(0n);
    expect(toMicro("-2")).toBe(-2_000_000n);
  });

  it("drops what is past a millionth", () => {
    expect(toMicro("0.001157625")).toBe(1157n);
  });

  it("refuses what is not a decimal", () => {
    expect(() => toMicro("1e3")).toThrow("not a decimal");
  });

  it("adds exactly where a float would not", () => {
    expect(sum(["0.1", "0.2"])).toBe(toMicro("0.3"));
    expect(plain(sum(["0.1", "0.2"]))).toBe("0.3");
  });

  it("writes numbers for people", () => {
    expect(plain(2_380_500_000n)).toBe("2,380.5");
    expect(plain(0n)).toBe("0");
    expect(plain(-1_200n * ONE)).toBe("-1,200");
  });

  it("rounds to a step", () => {
    expect(roundUp(toMicro("279.2"), ONE)).toBe(280n * ONE);
    expect(roundUp(toMicro("280"), ONE)).toBe(280n * ONE);
    expect(roundDown(toMicro("0.45"), ONE / 10n)).toBe(toMicro("0.4"));
  });

  it("gives a bar its width", () => {
    expect(ratio(toMicro("2380"), toMicro("4500"))).toBeCloseTo(0.5288, 4);
    expect(ratio(1n, 0n)).toBe(0);
  });
});
```

`apps/desktop/src/test/load.test.ts` (the same cases as the API's `test_load.py`):
```ts
import { afterAdding, loadOf } from "../lib/load";

const c = (weight_kg: string, volume_m3: string) => ({ weight_kg, volume_m3 });

describe("how full a run is", () => {
  it("is fine well under both limits", () => {
    const l = loadOf([c("2000", "10"), c("380", "1.2")], "4500", "22");
    expect(l.state).toBe("ok");
    expect(l.message).toBeNull();
    expect(l.weight.fill).toBeCloseTo(0.5289, 3);
  });

  it("is nearly full from 90 per cent of either limit", () => {
    const l = loadOf([c("1120", "8.6")], "1200", "9");
    expect(l.state).toBe("near");
    expect(l.weight.state).toBe("near");
    expect(l.volume.state).toBe("near");
    expect(l.message).toBe("80 kg and 0.4 m³ left. Small drops only.");
  });

  it("is over past either limit, says by how much, and only that bar goes red", () => {
    const l = loadOf([c("1640", "8"), c("3140", "11.4")], "4500", "22");
    expect(l.state).toBe("over");
    expect(l.weight.state).toBe("over");
    expect(l.volume.state).toBe("ok");
    expect(l.weight.fill).toBe(1);
    expect(l.headline).toBe("280 kg too heavy.");
    expect(l.message).toBe("Move a stop to another run before printing the run sheet.");
  });

  it("names both when both are over, rounding what is over up", () => {
    const l = loadOf([c("4500.2", "22.01")], "4500", "22");
    expect(l.headline).toBe("1 kg too heavy and 0.1 m³ too big.");
  });

  it("says what one more drop would do", () => {
    expect(afterAdding([c("1000", "1")], c("100", "1"), "1200", "9")).toBe("near");
    expect(afterAdding([c("1000", "1")], c("300", "1"), "1200", "9")).toBe("over");
    expect(afterAdding([], c("100", "1"), "1200", "9")).toBe("ok");
  });
});
```

**Step 2: Run them to see them fail**

Run: `npm test`
Expected: FAIL — `Failed to resolve import "../lib/decimal"`.

**Step 3: Implement**

`apps/desktop/src/lib/decimal.ts`:
```ts
/** Weights and volumes arrive as decimal strings ("9.5"). They are added up
 * in whole millionths with BigInt, so no weight passes through a float.
 * Digits past the sixth decimal place are dropped: a millionth of a cubic
 * metre is a cubic centimetre. Only a bar's width is ever a float. */

const PLACES = 6;
export const ONE = 1_000_000n;

export function toMicro(text: string | null | undefined): bigint {
  if (text == null || text === "") return 0n;
  const m = /^(-)?(\d+)(?:\.(\d+))?$/.exec(text.trim());
  if (!m) throw new Error(`not a decimal: ${text}`);
  const frac = (m[3] ?? "").slice(0, PLACES).padEnd(PLACES, "0");
  const value = BigInt(m[2]) * ONE + BigInt(frac);
  return m[1] ? -value : value;
}

export function sum(values: (string | null | undefined)[]): bigint {
  return values.reduce((total, v) => total + toMicro(v), 0n);
}

/** 2380500000n -> "2,380.5": thousands grouped, no trailing zeros. */
export function plain(value: bigint): string {
  const sign = value < 0n ? "-" : "";
  const abs = value < 0n ? -value : value;
  const whole = (abs / ONE).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  const frac = (abs % ONE).toString().padStart(PLACES, "0").replace(/0+$/, "");
  return sign + whole + (frac ? `.${frac}` : "");
}

/** Up or down to a whole number of steps (a step in millionths too). */
export function roundUp(value: bigint, step: bigint): bigint {
  const r = ((value % step) + step) % step;
  return r === 0n ? value : value - r + step;
}

export function roundDown(value: bigint, step: bigint): bigint {
  const r = ((value % step) + step) % step;
  return value - r;
}

/** part / whole as a fraction, for drawing a bar. */
export function ratio(part: bigint, whole: bigint): number {
  if (whole <= 0n) return 0;
  return Number((part * 10_000n) / whole) / 10_000;
}
```

`apps/desktop/src/lib/load.ts`:
```ts
/** How full a run is, worked out here the way the API works it out
 * (api/tms/services/load.py), so a bar moves the moment a card is dropped.
 * The API still decides: it refuses to print or depart a run that is over. */
import { ONE, plain, ratio, roundDown, roundUp, sum, toMicro } from "./decimal";

export type LoadState = "ok" | "near" | "over";

export interface Gauge {
  used: bigint;
  max: bigint;
  state: LoadState;
  /** 0 to 1, for the bar; a run over its limit draws a full bar. */
  fill: number;
}

export interface LoadView {
  weight: Gauge;
  volume: Gauge;
  state: LoadState;
  /** Over: the bold part ("280 kg too heavy.") and what to do about it. */
  headline: string | null;
  message: string | null;
}

const KG = ONE;
const M3 = ONE / 10n;

function gauge(used: bigint, max: bigint): Gauge {
  // 90 % or more is nearly full: used * 10 >= max * 9, in whole numbers
  const state: LoadState = used > max ? "over" : used * 10n >= max * 9n ? "near" : "ok";
  return { used, max, state, fill: Math.min(1, ratio(used, max)) };
}

export function loadOf(items: { weight_kg: string; volume_m3: string }[],
                       maxKg: string, maxM3: string): LoadView {
  const weight = gauge(sum(items.map((i) => i.weight_kg)), toMicro(maxKg));
  const volume = gauge(sum(items.map((i) => i.volume_m3)), toMicro(maxM3));
  if (weight.state === "over" || volume.state === "over") {
    const parts = [];
    if (weight.state === "over") parts.push(`${plain(roundUp(weight.used - weight.max, KG))} kg too heavy`);
    if (volume.state === "over") parts.push(`${plain(roundUp(volume.used - volume.max, M3))} m³ too big`);
    return {
      weight, volume, state: "over", headline: `${parts.join(" and ")}.`,
      message: "Move a stop to another run before printing the run sheet.",
    };
  }
  if (weight.state === "near" || volume.state === "near") {
    const kg = plain(roundDown(weight.max - weight.used, KG));
    const m3 = plain(roundDown(volume.max - volume.used, M3));
    return { weight, volume, state: "near", headline: null,
             message: `${kg} kg and ${m3} m³ left. Small drops only.` };
  }
  return { weight, volume, state: "ok", headline: null, message: null };
}

/** What dropping one more consignment on a run would do, for "Drop here". */
export function afterAdding(items: { weight_kg: string; volume_m3: string }[],
                            extra: { weight_kg: string; volume_m3: string },
                            maxKg: string, maxM3: string): LoadState {
  return loadOf([...items, extra], maxKg, maxM3).state;
}
```

**Step 4: Run the tests**

Run: `npm test`
Expected: `19 passed`.

**Step 5: Commit**

```bash
git add apps/desktop/src/lib apps/desktop/src/test
git commit -m "Add up weights and volumes in the desktop without floats"
```

### Task 21: The plan board's logic, and the days

**Files:**
- Create: `apps/desktop/src/lib/plan.ts`, `src/lib/days.ts`
- Create: `apps/desktop/src/test/plan.test.ts`

**Step 1: Write the failing tests**

`apps/desktop/src/test/plan.test.ts`:
```ts
import type { Consignment } from "../api/types";
import { cardOf, dropPosition, kg, m3, moved, packagesLabel, positionWithout, runColour } from "../lib/plan";
import { dayLabel, dayOptions, isoDay } from "../lib/days";

const consignment = (over: Partial<Consignment> = {}): Consignment => ({
  id: 7, wms_ref: "0080034519", warehouse: "BAL-WH01", owner: "DEFAULT", customer: "Station Deli",
  ship_to: { name: "Station Deli", suburb: "Ballan" }, required_by: null, status: "packed",
  created_at: "2026-10-13T22:00:00Z", weight_kg: "72", volume_m3: "0.5",
  packages: [1, 2, 3, 4, 5].map((n) => ({
    package_no: n, type: "carton", weight_kg: null, length_cm: null, width_cm: null,
    height_cm: null, sscc: null,
  })),
  ...over,
});

describe("the plan board", () => {
  it("makes a card from a consignment", () => {
    const card = cardOf(consignment());
    expect(card).toEqual({ id: 7, wms_ref: "0080034519", customer: "Station Deli", suburb: "Ballan",
                           status: "packed", packages: 5, cartons: 5, weight_kg: "72", volume_m3: "0.5" });
    expect(packagesLabel(card)).toBe("5 ctn");
    expect(kg(card)).toBe("72 kg");
    expect(m3(card)).toBe("0.5 m³");
  });

  it("calls a mix of cartons and pallets packages", () => {
    const c = consignment();
    c.packages[0].type = "pallet";
    expect(packagesLabel(cardOf(c))).toBe("5 pkg");
  });

  it("moves one stop and keeps the rest in order", () => {
    expect(moved([1, 2, 3, 4], 0, 2)).toEqual([2, 3, 1, 4]);
    expect(moved([1, 2, 3, 4], 3, 0)).toEqual([4, 1, 2, 3]);
    expect(moved([1, 2, 3], 0, 5)).toEqual([1, 2, 3]);
  });

  it("drops above a stop in its top half and below it in the bottom half", () => {
    expect(dropPosition(0, 10, 0, 52)).toBe(1);
    expect(dropPosition(0, 40, 0, 52)).toBe(2);
    expect(dropPosition(2, 120, 104, 52)).toBe(3);
  });

  it("counts a stop's own old place out when it moves down its run", () => {
    // stops A B C; A dropped below C is position 4 as drawn, 3 once A has gone
    expect(positionWithout(0, 4)).toBe(3);
    expect(positionWithout(2, 1)).toBe(1);
  });

  it("gives runs the map's colours in turn", () => {
    expect([0, 1, 2, 3].map(runColour)).toEqual(["#1F8A5B", "#2F6FB8", "#7B5CC4", "#1F8A5B"]);
  });
});

describe("days", () => {
  it("offers today and the week ahead", () => {
    const opts = dayOptions(new Date(2026, 9, 13, 11, 16));
    expect(opts[0]).toEqual({ value: "2026-10-13", label: "Today · Tue 13 Oct" });
    expect(opts[1]).toEqual({ value: "2026-10-14", label: "Tomorrow · Wed 14 Oct" });
    expect(opts).toHaveLength(7);
    expect(isoDay(new Date(2026, 11, 31))).toBe("2026-12-31");
    expect(dayLabel(new Date(2027, 0, 1))).toBe("Fri 1 Jan");
  });
});
```

**Step 2: Run them to see them fail**

Run: `npm test`
Expected: FAIL — `Failed to resolve import "../lib/plan"`.

**Step 3: Implement**

`apps/desktop/src/lib/plan.ts`:
```ts
/** The plan board's own logic, kept out of the components so it can be
 * tested on its own: cards from consignments, and where a drop lands. */
import type { Card, Consignment } from "../api/types";
import { plain, toMicro } from "./decimal";

export function cardOf(c: Consignment): Card {
  return {
    id: c.id, wms_ref: c.wms_ref, customer: c.customer, suburb: c.ship_to?.suburb ?? null,
    status: c.status, packages: c.packages.length,
    cartons: c.packages.filter((p) => p.type === "carton").length,
    weight_kg: c.weight_kg, volume_m3: c.volume_m3,
  };
}

/** "5 ctn" when every package is a carton, "3 pkg" otherwise. */
export function packagesLabel(c: Card): string {
  return `${c.packages} ${c.packages > 0 && c.cartons === c.packages ? "ctn" : "pkg"}`;
}

export function kg(c: Card): string {
  return `${plain(toMicro(c.weight_kg))} kg`;
}

export function m3(c: Card): string {
  return `${plain(toMicro(c.volume_m3))} m³`;
}

/** The whole order after moving one stop from one place to another (both
 * counted from 0). Out of range is no move. */
export function moved(ids: number[], from: number, to: number): number[] {
  if (from < 0 || from >= ids.length || to < 0 || to >= ids.length || from === to) return ids;
  const out = [...ids];
  const [id] = out.splice(from, 1);
  out.splice(to, 0, id);
  return out;
}

/** Where a card dragged over a stop would go: above the stop when the
 * pointer is in its top half, below it otherwise. Positions count from 1,
 * as the API's do. */
export function dropPosition(stopIndex: number, pointerY: number, top: number,
                             height: number): number {
  return pointerY < top + height / 2 ? stopIndex + 1 : stopIndex + 2;
}

/** The API's position for a stop already on this run, dropped at `position`
 * among the stops as they are drawn (with it still in its old place). */
export function positionWithout(currentIndex: number, position: number): number {
  return currentIndex + 1 < position ? position - 1 : position;
}

const RUN_COLOURS = ["#1F8A5B", "#2F6FB8", "#7B5CC4"];

export function runColour(index: number): string {
  return RUN_COLOURS[index % RUN_COLOURS.length];
}
```

`apps/desktop/src/lib/days.ts`:
```ts
/** The days a dispatcher plans: today and the week ahead, by the depot
 * computer's own calendar, as YYYY-MM-DD for the API. */

export function isoDay(d: Date): string {
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

/** "Wed 14 Oct", without the comma en-AU puts after the weekday. */
export function dayLabel(d: Date): string {
  const parts = new Intl.DateTimeFormat("en-AU", { weekday: "short", day: "numeric", month: "short" })
    .formatToParts(d);
  const get = (type: string) => parts.find((p) => p.type === type)?.value ?? "";
  return `${get("weekday")} ${get("day")} ${get("month")}`;
}

export function dayOptions(today: Date, ahead = 6): { value: string; label: string }[] {
  const out = [];
  for (let i = 0; i <= ahead; i += 1) {
    const d = new Date(today.getFullYear(), today.getMonth(), today.getDate() + i);
    const name = i === 0 ? "Today · " : i === 1 ? "Tomorrow · " : "";
    out.push({ value: isoDay(d), label: name + dayLabel(d) });
  }
  return out;
}

export function clock(d: Date): string {
  return new Intl.DateTimeFormat("en-AU", { hour: "numeric", minute: "2-digit" }).format(d);
}
```

**Step 4: Run the tests**

Run: `npm test`
Expected: `26 passed`.

**Step 5: Commit**

```bash
git add apps/desktop/src/lib apps/desktop/src/test
git commit -m "Work out where a drop lands and which days to plan"
```

### Task 22: Sign in, the map, and the drawers

**Files:**
- Create: `apps/desktop/src/auth/AuthContext.tsx`, `src/auth/RequireAuth.tsx`
- Create: `apps/desktop/src/ui/icons.tsx`, `src/ui/Drawer.tsx`
- Create: `apps/desktop/src/pages/SignIn.tsx`, `src/pages/MapHome.tsx`, `src/pages/Later.tsx`, `src/pages/PlanRuns.tsx` (a stand-in until Task 23)
- Create: `apps/desktop/src/App.tsx`
- Create: `apps/desktop/src/test/harness.tsx`, `src/test/SignIn.test.tsx`, `src/test/MapHome.test.tsx`

**Step 1: Write the failing tests**

`apps/desktop/src/test/harness.tsx` (the whole app, signed in as a dispatcher, with `fetch` stubbed):
```tsx
import { render } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { api } from "../api/client";
import { AppRoutes } from "../App";
import { AuthProvider } from "../auth/AuthContext";

export function json(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

export interface Call { method: string; url: string; body: unknown }
type Handler = (method: string, url: string, body: unknown) => Response | null | Promise<Response | null>;

/** Signed in as a dispatcher; `more` answers everything else, or it is a 404. */
export function stubApi(more: Handler = () => null) {
  const calls: Call[] = [];
  const fetchMock = vi.fn(async (url: string, init: RequestInit = {}) => {
    const method = init.method ?? "GET";
    const body = init.body ? JSON.parse(init.body as string) : undefined;
    calls.push({ method, url, body });
    if (url === "/v1/auth/me") {
      return json(200, { kind: "user", id: 1, name: "dee", display_name: "Dee Dispatch", role: "dispatcher",
                         scopes: ["fleet:read", "consignments:read", "runs:read", "runs:write"] });
    }
    if (url === "/v1/auth/logout") return new Response(null, { status: 204 });
    const answer = await more(method, url, body);
    return answer ?? json(404, { detail: `no stub for ${method} ${url}` });
  });
  vi.stubGlobal("fetch", fetchMock);
  return { calls, fetchMock };
}

function Where() {
  return <output data-testid="where">{useLocation().pathname}</output>;
}

export function renderApp(path: string) {
  api.setSession({ token: "t", refresh_token: "r", expires_in: 900 });
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AuthProvider>
        <AppRoutes />
        <Where />
      </AuthProvider>
    </MemoryRouter>,
  );
}

export function path(url: string): string {
  return url.split("?")[0];
}
```

`apps/desktop/src/test/SignIn.test.tsx`:
```tsx
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { api } from "../api/client";
import { AppRoutes } from "../App";
import { AuthProvider } from "../auth/AuthContext";
import { json } from "./harness";

function signedOut() {
  api.setSession(null);
  return render(<MemoryRouter initialEntries={["/"]}><AuthProvider><AppRoutes /></AuthProvider></MemoryRouter>);
}

describe("sign in", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("sends someone with no session to sign in, then to the map", async () => {
    const fetchMock = vi.fn(async (url: string) => {
      if (url === "/v1/auth/login") return json(200, { token: "t", refresh_token: "r", expires_in: 900 });
      if (url === "/v1/auth/me") return json(200, { kind: "user", id: 1, name: "dee", display_name: "Dee Dispatch",
                                                    role: "dispatcher", scopes: [] });
      if (url.startsWith("/v1/consignments")) return json(200, { items: [], total: 3 });
      return json(404, {});
    });
    vi.stubGlobal("fetch", fetchMock);
    signedOut();
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Username"), "dee");
    await user.type(screen.getByLabelText("Password"), "correct horse battery");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("navigation", { name: "Main drawers" })).toBeInTheDocument();
    const login = fetchMock.mock.calls.find((c) => c[0] === "/v1/auth/login") as unknown as [string, RequestInit];
    expect(JSON.parse(login[1].body as string)).toEqual({ username: "dee", password: "correct horse battery" });
  });

  it("says how many tries are left after a wrong password", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json(401, { detail: "wrong username or password",
                                                         code: "wrong_password", tries_left: 3 })));
    signedOut();
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Username"), "dee");
    await user.type(screen.getByLabelText("Password"), "nope");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Wrong username or password. 3 tries left before the account locks.");
  });
});
```

`apps/desktop/src/test/MapHome.test.tsx`:
```tsx
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { json, path, renderApp, stubApi } from "./harness";

describe("the map", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("shows the day's numbers and opens drawers from the bar", async () => {
    stubApi((_m, url) => {
      if (path(url) !== "/v1/consignments") return null;
      const status = new URLSearchParams(url.split("?")[1]).get("status");
      return json(200, { items: [], total: { packed: 14, departed: 21, delivered: 0 }[status ?? ""] ?? 0 });
    });
    renderApp("/");
    const nav = await screen.findByRole("navigation", { name: "Main drawers" });
    await waitFor(() => expect(nav).toHaveTextContent("Plan runs14"));
    expect(screen.getByText("21")).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("link", { name: /Fleet/ }));
    expect(screen.getByTestId("where")).toHaveTextContent("/fleet");
    expect(screen.getByText(/This screen arrives with build step 4/)).toBeInTheDocument();
  });

  it("signs out from the account button", async () => {
    const { calls } = stubApi(() => json(200, { items: [], total: 0 }));
    renderApp("/");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Account: Dee Dispatch" }));
    await user.click(screen.getByRole("button", { name: "Sign out" }));
    expect(await screen.findByRole("button", { name: "Sign in" })).toBeInTheDocument();
    expect(calls.some((c) => c.url === "/v1/auth/logout")).toBe(true);
  });
});
```

**Step 2: Run them to see them fail**

Run: `npm test`
Expected: FAIL — `Failed to resolve import "../App"`.

**Step 3: The session**

`apps/desktop/src/auth/AuthContext.tsx`:
```tsx
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, type Session } from "../api/client";
import type { Me } from "../api/types";

interface AuthState {
  ready: boolean;
  user: Me | null;
  signIn: (username: string, password: string) => Promise<void>;
  signOut: () => Promise<void>;
  can: (scope: string) => boolean;
}

const Ctx = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [ready, setReady] = useState(false);
  const [user, setUser] = useState<Me | null>(null);

  const loadMe = useCallback(async () => {
    setUser(await api.get<Me>("/v1/auth/me"));
  }, []);

  useEffect(() => {
    api.onSignedOut = () => setUser(null);
    (async () => {
      try {
        // a session handed in already (a test, say), or one to pick up after a reload
        if (api.session?.token || (await api.resume())) await loadMe();
      } catch {
        api.setSession(null);
      } finally {
        setReady(true);
      }
    })();
  }, [loadMe]);

  const signIn = useCallback(async (username: string, password: string) => {
    api.setSession(await api.post<Session>("/v1/auth/login", { username, password }));
    await loadMe();
  }, [loadMe]);

  const signOut = useCallback(async () => {
    const refresh = api.session?.refresh_token;
    try {
      if (refresh) await api.post("/v1/auth/logout", { refresh_token: refresh });
    } finally {
      api.setSession(null);
      setUser(null);
    }
  }, []);

  const can = useCallback((scope: string) => {
    if (!user) return false;
    const area = scope.split(":")[0];
    return user.scopes.includes("*") || user.scopes.includes(scope) || user.scopes.includes(`${area}:*`);
  }, [user]);

  const value = useMemo(() => ({ ready, user, signIn, signOut, can }), [ready, user, signIn, signOut, can]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useAuth outside AuthProvider");
  return ctx;
}
```

`apps/desktop/src/auth/RequireAuth.tsx`:
```tsx
import { Navigate, Outlet } from "react-router-dom";
import { useAuth } from "./AuthContext";

export function RequireAuth() {
  const { ready, user } = useAuth();
  if (!ready) return <div className="p-6 text-sm text-muted">Loading…</div>;
  if (!user) return <Navigate to="/sign-in" replace />;
  return <Outlet />;
}
```

**Step 4: The pieces**

`apps/desktop/src/ui/icons.tsx` (the drawn screens' line icons):
```tsx
/** The few line icons the screens use, drawn as in design/screens. */
const base = { fill: "none", stroke: "currentColor", strokeLinecap: "round", strokeLinejoin: "round" } as const;

export function Logo({ size = 32 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true">
      <path d="M16 3 28 10 16 17 4 10Z" fill="#5BC28F" />
      <path d="M4 10 16 17V30L4 23Z" fill="#1F8A5B" />
      <path d="M28 10 16 17V30L28 23Z" fill="#146B45" />
      <path d="M10 6.5 22 13.5v4" fill="none" stroke="#E7F4EC" strokeWidth="2" />
    </svg>
  );
}

export function Icon({ name, size = 18, width = 2 }: { name: keyof typeof PATHS; size?: number; width?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" strokeWidth={width} aria-hidden="true" {...base}>
      {PATHS[name]}
    </svg>
  );
}

const PATHS = {
  search: <><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></>,
  close: <path d="M6 6l12 12M18 6 6 18" />,
  plus: <path d="M12 5v14M5 12h14" />,
  print: <><path d="M7 9V3h10v6M7 17H4v-7h16v7h-3" /><path d="M7 14h10v7H7Z" /></>,
  grip: <><circle cx="9" cy="6" r="1.2" /><circle cx="15" cy="6" r="1.2" /><circle cx="9" cy="12" r="1.2" /><circle cx="15" cy="12" r="1.2" /><circle cx="9" cy="18" r="1.2" /><circle cx="15" cy="18" r="1.2" /></>,
  up: <path d="m6 15 6-6 6 6" />,
  down: <path d="m6 9 6 6 6-6" />,
  info: <><circle cx="12" cy="12" r="9" /><path d="M12 11v5M12 8h.01" /></>,
  warn: <><path d="M12 3 2 20h20Z" /><path d="M12 10v4M12 17h.01" /></>,
  ban: <><circle cx="12" cy="12" r="9" /><path d="m5.6 5.6 12.8 12.8" /></>,
  route: <><circle cx="6" cy="19" r="2" /><circle cx="18" cy="5" r="2" /><path d="M8 19h8a3.5 3.5 0 0 0 0-7H8a3.5 3.5 0 0 1 0-7h8" /></>,
  box: <><path d="M12 3 20 7.5v9L12 21 4 16.5v-9Z" /><path d="m4 7.5 8 4.5 8-4.5M12 12v9" /></>,
  hand: <><path d="M3 11l4-4 4 2 3-2 7 5-3 3" /><path d="M7 7v6l4 4 3-1 4-2" /></>,
  truck: <><path d="M2 6h12v10H2Z" /><path d="M14 9h4.5L22 12.5V16h-8Z" /><circle cx="6.5" cy="17.5" r="2" /><circle cx="17.5" cy="17.5" r="2" /></>,
  go: <path d="M5 12h14M13 6l6 6-6 6" />,
};
```

`apps/desktop/src/ui/Drawer.tsx`:
```tsx
import { useEffect, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { Icon } from "./icons";

/** A screen over the map. Escape or the close button goes back to the map. */
export function Drawer({ label, wide, head, children }: {
  label: string; wide?: boolean; head: ReactNode; children: ReactNode;
}) {
  const navigate = useNavigate();
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") navigate("/"); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [navigate]);
  return (
    <>
      <div className="dim" aria-hidden="true" onClick={() => navigate("/")} />
      <section className={`drawer${wide ? " wide" : ""}`} aria-label={label} role="dialog">
        <header className="dhead">
          <button type="button" className="closebtn" aria-label="Back to the map" onClick={() => navigate("/")}>
            <Icon name="close" size={20} width={2.4} />
          </button>
          {head}
        </header>
        <div className="dbody">{children}</div>
      </section>
    </>
  );
}
```

`apps/desktop/src/pages/Later.tsx`:
```tsx
import { Drawer } from "../ui/Drawer";

/** A drawer whose screen comes with a later build step. Says so, plainly. */
export function Later({ title, step, what }: { title: string; step: string; what: string }) {
  return (
    <Drawer label={title} head={<div style={{ flex: 1 }}><h1>{title}</h1><div className="sub">{what}</div></div>}>
      <div className="msg a" role="status">This screen arrives with {step}. Until then, the API has what there is.</div>
    </Drawer>
  );
}
```

**Step 5: The screens**

`apps/desktop/src/pages/SignIn.tsx`:
```tsx
import { useState, type FormEvent } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { ApiError } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { Logo } from "../ui/icons";

/** What a refusal means, in a sentence. The API says how many tries are left. */
function refusal(e: unknown): string {
  if (!(e instanceof ApiError)) return "Simple TMS could not be reached. Check the connection and try again.";
  if (e.code === "locked") return e.message.charAt(0).toUpperCase() + e.message.slice(1) + ".";
  if (e.status === 401) {
    const left = (e.body as { tries_left?: number })?.tries_left;
    return left !== undefined
      ? `Wrong username or password. ${left} ${left === 1 ? "try" : "tries"} left before the account locks.`
      : "Wrong username or password.";
  }
  return e.message;
}

export function SignIn() {
  const { user, signIn } = useAuth();
  const navigate = useNavigate();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (user) return <Navigate to="/" replace />;

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await signIn(username.trim(), password);
      navigate("/", { replace: true });
    } catch (err) {
      setError(refusal(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="min-h-screen grid place-items-center p-6">
      <form onSubmit={submit} className="w-full max-w-sm bg-white rounded-3xl p-8 flex flex-col gap-4"
            style={{ boxShadow: "var(--shadow-lift)" }}>
        <div className="brand"><Logo /><span>Simple TMS</span></div>
        <h1 className="m-0 text-2xl font-extrabold">Sign in</h1>
        <label className="flex flex-col gap-1 text-sm font-bold text-ink-2">Username
          <input className="field" autoComplete="username" value={username}
                 onChange={(e) => setUsername(e.target.value)} required />
        </label>
        <label className="flex flex-col gap-1 text-sm font-bold text-ink-2">Password
          <input className="field" type="password" autoComplete="current-password" value={password}
                 onChange={(e) => setPassword(e.target.value)} required />
        </label>
        {error && <div className="msg r" role="alert">{error}</div>}
        <button className="btn primary" type="submit" disabled={busy}>Sign in</button>
      </form>
    </main>
  );
}
```

`apps/desktop/src/pages/MapHome.tsx` (Main.html: the header, the region, the three numbers, the bar of drawers; the goal, selected-truck and feed panels wait for data that is real):
```tsx
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { Page } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { clock } from "../lib/days";
import { Icon, Logo } from "../ui/icons";

interface Counts { toPlan: number; onRoad: number; delivered: number }

async function count(status: string): Promise<number> {
  return (await api.get<Page<unknown>>("/v1/consignments", { status, limit: 0 })).total;
}

function initials(name: string): string {
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join("").toUpperCase() || "?";
}

/** Main.html: the region as a picture, the day's numbers over it, and the
 * drawers. Trucks moving on the map come with live location (step 5). */
export function MapHome() {
  const { user, signOut } = useAuth();
  const navigate = useNavigate();
  const [counts, setCounts] = useState<Counts | null>(null);
  const [now, setNow] = useState(() => new Date());
  const [menu, setMenu] = useState(false);
  const [q, setQ] = useState("");

  const load = useCallback(async () => {
    try {
      const [toPlan, onRoad, delivered] = await Promise.all(
        ["packed", "departed", "delivered"].map(count));
      setCounts({ toPlan, onRoad, delivered });
    } catch { /* the numbers wait for the next go */ }
  }, []);

  useEffect(() => {
    void load();
    const t = setInterval(() => { setNow(new Date()); void load(); }, 30_000);
    return () => clearInterval(t);
  }, [load]);

  function search(e: FormEvent) {
    e.preventDefault();
    navigate(`/consignments?q=${encodeURIComponent(q)}`);
  }

  const weekday = new Intl.DateTimeFormat("en-AU", { weekday: "short" }).format(now);
  return (
    <div className="app">
      <header className="hdr">
        <div className="brand"><Logo /><span>Simple TMS</span></div>
        <form className="search" role="search" onSubmit={search}>
          <Icon name="search" />
          <label className="sr" htmlFor="find">Search</label>
          <input id="find" type="search" placeholder="Find a consignment, customer or con note"
                 value={q} onChange={(e) => setQ(e.target.value)} />
        </form>
        <div className="hright">
          <span className="live"><span className="dot" style={{ width: 8, height: 8, background: "#1F8A5B" }} />
            Live · {weekday} {clock(now)}</span>
          <div style={{ position: "relative" }}>
            <button type="button" className="avatar" aria-label={`Account: ${user?.display_name}`}
                    aria-expanded={menu} onClick={() => setMenu(!menu)}>
              {initials(user?.display_name ?? "")}
            </button>
            {menu && (
              <div className="panel" style={{ position: "absolute", right: 0, top: 52, zIndex: 20, background: "#fff",
                                               borderRadius: 14, boxShadow: "var(--shadow-lift)", padding: 12, minWidth: 180 }}>
                <div className="small" style={{ marginBottom: 8 }}>{user?.display_name} · {user?.role}</div>
                <button type="button" className="btn sm" onClick={() => void signOut()}>Sign out</button>
              </div>
            )}
          </div>
        </div>
      </header>

      <main className="stage">
        <img src="/region.svg" alt="The region: the depot with its carrier bay, the roads and customer stops" />
        <div className="ov" style={{ top: 20, left: 24, width: 318 }}>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(3,minmax(0,1fr))", gap: 8 }}>
            <NavLink to="/plan" className="hud">
              <div className="eyebrow" style={{ color: "var(--color-amber-ink)" }}>To plan</div>
              <div className="v num">{counts?.toPlan ?? "–"}</div>
            </NavLink>
            <div className="hud">
              <div className="eyebrow" style={{ color: "var(--color-green)" }}>On road</div>
              <div className="v num">{counts?.onRoad ?? "–"}</div>
            </div>
            <div className="hud">
              <div className="eyebrow" style={{ color: "var(--color-ink-2)" }}>Delivered</div>
              <div className="v num">{counts?.delivered ?? "–"}</div>
            </div>
          </div>
        </div>
        <nav className="ov nav" aria-label="Main drawers" style={{ bottom: 24, left: "50%", transform: "translateX(-50%)" }}>
          <NavLink to="/plan" className={({ isActive }) => (isActive ? "on" : "")}>
            <Icon name="route" />Plan runs{counts && counts.toPlan > 0 && <i>{counts.toPlan}</i>}
          </NavLink>
          <NavLink to="/consignments" className={({ isActive }) => (isActive ? "on" : "")}><Icon name="box" />Consignments</NavLink>
          <NavLink to="/carriers" className={({ isActive }) => (isActive ? "on" : "")}><Icon name="hand" />Carriers</NavLink>
          <NavLink to="/fleet" className={({ isActive }) => (isActive ? "on" : "")}><Icon name="truck" />Fleet</NavLink>
        </nav>
      </main>
      <Outlet context={{ reloadCounts: load }} />
    </div>
  );
}
```

`apps/desktop/src/pages/PlanRuns.tsx`, a stand-in Task 23 replaces:
```tsx
import { Later } from "./Later";

export function PlanRuns() {
  return <Later title="Plan runs" step="the next task" what="Drag consignments onto runs." />;
}
```

`apps/desktop/src/App.tsx`:
```tsx
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider } from "./auth/AuthContext";
import { RequireAuth } from "./auth/RequireAuth";
import { MapHome } from "./pages/MapHome";
import { PlanRuns } from "./pages/PlanRuns";
import { Later } from "./pages/Later";
import { SignIn } from "./pages/SignIn";

/** The map is home; every other screen opens as a drawer over it. */
export function AppRoutes() {
  return (
    <Routes>
      <Route path="/sign-in" element={<SignIn />} />
      <Route element={<RequireAuth />}>
        <Route element={<MapHome />}>
          <Route index element={null} />
          <Route path="/plan" element={<PlanRuns />} />
          <Route path="/consignments" element={
            <Later title="Consignments" step="a later build step"
                   what="Search by WMS delivery, customer or con note, and see each one's history." />} />
          <Route path="/carriers" element={
            <Later title="Carriers" step="build step 6"
                   what="Today's carrier consignments, con note entry and the manifest." />} />
          <Route path="/fleet" element={
            <Later title="Fleet" step="build step 4"
                   what="Vehicles, drivers, pre-start checks, and rego and service due." />} />
        </Route>
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

export function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <AppRoutes />
      </AuthProvider>
    </BrowserRouter>
  );
}
```

**Step 6: Run the tests and the type check**

Run: `npm test && npx tsc -b`
Expected: `30 passed`, and no type errors.

**Step 7: Commit**

```bash
git add apps/desktop/src
git commit -m "Sign in to the desktop and open drawers over the map"
```

### Task 23: The Plan runs drawer

**Files:**
- Modify: `apps/desktop/src/pages/PlanRuns.tsx` (replace the stand-in)
- Create: `apps/desktop/src/test/PlanRuns.test.tsx`

**Step 1: Write the failing tests**

UI tests stay few and about behaviour: what is drawn from the API's answers, that each keyboard control and a real drag send the right request, and that refusals are said plainly.

`apps/desktop/src/test/PlanRuns.test.tsx`:
```tsx
import { createEvent, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { Card, Consignment, Run } from "../api/types";
import { json, path, renderApp, stubApi, type Call } from "./harness";

const card = (id: number, customer: string, weight_kg: string, volume_m3 = "1"): Card => ({
  id, wms_ref: `00800345${id}`, customer, suburb: "Ballarat", status: "assigned", packages: 3, cartons: 3,
  weight_kg, volume_m3,
});

const run = (id: number, driver: string, rego: string, stops: Card[], over: Partial<Run> = {}): Run => ({
  id, run_no: `R-000${id}`, date: "2026-10-14", status: "planned",
  vehicle: { rego, type: "rigid", max_kg: "4500", max_m3: "22", active: true },
  driver: { code: driver.toLowerCase(), name: driver }, created_by: "dee", created_at: "", departed_at: null,
  stops: stops.map((c, i) => ({ sequence: i + 1, consignment: c })),
  load: { weight_kg: "0", volume_m3: "0", max_kg: "4500", max_m3: "22", unknown_weight: 0, unknown_volume: 0,
          state: "ok", message: null },
  run_sheet: null, shipping: { pending: 0, done: 0, failed: 0 }, ...over,
});

const packed = (id: number, customer: string, suburb: string): Consignment => ({
  id, wms_ref: `00800345${id}`, warehouse: "BAL-WH01", owner: "DEFAULT", customer, ship_to: { suburb },
  required_by: null, status: "packed", created_at: "", weight_kg: "210", volume_m3: "1.4",
  packages: Array.from({ length: 8 }, (_, i) => ({ package_no: i + 1, type: "carton", weight_kg: null,
                                                   length_cm: null, width_cm: null, height_cm: null, sscc: null })),
});

const KIM = run(1, "Kim", "TMS041", [card(11, "Hillside Bakery", "84"), card(12, "Two Rivers Vet", "41"),
                                     card(13, "Ridge Auto Workshop", "620")]);
const MEL = run(2, "Mel", "TMS052", [card(21, "Wattle Flat Stockfeed", "1640"), card(22, "Clarkes Hill Builders", "3140")]);

function board(answers: Record<string, (call: Call) => Response | null> = {}) {
  return stubApi((method, url, body) => {
    const key = `${method} ${path(url)}`;
    if (answers[key]) return answers[key]({ method, url, body });
    if (key === "GET /v1/runs") return json(200, { items: [KIM, MEL], total: 2 });
    if (key === "GET /v1/consignments") {
      return json(200, { items: [packed(31, "Paddock Produce", "Clunes"), packed(32, "Kerbside Bikes", "Alfredton")], total: 2 });
    }
    if (key === "GET /v1/vehicles") {
      return json(200, { items: [
        { id: 1, rego: "TMS041", type: "rigid", max_kg: "4500", max_m3: "22", active: true },
        { id: 2, rego: "TMS052", type: "rigid", max_kg: "4500", max_m3: "22", active: true },
        { id: 3, rego: "TMS063", type: "van", max_kg: "1200", max_m3: "9", active: true },
        { id: 4, rego: "TMS027", type: "ute", max_kg: "600", max_m3: "3", active: false },
      ], total: 4 });
    }
    if (key === "GET /v1/drivers") return json(200, { items: [{ id: 1, code: "kim", name: "Kim", active: true }], total: 1 });
    if (method === "POST") return json(200, KIM);
    return null;
  });
}

const lane = (name: string) => screen.findByRole("region", { name });

describe("Plan runs", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("draws the tray, each run with its bars, and the truck that can't go", async () => {
    board();
    renderApp("/plan");
    const tray = await lane("Consignments to plan");
    expect(await within(tray).findByText("Paddock Produce")).toBeInTheDocument();
    expect(within(tray).getAllByText("8 ctn")).toHaveLength(2);
    expect(within(tray).getByText("From WMS delivery 0080034531")).toBeInTheDocument();

    const kim = await lane("Run R-0001");
    expect(within(kim).getByText("R-0001 · Kim")).toBeInTheDocument();
    expect(within(kim).getByText("3 stops")).toBeInTheDocument();
    expect(within(kim).getByRole("img", { name: "Weight 745 of 4,500 kg" })).toBeInTheDocument();

    const mel = await lane("Run R-0002");
    expect(within(mel).getByText("Over")).toBeInTheDocument();
    expect(within(mel).getByText("280 kg too heavy.")).toBeInTheDocument();
    expect(within(mel).getByText(/Move a stop to another run before printing the run sheet/)).toBeInTheDocument();
    expect(within(mel).getByRole("button", { name: /Leaves now/ })).toBeDisabled();

    const out = await lane("TMS027, unavailable");
    expect(within(out).getByText("Deactivated")).toBeInTheDocument();
    expect(screen.getByText(/1 truck free: TMS063/)).toBeInTheDocument();
  });

  it("puts a consignment on a run from the keyboard", async () => {
    const { calls } = board();
    renderApp("/plan");
    const user = userEvent.setup();
    await user.selectOptions(await screen.findByRole("combobox", { name: "Put Paddock Produce on a run" }), "1");
    await waitFor(() => expect(calls.find((c) => c.url === "/v1/runs/1/assign")?.body)
      .toEqual({ consignment_id: 31 }));
  });

  it("moves a stop down from the keyboard by sending the whole order", async () => {
    const { calls } = board();
    renderApp("/plan");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Move Hillside Bakery down" }));
    await waitFor(() => expect(calls.find((c) => c.url === "/v1/runs/1/reorder")?.body)
      .toEqual({ consignment_ids: [12, 11, 13] }));
    expect(screen.getByRole("button", { name: "Move Hillside Bakery up" })).toBeDisabled();
  });

  it("sends a stop back to the tray from the keyboard", async () => {
    const { calls } = board();
    renderApp("/plan");
    await userEvent.setup().selectOptions(
      await screen.findByRole("combobox", { name: "Move Two Rivers Vet to" }), "tray");
    await waitFor(() => expect(calls.find((c) => c.url === "/v1/runs/1/unassign")?.body)
      .toEqual({ consignment_id: 12 }));
  });

  it("drags a consignment from the tray onto a run, at the end", async () => {
    const { calls } = board();
    renderApp("/plan");
    const tray = await lane("Consignments to plan");
    const cardEl = (await within(tray).findByText("Kerbside Bikes")).closest(".con")!;
    const kim = await lane("Run R-0001");
    const data = { setData: vi.fn(), getData: vi.fn(), effectAllowed: "" };
    fireEvent.dragStart(cardEl, { dataTransfer: data });
    const over = createEvent.dragOver(kim, { dataTransfer: data });
    fireEvent(kim, over);
    expect(await within(kim).findByText("Drop here · Kerbside Bikes fits")).toBeInTheDocument();
    fireEvent.drop(kim, { dataTransfer: data });
    await waitFor(() => expect(calls.find((c) => c.url === "/v1/runs/1/assign")?.body)
      .toEqual({ consignment_id: 32, position: 4 }));
  });

  it("says plainly when someone else changed the run meanwhile", async () => {
    board({
      "POST /v1/runs/1/reorder": () => json(409, {
        code: "stale", detail: "R-0001 changed while you were looking; it has been reloaded, so try that again" }),
    });
    renderApp("/plan");
    await userEvent.setup().click(await screen.findByRole("button", { name: "Move Two Rivers Vet up" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "R-0001 changed while you were looking; it has been reloaded, so try that again");
  });

  it("prints every run sheet it can and says what it could not", async () => {
    const { calls } = board({
      "POST /v1/runs/2/print": () => json(409, { code: "over_capacity",
        detail: "R-0002: 280 kg too heavy. Move a stop to another run before printing the run sheet." }),
      "POST /v1/runs/1/print": () => json(202, { status: "pending" }),
    });
    renderApp("/plan");
    await userEvent.setup().click(await screen.findByRole("button", { name: "Print run sheets" }));
    expect(await screen.findByText(/Sent 1 run sheet to Platen\. R-0002: 280 kg too heavy/)).toBeInTheDocument();
    expect(calls.filter((c) => c.url.endsWith("/print")).map((c) => c.url)).toEqual(["/v1/runs/1/print", "/v1/runs/2/print"]);
  });

  it("asks before a run leaves, then departs it", async () => {
    const { calls } = board();
    const ask = vi.spyOn(window, "confirm").mockReturnValue(true);
    renderApp("/plan");
    await userEvent.setup().click(within(await lane("Run R-0001")).getByRole("button", { name: /Leaves now/ }));
    expect(ask).toHaveBeenCalledWith("R-0001 leaves now? Each consignment on it is marked shipped in Simple WMS.");
    await waitFor(() => expect(calls.some((c) => c.url === "/v1/runs/1/depart")).toBe(true));
    ask.mockRestore();
  });
});
```

**Step 2: Run them to see them fail**

Run: `npm test -- PlanRuns`
Expected: FAIL — `Unable to find role="region" and name "Consignments to plan"`.

**Step 3: The drawer**

`apps/desktop/src/pages/PlanRuns.tsx`:
```tsx
import { useCallback, useEffect, useMemo, useState, type DragEvent, type FormEvent } from "react";
import { Link, useOutletContext } from "react-router-dom";
import { api, ApiError } from "../api/client";
import type { Card, Consignment, Driver, Page, Run, Vehicle } from "../api/types";
import { clock, dayOptions } from "../lib/days";
import { plain, toMicro } from "../lib/decimal";
import { afterAdding, loadOf, type Gauge } from "../lib/load";
import { cardOf, dropPosition, kg, m3, moved, packagesLabel, positionWithout, runColour } from "../lib/plan";
import { Drawer } from "../ui/Drawer";
import { Icon } from "../ui/icons";

type Note = { tone: "r" | "a" | "g"; text: string } | null;
interface Dragging { card: Card; from: number | null; index: number }
interface Target { run: number | null; position: number }

function said(e: unknown): string {
  return e instanceof ApiError ? e.message : "Simple TMS could not be reached. Check the connection and try again.";
}

function Meter({ label, unit, g }: { label: string; unit: string; g: Gauge }) {
  const tone = g.state === "over" ? " r" : g.state === "near" ? " a" : "";
  return (
    <div className={`meter${tone}`}>
      <div className="row"><span>{label}</span>
        <span className="num"><b>{plain(g.used)}</b> / {plain(g.max)} {unit}</span></div>
      <div className="track" role="img" aria-label={`${label} ${plain(g.used)} of ${plain(g.max)} ${unit}`}>
        <span style={{ width: `${Math.round(g.fill * 100)}%` }} />
      </div>
    </div>
  );
}

/** A truck that can't take a run. Deactivated for now; grounded and overdue
 * arrive with fleet care (step 4) and use the same slot. */
function Unavailable({ v, reason, detail }: { v: Vehicle; reason: string; detail: string }) {
  return (
    <section className="lane out" aria-label={`${v.rego}, unavailable`}>
      <div className="lhead"><span className="swatch" style={{ background: "#A9B3AE" }} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div className="t" style={{ color: "var(--color-ink-2)" }}>{v.rego}</div>
          <div className="s">{v.type} · {plain(toMicro(v.max_kg))} kg</div></div></div>
      <span className="pill p-red" style={{ alignSelf: "flex-start" }}><Icon name="ban" size={13} width={2.4} />{reason}</span>
      <div className="small">{detail}</div>
      <Link to="/fleet" style={{ fontSize: 13, fontWeight: 700 }}>Open in Fleet</Link>
    </section>
  );
}

function sheetLine(run: Run): Note {
  const s = run.run_sheet;
  if (!s) return null;
  if (s.status === "printed") return { tone: "g", text: `Run sheet printed on ${s.printer}.` };
  if (s.status === "failed") return { tone: "r", text: `Run sheet didn't print: ${s.last_error ?? "Platen said it failed"}` };
  if (s.status === "accepted") return { tone: "g", text: `Run sheet sent to ${s.printer}.` };
  return s.last_error
    ? { tone: "a", text: `Run sheet waiting to go to Platen: ${s.last_error}` }
    : { tone: "a", text: "Run sheet waiting to go to Platen." };
}

/** PlanRuns.html: the tray of consignments to plan, a lane per run, and the
 * trucks that can't take one. Drag to plan; every drag has a keyboard way too. */
export function PlanRuns() {
  const { reloadCounts } = useOutletContext<{ reloadCounts: () => void }>();
  const days = useMemo(() => dayOptions(new Date()), []);
  const [day, setDay] = useState(days[1].value);
  const [runs, setRuns] = useState<Run[]>([]);
  const [tray, setTray] = useState<Card[]>([]);
  const [vehicles, setVehicles] = useState<Vehicle[]>([]);
  const [drivers, setDrivers] = useState<Driver[]>([]);
  const [filter, setFilter] = useState("");
  const [note, setNote] = useState<Note>(null);
  const [savedAt, setSavedAt] = useState<Date | null>(null);
  const [busy, setBusy] = useState(false);
  const [adding, setAdding] = useState(false);
  const [drag, setDrag] = useState<Dragging | null>(null);
  const [target, setTarget] = useState<Target | null>(null);

  const load = useCallback(async () => {
    const [r, t, v, d] = await Promise.all([
      api.get<Page<Run>>("/v1/runs", { date: day }),
      api.get<Page<Consignment>>("/v1/consignments", { status: "packed", limit: 2000 }),
      api.get<Page<Vehicle>>("/v1/vehicles"),
      api.get<Page<Driver>>("/v1/drivers", { active: true }),
    ]);
    setRuns(r.items);
    setTray(t.items.map(cardOf));
    setVehicles(v.items);
    setDrivers(d.items);
  }, [day]);

  useEffect(() => {
    load().catch((e) => setNote({ tone: "r", text: said(e) }));
    // what another dispatcher has done turns up within 20 s
    const t = setInterval(() => { load().catch(() => undefined); }, 20_000);
    return () => clearInterval(t);
  }, [load]);

  async function act(fn: () => Promise<unknown>, done: string | null = null) {
    setBusy(true);
    try {
      await fn();
      setSavedAt(new Date());
      setNote(done ? { tone: "g", text: done } : null);
    } catch (e) {
      setNote({ tone: "r", text: said(e) });
    } finally {
      await load().catch(() => undefined);
      reloadCounts();
      setBusy(false);
    }
  }

  const planned = runs.filter((r) => r.status === "planned" && r.vehicle.active);
  const assign = (runId: number, c: Card, position?: number) =>
    act(() => api.post(`/v1/runs/${runId}/assign`, { consignment_id: c.id, position }));
  const unassign = (runId: number, c: Card) =>
    act(() => api.post(`/v1/runs/${runId}/unassign`, { consignment_id: c.id }));
  const reorder = (run: Run, ids: number[]) =>
    act(() => api.post(`/v1/runs/${run.id}/reorder`, { consignment_ids: ids }));

  function depart(run: Run) {
    if (!window.confirm(`${run.run_no} leaves now? Each consignment on it is marked shipped in Simple WMS.`)) return;
    void act(() => api.post(`/v1/runs/${run.id}/depart`), `${run.run_no} has left. Simple WMS is being told.`);
  }

  async function printAll() {
    const ready = runs.filter((r) => r.status !== "cancelled" && r.stops.length > 0);
    if (ready.length === 0) { setNote({ tone: "a", text: "There are no runs with stops to print." }); return; }
    setBusy(true);
    const sent: string[] = [];
    const refused: string[] = [];
    for (const r of ready) {
      try {
        await api.post(`/v1/runs/${r.id}/print`);
        sent.push(r.run_no);
      } catch (e) {
        refused.push(said(e));
      }
    }
    const parts = [];
    if (sent.length) parts.push(`Sent ${sent.length} run ${sent.length === 1 ? "sheet" : "sheets"} to Platen.`);
    parts.push(...new Set(refused));
    setNote({ tone: refused.length ? (sent.length ? "a" : "r") : "g", text: parts.join(" ") });
    await load().catch(() => undefined);
    setBusy(false);
  }

  // ---- dragging -----------------------------------------------------------
  function startDrag(e: DragEvent, d: Dragging) {
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", String(d.card.id));
    setDrag(d);
  }
  function endDrag() { setDrag(null); setTarget(null); }

  function overRun(e: DragEvent, run: Run) {
    if (!drag) return;
    e.preventDefault();
    const el = (e.target as HTMLElement).closest<HTMLElement>("[data-index]");
    const position = el
      ? dropPosition(Number(el.dataset.index), e.clientY, el.getBoundingClientRect().top,
                     el.getBoundingClientRect().height)
      : run.stops.length + 1;
    if (target?.run !== run.id || target.position !== position) setTarget({ run: run.id, position });
  }

  function dropOnRun(e: DragEvent, run: Run) {
    e.preventDefault();
    const d = drag;
    const position = target?.run === run.id ? target.position : run.stops.length + 1;
    endDrag();
    if (!d) return;
    if (d.from === run.id) {
      const ids = run.stops.map((s) => s.consignment.id);
      const to = positionWithout(d.index, position) - 1;
      if (to !== d.index) void reorder(run, moved(ids, d.index, to));
    } else {
      void assign(run.id, d.card, position);
    }
  }

  function dropOnTray(e: DragEvent) {
    e.preventDefault();
    const d = drag;
    endDrag();
    if (d && d.from !== null) void unassign(d.from, d.card);
  }

  function dropHint(run: Run) {
    if (!drag || target?.run !== run.id) return null;
    if (drag.from === run.id) return <div className="drop">Drop here</div>;
    const state = afterAdding(run.stops.map((s) => s.consignment), drag.card, run.vehicle.max_kg, run.vehicle.max_m3);
    if (state === "over") return <div className="drop r">Drop here · {drag.card.customer} won't fit</div>;
    return <div className="drop">Drop here · {drag.card.customer} fits{state === "near" ? ", nearly full" : ""}</div>;
  }

  // ---- adding a run ---------------------------------------------------------
  const busyTrucks = new Set(runs.map((r) => r.vehicle.rego));
  const free = vehicles.filter((v) => v.active && !busyTrucks.has(v.rego));
  const unavailable = vehicles.filter((v) => !v.active);

  function addRun(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    setAdding(false);
    void act(() => api.post("/v1/runs", { date: day, vehicle: form.get("vehicle"), driver: form.get("driver") }));
  }

  // ---- drawing --------------------------------------------------------------
  const shown = tray.filter((c) => {
    const q = filter.trim().toLowerCase();
    return !q || c.customer.toLowerCase().includes(q) || (c.suburb ?? "").toLowerCase().includes(q);
  });
  const columns = `250px repeat(${runs.length}, minmax(210px, 1fr))${unavailable.length ? " 172px" : ""}`;
  const minWidth = 262 + runs.length * 222 + (unavailable.length ? 184 : 0);

  return (
    <Drawer label="Plan runs" wide head={
      <>
        <div style={{ flex: 1, minWidth: 200 }}>
          <div className="eyebrow" style={{ color: "var(--color-green)" }}>Own fleet</div>
          <h1>Plan runs</h1>
          <div className="sub">Drag consignments onto a run, then drag stops into order. You know the roads, so the order is yours.</div>
        </div>
        <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13, fontWeight: 700, color: "var(--color-ink-2)" }}>Day
          <select className="field" style={{ width: "auto", fontWeight: 700 }} value={day} onChange={(e) => setDay(e.target.value)}>
            {days.map((d) => <option key={d.value} value={d.value}>{d.label}</option>)}
          </select>
        </label>
        <span className="small">
          {savedAt ? `Saved ${clock(savedAt)} · ` : ""}
          {free.length === 0 ? "No truck free" : `${free.length} ${free.length === 1 ? "truck" : "trucks"} free: ${free.map((v) => v.rego).join(", ")}`}
        </span>
        <button type="button" className="btn" onClick={() => setAdding(!adding)} disabled={busy}><Icon name="plus" size={16} width={2.6} />Add a run</button>
        <button type="button" className="btn primary" onClick={() => void printAll()} disabled={busy}><Icon name="print" size={16} />Print run sheets</button>
      </>
    }>
      {adding && (
        <form className="lane" style={{ flexDirection: "row", flexWrap: "wrap", alignItems: "flex-end", gap: 12 }} onSubmit={addRun} aria-label="Add a run">
          <label className="small" style={{ display: "flex", flexDirection: "column", gap: 4, fontWeight: 700 }}>Truck
            <select className="field" name="vehicle" required>
              {free.map((v) => <option key={v.rego} value={v.rego}>{v.rego} · {v.type} · {plain(toMicro(v.max_kg))} kg</option>)}
            </select></label>
          <label className="small" style={{ display: "flex", flexDirection: "column", gap: 4, fontWeight: 700 }}>Driver
            <select className="field" name="driver" required>
              {drivers.map((d) => <option key={d.code} value={d.code}>{d.name} ({d.code})</option>)}
            </select></label>
          <button className="btn primary" type="submit" disabled={free.length === 0 || drivers.length === 0}>Add the run</button>
          <button className="btn" type="button" onClick={() => setAdding(false)}>Cancel</button>
          {free.length === 0 && <span className="small">Every active truck has a run on this day.</span>}
        </form>
      )}

      {note && <div className={`msg ${note.tone}`} role={note.tone === "r" ? "alert" : "status"}>{note.text}</div>}

      <div className="boardwrap"><div className="board" style={{ gridTemplateColumns: columns, minWidth }}>
        <section className={`lane tray${drag?.from != null && target?.run === null ? " target" : ""}`} aria-label="Consignments to plan"
                 onDragOver={(e) => { if (drag?.from != null) { e.preventDefault(); setTarget({ run: null, position: 0 }); } }}
                 onDrop={dropOnTray}>
          <div className="lhead"><span className="t" style={{ flex: 1 }}>To plan</span><span className="pill p-amber">{tray.length}</span></div>
          <label className="search" style={{ height: 40, background: "#fff" }}><Icon name="search" size={16} />
            <span className="sr">Filter consignments</span>
            <input type="search" placeholder="Customer or suburb" value={filter} onChange={(e) => setFilter(e.target.value)} /></label>
          {drag?.from != null && <div className="drop">Drop here to take it off its run</div>}
          {shown.map((c) => (
            <div key={c.id} className={`con${drag?.card.id === c.id ? " dragging" : ""}`} draggable tabIndex={0}
                 onDragStart={(e) => startDrag(e, { card: c, from: null, index: -1 })} onDragEnd={endDrag}>
              <span style={{ width: 22, display: "grid", placeItems: "center", color: "#9AA8A0" }} aria-hidden="true"><Icon name="grip" width={2.4} /></span>
              <div className="c"><b>{c.customer}</b><span className="small">{c.suburb}</span>
                <div className="meta num"><span>{packagesLabel(c)}</span><span>{kg(c)}</span><span>{m3(c)}</span></div>
                <div className="ref">From WMS delivery {c.wms_ref}</div>
              </div>
              <div className="kb">
                <select aria-label={`Put ${c.customer} on a run`} value="" disabled={busy || planned.length === 0}
                        onChange={(e) => { if (e.target.value) void assign(Number(e.target.value), c); }}>
                  <option value="">Put on a run…</option>
                  {planned.map((r) => <option key={r.id} value={r.id}>{r.run_no} · {r.driver.name}</option>)}
                </select>
              </div>
            </div>
          ))}
          {tray.length === 0 && <div className="small" style={{ textAlign: "center" }}>Nothing to plan. Packed deliveries from Simple WMS land here.</div>}
        </section>

        {runs.map((run, i) => {
          const colour = runColour(i);
          const open = run.status === "planned";
          const l = loadOf(run.stops.map((s) => s.consignment), run.vehicle.max_kg, run.vehicle.max_m3);
          const ids = run.stops.map((s) => s.consignment.id);
          const others = planned.filter((r) => r.id !== run.id);
          const sheet = sheetLine(run);
          const pill = !open ? <span className="pill p-grey">Departed</span>
            : l.state === "over" ? <span className="pill p-red">Over</span>
            : l.state === "near" ? <span className="pill p-amber">Nearly full</span>
            : <span className="pill p-green">{run.stops.length} {run.stops.length === 1 ? "stop" : "stops"}</span>;
          const hint = dropHint(run);
          const hintAt = target?.run === run.id ? target.position - 1 : -1;
          return (
            <section key={run.id} aria-label={`Run ${run.run_no}`}
                     className={`lane${l.state === "over" ? " over" : l.state === "near" ? " near" : ""}${hint ? " target" : ""}`}
                     onDragOver={open && run.vehicle.active ? (e) => overRun(e, run) : undefined}
                     onDrop={open && run.vehicle.active ? (e) => dropOnRun(e, run) : undefined}>
              <div className="lhead"><span className="swatch" style={{ background: colour }} />
                <div style={{ flex: 1, minWidth: 0 }}><div className="t">{run.run_no} · {run.driver.name}</div>
                  <div className="s">{run.vehicle.rego} · {run.vehicle.type}</div></div>{pill}</div>
              <Meter label="Weight" unit="kg" g={l.weight} />
              <Meter label="Volume" unit="m³" g={l.volume} />
              {l.state === "over" && <div className="msg r"><Icon name="warn" size={14} width={2.4} /><span><b>{l.headline}</b> {l.message}</span></div>}
              {run.stops.map((s, j) => (
                <div key={s.consignment.id}>
                  {j === hintAt && hint}
                  <div className={`stop${drag?.card.id === s.consignment.id ? " dragging" : ""}`} data-index={j} tabIndex={open ? 0 : undefined}
                       draggable={open} onDragStart={(e) => startDrag(e, { card: s.consignment, from: run.id, index: j })} onDragEnd={endDrag}>
                    {open && <span className="h" aria-hidden="true"><Icon name="grip" width={2.4} /></span>}
                    <span className="n" style={{ background: colour }}>{s.sequence}</span>
                    <span className="c"><b>{s.consignment.customer}</b>
                      <span>{[s.consignment.suburb, packagesLabel(s.consignment), kg(s.consignment)].filter(Boolean)
                        .map((part, k) => <span key={k}>{k > 0 && " · "}<span style={{ whiteSpace: "nowrap" }}>{part}</span></span>)}</span></span>
                    {open && (
                      <span className="kb">
                        <button type="button" aria-label={`Move ${s.consignment.customer} up`} disabled={busy || j === 0}
                                onClick={() => void reorder(run, moved(ids, j, j - 1))}><Icon name="up" size={14} /></button>
                        <button type="button" aria-label={`Move ${s.consignment.customer} down`} disabled={busy || j === ids.length - 1}
                                onClick={() => void reorder(run, moved(ids, j, j + 1))}><Icon name="down" size={14} /></button>
                        <select aria-label={`Move ${s.consignment.customer} to`} value="" disabled={busy}
                                onChange={(e) => {
                                  const to = e.target.value;
                                  if (to === "tray") void unassign(run.id, s.consignment);
                                  else if (to) void assign(Number(to), s.consignment);
                                }}>
                          <option value="">Move to…</option>
                          <option value="tray">Back to the tray</option>
                          {others.map((r) => <option key={r.id} value={r.id}>{r.run_no} · {r.driver.name}</option>)}
                        </select>
                      </span>
                    )}
                  </div>
                </div>
              ))}
              {hintAt >= run.stops.length && hint}
              {open && run.stops.length === 0 && !hint && <div className="small" style={{ textAlign: "center", padding: 10 }}>Drag consignments here.</div>}
              {l.state === "near" && <div className="msg a"><Icon name="info" size={14} width={2.4} /><span>{l.message}</span></div>}
              {(run.load.unknown_weight > 0 || run.load.unknown_volume > 0) && (
                <div className="small">Some packages have no weight or size, so the bars may read low.</div>)}
              {sheet && <div className={`msg ${sheet.tone}`}>{sheet.text}</div>}
              {!open && (
                <div className="small">Marked shipped in Simple WMS: {run.shipping.done} of {run.shipping.done + run.shipping.pending + run.shipping.failed}.</div>)}
              {run.shipping.failed > 0 && (
                <div className="msg r"><span>{run.shipping.failed} not marked shipped in Simple WMS after five tries.</span>
                  <button type="button" className="btn sm" disabled={busy}
                          onClick={() => void act(() => api.post(`/v1/runs/${run.id}/retry-ship`), "Trying Simple WMS again.")}>Try again</button></div>)}
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                {run.stops.length > 0 && (
                  <button type="button" className="btn sm" disabled={busy || l.state === "over"}
                          onClick={() => void act(() => api.post(`/v1/runs/${run.id}/print`), `${run.run_no} run sheet sent to Platen.`)}>
                    <Icon name="print" size={14} />Print</button>)}
                {open && run.stops.length > 0 && (
                  <button type="button" className="btn sm primary" disabled={busy || l.state === "over"} onClick={() => depart(run)}>
                    Leaves now<Icon name="go" size={14} width={2.4} /></button>)}
                {open && run.stops.length === 0 && (
                  <button type="button" className="btn sm" disabled={busy}
                          onClick={() => void act(() => api.post(`/v1/runs/${run.id}/cancel`))}>Remove run</button>)}
              </div>
            </section>
          );
        })}

        {unavailable.length > 0 && (
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            {unavailable.map((v) => (
              <Unavailable key={v.rego} v={v} reason="Deactivated"
                           detail="Can't take a run until a fleet manager reactivates it." />))}
          </div>
        )}
      </div></div>
      <div className="small">Print run sheets sends each run to the dispatch printer through Platen. When a run leaves, each consignment is marked shipped in Simple WMS.</div>
    </Drawer>
  );
}
```
How it hangs together:
- The board is a grid as drawn: the tray (250 px), a lane per run, and the dashed column for trucks that can't go. Lanes use the design's classes (`lane`, `over`, `near`, `meter a`/`r`, `msg`, `drop`, `dragging`).
- A drag is the browser's own: `dragstart` remembers the card and where it came from; `dragover` on a lane works out the position from the stop under the pointer (`dropPosition`) and shows "Drop here · Paddock Produce fits" (or "fits, nearly full", or "won't fit" in red, from `afterAdding`); `drop` sends `assign`, or `reorder` with the whole order when the stop came from the same run; dropping a stop on the tray sends `unassign`.
- The keyboard way: each card and stop is focusable (`tabIndex`), and its controls (`.kb`) show on focus or hover: a "Put on a run…" menu on a card; "Move up", "Move down" and "Move to…" (other runs, or "Back to the tray") on a stop. Each sends the same request a drag would.
- After every change, and every 20 s, the board reloads from the API, so the bars, the messages and another dispatcher's moves are what the API says. A refusal (`stale`, `over_capacity`, …) shows the API's own sentence in a red message.
- "Print run sheets" prints every run with stops one by one and says what went and what was refused. Each lane has "Print" and, while planned, "Leaves now" (with a confirm); both are off while the run is over. A departed lane says how many consignments Simple WMS has been told about, and offers "Try again" when some gave up.
- `Unavailable` is the slot for a truck that can't take a run; step 4 passes "Grounded" and the failed pre-start item.

**Step 4: Run the tests and the type check**

Run: `npm test && npx tsc -b`
Expected: `38 passed`, no type errors.

**Step 5: Look at it**

With the API up (Task 16) and a dispatcher made (`docker compose exec api tms create-user --username dee --role dispatcher`), run `npm run dev`, open `http://localhost:5174`, sign in, open **Plan runs**. Compare with `design/screens/PlanRuns.html` at 1440 × 900: tray, lanes, bars, pills, messages and the dashed column should line up with the drawing. Drag a card onto a lane; tab to a stop and move it with the keyboard.

**Step 6: Commit**

```bash
git add apps/desktop/src
git commit -m "Plan runs by dragging, or from the keyboard, with live weight and volume"
```

### Task 24: Build the desktop and serve it through Caddy

**Files:**
- Modify: `docker-compose.yml` (the `caddy` volumes)
- Modify: `deploy/Caddyfile`
- Modify: `.github/workflows/ci.yml` (a `desktop` job)

**Step 1: Caddy serves the build**

In `docker-compose.yml`, the `caddy` service's volumes gain, after the Caddyfile:
```yaml
      # the desktop app, built on the host: cd apps/desktop && npm run build
      - ./apps/desktop/dist:/srv/desktop:ro
```
In `deploy/Caddyfile`, replace the last `handle` (the 404 and its comment) with:
```
	# The desktop app (build step 2). The driver app arrives with step 3.
	handle {
		root * /srv/desktop
		try_files {path} /index.html
		file_server
	}
```
`/v1/*`, `/docs*` and `/openapi.json` still go to the API first.

**Step 2: CI**

In `.github/workflows/ci.yml`, add a job before `compose`:
```yaml
  desktop:
    name: Desktop app
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: apps/desktop
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: 22
          cache: npm
          cache-dependency-path: apps/desktop/package-lock.json
      - run: npm ci
      - name: Tests
        run: npm test
      - name: Build
        run: npm run build
```

**Step 3: Build and serve**

Run:
```
(cd apps/desktop && npm run build)
docker compose up -d caddy
curl -sk https://localhost:8543/ | grep -o '<title>Simple TMS</title>'
curl -sk https://localhost:8543/plan | grep -o '<title>Simple TMS</title>'
curl -sk https://localhost:8543/v1/health
```
Expected: the title twice (the app answers its own routes) and `{"status":"ok"}`.

**Step 4: Commit**

```bash
git add docker-compose.yml deploy/Caddyfile .github/workflows/ci.yml
git commit -m "Serve the desktop through Caddy and test and build it in CI"
```

## Part D — checks

### Task 25: Every suite, and a clean Compose start

**Step 1: The suites**

Run:
```
cd "/Users/leightonlauton/Q7 Technology/Software/q7/simple-tms/api" && .venv/bin/pytest -q
cd ../apps/desktop && npm test && npm run build
cd "/Users/leightonlauton/Q7 Technology/Software/q7/platen" && .venv/bin/pytest -q
```
Expected: `184 passed`; `38 passed` and a `dist/` built; `437 passed, 17 skipped`.

**Step 2: Compose from scratch**

Run (simple-tms root):
```
docker compose up -d --build
docker compose ps
docker compose logs worker | grep "worker started"
docker compose exec -T db psql -U tms -d tms -tAc "select version_num from alembic_version"
```
Expected: `api` healthy, `worker`, `db` and `caddy` up; the worker's start line; `0002`.

**Step 3: Push**

```bash
git push -u origin step-2-own-fleet
```
Wait for CI (API, Desktop app, Docker Compose comes up) to go green before the manual check's last step, then use **superpowers:finishing-a-development-branch**.

### Task 26: Manual check: pack, plan, print, depart, shipped

Everything runs locally: Simple WMS (`127.0.0.1:8011`), Platen (`localhost:18000`) on the `per-key-callback` branch, Simple TMS (`127.0.0.1:8022`, desktop on `https://localhost:8543`). Containers reach the host as `host.docker.internal` (Docker Desktop). Keys and passwords below are placeholders in angle brackets; nothing here is committed.

**Step 1: Platen on the new branch**

```
cd "/Users/leightonlauton/Q7 Technology/Software/q7/platen"
git switch per-key-callback
docker compose up -d --build api worker
docker compose exec -T db sh -c 'psql -U $POSTGRES_USER -d $POSTGRES_DB -tAc "select version_num from alembic_version"'
```
Expected: `0014`.

**Step 2: A Simple WMS key that can ship, and one for this check**

```
cd "/Users/leightonlauton/Q7 Technology/Software/q7/simple-wms"
docker compose exec api wms create-api-client --name simple-tms-ship --scopes tasks:read,tasks:write --warehouses '*' --owner '*'
docker compose exec api wms create-api-client --name tms-check-2 --scopes 'tasks:*,integration:*' --warehouses '*' --owner '*'
```
Each prints its key once. The first replaces the `simple-tms` key (id 3, `tasks:read` only): shipping needs `tasks:write` (`api/wms/api/routes/outbound.py:370`). The second is only for packing a delivery and reading events in this check.

**Step 3: A Simple TMS key for Platen's callback**

```
cd "/Users/leightonlauton/Q7 Technology/Software/q7/simple-tms"
docker compose exec api tms create-api-key --name platen --scopes printing:write
```
Expected: `api key platen (id 1) created with printing:write`, then the key (`tms_k.…`).

**Step 4: Platen: a key that reports to Simple TMS, and the run-sheet template**

Sign in to Platen as its admin (the `PLATEN_ADMIN_PASSWORD` in Platen's `.env`, unless changed since) and keep the cookie:
```
J=$(mktemp)
curl -s -c "$J" http://localhost:18000/auth/login -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"<Platen admin password>"}'
```
Make an operator key with a callback to the TMS API, using the key from Step 3:
```
curl -s -b "$J" http://localhost:18000/keys -H 'Content-Type: application/json' -d '{
  "name": "simple-tms", "role": "operator",
  "callback_url": "http://host.docker.internal:8022",
  "callback_key": "<the tms_k key from Step 3>"}'
```
Expected: `201` JSON with `"callback_url":"http://host.docker.internal:8022"`, `"callback_key":"***"` and the Platen key in `"token"` (shown once). The same can be done on **People → Keys** in the studio (`http://localhost:18000/studio/people`): the "Report printed jobs to" and "With this key of theirs" fields; the list then shows the address under "Reports to".

The template Platen needs is **`run-sheet`, a page template** (the id must be exactly that: Simple TMS sends `"template": "run-sheet"`). Create and publish a sample:
```
curl -s -b "$J" -X PUT http://localhost:18000/pages/run-sheet -H 'Content-Type: application/json' -d @- <<'JSON'
{
  "id": "run-sheet",
  "name": "Run sheet (Simple TMS)",
  "folder": "Simple TMS",
  "paper": "A4",
  "margin_mm": 15,
  "header": [
    {"kind": "text", "value": "Run sheet {{ run_no }}", "size_pt": 18, "bold": true},
    {"kind": "text", "value": "{{ date }} · {{ vehicle }} ({{ vehicle_type }}) · {{ driver }}", "size_pt": 11},
    {"kind": "text", "value": "{{ stop_count }} stops · {{ total_kg }} of {{ max_kg }} kg · {{ total_m3 }} of {{ max_m3 }} m³", "size_pt": 10},
    {"kind": "rule"}
  ],
  "body": [
    {"kind": "barcode", "value": "{{ run_no }}", "height_mm": 10},
    {"kind": "table", "size_pt": 9, "columns": [
      {"title": "#", "value": "{{ stop }}", "width_mm": 8, "align": "right"},
      {"title": "Customer", "value": "{{ customer }}"},
      {"title": "Address", "value": "{{ address }}, {{ suburb }} {{ postcode }}"},
      {"title": "Phone", "value": "{{ phone }}", "width_mm": 26},
      {"title": "Pkgs", "value": "{{ packages }}", "width_mm": 12, "align": "right"},
      {"title": "kg", "value": "{{ weight_kg }}", "width_mm": 16, "align": "right"},
      {"title": "Signed", "value": "", "width_mm": 30}
    ]}
  ],
  "footer": [
    {"kind": "text", "value": "{{ run_no }} · page {{ page }} of {{ pages }}", "size_pt": 8, "align": "right"}
  ]
}
JSON
curl -s -b "$J" -X POST http://localhost:18000/templates/run-sheet/publish
```
Expected: the page template back as JSON, then the published version. It is the run at the top (`{{ run_no }}`, date, truck, driver, totals against the truck's limits), the run number as a barcode, a table with a row per stop (`#`, customer, address, phone, packages, kg, a column to sign) and "R-0001 · page 1 of 1" at the foot. Open it in the studio's page editor to tidy the look.

**Step 5: Point Simple TMS at both**

In `simple-tms/.env` set (keep the rest):
```
TMS_WMS_URL=http://host.docker.internal:8011
TMS_WMS_API_KEY=<the simple-tms-ship key from Step 2>
TMS_PLATEN_URL=http://host.docker.internal:18000/intake/jobs
TMS_PLATEN_KEY=<the Platen key from Step 4>
TMS_RUN_SHEET_PRINTER=office
```
`office` is the local Platen's page printer (its stand-in on host port 9103 must be listening for the sheet to come out; otherwise the run fails and that is what comes back). Then:
```
docker compose up -d api worker
docker compose logs worker | tail -2
```
Expected: `worker started, polling every 5.0s`.

**Step 6: A truck, a driver and a dispatcher**

```
docker compose exec api tms create-user --username dee --role dispatcher --password 'dispatch password'
ADMIN=$(curl -s -X POST http://127.0.0.1:8022/v1/auth/login -H 'Content-Type: application/json' \
  -d '{"username":"<TMS admin>","password":"<its password>"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')
curl -s -X POST http://127.0.0.1:8022/v1/vehicles -H "Authorization: Bearer $ADMIN" -H 'Content-Type: application/json' \
  -d '{"rego":"TMS041","type":"rigid","max_kg":"4500","max_m3":"22"}'
curl -s -X POST http://127.0.0.1:8022/v1/drivers -H "Authorization: Bearer $ADMIN" -H 'Content-Type: application/json' \
  -d '{"code":"kim","name":"Kim","pin":"2468"}'
```
(Skip what already exists from step 1's check: a `422` saying it exists is fine.)

**Step 7: Pack a delivery in Simple WMS**

As in step 1's check, with a new reference:
```
cd "/Users/leightonlauton/Q7 Technology/Software/q7/simple-tms"
WMS=http://127.0.0.1:8011 SETUP_KEY=<the tms-check-2 key> REF=TMS-RUN-1 api/.venv/bin/python - <<'PY'
import os, uuid, httpx

WMS, REF = os.environ["WMS"], os.environ["REF"]
H = {"Authorization": f"Bearer {os.environ['SETUP_KEY']}"}

def post(path, body):
    r = httpx.post(f"{WMS}/v1{path}", headers=H, timeout=30,
                   json={"message_id": str(uuid.uuid4()), **body})
    assert r.status_code < 300, f"POST {path}: {r.status_code} {r.text}"
    return r.json()

post("/deliveries", {
    "external_ref": REF, "warehouse": "BAL-WH01", "owner": "DEFAULT",
    "ship_to": {"name": "Ballarat Hardware", "address": "12 Sturt St", "suburb": "Ballarat",
                "state": "VIC", "postcode": "3350", "country": "AU", "phone": "03 5300 0000"},
    "lines": [{"delivery_line": 10, "sku": "GLV-L", "qty": 2, "uom": "PR"}]})
task = httpx.get(f"{WMS}/v1/tasks", headers=H, params={
    "warehouse": "BAL-WH01", "source_ref": REF, "type": "pick"}).json()["items"][-1]
post(f"/tasks/{task['wms_id']}/start", {"operator": "op-017"})
for line in task["lines"]:
    post(f"/tasks/{task['wms_id']}/lines/{line['line_no']}/confirm",
         {"qty": line["expected_qty"], "uom": line["uom"], "operator": "op-017"})
post(f"/deliveries/{REF}/pack", {
    "warehouse": "BAL-WH01", "packed_by": "op-017", "complete": True,
    "packages": [{"package_no": 1, "type": "carton", "weight_kg": 1.2, "length_cm": 30,
                  "width_cm": 20, "height_cm": 10,
                  "lines": [{"delivery_line": 10, "sku": "GLV-L", "qty": 2, "uom": "PR"}]}]})
print("packed", REF)
PY
```
Expected: `packed TMS-RUN-1`. If `GLV-L` has run out on the shelf, use another SKU with stock in `BAL-WH01`.

**Step 8: Plan it in the desktop**

Open `https://localhost:8543` (accept Caddy's local certificate), sign in as `dee`. The map shows **To plan** at least 1. Open **Plan runs**, pick **Today** in Day, **Add a run**: truck `TMS041`, driver Kim. Drag "Ballarat Hardware · From WMS delivery TMS-RUN-1" from the tray onto R-…; while dragging, the lane says "Drop here · Ballarat Hardware fits". It lands as stop 1, the bars read `1.2 / 4,500 kg` and `0.006 / 22 m³`, and "Saved" shows the time. Try the keyboard too: Tab to the stop, "Move to…" → "Back to the tray", then "Put on a run…" → the run. In another browser (or a private window) signed in as another user, a move made in one shows in the other within 20 s.

**Step 9: Print the run sheet**

Press **Print run sheets**. Expected: "Sent 1 run sheet to Platen." Within about 10 s the lane shows "Run sheet sent to office.", then "Run sheet printed on office." once Platen reports back (Platen's report queue, then `POST /v1/print-jobs/{job_id}/status` on the TMS with the `platen` key). Check:
- Platen's studio, **Jobs** (`http://localhost:18000/studio/jobs`): a run of `run-sheet` on Office, done; the stand-in printer's output has the PDF.
- `docker compose logs worker | grep "print job"`: `accepted by Platen`.
- `docker compose logs api | grep print-jobs`: Platen's `POST /v1/print-jobs/…/status` answered `200`.
If the lane says "Run sheet didn't print: …" or "waiting to go to Platen: Platen answered 422: template: …", the sentence says what to fix (the template id, the printer name, the key).

**Step 10: Depart**

Press **Leaves now** and confirm. Expected: "R-… has left. Simple WMS is being told."; the lane turns to "Departed" and within a few seconds says "Marked shipped in Simple WMS: 1 of 1."; **On road** on the map reads 1. Check in Simple WMS:
```
curl -s "http://127.0.0.1:8011/v1/deliveries/TMS-RUN-1" -H "Authorization: Bearer <the tms-check-2 key>" \
  | python3 -c 'import sys,json;d=json.load(sys.stdin);print(d["status"],d["carrier"],d["tracking_no"])'
curl -s "http://127.0.0.1:8011/v1/events?event_type=delivery.shipped&external_ref=TMS-RUN-1" \
  -H "Authorization: Bearer <the tms-check-2 key>"
```
Expected: `shipped Own fleet R-0001` (the run's number), and a `delivery.shipped` event for each subscriber with `"carrier":"Own fleet","tracking_no":"R-0001"` in its data (the `simple-tms` subscriber's copy `delivered`: Simple TMS answers it `ignored`). The consignment's history in Simple TMS (`GET /v1/consignments/{id}`) reads `packed`, `assigned`, `departed`.

**Step 11: Simple WMS away, then back** (optional)

Pack a second delivery (Step 7 with `REF=TMS-RUN-2`) and, in the desktop, put it on a run for **Tomorrow** (Add a run: `TMS041`, Kim). Stop Simple WMS's API (`docker compose stop api` in simple-wms), then press **Leaves now** on that run. The lane shows "Marked shipped in Simple WMS: 0 of 1", and
`docker compose exec -T db psql -U tms -d tms -c 'select path, status, attempts, next_attempt_at, last_error from outbound_call order by id'`
shows the call `pending`, `attempts` 1, due a minute on, `last_error` starting `ConnectError`. Start Simple WMS's API again (`docker compose start api`); after the next try the call is `done`, `TMS-RUN-2` is `shipped` in Simple WMS, and `GET /v1/events?event_type=delivery.shipped&external_ref=TMS-RUN-2` lists one event per subscriber: shipped once.

**Step 12: Tidy up**

In Simple WMS revoke the check key and the old read-only key (with the check key: `GET /v1/api-clients` to find their ids, then `POST /v1/api-clients/{id}/revoke` for `tms-check-2` last and `simple-tms` (id 3)). Keep `simple-tms-ship`, the Platen key and the TMS `platen` key. Once the Platen branch is merged, `git switch main && git pull` in platen and rebuild its `api` and `worker`.

Linux hosts: `host.docker.internal` does not reach ports published on `127.0.0.1`; join the stacks' networks instead (`docker network connect …`) and use the containers' names, as step 1's check describes.

Nothing to commit.
