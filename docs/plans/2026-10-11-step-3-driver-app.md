# Simple TMS step 3: the driver app — implementation plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build step 3 of `docs/plans/2026-10-10-simple-tms-design.md`: the driver's phone app (`apps/driver`, a PWA at `/driver/`) with the run one stop at a time, proof of delivery (signature, printed name, photo, time and position), failed deliveries that come back to be planned again, and an offline queue that never records a delivery twice; with the API behind it and the little the dispatcher's desktop needs to show the outcome.

**Architecture:** The API grows `inbound_message` (Simple WMS's inbound envelope: a repeated `message_id` gets the original reply and does nothing), `pod_file` (signatures and photos on a volume, each under an id the phone chose) and `pod` (one per stop: delivered or failed), and `stop.outcome`. The driver starts a run (the same `runs.depart` the dispatcher's "Leaves now" calls), delivers or fails each stop and finishes the run, each a confirmation with its own `message_id`. The phone app copies the Simple WMS scanner: a service worker keeps the app shell, the run is kept whole in `localStorage` at the start, and every confirmation goes through a retry queue that assigns its `message_id` when it is made, keeps its pictures in IndexedDB, sends them first, then the confirmation, oldest first, in order. Caddy serves the build at `/driver/` with its own CSP.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2, Alembic, pytest on a real PostgreSQL 16; Vite 6, React 19, TypeScript 5, Tailwind 4, react-router 7, Vitest 3 with Testing Library and `fake-indexeddb`; Caddy 2 and Docker Compose.

---

## Context

Paths are relative to the Simple TMS repo, `/Users/leightonlauton/Q7 Technology/Software/q7/simple-tms/`, unless a Simple WMS path is named (`/Users/leightonlauton/Q7 Technology/Software/q7/simple-wms/`). Everything in this plan was checked against the code on `main` today (`5c9df84`): the API changes were built in a scratch copy on a throwaway PostgreSQL 16 and pass the whole suite (240 tests; 194 before), each task's tests passing at that task's stage; the driver app was built in a scratch copy, type-checks, passes Vitest (39 tests) and builds; the desktop changes pass its suite (54 tests; 52 before); and the whole driver flow (sign in, start, scan and tick, signature and photo, can't deliver with no signal, a reload with no signal at all, the queue draining once, finish) was driven in headless Chrome at Pixel 7 size through a throwaway Caddy running this plan's Caddyfile in front of the scratch API.

### 1. The rules that bind this step (`CLAUDE.md`)

- **Rule 1, append-only history.** `consignment_event` has the `block_change` trigger (`api/alembic/versions/0001_skeleton.py:137-148`). Delivered and failed are new rows, never changes.
- **Rule 2, a delivery is recorded once.** Every confirmation from the driver app carries a `message_id`; a repeat returns the original reply and does nothing; a dropped signal never records twice.
- **Rule 4, the API is the only door.** The driver app is an ordinary API client with a driver session token; a partner with an API key and the same scopes can do what it does.
- **Location.** The app uses position only during a run and says so on screen; positions are deleted after the retention period (step 5 brings the job). So a position is kept on the `pod` row, never in the append-only history.
- **Git.** Subject line and, if needed, a short body. No `Co-Authored-By`, no tool or generated-by trailers, no session links.

### 2. The TMS as it is after step 2 (read before planning: the code went past the step-2 plan)

- **Driver sessions.** `POST /v1/auth/driver-login` (`api/tms/api/routes/auth.py:147-178`) gives `tms_d.…`, 12 hours, no refresh (`api/tms/services/sessions.py:24-25`). `DRIVER_SCOPES = ["fleet:read"]` (`sessions.py:35`). A new PIN (`PATCH /v1/drivers/{code}` with `{"pin"}`, scope `fleet:write`, `api/tms/api/routes/drivers.py:90-98`) ends the driver's sessions through the credential stamp. That is how the manual check sets Kim's PIN.
- **Principals.** `get_principal` (`api/tms/api/deps.py:42-74`) gives `kind` `user`, `driver` or `key`, with `id`, `name` (username, driver code, key name) and `scopes`; `require(scope)` is the only scope check so far.
- **Errors.** `Conflict(code, message)` is `409 {"detail", "code"}`; `FieldError` is `422 {"errors": [{field, message}]}`; `NotFound` `404`; there is nothing for 413 or 415 yet (`api/tms/api/errors.py`).
- **Runs.** `runs.depart` (`api/tms/services/runs.py:242-275`) takes the planning advisory lock, returns a departed run as it is, refuses `run_empty`, `vehicle_unavailable`, `over_capacity` and, through `_planned`, anything else with `run_not_planned` ("R-0001 has done; its stops can't change", which reads badly for `done`), and queues one ship call per consignment with its own `message_id`. `PLANNABLE = ("packed", "assigned")` (`:130`). `assign` finds a consignment's live stop by `removed_at is null` (`:175-176`). The partial unique index `uq_stop_consignment_id_live` is `removed_at is null` (`api/tms/models/runs.py:55-56`, `0002_runs.py:61-62`), so a consignment that came back could never go on another run as things stand. `Run.stops` is the view-only list of stops with `removed_at` null, in order (`runs.py:37-40`).
- **The run as the desktop sees it.** `RunOut` and `StopOut` in `api/tms/api/routes/runs.py:46-104`; each stop is `{ sequence, consignment: {id, wms_ref, customer, suburb, status, packages, cartons, weight_kg, volume_m3} }`.
- **Tests.** `api/tests/conftest.py`: `client`, `db` (truncates every table after each test), `dispatcher_headers`, `all_at_once`, `fresh_client`; `api/tests/runs_samples.py`: `vehicle`, `driver` (Kim, PIN `2468`), `consignment` (one carton, `100` kg), `new_run`, `assign`, `DAY = 2026-10-14`. `pyproject.toml` turns every warning into an error. 194 pass on `main`.
- **The desktop.** The tray asks for `status=packed` (`apps/desktop/src/pages/PlanRuns.tsx:118`); a departed lane shows a grey "Departed" pill (`:341`); the map's **Delivered** counts every delivered consignment ever (`apps/desktop/src/pages/MapHome.tsx:31-38`). The Caddyfile serves it with a CSP of `default-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'` (`deploy/Caddyfile:29-40`). 52 Vitest tests pass.

### 3. Simple WMS, the pattern to mirror

- **Inbound envelope** (`api/wms/api/envelope.py:65-93`): look the `message_id` up; found, return the stored reply and count the duplicate; otherwise do the work, store `{status_code, response}` in the same transaction and commit; two at once, the loser's `IntegrityError` replays the winner's reply. Only a success is stored, so a refusal can be sent again once mended. Kept `WMS_MESSAGE_TTL_HOURS` (24).
- **Scanner queue** (`apps/scanner/src/lib/queue.ts`): `message_id` assigned when the item is made; items in `localStorage`; `drain()` sends oldest first and stops at the first still waiting, so order holds; a 4xx other than 401, 408, 429 is a refusal, marked `failed`, not retried; `Session.tsx` drains on `online` and every 15 s. `OfflineBanner` in `src/pages/task-shared.tsx:199-229`. `useScanWedge.ts`: a fast burst of keys and Enter is a scan; an `<input data-scan="true">` delivers on Enter too.
- **Scanner service worker** (`apps/scanner/public/sw.js`): caches a fixed shell list and serves it cache-first under a fixed `VERSION`, so a new build reaches a device only when `VERSION` is bumped, and the hashed assets of the very first visit are not kept until a second visit (the worker was not yet in control). The driver's worker below fixes both.
- **The ship-to** Simple WMS sends (`api/wms/api/routes/outbound.py:28-38`): `name, address, suburb, state, postcode, country, contact, phone, email`. There is no delivery-instructions field, so the stop's "delivery note" card shows the contact and a phone link.
- **Package labels** carry `delivery_ref`, `package_no` and `sscc` (`api/wms/services/printing.py:149-152`), so the scan field reads an SSCC (bare, with `00`/`(00)` in front, or a `]C1` prefix), a package number, or `<delivery>-<n>`.

### Decisions this plan makes that were not settled

- **The driver's "Start run" departs a planned run**, through the same `runs.depart` as the dispatcher's "Leaves now" (planning lock, ship calls and all, `shipped_by` the driver's code). A run the dispatcher already sent is left as it is, so pressing both does no harm in either order. "Leaves now" stays on the desktop for a driver without a phone. Starting needs signal (it loads the whole run onto the phone first); it still carries a `message_id`, reused if the driver presses Start again after a dropout. Done and cancelled runs are refused (`409 run_done`, `run_cancelled`); an over-full run is refused with the load's message and "Ask the office to move a stop."
- **No pre-start screen in step 3, and no placeholder for it.** The first screen after sign-in is the top half of `DriverPrestart.html` ("YOUR TRUCK": the driver's runs as rego, type and run number) with a card for the chosen run and **Start run**. The checklist and odometer are left out entirely rather than shown half-built to drivers; a comment in `Today.tsx` says step 4 puts the check between picking and starting. The design's spare and grounded trucks are not shown: a driver picks their run, and a truck comes with it.
- **Idempotency is Simple WMS's envelope, kept for good.** An `inbound_message` row per confirmation (start, deliver, fail, finish) with the reply. No time limit: a phone can sit in a black spot for a shift or more, and the rows are small. Two copies arriving at once take turns on an advisory lock keyed by the `message_id` (the two-number form, so it never meets the planning lock). A `message_id` already used by someone else is `409 message_id_taken`. Refusals are not stored.
- **Pictures go first, each under its own id.** `PUT /v1/pod-files/{id}?kind=signature|photo` with the picture as the body, the id chosen on the phone when it was taken; the same bytes from the same sender again are `200` with the same answer, anything else under that id `409 file_id_taken`. Then the confirmation names them (`signature_file`, `photo_file`). Chosen over one multipart request because: no new dependency (`python-multipart`), a dropout mid-photo resends only that picture, every confirmation stays a small JSON body with its `message_id`, and `DriverOffline.html` already shows "Stop 8 · signature and photo" as its own row. On the phone one queue item carries its pictures and sends them before itself, so a confirmation never arrives before its pictures; the pictures wait in IndexedDB (a run's photos outgrow `localStorage`), the rest in `localStorage` as in the scanner.
- **What a picture may be.** Checked by its first bytes, not its `Content-Type`: a signature is a PNG of at most 1 MB, a photo a JPEG or PNG of at most 5 MB (`413 too_large`, `415 unsupported_type`). The phone makes every photo a JPEG at most 1600 px on its long side (about 300 kB, and the camera's EXIF, location included, is gone). Files live under `TMS_POD_DIR` (default `/data/pod`, the Compose volume `pod_files`), as `<first two hex digits>/<id>.<png|jpg>`, written whole or not at all. `GET /v1/pod-files/{id}` serves one to anyone with `consignments:read` and to whoever sent it, with `Cache-Control: private, no-store`, `Content-Security-Policy: default-src 'none'; sandbox` and `Content-Disposition: inline; filename="<id>.<png|jpg>"`. The desktop does not show pictures in step 3; each history row names its files.
- **Proof of delivery is a signature with a printed name, a photo, or both.** No customer says which it needs yet, so the design's "This customer asks for a signature and printed name" becomes a general hint. Every package is named once, as scanned or ticked (no short deliveries in step 3); the app enables **Delivered** only when all are.
- **A stop's outcome lives on the stop.** `stop.outcome` (`delivered` or `failed`) and `outcome_at`, set once. The live-stop unique index becomes `removed_at is null and outcome is null`, and `assign` looks only at stops without an outcome, so a stop with an outcome stays on its run as the record of that trip. A failed consignment is `failed` and **can be planned again straight away** (`PLANNABLE` gains `failed`), even while its truck is still out: the dispatcher plans tomorrow during today. Departing it again queues a second ship call, which Simple WMS answers `409 already_shipped`, already treated as done (step 2).
- **`pod` is one row per stop**, the delivered or failed record with its reason, note, name, files, the package ticks, the phone's time and position. The position is only there, never in `consignment_event` (kept for good), and step 5's 90-day retention job will clear `pod.latitude`, `pod.longitude` and `pod.accuracy_m` along with the live positions (settled); for the same reason `pod` has no append-only trigger, but nothing in the API ever changes one.
- **Position** is asked for once at **Start run** (at the depot, not at the first drop) and taken at each confirmation, waiting at most 4 s; sent as decimal strings (`"-37.562210"`), rounded to six places by the API, never refused for its precision. The top bar's chip says "Location at drops" or "Location off" rather than the design's "Sharing location": live sharing is step 5.
- **The phone's time** (`at`) is kept as when it happened, but never later than when it arrived, and must carry a time zone.
- **Finishing** is a confirmation like the others, so it queues behind the last stop; refused with `409 stops_left` while a stop has no outcome; a finished run finishes again as a no-op. **The office can finish a run too** (settled): the Plan runs drawer shows **Finish run**, with a confirm, on a run that has left once every stop is delivered or failed, for a driver who is back but didn't press Finish; `POST /v1/runs/{id}/finish` takes `runs:write` (dispatchers and managers) as well as the run's own driver.
- **Scopes:** drivers get `deliveries:read` and `deliveries:write`, on their own runs only (another driver's run is `404`, so run numbers can't be tried). An API key may be given either. `GET /v1/runs/{id}/stops` also answers anyone with `runs:read`. No desktop role gets `deliveries:write` (an admin's `*` has it); finishing is the one driver confirmation the office may also send, with `runs:write`.
- **"Today" is the phone's date.** `GET /v1/driver/runs?date=YYYY-MM-DD`, because the server runs on UTC and a 7 am start in Ballarat is the day before in UTC. It also lists the driver's runs from earlier days that left and were never finished.
- **Events out (`consignment.delivered`, `consignment.failed`, `run.completed`) are deferred.** TMS has no subscriber registry, signing or outbound event queue, and nothing consumes these yet; the history and `pod` rows hold everything they would carry. Building a subscriber mechanism for no subscriber is YAGNI. It comes with the first consumer (an ERP, or Simple WMS wanting "delivered"), with the caveat that deliveries made before then will not be replayed as events.
- **The desktop, minimally:** each stop on a run that has left shows **Delivered** or **Not delivered: closed** (the reason), the lane says "Delivered 2 of 3 · 1 coming back · finished 3:12 pm" and a done run's pill reads **Done**; the tray lists `packed` and `failed` consignments, a failed one marked "Came back: not delivered"; the map's **To plan** counts both, and **Delivered** counts the stops delivered on today's runs (it counted every delivered consignment ever).
- **Driver codes match whatever the case** (settled; it changes step-1 code). Codes are stored lower case under a check constraint; made lower case when a driver is made; looked up lower case in `/v1/drivers/{code}`, at sign-in and when a run names its driver. Two codes that differ only by case are refused (`422`, already exists). Migration `0004` lower-cases existing codes and stops, naming them, if two would collide. So the code field may capitalise as drawn.
- **Copy and small departures from the drawn screens:** the PIN dots grow past four for a longer PIN (4 to 8 digits); "Ding!" is shown, not played, as on the scanner; the Proof screen gets a **Confirm delivery** button under the form, and the "Ding!" panel shows after it; the Can't deliver screen says "The office sees it straight away" only when there is signal; "Waiting to send" rows read "Waiting since …", "Sent …" or "Not taken" with the API's reason.
- **The service worker** fetches the page fresh when there is signal (giving up after 4 s), and serves the kept page when not; build files (`/driver/assets/`, named by hash) come from the cache first. On install it keeps the page, the files the page names and the fonts its stylesheet names, and drops an older build's files. So a new build reaches a phone the next time it opens the app in range, with no version to bump. Caddy sends `sw.js` with `Cache-Control: no-cache`.
- **Open in Maps** is a plain link built from the ship-to: `maps.apple.com/?q=` on an iPhone, `geo:0,0?q=` on Android (the phone's own chooser), Google Maps' search URL anywhere else. No key, nothing to pay.

### Ports and addresses, locally

Simple TMS API `127.0.0.1:8022`, Caddy `https://localhost:8543` (desktop at `/`, driver app at `/driver/`), database `127.0.0.1:5434`. Users `leighton` (admin, `a long enough password`) and `dee` (dispatcher, `dispatch password`); driver `kim`; vehicle `TMS041`. Simple WMS at `127.0.0.1:8011`; TMS's `.env` already has a `simple-tms-ship` key. Vite's dev server for the driver app is on `5175`, its preview on `4175`.

---

## Before you start

```
cd "/Users/leightonlauton/Q7 Technology/Software/q7/simple-tms"
git switch main && git pull --ff-only
git switch -c step-3-driver-app
docker compose up -d db                      # the tests' PostgreSQL on 127.0.0.1:5434
cd api && .venv/bin/pytest -q                # expect: 194 passed
cd ../apps/desktop && npm test               # expect: 52 passed
```

The plan has four parts: **A**, the API (Tasks 1–10); **B**, the driver app (Tasks 11–18); **C**, the desktop (Task 19); **D**, the docs and the checks, ending with the manual run on the local stacks (Tasks 20–22). Part B needs Part A only for the manual check; its tests stub the API. All `pytest` commands run from `api/`; `npm` commands from the app's folder. Commit after each task as shown, on the branch, never on `main`.

## Part A — the API

### Task 1: Settings, the driver's scopes, and refusals with a status of their own

**Files:**
- Modify: `api/tms/config.py` (a `pod_dir` setting)
- Modify: `api/tms/services/sessions.py:34-35` (`DRIVER_SCOPES`)
- Modify: `api/tms/services/api_keys.py:17` (`SCOPES`)
- Modify: `api/tms/api/errors.py` (`Refused`, for 413 and 415)
- Modify: `api/tms/api/deps.py` (`require_any`)
- Modify: `api/tests/conftest.py` (a `pod_dir` fixture for every test)
- Test: `api/tests/test_config.py`, `api/tests/test_drivers.py:71`

**Step 1: Write the failing tests**

Append to `api/tests/test_config.py`:
```python


def test_proof_of_delivery_is_kept_in_the_data_volume():
    assert Settings(secret_key="3f9c1e0a7b2d4c6e8f0a1b2c3d4e5f60").pod_dir == "/data/pod"
```

In `api/tests/test_drivers.py`, `test_driver_signs_in_with_code_and_pin`, replace
```python
    assert me["kind"] == "driver" and me["name"] == "drv-01" and me["scopes"] == ["fleet:read"]
    # a driver picks a truck, so may see the fleet, but not change it
```
with
```python
    assert me["kind"] == "driver" and me["name"] == "drv-01"
    assert me["scopes"] == ["fleet:read", "deliveries:read", "deliveries:write"]
    # a driver may see the fleet, but not change it
```

**Step 2: Run them to see them fail**

Run: `.venv/bin/pytest tests/test_config.py tests/test_drivers.py -q`
Expected: 2 failed: `AttributeError: 'Settings' object has no attribute 'pod_dir'`, and the scopes `['fleet:read'] == ['fleet:read', 'deliveries:read', 'deliveries:write']`.

**Step 3: Implement**

`api/tms/config.py`, before `# The worker that sends the ship calls and print jobs.`:
```python
    # Proof of delivery: where signatures and photos are kept on disk (a
    # volume in Compose). Only the API reads and writes them.
    pod_dir: str = "/data/pod"
```
`api/tms/services/sessions.py`, replace the two lines
```python
# A driver picks a truck after signing in, so may see the fleet. More with step 3.
DRIVER_SCOPES = ["fleet:read"]
```
with
```python
# A driver may see the fleet, and read and record the deliveries on their own
# runs (the routes check the run is theirs).
DRIVER_SCOPES = ["fleet:read", "deliveries:read", "deliveries:write"]
```
`api/tms/services/api_keys.py`, so a partner's handhelds can do what the driver app does:
```python
SCOPES = ("printing:write", "runs:read", "runs:write", "consignments:read", "fleet:read",
          "deliveries:read", "deliveries:write")
```
`api/tms/api/errors.py`, a new class before `class Unauthorised`:
```python
class Refused(Exception):
    """A request refused with a status of its own: a file too big (413) or of
    a kind we do not keep (415). `code` is for programs, `message` for people."""

    def __init__(self, status_code: int, code: str, message: str):
        self.status_code = status_code
        self.code = code
        self.message = message
        super().__init__(message)
```
and in `install()`, before the `Unauthorised` handler:
```python
    @app.exception_handler(Refused)
    async def _refused(request: Request, exc: Refused):
        return JSONResponse(status_code=exc.status_code,
                            content={"detail": exc.message, "code": exc.code})
```
`api/tms/api/deps.py`, at the end:
```python


def require_any(*scopes: str):
    """Dependency: the caller must carry at least one of these scopes."""

    def _check(who: Who) -> Principal:
        if not any(who.has_scope(s) for s in scopes):
            raise Forbidden(f"you need one of the scopes {', '.join(scopes)}")
        return who

    return Depends(_check)
```
`api/tests/conftest.py`, after the `client` fixture (so no test ever writes to `/data/pod`):
```python
@pytest.fixture(autouse=True)
def pod_dir(tmp_path, monkeypatch):
    """Signatures and photos go to a folder of the test's own, never /data/pod."""
    from tms.config import get_settings

    folder = tmp_path / "pod"
    monkeypatch.setattr(get_settings(), "pod_dir", str(folder))
    return folder
```
`Refused` and `require_any` are used from Task 3 on.

**Step 4: Run the tests**

Run: `.venv/bin/pytest -q`
Expected: `195 passed`.

**Step 5: Commit**

```bash
git add api/tms/config.py api/tms/services/sessions.py api/tms/services/api_keys.py \
        api/tms/api/errors.py api/tms/api/deps.py api/tests/conftest.py \
        api/tests/test_config.py api/tests/test_drivers.py
git commit -m "Give drivers the deliveries scopes, and a place for proof of delivery"
```

### Task 2: Stop outcomes, proof of delivery and seen messages in the database

**Files:**
- Create: `api/tms/models/delivery.py`
- Modify: `api/tms/models/__init__.py`
- Modify: `api/tms/models/runs.py` (`Run.finished_at`; `Stop.outcome`, `Stop.outcome_at`; the live-stop index)
- Create: `api/alembic/versions/0003_driver_app.py`
- Test: `api/tests/test_migrations.py:13-15`

**Step 1: Write the failing test**

In `api/tests/test_migrations.py` the tables a fresh install has become:
```python
TABLES = {"user", "user_session", "driver", "vehicle", "consignment", "package",
          "consignment_event", "inbound_event", "run", "stop", "outbound_call", "print_job",
          "api_key", "inbound_message", "pod_file", "pod",
          "alembic_version"}
```

**Step 2: Run it to see it fail**

Run: `.venv/bin/pytest tests/test_migrations.py -q`
Expected: `test_migrations_go_up_down_and_up_again` fails: the three tables are missing.

**Step 3: The models**

`api/tms/models/delivery.py`:
```python
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from tms.models.base import Base, created_at_column


class InboundMessage(Base):
    """Every confirmation from the driver app, by its message_id, with the
    reply it got. A repeat finds its row here and gets the same reply back,
    as Simple WMS's inbound envelope does. Kept for good: a phone can sit in
    a black spot for a whole shift."""

    __tablename__ = "inbound_message"

    message_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    # who sent it: driver:7, key:2, user:1
    sender: Mapped[str] = mapped_column(String(40))
    path: Mapped[str] = mapped_column(String(200))
    status_code: Mapped[int] = mapped_column(Integer)
    response: Mapped[dict] = mapped_column(JSONB)
    received_at: Mapped[datetime] = created_at_column()
    duplicates: Mapped[int] = mapped_column(Integer, default=0)
    last_duplicate_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PodFile(Base):
    """A signature or photo, kept on disk under TMS_POD_DIR. The id is the
    phone's, chosen when the picture was taken, so sending it again is
    harmless. The pod that names it is what it belongs to; a file goes with
    one pod at most."""

    __tablename__ = "pod_file"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    # signature, photo
    kind: Mapped[str] = mapped_column(String(16))
    content_type: Mapped[str] = mapped_column(String(32))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    # under TMS_POD_DIR: 3f/3f2a…-….png (the first two hex digits of the id)
    path: Mapped[str] = mapped_column(String(200))
    # who sent it, as for inbound_message: only they may name it in a pod
    uploaded_by: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = created_at_column()


class Pod(Base):
    """What happened at a stop, once: delivered (with the signature, the
    printed name and a photo, as the customer needs) or failed (with the
    reason). One per stop; the API never changes one."""

    __tablename__ = "pod"

    id: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[uuid.UUID] = mapped_column(Uuid, unique=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("run.id"), index=True)
    stop_id: Mapped[int] = mapped_column(ForeignKey("stop.id"), unique=True)
    consignment_id: Mapped[int] = mapped_column(ForeignKey("consignment.id"), index=True)
    # delivered, failed
    outcome: Mapped[str] = mapped_column(String(16))
    # failed: closed, refused, damaged, wrong_address
    reason: Mapped[str | None] = mapped_column(String(16))
    note: Mapped[str | None] = mapped_column(String(500))
    signed_name: Mapped[str | None] = mapped_column(String(120))
    signature_file_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("pod_file.id"), unique=True)
    photo_file_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("pod_file.id"), unique=True)
    # each package, and whether it was scanned or ticked: [{package_no, how}]
    packages: Mapped[list] = mapped_column(JSONB, default=list)
    # when it happened, by the phone's clock (never later than it arrived)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # where the phone was, if the driver let it say
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    accuracy_m: Mapped[Decimal | None] = mapped_column(Numeric(8, 1))
    recorded_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = created_at_column()
```

No foreign key from `pod_file` back to `pod`: the two would make a cycle, and `Base.metadata.sorted_tables` (the `db` fixture truncates by it) warns on a cycle, which this suite turns into an error. A file belongs to the pod that names it; the unique constraints on `pod.signature_file_id` and `pod.photo_file_id` keep it to one.

`api/tms/models/__init__.py`: after `from tms.models.runs import Run, Stop` add
```python
from tms.models.delivery import InboundMessage, Pod, PodFile
```
and add `"InboundMessage", "Pod", "PodFile",` to `__all__`.

`api/tms/models/runs.py`:
- `Run`: change the status comment to `# planned, departed, done (the driver finished it), cancelled` and add after `departed_at`:
```python
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
```
- `Stop`: the docstring, index and two columns become
```python
class Stop(Base):
    """A consignment's place on a run. Taken off the run, the row stays with
    removed_at set: cancel, never delete. Once the driver has delivered it or
    could not, `outcome` says which, and the stop stays on its run as a
    record; a consignment that came back can then go on another run."""

    __tablename__ = "stop"
    __table_args__ = (
        # a consignment is on one run at a time, however many people drag it
        Index("uq_stop_consignment_id_live", "consignment_id", unique=True,
              postgresql_where=text("removed_at is null and outcome is null")),
    )
```
and, after `removed_at`:
```python
    # delivered, failed: set once, by the driver's confirmation
    outcome: Mapped[str | None] = mapped_column(String(16))
    outcome_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
```

**Step 4: The migration**

`api/alembic/versions/0003_driver_app.py`:
```python
"""the driver app: stop outcomes, proof of delivery and its files, seen messages

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-11
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _at(name: str = "created_at") -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.text("now()"),
                     nullable=False)


def upgrade() -> None:
    op.add_column("run", sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("stop", sa.Column("outcome", sa.String(length=16), nullable=True))
    op.add_column("stop", sa.Column("outcome_at", sa.DateTime(timezone=True), nullable=True))
    # a stop with an outcome is a record of the trip, so a consignment that
    # came back can go on another run
    op.drop_index("uq_stop_consignment_id_live", table_name="stop")
    op.create_index("uq_stop_consignment_id_live", "stop", ["consignment_id"], unique=True,
                    postgresql_where=sa.text("removed_at is null and outcome is null"))

    op.create_table(
        "inbound_message",
        sa.Column("message_id", sa.Uuid(), nullable=False),
        sa.Column("sender", sa.String(length=40), nullable=False),
        sa.Column("path", sa.String(length=200), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column("response", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        _at("received_at"),
        sa.Column("duplicates", sa.Integer(), nullable=False),
        sa.Column("last_duplicate_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("message_id", name=op.f("pk_inbound_message")),
    )

    op.create_table(
        "pod_file",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("content_type", sa.String(length=32), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("path", sa.String(length=200), nullable=False),
        sa.Column("uploaded_by", sa.String(length=40), nullable=False),
        _at(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pod_file")),
    )

    op.create_table(
        "pod",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("stop_id", sa.Integer(), nullable=False),
        sa.Column("consignment_id", sa.Integer(), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.String(length=16), nullable=True),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column("signed_name", sa.String(length=120), nullable=True),
        sa.Column("signature_file_id", sa.Uuid(), nullable=True),
        sa.Column("photo_file_id", sa.Uuid(), nullable=True),
        sa.Column("packages", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("latitude", sa.Numeric(9, 6), nullable=True),
        sa.Column("longitude", sa.Numeric(9, 6), nullable=True),
        sa.Column("accuracy_m", sa.Numeric(8, 1), nullable=True),
        sa.Column("recorded_by", sa.String(length=64), nullable=False),
        _at(),
        sa.ForeignKeyConstraint(["run_id"], ["run.id"], name=op.f("fk_pod_run_id_run")),
        sa.ForeignKeyConstraint(["stop_id"], ["stop.id"], name=op.f("fk_pod_stop_id_stop")),
        sa.ForeignKeyConstraint(["consignment_id"], ["consignment.id"],
                                name=op.f("fk_pod_consignment_id_consignment")),
        sa.ForeignKeyConstraint(["signature_file_id"], ["pod_file.id"],
                                name=op.f("fk_pod_signature_file_id_pod_file")),
        sa.ForeignKeyConstraint(["photo_file_id"], ["pod_file.id"],
                                name=op.f("fk_pod_photo_file_id_pod_file")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pod")),
        sa.UniqueConstraint("message_id", name=op.f("uq_pod_message_id")),
        sa.UniqueConstraint("stop_id", name=op.f("uq_pod_stop_id")),
        sa.UniqueConstraint("signature_file_id", name=op.f("uq_pod_signature_file_id")),
        sa.UniqueConstraint("photo_file_id", name=op.f("uq_pod_photo_file_id")),
    )
    op.create_index(op.f("ix_pod_run_id"), "pod", ["run_id"])
    op.create_index(op.f("ix_pod_consignment_id"), "pod", ["consignment_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_pod_consignment_id"), table_name="pod")
    op.drop_index(op.f("ix_pod_run_id"), table_name="pod")
    op.drop_table("pod")
    op.drop_table("pod_file")
    op.drop_table("inbound_message")
    op.drop_index("uq_stop_consignment_id_live", table_name="stop")
    op.create_index("uq_stop_consignment_id_live", "stop", ["consignment_id"], unique=True,
                    postgresql_where=sa.text("removed_at is null"))
    op.drop_column("stop", "outcome_at")
    op.drop_column("stop", "outcome")
    op.drop_column("run", "finished_at")
```

**Step 5: Run the tests**

Run: `.venv/bin/pytest tests/test_migrations.py -q && .venv/bin/pytest -q`
Expected: `2 passed` (up, down, up again, and the models agree with the migrations), then `195 passed`.

**Step 6: Commit**

```bash
git add api/tms/models api/alembic/versions/0003_driver_app.py api/tests/test_migrations.py
git commit -m "Keep each stop's outcome, its proof of delivery and every driver message"
```

### Task 3: What the phone reads: the day's runs and the whole run

**Files:**
- Create: `api/tms/services/deliveries.py`
- Create: `api/tms/api/routes/deliveries.py`
- Modify: `api/tms/api/app.py` (include the router)
- Create: `api/tests/deliveries_samples.py`
- Test: `api/tests/test_driver_runs.py`, `api/tests/test_api_keys.py`

**Step 1: Write the failing tests**

`api/tests/deliveries_samples.py` (the later tasks use all of it):
```python
"""A run on the road and a driver's phone: what the delivery tests start from."""
import uuid
from datetime import UTC, datetime

from conftest import bearer
from runs_samples import DAY, assign, consignment, driver, new_run, vehicle

# only the first bytes are looked at, so these are pictures enough
PNG = b"\x89PNG\r\n\x1a\n" + b"signature" * 20
JPEG = b"\xff\xd8\xff\xe0" + b"photo" * 40


def driver_headers(client, code="kim", pin="2468") -> dict:
    r = client.post("/v1/auth/driver-login", json={"code": code, "pin": pin})
    assert r.status_code == 200, r.text
    return bearer(r.json()["token"])


def planned_run(client, db, headers, n=2, day=DAY):
    """TMS041 for Kim with n stops, not yet left."""
    vehicle(db)
    driver(db)
    run = new_run(client, headers, day=day)
    cons = [consignment(db, ref=f"00800345{i:02d}", customer=f"Customer {i}") for i in range(n)]
    for c in cons:
        assert assign(client, headers, run["id"], c.id).status_code == 200
    return run, cons


def on_the_road(client, db, headers, n=2):
    """As planned_run, departed by the dispatcher; and Kim's phone signed in."""
    run, cons = planned_run(client, db, headers, n)
    assert client.post(f"/v1/runs/{run['id']}/depart", headers=headers).status_code == 200
    return run, cons, driver_headers(client)


def put_file(client, headers, kind="signature", data=PNG, file_id=None,
             content_type="image/png"):
    file_id = file_id or uuid.uuid4()
    r = client.put(f"/v1/pod-files/{file_id}", params={"kind": kind}, content=data,
                   headers={**headers, "Content-Type": content_type})
    return file_id, r


def now() -> str:
    return datetime.now(UTC).isoformat()


def delivered(client, headers, run_id, c, message_id=None, **over):
    """Deliver consignment c with a signature, Kim's way."""
    body = {"message_id": str(message_id or uuid.uuid4()), "at": now(),
            "packages": [{"package_no": p.package_no, "how": "tick"} for p in c.packages]}
    if "signature_file" not in over and "photo_file" not in over:
        sig, r = put_file(client, headers)
        assert r.status_code == 201, r.text
        body |= {"signature_file": str(sig), "signed_name": "Priya"}
    body |= over
    return client.post(f"/v1/runs/{run_id}/stops/{c.id}/deliver", headers=headers, json=body)


def failed(client, headers, run_id, c, message_id=None, **over):
    body = {"message_id": str(message_id or uuid.uuid4()), "at": now(), "reason": "closed",
            **over}
    return client.post(f"/v1/runs/{run_id}/stops/{c.id}/fail", headers=headers, json=body)
```

`api/tests/test_driver_runs.py`:
```python
"""What the driver's phone reads: today's runs, and a whole run to keep."""
from datetime import timedelta

from deliveries_samples import driver_headers, on_the_road, planned_run
from runs_samples import DAY, driver, new_run, vehicle


def test_a_driver_sees_their_own_runs_for_the_day(client, db, dispatcher_headers):
    run, cons = planned_run(client, db, dispatcher_headers)
    driver(db, code="mel", name="Mel")
    vehicle(db, rego="TMS052")
    new_run(client, dispatcher_headers, rego="TMS052", code="mel")
    kim = driver_headers(client)
    r = client.get("/v1/driver/runs", params={"date": DAY.isoformat()}, headers=kim)
    assert r.status_code == 200, r.text
    assert r.json() == [{"id": run["id"], "run_no": run["run_no"], "date": "2026-10-14",
                         "status": "planned", "vehicle": {"rego": "TMS041", "type": "van"},
                         "stops": 2, "packages": 2, "delivered": 0, "failed": 0}]
    later = client.get("/v1/driver/runs", params={"date": "2026-10-15"}, headers=kim)
    assert later.json() == []


def test_a_run_left_unfinished_on_an_earlier_day_still_shows(client, db, dispatcher_headers):
    run, _, kim = on_the_road(client, db, dispatcher_headers)
    next_day = (DAY + timedelta(days=1)).isoformat()
    r = client.get("/v1/driver/runs", params={"date": next_day}, headers=kim)
    assert [x["id"] for x in r.json()] == [run["id"]]


def test_only_a_driver_has_runs_of_their_own(client, dispatcher_headers):
    r = client.get("/v1/driver/runs", params={"date": DAY.isoformat()}, headers=dispatcher_headers)
    assert r.status_code == 403


def test_the_whole_run_loads_onto_the_phone(client, db, dispatcher_headers):
    run, cons = planned_run(client, db, dispatcher_headers)
    r = client.get(f"/v1/runs/{run['id']}/stops", headers=driver_headers(client))
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["run_no"] == run["run_no"] and out["status"] == "planned"
    assert out["vehicle"] == {"rego": "TMS041", "type": "van"}
    assert out["driver"] == {"code": "kim", "name": "Kim"}
    first = out["stops"][0]
    assert first == {
        "sequence": 1, "consignment_id": cons[0].id, "wms_ref": "0080034500",
        "customer": "Customer 0",
        "ship_to": {"name": "Customer 0", "address": "1 Main St", "suburb": "Ballan",
                    "state": "VIC", "postcode": "3342"},
        "packages": [{"package_no": 1, "type": "carton", "weight_kg": "100", "sscc": None}],
        "outcome": None, "outcome_at": None}


def test_a_driver_cannot_read_someone_elses_run(client, db, dispatcher_headers):
    run, _ = planned_run(client, db, dispatcher_headers)
    driver(db, code="mel", name="Mel")
    mel = driver_headers(client, code="mel")
    assert client.get(f"/v1/runs/{run['id']}/stops", headers=mel).status_code == 404
    # the office can
    assert client.get(f"/v1/runs/{run['id']}/stops", headers=dispatcher_headers).status_code == 200
    assert client.get(f"/v1/runs/{run['id']}/stops").status_code == 401
```

Append to `api/tests/test_api_keys.py`:
```python


def test_a_key_may_record_deliveries_as_the_driver_app_does(client, db, capsys):
    raw = _key(capsys, name="handhelds", scopes="deliveries:read,deliveries:write")
    me = client.get("/v1/auth/me", headers=bearer(raw)).json()
    assert me["scopes"] == ["deliveries:read", "deliveries:write"]
    # but has no runs of its own to list
    assert client.get("/v1/driver/runs", params={"date": "2026-10-14"},
                      headers=bearer(raw)).status_code == 403
```

**Step 2: Run them to see them fail**

Run: `.venv/bin/pytest tests/test_driver_runs.py tests/test_api_keys.py -q`
Expected: 6 failed, each a `404` where a `200` or `403` was wanted (no such routes yet).

**Step 3: The service**

`api/tms/services/deliveries.py`:
```python
"""The driver's side of a run: start it, deliver each stop or say why it
couldn't be, and finish it. Each change locks its run's row first, so two
confirmations for one run (two phones, or a retry racing the original) take
turns, and a stop is recorded once."""
from __future__ import annotations

from datetime import date

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from tms.api.errors import NotFound
from tms.models import Run
from tms.services import runs


def own_run(db: Session, who, run_id: int) -> Run:
    """The run, if the caller may act on it: a driver only on their own (any
    other is not found, so run numbers can't be tried); a key or a user with
    the scope, on any."""
    run = runs.get_run(db, run_id)
    if who.kind == "driver" and run.driver_id != who.id:
        raise NotFound(f"no run {run_id}")
    return run


def driver_runs(db: Session, driver_id: int, day: date) -> list[Run]:
    """This driver's runs for the day, and any of theirs from before that
    left and were never finished."""
    ids = db.execute(
        select(Run.id).where(Run.driver_id == driver_id, or_(
            and_(Run.day == day, Run.status != "cancelled"),
            and_(Run.day < day, Run.status == "departed")))
        .order_by(Run.day, Run.id)).scalars().all()
    return [runs.get_run(db, i) for i in ids]
```

**Step 4: The routes**

`api/tms/api/routes/deliveries.py`:
```python
"""The driver app's routes: today's runs, a run loaded onto the phone, and
the confirmations from the road. Every confirmation carries a message_id; a
repeat gets the first reply back and changes nothing."""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Query
from pydantic import BaseModel

from tms.api.deps import DB, Principal, require, require_any
from tms.api.errors import Forbidden
from tms.api.schemas import Dec
from tms.models import Run
from tms.services import deliveries

router = APIRouter(tags=["deliveries"])


# ---- reading -----------------------------------------------------------------

class DriverRunOut(BaseModel):
    id: int
    run_no: str
    date: dt.date
    status: str
    vehicle: dict
    stops: int
    packages: int
    delivered: int
    failed: int


class PackageLine(BaseModel):
    package_no: int
    type: str
    weight_kg: Dec | None
    sscc: str | None


class StopDetail(BaseModel):
    sequence: int
    consignment_id: int
    wms_ref: str
    customer: str
    ship_to: dict
    packages: list[PackageLine]
    outcome: str | None
    outcome_at: dt.datetime | None


class RunStopsOut(BaseModel):
    id: int
    run_no: str
    date: dt.date
    status: str
    vehicle: dict
    driver: dict
    departed_at: dt.datetime | None
    finished_at: dt.datetime | None
    stops: list[StopDetail]


def driver_run_out(run: Run) -> DriverRunOut:
    return DriverRunOut(
        id=run.id, run_no=run.number, date=run.day, status=run.status,
        vehicle={"rego": run.vehicle.rego, "type": run.vehicle.type},
        stops=len(run.stops), packages=sum(len(s.consignment.packages) for s in run.stops),
        delivered=sum(s.outcome == "delivered" for s in run.stops),
        failed=sum(s.outcome == "failed" for s in run.stops))


def run_stops_out(run: Run) -> RunStopsOut:
    return RunStopsOut(
        id=run.id, run_no=run.number, date=run.day, status=run.status,
        vehicle={"rego": run.vehicle.rego, "type": run.vehicle.type},
        driver={"code": run.driver.code, "name": run.driver.name},
        departed_at=run.departed_at, finished_at=run.finished_at,
        stops=[StopDetail(
            sequence=s.sequence, consignment_id=s.consignment_id, wms_ref=s.consignment.wms_ref,
            customer=s.consignment.customer, ship_to=s.consignment.ship_to or {},
            packages=[PackageLine(package_no=p.package_no, type=p.type, weight_kg=p.weight_kg,
                                  sscc=p.sscc) for p in s.consignment.packages],
            outcome=s.outcome, outcome_at=s.outcome_at) for s in run.stops])


@router.get("/driver/runs", response_model=list[DriverRunOut])
def my_runs(db: DB, day: dt.date = Query(alias="date"),
            who: Principal = require("deliveries:read")):
    """The signed-in driver's runs for the day (the phone's own date), and any
    of theirs that left on an earlier day and were never finished."""
    if who.kind != "driver":
        raise Forbidden("only a driver has runs of their own")
    return [driver_run_out(r) for r in deliveries.driver_runs(db, who.id, day)]


@router.get("/runs/{id}/stops", response_model=RunStopsOut)
def run_stops(id: int, db: DB, who: Principal = require_any("runs:read", "deliveries:read")):
    """The whole run as the phone keeps it: each stop's address, packages and
    outcome. A driver sees only their own runs."""
    return run_stops_out(deliveries.own_run(db, who, id))
```

`api/tms/api/app.py`: import `deliveries` with the other routes (`auth, consignments, deliveries, drivers, printing, runs, vehicles, wms_events`) and add `v1.include_router(deliveries.router)` after the printing router.

**Step 5: Run the tests**

Run: `.venv/bin/pytest tests/test_driver_runs.py tests/test_api_keys.py -q && .venv/bin/pytest -q`
Expected: `10 passed`, then `201 passed`.

**Step 6: Commit**

```bash
git add api/tms/services/deliveries.py api/tms/api/routes/deliveries.py api/tms/api/app.py \
        api/tests/deliveries_samples.py api/tests/test_driver_runs.py api/tests/test_api_keys.py
git commit -m "Let a driver read today's runs and load a whole run onto the phone"
```

### Task 4: Starting a run, once, however often it is sent

**Files:**
- Create: `api/tms/services/envelope.py`
- Modify: `api/tms/services/deliveries.py` (`start`)
- Modify: `api/tms/api/routes/deliveries.py` (`POST /v1/runs/{id}/start`)
- Test: `api/tests/test_start_run.py`

**Step 1: Write the failing tests**

`api/tests/test_start_run.py`:
```python
"""The driver starts the run: a planned run departs, as the dispatcher's
"Leaves now" would; one already gone is left as it is."""
import uuid

from sqlalchemy import select

from conftest import all_at_once, fresh_client
from deliveries_samples import driver_headers, on_the_road, planned_run
from runs_samples import driver
from tms.models import InboundMessage, OutboundCall


def _start(client, headers, run_id, message_id=None):
    return client.post(f"/v1/runs/{run_id}/start", headers=headers,
                       json={"message_id": str(message_id or uuid.uuid4())})


def test_starting_a_planned_run_departs_it(client, db, dispatcher_headers):
    run, cons = planned_run(client, db, dispatcher_headers)
    kim = driver_headers(client)
    message_id = uuid.uuid4()
    r = _start(client, kim, run["id"], message_id)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["message_id"] == str(message_id) and out["run_no"] == run["run_no"]
    assert out["status"] == "departed" and out["departed_at"] is not None
    calls = db.execute(select(OutboundCall)).scalars().all()
    assert len(calls) == 2 and {c.body["shipped_by"] for c in calls} == {"kim"}
    h = client.get(f"/v1/consignments/{cons[0].id}", headers=dispatcher_headers).json()["history"]
    assert h[-1]["kind"] == "departed" and h[-1]["detail"]["by"] == "kim"


def test_starting_a_run_the_office_already_sent_changes_nothing(client, db, dispatcher_headers):
    run, _, kim = on_the_road(client, db, dispatcher_headers)
    r = _start(client, kim, run["id"])
    assert r.status_code == 200 and r.json()["status"] == "departed"
    assert len(db.execute(select(OutboundCall)).scalars().all()) == 2


def test_the_same_start_twice_gets_the_same_reply(client, db, dispatcher_headers):
    run, _ = planned_run(client, db, dispatcher_headers)
    kim = driver_headers(client)
    message_id = uuid.uuid4()
    first = _start(client, kim, run["id"], message_id)
    again = _start(client, kim, run["id"], message_id)
    assert again.status_code == 200 and again.json() == first.json()
    seen = db.execute(select(InboundMessage)).scalar_one()
    assert seen.message_id == message_id and seen.duplicates == 1 and seen.sender.startswith("driver:")


def test_a_start_sent_four_times_at_once_departs_once(client, db, dispatcher_headers):
    run, _ = planned_run(client, db, dispatcher_headers)
    kim = driver_headers(client)
    message_id = str(uuid.uuid4())

    def start():
        with fresh_client() as c:
            r = c.post(f"/v1/runs/{run['id']}/start", headers=kim, json={"message_id": message_id})
            return r.status_code, r.json()

    answers = all_at_once(4, start)
    assert [code for code, _ in answers] == [200] * 4
    assert all(body == answers[0][1] for _, body in answers)
    assert len(db.execute(select(OutboundCall)).scalars().all()) == 2


def test_a_driver_starts_only_their_own_run(client, db, dispatcher_headers):
    run, _ = planned_run(client, db, dispatcher_headers)
    driver(db, code="mel", name="Mel")
    assert _start(client, driver_headers(client, code="mel"), run["id"]).status_code == 404
    assert _start(client, dispatcher_headers, run["id"]).status_code == 403


def test_a_message_id_is_one_senders(client, db, dispatcher_headers):
    run, _ = planned_run(client, db, dispatcher_headers)
    message_id = uuid.uuid4()
    _start(client, driver_headers(client), run["id"], message_id)
    driver(db, code="mel", name="Mel")
    r = _start(client, driver_headers(client, code="mel"), run["id"], message_id)
    assert r.status_code == 409 and r.json()["code"] == "message_id_taken"


def test_an_over_full_run_is_not_started_from_the_phone(client, db, dispatcher_headers):
    from runs_samples import assign, consignment
    run, _ = planned_run(client, db, dispatcher_headers)
    heavy = consignment(db, ref="0080039999", kg="1100")
    assign(client, dispatcher_headers, run["id"], heavy.id)
    r = _start(client, driver_headers(client), run["id"])
    assert r.status_code == 409 and r.json()["code"] == "over_capacity"
    assert db.execute(select(InboundMessage)).first() is None
```

**Step 2: Run them to see them fail**

Run: `.venv/bin/pytest tests/test_start_run.py -q`
Expected: 7 failed (`404`, no route).

**Step 3: The envelope**

`api/tms/services/envelope.py`, Simple WMS's `handle` with a lock in front instead of an `IntegrityError` after (the work here writes rows with unique keys of their own, so the second copy must not start the work at all), and no time limit:
```python
"""A confirmation from the driver app is acted on once per message_id. A
repeat gets the original reply back and does nothing, as Simple WMS's
inbound envelope (simple-wms api/wms/api/envelope.py) does."""
from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from tms.api.errors import Conflict
from tms.models import InboundMessage

# the two-number form of an advisory lock: its keys never meet the planning
# lock's single number
MESSAGE_LOCK = 7302


def sender_of(who) -> str:
    return f"{who.kind}:{who.id}"


def lock_key(value: uuid.UUID) -> int:
    """32 bits of the uuid, as Postgres's int4 wants them."""
    return int.from_bytes(value.bytes[:4], "big", signed=True)


def handle(db: Session, who, message_id: uuid.UUID, path: str,
           work: Callable[[], BaseModel], status_code: int = 200) -> JSONResponse:
    """Run `work` once per message_id. Two copies of one message arriving at
    once take turns on a lock, so the second finds the first's reply. The
    reply is stored in the same transaction as the work: both land, or
    neither does and the phone tries again. A refusal is not stored, so the
    same message can be sent again once whatever stopped it is put right."""
    db.execute(text("select pg_advisory_xact_lock(:a, :b)"),
               {"a": MESSAGE_LOCK, "b": lock_key(message_id)})
    seen = db.get(InboundMessage, message_id, populate_existing=True)
    if seen is not None:
        if seen.sender != sender_of(who):
            raise Conflict("message_id_taken", "that message_id was sent by someone else")
        seen.duplicates += 1
        seen.last_duplicate_at = datetime.now(UTC)
        db.commit()
        return JSONResponse(status_code=seen.status_code, content=seen.response)

    reply = work().model_dump(mode="json")
    db.add(InboundMessage(message_id=message_id, sender=sender_of(who), path=path,
                          status_code=status_code, response=reply, duplicates=0))
    db.commit()
    return JSONResponse(status_code=status_code, content=reply)
```

**Step 4: Start, in the service and the route**

`api/tms/services/deliveries.py`: import `Conflict` too (`from tms.api.errors import Conflict, NotFound`) and append:
```python
def start(db: Session, who, run_id: int) -> Run:
    """The driver is leaving: a planned run departs exactly as the
    dispatcher's "Leaves now" does (ship calls and all); one that has already
    left is left as it is."""
    run = own_run(db, who, run_id)
    if run.status == "done":
        raise Conflict("run_done", f"{run.number} is finished")
    if run.status == "cancelled":
        raise Conflict("run_cancelled", f"{run.number} was cancelled")
    return runs.depart(db, run_id, by=who.name)
```

`api/tms/api/routes/deliveries.py`: the imports gain `import uuid`, `ConfigDict` (`from pydantic import BaseModel, ConfigDict`) and `from tms.services.envelope import handle`; append:
```python
# ---- confirmations -----------------------------------------------------------

class MessageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message_id: uuid.UUID


class StartOut(BaseModel):
    message_id: uuid.UUID
    run_id: int
    run_no: str
    status: str
    departed_at: dt.datetime | None


@router.post("/runs/{id}/start", response_model=StartOut)
def start_run(id: int, body: MessageIn, db: DB, who: Principal = require("deliveries:write")):
    def work():
        run = deliveries.start(db, who, id)
        return StartOut(message_id=body.message_id, run_id=run.id, run_no=run.number,
                        status=run.status, departed_at=run.departed_at)
    return handle(db, who, body.message_id, f"/v1/runs/{id}/start", work)
```

**Step 5: Run the tests**

Run: `.venv/bin/pytest tests/test_start_run.py tests/test_depart.py -q && .venv/bin/pytest -q`
Expected: `14 passed` (the dispatcher's depart is unchanged), then `208 passed`. `test_a_start_sent_four_times_at_once_departs_once` fails without the lock in `handle`: the copies all find no row, all do the work, and the second insert of the same `message_id` raises `UniqueViolation` on `pk_inbound_message` (checked by taking the lock out).

**Step 6: Commit**

```bash
git add api/tms/services/envelope.py api/tms/services/deliveries.py \
        api/tms/api/routes/deliveries.py api/tests/test_start_run.py
git commit -m "Start a run from the phone, once per message_id, as Leaves now does"
```

### Task 5: Signatures and photos, kept once and shown only to who may see them

**Files:**
- Create: `api/tms/services/pod_files.py`
- Create: `api/tms/api/routes/pod_files.py`
- Modify: `api/tms/api/app.py`
- Test: `api/tests/test_pod_files.py`

**Step 1: Write the failing tests**

`api/tests/test_pod_files.py`:
```python
"""Signatures and photos: kept on disk, checked for what they are, sent again
harmlessly, and shown only to who may see them."""
import hashlib
import uuid

from deliveries_samples import JPEG, PNG, driver_headers, put_file
from runs_samples import driver
from tms.services import pod_files


def test_a_signature_is_kept_on_disk_once(client, db, pod_dir):
    driver(db)
    kim = driver_headers(client)
    file_id, r = put_file(client, kim)
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["id"] == str(file_id) and out["kind"] == "signature"
    assert out["content_type"] == "image/png" and out["size_bytes"] == len(PNG)
    assert out["sha256"] == hashlib.sha256(PNG).hexdigest()
    stored = list(pod_dir.rglob("*.png"))
    assert [p.name for p in stored] == [f"{file_id}.png"] and stored[0].read_bytes() == PNG

    again, r2 = put_file(client, kim, file_id=file_id)
    assert r2.status_code == 200 and r2.json() == out
    assert len(list(pod_dir.rglob("*.png"))) == 1


def test_other_bytes_under_a_used_id_are_refused(client, db):
    driver(db)
    kim = driver_headers(client)
    file_id, _ = put_file(client, kim)
    _, r = put_file(client, kim, file_id=file_id, data=PNG + b"more")
    assert r.status_code == 409 and r.json()["code"] == "file_id_taken"


def test_only_pictures_of_the_right_kind(client, db):
    driver(db)
    kim = driver_headers(client)
    _, r = put_file(client, kim, kind="signature", data=JPEG, content_type="image/jpeg")
    assert r.status_code == 415 and r.json() == {
        "detail": "a signature must be a PNG picture", "code": "unsupported_type"}
    # what the request says it is counts for nothing; the bytes do
    _, r = put_file(client, kim, kind="photo", data=b"<html>not a photo</html>",
                    content_type="image/jpeg")
    assert r.status_code == 415
    _, r = put_file(client, kim, kind="photo", data=JPEG, content_type="image/jpeg")
    assert r.status_code == 201 and r.json()["content_type"] == "image/jpeg"
    _, r = put_file(client, kim, kind="selfie")
    assert r.status_code == 422


def test_a_file_too_big_is_refused(client, db, monkeypatch):
    driver(db)
    kim = driver_headers(client)
    monkeypatch.setitem(pod_files.KINDS, "signature", ({"image/png"}, 100))
    _, r = put_file(client, kim, data=PNG)
    assert r.status_code == 413 and r.json()["code"] == "too_large"
    monkeypatch.setattr(pod_files, "LARGEST", 50)
    _, r = put_file(client, kim, data=PNG)
    assert r.status_code == 413


def test_only_a_driver_or_a_key_sends_files(client, db, dispatcher_headers):
    _, r = put_file(client, dispatcher_headers)
    assert r.status_code == 403
    _, r = put_file(client, {})
    assert r.status_code == 401


def test_a_file_is_shown_to_the_office_and_to_whoever_sent_it(client, db, dispatcher_headers):
    driver(db)
    driver(db, code="mel", name="Mel")
    kim = driver_headers(client)
    file_id, _ = put_file(client, kim)
    for headers in (kim, dispatcher_headers):
        r = client.get(f"/v1/pod-files/{file_id}", headers=headers)
        assert r.status_code == 200 and r.content == PNG
        assert r.headers["content-type"] == "image/png"
        assert r.headers["cache-control"] == "private, no-store"
        assert r.headers["content-security-policy"] == "default-src 'none'"
    assert client.get(f"/v1/pod-files/{file_id}",
                      headers=driver_headers(client, code="mel")).status_code == 404
    assert client.get(f"/v1/pod-files/{file_id}").status_code == 401
    assert client.get(f"/v1/pod-files/{uuid.uuid4()}", headers=kim).status_code == 404
```

**Step 2: Run them to see them fail**

Run: `.venv/bin/pytest tests/test_pod_files.py -q`
Expected: `ImportError: cannot import name 'pod_files' from 'tms.services'`.

**Step 3: The service**

`api/tms/services/pod_files.py`:
```python
"""Signatures and photos for proof of delivery. Kept on disk under
TMS_POD_DIR, one file each, named by the id the phone gave it; the row says
what it is and who sent it. Only PNG and JPEG, checked by their first bytes,
not by what the request says they are."""
from __future__ import annotations

import hashlib
import os
import tempfile
import uuid
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

from tms.api.errors import Conflict, FieldError, Refused
from tms.config import get_settings
from tms.models import PodFile
from tms.services.envelope import sender_of

FILE_LOCK = 7303
# what each kind may be, and how big. A phone's photo is made smaller on the
# phone first (about 300 kB); a signature is a few kB.
KINDS = {
    "signature": ({"image/png"}, 1_000_000),
    "photo": ({"image/jpeg", "image/png"}, 5_000_000),
}
LARGEST = max(limit for _, limit in KINDS.values())
MAGIC = {"image/png": b"\x89PNG\r\n\x1a\n", "image/jpeg": b"\xff\xd8\xff"}
EXT = {"image/png": "png", "image/jpeg": "jpg"}


def sniff(data: bytes) -> str | None:
    for content_type, magic in MAGIC.items():
        if data.startswith(magic):
            return content_type
    return None


def full_path(row: PodFile) -> Path:
    return Path(get_settings().pod_dir) / row.path


def _write(target: Path, data: bytes) -> None:
    """All of it or none of it: a half-written file never has the real name."""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".upload-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def store(db: Session, who, file_id: uuid.UUID, kind: str, data: bytes) -> tuple[PodFile, bool]:
    """Keep the file; (row, True) when it is new. The same bytes sent again
    by the same sender are the same file (False); anything else with that id
    is refused."""
    if kind not in KINDS:
        raise FieldError("query.kind", "must be signature or photo")
    allowed, limit = KINDS[kind]
    if len(data) > limit:
        raise Refused(413, "too_large", f"a {kind} may be {limit // 1_000_000} MB at most")
    content_type = sniff(data)
    if content_type not in allowed:
        wanted = " or ".join(sorted(EXT[t].upper() for t in allowed))
        raise Refused(415, "unsupported_type", f"a {kind} must be a {wanted} picture")
    digest = hashlib.sha256(data).hexdigest()

    # two copies of one upload take turns, so neither writes over the other
    db.execute(text("select pg_advisory_xact_lock(:a, :b)"),
               {"a": FILE_LOCK, "b": int.from_bytes(file_id.bytes[:4], "big", signed=True)})
    row = db.get(PodFile, file_id, populate_existing=True)
    if row is not None:
        if row.uploaded_by != sender_of(who) or row.sha256 != digest or row.kind != kind:
            raise Conflict("file_id_taken", f"file {file_id} is already something else")
        return row, False

    relative = f"{file_id.hex[:2]}/{file_id}.{EXT[content_type]}"
    _write(Path(get_settings().pod_dir) / relative, data)
    row = PodFile(id=file_id, kind=kind, content_type=content_type, size_bytes=len(data),
                  sha256=digest, path=relative, uploaded_by=sender_of(who))
    db.add(row)
    db.flush()
    return row, True
```

**Step 4: The routes**

`api/tms/api/routes/pod_files.py`. The body is read by an `async` dependency that stops at the largest size kept, so a huge upload is cut off rather than held in memory; the route itself stays a plain `def` (run in the thread pool), as every other route here, so the database work never blocks the event loop.
```python
"""Signatures and photos for proof of delivery: sent by the phone before the
confirmation that names them, and shown only to who may see them."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from tms.api.deps import DB, Principal, Who, require
from tms.api.errors import NotFound, Refused
from tms.models import PodFile
from tms.services import pod_files
from tms.services.envelope import sender_of

router = APIRouter(tags=["deliveries"])


class PodFileOut(BaseModel):
    id: uuid.UUID
    kind: str
    content_type: str
    size_bytes: int
    sha256: str
    created_at: datetime


def file_out(row: PodFile) -> PodFileOut:
    return PodFileOut(id=row.id, kind=row.kind, content_type=row.content_type,
                      size_bytes=row.size_bytes, sha256=row.sha256, created_at=row.created_at)


async def picture(request: Request) -> bytes:
    """The request body, read no further than the largest file we keep."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > pod_files.LARGEST:
        raise Refused(413, "too_large", "that file is too big")
    data = bytearray()
    async for chunk in request.stream():
        data += chunk
        if len(data) > pod_files.LARGEST:
            raise Refused(413, "too_large", "that file is too big")
    return bytes(data)


@router.put("/pod-files/{file_id}", response_model=PodFileOut, status_code=201)
def put_file(file_id: uuid.UUID, kind: Literal["signature", "photo"], db: DB,
             data: bytes = Depends(picture), who: Principal = require("deliveries:write")):
    """The picture as the body (Content-Type image/png or image/jpeg; what
    counts is its first bytes). 201 when new; the same bytes again, 200."""
    row, new = pod_files.store(db, who, file_id, kind, data)
    db.commit()
    return JSONResponse(status_code=201 if new else 200,
                        content=file_out(row).model_dump(mode="json"))


@router.get("/pod-files/{file_id}", response_class=Response)
def get_file(file_id: uuid.UUID, db: DB, who: Who):
    """For anyone who may read consignments, and for whoever sent it."""
    row = db.get(PodFile, file_id)
    if row is None or not (who.has_scope("consignments:read")
                           or row.uploaded_by == sender_of(who)):
        raise NotFound(f"no file {file_id}")
    return Response(content=pod_files.full_path(row).read_bytes(), media_type=row.content_type,
                    headers={"Cache-Control": "private, no-store",
                             "Content-Security-Policy": "default-src 'none'",
                             "X-Content-Type-Options": "nosniff"})
```

`api/tms/api/app.py`: import `pod_files` with the other routes and add `v1.include_router(pod_files.router)` after the deliveries router.

**Step 5: Run the tests**

Run: `.venv/bin/pytest tests/test_pod_files.py -q && .venv/bin/pytest -q`
Expected: `6 passed`, then `214 passed`.

**Step 6: Commit**

```bash
git add api/tms/services/pod_files.py api/tms/api/routes/pod_files.py api/tms/api/app.py \
        api/tests/test_pod_files.py
git commit -m "Keep signatures and photos on disk, each once, under the phone's id"
```

### Task 6: Delivered, or why not: once per stop, with its proof

**Files:**
- Modify: `api/tms/services/deliveries.py` (`deliver`, `fail`, `tally`)
- Modify: `api/tms/api/routes/deliveries.py` (`POST /v1/runs/{id}/stops/{consignment_id}/deliver` and `/fail`)
- Modify: `api/tms/api/routes/runs.py` (each stop's outcome and reason, the run's `finished_at`)
- Test: `api/tests/test_deliver.py`, `api/tests/test_fail_and_replan.py`

**Step 1: Write the failing tests**

`api/tests/test_deliver.py`:
```python
"""Delivered: once, with its proof, however often the phone sends it."""
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from conftest import all_at_once, fresh_client
from deliveries_samples import (
    JPEG, delivered, driver_headers, now, on_the_road, planned_run, put_file,
)
from runs_samples import driver
from tms.models import ConsignmentEvent, InboundMessage, Pod


def test_a_stop_is_delivered_with_its_proof(client, db, dispatcher_headers):
    run, (a, b), kim = on_the_road(client, db, dispatcher_headers)
    sig, _ = put_file(client, kim)
    photo, _ = put_file(client, kim, kind="photo", data=JPEG, content_type="image/jpeg")
    message_id = uuid.uuid4()
    r = delivered(client, kim, run["id"], a, message_id=message_id, signature_file=str(sig),
                  signed_name="  Priya ", photo_file=str(photo), note="Left at the back door",
                  position={"latitude": "-37.5622104", "longitude": "143.8503", "accuracy_m": "12.34"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert {k: out[k] for k in ("message_id", "run_id", "consignment_id", "sequence", "outcome",
                                "done", "left")} == {
        "message_id": str(message_id), "run_id": run["id"], "consignment_id": a.id,
        "sequence": 1, "outcome": "delivered", "done": 1, "left": 1}

    pod = db.execute(select(Pod)).scalar_one()
    assert pod.id == out["pod_id"] and pod.signed_name == "Priya"
    assert pod.signature_file_id == sig and pod.photo_file_id == photo
    assert pod.packages == [{"package_no": 1, "how": "tick"}]
    assert str(pod.latitude) == "-37.562210" and str(pod.longitude) == "143.850300"
    assert str(pod.accuracy_m) == "12.3" and pod.recorded_by == "kim"

    detail = client.get(f"/v1/consignments/{a.id}", headers=dispatcher_headers).json()
    assert detail["status"] == "delivered"
    last = detail["history"][-1]
    assert last["kind"] == "delivered"
    # no position in the history: it is kept for good, positions are not
    assert last["detail"] == {"run": run["run_no"], "run_id": run["id"], "pod_id": pod.id,
                              "packages": 1, "signed_name": "Priya", "signature_file": str(sig),
                              "photo_file": str(photo), "by": "kim"}

    lane = client.get(f"/v1/runs/{run['id']}", headers=dispatcher_headers).json()
    assert [(s["outcome"], s["reason"]) for s in lane["stops"]] == [("delivered", None), (None, None)]


def test_the_same_delivery_sent_again_is_recorded_once(client, db, dispatcher_headers):
    run, (a, _), kim = on_the_road(client, db, dispatcher_headers)
    sig, _ = put_file(client, kim)
    body = {"message_id": str(uuid.uuid4()), "at": now(), "signature_file": str(sig),
            "signed_name": "Priya", "packages": [{"package_no": 1, "how": "tick"}]}
    path = f"/v1/runs/{run['id']}/stops/{a.id}/deliver"
    first = client.post(path, headers=kim, json=body)
    # the reply was lost in a black spot, so the phone sends it again
    again = client.post(path, headers=kim, json=body)
    assert first.status_code == 200, first.text
    assert again.status_code == 200 and again.json() == first.json()
    assert len(db.execute(select(Pod)).scalars().all()) == 1
    kinds = db.execute(select(ConsignmentEvent.kind).where(
        ConsignmentEvent.consignment_id == a.id)).scalars().all()
    assert kinds.count("delivered") == 1
    assert db.execute(select(InboundMessage)).scalar_one().duplicates == 1


def test_a_delivery_sent_four_times_at_once_is_recorded_once(client, db, dispatcher_headers):
    run, (a, _), kim = on_the_road(client, db, dispatcher_headers)
    sig, _ = put_file(client, kim)
    body = {"message_id": str(uuid.uuid4()), "at": now(), "signature_file": str(sig),
            "signed_name": "Priya", "packages": [{"package_no": 1, "how": "scan"}]}

    def send():
        with fresh_client() as c:
            r = c.post(f"/v1/runs/{run['id']}/stops/{a.id}/deliver", headers=kim, json=body)
            return r.status_code, r.json()

    answers = all_at_once(4, send)
    assert [code for code, _ in answers] == [200] * 4
    assert all(b == answers[0][1] for _, b in answers)
    assert len(db.execute(select(Pod)).scalars().all()) == 1


def test_a_second_different_confirmation_for_a_stop_is_refused(client, db, dispatcher_headers):
    run, (a, _), kim = on_the_road(client, db, dispatcher_headers)
    delivered(client, kim, run["id"], a)
    r = delivered(client, kim, run["id"], a)
    assert r.status_code == 409 and r.json() == {
        "code": "stop_done",
        "detail": f"stop 1 on {run['run_no']} is already recorded as delivered"}


def test_nothing_is_delivered_before_the_run_leaves(client, db, dispatcher_headers):
    run, (a, _) = planned_run(client, db, dispatcher_headers)
    r = delivered(client, driver_headers(client), run["id"], a)
    assert r.status_code == 409 and r.json()["code"] == "run_not_started"


def test_every_package_is_named_once(client, db, dispatcher_headers):
    run, (a, _), kim = on_the_road(client, db, dispatcher_headers)
    r = delivered(client, kim, run["id"], a, packages=[])
    assert r.status_code == 422 and r.json()["errors"][0]["field"] == "packages"
    r = delivered(client, kim, run["id"], a, packages=[{"package_no": 1, "how": "tick"}] * 2)
    assert r.status_code == 422


def test_a_delivery_needs_a_signature_and_name_or_a_photo(client, db, dispatcher_headers):
    run, (a, _), kim = on_the_road(client, db, dispatcher_headers)
    photo, _ = put_file(client, kim, kind="photo", data=JPEG, content_type="image/jpeg")
    sig, _ = put_file(client, kim)
    nothing = delivered(client, kim, run["id"], a, signature_file=None)
    assert nothing.status_code == 422
    assert "a signature with a printed name, a photo, or both" in nothing.text
    unnamed = delivered(client, kim, run["id"], a, signature_file=str(sig))
    assert unnamed.status_code == 422 and "printed name" in unnamed.text
    assert delivered(client, kim, run["id"], a, photo_file=str(photo)).status_code == 200


def test_a_picture_must_have_been_sent_by_the_same_phone(client, db, dispatcher_headers):
    run, (a, _), kim = on_the_road(client, db, dispatcher_headers)
    never = delivered(client, kim, run["id"], a, signature_file=str(uuid.uuid4()),
                      signed_name="Priya")
    assert never.status_code == 422 and never.json()["errors"][0]["field"] == "signature_file"
    driver(db, code="mel", name="Mel")
    mels, _ = put_file(client, driver_headers(client, code="mel"))
    theirs = delivered(client, kim, run["id"], a, signature_file=str(mels), signed_name="Priya")
    assert theirs.status_code == 422


def test_a_picture_belongs_to_one_stop(client, db, dispatcher_headers):
    run, (a, b), kim = on_the_road(client, db, dispatcher_headers)
    sig, _ = put_file(client, kim)
    assert delivered(client, kim, run["id"], a, signature_file=str(sig),
                     signed_name="Priya").status_code == 200
    r = delivered(client, kim, run["id"], b, signature_file=str(sig), signed_name="Priya")
    assert r.status_code == 422 and "already belongs to another stop" in r.text


def test_the_phones_time_is_kept_but_never_in_the_future(client, db, dispatcher_headers):
    run, (a, b), kim = on_the_road(client, db, dispatcher_headers)
    earlier = datetime.now(UTC) - timedelta(hours=3)
    delivered(client, kim, run["id"], a, at=earlier.isoformat())
    delivered(client, kim, run["id"], b, at=(datetime.now(UTC) + timedelta(days=2)).isoformat())
    pods = db.execute(select(Pod).order_by(Pod.id)).scalars().all()
    assert abs(pods[0].at - earlier) < timedelta(seconds=1)
    assert pods[1].at <= datetime.now(UTC)
    r = delivered(client, kim, run["id"], a, at="2026-10-14T10:00:00")
    assert r.status_code == 422


def test_a_driver_delivers_only_on_their_own_run(client, db, dispatcher_headers):
    run, (a, _), _ = on_the_road(client, db, dispatcher_headers)
    driver(db, code="mel", name="Mel")
    r = delivered(client, driver_headers(client, code="mel"), run["id"], a)
    assert r.status_code == 404
```

`api/tests/test_fail_and_replan.py` (Task 7 adds the re-planning):
```python
"""Couldn't deliver: the reason is kept, the cartons come back, and the
dispatcher plans the consignment again on another day."""
from sqlalchemy import select

from deliveries_samples import JPEG, failed, on_the_road, put_file
from tms.models import Pod


def test_a_stop_that_couldnt_be_delivered_says_why(client, db, dispatcher_headers):
    run, (a, b), kim = on_the_road(client, db, dispatcher_headers)
    photo, _ = put_file(client, kim, kind="photo", data=JPEG, content_type="image/jpeg")
    r = failed(client, kim, run["id"], b, reason="closed", note="Rang the number, no answer.",
               photo_file=str(photo))
    assert r.status_code == 200, r.text
    assert r.json()["outcome"] == "failed" and r.json()["sequence"] == 2
    pod = db.execute(select(Pod)).scalar_one()
    assert (pod.reason, pod.note, pod.photo_file_id) == ("closed", "Rang the number, no answer.",
                                                         photo)
    detail = client.get(f"/v1/consignments/{b.id}", headers=dispatcher_headers).json()
    assert detail["status"] == "failed"
    assert detail["history"][-1]["kind"] == "failed"
    assert detail["history"][-1]["detail"] == {
        "run": run["run_no"], "run_id": run["id"], "pod_id": pod.id, "reason": "closed",
        "note": "Rang the number, no answer.", "photo_file": str(photo), "by": "kim"}
    lane = client.get(f"/v1/runs/{run['id']}", headers=dispatcher_headers).json()
    assert lane["stops"][1]["outcome"] == "failed" and lane["stops"][1]["reason"] == "closed"


def test_only_the_four_reasons(client, db, dispatcher_headers):
    run, (a, _), kim = on_the_road(client, db, dispatcher_headers)
    r = failed(client, kim, run["id"], a, reason="lunch")
    assert r.status_code == 422 and r.json()["errors"][0]["field"] == "reason"
    assert failed(client, kim, run["id"], a, reason="wrong_address").status_code == 200
```

**Step 2: Run them to see them fail**

Run: `.venv/bin/pytest tests/test_deliver.py tests/test_fail_and_replan.py -q`
Expected: 13 failed, mostly `404` (no routes yet).

**Step 3: The service**

`api/tms/services/deliveries.py`: replace the imports with
```python
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from tms.api.errors import Conflict, FieldError, NotFound
from tms.models import ConsignmentEvent, Pod, PodFile, Run, Stop
from tms.services import runs
from tms.services.envelope import sender_of
```
add after them
```python
MICRO = Decimal("0.000001")
TENTH = Decimal("0.1")


@dataclass(frozen=True)
class Where:
    latitude: Decimal
    longitude: Decimal
    accuracy_m: Decimal | None
```
and append:
```python
def _locked(db: Session, who, run_id: int) -> Run:
    db.execute(select(Run.id).where(Run.id == run_id).with_for_update())
    return own_run(db, who, run_id)


def _on_the_road(run: Run) -> None:
    if run.status == "planned":
        raise Conflict("run_not_started", f"{run.number} hasn't left yet: start it first")
    if run.status == "done":
        raise Conflict("run_done", f"{run.number} is finished")
    if run.status == "cancelled":
        raise Conflict("run_cancelled", f"{run.number} was cancelled")


def _open_stop(db: Session, run: Run, consignment_id: int) -> Stop:
    stop = db.execute(
        select(Stop).where(Stop.run_id == run.id, Stop.consignment_id == consignment_id,
                           Stop.removed_at.is_(None))
        .execution_options(populate_existing=True)).scalar_one_or_none()
    if stop is None:
        raise NotFound(f"{run.number} has no stop for consignment {consignment_id}")
    if stop.outcome is not None:
        said = "delivered" if stop.outcome == "delivered" else "not delivered"
        raise Conflict("stop_done",
                       f"stop {stop.sequence} on {run.number} is already recorded as {said}")
    return stop


def _file(db: Session, who, file_id: uuid.UUID | None, kind: str) -> PodFile | None:
    if file_id is None:
        return None
    row = db.get(PodFile, file_id)
    if row is None or row.uploaded_by != sender_of(who) or row.kind != kind:
        raise FieldError(f"{kind}_file", f"no {kind} {file_id} has been sent")
    used = db.execute(select(Pod.id).where(
        or_(Pod.signature_file_id == file_id, Pod.photo_file_id == file_id))).first()
    if used is not None:
        raise FieldError(f"{kind}_file", f"{kind} {file_id} already belongs to another stop")
    return row


def _record(db: Session, who, run: Run, stop: Stop, *, message_id: uuid.UUID, outcome: str,
            at: datetime, where: Where | None, detail: dict, **pod) -> Pod:
    """The pod, the stop's outcome, the consignment's status and a history
    row, together. The time is the phone's, but never later than now."""
    when = min(at, datetime.now(UTC))
    row = Pod(message_id=message_id, run_id=run.id, stop_id=stop.id,
              consignment_id=stop.consignment_id, outcome=outcome, at=when,
              latitude=where.latitude.quantize(MICRO) if where else None,
              longitude=where.longitude.quantize(MICRO) if where else None,
              accuracy_m=(where.accuracy_m.quantize(TENTH)
                          if where and where.accuracy_m is not None else None),
              recorded_by=who.name, **pod)
    db.add(row)
    db.flush()
    stop.outcome = outcome
    stop.outcome_at = when
    c = stop.consignment
    c.status = outcome
    # the position stays on the pod only: history is kept for good, positions are not
    c.events.append(ConsignmentEvent(kind=outcome, at=when, detail={
        "run": run.number, "run_id": run.id, "pod_id": row.id, **detail, "by": who.name}))
    db.flush()
    return row


def deliver(db: Session, who, run_id: int, consignment_id: int, *, message_id: uuid.UUID,
            at: datetime, packages: list[dict], signed_name: str | None,
            signature_file: uuid.UUID | None, photo_file: uuid.UUID | None,
            note: str | None, where: Where | None) -> tuple[Run, Stop, Pod]:
    run = _locked(db, who, run_id)
    _on_the_road(run)
    stop = _open_stop(db, run, consignment_id)
    expected = sorted(p.package_no for p in stop.consignment.packages)
    if sorted(p["package_no"] for p in packages) != expected:
        raise FieldError("packages", f"name each of the {len(expected)} packages once")
    signature = _file(db, who, signature_file, "signature")
    photo = _file(db, who, photo_file, "photo")
    pod = _record(db, who, run, stop, message_id=message_id, outcome="delivered", at=at,
                  where=where, packages=packages, signed_name=signed_name, note=note,
                  signature_file_id=signature.id if signature else None,
                  photo_file_id=photo.id if photo else None,
                  detail={"packages": len(packages), "signed_name": signed_name,
                          "signature_file": str(signature.id) if signature else None,
                          "photo_file": str(photo.id) if photo else None})
    return run, stop, pod


def fail(db: Session, who, run_id: int, consignment_id: int, *, message_id: uuid.UUID,
         at: datetime, reason: str, note: str | None, photo_file: uuid.UUID | None,
         where: Where | None) -> tuple[Run, Stop, Pod]:
    """Couldn't deliver: the consignment stays on the truck and comes back to
    the depot. It is `failed` until a dispatcher puts it on another run."""
    run = _locked(db, who, run_id)
    _on_the_road(run)
    stop = _open_stop(db, run, consignment_id)
    photo = _file(db, who, photo_file, "photo")
    pod = _record(db, who, run, stop, message_id=message_id, outcome="failed", at=at,
                  where=where, reason=reason, note=note, packages=[],
                  photo_file_id=photo.id if photo else None,
                  detail={"reason": reason, "note": note,
                          "photo_file": str(photo.id) if photo else None})
    return run, stop, pod


def tally(db: Session, run_id: int) -> dict[str | None, int]:
    """How many of a run's stops are delivered, failed, and still to go (None)."""
    rows = db.execute(select(Stop.outcome, func.count()).where(
        Stop.run_id == run_id, Stop.removed_at.is_(None)).group_by(Stop.outcome)).all()
    return {outcome: n for outcome, n in rows}
```

The run's row is locked first (`_locked`), so two different confirmations for one run take turns; the second for a stop then finds its outcome and is refused `409 stop_done`. The same confirmation twice never gets that far: the envelope's lock and stored reply answer it. The `pod`'s unique `stop_id` stands behind both.

**Step 4: The routes**

`api/tms/api/routes/deliveries.py`: replace the imports with
```python
from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Query
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from tms.api.deps import DB, Principal, require, require_any
from tms.api.errors import Forbidden
from tms.api.schemas import Dec
from tms.models import Run
from tms.services import deliveries
from tms.services.envelope import handle
```
and append:
```python
class Position(BaseModel):
    """Where the phone was, as decimal strings ("-37.562210"); more than six
    places is rounded, never refused."""

    model_config = ConfigDict(extra="forbid")
    latitude: Decimal = Field(ge=-90, le=90)
    longitude: Decimal = Field(ge=-180, le=180)
    accuracy_m: Decimal | None = Field(default=None, ge=0, le=1_000_000)

    def where(self) -> deliveries.Where:
        return deliveries.Where(self.latitude, self.longitude, self.accuracy_m)


class PackageTick(BaseModel):
    model_config = ConfigDict(extra="forbid")
    package_no: int = Field(ge=1)
    how: Literal["scan", "tick"]


class DeliverIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    message_id: uuid.UUID
    at: AwareDatetime
    packages: list[PackageTick] = Field(max_length=500)
    signed_name: str | None = Field(default=None, max_length=120)
    signature_file: uuid.UUID | None = None
    photo_file: uuid.UUID | None = None
    note: str | None = Field(default=None, max_length=500)
    position: Position | None = None

    @model_validator(mode="after")
    def _proof(self):
        if self.signature_file and not self.signed_name:
            raise ValueError("a signature needs the printed name with it")
        if self.signed_name and not self.signature_file:
            raise ValueError("a printed name needs the signature with it")
        if not self.signature_file and not self.photo_file:
            raise ValueError("a delivery needs a signature with a printed name, a photo, or both")
        return self


class FailIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    message_id: uuid.UUID
    at: AwareDatetime
    reason: Literal["closed", "refused", "damaged", "wrong_address"]
    note: str | None = Field(default=None, max_length=500)
    photo_file: uuid.UUID | None = None
    position: Position | None = None


class StopDoneOut(BaseModel):
    message_id: uuid.UUID
    run_id: int
    run_no: str
    consignment_id: int
    sequence: int
    outcome: str
    at: dt.datetime
    pod_id: int
    done: int
    left: int


def _stop_done(db, message_id, run, stop, pod) -> StopDoneOut:
    t = deliveries.tally(db, run.id)
    return StopDoneOut(message_id=message_id, run_id=run.id, run_no=run.number,
                       consignment_id=stop.consignment_id, sequence=stop.sequence,
                       outcome=pod.outcome, at=pod.at, pod_id=pod.id,
                       done=t.get("delivered", 0) + t.get("failed", 0), left=t.get(None, 0))


@router.post("/runs/{id}/stops/{consignment_id}/deliver", response_model=StopDoneOut)
def deliver(id: int, consignment_id: int, body: DeliverIn, db: DB,
            who: Principal = require("deliveries:write")):
    def work():
        run, stop, pod = deliveries.deliver(
            db, who, id, consignment_id, message_id=body.message_id, at=body.at,
            packages=[p.model_dump() for p in body.packages], signed_name=body.signed_name,
            signature_file=body.signature_file, photo_file=body.photo_file, note=body.note,
            where=body.position.where() if body.position else None)
        return _stop_done(db, body.message_id, run, stop, pod)
    return handle(db, who, body.message_id, f"/v1/runs/{id}/stops/{consignment_id}/deliver", work)


@router.post("/runs/{id}/stops/{consignment_id}/fail", response_model=StopDoneOut)
def fail(id: int, consignment_id: int, body: FailIn, db: DB,
         who: Principal = require("deliveries:write")):
    def work():
        run, stop, pod = deliveries.fail(
            db, who, id, consignment_id, message_id=body.message_id, at=body.at,
            reason=body.reason, note=body.note, photo_file=body.photo_file,
            where=body.position.where() if body.position else None)
        return _stop_done(db, body.message_id, run, stop, pod)
    return handle(db, who, body.message_id, f"/v1/runs/{id}/stops/{consignment_id}/fail", work)
```

**Step 5: Each stop's outcome on the run**

`api/tms/api/routes/runs.py`:
- import `Pod`: `from tms.models import Consignment, OutboundCall, Pod, PrintJob, Run`
- `StopOut` gains three fields with defaults:
```python
class StopOut(BaseModel):
    sequence: int
    consignment: StopConsignment
    # what the driver recorded: delivered or failed (with the reason), or null
    outcome: str | None = None
    outcome_at: dt.datetime | None = None
    reason: str | None = None
```
- `RunOut` gains `finished_at: dt.datetime | None` after `departed_at`
- in `run_out`, before `calls = …`:
```python
    reasons = dict(db.execute(select(Pod.stop_id, Pod.reason).where(
        Pod.run_id == run.id, Pod.outcome == "failed")).all())
```
  and the `RunOut(...)` arguments from `created_by=` to the stops become
```python
        created_by=run.created_by, created_at=run.created_at, departed_at=run.departed_at,
        finished_at=run.finished_at,
        stops=[StopOut(sequence=s.sequence, consignment=stop_consignment(s.consignment),
                       outcome=s.outcome, outcome_at=s.outcome_at, reason=reasons.get(s.id))
               for s in run.stops],
```

**Step 6: Run the tests**

Run: `.venv/bin/pytest tests/test_deliver.py tests/test_fail_and_replan.py -q && .venv/bin/pytest -q`
Expected: `13 passed`, then `227 passed`. Both "four at once" tests fail without the envelope's lock (checked).

**Step 7: Commit**

```bash
git add api/tms/services/deliveries.py api/tms/api/routes/deliveries.py api/tms/api/routes/runs.py \
        api/tests/test_deliver.py api/tests/test_fail_and_replan.py
git commit -m "Record a delivery or the reason there wasn't one, once per stop" \
           -m "Each confirmation carries a message_id; a repeat gets the first reply
back. Signatures and photos are named by the ids the phone gave them."
```

### Task 7: What came back is planned again

**Files:**
- Modify: `api/tms/services/runs.py:129-130` (`PLANNABLE`) and `:175-176` (`assign`'s live stop)
- Test: `api/tests/test_fail_and_replan.py`

**Step 1: Write the failing tests**

In `api/tests/test_fail_and_replan.py`, replace the imports with
```python
from datetime import timedelta

from sqlalchemy import select

from deliveries_samples import JPEG, delivered, failed, on_the_road, put_file
from runs_samples import DAY, assign, new_run, order, vehicle
from tms.models import Pod
```
and append:
```python
def test_a_failed_consignment_is_planned_again_on_another_day(client, db, dispatcher_headers):
    run, (a, b), kim = on_the_road(client, db, dispatcher_headers)
    delivered(client, kim, run["id"], a)
    failed(client, kim, run["id"], b, reason="closed")
    # it waits in the tray with the packed ones
    tray = client.get("/v1/consignments", params={"status": "packed,failed"},
                      headers=dispatcher_headers).json()
    assert [c["id"] for c in tray["items"]] == [b.id]

    vehicle(db, rego="TMS018")
    tomorrow = new_run(client, dispatcher_headers, rego="TMS018", day=DAY + timedelta(days=1))
    r = assign(client, dispatcher_headers, tomorrow["id"], b.id)
    assert r.status_code == 200, r.text
    assert order(r.json()) == [b.id] and r.json()["stops"][0]["consignment"]["status"] == "assigned"
    # today's run still shows what happened there
    today = client.get(f"/v1/runs/{run['id']}", headers=dispatcher_headers).json()
    assert [s["outcome"] for s in today["stops"]] == ["delivered", "failed"]
    kinds = [e["kind"] for e in client.get(f"/v1/consignments/{b.id}",
                                           headers=dispatcher_headers).json()["history"]]
    assert kinds == ["packed", "assigned", "departed", "failed", "assigned"]
    # and it can come off again, back to the tray
    off = client.post(f"/v1/runs/{tomorrow['id']}/unassign", headers=dispatcher_headers,
                      json={"consignment_id": b.id})
    assert off.status_code == 200 and off.json()["stops"] == []


def test_a_delivered_consignment_is_not_planned_again(client, db, dispatcher_headers):
    run, (a, _), kim = on_the_road(client, db, dispatcher_headers)
    delivered(client, kim, run["id"], a)
    vehicle(db, rego="TMS018")
    other = new_run(client, dispatcher_headers, rego="TMS018", day=DAY + timedelta(days=1))
    r = assign(client, dispatcher_headers, other["id"], a.id)
    assert r.status_code == 409 and r.json()["code"] == "not_plannable"
```

**Step 2: Run them to see them fail**

Run: `.venv/bin/pytest tests/test_fail_and_replan.py -q`
Expected: `test_a_failed_consignment_is_planned_again_on_another_day` fails: `409 not_plannable` ("Customer 1 (0080034501) has failed, so it can't be planned").

**Step 3: Implement**

`api/tms/services/runs.py`:
```python
# a consignment that may still go on a run, or move to another; one the driver
# couldn't deliver comes back to the depot and is planned again
PLANNABLE = ("packed", "assigned", "failed")
```
and in `assign`:
```python
    # its stop on a run now; a stop on a trip already made (it has an outcome) is a record
    current = db.execute(select(Stop).where(
        Stop.consignment_id == c.id, Stop.removed_at.is_(None),
        Stop.outcome.is_(None))).scalar_one_or_none()
```
Without the second change, `assign` would find the failed stop on the run that has left and refuse with `run_not_planned`; without Task 2's new index, the new stop would break `uq_stop_consignment_id_live`.

**Step 4: Run the tests**

Run: `.venv/bin/pytest tests/test_fail_and_replan.py tests/test_planning.py -q && .venv/bin/pytest -q`
Expected: all pass, then `229 passed`.

**Step 5: Commit**

```bash
git add api/tms/services/runs.py api/tests/test_fail_and_replan.py
git commit -m "Plan a consignment that came back undelivered onto another run"
```

### Task 8: Finishing the run

**Files:**
- Modify: `api/tms/services/deliveries.py` (`finish`)
- Modify: `api/tms/api/routes/deliveries.py` (`POST /v1/runs/{id}/finish`, for the run's driver or the office)
- Modify: `api/tms/services/runs.py` (`_planned`'s message for a finished run)
- Test: `api/tests/test_finish_run.py`

**Step 1: Write the failing tests**

`api/tests/test_finish_run.py`:
```python
"""Back at the depot: the run is done once every stop is delivered or has a
reason it wasn't."""
import uuid

from deliveries_samples import delivered, failed, on_the_road


def _finish(client, headers, run_id, message_id=None):
    return client.post(f"/v1/runs/{run_id}/finish", headers=headers,
                       json={"message_id": str(message_id or uuid.uuid4())})


def test_a_run_with_stops_left_is_not_finished(client, db, dispatcher_headers):
    run, (a, _), kim = on_the_road(client, db, dispatcher_headers)
    delivered(client, kim, run["id"], a)
    r = _finish(client, kim, run["id"])
    assert r.status_code == 409 and r.json() == {
        "code": "stops_left", "detail": f"{run['run_no']} still has 1 stop to deliver or explain"}


def test_a_run_is_finished_when_every_stop_is_done(client, db, dispatcher_headers):
    run, (a, b), kim = on_the_road(client, db, dispatcher_headers)
    delivered(client, kim, run["id"], a)
    failed(client, kim, run["id"], b)
    message_id = uuid.uuid4()
    r = _finish(client, kim, run["id"], message_id)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["status"] == "done" and out["finished_at"] is not None
    assert (out["delivered"], out["failed"]) == (1, 1)
    assert _finish(client, kim, run["id"], message_id).json() == out
    # a new message for a finished run changes nothing either
    assert _finish(client, kim, run["id"]).json()["finished_at"] == out["finished_at"]
    lane = client.get(f"/v1/runs/{run['id']}", headers=dispatcher_headers).json()
    assert lane["status"] == "done" and lane["finished_at"] == out["finished_at"]


def test_nothing_is_recorded_on_a_finished_run(client, db, dispatcher_headers):
    run, (a, b), kim = on_the_road(client, db, dispatcher_headers)
    delivered(client, kim, run["id"], a)
    failed(client, kim, run["id"], b)
    _finish(client, kim, run["id"])
    r = client.post(f"/v1/runs/{run['id']}/start", headers=kim,
                    json={"message_id": str(uuid.uuid4())})
    assert r.status_code == 409 and r.json()["code"] == "run_done"
    r = client.post(f"/v1/runs/{run['id']}/depart", headers=dispatcher_headers)
    assert r.status_code == 409 and r.json() == {
        "code": "run_not_planned", "detail": f"{run['run_no']} is finished; its stops can't change"}


def test_the_office_finishes_a_run_the_driver_left_open(client, db, dispatcher_headers):
    run, (a, b), kim = on_the_road(client, db, dispatcher_headers)
    delivered(client, kim, run["id"], a)
    r = _finish(client, dispatcher_headers, run["id"])
    assert r.status_code == 409 and r.json()["code"] == "stops_left"
    failed(client, kim, run["id"], b)
    r = _finish(client, dispatcher_headers, run["id"])
    assert r.status_code == 200 and r.json()["status"] == "done"


def test_finishing_takes_a_driver_or_runs_write(client, db, dispatcher_headers, capsys):
    from conftest import bearer
    from tms.cli import main
    run, _, _ = on_the_road(client, db, dispatcher_headers)
    assert main(["create-api-key", "--name", "viewer", "--scopes", "runs:read"]) == 0
    raw = capsys.readouterr().out.strip().splitlines()[-1]
    assert _finish(client, bearer(raw), run["id"]).status_code == 403
```

**Step 2: Run them to see them fail**

Run: `.venv/bin/pytest tests/test_finish_run.py -q`
Expected: 5 failed (`404`, no route).

**Step 3: Implement**

`api/tms/services/deliveries.py`, append:
```python
def finish(db: Session, who, run_id: int) -> Run:
    """Back at the depot: every stop is delivered or has a reason it wasn't.
    Finishing a finished run changes nothing."""
    run = _locked(db, who, run_id)
    if run.status == "done":
        return run
    _on_the_road(run)
    left = tally(db, run.id).get(None, 0)
    if left:
        raise Conflict("stops_left", f"{run.number} still has {left} "
                                     f"{'stop' if left == 1 else 'stops'} to deliver or explain")
    run.status = "done"
    run.finished_at = datetime.now(UTC)
    db.flush()
    return run
```

`api/tms/api/routes/deliveries.py`, append (the driver finishes their own run; the office, with `runs:write`, may finish any, for a driver who is back but didn't press Finish):
```python
class FinishOut(BaseModel):
    message_id: uuid.UUID
    run_id: int
    run_no: str
    status: str
    finished_at: dt.datetime | None
    delivered: int
    failed: int


@router.post("/runs/{id}/finish", response_model=FinishOut)
def finish_run(id: int, body: MessageIn, db: DB,
               who: Principal = require_any("deliveries:write", "runs:write")):
    """The driver back at the depot, or the office for them (runs:write)."""
    def work():
        run = deliveries.finish(db, who, id)
        t = deliveries.tally(db, run.id)
        return FinishOut(message_id=body.message_id, run_id=run.id, run_no=run.number,
                         status=run.status, finished_at=run.finished_at,
                         delivered=t.get("delivered", 0), failed=t.get("failed", 0))
    return handle(db, who, body.message_id, f"/v1/runs/{id}/finish", work)
```

`api/tms/services/runs.py`: "R-0001 has done" reads badly, so `_planned` says how the run is gone:
```python
# how a run that is past planning is described
GONE = {"departed": "has left", "done": "is finished", "cancelled": "was cancelled"}


def _planned(db: Session, run_id: int) -> Run:
    """The run, read after the lock, and still open to changes."""
    run = get_run(db, run_id)
    if run.status != "planned":
        raise Conflict("run_not_planned",
                       f"{run.number} {GONE.get(run.status, run.status)}; its stops can't change")
    return run
```
(No test reads the old wording; the desktop shows the API's `detail` as it is.)

**Step 4: Run the tests**

Run: `.venv/bin/pytest tests/test_finish_run.py -q && .venv/bin/pytest -q`
Expected: `5 passed`, then `234 passed`.

**Step 5: Commit**

```bash
git add api/tms/services/deliveries.py api/tms/api/routes/deliveries.py api/tms/services/runs.py \
        api/tests/test_finish_run.py
git commit -m "Finish a run once every stop is delivered or explained"
```

### Task 9: Driver codes match whatever the case

A phone's keyboard capitalises what it likes, so `KIM`, `Kim` and `kim` are one driver. Codes are stored lower case (a check constraint says so), made lower case when a driver is made, and looked up lower case in the address, at sign-in and when a run names its driver. Two codes that differ only by case can no longer both exist; an install that already has such a pair stops at the migration and says which, rather than guessing which driver is which.

**Files:**
- Modify: `api/tms/api/routes/drivers.py` (`DriverIn`, `_get`)
- Modify: `api/tms/api/routes/auth.py:151-153` (`driver_login`)
- Modify: `api/tms/services/runs.py:74-76` (`_driver`)
- Modify: `api/tms/models/access.py` (`Driver`'s check constraint)
- Create: `api/alembic/versions/0004_driver_codes_lower_case.py`
- Test: `api/tests/test_drivers.py`, `api/tests/test_runs.py`, `api/tests/test_migrations.py`

**Step 1: Write the failing tests**

Append to `api/tests/test_drivers.py`:
```python


def test_a_code_is_kept_lower_case_and_is_one_code_whatever_the_case(client, manager_headers):
    out = make_driver(client, manager_headers, {**ALEX, "code": "KIM-07"})
    assert out["code"] == "kim-07"
    r = client.post("/v1/drivers", headers=manager_headers, json={**ALEX, "code": "Kim-07"})
    assert r.status_code == 422
    assert r.json()["errors"] == [{"field": "code", "message": "driver kim-07 already exists"}]


def test_a_code_in_the_address_is_read_whatever_the_case(client, manager_headers):
    make_driver(client, manager_headers)
    assert client.get("/v1/drivers/DRV-01", headers=manager_headers).json()["code"] == "drv-01"
    r = client.patch("/v1/drivers/Drv-01", headers=manager_headers, json={"name": "Alex Tran"})
    assert r.status_code == 200 and r.json()["name"] == "Alex Tran"


def test_a_driver_signs_in_whatever_the_case_of_the_code(client, manager_headers):
    make_driver(client, manager_headers)
    for typed in ("DRV-01", "Drv-01", " drv-01 "):
        r = client.post("/v1/auth/driver-login", json={"code": typed, "pin": "2468"})
        assert r.status_code == 200, typed
```

Append to `api/tests/test_runs.py`:
```python


def test_the_driver_is_read_however_the_code_is_typed(client, db, dispatcher_headers):
    vehicle(db)
    driver(db)
    assert new_run(client, dispatcher_headers, code="KIM")["driver"]["code"] == "kim"
```

Append to `api/tests/test_migrations.py` (the `blank_database` fixture gives each a database of its own):
```python


def _driver_row(conn, code: str) -> None:
    conn.execute(text("insert into driver (code, name, pin_hash, failed_attempts, locked, active) "
                      "values (:c, :c, 'x', 0, false, true)"), {"c": code})


def test_existing_driver_codes_become_lower_case(blank_database):
    from sqlalchemy.exc import IntegrityError

    cfg = alembic_config(blank_database)
    command.upgrade(cfg, "0003")
    engine = create_engine(blank_database)
    with engine.begin() as conn:
        _driver_row(conn, "KIM-07")
        _driver_row(conn, "sam")
    command.upgrade(cfg, "head")
    with engine.connect() as conn:
        assert sorted(conn.execute(text("select code from driver")).scalars()) == ["kim-07", "sam"]
    with pytest.raises(IntegrityError, match="ck_driver_code_lower"):
        with engine.begin() as conn:
            _driver_row(conn, "MEL")
    engine.dispose()


def test_codes_that_differ_only_by_case_stop_the_upgrade_and_say_which(blank_database):
    cfg = alembic_config(blank_database)
    command.upgrade(cfg, "0003")
    engine = create_engine(blank_database)
    with engine.begin() as conn:
        _driver_row(conn, "Kim")
        _driver_row(conn, "KIM")
    with pytest.raises(RuntimeError, match="differ only by case") as refused:
        command.upgrade(cfg, "head")
    assert "Kim" in str(refused.value) and "KIM" in str(refused.value)
    with engine.connect() as conn:
        assert sorted(conn.execute(text("select code from driver")).scalars()) == ["KIM", "Kim"]
    engine.dispose()
```

**Step 2: Run them to see them fail**

Run: `.venv/bin/pytest tests/test_drivers.py tests/test_runs.py tests/test_migrations.py -q`
Expected: 6 failed: `KIM-07` comes back as typed, the lookups in the address, at sign-in and on a run are `404`/`401`/`422`, and there is no revision `0004` past `0003` to lower-case anything.

**Step 3: The code**

`api/tms/api/routes/drivers.py`: import `field_validator` from pydantic, and give `DriverIn` (after `pin`):
```python
    @field_validator("code")
    @classmethod
    def _lower(cls, code: str) -> str:
        # a driver types their code however the phone's keyboard likes
        return code.lower()
```
`_get` looks the code up as `Driver.code == code.lower()`. `create_driver` needs nothing more: its check for a taken code, and `uq_driver_code` behind it, now see the lower-case code, so `Kim-07` after `KIM-07` is `422` "driver kim-07 already exists".

`api/tms/api/routes/auth.py`, in `driver_login`:
```python
        select(Driver).where(Driver.code == body.code.lower()).with_for_update()
```
`api/tms/services/runs.py`, in `_driver`:
```python
    d = db.execute(select(Driver).where(Driver.code == code.lower())).scalar_one_or_none()
```
`api/tms/models/access.py`: import `CheckConstraint` from sqlalchemy, and in `Driver`, after `__tablename__`:
```python
    # codes are kept lower case, so KIM-07 and kim-07 are one driver
    __table_args__ = (CheckConstraint("code = lower(code)", name="code_lower"),)
```

**Step 4: The migration**

`api/alembic/versions/0004_driver_codes_lower_case.py`. It runs in the migration's transaction, so a clash leaves every code as it was; driver tokens carry the driver's id, not the code, so nobody is signed out:
```python
"""driver codes kept lower case, so a code matches however it is typed

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-11
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    clashes = op.get_bind().execute(sa.text(
        "select string_agg(code, ' and ' order by code) from driver "
        "group by lower(code) having count(*) > 1")).scalars().all()
    if clashes:
        raise RuntimeError(
            "driver codes that differ only by case: " + "; ".join(clashes)
            + ". Codes are now one whatever the case, so rename all but one of each (in psql: "
            "update driver set code = '<new code>' where code = '<old code>'), then upgrade again.")
    op.execute("update driver set code = lower(code) where code <> lower(code)")
    op.create_check_constraint(op.f("ck_driver_code_lower"), "driver", "code = lower(code)")


def downgrade() -> None:
    # the codes stay lower case: there is no telling how they were typed
    op.drop_constraint(op.f("ck_driver_code_lower"), "driver", type_="check")
```

**Step 5: Run the tests**

Run: `.venv/bin/pytest tests/test_drivers.py tests/test_runs.py tests/test_migrations.py -q && .venv/bin/pytest -q`
Expected: all pass, then `240 passed`.

**Step 6: Commit**

```bash
git add api/tms/api/routes/drivers.py api/tms/api/routes/auth.py api/tms/services/runs.py \
        api/tms/models/access.py api/alembic/versions/0004_driver_codes_lower_case.py \
        api/tests/test_drivers.py api/tests/test_runs.py api/tests/test_migrations.py
git commit -m "Match a driver's code whatever its case, and keep codes lower case"
```

### Task 10: A volume for the pictures, and `docs/api.md`

**Files:**
- Modify: `docker-compose.yml` (the `api` service's volume, and `pod_files`)
- Modify: `docs/api.md`

**Step 1: Compose**

In `docker-compose.yml`, the `api` service gains, after its `environment`:
```yaml
    volumes:
      # signatures and photos for proof of delivery (TMS_POD_DIR)
      - pod_files:/data/pod
```
and the top-level `volumes:` gains `pod_files:` after `pgdata:`. The worker never reads the pictures, so it does not mount it. The API runs as root in its image, so it can make `/data/pod`'s folders.

**Step 2: `docs/api.md`**

- **Shared rules**, a new bullet after **Errors**:
```markdown
- **Confirmations from the driver app** (start, deliver, fail, finish) carry a
  `message_id`, a UUID the phone makes when the confirmation is made. The
  first time, the API does the work and keeps the reply; the same
  `message_id` again from the same sender gets that reply back (status and
  body) and changes nothing, however long after. A refusal is not kept, so the
  same message can be sent again once whatever stopped it is put right. A
  `message_id` someone else has used is `409 message_id_taken`.
```
- **Scopes** table: the driver row becomes `` `fleet:read`, `deliveries:read`, `deliveries:write` (on their own runs only) ``, and the API key row's list gains `deliveries:read`, `deliveries:write`.
- **Drivers**: after the `POST /v1/drivers` bullet: "A code is kept lower case: `KIM-07` is stored, shown and signed in with as `kim-07`, and is found in the address however it is typed (`/v1/drivers/KIM-07`). A code that differs from one on file only by case is `422` on `code`." And in **Sign in**, `POST /v1/auth/driver-login`: "The code is matched whatever its case."
- **Consignments**: "A driver cannot see them yet." stays true. The statuses paragraph at the end of `GET /v1/consignments/{id}` becomes:
```markdown
Statuses: `packed` (in the tray), `assigned` (on a planned run), `departed`
(its run has left), `delivered`, and `failed` (the driver couldn't deliver
it: it comes back to the depot and waits in the tray to be planned again).
The history has a row for each: `packed`, `assigned` and `unassigned` (with
the run and who), `departed` (with the run, vehicle, driver and who),
`delivered` and `failed` (see "The driver app"). A consignment planned again
after `failed` gets `assigned` and `departed` rows again.
```
- **Runs**: in the example add `"finished_at": null` after `"departed_at": null`, and `"outcome": null, "outcome_at": null, "reason": null` to the stop, beside `"sequence"`. The sentence on `status` becomes:
```markdown
`status` is `planned`, `departed`, `done` (the driver finished it) or
`cancelled`. Once a run has left, each stop's `outcome` is `delivered`,
`failed` (with the driver's `reason`: `closed`, `refused`, `damaged`,
`wrong_address`) or `null` while it is still to go.
```
- **Planning the stops**: `assign` "Puts a `packed` consignment" becomes "Puts a `packed` consignment (or a `failed` one, back from a run)"; the `not_plannable` row reads "the consignment has left or been delivered".
- **`POST /v1/runs/{id}/depart`**: add "The driver's `POST /v1/runs/{id}/start` does the same."
- A new section before **From Simple WMS**:
````markdown
## The driver app

The driver's phone uses these with a driver token (`deliveries:read`,
`deliveries:write`). A driver acts only on their own runs: another driver's
run is `404`. An API key with the same scopes acts on any run.

### GET /v1/driver/runs?date=2026-10-14
The signed-in driver's runs that day, by the phone's own date (the server's
is UTC), not cancelled; and any of theirs from earlier days that left and
were never finished. Only for a driver: anyone else is `403`.
```json
[{ "id": 7, "run_no": "R-0007", "date": "2026-10-14", "status": "planned",
   "vehicle": { "rego": "TMS041", "type": "rigid" },
   "stops": 12, "packages": 46, "delivered": 0, "failed": 0 }]
```

### GET /v1/runs/{id}/stops
The whole run as the phone keeps it for the day: the run's driver with
`deliveries:read`, or anyone with `runs:read`.
```json
{ "id": 7, "run_no": "R-0007", "date": "2026-10-14", "status": "departed",
  "vehicle": { "rego": "TMS041", "type": "rigid" }, "driver": { "code": "kim", "name": "Kim" },
  "departed_at": "2026-10-13T21:10:02.118Z", "finished_at": null,
  "stops": [{ "sequence": 1, "consignment_id": 31, "wms_ref": "0080034519",
              "customer": "Station Deli",
              "ship_to": { "name": "Station Deli", "address": "1 Main St", "suburb": "Ballan",
                           "state": "VIC", "postcode": "3342", "country": "AU",
                           "contact": "Priya", "phone": "03 5300 0000" },
              "packages": [{ "package_no": 1, "type": "carton", "weight_kg": "9.5",
                             "sscc": "093123450000000012" }],
              "outcome": null, "outcome_at": null }] }
```

### POST /v1/runs/{id}/start
`{ "message_id" }` →
`{ "message_id", "run_id", "run_no", "status": "departed", "departed_at" }`.
The driver is leaving: a planned run departs exactly as
`POST /v1/runs/{id}/depart` does (history `departed` by the driver's code, a
ship call to Simple WMS per consignment); a run that has already left is left
as it is. `409`: `run_done`, `run_cancelled`, and depart's `run_empty`,
`vehicle_unavailable`, `over_capacity`.

### PUT /v1/pod-files/{file_id}?kind=signature|photo
A signature or a photo, sent before the confirmation that names it.
`file_id` is a UUID the phone chose when the picture was made; the body is
the picture (`Content-Type: image/png` or `image/jpeg`). What counts is its
first bytes: a signature is a PNG of at most 1 MB, a photo a JPEG or PNG of
at most 5 MB (`415 unsupported_type`, `413 too_large`). → `201`:
```json
{ "id": "3f2a…", "kind": "signature", "content_type": "image/png", "size_bytes": 18886,
  "sha256": "…", "created_at": "2026-10-14T00:52:09.931Z" }
```
The same bytes again from the same sender: `200`, the same body. Other bytes,
another kind or another sender under that id: `409 file_id_taken`. Kept on
disk under `TMS_POD_DIR` (`/data/pod`, the `pod_files` volume in Compose).

### GET /v1/pod-files/{file_id}
The picture, for anyone with `consignments:read` and for whoever sent it;
anyone else gets `404`. Sent with `Cache-Control: private, no-store`.

### POST /v1/runs/{id}/stops/{consignment_id}/deliver
```json
{ "message_id": "…", "at": "2026-10-14T00:52:10.512Z",
  "packages": [{ "package_no": 1, "how": "scan" }, { "package_no": 2, "how": "tick" }],
  "signed_name": "Priya", "signature_file": "…", "photo_file": "…", "note": null,
  "position": { "latitude": "-37.562210", "longitude": "143.850300", "accuracy_m": "12.0" } }
```
A signature (`signature_file`) needs the printed name (`signed_name`) and the
other way round, and a delivery needs a signature, a photo or both (`422`).
`packages` names each of the consignment's packages once, scanned or ticked
(`422` on `packages`). A file must have been sent by the same sender and not
belong to another stop (`422` on `signature_file` or `photo_file`). `at` is
when it happened by the phone's clock, with its time zone; kept, but never
later than when it arrived. `position` is optional, decimals as strings,
rounded to six places. →
```json
{ "message_id": "…", "run_id": 7, "run_no": "R-0007", "consignment_id": 31,
  "sequence": 1, "outcome": "delivered", "at": "…", "pod_id": 12, "done": 5, "left": 7 }
```
The consignment becomes `delivered` and the stop's `outcome` `delivered`;
the history gets `delivered` with `{ run, run_id, pod_id, packages,
signed_name, signature_file, photo_file, by }`. The position is kept on the
proof of delivery only, never in the history (kept for good; positions are
not).

### POST /v1/runs/{id}/stops/{consignment_id}/fail
`{ "message_id", "at", "reason": "closed" | "refused" | "damaged" | "wrong_address", "note"?, "photo_file"?, "position"? }`
→ as for deliver, with `"outcome": "failed"`. The consignment becomes
`failed`: it comes back to the depot on the truck, and can be put on another
run at once. History `failed` with `{ run, run_id, pod_id, reason, note,
photo_file, by }`.

### POST /v1/runs/{id}/finish
`{ "message_id" }` →
`{ "message_id", "run_id", "run_no", "status": "done", "finished_at", "delivered", "failed" }`.
Back at the depot. Sent by the run's driver, or by the office (`runs:write`)
for a driver who is back but didn't finish on the phone; the desktop's
**Finish run** sends it with a `message_id` of its own. A finished run is
left as it is.

| `409` code | When |
| --- | --- |
| `run_not_started` | deliver or fail before the run has left |
| `run_done`, `run_cancelled` | the run is finished, or was cancelled |
| `stop_done` | the stop already has an outcome, from another message |
| `stops_left` | finish while a stop has no outcome |
| `message_id_taken` | someone else sent that `message_id` |
| `file_id_taken` | that file id is already another picture |

A consignment that is not on the run is `404`.

Simple TMS sends no events of its own yet (`consignment.delivered`,
`consignment.failed`, `run.completed` in the design): they come with the
first system that subscribes.
````

**Step 3: Check and commit**

Run: `(cd .. && docker compose config --quiet) && .venv/bin/pytest -q`
Expected: no output from `config`, then `240 passed`.

```bash
git add docker-compose.yml docs/api.md
git commit -m "Document the driver app's API, and keep its pictures in a volume"
```

## Part B — the driver app (`apps/driver`)

Everything in Part B runs from `apps/driver/`. Screens follow `design/screens/Driver*.html`: open each beside the app while building it. The design's CSS classes are carried over as drawn (in `@layer components`), as the desktop did; Tailwind is only there for the build and resets.

### Task 11: The app's skeleton, and the offline queue

**Files:**
- Create: `apps/driver/package.json`, `vite.config.ts`, `tsconfig.json`, `index.html`, `src/vite-env.d.ts`
- Create: `apps/driver/public/manifest.webmanifest`, `public/favicon.svg`, `scripts/make_icons.py`, `public/icon-192.png`, `public/icon-512.png`
- Create: `apps/driver/src/test/setup.ts`
- Create: `apps/driver/src/lib/files.ts`, `apps/driver/src/lib/queue.ts`
- Test: `apps/driver/src/test/files.test.ts`, `apps/driver/src/test/queue.test.ts`

**Step 1: The skeleton**

`apps/driver/package.json` (the desktop's, with `fake-indexeddb` for the picture store's test, and `dev`/`preview` on the network so a phone can reach them):
```json
{
  "name": "simple-tms-driver",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite --host",
    "build": "tsc -b && vite build",
    "preview": "vite preview --host",
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
    "fake-indexeddb": "^6.0.0",
    "jsdom": "^26.1.0",
    "tailwindcss": "^4.1.0",
    "typescript": "^5.8.0",
    "vite": "^6.3.0",
    "vitest": "^3.2.0"
  }
}
```

`apps/driver/vite.config.ts`:
```ts
/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Vite runs this under Node; the app itself never sees process.
declare const process: { env: Record<string, string | undefined> };

const api = { "/v1": { target: process.env.TMS_API_URL ?? "http://127.0.0.1:8022", changeOrigin: true } };

// Served by Caddy at /driver/ (deploy/Caddyfile). `npm run dev` and
// `npm run preview` send /v1 to the API, so a phone can be pointed at either.
export default defineConfig({
  base: "/driver/",
  plugins: [react(), tailwindcss()],
  server: { port: 5175, proxy: api },
  preview: { port: 4175, proxy: api },
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

`apps/driver/tsconfig.json` and `src/vite-env.d.ts` are copies of the desktop's (`cp ../desktop/tsconfig.json . && mkdir -p src && cp ../desktop/src/vite-env.d.ts src/`); `public/favicon.svg` too (`mkdir -p public && cp ../desktop/public/favicon.svg public/`).

`apps/driver/index.html`:
```html
<!doctype html>
<html lang="en-AU">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover" />
    <meta name="color-scheme" content="light" />
    <meta name="theme-color" content="#F3F6F4" />
    <meta name="mobile-web-app-capable" content="yes" />
    <meta name="apple-mobile-web-app-capable" content="yes" />
    <meta name="apple-mobile-web-app-status-bar-style" content="default" />
    <title>Simple TMS driver</title>
    <link rel="icon" href="/driver/favicon.svg" type="image/svg+xml" />
    <link rel="apple-touch-icon" href="/driver/icon-192.png" />
    <link rel="manifest" href="/driver/manifest.webmanifest" />
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

`apps/driver/public/manifest.webmanifest`:
```json
{
  "name": "Simple TMS driver",
  "short_name": "TMS driver",
  "description": "Your run, one stop at a time.",
  "start_url": "/driver/",
  "scope": "/driver/",
  "display": "standalone",
  "orientation": "portrait",
  "background_color": "#F3F6F4",
  "theme_color": "#F3F6F4",
  "icons": [
    { "src": "/driver/icon-192.png", "sizes": "192x192", "type": "image/png" },
    { "src": "/driver/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable" }
  ]
}
```

The two icons are drawn from the favicon's cube by a script with no dependencies, so they can be made again. `apps/driver/scripts/make_icons.py`:
```python
"""Draws public/icon-192.png and public/icon-512.png: the Simple TMS cube from
favicon.svg on the page colour, inside the middle 60 % so a maskable crop
keeps it whole. Standard library only. Run from apps/driver:
    python3 scripts/make_icons.py
"""
import struct
import zlib
from pathlib import Path

PAGE = (0xF3, 0xF6, 0xF4)
# the cube's three faces in favicon.svg's 32 x 32 box, with their colours
FACES = [
    ([(16, 3), (28, 10), (16, 17), (4, 10)], (0x5B, 0xC2, 0x8F)),
    ([(4, 10), (16, 17), (16, 30), (4, 23)], (0x1F, 0x8A, 0x5B)),
    ([(28, 10), (16, 17), (16, 30), (28, 23)], (0x14, 0x6B, 0x45)),
]
SAMPLES = 4  # per side, per pixel: smooth edges


def inside(x: float, y: float, poly: list[tuple[float, float]]) -> bool:
    hit = False
    for (x1, y1), (x2, y2) in zip(poly, poly[1:] + poly[:1]):
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            hit = not hit
    return hit


def draw(size: int) -> bytes:
    scale = size * 0.6 / 27          # the cube is 27 units tall (y 3 to 30)
    left = (size - 24 * scale) / 2 - 4 * scale
    top = (size - 27 * scale) / 2 - 3 * scale
    rows = []
    for py in range(size):
        row = bytearray([0])  # filter: none
        for px in range(size):
            totals = [0, 0, 0]
            for sy in range(SAMPLES):
                for sx in range(SAMPLES):
                    x = ((px + (sx + 0.5) / SAMPLES) - left) / scale
                    y = ((py + (sy + 0.5) / SAMPLES) - top) / scale
                    colour = next((c for poly, c in FACES if inside(x, y, poly)), PAGE)
                    for i in range(3):
                        totals[i] += colour[i]
            row += bytes(t // (SAMPLES * SAMPLES) for t in totals)
        rows.append(bytes(row))
    raw = zlib.compress(b"".join(rows), 9)

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))

    header = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", raw)
            + chunk(b"IEND", b""))


if __name__ == "__main__":
    public = Path(__file__).resolve().parent.parent / "public"
    for size in (192, 512):
        (public / f"icon-{size}.png").write_bytes(draw(size))
        print(f"public/icon-{size}.png")
```

Run: `python3 scripts/make_icons.py && npm install`
Expected: `public/icon-192.png`, `public/icon-512.png` (the green cube on the page colour, inside the middle 60 %), then `added … packages` and a `package-lock.json`.

**Step 2: Write the failing tests**

`apps/driver/src/test/setup.ts`:
```ts
import "@testing-library/jest-dom/vitest";
import "fake-indexeddb/auto";

// jsdom draws nothing: a canvas has no context, as on a phone that can't draw
HTMLCanvasElement.prototype.getContext = (() => null) as typeof HTMLCanvasElement.prototype.getContext;

afterEach(() => {
  window.localStorage.clear();
});
```

`apps/driver/src/test/files.test.ts`:
```ts
import { indexedDbFiles, memoryFiles, readBytes } from "../lib/files";

describe("pictures kept on the phone", () => {
  it("keeps a picture in IndexedDB until it is deleted", async () => {
    const blob = new Blob([new Uint8Array([0x89, 0x50, 0x4e, 0x47])], { type: "image/png" });
    await indexedDbFiles.put("a", blob);
    const back = await indexedDbFiles.get("a");
    expect(back?.type).toBe("image/png");
    expect(new Uint8Array(await readBytes(back!))).toEqual(new Uint8Array([0x89, 0x50, 0x4e, 0x47]));
    await indexedDbFiles.delete("a");
    expect(await indexedDbFiles.get("a")).toBeUndefined();
  });

  it("has a stand-in in memory", async () => {
    const files = memoryFiles();
    await files.put("x", new Blob(["1"]));
    expect(files.size()).toBe(1);
    await files.delete("x");
    expect(await files.get("x")).toBeUndefined();
  });
});
```

`apps/driver/src/test/queue.test.ts`:
```ts
import { memoryFiles } from "../lib/files";
import { RetryQueue, type Request } from "../lib/queue";

function json(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const PNG = () => new Blob([new Uint8Array([0x89, 0x50, 0x4e, 0x47])], { type: "image/png" });

describe("the offline queue", () => {
  let sent: Request[];
  let answers: (Response | Error)[];
  let online: boolean;
  let store: Map<string, string>;
  let files: ReturnType<typeof memoryFiles>;

  function queue() {
    return new RetryQueue({
      send: async (req) => {
        sent.push(req);
        const a = answers.shift() ?? json(200, { ok: true });
        if (a instanceof Error) throw a;
        return a;
      },
      files,
      storage: { get: (k) => store.get(k), set: (k, v) => store.set(k, v) },
      online: () => online,
    });
  }
  const body = (r: Request) => JSON.parse(r.body as string);
  const item = (label: string, consignment_id = 1) => ({
    path: `/v1/runs/7/stops/${consignment_id}/fail`, body: { reason: "closed" }, label, run_id: 7,
    consignment_id, outcome: "failed" as const,
  });

  beforeEach(() => {
    sent = [];
    answers = [];
    online = true;
    store = new Map();
    files = memoryFiles();
  });

  it("gives each confirmation its message_id when it is made, and keeps it through a reload", async () => {
    online = false;
    const q = queue();
    const a = await q.submit(item("Stop 1 · couldn't deliver"));
    expect(a.message_id).toMatch(/^[0-9a-f-]{36}$/);
    expect(a.status).toBe("queued");
    const again = queue();
    expect(again.pending().map((i) => i.message_id)).toEqual([a.message_id]);
    expect(sent).toHaveLength(0);
  });

  it("sends the pictures first, then the confirmation that names them", async () => {
    const q = queue();
    const a = await q.submit({
      ...item("Stop 1 · delivered"), path: "/v1/runs/7/stops/1/deliver", outcome: "delivered",
      body: { signed_name: "Priya" },
      pictures: [{ kind: "signature", field: "signature_file", blob: PNG() }],
    });
    await q.drain();
    const fileId = a.files[0].id;
    expect(sent.map((r) => `${r.method} ${r.path}`)).toEqual([
      `PUT /v1/pod-files/${fileId}?kind=signature`, "POST /v1/runs/7/stops/1/deliver"]);
    expect(sent[0].contentType).toBe("image/png");
    expect(body(sent[1])).toEqual({ message_id: a.message_id, signed_name: "Priya", signature_file: fileId });
    expect(q.all()[0].status).toBe("sent");
    // sent, so the picture is off the phone
    expect(files.size()).toBe(0);
  });

  it("waits without signal, then sends everything in order, once each", async () => {
    online = false;
    const q = queue();
    await q.submit(item("Stop 1", 1));
    await q.submit(item("Stop 2", 2));
    expect(sent).toHaveLength(0);
    online = true;
    expect(await q.drain()).toBe(2);
    expect(sent.map((r) => r.path)).toEqual(["/v1/runs/7/stops/1/fail", "/v1/runs/7/stops/2/fail"]);
    expect(await q.drain()).toBe(0);
    expect(sent).toHaveLength(2);
  });

  it("keeps the order when the first can't go yet", async () => {
    online = false;
    const q = queue();
    await q.submit(item("Stop 1", 1));
    await q.submit(item("Stop 2", 2));
    online = true;
    answers.push(new TypeError("Failed to fetch"));
    expect(await q.drain()).toBe(0);
    expect(sent.map((r) => r.path)).toEqual(["/v1/runs/7/stops/1/fail"]);
    expect(q.pending()).toHaveLength(2);
    expect(q.pending()[0].error).toBe("Failed to fetch");
  });

  it("sends a confirmation whose reply was lost again with the same message_id", async () => {
    const q = queue();
    answers.push(new TypeError("Failed to fetch"));   // it got there; the answer didn't come back
    const a = await q.submit(item("Stop 1"));
    await q.drain();
    await q.drain();
    expect(sent).toHaveLength(2);
    expect(body(sent[0]).message_id).toBe(a.message_id);
    expect(body(sent[1]).message_id).toBe(a.message_id);
    expect(q.all()[0].status).toBe("sent");
  });

  it("doesn't send a picture again once it has gone", async () => {
    const q = queue();
    answers.push(json(201, {}), json(503, { detail: "starting up" }));
    await q.submit({ ...item("Stop 1"), pictures: [{ kind: "photo", field: "photo_file", blob: PNG() }] });
    await q.drain();
    expect(q.pending()[0].error).toBe("starting up");
    await q.drain();
    expect(sent.map((r) => r.method)).toEqual(["PUT", "POST", "POST"]);
  });

  it("marks a refusal failed and goes on with the rest", async () => {
    online = false;
    const q = queue();
    await q.submit(item("Stop 1", 1));
    await q.submit(item("Stop 2", 2));
    online = true;
    answers.push(json(409, { code: "stop_done", detail: "stop 1 on R-0007 is already recorded as delivered" }));
    expect(await q.drain()).toBe(1);
    expect(q.failed().map((i) => i.error)).toEqual(["stop 1 on R-0007 is already recorded as delivered"]);
    await q.drain();
    expect(sent).toHaveLength(2);
  });

  it("keeps one waiting when the session has run out, to go after signing in again", async () => {
    const q = queue();
    answers.push(json(401, { detail: "driver session expired or invalid" }));
    await q.submit(item("Stop 1"));
    await q.drain();
    expect(q.pending()).toHaveLength(1);
    await q.drain();
    expect(q.all()[0].status).toBe("sent");
  });

  it("doesn't send a confirmation whose picture was refused", async () => {
    const q = queue();
    answers.push(json(413, { detail: "a photo may be 5 MB at most", code: "too_large" }));
    await q.submit({ ...item("Stop 1"), pictures: [{ kind: "photo", field: "photo_file", blob: PNG() }] });
    await q.drain();
    expect(sent.map((r) => r.method)).toEqual(["PUT"]);
    expect(q.failed()[0].error).toBe("The photo wasn't taken: a photo may be 5 MB at most");
  });

  it("says so when a picture has gone from the phone", async () => {
    online = false;
    const q = queue();
    const a = await q.submit({ ...item("Stop 1"), pictures: [{ kind: "signature", field: "signature_file", blob: PNG() }] });
    await files.delete(a.files[0].id);
    online = true;
    await q.drain();
    expect(sent).toHaveLength(0);
    expect(q.failed()[0].error).toMatch(/The signature is no longer on this phone/);
  });

  it("drains one at a time however often it is asked", async () => {
    online = false;
    const q = queue();
    await q.submit(item("Stop 1", 1));
    await q.submit(item("Stop 2", 2));
    online = true;
    await Promise.all([q.drain(), q.drain(), q.drain()]);
    expect(sent).toHaveLength(2);
  });
});
```

**Step 3: Run them to see them fail**

Run: `npm test`
Expected: both files fail: `Failed to resolve import "../lib/files"`.

**Step 4: The picture store**

`apps/driver/src/lib/files.ts`. Bytes, not `Blob`s, go into IndexedDB (older Safari can't keep a `Blob` there), read with `FileReader`, which every browser and jsdom have (jsdom's `Blob` has no `arrayBuffer()`):
```ts
/** Signatures and photos waiting to be sent, kept on the phone in IndexedDB:
 * localStorage holds a few megabytes of text, and a run's photos are more. */

export interface FileStore {
  put(id: string, blob: Blob): Promise<void>;
  get(id: string): Promise<Blob | undefined>;
  delete(id: string): Promise<void>;
}

const DB = "tms-driver";
const STORE = "files";

function open(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB, 1);
    req.onupgradeneeded = () => { req.result.createObjectStore(STORE); };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

function run<T>(mode: IDBTransactionMode, act: (store: IDBObjectStore) => IDBRequest): Promise<T> {
  return open().then((db) => new Promise<T>((resolve, reject) => {
    const tx = db.transaction(STORE, mode);
    const req = act(tx.objectStore(STORE));
    // a write is kept only once its transaction is: wait for that, not the request
    tx.oncomplete = () => { db.close(); resolve(req.result as T); };
    tx.onerror = () => { db.close(); reject(tx.error); };
    tx.onabort = () => { db.close(); reject(tx.error); };
  }));
}

/** Stored as bytes with their type: some browsers can't keep a Blob in IndexedDB. */
interface Stored { type: string; bytes: ArrayBuffer }

/** A Blob's bytes, by FileReader, which every browser (and jsdom) has. */
export function readBytes(blob: Blob): Promise<ArrayBuffer> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as ArrayBuffer);
    reader.onerror = () => reject(reader.error);
    reader.readAsArrayBuffer(blob);
  });
}

export const indexedDbFiles: FileStore = {
  async put(id, blob) {
    const stored: Stored = { type: blob.type, bytes: await readBytes(blob) };
    await run("readwrite", (s) => s.put(stored, id));
  },
  async get(id) {
    const stored = await run<Stored | undefined>("readonly", (s) => s.get(id));
    return stored ? new Blob([stored.bytes], { type: stored.type }) : undefined;
  },
  async delete(id) {
    await run("readwrite", (s) => s.delete(id));
  },
};

/** For tests, and for a browser with no IndexedDB at all. */
export function memoryFiles(): FileStore & { size(): number } {
  const map = new Map<string, Blob>();
  return {
    put: async (id, blob) => { map.set(id, blob); },
    get: async (id) => map.get(id),
    delete: async (id) => { map.delete(id); },
    size: () => map.size,
  };
}
```

**Step 5: The queue**

`apps/driver/src/lib/queue.ts`. The scanner's queue, with three changes: an item carries its pictures and sends them first; `drain()` looks for the next waiting item each time round instead of walking a list made at the start, so one added mid-drain is sent by the drain already running (and in order); and the drain's promise is cleared in a `finally` that runs after it is stored (an `async` body that finishes without waiting would otherwise clear it before it is stored, and every later `drain()` would get the old, settled promise: one of the tests catches exactly that).
```ts
/** Confirmations from the road, sent when there is signal. As the Simple WMS
 * scanner's queue: each gets its message_id the moment it is made, is kept on
 * the phone, and is sent oldest first, in order; a repeat gets the same reply
 * and records nothing twice. A refusal (a 4xx that a retry can't mend) is
 * marked failed and not sent again.
 *
 * A confirmation may carry pictures (a signature, a photo). They go first,
 * each to PUT /v1/pod-files/{id} under an id chosen when it was taken, so
 * sending one twice is harmless too; then the confirmation that names them.
 * The pictures wait in a FileStore; the rest in localStorage. */
import type { FileStore } from "./files";

export type PictureKind = "signature" | "photo";

export interface QueuedFile {
  id: string;
  kind: PictureKind;
  sent: boolean;
}

export interface QueueItem {
  id: string;
  message_id: string;
  path: string;
  body: Record<string, unknown>;
  files: QueuedFile[];
  label: string;
  run_id: number;
  /** the stop it records, with what it says happened there */
  consignment_id: number | null;
  outcome: "delivered" | "failed" | "finished";
  created_at: string;
  status: "queued" | "sent" | "failed";
  attempts: number;
  sent_at?: string;
  reply?: unknown;
  error?: string;
}

export interface NewItem {
  path: string;
  body: Record<string, unknown>;
  label: string;
  run_id: number;
  consignment_id: number | null;
  outcome: QueueItem["outcome"];
  /** each picture, and the body field its id goes in (signature_file, photo_file) */
  pictures?: { kind: PictureKind; field: string; blob: Blob }[];
}

export interface Request {
  method: "POST" | "PUT";
  path: string;
  body: string | Blob;
  contentType: string;
}
type Sender = (req: Request) => Promise<Response>;
interface Storage {
  get(key: string): string | undefined;
  set(key: string, value: string): unknown;
}

const KEY = "tms.driver.queue";
const MAX = 300;

function browserStorage(): Storage {
  return {
    get: (k) => { try { return window.localStorage.getItem(k) ?? undefined; } catch { return undefined; } },
    set: (k, v) => { try { window.localStorage.setItem(k, v); } catch { /* full or private: kept in memory */ } },
  };
}

/** A refusal: the API understood and said no, so sending again can't help.
 * A 401 is not one (sign in again and it goes), nor 408 or 429. */
function refused(status: number): boolean {
  return status >= 400 && status < 500 && status !== 401 && status !== 408 && status !== 429;
}

export function describe(body: unknown, status: number): string {
  const errors = (body as { errors?: { field: string; message: string }[] })?.errors;
  if (errors?.length) return errors.map((e) => e.message).join("; ");
  const detail = (body as { detail?: string })?.detail;
  return typeof detail === "string" ? detail : `Simple TMS answered ${status}`;
}

async function readJson(res: Response): Promise<unknown> {
  const text = await res.text();
  try { return text ? JSON.parse(text) : null; } catch { return null; }
}

export class RetryQueue {
  private items: QueueItem[] = [];
  private send: Sender;
  private files: FileStore;
  private storage: Storage;
  private online: () => boolean;
  private draining: Promise<number> | null = null;
  private listeners = new Set<() => void>();

  constructor(opts: { send: Sender; files: FileStore; storage?: Storage; online?: () => boolean }) {
    this.send = opts.send;
    this.files = opts.files;
    this.storage = opts.storage ?? browserStorage();
    this.online = opts.online ?? (() => typeof navigator === "undefined" || navigator.onLine);
    try {
      this.items = JSON.parse(this.storage.get(KEY) ?? "[]") as QueueItem[];
    } catch {
      this.items = [];
    }
  }

  private save() {
    // forget the oldest that went, never one still waiting
    while (this.items.length > MAX) {
      const i = this.items.findIndex((x) => x.status !== "queued");
      if (i < 0) break;
      this.items.splice(i, 1);
    }
    this.storage.set(KEY, JSON.stringify(this.items));
    this.listeners.forEach((fn) => fn());
  }

  subscribe(fn: () => void) {
    this.listeners.add(fn);
    return () => { this.listeners.delete(fn); };
  }

  /** Newest first. */
  all(): QueueItem[] { return this.items.slice().reverse(); }
  pending(): QueueItem[] { return this.items.filter((i) => i.status === "queued"); }
  failed(): QueueItem[] { return this.items.filter((i) => i.status === "failed"); }
  forRun(runId: number): QueueItem[] { return this.all().filter((i) => i.run_id === runId); }

  /** Keep it (pictures first), then send what is waiting if there is signal. */
  async submit(input: NewItem): Promise<QueueItem> {
    const files: QueuedFile[] = [];
    const body = { ...input.body };
    for (const p of input.pictures ?? []) {
      const id = crypto.randomUUID();
      // on the phone before the confirmation that names it, so a reload never finds one without the other
      await this.files.put(id, p.blob);
      files.push({ id, kind: p.kind, sent: false });
      body[p.field] = id;
    }
    const item: QueueItem = {
      id: crypto.randomUUID(), message_id: crypto.randomUUID(), path: input.path, body, files,
      label: input.label, run_id: input.run_id, consignment_id: input.consignment_id,
      outcome: input.outcome, created_at: new Date().toISOString(), status: "queued", attempts: 0,
    };
    this.items.push(item);
    this.save();
    void this.drain();
    return item;
  }

  private fail(item: QueueItem, error: string) {
    item.status = "failed";
    item.error = error;
  }

  /** One go at an item: its pictures, then itself. It is sent, failed, or still queued after. */
  private async attempt(item: QueueItem): Promise<void> {
    item.attempts += 1;
    try {
      for (const f of item.files.filter((x) => !x.sent)) {
        const blob = await this.files.get(f.id);
        if (!blob) {
          this.fail(item, `The ${f.kind} is no longer on this phone, so this can't be sent. Do the stop again.`);
          return;
        }
        const res = await this.send({ method: "PUT", path: `/v1/pod-files/${f.id}?kind=${f.kind}`,
                                      body: blob, contentType: blob.type || "application/octet-stream" });
        if (res.ok) {
          f.sent = true;
          this.save();
          continue;
        }
        const why = describe(await readJson(res), res.status);
        if (refused(res.status)) this.fail(item, `The ${f.kind} wasn't taken: ${why}`);
        else item.error = why;
        return;
      }
      const res = await this.send({ method: "POST", path: item.path, contentType: "application/json",
                                    body: JSON.stringify({ message_id: item.message_id, ...item.body }) });
      const parsed = await readJson(res);
      if (res.ok) {
        item.status = "sent";
        item.sent_at = new Date().toISOString();
        item.reply = parsed;
        item.error = undefined;
        for (const f of item.files) await this.files.delete(f.id).catch(() => undefined);
      } else if (refused(res.status)) {
        this.fail(item, describe(parsed, res.status));
      } else {
        item.error = describe(parsed, res.status);
      }
    } catch (e) {
      item.error = e instanceof Error && e.message ? e.message : "no signal";
    } finally {
      this.save();
    }
  }

  /** Send everything waiting, oldest first, stopping at the first that has to
   * wait, so they arrive in the order they were made. One drain at a time;
   * what is added meanwhile is picked up by the one running. */
  drain(): Promise<number> {
    // cleared once it has settled, never before it is stored
    this.draining ??= this.drainNow().finally(() => { this.draining = null; });
    return this.draining;
  }

  private async drainNow(): Promise<number> {
    let sent = 0;
    for (;;) {
      const next = this.items.find((i) => i.status === "queued");
      if (!next || !this.online()) break;
      await this.attempt(next);
      if (next.status === "sent") sent += 1;
      else if (next.status === "queued") break;
    }
    return sent;
  }
}
```

**Step 6: Run the tests**

Run: `npm test && npm run typecheck`
Expected: `13 passed`; the type check prints nothing.

**Step 7: Commit**

```bash
git add apps/driver
git commit -m "Start the driver app: a queue that keeps confirmations and their pictures"
```

(`node_modules/` and `dist/` are already in `.gitignore`.)

### Task 12: The API client

**Files:**
- Create: `apps/driver/src/api/types.ts`, `apps/driver/src/api/client.ts`
- Test: `apps/driver/src/test/client.test.ts`

The driver token lasts the 12-hour shift with no refresh, so the client is the desktop's without the refresh: a `401` (other than a wrong PIN) means the session is over, and the app signs the driver out. What is waiting in the queue stays, and goes once they sign in again.

**Step 1: Write the failing test**

`apps/driver/src/test/client.test.ts`:
```ts
import { ApiError, DriverApi, Unreachable } from "../api/client";

function json(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

describe("the driver's API client", () => {
  it("sends the driver token and reads JSON", async () => {
    const seen: RequestInit[] = [];
    const api = new DriverApi(async (_url, init) => { seen.push(init); return json(200, [{ id: 7 }]); });
    api.token = "tms_d.abc";
    expect(await api.get("/v1/driver/runs?date=2026-10-14")).toEqual([{ id: 7 }]);
    expect((seen[0].headers as Record<string, string>).Authorization).toBe("Bearer tms_d.abc");
  });

  it("gives the API's reason and code when it says no", async () => {
    const api = new DriverApi(async () => json(409, { detail: "R-0007 is finished", code: "run_done" }));
    const e = (await api.post("/v1/runs/7/start", { message_id: "m" }).catch((x) => x)) as ApiError;
    expect(e).toBeInstanceOf(ApiError);
    expect([e.status, e.code, e.message]).toEqual([409, "run_done", "R-0007 is finished"]);
  });

  it("says no signal when there is none, or the answer is not the API's", async () => {
    const offline = new DriverApi(async () => { throw new TypeError("Failed to fetch"); });
    expect(await offline.get("/v1/driver/runs").catch((x) => x)).toBeInstanceOf(Unreachable);
    const proxy = new DriverApi(async () => new Response("<html>Bad gateway</html>", { status: 502 }));
    expect(await proxy.get("/v1/driver/runs").catch((x) => x)).toBeInstanceOf(Unreachable);
  });

  it("signs the driver out when the API no longer takes the token", async () => {
    const api = new DriverApi(async () => json(401, { detail: "driver session expired or invalid" }));
    const out = vi.fn();
    api.onSignedOut = out;
    await api.get("/v1/driver/runs").catch(() => undefined);
    await api.send({ method: "POST", path: "/v1/runs/7/finish", body: "{}", contentType: "application/json" });
    expect(out).toHaveBeenCalledTimes(2);
    // a wrong PIN is not a session ending
    await api.post("/v1/auth/driver-login", { code: "kim", pin: "0000" }).catch(() => undefined);
    expect(out).toHaveBeenCalledTimes(2);
  });
});
```

**Step 2: Run it to see it fail**

Run: `npm test -- client`
Expected: `Failed to resolve import "../api/client"`.

**Step 3: Implement**

`apps/driver/src/api/types.ts`:
```ts
/** Response shapes, as docs/api.md gives them. Decimals are strings. */

export interface Me {
  kind: "driver";
  id: number;
  name: string;
  display_name: string;
  role: string;
  scopes: string[];
}

export interface DriverSessionOut {
  token: string;
  expires_in: number;
  driver: Me;
}

export interface DriverRun {
  id: number;
  run_no: string;
  date: string;
  status: "planned" | "departed" | "done" | "cancelled";
  vehicle: { rego: string; type: string };
  stops: number;
  packages: number;
  delivered: number;
  failed: number;
}

export interface ShipTo {
  name?: string | null;
  address?: string | null;
  suburb?: string | null;
  state?: string | null;
  postcode?: string | null;
  country?: string | null;
  contact?: string | null;
  phone?: string | null;
}

export interface PackageLine {
  package_no: number;
  type: string;
  weight_kg: string | null;
  sscc: string | null;
}

export type Outcome = "delivered" | "failed";

export interface StopDetail {
  sequence: number;
  consignment_id: number;
  wms_ref: string;
  customer: string;
  ship_to: ShipTo;
  packages: PackageLine[];
  outcome: Outcome | null;
  outcome_at: string | null;
}

export interface RunStops {
  id: number;
  run_no: string;
  date: string;
  status: DriverRun["status"];
  vehicle: { rego: string; type: string };
  driver: { code: string; name: string };
  departed_at: string | null;
  finished_at: string | null;
  stops: StopDetail[];
}

export interface StartOut {
  message_id: string;
  run_id: number;
  run_no: string;
  status: DriverRun["status"];
  departed_at: string | null;
}
```

`apps/driver/src/api/client.ts`:
```ts
/** The driver app is an ordinary API client with a driver session token
 * (POST /v1/auth/driver-login, 12 hours, no refresh). Nothing here a partner
 * could not do with the same API. */
import type { Request } from "../lib/queue";

export interface FieldError {
  field: string;
  message: string;
}

export class ApiError extends Error {
  status: number;
  errors: FieldError[];
  /** The API's reason when it gives one: wrong_pin, locked, over_capacity, ... */
  code: string | null;
  body: unknown;
  constructor(status: number, body: unknown) {
    const errors = ((body as { errors?: FieldError[] })?.errors ?? []) as FieldError[];
    const detail = (body as { detail?: string })?.detail;
    super(errors.length > 0 ? errors.map((e) => e.message).join("; ")
      : typeof detail === "string" ? detail : `Simple TMS answered ${status}`);
    this.status = status;
    this.errors = errors;
    this.code = typeof (body as { code?: unknown })?.code === "string" ? (body as { code: string }).code : null;
    this.body = body;
  }
}

/** No signal, or no proper answer. */
export class Unreachable extends Error {
  constructor() {
    super("No signal: Simple TMS can't be reached right now.");
  }
}

type Fetcher = (url: string, init: RequestInit) => Promise<Response>;

export class DriverApi {
  token: string | null = null;
  /** The API no longer takes the token: the shift ran out, or the PIN changed. */
  onSignedOut: (() => void) | null = null;
  private fetcher: Fetcher;

  constructor(fetcher?: Fetcher) {
    this.fetcher = fetcher ?? ((url, init) => fetch(url, init));
  }

  private headers(extra: Record<string, string> = {}): Record<string, string> {
    return { Accept: "application/json", ...extra, ...(this.token ? { Authorization: `Bearer ${this.token}` } : {}) };
  }

  async request<T>(method: string, path: string, body?: unknown): Promise<T> {
    let res: Response;
    try {
      res = await this.fetcher(path, {
        method,
        headers: this.headers(body === undefined ? {} : { "Content-Type": "application/json" }),
        body: body === undefined ? undefined : JSON.stringify(body),
      });
    } catch {
      throw new Unreachable();
    }
    if (res.status === 401 && path !== "/v1/auth/driver-login") this.onSignedOut?.();
    const text = await res.text();
    let parsed: unknown = null;
    try {
      parsed = text ? JSON.parse(text) : null;
    } catch {
      // a proxy's error page, say: not the API's own answer
      if (res.ok || res.status >= 500) throw new Unreachable();
      parsed = { detail: `Simple TMS didn't answer properly (${res.status}).` };
    }
    if (!res.ok) throw new ApiError(res.status, parsed);
    return parsed as T;
  }

  get<T>(path: string) { return this.request<T>("GET", path); }
  post<T>(path: string, body: unknown = {}) { return this.request<T>("POST", path, body); }

  /** For the offline queue: one request as it stands, with today's token. */
  async send(req: Request): Promise<Response> {
    const res = await this.fetcher(req.path, {
      method: req.method, headers: this.headers({ "Content-Type": req.contentType }), body: req.body,
    });
    if (res.status === 401) this.onSignedOut?.();
    return res;
  }
}

export const api = new DriverApi();
```

**Step 4: Run the tests**

Run: `npm test && npm run typecheck`
Expected: `17 passed`.

**Step 5: Commit**

```bash
git add apps/driver/src/api apps/driver/src/test/client.test.ts
git commit -m "Talk to the API from the driver app with the driver's token"
```

### Task 13: The run on the phone: keeping it, reading labels, maps, position, pictures

**Files:**
- Create: `apps/driver/src/lib/time.ts`, `run.ts`, `maps.ts`, `scan.ts`, `useScanWedge.ts`, `where.ts`, `picture.ts`
- Create: `apps/driver/src/test/samples.ts`
- Test: `apps/driver/src/test/run.test.ts`, `apps/driver/src/test/scan.test.ts`

**Step 1: Write the failing tests**

`apps/driver/src/test/samples.ts`, a run of two stops as the API gives it:
```ts
/** A run of two stops, as GET /v1/runs/{id}/stops gives it. */
import type { RunStops, StopDetail } from "../api/types";

export const stop = (n: number, customer: string, over: Partial<StopDetail> = {}): StopDetail => ({
  sequence: n, consignment_id: 30 + n, wms_ref: `00800345${n}`, customer,
  ship_to: { name: customer, address: "12 Sturt St", suburb: "Ballarat", state: "VIC", postcode: "3350",
             country: "AU", contact: "Priya", phone: "03 5300 0000" },
  packages: [
    { package_no: 1, type: "carton", weight_kg: "9.5", sscc: `093123450000000${n}01` },
    { package_no: 2, type: "carton", weight_kg: null, sscc: null },
  ],
  outcome: null, outcome_at: null, ...over,
});

export const RUN: RunStops = {
  id: 7, run_no: "R-0007", date: "2026-10-14", status: "departed",
  vehicle: { rego: "TMS041", type: "rigid" }, driver: { code: "kim", name: "Kim" },
  departed_at: "2026-10-14T21:10:00Z", finished_at: null,
  stops: [stop(1, "Hillside Bakery"), stop(2, "Two Rivers Vet")],
};
```

`apps/driver/src/test/run.test.ts`:
```ts
import type { QueueItem } from "../lib/queue";
import { finished, loadRun, nextStop, outcomes, saveRun } from "../lib/run";
import { RUN } from "./samples";

const item = (over: Partial<QueueItem>): QueueItem => ({
  id: "i", message_id: "m", path: "", body: {}, files: [], label: "", run_id: 7, consignment_id: 31,
  outcome: "delivered", created_at: "", status: "queued", attempts: 0, ...over,
});

describe("the run on the phone", () => {
  it("is kept whole on the phone", () => {
    saveRun(RUN);
    expect(loadRun(7)).toEqual(RUN);
    expect(loadRun(8)).toBeNull();
  });

  it("counts a stop done the moment the phone has it, sent or not", () => {
    const done = outcomes(RUN, [item({ status: "queued" })]);
    expect([...done]).toEqual([[31, "delivered"]]);
    expect(nextStop(RUN, done)?.consignment_id).toBe(32);
  });

  it("doesn't count a confirmation the office refused", () => {
    expect(outcomes(RUN, [item({ status: "failed" })]).size).toBe(0);
  });

  it("takes the office's record over the phone's", () => {
    const run = { ...RUN, stops: [{ ...RUN.stops[0], outcome: "failed" as const }, RUN.stops[1]] };
    expect(outcomes(run, [item({})]).get(31)).toBe("failed");
  });

  it("is finished when the office says so, or the phone has sent it", () => {
    expect(finished(RUN, [])).toBe(false);
    expect(finished(RUN, [item({ outcome: "finished", consignment_id: null })])).toBe(true);
    expect(finished(RUN, [item({ outcome: "finished", consignment_id: null, status: "failed" })])).toBe(false);
    expect(finished({ ...RUN, status: "done" }, [])).toBe(true);
  });
});
```

`apps/driver/src/test/scan.test.ts`:
```ts
import { matchScan, readLabel } from "../lib/scan";
import { mapsLink } from "../lib/maps";
import { RUN } from "./samples";

describe("reading a carton label", () => {
  it("reads an SSCC however the scanner sends it", () => {
    expect(readLabel("093123450000000101")).toEqual({ sscc: "093123450000000101" });
    expect(readLabel("00093123450000000101")).toEqual({ sscc: "093123450000000101" });
    expect(readLabel("(00) 093123450000000101")).toEqual({ sscc: "093123450000000101" });
    expect(readLabel("]C100093123450000000101")).toEqual({ sscc: "093123450000000101" });
  });

  it("reads a package number, alone or after its delivery", () => {
    expect(readLabel("2")).toEqual({ packageNo: 2 });
    expect(readLabel("0080034519-2")).toEqual({ ref: "0080034519", packageNo: 2 });
    expect(readLabel("TMS-RUN-1/3")).toEqual({ ref: "TMS-RUN-1", packageNo: 3 });
  });

  it("finds the carton on this stop, on another, or nowhere", () => {
    const [here, there] = RUN.stops;
    expect(matchScan("093123450000000101", RUN.stops, here)).toEqual({ kind: "here", package_no: 1 });
    expect(matchScan("2", RUN.stops, here)).toEqual({ kind: "here", package_no: 2 });
    expect(matchScan("008003452-2", RUN.stops, here)).toEqual({ kind: "other", stop: there, package_no: 2 });
    expect(matchScan("093123450000000999", RUN.stops, here)).toEqual({ kind: "unknown" });
    expect(matchScan("7", RUN.stops, here)).toEqual({ kind: "unknown" });
  });
});

describe("Open in Maps", () => {
  const to = RUN.stops[0].ship_to;
  it("opens the phone's own maps app", () => {
    expect(mapsLink(to, "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X)"))
      .toBe("https://maps.apple.com/?q=12%20Sturt%20St%2C%20Ballarat%20VIC%203350%2C%20Australia");
    expect(mapsLink(to, "Mozilla/5.0 (Linux; Android 15)")).toBe("geo:0,0?q=12%20Sturt%20St%2C%20Ballarat%20VIC%203350%2C%20Australia");
  });
});
```

**Step 2: Run them to see them fail**

Run: `npm test -- run scan`
Expected: `Failed to resolve import "../lib/run"` and `"../lib/scan"`.

**Step 3: Implement**

`apps/driver/src/lib/time.ts`:
```ts
/** Dates and times as the phone has them. */

/** YYYY-MM-DD by the phone's own calendar: the day the driver means by "today". */
export function isoDay(d: Date): string {
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

/** "Tue 13 Oct", without the comma en-AU puts after the weekday. */
export function dayLabel(d: Date): string {
  const parts = new Intl.DateTimeFormat("en-AU", { weekday: "short", day: "numeric", month: "short" }).formatToParts(d);
  const get = (type: string) => parts.find((p) => p.type === type)?.value ?? "";
  return `${get("weekday")} ${get("day")} ${get("month")}`;
}

/** "10:41 am" */
export function clock(d: Date): string {
  return new Intl.DateTimeFormat("en-AU", { hour: "numeric", minute: "2-digit" }).format(d);
}
```

`apps/driver/src/lib/run.ts`:
```ts
/** The run on the phone. It is loaded whole when the driver starts, kept in
 * localStorage so a black spot or a reload changes nothing, and read
 * together with the queue: a stop confirmed on the phone is done here at
 * once, whether or not it has reached the office yet. */
import type { Outcome, RunStops, StopDetail } from "../api/types";
import type { QueueItem } from "./queue";

const RUN = (id: number) => `tms.driver.run.${id}`;
const TICKS = (run: number, consignment: number) => `tms.driver.ticks.${run}.${consignment}`;

function read<T>(key: string): T | null {
  try {
    const raw = window.localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : null;
  } catch {
    return null;
  }
}

function write(key: string, value: unknown) {
  try { window.localStorage.setItem(key, JSON.stringify(value)); } catch { /* full: kept for this visit only */ }
}

export function saveRun(run: RunStops) { write(RUN(run.id), run); }
export function loadRun(id: number): RunStops | null { return read<RunStops>(RUN(id)); }

export type How = "scan" | "tick";
export type Ticks = Record<number, How>;

export function loadTicks(runId: number, consignmentId: number): Ticks {
  return read<Ticks>(TICKS(runId, consignmentId)) ?? {};
}
export function saveTicks(runId: number, consignmentId: number, ticks: Ticks) {
  write(TICKS(runId, consignmentId), ticks);
}

/** What happened at each stop: the office's record, and what is on its way
 * from this phone (a confirmation that was refused doesn't count). */
export function outcomes(run: RunStops, items: QueueItem[]): Map<number, Outcome> {
  const out = new Map<number, Outcome>();
  for (const s of run.stops) if (s.outcome) out.set(s.consignment_id, s.outcome);
  for (const i of items) {
    if (i.run_id !== run.id || i.status === "failed" || i.consignment_id === null) continue;
    if (i.outcome === "delivered" || i.outcome === "failed") {
      if (!out.has(i.consignment_id)) out.set(i.consignment_id, i.outcome);
    }
  }
  return out;
}

export function nextStop(run: RunStops, done: Map<number, Outcome>): StopDetail | null {
  return run.stops.find((s) => !done.has(s.consignment_id)) ?? null;
}

/** Finished on the office's record, or the phone has sent (or is sending) it. */
export function finished(run: RunStops, items: QueueItem[]): boolean {
  return run.status === "done"
    || items.some((i) => i.run_id === run.id && i.outcome === "finished" && i.status !== "failed");
}

export function stopOf(run: RunStops, consignmentId: number): StopDetail | null {
  return run.stops.find((s) => s.consignment_id === consignmentId) ?? null;
}

export function plural(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`;
}
```

`apps/driver/src/lib/maps.ts`:
```ts
/** "Open in Maps": a link the phone's own maps app opens, built from the
 * ship-to address. No maps service of our own, and nothing to pay for. */
import type { ShipTo } from "../api/types";

/** "12 Sturt St, Ballarat VIC 3350" */
export function addressLine(t: ShipTo): string {
  const place = [t.suburb, t.state, t.postcode].filter(Boolean).join(" ");
  return [t.address, place].filter(Boolean).join(", ");
}

/** Apple Maps on an iPhone or iPad, the Android maps chooser (geo:) on
 * Android, and Google Maps in a browser anywhere else. */
export function mapsLink(t: ShipTo, userAgent = typeof navigator === "undefined" ? "" : navigator.userAgent): string {
  const where = [addressLine(t), t.country === "AU" ? "Australia" : t.country].filter(Boolean).join(", ")
    || t.name || "";
  const q = encodeURIComponent(where);
  if (/iPhone|iPad|iPod/.test(userAgent)) return `https://maps.apple.com/?q=${q}`;
  if (/Android/.test(userAgent)) return `geo:0,0?q=${q}`;
  return `https://www.google.com/maps/search/?api=1&query=${q}`;
}
```

`apps/driver/src/lib/scan.ts`:
```ts
/** Reading a carton label: its SSCC (as GS1-128 gives it, with or without the
 * (00) in front), the package number on its own, or "<delivery>-<n>". The
 * whole run is on the phone, so a carton for another stop is caught here. */
import type { StopDetail } from "../api/types";

export type ScanMatch =
  | { kind: "here"; package_no: number }
  | { kind: "other"; stop: StopDetail; package_no: number }
  | { kind: "unknown" };

interface Label { sscc?: string; ref?: string; packageNo?: number }

export function readLabel(raw: string): Label {
  // a scanner's symbology prefix (]C1), GS1's group separator, brackets and spaces
  const code = raw.trim().replace(/^\][A-Za-z]\d/, "").replace(/[\u001d()\s]/g, "");
  if (/^\d+$/.test(code)) {
    if (code.length === 20 && code.startsWith("00")) return { sscc: code.slice(2) };
    if (code.length === 18) return { sscc: code };
    if (code.length <= 4) return { packageNo: Number(code) };
  }
  const m = /^(.+?)[-/](\d{1,4})$/.exec(raw.trim());
  if (m) return { ref: m[1].trim(), packageNo: Number(m[2]) };
  return {};
}

export function matchScan(raw: string, stops: StopDetail[], current: StopDetail): ScanMatch {
  const label = readLabel(raw);
  const others = stops.filter((s) => s.consignment_id !== current.consignment_id);
  if (label.sscc) {
    for (const stop of [current, ...others]) {
      const p = stop.packages.find((x) => x.sscc === label.sscc);
      if (p) return stop === current ? { kind: "here", package_no: p.package_no }
        : { kind: "other", stop, package_no: p.package_no };
    }
    return { kind: "unknown" };
  }
  if (label.packageNo !== undefined) {
    const stop = label.ref ? [current, ...others].find((s) => s.wms_ref === label.ref) : current;
    const p = stop?.packages.find((x) => x.package_no === label.packageNo);
    if (stop && p) return stop === current ? { kind: "here", package_no: p.package_no }
      : { kind: "other", stop, package_no: p.package_no };
  }
  return { kind: "unknown" };
}
```

`apps/driver/src/lib/useScanWedge.ts`, the scanner's, with a minimum length of 1 so a typed package number works:
```ts
import { useEffect, useRef } from "react";

/** Keyboard-wedge scanners type the code fast and press Enter. Collect a
 * burst of keys with short gaps; deliver it on Enter. Human typing into a
 * normal input is left alone; an input with data-scan="true" is treated as
 * a scan field so its Enter also delivers. (As the Simple WMS scanner's.) */
export function useScanWedge(onScan: (code: string) => void, opts: { maxGapMs?: number; minLength?: number } = {}) {
  const handler = useRef(onScan);
  handler.current = onScan;
  const maxGap = opts.maxGapMs ?? 80;
  const minLength = opts.minLength ?? 1;

  useEffect(() => {
    let buffer = "";
    let last = 0;
    let fast = true;
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      const inField = !!target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable);
      const scanField = inField && target!.dataset.scan === "true";
      if (inField && !scanField) return;
      const now = Date.now();
      if (e.key === "Enter") {
        const code = scanField ? (target as HTMLInputElement).value.trim() || buffer : buffer;
        if ((scanField || fast) && code.length >= minLength) {
          e.preventDefault();
          handler.current(code);
          if (scanField) (target as HTMLInputElement).value = "";
        }
        buffer = "";
        fast = true;
        return;
      }
      if (e.key.length !== 1) return;
      if (buffer && now - last > maxGap) {
        // slow typing: start over and remember it was not a scanner
        buffer = "";
        fast = false;
      } else if (!buffer) {
        fast = true;
      }
      buffer += e.key;
      last = now;
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [maxGap, minLength]);
}
```

`apps/driver/src/lib/where.ts`:
```ts
/** Where the phone is, for a delivery's record, asked for only during a run
 * and only when a stop is confirmed. Never more than a few seconds' wait:
 * without a fix the confirmation goes without one. */

export interface Position {
  latitude: string;
  longitude: string;
  accuracy_m: string | null;
}

export function getPosition(timeoutMs = 4000): Promise<Position | null> {
  if (typeof navigator === "undefined" || !navigator.geolocation) return Promise.resolve(null);
  return new Promise((resolve) => {
    const give = setTimeout(() => resolve(null), timeoutMs + 500);
    navigator.geolocation.getCurrentPosition(
      (p) => {
        clearTimeout(give);
        // as decimal strings: no float goes to the API
        resolve({ latitude: p.coords.latitude.toFixed(6), longitude: p.coords.longitude.toFixed(6),
                  accuracy_m: Number.isFinite(p.coords.accuracy) ? p.coords.accuracy.toFixed(1) : null });
      },
      () => { clearTimeout(give); resolve(null); },
      { enableHighAccuracy: true, timeout: timeoutMs, maximumAge: 60_000 },
    );
  });
}

/** Whether the driver has let the app know where it is. */
export async function locationAllowed(): Promise<"granted" | "denied" | "prompt" | "unknown"> {
  try {
    const s = await navigator.permissions.query({ name: "geolocation" as PermissionName });
    return s.state;
  } catch {
    return "unknown";
  }
}
```

`apps/driver/src/lib/picture.ts` (no test: jsdom has no canvas to draw on; the manual check in Task 22 takes a real photo through it):
```ts
/** Pictures for proof of delivery, made small enough to send from a black
 * spot: a photo becomes a JPEG at most 1600 pixels on its long side (and
 * loses the camera's EXIF, location included); a signature is a PNG. */

export const PHOTO_MAX_SIDE = 1600;

export async function shrinkPhoto(file: Blob, maxSide = PHOTO_MAX_SIDE, quality = 0.8): Promise<Blob> {
  const image = await createImageBitmap(file);
  const scale = Math.min(1, maxSide / Math.max(image.width, image.height));
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(image.width * scale);
  canvas.height = Math.round(image.height * scale);
  canvas.getContext("2d")!.drawImage(image, 0, 0, canvas.width, canvas.height);
  image.close();
  return toBlob(canvas, "image/jpeg", quality);
}

export function toBlob(canvas: HTMLCanvasElement, type: "image/png" | "image/jpeg", quality?: number): Promise<Blob> {
  return new Promise((resolve, reject) => {
    canvas.toBlob((b) => (b ? resolve(b) : reject(new Error("That picture couldn't be made. Try again."))), type, quality);
  });
}
```

**Step 4: Run the tests**

Run: `npm test && npm run typecheck`
Expected: `26 passed`.

**Step 5: Commit**

```bash
git add apps/driver/src/lib apps/driver/src/test
git commit -m "Keep the run on the phone, read carton labels and open the phone's maps"
```

### Task 14: Signing in, today's run, and the run between stops

**Files:**
- Create: `apps/driver/src/index.css`, `src/ui/icons.tsx`, `src/ui/kit.tsx`
- Create: `apps/driver/src/auth/Session.tsx`
- Create: `apps/driver/src/pages/SignIn.tsx` (`DriverSignIn.html`), `src/pages/Today.tsx` (the top of `DriverPrestart.html`), `src/pages/RunHome.tsx` (`DriverOffline.html`)
- Create: `apps/driver/src/App.tsx`, `apps/driver/src/main.tsx`
- Create: `apps/driver/src/test/harness.tsx`
- Test: `apps/driver/src/test/SignIn.test.tsx`, `Today.test.tsx`, `RunHome.test.tsx`

**Step 1: Write the failing tests**

`apps/driver/src/test/harness.tsx`: the API stubbed through `fetch` (a thrown `TypeError` is no signal), a driver already signed in, a fresh queue per test with its pictures in memory, and the run on the phone:
```tsx
import { render } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { api } from "../api/client";
import type { RunStops } from "../api/types";
import { AppRoutes } from "../App";
import { SessionProvider } from "../auth/Session";
import { memoryFiles } from "../lib/files";
import { RetryQueue } from "../lib/queue";
import { saveRun } from "../lib/run";
import { RUN } from "./samples";

export { RUN, stop } from "./samples";

export function json(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

export interface Call { method: string; url: string; body: unknown; contentType: string | null }
type Handler = (method: string, url: string, body: unknown) => Response | null | Promise<Response | null>;

/** `answer` answers the API, or it is a 404; a thrown TypeError is no signal. */
export function stubApi(answer: Handler = () => null) {
  const calls: Call[] = [];
  const fetchMock = vi.fn(async (url: string, init: RequestInit = {}) => {
    const method = init.method ?? "GET";
    const headers = (init.headers ?? {}) as Record<string, string>;
    const raw = init.body;
    const body = typeof raw === "string" ? JSON.parse(raw) : raw;
    calls.push({ method, url, body, contentType: headers["Content-Type"] ?? null });
    const a = await answer(method, url, body);
    return a ?? json(404, { detail: `no stub for ${method} ${url}` });
  });
  vi.stubGlobal("fetch", fetchMock);
  return { calls };
}

export function signedIn() {
  window.localStorage.setItem("tms.driver.session", JSON.stringify({
    token: "tms_d.test", expires_at: Date.now() + 3_600_000,
    driver: { kind: "driver", id: 3, name: "kim", display_name: "Kim", role: "driver",
              scopes: ["fleet:read", "deliveries:read", "deliveries:write"] },
  }));
}

function Where() {
  return <output data-testid="where">{useLocation().pathname}</output>;
}

export function renderApp(path: string, opts: { online?: () => boolean } = {}) {
  api.token = null;
  const queue = new RetryQueue({ send: (r) => api.send(r), files: memoryFiles(), online: opts.online ?? (() => true) });
  render(
    <MemoryRouter initialEntries={[path]}>
      <SessionProvider queue={queue}>
        <AppRoutes />
        <Where />
      </SessionProvider>
    </MemoryRouter>,
  );
  return { queue };
}

/** The run already on the phone, as starting it leaves it. */
export function onThePhone(run: RunStops = RUN) {
  saveRun(run);
}
```

`apps/driver/src/test/SignIn.test.tsx`:
```tsx
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { json, renderApp, stubApi } from "./harness";

const KIM = { kind: "driver", id: 3, name: "kim", display_name: "Kim", role: "driver", scopes: [] };

describe("signing in", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("signs in with the driver code and a PIN on the keypad", async () => {
    const { calls } = stubApi((_method, url) => {
      if (url === "/v1/auth/driver-login") return json(200, { token: "tms_d.x", expires_in: 43200, driver: KIM });
      if (url.startsWith("/v1/driver/runs")) return json(200, []);
      return null;
    });
    renderApp("/sign-in");
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Driver code"), "kim");
    for (const d of ["2", "4", "6", "8"]) await user.click(screen.getByRole("button", { name: d }));
    expect(screen.getByRole("img", { name: "PIN, 4 numbers entered" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByText("No run for you today.")).toBeInTheDocument();
    expect(calls[0].body).toEqual({ code: "kim", pin: "2468" });
    expect(screen.getByTestId("where")).toHaveTextContent("/");
  });

  it("says a wrong PIN is wrong, and clears it", async () => {
    stubApi(() => json(401, { detail: "wrong driver code or PIN", code: "wrong_pin" }));
    renderApp("/sign-in");
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Driver code"), "kim");
    for (const d of ["1", "1", "1", "1"]) await user.click(screen.getByRole("button", { name: d }));
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Wrong driver code or PIN.");
    await waitFor(() => expect(screen.getByRole("img", { name: "PIN, 0 numbers entered" })).toBeInTheDocument());
  });
});
```

`apps/driver/src/test/Today.test.tsx`:
```tsx
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { loadRun } from "../lib/run";
import { json, renderApp, RUN, signedIn, stubApi } from "./harness";

const TODAY = { id: 7, run_no: "R-0007", date: "2026-10-14", status: "planned",
                vehicle: { rego: "TMS041", type: "rigid" }, stops: 2, packages: 4, delivered: 0, failed: 0 };

describe("today's run", () => {
  beforeEach(signedIn);
  afterEach(() => vi.unstubAllGlobals());

  it("loads the whole run onto the phone and starts it", async () => {
    let started = false;
    const { calls } = stubApi((_method, url) => {
      if (url.startsWith("/v1/driver/runs?date=")) return json(200, [TODAY]);
      if (url === "/v1/runs/7/stops") return json(200, started ? RUN : { ...RUN, status: "planned", departed_at: null });
      if (url === "/v1/runs/7/start") {
        started = true;
        return json(200, { message_id: "m", run_id: 7, run_no: "R-0007", status: "departed", departed_at: "2026-10-14T21:10:00Z" });
      }
      return null;
    });
    renderApp("/");
    expect(await screen.findByRole("radio", { name: /TMS041/ })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByText("2 stops · 4 packages")).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("button", { name: "Start run" }));
    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("/run/7"));
    const start = calls.find((c) => c.url === "/v1/runs/7/start")!;
    expect(start.body).toEqual({ message_id: expect.stringMatching(/^[0-9a-f-]{36}$/) });
    expect(loadRun(7)?.status).toBe("departed");
    expect(loadRun(7)?.stops).toHaveLength(2);
    expect(await screen.findByRole("link", { name: /Start stop 1/ })).toBeInTheDocument();
  });

  it("says starting needs signal, and sends the same start again after", async () => {
    let tries = 0;
    const { calls } = stubApi((_method, url) => {
      if (url.startsWith("/v1/driver/runs?date=")) return json(200, [TODAY]);
      if (url === "/v1/runs/7/stops") return json(200, { ...RUN, status: "planned" });
      if (url === "/v1/runs/7/start") {
        tries += 1;
        if (tries === 1) throw new TypeError("Failed to fetch");
        return json(200, { message_id: "m", run_id: 7, run_no: "R-0007", status: "departed", departed_at: null });
      }
      return null;
    });
    renderApp("/");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Start run" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("No signal. Starting a run needs a connection");
    await user.click(screen.getByRole("button", { name: "Start run" }));
    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("/run/7"));
    const starts = calls.filter((c) => c.url === "/v1/runs/7/start").map((c) => (c.body as { message_id: string }).message_id);
    expect(starts).toHaveLength(2);
    expect(starts[0]).toBe(starts[1]);
  });

  it("says when there is no run today", async () => {
    stubApi((_method, url) => (url.startsWith("/v1/driver/runs") ? json(200, []) : null));
    renderApp("/");
    expect(await screen.findByText("No run for you today.")).toBeInTheDocument();
  });
});
```

`apps/driver/src/test/RunHome.test.tsx`:
```tsx
import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { json, onThePhone, renderApp, RUN, signedIn, stubApi } from "./harness";

describe("between stops", () => {
  beforeEach(() => { signedIn(); onThePhone(); });
  afterEach(() => vi.unstubAllGlobals());

  it("works from memory with no signal, and sends what waited once it's back", async () => {
    let online = false;
    const { calls } = stubApi((method) => {
      if (method === "POST") return json(200, { outcome: "failed" });
      return json(200, RUN);
    });
    const { queue } = renderApp("/run/7", { online: () => online });
    await act(async () => {
      await queue.submit({ path: "/v1/runs/7/stops/31/fail", body: { reason: "closed" }, label: "Stop 1 · couldn't deliver · closed",
                           run_id: 7, consignment_id: 31, outcome: "failed" });
    });
    expect(screen.getByText(/1 confirmation waiting/)).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "1 of 2 stops done" })).toBeInTheDocument();
    const next = screen.getByRole("region", { name: "Next stop" });
    expect(within(next).getByText("Two Rivers Vet")).toBeInTheDocument();
    expect(screen.getByText("Stop 1 · couldn't deliver · closed")).toBeInTheDocument();
    expect(screen.getByText(/Waiting since/)).toBeInTheDocument();
    expect(calls.filter((c) => c.method === "POST")).toHaveLength(0);

    online = true;
    await userEvent.setup().click(screen.getByRole("button", { name: "Retry now" }));
    await waitFor(() => expect(screen.getByText(/^Sent /)).toBeInTheDocument());
    expect(calls.filter((c) => c.url === "/v1/runs/7/stops/31/fail")).toHaveLength(1);
  });

  it("finishes the run once every stop is done", async () => {
    const done = { ...RUN, stops: RUN.stops.map((s, i) => ({ ...s, outcome: i ? "failed" as const : "delivered" as const })) };
    onThePhone(done);
    const { calls } = stubApi((_method, url) => {
      if (url === "/v1/runs/7/finish") return json(200, { status: "done" });
      return json(200, done);
    });
    renderApp("/run/7");
    expect(await screen.findByText("Every stop is done.")).toBeInTheDocument();
    expect(screen.getByText(/1 delivered, 1 coming back to the depot/)).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("button", { name: "Finish the run" }));
    expect(await screen.findByText("R-0007 is finished.")).toBeInTheDocument();
    await waitFor(() => expect(calls.some((c) => c.url === "/v1/runs/7/finish")).toBe(true));
    expect(screen.getByRole("link", { name: "Back to today's runs" })).toBeInTheDocument();
  });
});
```

**Step 2: Run them to see them fail**

Run: `npm test`
Expected: the three files fail to resolve `../App`; the 26 from before pass.

**Step 3: Styles and icons**

`apps/driver/src/index.css`: the colours of `design/README.md` as the desktop has them, and the classes of the `Driver*.html` screens; the drawn 390 × 844 `.phone` fills a real phone's screen (`100dvh`, safe-area padding) instead:
```css
@import "tailwindcss";

/* Simple TMS colours (design/README.md), as the desktop has them. */
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
  --color-row: #EDF3EF;
  --color-amber: #E8A317;
  --color-amber-ink: #8A5A00;
  --color-amber-bg: #FFF3D6;
  --color-red: #C9343D;
  --color-red-ink: #A3242C;
  --color-red-bg: #FCE9EA;
  --color-red-line: #F1B9BD;
  --font-sans: Manrope, ui-sans-serif, system-ui, sans-serif;
}

@layer base {
  html, body, #root { min-height: 100%; }
  body { margin: 0; background: var(--color-page); color: var(--color-ink); font-family: var(--font-sans); -webkit-font-smoothing: antialiased; -webkit-tap-highlight-color: transparent; }
  a { color: #146B45; }
  :focus-visible { outline: 3px solid rgba(31, 138, 91, 0.45); outline-offset: 2px; }
  button, input, select, textarea { font: inherit; color: inherit; }
}

/* The classes of design/screens/Driver*.html, as drawn; the phone fills a
 * real phone's screen instead of the drawn 390 x 844. */
@layer components {
  .sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
  .pill { display: inline-flex; align-items: center; gap: 5px; padding: 4px 10px; border-radius: 999px; font-size: 12px; font-weight: 800; white-space: nowrap; line-height: 1.3; }
  .p-green { background: #E3F3EA; color: #146B45; }
  .p-amber { background: var(--color-amber-bg); color: var(--color-amber-ink); }
  .p-red { background: var(--color-red-bg); color: var(--color-red-ink); }
  .p-grey { background: #EEF2EF; color: #4B5A53; }
  .field { height: 44px; padding: 0 12px; border: 1px solid var(--color-line-2); border-radius: 12px; background: #fff; font-size: 14px; color: var(--color-ink); width: 100%; }
  .small { font-size: 12px; color: var(--color-muted); }
  .num { font-variant-numeric: tabular-nums; }

  .phone { position: relative; width: 100%; max-width: 480px; height: 100vh; height: 100dvh; margin: 0 auto; padding: max(18px, env(safe-area-inset-top)) 18px max(16px, env(safe-area-inset-bottom)); background: var(--color-page); display: flex; flex-direction: column; gap: 12px; overflow: hidden; }
  .top { display: flex; align-items: center; gap: 10px; flex: none; }
  .back { width: 44px; height: 44px; flex: none; border-radius: 14px; background: #fff; border: 1px solid var(--color-line); color: var(--color-ink); display: grid; place-items: center; text-decoration: none; cursor: pointer; }
  .tile { width: 44px; height: 44px; border-radius: 14px; background: var(--color-green); color: #fff; display: grid; place-items: center; flex: none; }
  .ttl { font-size: 18px; font-weight: 800; line-height: 1.2; }
  .st { font-size: 12px; color: var(--color-ink-2); }
  .chip { padding: 6px 10px; border-radius: 999px; background: #fff; border: 1px solid var(--color-line); font-size: 12px; font-weight: 800; color: var(--color-ink-2); white-space: nowrap; display: inline-flex; align-items: center; gap: 6px; }
  .chip.on { color: var(--color-green-dark); background: var(--color-tint); border-color: #BFDCCB; }
  .scroll { flex: 1; min-height: 0; overflow: auto; display: flex; flex-direction: column; gap: 10px; }
  .scroll > * { flex: none; }
  .card { padding: 14px 16px; border-radius: 18px; background: #fff; border: 1px solid var(--color-line); }
  .big { height: 58px; border-radius: 16px; font-weight: 800; font-size: 18px; cursor: pointer; display: flex; align-items: center; justify-content: center; gap: 8px; text-decoration: none; border: 0; padding: 0 14px; }
  .big:disabled { opacity: 0.5; cursor: default; }
  .big.primary { background: var(--color-green); color: #fff; }
  .big.primary:hover:not(:disabled) { background: var(--color-green-dark); color: #fff; }
  .big.ghost { background: #fff; color: var(--color-ink); border: 2px solid var(--color-line-2); font-size: 16px; }
  .big.white { background: #fff; color: var(--color-green-dark); font-size: 16px; }
  .scanbox { min-height: 62px; padding: 10px 14px; border-radius: 16px; border: 2px dashed var(--color-green); background: #fff; display: flex; align-items: center; gap: 12px; cursor: pointer; color: var(--color-ink); text-align: left; width: 100%; }
  .scanbox.quiet { border-color: var(--color-line-2); }
  .scanbox b { display: block; font-size: 16px; font-weight: 800; }
  .scanbox small { display: block; font-size: 12px; color: var(--color-ink-2); }
  .scanbox input { margin-top: 4px; width: 100%; height: 32px; border: 1px solid var(--color-line); border-radius: 8px; padding: 0 8px; font-size: 14px; background: #F7FAF8; }
  .lbl { font-size: 13px; font-weight: 800; letter-spacing: 0.6px; color: var(--color-ink-2); text-transform: uppercase; }
  .input { height: 54px; width: 100%; padding: 0 16px; border-radius: 14px; font-size: 17px; font-weight: 600; color: var(--color-ink); background: #fff; border: 2px solid var(--color-line-2); }
  .goal { display: flex; flex-direction: column; gap: 6px; flex: none; }
  .segs { display: grid; gap: 4px; }
  .segs span { height: 10px; border-radius: 999px; background: #D5E3D9; }
  .segs .d { background: var(--color-green); }
  .segs .x { background: var(--color-red); }
  .segs .c { background: #7FC3A0; }
  .msg { font-size: 13px; border-radius: 12px; padding: 10px 12px; }
  .msg.r { background: var(--color-red-bg); color: var(--color-red-ink); }
  .msg.a { background: var(--color-amber-bg); color: var(--color-amber-ink); }
  .msg.g { background: var(--color-tint); color: var(--color-green-dark); }

  .key { height: 52px; border-radius: 16px; border: 1px solid var(--color-line); background: #fff; color: var(--color-ink); font-size: 24px; font-weight: 800; cursor: pointer; }
  .key.quiet { border: 0; background: transparent; color: var(--color-ink-2); font-size: 15px; display: grid; place-items: center; }
  .carton { display: flex; align-items: center; gap: 10px; padding: 9px 12px; border-top: 1px solid var(--color-row); cursor: pointer; }
  .tick { width: 30px; height: 30px; border-radius: 50%; display: grid; place-items: center; flex: none; border: 2px solid var(--color-line-2); color: #fff; }
  .carton[aria-checked="true"] .tick { background: var(--color-green); border-color: var(--color-green); }
  .choice { min-height: 76px; padding: 10px; border-radius: 16px; cursor: pointer; text-align: left; display: flex; flex-direction: column; gap: 4px; border: 1px solid var(--color-line); background: #fff; }
  .choice[aria-checked="true"] { border: 2px solid var(--color-green); background: var(--color-tint); }
  .ding { position: absolute; left: 12px; right: 12px; bottom: 12px; padding: 20px 18px 16px; border-radius: 24px; background: var(--color-green); color: #fff; box-shadow: 0 -10px 40px rgba(23, 37, 31, 0.25); display: flex; flex-direction: column; gap: 10px; overflow: hidden; }
  .pad { display: block; width: 100%; height: 150px; touch-action: none; border-radius: 10px; background: #fff; }
}

@keyframes tms-ding-in { from { transform: translateY(30px); opacity: 0; } to { transform: none; opacity: 1; } }
.ding { animation: tms-ding-in 0.25s ease-out; }
@media (prefers-reduced-motion: reduce) { .ding { animation: none; } }
```

`apps/driver/src/ui/icons.tsx`, the line icons the driver screens draw:
```tsx
/** The line icons the driver screens use, drawn as in design/screens. */
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

export function Icon({ name, size = 20, width = 2 }: { name: keyof typeof PATHS; size?: number; width?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" strokeWidth={width} aria-hidden="true" {...base}>
      {PATHS[name]}
    </svg>
  );
}

const PATHS = {
  truck: <><path d="M2 6h12v10H2Z" /><path d="M14 9h4.5L22 12.5V16h-8Z" /><circle cx="6.5" cy="17.5" r="2" /><circle cx="17.5" cy="17.5" r="2" /></>,
  back: <><path d="m12 19-7-7 7-7" /><path d="M19 12H5" /></>,
  go: <path d="M5 12h14M13 6l6 6-6 6" />,
  check: <path d="m5 12 5 5 9-10" />,
  map: <><path d="M9 4 3 6v14l6-2 6 2 6-2V4l-6 2Z" /><path d="M9 4v14M15 6v14" /></>,
  pin: <path d="M3 11 21 3l-8 18-2-8Z" />,
  scan: <><path d="M3 7V5a2 2 0 0 1 2-2h2M17 3h2a2 2 0 0 1 2 2v2M21 17v2a2 2 0 0 1-2 2h-2M7 21H5a2 2 0 0 1-2-2v-2" /><path d="M8 7v10M12 7v10M16 7v10" /></>,
  note: <><path d="M5 4h14v12l-4 4H5Z" /><path d="M15 20v-4h4M9 9h6M9 13h4" /></>,
  camera: <><path d="M4 8h3l2-3h6l2 3h3v11H4Z" /><circle cx="12" cy="13" r="3.5" /></>,
  info: <><circle cx="12" cy="12" r="9" /><path d="M12 11v5M12 8h.01" /></>,
  nosignal: <><path d="M2 20h.01M7 20v-4M12 20v-8M17 20V8M22 4v16" /><path d="M3 3l18 18" /></>,
  delete: <><path d="M9 6h11v12H9l-6-6Z" /><path d="m12 10 4 4M16 10l-4 4" /></>,
  closed: <><path d="M5 21V4h11v17M3 21h18" /><path d="M13 12h.01" /></>,
  refused: <><circle cx="12" cy="12" r="9" /><path d="m5.6 5.6 12.8 12.8" /></>,
  damaged: <><path d="M12 3 20 7.5v9L12 21 4 16.5v-9Z" /><path d="m10 8 2 3-2 2 2 3" /></>,
  wrong: <path d="M12 3v18M5 6h11l3 3-3 3H5Z" />,
  returns: <><path d="M9 14 4 9l5-5" /><path d="M4 9h10a6 6 0 0 1 0 12h-3" /></>,
  phone: <path d="M5 4h4l2 5-2.5 1.5a11 11 0 0 0 5 5L15 13l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2" />,
};
```

`apps/driver/src/ui/kit.tsx`, the pieces every run screen shares: the run's top bar with its location chip, the day's goal, the no-signal banner, and "Waiting to send":
```tsx
/** The pieces every run screen shares: the run's top bar, the day's goal,
 * the no-signal banner and the list of what is waiting to send. */
import { useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import type { Outcome, RunStops } from "../api/types";
import type { QueueItem } from "../lib/queue";
import { plural } from "../lib/run";
import { clock } from "../lib/time";
import { locationAllowed } from "../lib/where";
import { Icon } from "./icons";

export function Phone({ label, children }: { label: string; children: ReactNode }) {
  return <main className="phone" aria-label={label}>{children}</main>;
}

export function BackTop({ to, label, title, sub }: { to: string; label: string; title: string; sub: string }) {
  return (
    <div className="top">
      <Link to={to} className="back" aria-label={label}><Icon name="back" size={22} width={2.2} /></Link>
      <div style={{ flex: 1, minWidth: 0 }}><div className="ttl">{title}</div><div className="st">{sub}</div></div>
    </div>
  );
}

/** The run, the truck, the driver and the time, and whether this phone
 * records where each drop happened. Only during a run, and it says so. */
export function RunTop({ run }: { run: RunStops }) {
  const [now, setNow] = useState(() => new Date());
  const [where, setWhere] = useState<string>("unknown");
  useEffect(() => {
    void locationAllowed().then(setWhere);
    const t = setInterval(() => setNow(new Date()), 30_000);
    return () => clearInterval(t);
  }, []);
  const on = where === "granted";
  return (
    <div className="top">
      <span className="tile"><Icon name="truck" size={22} /></span>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div className="ttl">Run {run.run_no}</div>
        <div className="st">{run.vehicle.rego} · {run.driver.name} · {clock(now)}</div>
      </div>
      <span className={`chip${on ? " on" : ""}`}><Icon name="pin" size={13} width={2.4} />
        {on ? "Location at drops" : "Location off"}</span>
    </div>
  );
}

export function Goal({ run, done, current }: { run: RunStops; done: Map<number, Outcome>; current?: number | null }) {
  const total = run.stops.length;
  const finished = run.stops.filter((s) => done.has(s.consignment_id)).length;
  return (
    <div className="goal">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", fontSize: 13, fontWeight: 800 }}>
        <span>Today's goal · {plural(total, "stop")}</span>
        <span style={{ color: "var(--color-muted)" }}>{finished} done · {total - finished} to go</span>
      </div>
      <div className="segs" role="img" aria-label={`${finished} of ${total} stops done`}
           style={{ gridTemplateColumns: `repeat(${Math.max(total, 1)},minmax(0,1fr))` }}>
        {run.stops.map((s) => {
          const o = done.get(s.consignment_id);
          const cls = o === "delivered" ? "d" : o === "failed" ? "x" : s.consignment_id === current ? "c" : "";
          return <span key={s.consignment_id} className={cls} />;
        })}
      </div>
    </div>
  );
}

export function OfflineBanner({ online, waiting }: { online: boolean; waiting: number }) {
  if (online && waiting === 0) return null;
  return (
    <div role="status" style={{ display: "flex", alignItems: "center", gap: 12, padding: "12px 14px", borderRadius: 16, flex: "none",
                                background: "var(--color-amber-bg)", color: "var(--color-amber-ink)" }}>
      <Icon name="nosignal" size={26} width={2.2} />
      <span style={{ flex: 1, minWidth: 0 }}>
        <span style={{ display: "block", fontSize: 15, fontWeight: 800 }}>
          {online ? `Sending · ${waiting} waiting` : "No signal · working from memory"}</span>
        <span style={{ display: "block", fontSize: 12, fontWeight: 600 }}>
          {waiting > 0
            ? `${plural(waiting, "confirmation")} waiting · they send by themselves when you're back in range`
            : "Your whole run is on this phone, so keep going."}</span>
      </span>
    </div>
  );
}

function since(iso: string): string {
  return clock(new Date(iso));
}

const PICTURES: Record<string, string> = { signature: "signature", photo: "photo" };

/** WAITING TO SEND: newest first, a row for the pictures while they wait. */
export function WaitingList({ items }: { items: QueueItem[] }) {
  if (items.length === 0) return null;
  const rows: { key: string; text: string; pill: ReactNode; error?: string }[] = [];
  for (const i of items.slice(0, 8)) {
    const unsent = i.files.filter((f) => !f.sent);
    if (i.status === "queued" && unsent.length > 0) {
      rows.push({ key: `${i.id}-files`, text: `${i.label.split(" · ")[0]} · ${unsent.map((f) => PICTURES[f.kind]).join(" and ")}`,
                  pill: <span className="pill p-amber" style={{ fontSize: 11 }}>Waiting since {since(i.created_at)}</span> });
    }
    rows.push({
      key: i.id, text: i.label, error: i.status === "failed" ? i.error : undefined,
      pill: i.status === "sent" ? <span className="pill p-green" style={{ fontSize: 11 }}>Sent {since(i.sent_at ?? i.created_at)}</span>
        : i.status === "failed" ? <span className="pill p-red" style={{ fontSize: 11 }}>Not taken</span>
        : <span className="pill p-amber" style={{ fontSize: 11 }}>Waiting since {since(i.created_at)}</span>,
    });
  }
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <span className="lbl">Waiting to send</span>
      <div style={{ borderRadius: 16, background: "#fff", border: "1px solid var(--color-line)", overflow: "hidden" }}>
        {rows.map((r) => (
          <div key={r.key} style={{ padding: "11px 14px", borderTop: "1px solid var(--color-row)" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10 }}>
              <span style={{ fontSize: 14, fontWeight: 700 }}>{r.text}</span>{r.pill}
            </div>
            {r.error && <div className="small" style={{ color: "var(--color-red-ink)", marginTop: 4 }}>{r.error}</div>}
          </div>
        ))}
      </div>
    </div>
  );
}
```

**Step 4: The session**

`apps/driver/src/auth/Session.tsx`. As the scanner's: the session in `localStorage` (until it expires), online and offline events, and the queue drained on `online`, every 15 s while anything waits, and on signing in. Signing out leaves the queue alone. The provider takes a queue so each test has its own.
```tsx
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api } from "../api/client";
import type { DriverSessionOut, Me } from "../api/types";
import { indexedDbFiles } from "../lib/files";
import { RetryQueue } from "../lib/queue";

interface Stored { token: string; expires_at: number; driver: Me }

interface State {
  driver: Me | null;
  online: boolean;
  queue: RetryQueue;
  /** confirmations still to send */
  waiting: number;
  signIn: (code: string, pin: string) => Promise<void>;
  signOut: () => void;
}

const Ctx = createContext<State | null>(null);
const SESSION_KEY = "tms.driver.session";
// what is waiting goes when the signal comes back, and every 15 s while it is patchy
const DRAIN_EVERY_MS = 15_000;

function readSession(): Stored | null {
  try {
    const s = JSON.parse(window.localStorage.getItem(SESSION_KEY) ?? "null") as Stored | null;
    return s && s.expires_at > Date.now() ? s : null;
  } catch {
    return null;
  }
}

function writeSession(s: Stored | null) {
  try {
    if (s) window.localStorage.setItem(SESSION_KEY, JSON.stringify(s));
    else window.localStorage.removeItem(SESSION_KEY);
  } catch { /* private mode: signed in for this visit only */ }
}

export const defaultQueue = new RetryQueue({ send: (r) => api.send(r), files: indexedDbFiles });

export function SessionProvider({ children, queue = defaultQueue }: { children: ReactNode; queue?: RetryQueue }) {
  const [session, setSession] = useState<Stored | null>(() => {
    const s = readSession();
    api.token = s?.token ?? null;
    return s;
  });
  const [online, setOnline] = useState(typeof navigator === "undefined" ? true : navigator.onLine);
  const [waiting, setWaiting] = useState(queue.pending().length);

  const signOut = useCallback(() => {
    // what is waiting stays on the phone, and goes once a driver signs in again
    api.token = null;
    writeSession(null);
    setSession(null);
  }, []);

  useEffect(() => {
    api.onSignedOut = signOut;
    const up = () => { setOnline(true); void queue.drain(); };
    const down = () => setOnline(false);
    window.addEventListener("online", up);
    window.addEventListener("offline", down);
    const unsubscribe = queue.subscribe(() => setWaiting(queue.pending().length));
    const tick = setInterval(() => { if (navigator.onLine && queue.pending().length) void queue.drain(); }, DRAIN_EVERY_MS);
    return () => {
      window.removeEventListener("online", up);
      window.removeEventListener("offline", down);
      unsubscribe();
      clearInterval(tick);
    };
  }, [queue, signOut]);

  // signed in again: send what waited for it
  useEffect(() => { if (session) void queue.drain(); }, [session, queue]);

  const signIn = useCallback(async (code: string, pin: string) => {
    const out = await api.post<DriverSessionOut>("/v1/auth/driver-login", { code, pin });
    const s = { token: out.token, expires_at: Date.now() + out.expires_in * 1000, driver: out.driver };
    api.token = s.token;
    writeSession(s);
    setSession(s);
  }, []);

  const value = useMemo<State>(() => ({
    driver: session?.driver ?? null, online, queue, waiting, signIn, signOut,
  }), [session, online, queue, waiting, signIn, signOut]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useSession(): State {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useSession outside SessionProvider");
  return ctx;
}

/** Re-render whenever the queue changes. */
export function useQueueItems(runId: number) {
  const { queue } = useSession();
  const [, bump] = useState(0);
  useEffect(() => queue.subscribe(() => bump((n) => n + 1)), [queue]);
  return queue.forRun(runId);
}
```

**Step 5: The screens**

`apps/driver/src/pages/SignIn.tsx`:
```tsx
import { useEffect, useState, type FormEvent } from "react";
import { Navigate } from "react-router-dom";
import { ApiError } from "../api/client";
import { useSession } from "../auth/Session";
import { Icon, Logo } from "../ui/icons";
import { Phone } from "../ui/kit";

const MAX_PIN = 8;

function refusal(e: unknown): string {
  if (!(e instanceof ApiError)) return "No signal: signing in needs a connection. Try again in range.";
  if (e.code === "locked") return e.message.charAt(0).toUpperCase() + e.message.slice(1) + ".";
  if (e.code === "wrong_pin") return "Wrong driver code or PIN.";
  return e.message;
}

/** DriverSignIn.html: driver code and PIN, on a keypad. */
export function SignIn() {
  const { driver, signIn } = useSession();
  const [code, setCode] = useState("");
  const [pin, setPin] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // a phone with a keyboard can type the PIN too
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement)?.tagName === "INPUT") return;
      if (/^\d$/.test(e.key)) setPin((p) => (p.length < MAX_PIN ? p + e.key : p));
      if (e.key === "Backspace") setPin((p) => p.slice(0, -1));
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  if (driver) return <Navigate to="/" replace />;

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await signIn(code.trim(), pin);
    } catch (err) {
      setError(refusal(err));
      setPin("");
    } finally {
      setBusy(false);
    }
  }

  const dots = Math.max(4, pin.length);
  const press = (d: string) => setPin((p) => (p.length < MAX_PIN ? p + d : p));
  return (
    <Phone label="Driver sign-in">
      <form onSubmit={submit} style={{ display: "contents" }}>
        <div className="top" style={{ justifyContent: "space-between" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}><Logo />
            <span style={{ fontSize: 17, fontWeight: 800 }}>Simple TMS</span></div>
          <span className="chip">Driver app</span>
        </div>
        <h1 style={{ margin: "4px 0 0", fontSize: 28, fontWeight: 800, letterSpacing: -0.4 }}>Morning! Ready to roll?</h1>
        <label style={{ display: "flex", flexDirection: "column", gap: 6, fontSize: 14, fontWeight: 800 }}>Driver code
          <input className="input" type="text" autoComplete="username" autoCapitalize="characters" autoCorrect="off"
                 spellCheck={false} value={code} onChange={(e) => setCode(e.target.value)} required />
        </label>
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <span style={{ fontSize: 14, fontWeight: 800 }}>PIN</span>
          <div role="img" aria-label={`PIN, ${pin.length} ${pin.length === 1 ? "number" : "numbers"} entered`}
               style={{ display: "flex", justifyContent: "center", gap: 18, padding: "2px 0" }}>
            {Array.from({ length: dots }, (_, i) => (
              <span key={i} style={{ width: 18, height: 18, borderRadius: "50%", border: "2px solid var(--color-green)",
                                     background: i < pin.length ? "var(--color-green)" : "transparent" }} />
            ))}
          </div>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(3,minmax(0,1fr))", gap: 8 }}>
          {["1", "2", "3", "4", "5", "6", "7", "8", "9"].map((d) => (
            <button key={d} type="button" className="key" onClick={() => press(d)}>{d}</button>
          ))}
          <button type="button" className="key quiet" aria-label="Clear PIN" onClick={() => setPin("")}>Clear</button>
          <button type="button" className="key" onClick={() => press("0")}>0</button>
          <button type="button" className="key quiet" aria-label="Delete last number" onClick={() => setPin((p) => p.slice(0, -1))}>
            <Icon name="delete" size={24} width={2.2} /></button>
        </div>
        {error && <div className="msg r" role="alert">{error}</div>}
        <button type="submit" className="big primary" disabled={busy || !code.trim() || pin.length < 4}>Sign in</button>
        <div style={{ marginTop: "auto", display: "flex", flexDirection: "column", gap: 6, alignItems: "center", textAlign: "center",
                      fontSize: 12, color: "var(--color-muted)" }}>
          <span style={{ display: "flex", alignItems: "center", gap: 6, fontWeight: 700, color: "var(--color-ink-2)" }}>
            <Icon name="pin" size={14} width={2.2} />Location is used only while you're on a run</span>
          <span>Your whole run loads onto the phone when you start, so black spots don't stop you.</span>
        </div>
      </form>
    </Phone>
  );
}
```

`apps/driver/src/pages/Today.tsx`. "Start run" loads the whole run (`GET /v1/runs/{id}/stops`) into `localStorage`, asks for location once (at the depot), then starts it with a `message_id` kept for this visit, so pressing Start again after a dropout is the same start. With no signal and a run already on the phone, it offers to carry on from memory.
```tsx
import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, ApiError, Unreachable } from "../api/client";
import type { DriverRun, RunStops, StartOut } from "../api/types";
import { useSession } from "../auth/Session";
import { loadRun, plural, saveRun } from "../lib/run";
import { dayLabel, isoDay } from "../lib/time";
import { getPosition } from "../lib/where";
import { Icon } from "../ui/icons";
import { Phone } from "../ui/kit";

const CURRENT = "tms.driver.current";

export function rememberCurrent(runId: number) {
  try { window.localStorage.setItem(CURRENT, String(runId)); } catch { /* ignore */ }
}
function current(): number | null {
  try { return Number(window.localStorage.getItem(CURRENT)) || null; } catch { return null; }
}

function said(e: unknown): string {
  if (e instanceof Unreachable) return "No signal. Starting a run needs a connection, to load the whole run onto this phone.";
  if (e instanceof ApiError && e.code === "over_capacity") return `${e.message} Ask the office to move a stop.`;
  return e instanceof Error ? e.message : "Something went wrong. Try again.";
}

function state(r: DriverRun): string {
  if (r.status === "planned") return "Ready to go. Starting tells the office you've left, and loads the whole run onto this phone.";
  if (r.status === "departed") return `On the road: ${r.delivered + r.failed} of ${plural(r.stops, "stop")} done.`;
  return `Finished: ${r.delivered} delivered, ${r.failed} coming back.`;
}

/** The top of DriverPrestart.html: the driver's run and truck for today.
 * The pre-start check that goes between picking and starting comes with
 * build step 4; until then the run starts from here. */
export function Today() {
  const { driver, queue, waiting, signOut } = useSession();
  const navigate = useNavigate();
  const [runs, setRuns] = useState<DriverRun[] | null>(null);
  const [chosen, setChosen] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // one message_id per run for this visit: pressing Start again after a dropout is the same start
  const startIds = useRef<Record<number, string>>({});
  const saved = current();
  const savedRun = saved ? loadRun(saved) : null;

  const load = useCallback(async () => {
    setError(null);
    try {
      const list = await api.get<DriverRun[]>(`/v1/driver/runs?date=${isoDay(new Date())}`);
      setRuns(list);
      setChosen((c) => c ?? list.find((r) => r.status !== "done")?.id ?? list[0]?.id ?? null);
    } catch (e) {
      setError(e instanceof Unreachable ? "No signal, so today's runs can't be loaded." : said(e));
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  async function go(r: DriverRun) {
    setBusy(true);
    setError(null);
    try {
      const whole = await api.get<RunStops>(`/v1/runs/${r.id}/stops`);
      saveRun(whole);
      if (whole.status === "planned") {
        // asked here, at the depot, rather than at the first drop
        void getPosition(8000);
        const id = (startIds.current[r.id] ??= crypto.randomUUID());
        const out = await api.post<StartOut>(`/v1/runs/${r.id}/start`, { message_id: id });
        saveRun({ ...whole, status: out.status, departed_at: out.departed_at });
      }
      rememberCurrent(r.id);
      navigate(`/run/${r.id}`);
    } catch (e) {
      setError(said(e));
    } finally {
      setBusy(false);
    }
  }

  function leave() {
    if (waiting > 0 && !window.confirm(
      `${plural(waiting, "confirmation")} still waiting to send. They go once you sign in again. Sign out?`)) return;
    signOut();
  }

  const run = runs?.find((r) => r.id === chosen) ?? null;
  return (
    <Phone label="Today's run">
      <div className="top">
        <div style={{ flex: 1, minWidth: 0 }}><div className="ttl">Today's run</div>
          <div className="st">{driver?.display_name} · {dayLabel(new Date())}</div></div>
        <button type="button" className="chip" onClick={leave}>Sign out</button>
      </div>
      {runs && runs.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 6, flex: "none" }}>
          <span className="lbl">Your truck</span>
          <div role="radiogroup" aria-label="Pick your run" style={{ display: "flex", gap: 6 }}>
            {runs.map((r) => (
              <button key={r.id} type="button" role="radio" aria-checked={r.id === chosen} onClick={() => setChosen(r.id)}
                      className="choice" style={{ flex: 1, minWidth: 0, minHeight: 0, padding: "8px 6px", textAlign: "center", alignItems: "center" }}>
                <b style={{ display: "block", fontSize: 14 }}>{r.vehicle.rego}</b>
                <span style={{ fontSize: 11, fontWeight: 700, color: "var(--color-ink-2)", textTransform: "capitalize" }}>
                  {r.vehicle.type} · {r.run_no}</span>
              </button>
            ))}
          </div>
        </div>
      )}
      <div className="scroll">
        {run && (
          <section className="card" aria-label={`Run ${run.run_no}`} style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <div className="lbl">{run.run_no}{run.date !== isoDay(new Date()) ? ` · from ${dayLabel(new Date(`${run.date}T00:00`))}` : ""}</div>
            <div style={{ fontSize: 24, fontWeight: 800 }}>{plural(run.stops, "stop")} · {plural(run.packages, "package")}</div>
            <div style={{ fontSize: 14, color: "var(--color-ink-2)" }}>{state(run)}</div>
          </section>
        )}
        {runs?.length === 0 && (
          <section className="card"><b>No run for you today.</b>
            <div style={{ fontSize: 14, color: "var(--color-ink-2)", marginTop: 4 }}>Check with the office, then look again.</div></section>
        )}
        {error && <div className="msg r" role="alert">{error}</div>}
        {error && savedRun && savedRun.status !== "done" && (
          <Link to={`/run/${savedRun.id}`} className="big ghost">Carry on with {savedRun.run_no} from memory</Link>
        )}
        {run?.status === "planned" && (
          <div style={{ display: "flex", gap: 8, fontSize: 12, color: "var(--color-muted)" }}>
            <Icon name="pin" size={16} width={2.2} />
            <span>Starting asks to use this phone's location. It is kept with each drop, and only while you're on a run.</span>
          </div>
        )}
      </div>
      {!run && (
        <button type="button" className="big ghost" style={{ flex: "none" }} onClick={() => void load()}>Look again</button>
      )}
      {run && run.status !== "done" && (
        <button type="button" className="big primary" style={{ flex: "none" }} disabled={busy} onClick={() => void go(run)}>
          <Icon name="truck" size={22} />{run.status === "planned" ? "Start run" : "Carry on"}</button>
      )}
      {queue.failed().length > 0 && (
        <div className="small" style={{ color: "var(--color-red-ink)" }}>
          {plural(queue.failed().length, "confirmation")} wasn't taken. Open the run to see why.</div>
      )}
    </Phone>
  );
}
```

`apps/driver/src/pages/RunHome.tsx`:
```tsx
import { useEffect, useState } from "react";
import { Link, Navigate, useLocation, useParams } from "react-router-dom";
import { api } from "../api/client";
import type { RunStops } from "../api/types";
import { useQueueItems, useSession } from "../auth/Session";
import { addressLine, mapsLink } from "../lib/maps";
import { finished, loadRun, nextStop, outcomes, plural, saveRun } from "../lib/run";
import { Icon } from "../ui/icons";
import { Goal, OfflineBanner, Phone, RunTop, WaitingList } from "../ui/kit";

/** Between stops (DriverOffline.html): the next stop, what is waiting to
 * send, and the run's end. Works from the phone's copy of the run; reloads
 * it from the office when there is signal. */
export function RunHome() {
  const runId = Number(useParams().runId);
  const { online, waiting, queue } = useSession();
  const items = useQueueItems(runId);
  const note = (useLocation().state as { note?: string } | null)?.note;
  const [run, setRun] = useState<RunStops | null>(() => loadRun(runId));

  useEffect(() => {
    if (!online) return;
    api.get<RunStops>(`/v1/runs/${runId}/stops`)
      .then((fresh) => { saveRun(fresh); setRun(fresh); })
      .catch(() => undefined);   // the phone's copy will do
  }, [runId, online]);

  if (!run) return <Navigate to="/" replace />;
  const done = outcomes(run, items);
  const next = nextStop(run, done);
  const over = finished(run, items);
  const delivered = [...done.values()].filter((o) => o === "delivered").length;
  const back = done.size - delivered;

  function finish() {
    void queue.submit({ path: `/v1/runs/${runId}/finish`, body: {}, label: `${run!.run_no} · finished`,
                        run_id: runId, consignment_id: null, outcome: "finished" });
  }

  return (
    <Phone label={`Run ${run.run_no}`}>
      <RunTop run={run} />
      <OfflineBanner online={online} waiting={waiting} />
      <Goal run={run} done={done} current={next?.consignment_id} />
      <div className="scroll">
        {note && <div className="msg a" role="status">{note}</div>}
        {next && (
          <section aria-label="Next stop" className="card" style={{ padding: 16, display: "flex", flexDirection: "column", gap: 8 }}>
            <div className="lbl">Stop {next.sequence} of {run.stops.length}</div>
            <div style={{ fontSize: 26, fontWeight: 800, letterSpacing: -0.4, lineHeight: 1.1 }}>{next.customer}</div>
            <div style={{ fontSize: 14, color: "var(--color-ink-2)" }}>
              {[addressLine(next.ship_to), plural(next.packages.length, "carton")].filter(Boolean).join(" · ")}</div>
            <a href={mapsLink(next.ship_to)} target="_blank" rel="noopener noreferrer" className="big ghost" style={{ height: 48, fontSize: 15 }}>
              <Icon name="map" />Open in Maps</a>
          </section>
        )}
        {!next && !over && (
          <section className="card" aria-label="Every stop done">
            <b style={{ fontSize: 20 }}>Every stop is done.</b>
            <div style={{ fontSize: 14, color: "var(--color-ink-2)", marginTop: 4 }}>
              {delivered} delivered{back ? `, ${back} coming back to the depot` : ""}. Finish the run when you're back.</div>
          </section>
        )}
        {over && (
          <section className="card" aria-label="Run finished">
            <b style={{ fontSize: 20 }}>{run.run_no} is finished.</b>
            <div style={{ fontSize: 14, color: "var(--color-ink-2)", marginTop: 4 }}>
              {delivered} delivered{back ? `, ${back} back at the depot` : ""}. Thanks, {run.driver.name}.</div>
          </section>
        )}
        <WaitingList items={items} />
        <div style={{ display: "flex", gap: 8, alignItems: "flex-start", fontSize: 12, color: "var(--color-muted)" }}>
          <Icon name="info" size={16} width={2.2} />
          <span>Your whole run is saved on this phone, so keep going. Each confirmation has its own number, so sending it twice never records it twice.</span>
        </div>
      </div>
      <div style={{ display: "flex", gap: 10, flex: "none" }}>
        {waiting > 0 && (
          <button type="button" className="big ghost" style={{ width: 130 }} onClick={() => void queue.drain()}>Retry now</button>)}
        {next && (
          <Link to={`/run/${runId}/stop/${next.consignment_id}`} className="big primary" style={{ flex: 1 }}>
            Start stop {next.sequence}<Icon name="go" size={20} width={2.6} /></Link>)}
        {!next && !over && (
          <button type="button" className="big primary" style={{ flex: 1 }} onClick={finish}>Finish the run</button>)}
        {over && <Link to="/" className="big ghost" style={{ flex: 1 }}>Back to today's runs</Link>}
      </div>
    </Phone>
  );
}
```

`apps/driver/src/App.tsx` (the stop screens join it in Tasks 15 and 16):
```tsx
import { BrowserRouter, Navigate, Outlet, Route, Routes } from "react-router-dom";
import { SessionProvider, useSession } from "./auth/Session";
import { RunHome } from "./pages/RunHome";
import { SignIn } from "./pages/SignIn";
import { Today } from "./pages/Today";

function RequireDriver() {
  const { driver } = useSession();
  return driver ? <Outlet /> : <Navigate to="/sign-in" replace />;
}

/** One thing at a time: today's run, then each stop in turn. */
export function AppRoutes() {
  return (
    <Routes>
      <Route path="/sign-in" element={<SignIn />} />
      <Route element={<RequireDriver />}>
        <Route index element={<Today />} />
        <Route path="/run/:runId" element={<RunHome />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

export function App() {
  return (
    <BrowserRouter basename="/driver">
      <SessionProvider>
        <AppRoutes />
      </SessionProvider>
    </BrowserRouter>
  );
}
```

`apps/driver/src/main.tsx`. The service worker (Task 17) is registered only in a build: in `npm run dev` it would serve stale modules.
```tsx
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "@fontsource/manrope/latin-400.css";
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

// the app shell is kept on the phone, so it opens in a black spot (public/sw.js)
if ("serviceWorker" in navigator && import.meta.env.PROD) {
  void navigator.serviceWorker.register("/driver/sw.js", { scope: "/driver/" });
}
```

**Step 6: Run the tests and build**

Run: `npm test && npm run build`
Expected: `33 passed`; then `tsc` silent and `✓ built`, with `dist/index.html`, `dist/assets/index-….js` and `.css` and the Manrope fonts.

**Step 7: Commit**

```bash
git add apps/driver/src
git commit -m "Sign in, start today's run and see the next stop, signal or not"
```

### Task 15: A stop: the address, Open in Maps, and each carton scanned or ticked

**Files:**
- Create: `apps/driver/src/pages/Stop.tsx` (`DriverStop.html`)
- Modify: `apps/driver/src/App.tsx`
- Test: `apps/driver/src/test/Stop.test.tsx`

**Step 1: Write the failing test**

`apps/driver/src/test/Stop.test.tsx`:
```tsx
import { fireEvent, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { onThePhone, renderApp, signedIn, stubApi } from "./harness";

describe("a stop", () => {
  beforeEach(() => { signedIn(); onThePhone(); stubApi(); });
  afterEach(() => vi.unstubAllGlobals());

  it("shows the address, a maps link and the cartons to drop", async () => {
    renderApp("/run/7/stop/31");
    expect(await screen.findByText("Hillside Bakery")).toBeInTheDocument();
    expect(screen.getByText("12 Sturt St, Ballarat VIC 3350")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open in Maps" })).toHaveAttribute(
      "href", "https://www.google.com/maps/search/?api=1&query=12%20Sturt%20St%2C%20Ballarat%20VIC%203350%2C%20Australia");
    expect(screen.getByRole("checkbox", { name: "008003451 · 1 of 2" })).toHaveAttribute("aria-checked", "false");
    expect(screen.getByRole("link", { name: "Call 03 5300 0000" })).toHaveAttribute("href", "tel:0353000000");
    expect(screen.getByRole("button", { name: "Delivered" })).toBeDisabled();
  });

  it("is delivered only once every carton is scanned or ticked", async () => {
    renderApp("/run/7/stop/31");
    const user = userEvent.setup();
    // a wedge scanner types into the scan field and presses Enter
    const field = await screen.findByLabelText("Carton label");
    await user.type(field, "(00)093123450000000101{Enter}");
    expect(await screen.findByText("Carton 1 of 2 scanned.")).toBeInTheDocument();
    expect(screen.getByText("Scanned")).toBeInTheDocument();
    await user.click(screen.getByRole("checkbox", { name: "008003451 · 2 of 2" }));
    expect(screen.getByText("Ticked")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Delivered" })).toHaveAttribute("href", "/run/7/stop/31/proof");
  });

  it("catches a carton for another stop", async () => {
    renderApp("/run/7/stop/31");
    const field = await screen.findByLabelText("Carton label");
    fireEvent.change(field, { target: { value: "093123450000000201" } });
    fireEvent.keyDown(field, { key: "Enter" });
    expect(await screen.findByText("That carton is for stop 2 · Two Rivers Vet. It stays on the truck."))
      .toBeInTheDocument();
  });
});
```

**Step 2: Run it to see it fail**

Run: `npm test -- Stop`
Expected: 3 failed: there is no such route yet, so the app goes back to today's run.

**Step 3: Implement**

`apps/driver/src/pages/Stop.tsx`. The ticks are kept per stop in `localStorage`, so they survive going to the proof screen and back, and a reload. A keyboard-wedge scan (or a code typed into the scan field and Enter) is matched against the whole run, so a carton for another stop is caught. The design's delivery note becomes the contact and a phone link: Simple WMS has no delivery instructions to show.
```tsx
import { useState } from "react";
import { Link, Navigate, useParams } from "react-router-dom";
import { useQueueItems } from "../auth/Session";
import { addressLine, mapsLink } from "../lib/maps";
import { loadRun, loadTicks, outcomes, saveTicks, stopOf, type How, type Ticks } from "../lib/run";
import { matchScan } from "../lib/scan";
import { useScanWedge } from "../lib/useScanWedge";
import { useSession } from "../auth/Session";
import { Icon } from "../ui/icons";
import { Goal, OfflineBanner, Phone, RunTop } from "../ui/kit";

type Said = { tone: "g" | "a" | "r"; text: string } | null;

/** DriverStop.html: one stop. Each carton is scanned or ticked off before
 * it can be delivered. */
export function Stop() {
  const params = useParams();
  const runId = Number(params.runId);
  const consignmentId = Number(params.consignmentId);
  const { online, waiting } = useSession();
  const items = useQueueItems(runId);
  const run = loadRun(runId);
  const stop = run ? stopOf(run, consignmentId) : null;
  const [ticks, setTicks] = useState<Ticks>(() => loadTicks(runId, consignmentId));
  const [said, setSaid] = useState<Said>(null);

  function mark(packageNo: number, how: How | null) {
    setTicks((t) => {
      const next = { ...t };
      if (how) next[packageNo] = how; else delete next[packageNo];
      saveTicks(runId, consignmentId, next);
      return next;
    });
  }

  useScanWedge((code) => {
    if (!run || !stop) return;
    const m = matchScan(code, run.stops, stop);
    if (m.kind === "here") {
      mark(m.package_no, "scan");
      setSaid({ tone: "g", text: `Carton ${m.package_no} of ${stop.packages.length} scanned.` });
    } else if (m.kind === "other") {
      setSaid({ tone: "a", text: `That carton is for stop ${m.stop.sequence} · ${m.stop.customer}. It stays on the truck.` });
    } else {
      setSaid({ tone: "r", text: "That label isn't on this run." });
    }
  });

  if (!run || !stop) return <Navigate to={run ? `/run/${runId}` : "/"} replace />;
  const done = outcomes(run, items);
  if (done.has(consignmentId)) return <Navigate to={`/run/${runId}`} replace />;
  const count = stop.packages.filter((p) => ticks[p.package_no]).length;
  const all = count === stop.packages.length;
  const t = stop.ship_to;

  return (
    <Phone label={`Stop ${stop.sequence} of ${run.stops.length}`}>
      <RunTop run={run} />
      <OfflineBanner online={online} waiting={waiting} />
      <Goal run={run} done={done} current={consignmentId} />
      <div className="scroll">
        <section aria-label="This stop" style={{ padding: "14px 16px", borderRadius: 18, background: "var(--color-green)", color: "#fff",
                                                 display: "flex", flexDirection: "column", gap: 6 }}>
          <span style={{ fontSize: 13, fontWeight: 800, letterSpacing: 0.6, color: "#D5F0E1" }}>
            STOP {stop.sequence} OF {run.stops.length}</span>
          <span style={{ fontSize: 28, fontWeight: 800, letterSpacing: -0.4, lineHeight: 1.1 }}>{stop.customer}</span>
          <span style={{ fontSize: 14, color: "#E7F4EC" }}>{addressLine(t) || "No address given"}</span>
          <a href={mapsLink(t)} target="_blank" rel="noopener noreferrer" className="big white" style={{ marginTop: 6, height: 50 }}>
            <Icon name="map" />Open in Maps</a>
        </section>
        <section aria-label="Cartons to drop" style={{ borderRadius: 18, background: "#fff", border: "1px solid var(--color-line)", overflow: "hidden" }}>
          <div style={{ display: "flex", justifyContent: "space-between", padding: "10px 12px 6px" }}>
            <span className="lbl">Cartons to drop</span>
            <span style={{ fontSize: 13, fontWeight: 800, color: "var(--color-green)" }}>{count} of {stop.packages.length}</span>
          </div>
          {stop.packages.map((p, i) => {
            const how = ticks[p.package_no];
            return (
              <div key={p.package_no} role="checkbox" aria-checked={!!how} tabIndex={0} className="carton"
                   aria-label={`${stop.wms_ref} · ${i + 1} of ${stop.packages.length}`}
                   onClick={() => mark(p.package_no, how ? null : "tick")}
                   onKeyDown={(e) => { if (e.key === " ") { e.preventDefault(); mark(p.package_no, how ? null : "tick"); } }}>
                <span className="tick"><Icon name="check" size={16} width={3} /></span>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 15, fontWeight: 800 }}>{stop.wms_ref} · {i + 1} of {stop.packages.length}</div>
                  <div className="small">
                    {[p.type.charAt(0).toUpperCase() + p.type.slice(1), p.weight_kg ? `${p.weight_kg} kg` : null]
                      .filter(Boolean).join(" · ")}</div>
                </div>
                {how === "scan" && <span className="pill p-green">Scanned</span>}
                {how === "tick" && <span className="pill p-grey">Ticked</span>}
              </div>
            );
          })}
        </section>
        <label className="scanbox">
          <span style={{ color: "var(--color-green)" }}><Icon name="scan" size={26} /></span>
          <span style={{ flex: 1 }}><b>Scan a carton label</b><small>Or tap a carton to tick it off.</small>
            <input data-scan="true" aria-label="Carton label" placeholder="or type it and press Enter" inputMode="text"
                   autoCapitalize="none" autoComplete="off" /></span>
        </label>
        {said && <div className={`msg ${said.tone}`} role="status">{said.text}</div>}
        {(t.contact || t.phone) && (
          <section aria-label="Delivery note" className="card" style={{ padding: "12px 14px", display: "flex", gap: 10 }}>
            <span style={{ color: "var(--color-ink-2)", flex: "none" }}><Icon name="note" /></span>
            <div style={{ fontSize: 14 }}><b>{t.contact ? `Ask for ${t.contact}` : `For ${stop.customer}`}</b>
              {t.phone && <div><a href={`tel:${t.phone.replace(/\s/g, "")}`}>Call {t.phone}</a></div>}</div>
          </section>
        )}
      </div>
      {!all && <div className="small" style={{ textAlign: "center", flex: "none" }}>Scan or tick every carton to deliver.</div>}
      <div style={{ display: "flex", gap: 8, flex: "none" }}>
        <Link to={`/run/${runId}/stop/${consignmentId}/cant`} className="big ghost" style={{ flex: 1 }}>Can't deliver</Link>
        {all
          ? <Link to={`/run/${runId}/stop/${consignmentId}/proof`} className="big primary" style={{ flex: 2 }}>
              <Icon name="check" size={22} width={2.6} />Delivered</Link>
          : <button type="button" className="big primary" style={{ flex: 2 }} disabled>
              <Icon name="check" size={22} width={2.6} />Delivered</button>}
      </div>
    </Phone>
  );
}
```

`apps/driver/src/App.tsx`: import `Stop` (`import { Stop } from "./pages/Stop";`) and add, after the `/run/:runId` route:
```tsx
        <Route path="/run/:runId/stop/:consignmentId" element={<Stop />} />
```

**Step 4: Run the tests**

Run: `npm test && npm run typecheck`
Expected: `36 passed`.

**Step 5: Commit**

```bash
git add apps/driver/src
git commit -m "Show a stop with its cartons, scanned or ticked off, and a maps link"
```

### Task 16: Proof of delivery, and can't deliver

**Files:**
- Create: `apps/driver/src/ui/pictures.tsx` (the photo picker and the signature pad)
- Create: `apps/driver/src/pages/Proof.tsx` (`DriverProof.html`), `apps/driver/src/pages/CantDeliver.tsx` (`DriverCantDeliver.html`)
- Modify: `apps/driver/src/App.tsx`
- Test: `apps/driver/src/test/Proof.test.tsx`, `apps/driver/src/test/CantDeliver.test.tsx`

**Step 1: Write the failing tests**

`apps/driver/src/test/Proof.test.tsx` (jsdom can't draw, so the picture helpers are stand-ins and the signature path is left to the manual check):
```tsx
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { saveTicks } from "../lib/run";
import { json, onThePhone, renderApp, signedIn, stubApi } from "./harness";

vi.mock("../lib/picture", () => ({
  shrinkPhoto: async () => new Blob([new Uint8Array([0xff, 0xd8, 0xff])], { type: "image/jpeg" }),
  toBlob: async () => new Blob([new Uint8Array([0x89, 0x50])], { type: "image/png" }),
}));

describe("proof of delivery", () => {
  beforeEach(() => {
    signedIn();
    onThePhone();
    saveTicks(7, 31, { 1: "scan", 2: "tick" });
    URL.createObjectURL = vi.fn(() => "blob:photo");
    URL.revokeObjectURL = vi.fn();
  });
  afterEach(() => vi.unstubAllGlobals());

  it("sends the photo, then the delivery, and says Ding!", async () => {
    const { calls } = stubApi((method, url) => {
      if (method === "PUT") return json(201, {});
      if (url === "/v1/runs/7/stops/31/deliver") return json(200, { outcome: "delivered" });
      return json(200, { ...{}, id: 7 });
    });
    renderApp("/run/7/stop/31/proof");
    const user = userEvent.setup();
    expect(await screen.findByRole("button", { name: "Confirm delivery" })).toBeDisabled();
    await user.upload(screen.getByLabelText(/Add a photo/), new File(["x"], "door.jpg", { type: "image/jpeg" }));
    expect(await screen.findByRole("img", { name: "The photo taken" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Confirm delivery" }));

    const ding = await screen.findByRole("status", { name: "Delivered" });
    expect(ding).toHaveTextContent("Ding!");
    expect(ding).toHaveTextContent("Stop 1 delivered · 1 of 2 done");
    expect(ding).toHaveTextContent("2 cartons · photo taken");
    expect(screen.getByRole("link", { name: /Next: stop 2 · Two Rivers Vet/ })).toHaveAttribute("href", "/run/7/stop/32");

    await waitFor(() => expect(calls.some((c) => c.url === "/v1/runs/7/stops/31/deliver")).toBe(true));
    const put = calls.find((c) => c.method === "PUT")!;
    expect(put.url).toMatch(/^\/v1\/pod-files\/[0-9a-f-]{36}\?kind=photo$/);
    expect(put.contentType).toBe("image/jpeg");
    const deliver = calls.find((c) => c.url === "/v1/runs/7/stops/31/deliver")!.body as Record<string, unknown>;
    expect(deliver).toEqual({
      message_id: expect.any(String), at: expect.any(String), photo_file: put.url.split("/")[3].split("?")[0],
      packages: [{ package_no: 1, how: "scan" }, { package_no: 2, how: "tick" }],
    });
  });

  it("goes back to the stop until every carton is ticked", async () => {
    saveTicks(7, 31, { 1: "tick" });
    stubApi();
    renderApp("/run/7/stop/31/proof");
    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent(/^\/run\/7\/stop\/31$/));
  });
});
```

`apps/driver/src/test/CantDeliver.test.tsx`:
```tsx
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { json, onThePhone, renderApp, RUN, signedIn, stubApi } from "./harness";

describe("can't deliver", () => {
  beforeEach(() => { signedIn(); onThePhone(); });
  afterEach(() => vi.unstubAllGlobals());

  it("sends the reason and puts the cartons back on the truck", async () => {
    const { calls } = stubApi((method) => (method === "POST" ? json(200, { outcome: "failed" }) : json(200, RUN)));
    renderApp("/run/7/stop/32/cant");
    const user = userEvent.setup();
    expect(await screen.findByText(/2 cartons stay on the truck/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Back on the truck/ })).toBeDisabled();
    await user.click(screen.getByRole("radio", { name: /Closed/ }));
    await user.type(screen.getByLabelText(/Note/), "Rang the number, no answer.");
    await user.click(screen.getByRole("button", { name: /Back on the truck/ }));
    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent(/^\/run\/7$/));
    expect(await screen.findByText("Stop 2 is coming back to the depot.")).toBeInTheDocument();
    await waitFor(() => expect(calls.some((c) => c.url === "/v1/runs/7/stops/32/fail")).toBe(true));
    expect(calls.find((c) => c.url === "/v1/runs/7/stops/32/fail")!.body).toEqual({
      message_id: expect.any(String), at: expect.any(String), reason: "closed", note: "Rang the number, no answer." });
  });
});
```

**Step 2: Run them to see them fail**

Run: `npm test -- Proof CantDeliver`
Expected: 3 failed (no such routes).

**Step 3: The photo and the signature**

`apps/driver/src/ui/pictures.tsx`. The photo comes from the phone's own camera through `<input type="file" accept="image/*" capture="environment">` (no camera permission, no library) and is made small at once; the signature is drawn with pointer events on a canvas as large as the screen's pixels, on white, so the PNG reads anywhere.
```tsx
/** Taking a photo with the phone's camera, and signing on the screen. */
import { useEffect, useRef, useState, type RefObject } from "react";
import { shrinkPhoto } from "../lib/picture";
import { Icon } from "./icons";

/** "Add a photo": the phone's own camera through a file input, made small
 * on the phone before it is kept. */
export function PhotoPicker({ photo, onPhoto, hint }: { photo: Blob | null; onPhoto: (b: Blob | null) => void; hint: string }) {
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!photo) { setUrl(null); return; }
    const u = URL.createObjectURL(photo);
    setUrl(u);
    return () => URL.revokeObjectURL(u);
  }, [photo]);

  async function picked(file: File | undefined) {
    if (!file) return;
    setError(null);
    try {
      onPhoto(await shrinkPhoto(file));
    } catch {
      setError("That photo couldn't be read. Take it again.");
    }
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <div style={{ display: "flex", gap: 8 }}>
        {url && <img src={url} alt="The photo taken" style={{ width: 76, height: 66, borderRadius: 12, objectFit: "cover", flex: "none" }} />}
        <label className="scanbox quiet">
          <Icon name="camera" size={26} />
          <span><b>{photo ? "Retake photo" : "Add a photo"}</b><small>{photo ? `${hint} · 1 added` : hint}</small></span>
          <input type="file" accept="image/*" capture="environment" className="sr"
                 onChange={(e) => { void picked(e.target.files?.[0]); e.target.value = ""; }} />
        </label>
      </div>
      {error && <div className="msg r" role="alert">{error}</div>}
    </div>
  );
}

/** SIGN HERE: a canvas the customer signs with a finger. `signed` says
 * whether anything has been drawn. */
export function SignaturePad({ canvas, onSigned }: { canvas: RefObject<HTMLCanvasElement | null>; onSigned: (signed: boolean) => void }) {
  const drawing = useRef(false);

  function context(): CanvasRenderingContext2D | null {
    return canvas.current?.getContext("2d") ?? null;
  }

  function clear() {
    const el = canvas.current;
    const ctx = context();
    if (el && ctx) {
      ctx.fillStyle = "#fff";
      ctx.fillRect(0, 0, el.width, el.height);
    }
    onSigned(false);
  }

  useEffect(() => {
    // as many pixels as the screen has, so the signature isn't blurred
    const el = canvas.current;
    if (!el) return;
    const ratio = window.devicePixelRatio || 1;
    el.width = Math.round(el.clientWidth * ratio) || 600;
    el.height = Math.round(el.clientHeight * ratio) || 300;
    const ctx = context();
    if (ctx) {
      ctx.scale(ratio, ratio);
      ctx.lineWidth = 3;
      ctx.lineCap = "round";
      ctx.lineJoin = "round";
      ctx.strokeStyle = "#17251F";
    }
    clear();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function at(e: React.PointerEvent<HTMLCanvasElement>) {
    const r = e.currentTarget.getBoundingClientRect();
    return [e.clientX - r.left, e.clientY - r.top] as const;
  }

  return (
    <section aria-label="Signature" style={{ borderRadius: 18, background: "#fff", border: "2px solid var(--color-line-2)",
                                             padding: "10px 12px", display: "flex", flexDirection: "column", gap: 4 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <span className="lbl">Sign here</span>
        <button type="button" onClick={clear}
                style={{ border: 0, background: "transparent", color: "var(--color-green-dark)", fontWeight: 800, fontSize: 13, cursor: "pointer" }}>
          Clear</button>
      </div>
      <canvas ref={canvas} className="pad" aria-label="Signature pad: sign with a finger"
              onPointerDown={(e) => {
                const ctx = context();
                if (!ctx) return;
                e.currentTarget.setPointerCapture?.(e.pointerId);
                drawing.current = true;
                const [x, y] = at(e);
                ctx.beginPath();
                ctx.moveTo(x, y);
              }}
              onPointerMove={(e) => {
                const ctx = context();
                if (!drawing.current || !ctx) return;
                const [x, y] = at(e);
                ctx.lineTo(x, y);
                ctx.stroke();
                onSigned(true);
              }}
              onPointerUp={() => { drawing.current = false; }}
              onPointerCancel={() => { drawing.current = false; }} />
    </section>
  );
}
```

**Step 4: The screens**

`apps/driver/src/pages/Proof.tsx`. The ticks are read once when the screen opens: the delivery clears them, and the "Ding!" panel must not then send the driver back to tick them again.
```tsx
import { useRef, useState } from "react";
import { Link, Navigate, useParams } from "react-router-dom";
import { useQueueItems, useSession } from "../auth/Session";
import { toBlob } from "../lib/picture";
import type { PictureKind } from "../lib/queue";
import { loadRun, loadTicks, nextStop, outcomes, plural, saveTicks, stopOf } from "../lib/run";
import { clock } from "../lib/time";
import { getPosition } from "../lib/where";
import { Icon } from "../ui/icons";
import { BackTop, Phone } from "../ui/kit";
import { PhotoPicker, SignaturePad } from "../ui/pictures";

interface Dinged { sequence: number; doneCount: number; cartons: number; by: string; at: Date }

/** DriverProof.html: a signature with the printed name, a photo, or both;
 * then "Ding!" and on to the next stop. */
export function Proof() {
  const params = useParams();
  const runId = Number(params.runId);
  const consignmentId = Number(params.consignmentId);
  const { queue } = useSession();
  const items = useQueueItems(runId);
  const run = loadRun(runId);
  const stop = run ? stopOf(run, consignmentId) : null;
  const canvas = useRef<HTMLCanvasElement | null>(null);
  const [signed, setSigned] = useState(false);
  const [name, setName] = useState("");
  const [photo, setPhoto] = useState<Blob | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [ding, setDing] = useState<Dinged | null>(null);
  // as the stop page left them: cleared on the phone once the delivery is kept
  const [ticks] = useState(() => loadTicks(runId, consignmentId));

  if (!run || !stop) return <Navigate to="/" replace />;
  const back = `/run/${runId}/stop/${consignmentId}`;
  if (!ding && stop.packages.some((p) => !ticks[p.package_no])) return <Navigate to={back} replace />;
  const ready = (signed && name.trim().length > 0) || photo !== null;

  async function confirm() {
    if (!run || !stop) return;
    setBusy(true);
    setError(null);
    try {
      const pictures: { kind: PictureKind; field: string; blob: Blob }[] = [];
      const withSignature = signed && name.trim().length > 0 && canvas.current;
      if (withSignature) pictures.push({ kind: "signature", field: "signature_file", blob: await toBlob(canvas.current!, "image/png") });
      if (photo) pictures.push({ kind: "photo", field: "photo_file", blob: photo });
      const position = await getPosition();
      const at = new Date();
      await queue.submit({
        path: `/v1/runs/${runId}/stops/${consignmentId}/deliver`,
        body: {
          at: at.toISOString(),
          packages: stop.packages.map((p) => ({ package_no: p.package_no, how: ticks[p.package_no] })),
          ...(withSignature ? { signed_name: name.trim() } : {}),
          ...(position ? { position } : {}),
        },
        label: `Stop ${stop.sequence} · delivered · ${plural(stop.packages.length, "carton")}`,
        run_id: runId, consignment_id: consignmentId, outcome: "delivered", pictures,
      });
      saveTicks(runId, consignmentId, {});
      const done = outcomes(run, queue.forRun(runId));
      setDing({ sequence: stop.sequence, doneCount: done.size, cartons: stop.packages.length,
                by: withSignature ? `signed by ${name.trim()}` : "photo taken", at });
    } catch (e) {
      setError(e instanceof Error ? e.message : "That couldn't be kept on the phone. Try again.");
    } finally {
      setBusy(false);
    }
  }

  const next = ding ? nextStop(run, outcomes(run, items)) : null;
  return (
    <Phone label="Proof of delivery">
      <BackTop to={back} label="Back to the stop" title="Proof of delivery"
               sub={`Stop ${stop.sequence} of ${run.stops.length} · ${stop.customer}`} />
      <div className="scroll" style={{ paddingBottom: ding ? 230 : 0 }}>
        <div style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 13, color: "var(--color-ink-2)" }}>
          <Icon name="info" size={16} />A signature and printed name, or a photo of where you left it if nobody is there to sign.</div>
        <SignaturePad canvas={canvas} onSigned={setSigned} />
        <label style={{ display: "flex", flexDirection: "column", gap: 6, fontSize: 14, fontWeight: 800 }}>Printed name
          <input className="input" type="text" autoComplete="off" maxLength={120} value={name}
                 onChange={(e) => setName(e.target.value)} /></label>
        <PhotoPicker photo={photo} onPhoto={setPhoto} hint={signed ? "Optional" : "Needed if there's no signature"} />
        {error && <div className="msg r" role="alert">{error}</div>}
      </div>
      {!ding && (
        <button type="button" className="big primary" style={{ flex: "none" }} disabled={!ready || busy} onClick={() => void confirm()}>
          <Icon name="check" size={22} width={2.6} />Confirm delivery</button>
      )}
      {ding && (
        <section role="status" aria-label="Delivered" className="ding">
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <span style={{ width: 52, height: 52, borderRadius: "50%", background: "#fff", color: "var(--color-green)",
                           display: "grid", placeItems: "center", flex: "none" }}><Icon name="check" size={30} width={3} /></span>
            <div><div style={{ fontSize: 30, fontWeight: 800, letterSpacing: -0.4, lineHeight: 1 }}>Ding!</div>
              <div style={{ fontSize: 15, fontWeight: 700, color: "#E7F4EC" }}>
                Stop {ding.sequence} delivered · {ding.doneCount} of {run.stops.length} done</div></div>
          </div>
          <div style={{ fontSize: 13, color: "#D5F0E1" }}>{plural(ding.cartons, "carton")} · {ding.by} · {clock(ding.at)}</div>
          {next
            ? <Link to={`/run/${runId}/stop/${next.consignment_id}`} className="big white">
                Next: stop {next.sequence} · {next.customer}<Icon name="go" size={20} width={2.6} /></Link>
            : <Link to={`/run/${runId}`} className="big white">Back to the run<Icon name="go" size={20} width={2.6} /></Link>}
        </section>
      )}
    </Phone>
  );
}
```

`apps/driver/src/pages/CantDeliver.tsx`:
```tsx
import { useState } from "react";
import { Navigate, useNavigate, useParams } from "react-router-dom";
import { useSession } from "../auth/Session";
import { loadRun, plural, saveTicks, stopOf } from "../lib/run";
import { getPosition } from "../lib/where";
import { Icon } from "../ui/icons";
import { BackTop, Phone } from "../ui/kit";
import { PhotoPicker } from "../ui/pictures";

const REASONS = [
  { value: "closed", label: "Closed", hint: "Nobody there", icon: "closed" },
  { value: "refused", label: "Refused", hint: "Customer said no", icon: "refused" },
  { value: "damaged", label: "Damaged", hint: "Carton not fit to leave", icon: "damaged" },
  { value: "wrong_address", label: "Wrong address", hint: "Can't find it", icon: "wrong" },
] as const;

/** DriverCantDeliver.html: why, a photo if it helps, and the cartons go
 * back on the truck. */
export function CantDeliver() {
  const params = useParams();
  const runId = Number(params.runId);
  const consignmentId = Number(params.consignmentId);
  const { queue, online } = useSession();
  const navigate = useNavigate();
  const run = loadRun(runId);
  const stop = run ? stopOf(run, consignmentId) : null;
  const [reason, setReason] = useState<string | null>(null);
  const [photo, setPhoto] = useState<Blob | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!run || !stop) return <Navigate to="/" replace />;

  async function confirm() {
    if (!run || !stop || !reason) return;
    setBusy(true);
    setError(null);
    try {
      const position = await getPosition();
      await queue.submit({
        path: `/v1/runs/${runId}/stops/${consignmentId}/fail`,
        body: { at: new Date().toISOString(), reason, ...(note.trim() ? { note: note.trim() } : {}),
                ...(position ? { position } : {}) },
        label: `Stop ${stop.sequence} · couldn't deliver · ${REASONS.find((r) => r.value === reason)!.label.toLowerCase()}`,
        run_id: runId, consignment_id: consignmentId, outcome: "failed",
        pictures: photo ? [{ kind: "photo", field: "photo_file", blob: photo }] : [],
      });
      saveTicks(runId, consignmentId, {});
      navigate(`/run/${runId}`, { state: { note: `Stop ${stop.sequence} is coming back to the depot.` } });
    } catch (e) {
      setError(e instanceof Error ? e.message : "That couldn't be kept on the phone. Try again.");
      setBusy(false);
    }
  }

  const where = stop.ship_to.suburb ? ` · ${stop.ship_to.suburb}` : "";
  return (
    <Phone label="Can't deliver">
      <BackTop to={`/run/${runId}/stop/${consignmentId}`} label="Back to the stop" title="Can't deliver"
               sub={`Stop ${stop.sequence} of ${run.stops.length} · ${stop.customer}${where}`} />
      <div className="scroll">
        <span style={{ fontSize: 16, fontWeight: 800 }}>What happened?</span>
        <div role="radiogroup" aria-label="Reason" style={{ display: "grid", gridTemplateColumns: "repeat(2,minmax(0,1fr))", gap: 8 }}>
          {REASONS.map((r) => (
            <button key={r.value} type="button" role="radio" aria-checked={reason === r.value} className="choice"
                    onClick={() => setReason(r.value)}>
              <span style={{ color: reason === r.value ? "var(--color-green)" : "var(--color-ink-2)" }}><Icon name={r.icon} size={22} /></span>
              <b style={{ fontSize: 15 }}>{r.label}</b><span className="small">{r.hint}</span>
            </button>
          ))}
        </div>
        <span className="lbl" style={{ marginTop: 4 }}>Photo</span>
        <PhotoPicker photo={photo} onPhoto={setPhoto} hint="Optional" />
        <label style={{ display: "flex", flexDirection: "column", gap: 6, fontSize: 14, fontWeight: 800 }}>
          <span>Note <span className="small" style={{ fontWeight: 600 }}>Optional</span></span>
          <input className="field" style={{ height: 50 }} type="text" maxLength={500} value={note} onChange={(e) => setNote(e.target.value)} />
        </label>
        <div className="card" style={{ display: "flex", gap: 10, alignItems: "flex-start", padding: "12px 14px", fontSize: 13, color: "var(--color-ink-2)" }}>
          <span style={{ flex: "none", color: "var(--color-green)" }}><Icon name="returns" /></span>
          <span>The <b style={{ color: "var(--color-ink)" }}>{plural(stop.packages.length, "carton")} stay on the truck</b> and come back to the depot.
            {online ? " The office sees it straight away." : " The office sees it once you're back in range."}</span>
        </div>
        {error && <div className="msg r" role="alert">{error}</div>}
      </div>
      <button type="button" className="big primary" style={{ flex: "none" }} disabled={!reason || busy} onClick={() => void confirm()}>
        <Icon name="truck" size={22} />Back on the truck</button>
    </Phone>
  );
}
```

`apps/driver/src/App.tsx` is now:
```tsx
import { BrowserRouter, Navigate, Outlet, Route, Routes } from "react-router-dom";
import { SessionProvider, useSession } from "./auth/Session";
import { CantDeliver } from "./pages/CantDeliver";
import { Proof } from "./pages/Proof";
import { RunHome } from "./pages/RunHome";
import { SignIn } from "./pages/SignIn";
import { Stop } from "./pages/Stop";
import { Today } from "./pages/Today";

function RequireDriver() {
  const { driver } = useSession();
  return driver ? <Outlet /> : <Navigate to="/sign-in" replace />;
}

/** One thing at a time: today's run, then each stop in turn. */
export function AppRoutes() {
  return (
    <Routes>
      <Route path="/sign-in" element={<SignIn />} />
      <Route element={<RequireDriver />}>
        <Route index element={<Today />} />
        <Route path="/run/:runId" element={<RunHome />} />
        <Route path="/run/:runId/stop/:consignmentId" element={<Stop />} />
        <Route path="/run/:runId/stop/:consignmentId/proof" element={<Proof />} />
        <Route path="/run/:runId/stop/:consignmentId/cant" element={<CantDeliver />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

export function App() {
  return (
    <BrowserRouter basename="/driver">
      <SessionProvider>
        <AppRoutes />
      </SessionProvider>
    </BrowserRouter>
  );
}
```

**Step 5: Run the tests and build**

Run: `npm test && npm run build`
Expected: `39 passed` and `✓ built`.

**Step 6: Commit**

```bash
git add apps/driver/src
git commit -m "Take proof of delivery, or the reason there wasn't one, into the queue"
```

### Task 17: The service worker: the app opens in a black spot

**Files:**
- Create: `apps/driver/public/sw.js`

**Step 1: Implement**

`apps/driver/public/sw.js`:
```js
/* The driver app's service worker: keeps the app shell on the phone so the
 * app opens in a black spot. API calls (/v1) always go to the network; the
 * app's own queue looks after confirmations, and the run is kept in the
 * app's storage. The page is fetched fresh whenever there is signal, so a
 * new build reaches the phone the next time the app is opened in range. */
const CACHE = "tms-driver-v1";
const BASE = "/driver/";
const SHELL = [BASE, `${BASE}manifest.webmanifest`, `${BASE}favicon.svg`, `${BASE}icon-192.png`, `${BASE}icon-512.png`];
const ASSET = /\/driver\/assets\/[^"'()\s]+/g;
const PAGE_WAIT_MS = 4000;

async function assetsOf(response) {
  if (!response) return [];
  return [...new Set((await response.clone().text()).match(ASSET) ?? [])];
}

/** The page's scripts and styles, and the fonts its styles name; and none of
 * an older build's. */
async function keepAssets(page) {
  const cache = await caches.open(CACHE);
  const wanted = new Set(await assetsOf(page));
  for (const css of [...wanted].filter((a) => a.endsWith(".css"))) {
    let res = await cache.match(css);
    if (!res) {
      res = await fetch(css);
      if (res.ok) await cache.put(css, res.clone());
    }
    for (const a of await assetsOf(res)) wanted.add(a);
  }
  for (const a of wanted) {
    if (!(await cache.match(a))) {
      const res = await fetch(a);
      if (res.ok) await cache.put(a, res);
    }
  }
  for (const req of await cache.keys()) {
    const path = new URL(req.url).pathname;
    if (path.startsWith(`${BASE}assets/`) && !wanted.has(path)) await cache.delete(req);
  }
}

self.addEventListener("install", (event) => {
  event.waitUntil((async () => {
    const cache = await caches.open(CACHE);
    await cache.addAll(SHELL);
    await keepAssets(await cache.match(BASE));
    await self.skipWaiting();
  })());
});

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    for (const key of await caches.keys()) if (key !== CACHE) await caches.delete(key);
    await self.clients.claim();
  })());
});

function timeout(ms) {
  return new Promise((_, reject) => setTimeout(() => reject(new Error("slow")), ms));
}

self.addEventListener("fetch", (event) => {
  const req = event.request;
  const url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== self.location.origin || !url.pathname.startsWith(BASE)) return;

  if (req.mode === "navigate") {
    // the page: fresh when there is signal (and quickly), the kept one when not
    event.respondWith((async () => {
      try {
        const res = await Promise.race([fetch(req), timeout(PAGE_WAIT_MS)]);
        if (res.ok) {
          const cache = await caches.open(CACHE);
          await cache.put(BASE, res.clone());
          event.waitUntil(keepAssets(res.clone()).catch(() => undefined));
        }
        return res;
      } catch {
        return (await caches.match(BASE)) ?? Response.error();
      }
    })());
    return;
  }

  // a build's files never change under their name: the kept copy, else fetch and keep it
  event.respondWith((async () => {
    const hit = await caches.match(req);
    if (hit) return hit;
    const res = await fetch(req);
    if (res.ok && url.pathname.startsWith(`${BASE}assets/`)) {
      const cache = await caches.open(CACHE);
      await cache.put(req, res.clone());
    }
    return res;
  })());
});
```

**Step 2: Check it in a browser**

Run: `npm run build && npm run preview` (the API on `127.0.0.1:8022` must be up for signing in).
Open `http://localhost:4175/driver/` in Chrome (localhost counts as secure, so the worker registers). DevTools → **Application** → **Service workers**: `sw.js` is *activated and is running*; **Cache storage** → `tms-driver-v1` holds `/driver/`, the manifest, the icons, `assets/index-….js`, `.css` and the eight Manrope font files. **Network** → *Offline*, reload: the sign-in screen still draws, in Manrope. Back online, change any text, `npm run build` again and reload: the new build shows (the page is fetched fresh), and the cache then holds only the new build's `assets/` files.

**Step 3: Commit**

```bash
git add apps/driver/public/sw.js
git commit -m "Keep the driver app's shell on the phone, fetching it fresh when in range"
```

### Task 18: Served by Caddy at `/driver/`, and built in CI

**Files:**
- Modify: `deploy/Caddyfile`
- Modify: `docker-compose.yml` (the `caddy` service's volumes)
- Modify: `.github/workflows/ci.yml` (a `driver` job)

**Step 1: Caddy**

In `deploy/Caddyfile`, before the desktop's `handle` (and change that block's comment to `# The desktop app (build step 2).`):
```
	# The driver app (build step 3): a PWA under /driver/. Its pictures are
	# blob: URLs of photos taken on the phone; nothing else comes from outside.
	@driverbare path /driver
	redir @driverbare /driver/ 308
	handle_path /driver/* {
		header Content-Security-Policy "default-src 'self'; img-src 'self' blob:; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
		# checked on every launch, so a new build reaches the phones
		header /sw.js Cache-Control "no-cache"
		root * /srv/driver
		try_files {path} /index.html
		file_server
	}
```

`handle_path` takes `/driver` off the path, so the build is served from `/srv/driver` as it is and any app route (`/driver/run/7/stop/31`) gets its `index.html`. The CSP is the desktop's plus `img-src blob:`, for the photo just taken; the API's `/v1` handlers come first, as before.

**Step 2: Compose**

In `docker-compose.yml`, the `caddy` service's volumes gain, after the desktop's:
```yaml
      # the driver app, built on the host: cd apps/driver && npm run build
      - ./apps/driver/dist:/srv/driver:ro
```

**Step 3: CI**

In `.github/workflows/ci.yml`, a job after `desktop`:
```yaml
  driver:
    name: Driver app
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: apps/driver
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: 22
          cache: npm
          cache-dependency-path: apps/driver/package-lock.json
      - run: npm ci
      - name: Tests
        run: npm test
      - name: Build
        run: npm run build
```

**Step 4: Serve it**

Run (repo root):
```
(cd apps/driver && npm run build)
docker compose run --rm --no-deps caddy caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
docker compose up -d caddy
curl -sk -o /dev/null -w '%{http_code} %{redirect_url}\n' https://localhost:8543/driver
curl -sk https://localhost:8543/driver/run/7/stop/31 | grep -o '<title>.*</title>'
curl -sk -D - -o /dev/null https://localhost:8543/driver/ | grep -i content-security-policy
curl -sk -D - -o /dev/null https://localhost:8543/driver/sw.js | grep -i cache-control
curl -sk https://localhost:8543/plan | grep -o '<title>.*</title>'
```
Expected: `Valid configuration`; `308 https://localhost:8543/driver/`; `<title>Simple TMS driver</title>`; the CSP with `img-src 'self' blob:`; `cache-control: no-cache`; `<title>Simple TMS</title>` (the desktop is untouched).

**Step 5: Commit**

```bash
git add deploy/Caddyfile docker-compose.yml .github/workflows/ci.yml
git commit -m "Serve the driver app at /driver/ and test and build it in CI"
```

## Part C — the dispatcher's desktop

### Task 19: The outcome on the board and the map, and finishing a run from the office

**Files:**
- Modify: `apps/desktop/src/api/types.ts` (`Run`)
- Modify: `apps/desktop/src/pages/PlanRuns.tsx`
- Modify: `apps/desktop/src/pages/MapHome.tsx:1-38`
- Test: `apps/desktop/src/test/PlanRuns.test.tsx`, `apps/desktop/src/test/MapHome.test.tsx`

All from `apps/desktop/`.

**Step 1: Write the failing tests**

Add `cleanup` to the `@testing-library/react` import of `src/test/PlanRuns.test.tsx`, and append inside `describe("Plan runs", …)`:
```tsx
  it("shows what the driver recorded, and plans again what came back", async () => {
    const out = run(1, "Kim", "TMS041", [card(11, "Hillside Bakery", "84"), card(12, "Two Rivers Vet", "41"),
                                         card(13, "Ridge Auto Workshop", "620")],
                    { status: "done", departed_at: "2026-10-14T21:10:00Z", finished_at: "2026-10-15T05:12:00Z" });
    out.stops[0].outcome = "delivered";
    out.stops[1].outcome = "failed";
    out.stops[1].reason = "wrong_address";
    out.stops[2].outcome = "delivered";
    const back = { ...packed(12, "Two Rivers Vet", "Smythesdale"), status: "failed" };
    const { calls } = board({
      "GET /v1/runs": () => json(200, { items: [out, MEL], total: 2 }),
      "GET /v1/consignments": () => json(200, { items: [back], total: 1 }),
    });
    renderApp("/plan");
    const kim = await lane("Run R-0001");
    expect(within(kim).getByText("Done")).toBeInTheDocument();
    expect(within(kim).getAllByText("Delivered")).toHaveLength(2);
    expect(within(kim).getByText("Not delivered: wrong address")).toBeInTheDocument();
    expect(within(kim).getByText(/^Delivered 2 of 3 · 1 coming back · finished /)).toBeInTheDocument();
    const tray = await lane("Consignments to plan");
    expect(within(tray).getByText("Came back: not delivered")).toBeInTheDocument();
    expect(calls.find((c) => path(c.url) === "/v1/consignments")?.url).toContain("status=packed%2Cfailed");
  });

  it("finishes a run the driver left open, once every stop is done", async () => {
    const out = run(1, "Kim", "TMS041", [card(11, "Hillside Bakery", "84"), card(12, "Two Rivers Vet", "41")],
                    { status: "departed", departed_at: "2026-10-14T21:10:00Z" });
    out.stops[0].outcome = "delivered";
    const { calls } = board({ "GET /v1/runs": () => json(200, { items: [out], total: 1 }) });
    renderApp("/plan");
    const kim = await lane("Run R-0001");
    expect(within(kim).queryByRole("button", { name: "Finish run" })).not.toBeInTheDocument();

    out.stops[1].outcome = "failed";
    cleanup();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    renderApp("/plan");
    await userEvent.setup().click(await within(await lane("Run R-0001")).findByRole("button", { name: "Finish run" }));
    await waitFor(() => expect(calls.find((c) => c.url === "/v1/runs/1/finish")?.body)
      .toEqual({ message_id: expect.stringMatching(/^[0-9a-f-]{36}$/) }));
    expect(await screen.findByText("R-0001 is finished.")).toBeInTheDocument();
    confirm.mockRestore();
  });
```

In `src/test/MapHome.test.tsx`, the first test's opening, up to the click on **Fleet**, becomes:
```tsx
  it("shows the day's numbers and opens drawers from the bar", async () => {
    const stop = (outcome: string | null) => ({ sequence: 1, consignment: {}, outcome });
    const { calls } = stubApi((_m, url) => {
      if (path(url) === "/v1/runs") {
        return json(200, { items: [{ stops: [stop("delivered"), stop("failed"), stop(null)] },
                                   { stops: [stop("delivered"), stop("delivered")] }], total: 2 });
      }
      if (path(url) !== "/v1/consignments") return null;
      const status = new URLSearchParams(url.split("?")[1]).get("status");
      return json(200, { items: [], total: { "packed,failed": 14, departed: 21 }[status ?? ""] ?? 0 });
    });
    renderApp("/");
    const nav = await screen.findByRole("navigation", { name: "Main drawers" });
    await waitFor(() => expect(nav).toHaveTextContent("Plan runs14"));
    expect(screen.getByText("21")).toBeInTheDocument();
    // delivered today: the stops the drivers have delivered on today's runs
    expect(screen.getByText("Delivered").nextSibling).toHaveTextContent("3");
    expect(calls.find((c) => path(c.url) === "/v1/runs")?.url).toMatch(/status=departed%2Cdone/);
```

**Step 2: Run them to see them fail**

Run: `npm test`
Expected: 3 failed (no "Done", no "Delivered" pills, no **Finish run**; the map still asks for `status=packed`), 51 passed.

**Step 3: Implement**

`src/api/types.ts`, in `Run`, the stops and a new `finished_at` (optional, so the existing test runs need no change):
```ts
  departed_at: string | null;
  finished_at?: string | null;
  /** outcome: what the driver recorded there, with the reason one couldn't be delivered */
  stops: { sequence: number; consignment: Card; outcome?: "delivered" | "failed" | null;
           outcome_at?: string | null; reason?: string | null }[];
```

`src/pages/PlanRuns.tsx`:
- before `function sheetLine`:
```tsx
const REASONS: Record<string, string> = {
  closed: "closed", refused: "refused", damaged: "damaged", wrong_address: "wrong address",
};

/** What the driver recorded at a stop, once the run is on the road. */
function outcomePill(s: Run["stops"][number]) {
  if (s.outcome === "delivered") return <span className="pill p-green">Delivered</span>;
  if (s.outcome === "failed") {
    return <span className="pill p-red">Not delivered{s.reason ? `: ${REASONS[s.reason] ?? s.reason}` : ""}</span>;
  }
  return null;
}

/** How a run on the road, or back, is going. */
function tripLine(run: Run): string {
  const delivered = run.stops.filter((s) => s.outcome === "delivered").length;
  const failed = run.stops.filter((s) => s.outcome === "failed").length;
  const parts = [`Delivered ${delivered} of ${run.stops.length}`];
  if (failed) parts.push(`${failed} coming back`);
  if (run.status === "done" && run.finished_at) parts.push(`finished ${clock(new Date(run.finished_at))}`);
  return parts.join(" · ") + ".";
}
```
- in `load`, the tray asks for what came back too:
```tsx
        // packed, and any the driver couldn't deliver, back to be planned again
        api.get<Page<Consignment>>("/v1/consignments", { status: "packed,failed", limit: 2000 }),
```
- in a tray card, after `<span className="small">{c.suburb}</span>`:
```tsx
                {c.status === "failed" && <div><span className="pill p-red">Came back: not delivered</span></div>}
```
- the lane's pill:
```tsx
          const pill = run.status === "done" ? <span className="pill p-grey">Done</span>
            : !open ? <span className="pill p-grey">Departed</span>
```
- in each stop, after the `<span className="c">…</span>` and before `{open && (<span className="kb">`:
```tsx
                    {!open && outcomePill(s)}
```
- `import { useAuth } from "../auth/AuthContext";`, and in `PlanRuns()` after `useOutletContext`: `const { can } = useAuth();`
- before `function depart`:
```tsx
  /** For a driver who is back but didn't finish on the phone. Every stop has
   * to be delivered or coming back first; the API checks it too. */
  function finish(run: Run) {
    if (!window.confirm(`${run.run_no} is back at the depot? It is marked finished, and nothing more can be recorded on it.`)) return;
    void act(() => api.post(`/v1/runs/${run.id}/finish`, { message_id: crypto.randomUUID() }),
             `${run.run_no} is finished.`);
  }
```
- in the lane's buttons, before the **Remove run** button:
```tsx
                {run.status === "departed" && can("runs:write") && run.stops.length > 0
                  && run.stops.every((s) => s.outcome) && (
                  <button type="button" className="btn sm primary" disabled={busy} onClick={() => finish(run)}>
                    Finish run</button>)}
```
- before the "Marked shipped in Simple WMS" line:
```tsx
              {!open && <div className="small">{tripLine(run)}</div>}
```

`src/pages/MapHome.tsx`: `import type { Page, Run } from "../api/types";`, `import { clock, isoDay } from "../lib/days";`, and `load` becomes
```tsx
  const load = useCallback(async () => {
    try {
      // to plan: packed, and what came back undelivered; delivered: today's drops
      const [toPlan, onRoad, today] = await Promise.all([
        count("packed,failed"), count("departed"),
        api.get<Page<Run>>("/v1/runs", { date: isoDay(new Date()), status: "departed,done" })]);
      const delivered = today.items.reduce(
        (n, run) => n + run.stops.filter((s) => s.outcome === "delivered").length, 0);
      setCounts({ toPlan, onRoad, delivered });
    } catch { /* the numbers wait for the next go */ }
  }, []);
```

**Step 4: Run the tests and build**

Run: `npm test && npm run build`
Expected: `54 passed` and `✓ built`.

**Step 5: Commit**

```bash
git add apps/desktop/src
git commit -m "Show what the drivers delivered and what came back, and finish a run from the office"
```

## Part D — docs and checks

### Task 20: README and `CLAUDE.md`

**Files:**
- Modify: `README.md`
- Modify: `CLAUDE.md` (the repo layout and where `npm` runs)

**Step 1: README**

- **Status** paragraph:
```markdown
Status: **build step 3, the driver app.** Drivers sign in on their phones,
start the run, deliver each stop with a signature, a photo or both (or say
why they couldn't), and finish the run, with or without signal; on top of
step 2 (runs planned on a map-and-drawer desktop, run sheets printed through
Platen, Simple WMS told each delivery shipped when its run leaves) and step 1
(sign-in, vehicles and drivers, consignments from Simple WMS's
`delivery.packed`). Fleet care, live location and carriers come with the
next steps in `docs/plans/2026-10-10-simple-tms-design.md`.
```
- **What is in this repo**: after `apps/desktop/` add `apps/driver/          the driver's phone app, a PWA at /driver/ (Vite + React)`.
- **Getting started**: after the desktop's build line add `(cd apps/driver && npm ci && npm run build)`, and after the paragraph on the desktop:
```markdown
The driver app is at `https://<TMS_DOMAIN>/driver/`. A driver signs in with
their code (in any case) and PIN (given when the driver is made, `POST /v1/drivers`, or
changed by a manager, `PATCH /v1/drivers/{code}` with a new `pin`), starts
the day's run, and works through it one stop at a time; it keeps working with
no signal and sends what waited when the signal is back. A driver who is
back but didn't finish on the phone can be finished from **Plan runs**
(**Finish run**, once every stop is delivered or coming back). Rebuild it
(`npm run build` in `apps/driver`) after pulling changes. Signatures and
photos are kept in the `pod_files` volume: back it up with the database.
```
- **Production on a real domain**: both front-Caddy snippets gain a block before their `handle {`. For the Caddy on the host:
```
  	handle_path /driver/* {
  		header Content-Security-Policy "default-src 'self'; img-src 'self' blob:; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
  		header /sw.js Cache-Control "no-cache"
  		root * /path/to/simple-tms/apps/driver/dist
  		try_files {path} /index.html
  		file_server
  	}
```
For Simple WMS's Caddy in Docker, with `root * /srv/tms-driver`, and its override's volumes gain `- /path/to/simple-tms/apps/driver/dist:/srv/tms-driver:ro`.
- A new section after **Run the desktop**:
````markdown
## Run the driver app

```
cd apps/driver
npm ci
npm run dev                     # http://localhost:5175/driver/, and on this computer's LAN address
npm run build && npm run preview   # the built app with its service worker, on 4175
```

Both send `/v1` to the API on `http://127.0.0.1:8022` (`TMS_API_URL=…` for
another). `npm test` runs its tests and `npm run typecheck` its type check.

**On a phone.** A phone's browser gives the camera to any page, but location
and the service worker (the app opening with no signal) only to a secure
one: `https://` with a certificate the phone trusts, or `http://localhost`.

- **Android:** turn on USB debugging, plug the phone in, open
  `chrome://inspect/#devices` in Chrome on the computer and under **Port
  forwarding** forward `4175` to `localhost:4175`. Run `npm run build && npm
  run preview` and open `http://localhost:4175/driver/` in Chrome on the
  phone. Everything works, black spots included (aeroplane mode).
- **iPhone:** serve it through Caddy on the computer's LAN address. Set
  `TMS_DOMAIN` to that address (say `192.168.1.20`) in `.env` and
  `docker compose up -d caddy`; copy Caddy's local root certificate out with
  `docker compose cp caddy:/data/caddy/pki/authorities/local/root.crt caddy-root.crt`,
  AirDrop it to the phone, install the profile (**Settings → Profile
  Downloaded**) and turn on full trust for it (**Settings → General → About →
  Certificate Trust Settings**). Open `https://192.168.1.20:8543/driver/`.
  Put `TMS_DOMAIN` back afterwards: the desktop answers on that one name only.
- **Any phone, just to look:** `npm run dev` and open
  `http://<the computer's LAN address>:5175/driver/`. No location and no
  offline start, but the screens, the camera and the queue all work.
````
- **Tests**: after the desktop's, add "The driver app's tests are Vitest too, with the API stubbed and IndexedDB stood in for by `fake-indexeddb`: `cd apps/driver && npm test`."

**Step 2: `CLAUDE.md`**

In the layout, `apps/desktop/        the dispatcher's desktop app; apps/driver comes with step 3` becomes two lines:
```
apps/desktop/        the dispatcher's desktop app
apps/driver/         the driver's phone app, a PWA served at /driver/
```
and the paragraph under it becomes:
```
`npm` commands run from the app's folder (`apps/desktop/` or `apps/driver/`):
`npm test`, `npm run typecheck`, `npm run build` (Caddy serves each app's
`dist`).
```

**Step 3: Commit**

```bash
git add README.md CLAUDE.md
git commit -m "Document the driver app: running it, trying it on a phone, serving it"
```

### Task 21: Every suite, and a clean Compose start

**Step 1: The suites**

Run:
```
cd "/Users/leightonlauton/Q7 Technology/Software/q7/simple-tms/api" && .venv/bin/pytest -q
cd ../apps/desktop && npm test && npm run build
cd ../driver && npm test && npm run build
```
Expected: `240 passed`; `54 passed` and a `dist/`; `39 passed` and a `dist/` with `sw.js`.

**Step 2: Compose from scratch**

Run (repo root):
```
docker compose up -d --build
docker compose ps
docker compose exec -T db psql -U tms -d tms -tAc "select version_num from alembic_version"
docker compose exec -T api sh -c 'ls -ld /data/pod'
curl -sk https://localhost:8543/driver/ | grep -o '<title>.*</title>'
```
Expected: `api` healthy, `worker`, `db` and `caddy` up; `0004`; a `/data/pod` directory (the volume); `<title>Simple TMS driver</title>`.

**Step 3: Push**

```bash
git push -u origin step-3-driver-app
```
Wait for CI (API, Desktop app, Driver app, Docker Compose comes up) to go green before the manual check's last step, then use **superpowers:finishing-a-development-branch**.

### Task 22: Manual check on the local stacks: plan, start, deliver, fail with no signal, finish

Everything runs locally: Simple TMS (`127.0.0.1:8022`, Caddy `https://localhost:8543`) on this branch, Simple WMS (`127.0.0.1:8011`). Keys and tokens below are placeholders in angle brackets; nothing here is committed.

**Step 1: The stack on this branch**

Run (simple-tms root):
```
(cd apps/desktop && npm run build) && (cd apps/driver && npm run build)
docker compose up -d --build api worker
docker compose up -d caddy
docker compose exec -T db psql -U tms -d tms -tAc "select version_num from alembic_version"
```
Expected: `0004` (the API migrated as it started; with two driver codes that differ only by case it would stop here and name them: rename one with `psql` and start it again). `docker compose up -d caddy` recreates Caddy for its new volume.

**Step 2: Kim's PIN**

Kim's PIN isn't known, so set it as the admin (a new PIN also ends any session Kim had):
```
ADMIN=$(curl -s -X POST http://127.0.0.1:8022/v1/auth/login -H 'Content-Type: application/json' \
  -d '{"username":"leighton","password":"a long enough password"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')
curl -s -X PATCH http://127.0.0.1:8022/v1/drivers/kim -H "Authorization: Bearer $ADMIN" \
  -H 'Content-Type: application/json' -d '{"pin":"2468"}'
curl -s -X POST http://127.0.0.1:8022/v1/drivers/kim/unlock -H "Authorization: Bearer $ADMIN"
```
Expected: Kim's driver record (`"code": "kim"`), `"active": true`, then `"locked": false, "failed_attempts": 0`. `curl -s -X POST http://127.0.0.1:8022/v1/auth/driver-login -H 'Content-Type: application/json' -d '{"code":"KIM","pin":"2468"}'` signs in too: codes match whatever the case.

**Step 3: Two consignments to plan**

See what is waiting:
```
curl -s "http://127.0.0.1:8022/v1/consignments?status=packed" -H "Authorization: Bearer $ADMIN" \
  | python3 -c 'import sys,json;d=json.load(sys.stdin);print(d["total"],[(c["id"],c["wms_ref"],c["customer"]) for c in d["items"]])'
```
With two or more, go on to Step 4. Otherwise pack two in Simple WMS, which sends `delivery.packed` to Simple TMS. A key for this check only:
```
cd "/Users/leightonlauton/Q7 Technology/Software/q7/simple-wms"
docker compose exec api wms create-api-client --name tms-check-3 --scopes 'tasks:*,integration:*' --warehouses '*' --owner '*'
```
(printed once), then, as in step 2's check, for `REF=TMS-DRV-1` and again for `REF=TMS-DRV-2`:
```
cd "/Users/leightonlauton/Q7 Technology/Software/q7/simple-tms"
WMS=http://127.0.0.1:8011 SETUP_KEY=<the tms-check-3 key> REF=TMS-DRV-1 api/.venv/bin/python - <<'PY'
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
    "ship_to": {"name": f"Ballarat Hardware {REF[-1]}", "address": "12 Sturt St", "suburb": "Ballarat",
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
Expected: `packed TMS-DRV-1`, `packed TMS-DRV-2`, and both in the list above within a few seconds. (If `GLV-L` has run out in `BAL-WH01`, use another SKU with stock there.)

**Step 4: Plan the run in the desktop**

Open `https://localhost:8543`, sign in as `dee` (`dispatch password`), **Plan runs**, **Day: Today** (the driver app asks for the phone's today). If `TMS041` has no run today, **Add a run**: `TMS041`, Kim. Drag the two consignments onto it. Do not press **Leaves now**: the driver's Start does that. (If `TMS041` already has a run today that has left, or isn't Kim's, add a truck as in Step 8 and use it here and in the script's `/TMS041/`.)

**Step 5: The driver's day, in headless Chrome at a phone's size**

Playwright drives the Chrome already installed:
```
mkdir -p /tmp/tms-pw/shots && cd /tmp/tms-pw && npm init -y >/dev/null && npm i playwright-core@1 >/dev/null
sips -s format jpeg "/Users/leightonlauton/Q7 Technology/Software/q7/simple-tms/apps/driver/public/icon-512.png" --out /tmp/tms-pw/door.jpg
```
Save as `/tmp/tms-pw/driver-day.mjs`:
```js
import { createRequire } from "module";
const require = createRequire("/tmp/tms-pw/");
const { chromium, devices } = require("playwright-core");
const BASE = "https://localhost:8543", OUT = "/tmp/tms-pw/shots", PHOTO = "/tmp/tms-pw/door.jpg";

// Chrome registers a service worker only on a trusted certificate; the flag
// lets it trust Caddy's local one for this run.
const browser = await chromium.launch({
  executablePath: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  headless: true, args: ["--ignore-certificate-errors"] });
const ctx = await browser.newContext({ ...devices["Pixel 7"], ignoreHTTPSErrors: true,
  permissions: ["geolocation"], geolocation: { latitude: -37.5622, longitude: 143.8503, accuracy: 12 } });
const page = await ctx.newPage();
page.on("pageerror", (e) => console.log("pageerror:", e.message));
const shot = (n) => page.screenshot({ path: `${OUT}/${n}.png` });
// wait until the phone's queue has sent n confirmations (asked from here: the
// app's CSP rightly forbids the eval that page.waitForFunction would need)
async function sent(n) {
  for (let i = 0; i < 60; i++) {
    const done = await page.evaluate(() => JSON.parse(localStorage.getItem("tms.driver.queue") ?? "[]")
      .filter((x) => x.status === "sent").length);
    if (done >= n) return;
    await page.waitForTimeout(500);
  }
  throw new Error(`fewer than ${n} confirmations sent`);
}

await page.goto(`${BASE}/driver/`);
await page.getByLabel("Driver code").fill("kim");
for (const d of "2468") await page.getByRole("button", { name: d, exact: true }).click();
await shot("1-sign-in");
await page.getByRole("button", { name: "Sign in" }).click();
await page.getByRole("radio", { name: /TMS041/ }).click();
await shot("2-today");
await page.getByRole("button", { name: /^(Start run|Carry on)$/ }).click();
await page.getByRole("link", { name: /Start stop 1/ }).waitFor();
console.log("service worker in control:", await page.evaluate(() => !!navigator.serviceWorker.controller));
await shot("3-run");

// stop 1: carton 1 by its number in the scan field, the rest ticked; signed, and a photo
await page.getByRole("link", { name: /Start stop 1/ }).click();
await page.getByLabel("Carton label").fill("1");
await page.getByLabel("Carton label").press("Enter");
await page.getByText(/^Carton 1 of \d+ scanned\.$/).waitFor();
for (const box of await page.getByRole("checkbox").all()) {
  if ((await box.getAttribute("aria-checked")) === "false") await box.click();
}
await shot("4-stop");
await page.getByRole("link", { name: "Delivered" }).click();
const pad = await page.getByLabel(/^Signature pad/).boundingBox();
await page.mouse.move(pad.x + 30, pad.y + 100);
await page.mouse.down();
for (let i = 1; i <= 12; i++) await page.mouse.move(pad.x + 30 + i * 20, pad.y + 100 - (i % 2) * 50);
await page.mouse.up();
await page.getByLabel("Printed name").fill("Priya");
await page.locator('input[type="file"]').setInputFiles(PHOTO);
await page.getByRole("img", { name: "The photo taken" }).waitFor();
await shot("5-proof");
await page.getByRole("button", { name: "Confirm delivery" }).click();
await page.getByText("Ding!").waitFor();
await sent(1);
await shot("6-ding");

// stop 2, in a black spot: can't deliver, with a photo and a note
await ctx.setOffline(true);
await page.getByRole("link", { name: /Next: stop 2/ }).click();
await page.getByRole("link", { name: "Can't deliver" }).click();
await page.getByRole("radio", { name: /Closed/ }).click();
await page.locator('input[type="file"]').setInputFiles(PHOTO);
await page.getByLabel(/Note/).fill("Closed sign up. Rang the number, no answer.");
await shot("7-cant-deliver");
await page.getByRole("button", { name: /Back on the truck/ }).click();
await page.getByText("No signal · working from memory").waitFor();
await shot("8-no-signal");

// the app opens from the phone with no signal at all
await page.reload();
await page.getByText(/1 confirmation waiting/).waitFor();
await shot("9-reloaded-offline");
console.log("opened with no signal: ok");

// back in range: what waited goes, once; then finish
await ctx.setOffline(false);
await page.evaluate(() => window.dispatchEvent(new Event("online")));
await sent(2);
await page.getByRole("button", { name: "Finish the run" }).click();
await page.getByText(/is finished\./).waitFor();
await sent(3);
await shot("10-finished");
console.log("finished: ok");
await browser.close();
```
Run: `node /tmp/tms-pw/driver-day.mjs`
Expected:
```
service worker in control: true
opened with no signal: ok
finished: ok
```
and ten screenshots in `/tmp/tms-pw/shots`. Look at each beside its drawn screen in `design/screens/` (sign-in, stop, proof with "Ding!", can't deliver, the no-signal banner with "Waiting to send"): layout, copy and states should match, with the departures listed under "Decisions".

To do the same by hand: Chrome on the computer, `https://localhost:8543/driver/`, DevTools (⌥⌘I) → device toolbar (⇧⌘M) → **Pixel 7**; **Network → No throttling → Offline** for the black spot; **Application → Service workers** and **Local storage** (`tms.driver.queue`) to watch the queue.

**Step 6: Recorded exactly once**

Run (simple-tms root):
```
docker compose exec -T db psql -U tms -d tms -c "
  select p.id, r.id as run, p.consignment_id, p.outcome, p.reason, p.signed_name,
         p.signature_file_id is not null as signature, p.photo_file_id is not null as photo,
         p.latitude, p.longitude, p.at
    from pod p join run r on r.id = p.run_id order by p.id desc limit 2" -c "
  select path, duplicates, received_at from inbound_message order by received_at desc limit 4" -c "
  select c.wms_ref, c.status, count(*) filter (where e.kind in ('delivered','failed')) as outcomes
    from consignment c join consignment_event e on e.consignment_id = c.id
   where c.id in (select consignment_id from pod order by id desc limit 2) group by c.wms_ref, c.status"
docker compose exec -T api sh -c 'find /data/pod -type f | wc -l'
```
Expected: two pods for the run (one `delivered` signed "Priya" with a signature and a photo, one `failed`, `closed`, with a photo), both at `-37.562200, 143.850300`; four messages (`start`, `deliver`, `fail`, `finish`), each `duplicates` 0; each consignment with exactly one outcome row (`delivered`, or `failed`); three files on the volume (at least: earlier tests may have left more).

Then send the failure again, as a phone whose reply was lost would, with the same `message_id`:
```
KIM=$(curl -s -X POST http://127.0.0.1:8022/v1/auth/driver-login -H 'Content-Type: application/json' \
  -d '{"code":"kim","pin":"2468"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')
docker compose exec -T db psql -U tms -d tms -tAc \
  "select message_id, path from inbound_message where path like '%/fail' order by received_at desc limit 1"
curl -s -X POST "http://127.0.0.1:8022<that path>" -H "Authorization: Bearer $KIM" \
  -H 'Content-Type: application/json' \
  -d '{"message_id":"<that message_id>","at":"2026-10-14T01:00:00Z","reason":"refused"}'
```
Expected: the original reply (`"outcome": "failed"`, the same `pod_id` and `at`), not a second record: the pod still says `closed`, `inbound_message.duplicates` for it is now 1, and the consignment still has one `failed` row. A new `message_id` for it instead is refused: `409 run_done`, since the run is finished (before finishing it would be `409 stop_done`).

**Step 7: What the office sees**

In the desktop, **Plan runs**, **Today**: the run's lane says **Done**, its stops **Delivered** and **Not delivered: closed**, "Delivered 1 of 2 · 1 coming back · finished …", and, when the consignments came from Simple WMS, "Marked shipped in Simple WMS: 2 of 2" (the driver's Start made the ship calls). The failed consignment is in the tray marked "Came back: not delivered"; **Day: Tomorrow**, drag it onto a run: it goes on, and its history (`GET /v1/consignments/{id}` as `dee`) reads `packed, assigned, departed, failed, assigned`. Take it back off to the tray afterwards. **Finish run** from the office: plan another run for today (any free truck, Step 8 makes one), press **Leaves now**, then as Kim (`$KIM`, above) deliver or fail its stops with `curl` or the phone but don't finish; the lane shows **Finish run** only once every stop has an outcome; press it and confirm: "R-… is finished." and the lane says **Done**. On the map, **Delivered** counts 1 more than before and **To plan** includes the one that came back.

A picture, as the office may see it:
```
DEE=$(curl -s -X POST http://127.0.0.1:8022/v1/auth/login -H 'Content-Type: application/json' \
  -d '{"username":"dee","password":"dispatch password"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')
SIG=$(docker compose exec -T db psql -U tms -d tms -tAc "select signature_file_id from pod where signature_file_id is not null order by id desc limit 1")
curl -s "http://127.0.0.1:8022/v1/pod-files/$SIG" -H "Authorization: Bearer $DEE" -o /tmp/tms-pw/signature.png && file /tmp/tms-pw/signature.png
curl -s -o /dev/null -w '%{http_code}\n' "http://127.0.0.1:8022/v1/pod-files/$SIG"
```
Expected: `PNG image data, …` (open it: Priya's zigzag), then `401` without a token.

If the consignments came from Simple WMS (Step 3), check it was told they shipped when Kim started:
```
curl -s "http://127.0.0.1:8011/v1/deliveries/TMS-DRV-1" -H "Authorization: Bearer <the tms-check-3 key>" \
  | python3 -c 'import sys,json;d=json.load(sys.stdin);print(d["status"],d["carrier"],d["tracking_no"])'
```
Expected: `shipped Own fleet R-…`.

**Step 8: On a real phone (optional)**

Plan a fresh run for today with a consignment or two (another truck: `POST /v1/vehicles` as the admin, `{"rego":"TMS052","type":"van","max_kg":"1200","max_m3":"9"}`, or put the one that came back on it), then follow "On a phone" in the README: Android through `chrome://inspect` port forwarding and `npm run preview`, or an iPhone through Caddy on the LAN address with its root certificate trusted. Sign in as `kim`, allow location, start, take a real photo with the camera, sign with a finger, turn on aeroplane mode for the next stop, then off: the banner goes and "Waiting to send" turns to "Sent", once.

**Step 9: Tidy up**

Revoke `tms-check-3` in Simple WMS if you made it (with it: `GET /v1/api-clients` for its id, then `POST /v1/api-clients/{id}/revoke`). Put `TMS_DOMAIN` back to `localhost` if you changed it, and `docker compose up -d caddy`. `rm -rf /tmp/tms-pw` when done. Kim's PIN stays `2468` until a manager changes it.

Nothing to commit.

---

## What is left for later steps

- **Pre-start checks** (step 4) go between picking the run and **Start run** on `Today.tsx`.
- **Live location** (step 5) replaces the "Location at drops" chip with sharing every 30 s during a run; its 90-day retention must also clear `pod.latitude`, `pod.longitude` and `pod.accuracy_m`.
- **Events out** (`consignment.delivered` with the pod, `consignment.failed` with the reason, `run.completed`) come with the first subscriber, from the same transactions that write the history rows here.
- **Showing the pictures in the desktop** comes with the Consignments drawer; `GET /v1/pod-files/{id}` is there for it.
