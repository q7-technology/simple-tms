# Simple TMS step 5: live location — implementation plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build step 5 of `docs/plans/2026-10-10-simple-tms-design.md`: the driver's phone shares where it is about every 30 seconds from **Start run** until the run is finished (never outside a run, and saying so on screen); the dispatcher's map draws each truck, says when it was last heard from, and gives ETAs for its next stop and the depot from the stop order and the driver's average time per stop, labelled as estimates; a "What just happened" feed and the grounded-truck marker deferred from step 4; **Where is it** on a real map from a configured tile provider or self-hosted tile server (never OpenStreetMap's own servers); and positions (with the position kept on each proof of delivery) deleted after 90 days by default.

**Architecture:** The API grows a `position` table (no append-only trigger: positions are personal information, not business history), `POST /v1/runs/{id}/position` (the run's driver, only while the run is `departed`; no `message_id`, no queue), `GET /v1/positions?date=` and `GET /v1/map` behind a new `location:read` scope, an `estimate` on every run on the road (`services/eta.py`, a pure function), `GET /v1/feed` read from the records already kept, and a worker job that deletes positions older than `TMS_POSITION_RETENTION_DAYS` and clears `pod.latitude/longitude/accuracy_m` once an hour. The driver app gets `lib/share.ts` (one `watchPosition` per run, the newest fix sent every 30 s, nothing queued) mounted once for all the run's screens by a layout route, so the top bar says **Sharing location**. The desktop keeps the drawn world as home (each run's truck placed along a drawn road by how many stops are done, as the design says) with the picked truck's card from `Main.html`, the feed and the grounded marker; **Where is it** is a drawer that lazy-loads Leaflet with raster tiles from `TMS_MAP_TILE_URL`, and lists every position as text whether or not tiles are set up.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2, Alembic, pytest on a real PostgreSQL 16; Vite 6, React 19, TypeScript 5, Tailwind 4, react-router 7, Vitest 3 with Testing Library; Leaflet 1.9.4 (new, desktop only, loaded only by **Where is it**); Caddy 2 and Docker Compose.

---

## Context

Paths are relative to the Simple TMS repo, `/Users/leightonlauton/Q7 Technology/Software/q7/simple-tms/`. Everything here was checked against `main` today (`b5d98b0`, "Ask Fleet about today on each load, not the day it opened"): each task was built in a scratch copy, in order, against a throwaway PostgreSQL 16, and the counts given at each task are what the suites printed there. API 297 → 332, desktop 70 → 84, driver app 89 → 98; both apps type-check and build. The branch was also started as a throwaway Compose project (its own ports and volumes) and the manual check's own scripts (Task 16, Steps 5–8) were run against it with a real Chrome and a faked GPS: the phone shared two positions 30 s apart, the desktop's card and **Where is it** showed them, the office finishing the run stopped the phone ("Location off") with nothing kept after, and the worker forgot a 91-day-old position and a proof of delivery's position on start. The manual check itself has not been run on the local stacks.

### 1. The rules that bind this step (`CLAUDE.md`)

- **Location (Conventions):** "the driver app shares its position only during a run, and says so on screen. Positions are deleted after the retention period." This step makes both true and rewords the line to say why deleting them doesn't break rule 1 (Task 14).
- **Rule 1: append-only.** Positions are never put in `consignment_event` (step 3 already kept a delivery's position on the `pod` row only, for this reason). Deleting positions and clearing the position on a `pod` row is the one deletion this step makes; no history row, check or record is touched.
- **Rule 2: recorded once.** Untouched: a position is not a confirmation. It has no `message_id` and doesn't go through `envelope.handle` or the phone's offline queue (Decision 4).
- **Rule 4: the API is the only door.** The phone and the desktop use only the endpoints below; a tracker in the truck could send positions with a `deliveries:write` key, and a system reads them only with a key made with `location:read`.
- **Git.** Subject line and, if needed, a short body. No `Co-Authored-By`, no tool or generated-by trailers, no session links.

### 2. The code as it is on `main`

- **Positions today.** Step 3 takes the phone's position once per delivery or failed delivery (`apps/driver/src/lib/where.ts`, `getPosition`, at most 4 s) and keeps it on `pod.latitude`, `pod.longitude`, `pod.accuracy_m` (`api/tms/models/delivery.py`); `pod` has no append-only trigger, and the step-3 plan settled that step 5's retention job clears those three columns too. `askForLocation()` asks for permission once, right after **Start run** (`apps/driver/src/pages/Today.tsx:81`). The top bar's chip (`apps/driver/src/ui/kit.tsx`, `RunTop`) says "Location at drops" or "Location off", by `navigator.permissions` alone, instead of the design's "Sharing location".
- **The driver's run screens** are four sibling routes (`apps/driver/src/App.tsx`): `/run/:runId` (`RunHome`), `.../stop/:consignmentId` (`Stop`), `.../proof`, `.../cant`. Only `RunHome` and `Stop` draw `RunTop`. A run's copy is in localStorage (`lib/run.ts`, `loadRun`), its queued confirmations from `useQueueItems`; `finished(run, items)` is true once a finish is queued or done.
- **Run status.** `planned` → `departed` (the driver's Start, or the office's **Leaves now**) → `done` (finish), or `cancelled`. `deliveries.own_run` gives a driver only their own runs (another's is `404`); a key or user with the scope gets any.
- **Roles** (`api/tms/services/sessions.py`): `admin` `*`; `manager` and `dispatcher` with `fleet:read`, `consignments:read`, `runs:read`, `runs:write` (and `fleet:write` for a manager); drivers `fleet:read`, `deliveries:read`, `deliveries:write`. `api_keys.SCOPES` lists what a key may be given. `tests/test_auth.py:14` asserts the dispatcher's scopes exactly.
- **The worker** (`api/tms/worker.py`) loops over the ship-call and print queues every `TMS_WORKER_POLL_SECONDS`; it has no timed jobs yet.
- **The desktop map** (`apps/desktop/src/pages/MapHome.tsx`) is `public/region.svg` as an `<img>` (the design's picture with its pins and labels taken out, but still with four drawn trucks in it: lines 79, 81, 84 and 99), the three counts, the drawer bar and, since step 4, a red count on **Fleet** of trucks that can't go. It polls every 30 s (`/v1/consignments` counts, `/v1/runs?status=departed,done` for the delivered count, `/v1/vehicles?date=&active=true`). No truck is drawn from data and there is no feed. `useAuth().can(scope)` exists. The desktop's CSP is `default-src 'self'; img-src 'self' blob:; …` (`deploy/Caddyfile:57` and twice in `README.md`).
- **Tests.** API: `conftest.py` truncates every table after each test; `deliveries_samples.py` (`planned_run`, `on_the_road`, `driver_headers`, `delivered`, `failed`, `prestart`), `runs_samples.py` (`vehicle`, `driver`, `consignment`, `new_run`, `assign`, `DAY = 2026-10-14`), `fleet_samples.py` (`record`), `test_api_keys._key`. Driver: `harness.tsx` (`stubApi`, `renderApp`, `signedIn`, `onThePhone`), `samples.ts` (`RUN`). Desktop: `harness.tsx` (`stubApi`, `renderApp`, `DISPATCHER`, `MANAGER`).

### 3. The designs

- `Main.html`: the drawn world with run trucks on its roads, labels "R-2207 · Kim" (a white pill with the run's colour dot), "R-2209 · loading" at the depot and "TMS-027 · grounded" in red over a grey truck. The **Selected truck** card, top right: "TMS-041 · Kim · rigid", "Run R-2207", close; green **On the road** pill and "Last heard 30 s ago"; "7 of 12 stops" / "5 to go" with a segment bar; Next stop "8 · Ridge Auto Workshop", Arrives "about 11:40", Load left "2,140 kg · 9.8 m³", Back at depot "about 2:15 pm"; "Times are estimates from the stop order and Kim's average time per stop today."; **Open run** and **Where is it**. Bottom left, **WHAT JUST HAPPENED**: "Kim delivered stop 7 · Main Street Chemist · 2 min" (green), "Mel passed the pre-start on TMS-052 · 9 min" (violet), "C-30431 packed in Simple WMS · 14 min" (brown), "TMS-027 grounded · brake light out · 4 h" (red). Also drawn and left out here: the Game-touches, day-and-night and sound toggles, the zoom buttons and **Today's goal** (Decision 12).
- `DriverStop.html`, `DriverOffline.html`: the top bar's green chip **Sharing location** with a navigation-arrow icon (the app's existing `pin` icon).
- `design/README.md`: run colours `#1F8A5B`, `#2F6FB8`, `#7B5CC4`; red only where a person needs to look.

### Decisions this plan makes that were not settled

Each is for the user to confirm or change; the plan is built on them. The ones marked **(you choose)** are real choices with more than one good answer; the rest follow from the design and the code.

1. **The map: the drawn world stays home, and "Where is it" opens a real map (option c). (you choose)** The design says both things: the desktop "is a map … a small hand-drawn world" where "each run's truck drives its route as stops are delivered", and live location uses "a configured tile provider or a self-hosted server". The drawn region is pretend geography: a real position can't be placed on it honestly. So:
   - **Home** is `Main.html` as drawn: each run on the road is a truck on a drawn road of its own (five roads out of the depot gate; a sixth run shares the first's, a little aside), as far along as its stops are done (the truck half way between the last stop done and the next, the drive back the last leg); runs still loading and trucks that can't go stand in the depot yard. Placement is from data the API already has, not from GPS, and the README says so (Known limitations). The four trucks drawn into the picture itself are taken out of the app's copy of `region.svg` (`design/` is untouched), so only real runs show.
   - **Where is it** (the card's button) is a drawer with a real slippy map, each truck where its phone last said, and the same positions as a table under it, always.
   - Option (a), a real map as the home screen styled to look drawn, loses the Game Mode look the design and both sibling apps are built on, and shows nothing at all until a tile provider is set up. Option (b), progress only and no real map, ignores the design's tile-provider line and leaves the office unable to answer "where is it?". (c) costs one lazily loaded drawer.
2. **Leaflet with raster tiles from a URL you set; nothing out of the box. (you choose the provider)** There is no tile source that works for a local install with no account: OpenStreetMap's own servers are ruled out by the design (and their tile policy), and every provider needs a sign-up and a key. So with nothing set, **Where is it** says "No map tiles are set up, so each truck's position is listed without a map" and lists them; once `TMS_MAP_TILE_URL` is set (any `{z}/{x}/{y}` raster address, `https://` or a path on this server), it draws the map. **Recommendation: MapTiler's free tier** (raster `streets-v2`, a key restricted to your domain) to start; a self-hosted tile server (for example `tileserver-gl` with a Victorian extract behind this Caddy at `/tiles/…`) when you want no third party to see which areas the office looks at. Leaflet rather than MapLibre: 44 kB gzipped against several hundred, no WebGL and no `worker-src blob:` in the CSP, any raster provider works, and a few markers on a map need nothing more. It is loaded only when **Where is it** opens (its own chunk). The API refuses to start with a tile URL without `{z}`, `{x}` and `{y}`, not `https://` or a path, or on `tile.openstreetmap.org` (`tms/config.py`). `GET /v1/map` hands the desktop the URL and attribution, so a provider change is a `.env` edit, not a rebuild.
3. **The CSP gets the tile origin from `.env`.** The desktop's `img-src` becomes `'self' blob: {$TMS_MAP_TILE_ORIGIN}` in the Caddyfile (Caddy fills it from the environment when it starts; empty is harmless, and right for tiles on this server). That is a second setting beside `TMS_MAP_TILE_URL` (a CSP source can't contain `{z}`), so if they disagree **Where is it** says "The map tiles didn't load" and names both. The README's two shared-Caddy snippets get a comment to add the origin by hand.
4. **The phone shares while a run is on the road, from the run's screens, with the app open; nothing is queued.** `lib/share.ts` starts one `watchPosition` (high accuracy, fixes up to 15 s old) when the run's screens open on a `departed` run that isn't finished on the phone, sends the first fix at once and then the newest every 30 s if there is a newer one, and stops when the run is finished (a finish queued on the phone counts), when the API answers `409` (the office finished or cancelled it), when the driver leaves the run's screens (Today, sign-out) and when the app is closed. One watch serves all four run screens: they become children of a layout route (`OnTheRun`), so moving from a stop to its proof doesn't restart it. `OnTheRun` also holds the run itself: it reads the phone's copy, looks it up from the office once (and again when the signal is back, or when the API answers a position with `409`), and hands it to the run's screens through context with the sharing state, so a refresh changes what they show and whether the phone shares together. A run the office finished says so plainly ("The office finished R-0008.") and shows as finished, with no next stop; with no signal to look it up after a `409 run_done`, the phone's copy is marked finished. **No backlog in a black spot:** a position that can't be sent is dropped and the next newest goes when the signal is back. Positions aren't confirmations (rule 2 is about deliveries), nobody is shown a trail (Decision 8), and a backlog would mean more personal data kept, more data used and more battery for nothing the office sees. **Foreground only:** a web app can't use location in the background; the README says so plainly, and the desktop turns "Last heard" amber after 5 minutes so the office can tell. **Battery:** GPS stays on during a run; a phone in a cradle on charge is the norm in a truck, and the README doesn't promise otherwise. **(you choose)** if you want trails for disputes later, a small bounded queue (say the last 20 fixes) is the change.
5. **What the API takes, and what it refuses.** `POST /v1/runs/{id}/position` `{at, latitude, longitude, accuracy_m}` → `204`, `location:write`, a scope of its own: the run's driver (another's run is `404`; drivers have it), or a key made with `location:write` named (a tracker), which may post for any run on the road, as a key may record deliveries on any run. A key with only `deliveries:write` is `403`: recording a delivery is not saying where a truck is. Only while the run is `departed`: `409 run_not_started` before (never before Start), `409 run_done` / `run_cancelled` after (the phone stops). Decimal strings rounded to six places, as for a delivery. `accuracy_m` required and at most 1,000 m (`422`; the phone doesn't send a rougher fix either). `at` later than now is taken as now. Another position for the run less than 10 s after the last is `429 too_often` (the phone sends every 30 s; this bounds a faulty or hostile client). **No "impossible jump" filter:** with a 1 km accuracy cap the GPS is rarely that wrong, and refusing a jump would refuse every correct fix after one bad one; the office sees the latest anyway.
6. **One table, kept apart from the business records.** `position` (`id` bigint, `run_id`, `driver_id` and `vehicle_id` as the run had them when it arrived, `latitude`, `longitude`, `accuracy_m`, `recorded_at` from the phone, `received_at` by the server), indexed by run and arrival (for the latest) and by `recorded_at` (for retention). No trigger, and nothing references it. Nothing about a position is written to `consignment_event`, `inbound_message` or the logs.
7. **Retention: the worker forgets, hourly.** On start and then once an hour (`FORGET_EVERY_SECONDS`), `forget_positions` deletes positions whose `recorded_at` is older than `TMS_POSITION_RETENTION_DAYS` (default 90, at least 1) and sets `pod.latitude`, `longitude` and `accuracy_m` to null where `pod.at` is older (a partial index on `pod.at where latitude is not null` finds them). The delivery, its signature, photo and history stay for good. CLAUDE.md's location rule says why this is not a breach of rule 1: positions are personal information, not business history, and this is the only deletion allowed. A failure is logged and tried again in an hour; the queues go on.
8. **Who sees positions: dispatchers, managers and admins, the latest only. (you choose)** A new scope, `location:read`, given to the `dispatcher` and `manager` roles (admins have `*`), not to drivers, and to a key only when it is named, since it is personal information about the drivers. `GET /v1/positions?date=` gives the latest position of each of the day's runs on the road; a run that hasn't left or is finished has none, whatever was kept. **There is no endpoint for earlier positions** (no trails, no history screen): the design asks for "each truck and when it was last heard from", and the kept rows are for a dispute, read from the database by an administrator. Choose otherwise if you want managers only to see the map, or a day's trail.
9. **ETAs: the run's own pace, no distances, never before now.** On every run that is `departed`, `estimate` = `{basis, minutes_per_stop, stops: [{consignment_id, sequence, at}], back_at_depot}`. A stop takes the average from leaving the depot to the latest stop done (delivered or not), over the stops done; before the first, `TMS_ETA_MINUTES_PER_STOP` (20, `basis: "default"`), and never less than a minute. The next stop is one average after the latest stop done (or leaving), never before now (a driver running late is due now, and the rest move with them), each later stop one average more, and the drive back counts as one stop more. **Distance doesn't come in:** without a routing service a straight-line distance would mislead more than it helps, and the average already has the driving in it. It is "per run", which for one run a day is the design's "average time per stop so far today". It is worked out by the API (so a partner gets the same numbers) and the desktop rounds to five minutes: "about 11:40 am", with the design's line "Times are estimates from the stop order and Kim's average time per stop today." (or, before the first drop, "…at 20 minutes a stop until Kim's first drop.").
10. **"What just happened" is read from what is already kept, and polled.** `GET /v1/feed?limit=` (`runs:read`): consignments `packed` from Simple WMS, stops `delivered` and `failed` (with the reason), runs that `left` and `finished`, `prestart` checks (passed, an item failed, grounded) and trucks marked `fixed` (these two only for a caller who also has `fleet:read`: they are the fleet's records, and a key with `runs:read` alone doesn't see them); newest first, the newest `limit` of each kind merged and cut to `limit`. Structured fields, worded by the desktop as the design does ("Kim delivered stop 7 · Main Street Chemist"). Nothing new is written, and no position is in it. The map asks for 5 on its existing 30-second poll; no websockets (a dispatcher's feed is fine 30 s late, and polling is what the rest of the desktop does). Assigning, unassigning and reordering are left out: they are the dispatcher's own doing.
11. **The grounded truck on the map.** A truck that can't go today (grounded, or rego or service overdue, from step 4's `/v1/vehicles`) and isn't on a run today stands in the depot yard, grey, with a red "TMS053 · grounded" (or "· overdue") label that opens **Fleet**. A run whose truck became blocked keeps its own marker, with a red dot. Step 4's red count on the **Fleet** button stays as it is.
12. **The map's other pieces.** The card shows the first run on the road until another is picked (as `Main.html` shows one); **Close** hides it until one is picked. Every marker is a real button (or link) whose name says the run, the driver and when it was last heard from, which is the text alternative for the drawing. **Open run** opens **Plan runs** (it doesn't scroll to the run). Left out of this step: Today's goal, Game touches, sounds, day and night and the zoom buttons (no step has them; they are their own piece of work), and the stops drawn as pins along each road.
13. **Copy.** The chip says **Sharing location** while sharing (and while waiting for the first fix) and **Location off** otherwise, as drawn. Under the next stop: "While this run is on the road, this phone shares where it is with the office about every 30 seconds, with the app open. It stops when you finish the run or sign out." (or "Location is off for this app, so the office can't see where you are. Each drop is still recorded."). Before Start: "Starting shares this phone's location with the office about every 30 seconds until you finish the run, and keeps it with each drop. Never outside a run."
14. **Events out stay deferred** (`consignment.delivered` and the rest), as in steps 3 and 4: nothing needs them for this step.

**For you to decide, in short:** the map shape (1, recommended: drawn home plus a real map behind **Where is it**); which tile provider, or a tile server of your own (2, recommended: MapTiler's free tier to start); whether 90 days suits your business and state (7; the README points at the NSW Workplace Surveillance Act); who sees positions and whether anyone needs trails (8, recommended: dispatchers and managers, latest only); and whether the foreground-only limit of a web app is acceptable (4).

### Ports and addresses, locally

Simple TMS API `127.0.0.1:8022`, Caddy `https://localhost:8543` (desktop at `/`, driver app at `/driver/`), database `127.0.0.1:5434` on migration `0005` before this step. Users `leighton` (admin, `a long enough password`) and `dee` (dispatcher, `dispatch password`); driver `kim`, PIN `2468`; vehicles `TMS041` (rigid), `TMS052` and `TMS053` (vans). Chrome needs `--ignore-certificate-errors` for Caddy's local certificate; Playwright can fake the phone's position.

---

## Before you start

```
cd "/Users/leightonlauton/Q7 Technology/Software/q7/simple-tms"
git switch main && git pull --ff-only
git switch -c step-5-live-location
docker compose up -d db                      # the tests' PostgreSQL on 127.0.0.1:5434
cd api && caffeinate -i .venv/bin/pytest -q  # expect: 297 passed
cd ../apps/desktop && npm test               # expect: 70 passed
cd ../driver && npm test                     # expect: 89 passed
```

The plan has four parts: **A**, the API (Tasks 1–7); **B**, the driver app (Tasks 8–9); **C**, the desktop (Tasks 10–13); **D**, the docs and the checks, ending with the manual check on the local stacks (Tasks 14–16). Parts B and C stub the API in their tests, so they don't wait on Part A except for the manual check. `pytest` runs from `api/` (with `caffeinate -i` in front for the whole suite: this Mac sleeps on battery and the suite's 15-minute tokens then expire mid-run), `npm` from the app's folder. Code is given whole for a new file, and as a unified diff for a changed one, against the file as the tasks before it leave it (`main` for its first change); apply it by hand, or save it and `git apply`. Commit after each task as shown, on the branch.

## Part A — the API

### Task 1: The `position` table, and the retention, map and ETA settings

**Files:**
- Create: `api/tms/models/location.py`, `api/alembic/versions/0006_live_location.py`
- Modify: `api/tms/models/__init__.py`, `api/tms/models/delivery.py` (the partial index retention uses), `api/tms/config.py`
- Test: `api/tests/test_config.py`, `api/tests/test_migrations.py`

**Step 1: Write the failing tests**

Change `api/tests/test_config.py`:
```diff
--- a/api/tests/test_config.py
+++ b/api/tests/test_config.py
@@ -31,3 +31,35 @@ def test_printing_and_the_worker_start_switched_off_with_safe_waits():
 
 def test_proof_of_delivery_is_kept_in_the_data_volume():
     assert Settings(secret_key="3f9c1e0a7b2d4c6e8f0a1b2c3d4e5f60").pod_dir == "/data/pod"
+
+
+KEY = "3f9c1e0a7b2d4c6e8f0a1b2c3d4e5f60"
+
+
+def test_positions_are_kept_90_days_and_no_map_tiles_are_set_up():
+    s = Settings(secret_key=KEY)
+    assert s.position_retention_days == 90
+    assert (s.map_tile_url, s.map_attribution) == ("", "")
+    assert s.eta_minutes_per_stop == 20
+
+
+@pytest.mark.parametrize("url", [
+    "https://api.maptiler.com/maps/streets-v2/256/{z}/{x}/{y}.png?key=abc",
+    "/tiles/{z}/{x}/{y}.png"])
+def test_a_tile_url_from_a_provider_or_this_server(url):
+    assert Settings(secret_key=KEY, map_tile_url=url).map_tile_url == url
+
+
+@pytest.mark.parametrize("url", [
+    "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
+    "https://a.tile.openstreetmap.org/{z}/{x}/{y}.png",
+    "http://tiles.example.com/{z}/{x}/{y}.png",
+    "https://tiles.example.com/{z}/{x}.png"])
+def test_a_tile_url_that_cant_be_used_refuses_to_start(url):
+    with pytest.raises(ValidationError, match="TMS_MAP_TILE_URL"):
+        Settings(secret_key=KEY, map_tile_url=url)
+
+
+def test_a_retention_period_of_no_days_refuses_to_start():
+    with pytest.raises(ValidationError):
+        Settings(secret_key=KEY, position_retention_days=0)
```

Change `api/tests/test_migrations.py`:
```diff
--- a/api/tests/test_migrations.py
+++ b/api/tests/test_migrations.py
@@ -14,7 +14,7 @@ TABLES = {"user", "user_session", "driver", "vehicle", "consignment", "package",
           "consignment_event", "inbound_event", "run", "stop", "outbound_call", "print_job",
           "api_key", "inbound_message", "pod_file", "pod",
           "checklist_item", "prestart_check", "prestart_item", "vehicle_record",
-          "alembic_version"}
+          "position", "alembic_version"}
 
 
 @pytest.fixture
```


**Step 2: Run them to see them fail**

Run: `.venv/bin/pytest tests/test_config.py tests/test_migrations.py -q`
Expected: the 8 new config tests fail (`AttributeError: 'Settings' object has no attribute 'position_retention_days'`, or `'map_tile_url'`, or `DID NOT RAISE`), and `test_migrations_go_up_down_and_up_again` fails: `position` is not among the tables.

**Step 3: Implement**

Create `api/alembic/versions/0006_live_location.py`:
```python
"""live location: where each run's phone was, kept for the retention period

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-13
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # no append-only trigger: positions are personal information, deleted
    # after the retention period, not business history
    op.create_table(
        "position",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("driver_id", sa.Integer(), nullable=False),
        sa.Column("vehicle_id", sa.Integer(), nullable=False),
        sa.Column("latitude", sa.Numeric(9, 6), nullable=False),
        sa.Column("longitude", sa.Numeric(9, 6), nullable=False),
        sa.Column("accuracy_m", sa.Numeric(8, 1), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["run.id"], name=op.f("fk_position_run_id_run")),
        sa.ForeignKeyConstraint(["driver_id"], ["driver.id"],
                                name=op.f("fk_position_driver_id_driver")),
        sa.ForeignKeyConstraint(["vehicle_id"], ["vehicle.id"],
                                name=op.f("fk_position_vehicle_id_vehicle")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_position")),
    )
    op.create_index("ix_position_run_id_received_at", "position", ["run_id", "received_at"])
    op.create_index(op.f("ix_position_recorded_at"), "position", ["recorded_at"])
    # the proofs of delivery whose position the worker will clear one day
    op.create_index("ix_pod_at_located", "pod", ["at"],
                    postgresql_where=sa.text("latitude is not null"))


def downgrade() -> None:
    op.drop_index("ix_pod_at_located", table_name="pod")
    op.drop_index(op.f("ix_position_recorded_at"), table_name="position")
    op.drop_index("ix_position_run_id_received_at", table_name="position")
    op.drop_table("position")
```

Change `api/tms/config.py`:
```diff
--- a/api/tms/config.py
+++ b/api/tms/config.py
@@ -1,9 +1,14 @@
 from functools import lru_cache
 
-from pydantic import field_validator
+import re
+
+from pydantic import Field, field_validator
 from pydantic_settings import BaseSettings, SettingsConfigDict
 
 MIN_SECRET_KEY = 32
+# OpenStreetMap's own tile servers are for its website, not for apps
+# (operations.osmfoundation.org/policies/tiles): the design rules them out.
+OSM_TILES = re.compile(r"^https://([a-c]\.)?tile\.openstreetmap\.org/", re.IGNORECASE)
 
 
 class Settings(BaseSettings):
@@ -32,6 +37,20 @@ class Settings(BaseSettings):
     # volume in Compose). Only the API reads and writes them.
     pod_dir: str = "/data/pod"
 
+    # Live location. A truck's positions, and the position kept on a proof of
+    # delivery, are deleted by the worker this many days after they were
+    # taken: they are personal information, not business history.
+    position_retention_days: int = Field(default=90, ge=1)
+    # Map tiles for the desktop's "Where is it": a raster tile address with
+    # {z}, {x} and {y}, from a tile provider (with its key) or a tile server
+    # of your own (an https address, or a path on this server such as
+    # /tiles/{z}/{x}/{y}.png), and the attribution it asks for. Left empty,
+    # the desktop lists each truck's position without a map.
+    map_tile_url: str = ""
+    map_attribution: str = ""
+    # ETAs: the minutes a stop is taken to need until the driver has done one.
+    eta_minutes_per_stop: int = Field(default=20, ge=1, le=600)
+
     # The worker that sends the ship calls and print jobs.
     worker_poll_seconds: float = 5.0
     worker_http_timeout_seconds: float = 10.0
@@ -45,6 +64,23 @@ class Settings(BaseSettings):
                 "example; make one with: openssl rand -hex 32")
         return value
 
+    @field_validator("map_tile_url")
+    @classmethod
+    def _usable_tiles(cls, value: str) -> str:
+        value = value.strip()
+        if not value:
+            return value
+        if not all(part in value for part in ("{z}", "{x}", "{y}")):
+            raise ValueError("TMS_MAP_TILE_URL needs {z}, {x} and {y} in it, e.g. "
+                             "https://tiles.example.com/{z}/{x}/{y}.png")
+        if not (value.startswith("https://") or value.startswith("/")):
+            raise ValueError("TMS_MAP_TILE_URL must be an https:// address, or a path on this "
+                             "server such as /tiles/{z}/{x}/{y}.png")
+        if OSM_TILES.match(value):
+            raise ValueError("TMS_MAP_TILE_URL can't be OpenStreetMap's own tile servers: their "
+                             "policy rules out apps. Use a tile provider or a server of your own")
+        return value
+
 
 @lru_cache
 def get_settings() -> Settings:
```

Change `api/tms/models/__init__.py`:
```diff
--- a/api/tms/models/__init__.py
+++ b/api/tms/models/__init__.py
@@ -5,10 +5,11 @@ from tms.models.consignment import Consignment, ConsignmentEvent, Package
 from tms.models.integration import InboundEvent, OutboundCall, PrintJob
 from tms.models.runs import Run, Stop
 from tms.models.delivery import InboundMessage, Pod, PodFile
+from tms.models.location import Position
 
 __all__ = [
     "Base", "User", "UserSession", "Driver", "Vehicle", "Consignment", "Package",
     "ConsignmentEvent", "InboundEvent", "Run", "Stop", "OutboundCall", "PrintJob", "ApiKey",
     "InboundMessage", "Pod", "PodFile", "ChecklistItem", "PrestartCheck", "PrestartItem",
-    "VehicleRecord",
+    "VehicleRecord", "Position",
 ]
```

Change `api/tms/models/delivery.py`:
```diff
--- a/api/tms/models/delivery.py
+++ b/api/tms/models/delivery.py
@@ -4,7 +4,7 @@ import uuid
 from datetime import datetime
 from decimal import Decimal
 
-from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Uuid
+from sqlalchemy import DateTime, ForeignKey, Index, Integer, Numeric, String, Uuid, text
 from sqlalchemy.dialects.postgresql import JSONB
 from sqlalchemy.orm import Mapped, mapped_column
 
@@ -57,6 +57,9 @@ class Pod(Base):
     reason). One per stop; the API never changes one."""
 
     __tablename__ = "pod"
+    # the worker clears a position older than the retention period: this finds them
+    __table_args__ = (Index("ix_pod_at_located", "at",
+                            postgresql_where=text("latitude is not null")),)
 
     id: Mapped[int] = mapped_column(primary_key=True)
     message_id: Mapped[uuid.UUID] = mapped_column(Uuid, unique=True)
@@ -76,7 +79,8 @@ class Pod(Base):
     packages: Mapped[list] = mapped_column(JSONB, default=list)
     # when it happened, by the phone's clock (never later than it arrived)
     at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
-    # where the phone was, if the driver let it say
+    # where the phone was, if the driver let it say; cleared after the
+    # retention period, as positions are (the only change ever made to a pod)
     latitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
     longitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
     accuracy_m: Mapped[Decimal | None] = mapped_column(Numeric(8, 1))
```

Create `api/tms/models/location.py`:
```python
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Numeric
from sqlalchemy.orm import Mapped, mapped_column

from tms.models.base import Base


class Position(Base):
    """Where a run's phone was, about every 30 seconds from Start until the
    run is finished, and never outside a run. Personal information, not
    business history: the worker deletes each one once it is older than
    TMS_POSITION_RETENTION_DAYS (90 by default). So no append-only trigger,
    and nothing else points at it."""

    __tablename__ = "position"
    __table_args__ = (Index("ix_position_run_id_received_at", "run_id", "received_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("run.id"))
    # who was driving and what, as the run had them when it was sent
    driver_id: Mapped[int] = mapped_column(ForeignKey("driver.id"))
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicle.id"))
    latitude: Mapped[Decimal] = mapped_column(Numeric(9, 6))
    longitude: Mapped[Decimal] = mapped_column(Numeric(9, 6))
    accuracy_m: Mapped[Decimal] = mapped_column(Numeric(8, 1))
    # when the phone took it (never later than it arrived); what retention counts from
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    # when it arrived: "last heard"
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
```


**Step 4: Run the tests**

Run: `.venv/bin/pytest tests/test_config.py tests/test_migrations.py -q`
Expected: `22 passed` (the models-match-migrations test proves the model and 0006 agree). Then `caffeinate -i .venv/bin/pytest -q`: `305 passed`.

**Step 5: Commit**

```bash
git add api
git commit -m "Keep positions in a table of their own, and settle the map and retention settings"
```

### Task 2: The phone's position, taken only while its run is on the road

**Files:**
- Create: `api/tms/services/location.py`, `api/tms/api/routes/location.py`
- Modify: `api/tms/api/app.py`
- Test: `api/tests/test_positions.py`

**Step 1: Write the failing tests**

Create `api/tests/test_positions.py`:
```python
"""The driver's phone says where the truck is, about every 30 seconds, from
Start until the run is finished, and never outside a run."""
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from deliveries_samples import delivered, driver_headers, on_the_road, planned_run
from runs_samples import driver
from tms.models import ConsignmentEvent, Position


def where(**over) -> dict:
    return {"at": datetime.now(UTC).isoformat(), "latitude": "-37.5622104",
            "longitude": "143.8503", "accuracy_m": "12.34", **over}


def send(client, headers, run_id, **over):
    return client.post(f"/v1/runs/{run_id}/position", headers=headers, json=where(**over))


def test_the_phone_says_where_the_truck_is_while_the_run_is_on_the_road(client, db,
                                                                        dispatcher_headers):
    run, _, kim = on_the_road(client, db, dispatcher_headers)
    events = db.execute(select(ConsignmentEvent.id)).scalars().all()
    r = send(client, kim, run["id"])
    assert r.status_code == 204, r.text
    p = db.execute(select(Position)).scalar_one()
    assert p.run_id == run["id"] and p.driver_id == 1 and p.vehicle_id == 1
    assert (str(p.latitude), str(p.longitude), str(p.accuracy_m)) == ("-37.562210", "143.850300", "12.3")
    assert p.received_at >= p.recorded_at
    # a position is never history: nothing is added to a consignment's
    assert db.execute(select(ConsignmentEvent.id)).scalars().all() == events


def test_nothing_is_shared_before_the_run_leaves(client, db, dispatcher_headers):
    run, _ = planned_run(client, db, dispatcher_headers)
    r = send(client, driver_headers(client), run["id"])
    assert r.status_code == 409 and r.json() == {
        "code": "run_not_started",
        "detail": f"{run['run_no']} hasn't left yet, so where it is isn't shared"}
    assert db.execute(select(Position)).first() is None


def test_nothing_is_shared_once_the_run_is_finished(client, db, dispatcher_headers):
    run, (a,), kim = on_the_road(client, db, dispatcher_headers, n=1)
    delivered(client, kim, run["id"], a)
    finish = client.post(f"/v1/runs/{run['id']}/finish", headers=kim,
                         json={"message_id": str(uuid.uuid4())})
    assert finish.status_code == 200, finish.text
    r = send(client, kim, run["id"])
    assert r.status_code == 409 and r.json() == {
        "code": "run_done", "detail": f"{run['run_no']} is finished, so where it is isn't shared"}
    assert db.execute(select(Position)).first() is None


def test_another_drivers_run_is_not_found(client, db, dispatcher_headers):
    run, _, _ = on_the_road(client, db, dispatcher_headers)
    driver(db, code="mel", name="Mel")
    r = send(client, driver_headers(client, code="mel"), run["id"])
    assert r.status_code == 404


def test_a_dispatcher_cant_send_one(client, db, dispatcher_headers):
    run, _, _ = on_the_road(client, db, dispatcher_headers)
    assert send(client, dispatcher_headers, run["id"]).status_code == 403


def test_a_position_too_rough_to_draw_is_refused(client, db, dispatcher_headers):
    run, _, kim = on_the_road(client, db, dispatcher_headers)
    r = send(client, kim, run["id"], accuracy_m="1500")
    assert r.status_code == 422
    assert r.json()["errors"][0]["field"] == "accuracy_m"
    assert send(client, kim, run["id"], accuracy_m=None).status_code == 422


def test_a_time_ahead_of_now_is_taken_as_now(client, db, dispatcher_headers):
    run, _, kim = on_the_road(client, db, dispatcher_headers)
    ahead = datetime.now(UTC) + timedelta(hours=3)
    assert send(client, kim, run["id"], at=ahead.isoformat()).status_code == 204
    p = db.execute(select(Position)).scalar_one()
    assert p.recorded_at <= p.received_at


def test_one_position_every_ten_seconds_is_plenty(client, db, dispatcher_headers):
    run, _, kim = on_the_road(client, db, dispatcher_headers)
    assert send(client, kim, run["id"]).status_code == 204
    r = send(client, kim, run["id"])
    assert r.status_code == 429 and r.json()["code"] == "too_often"
    # ten seconds on, the next is kept
    db.execute(update(Position).values(received_at=Position.received_at - timedelta(seconds=11)))
    db.commit()
    assert send(client, kim, run["id"]).status_code == 204
    assert len(db.execute(select(Position)).all()) == 2
```


**Step 2: Run them to see them fail**

Run: `.venv/bin/pytest tests/test_positions.py -q`
Expected: `7 failed, 1 passed` (each `assert 404 == …`: there is no route yet; the other driver's run is a `404` already).

**Step 3: Implement**

Change `api/tms/api/app.py`:
```diff
--- a/api/tms/api/app.py
+++ b/api/tms/api/app.py
@@ -6,8 +6,8 @@ from sqlalchemy import text
 from tms.api import errors
 from tms.api.deps import DB
 from tms.api.routes import (
-    auth, consignments, deliveries, drivers, fleet, pod_files, printing, runs, vehicles,
-    wms_events,
+    auth, consignments, deliveries, drivers, fleet, location, pod_files, printing, runs,
+    vehicles, wms_events,
 )
 
 logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
@@ -32,6 +32,7 @@ v1.include_router(printing.router)
 v1.include_router(deliveries.router)
 v1.include_router(pod_files.router)
 v1.include_router(fleet.router)
+v1.include_router(location.router)
 
 
 @v1.get("/health", tags=["system"])
```

Create `api/tms/api/routes/location.py`:
```python
"""Live location: the driver's phone says where the truck is while the run is
on the road; the office sees each truck's latest position."""
from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Response
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from tms.api.deps import DB, Principal, require
from tms.services import location

router = APIRouter(tags=["location"])


class PositionIn(BaseModel):
    """As the phone has it, in decimal strings ("-37.562210"); more than six
    places is rounded, never refused."""

    model_config = ConfigDict(extra="forbid")
    at: AwareDatetime
    latitude: Decimal = Field(ge=-90, le=90)
    longitude: Decimal = Field(ge=-180, le=180)
    accuracy_m: Decimal = Field(ge=0)

    @field_validator("accuracy_m")
    @classmethod
    def _close_enough(cls, value: Decimal) -> Decimal:
        if value > location.MAX_ACCURACY_M:
            raise ValueError("a position rougher than 1,000 m isn't kept")
        return value


@router.post("/runs/{id}/position", status_code=204)
def send_position(id: int, body: PositionIn, db: DB,
                  who: Principal = require("deliveries:write")):
    """Where the run's phone is now, about every 30 seconds while the run is
    on the road. Not a confirmation: no message_id, nothing queued, and a
    lost one is simply followed by the next."""
    location.record(db, who, id, at=body.at, latitude=body.latitude,
                    longitude=body.longitude, accuracy_m=body.accuracy_m)
    db.commit()
    return Response(status_code=204)
```

Create `api/tms/services/location.py`:
```python
"""Live location: where each truck on the road is, from its driver's phone.
Shared only while a run is on the road (from Start until it is finished),
and kept only for the retention period: positions are personal information,
not business history, so they never go in a consignment's history."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tms.api.errors import Conflict, Refused
from tms.models import Position
from tms.services import deliveries
from tms.services.deliveries import MICRO, TENTH

# the phone sends one about every 30 s; more often than this is a fault
MIN_GAP = timedelta(seconds=10)
# a fix rougher than this would put the truck in the wrong suburb
MAX_ACCURACY_M = Decimal(1000)

NOT_SHARED = {"planned": ("run_not_started", "hasn't left yet"),
              "done": ("run_done", "is finished"),
              "cancelled": ("run_cancelled", "was cancelled")}


def record(db: Session, who, run_id: int, *, at: datetime, latitude: Decimal,
           longitude: Decimal, accuracy_m: Decimal) -> Position:
    """Where the run's phone is now. Only for a run on the road: before Start
    and after it is finished nothing is kept. The time is the phone's, but
    never later than now."""
    run = deliveries.own_run(db, who, run_id)
    if run.status in NOT_SHARED:
        code, said = NOT_SHARED[run.status]
        raise Conflict(code, f"{run.number} {said}, so where it is isn't shared")
    now = datetime.now(UTC)
    last = db.execute(select(func.max(Position.received_at))
                      .where(Position.run_id == run.id)).scalar()
    if last is not None and now - last < MIN_GAP:
        raise Refused(429, "too_often", "one position every 10 seconds is plenty")
    row = Position(run_id=run.id, driver_id=run.driver_id, vehicle_id=run.vehicle_id,
                   latitude=latitude.quantize(MICRO), longitude=longitude.quantize(MICRO),
                   accuracy_m=accuracy_m.quantize(TENTH), recorded_at=min(at, now),
                   received_at=now)
    db.add(row)
    db.flush()
    return row
```


**Step 4: Run the tests**

Run: `.venv/bin/pytest tests/test_positions.py -q`
Expected: `8 passed`. Whole suite: `313 passed`.

**Step 5: Commit**

```bash
git add api
git commit -m "Take the phone's position while its run is on the road, and only then"
```

### Task 3: Where each truck on the road was last heard from, for those with `location:read`; and the map's tiles

**Files:**
- Modify: `api/tms/services/sessions.py` (`location:read` for dispatchers and managers), `api/tms/services/api_keys.py`, `api/tms/services/location.py` (`latest`), `api/tms/api/routes/location.py` (`GET /v1/positions`, `GET /v1/map`)
- Test: `api/tests/test_where_trucks_are.py`, `api/tests/test_auth.py`

**Step 1: Write the failing tests**

Change `api/tests/test_auth.py`:
```diff
--- a/api/tests/test_auth.py
+++ b/api/tests/test_auth.py
@@ -11,7 +11,8 @@ def test_sign_in_gives_a_token_that_says_who_you_are(client, dispatcher):
     r = client.get("/v1/auth/me", headers=bearer(session["token"]))
     assert r.status_code == 200
     assert r.json()["kind"] == "user"
-    assert r.json()["scopes"] == ["fleet:read", "consignments:read", "runs:read", "runs:write"]
+    assert r.json()["scopes"] == ["fleet:read", "consignments:read", "runs:read", "runs:write",
+                                  "location:read"]
 
 
 def test_a_wrong_password_says_how_many_tries_are_left(client, dispatcher):
```

Create `api/tests/test_where_trucks_are.py`:
```python
"""The office sees where each truck on the road is, and when it was last
heard from: the latest position of each run on the road, and nothing from
before Start or after the run is finished."""
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import update

from conftest import bearer
from deliveries_samples import delivered, driver_headers, on_the_road
from runs_samples import DAY, assign, consignment, driver, new_run, vehicle
from test_api_keys import _key
from test_positions import send
from tms.models import Position


def latest(client, headers, day=DAY):
    return client.get("/v1/positions", params={"date": day.isoformat()}, headers=headers)


def _older(db, seconds=30):
    db.execute(update(Position).values(received_at=Position.received_at - timedelta(seconds=seconds),
                                       recorded_at=Position.recorded_at - timedelta(seconds=seconds)))
    db.commit()


def test_each_truck_on_the_road_with_where_it_was_last_heard_from(client, db, dispatcher_headers):
    run, _, kim = on_the_road(client, db, dispatcher_headers)
    assert send(client, kim, run["id"], latitude="-37.5", longitude="143.8").status_code == 204
    _older(db)
    assert send(client, kim, run["id"], latitude="-37.56", longitude="143.85",
                accuracy_m="8").status_code == 204
    r = latest(client, dispatcher_headers)
    assert r.status_code == 200, r.text
    (p,) = r.json()["items"]
    assert p | {"at": None, "received_at": None} == {
        "run_id": run["id"], "run_no": run["run_no"],
        "vehicle": {"rego": "TMS041", "type": "van"}, "driver": {"code": "kim", "name": "Kim"},
        "latitude": "-37.56", "longitude": "143.85", "accuracy_m": "8", "at": None,
        "received_at": None}
    assert datetime.fromisoformat(p["received_at"]) > datetime.now(UTC) - timedelta(seconds=10)


def test_a_finished_run_shows_no_position(client, db, dispatcher_headers):
    run, (a,), kim = on_the_road(client, db, dispatcher_headers, n=1)
    send(client, kim, run["id"])
    delivered(client, kim, run["id"], a)
    client.post(f"/v1/runs/{run['id']}/finish", headers=kim, json={"message_id": str(uuid.uuid4())})
    assert latest(client, dispatcher_headers).json() == {"items": [], "total": 0}


def test_only_the_days_runs(client, db, dispatcher_headers):
    run, _, kim = on_the_road(client, db, dispatcher_headers)
    send(client, kim, run["id"])
    assert latest(client, dispatcher_headers, DAY + timedelta(days=1)).json()["total"] == 0


def test_two_trucks_each_with_its_own(client, db, dispatcher_headers):
    run, _, kim = on_the_road(client, db, dispatcher_headers)
    vehicle(db, rego="TMS052")
    driver(db, code="mel", name="Mel")
    other = new_run(client, dispatcher_headers, rego="TMS052", code="mel")
    assign(client, dispatcher_headers, other["id"], consignment(db, ref="0080099999").id)
    client.post(f"/v1/runs/{other['id']}/depart", headers=dispatcher_headers)
    send(client, kim, run["id"], latitude="-37.1")
    send(client, driver_headers(client, code="mel"), other["id"], latitude="-37.2")
    items = latest(client, dispatcher_headers).json()["items"]
    assert [(p["vehicle"]["rego"], p["latitude"]) for p in items] == [("TMS041", "-37.1"),
                                                                      ("TMS052", "-37.2")]


def test_who_may_see_where_the_trucks_are(client, db, dispatcher_headers, manager_headers,
                                          capsys):
    run, _, kim = on_the_road(client, db, dispatcher_headers)
    send(client, kim, run["id"])
    assert latest(client, manager_headers).status_code == 200
    # not a driver, and a system only with a key that says so
    assert latest(client, kim).status_code == 403
    assert latest(client, bearer(_key(capsys, "erp", "runs:read"))).status_code == 403
    assert latest(client, bearer(_key(capsys, "tracker", "location:read"))).status_code == 200


def test_the_map_tiles_as_set_up(client, db, dispatcher_headers, monkeypatch):
    from tms.config import get_settings

    assert client.get("/v1/map", headers=dispatcher_headers).json() == {
        "tile_url": None, "attribution": None}
    monkeypatch.setattr(get_settings(), "map_tile_url", "/tiles/{z}/{x}/{y}.png")
    monkeypatch.setattr(get_settings(), "map_attribution", "© OpenStreetMap contributors")
    assert client.get("/v1/map", headers=dispatcher_headers).json() == {
        "tile_url": "/tiles/{z}/{x}/{y}.png", "attribution": "© OpenStreetMap contributors"}
```


**Step 2: Run them to see them fail**

Run: `.venv/bin/pytest tests/test_where_trucks_are.py tests/test_auth.py -q`
Expected: the six new tests fail (`404 Not Found` for `/v1/positions` and `/v1/map`, and the `tracker` key can't be made yet: `location:read` isn't a scope a key may have), and `test_sign_in_gives_a_token_that_says_who_you_are` fails: the dispatcher has no `location:read` yet.

**Step 3: Implement**

Change `api/tms/api/routes/location.py`:
```diff
--- a/api/tms/api/routes/location.py
+++ b/api/tms/api/routes/location.py
@@ -2,12 +2,15 @@
 on the road; the office sees each truck's latest position."""
 from __future__ import annotations
 
+import datetime as dt
 from decimal import Decimal
 
-from fastapi import APIRouter, Response
+from fastapi import APIRouter, Query, Response
 from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator
 
 from tms.api.deps import DB, Principal, require
+from tms.api.schemas import Page
+from tms.config import get_settings
 from tms.services import location
 
 router = APIRouter(tags=["location"])
@@ -41,3 +44,49 @@ def send_position(id: int, body: PositionIn, db: DB,
                     longitude=body.longitude, accuracy_m=body.accuracy_m)
     db.commit()
     return Response(status_code=204)
+
+
+class TruckAt(BaseModel):
+    run_id: int
+    run_no: str
+    vehicle: dict
+    driver: dict
+    # decimal strings, as sent
+    latitude: str
+    longitude: str
+    accuracy_m: str
+    # when the phone took it, and when it arrived ("last heard")
+    at: dt.datetime
+    received_at: dt.datetime
+
+
+def _plain(d: Decimal) -> str:
+    return format(d.normalize(), "f")
+
+
+@router.get("/positions", response_model=Page[TruckAt])
+def where_trucks_are(db: DB, day: dt.date = Query(alias="date"),
+                     who: Principal = require("location:read")):
+    """The latest position of each of the day's runs on the road, by run.
+    Before Start and once a run is finished, it has none."""
+    rows = location.latest(db, day)
+    return Page(items=[TruckAt(
+        run_id=run.id, run_no=run.number,
+        vehicle={"rego": run.vehicle.rego, "type": run.vehicle.type},
+        driver={"code": run.driver.code, "name": run.driver.name},
+        latitude=_plain(p.latitude), longitude=_plain(p.longitude),
+        accuracy_m=_plain(p.accuracy_m), at=p.recorded_at, received_at=p.received_at)
+        for run, p in rows], total=len(rows))
+
+
+class MapOut(BaseModel):
+    # a raster tile address with {z}, {x} and {y}, or null: no map, a list only
+    tile_url: str | None
+    attribution: str | None
+
+
+@router.get("/map", response_model=MapOut)
+def map_tiles(who: Principal = require("location:read")):
+    """Where the desktop's map tiles come from (TMS_MAP_TILE_URL)."""
+    s = get_settings()
+    return MapOut(tile_url=s.map_tile_url or None, attribution=s.map_attribution or None)
```

Change `api/tms/services/api_keys.py`:
```diff
--- a/api/tms/services/api_keys.py
+++ b/api/tms/services/api_keys.py
@@ -14,7 +14,7 @@ from tms.models import ApiKey
 PREFIX = "tms_k."
 # what a key may be given; a person's role gives the rest
 SCOPES = ("printing:write", "runs:read", "runs:write", "consignments:read", "fleet:read",
-          "deliveries:read", "deliveries:write")
+          "deliveries:read", "deliveries:write", "location:read")
 
 
 def _hash(raw: str) -> str:
```

Change `api/tms/services/location.py`:
```diff
--- a/api/tms/services/location.py
+++ b/api/tms/services/location.py
@@ -4,14 +4,14 @@ and kept only for the retention period: positions are personal information,
 not business history, so they never go in a consignment's history."""
 from __future__ import annotations
 
-from datetime import UTC, datetime, timedelta
+from datetime import UTC, date, datetime, timedelta
 from decimal import Decimal
 
 from sqlalchemy import func, select
 from sqlalchemy.orm import Session
 
 from tms.api.errors import Conflict, Refused
-from tms.models import Position
+from tms.models import Position, Run
 from tms.services import deliveries
 from tms.services.deliveries import MICRO, TENTH
 
@@ -46,3 +46,16 @@ def record(db: Session, who, run_id: int, *, at: datetime, latitude: Decimal,
     db.add(row)
     db.flush()
     return row
+
+
+def latest(db: Session, day: date) -> list[tuple[Run, Position]]:
+    """Each of the day's runs on the road, with the latest position its phone
+    sent. A run not on the road shows none, whatever was kept from it."""
+    # the newest row of each run is its latest: rows are numbered as they arrive
+    newest = (select(func.max(Position.id)).join(Run, Run.id == Position.run_id)
+              .where(Run.day == day, Run.status == "departed").group_by(Position.run_id))
+    rows = db.execute(select(Position).where(Position.id.in_(newest))
+                      .order_by(Position.run_id)).scalars().all()
+    runs = {r.id: r for r in db.execute(select(Run).where(
+        Run.id.in_([p.run_id for p in rows]))).scalars()}
+    return [(runs[p.run_id], p) for p in rows]
```

Change `api/tms/services/sessions.py`:
```diff
--- a/api/tms/services/sessions.py
+++ b/api/tms/services/sessions.py
@@ -24,11 +24,13 @@ REFRESH_TTL = timedelta(days=14)
 DRIVER_PREFIX = "tms_d."
 DRIVER_TTL_SECONDS = 12 * 3600
 
-# What each desktop role may do.
+# What each desktop role may do. location:read is where the trucks on the
+# road are: personal information about the drivers, so a key needs it named.
 ROLE_SCOPES: dict[str, list[str]] = {
     "admin": ["*"],
-    "manager": ["fleet:read", "fleet:write", "consignments:read", "runs:read", "runs:write"],
-    "dispatcher": ["fleet:read", "consignments:read", "runs:read", "runs:write"],
+    "manager": ["fleet:read", "fleet:write", "consignments:read", "runs:read", "runs:write",
+                "location:read"],
+    "dispatcher": ["fleet:read", "consignments:read", "runs:read", "runs:write", "location:read"],
 }
 ROLES = tuple(ROLE_SCOPES)
 # A driver may see the fleet, and read and record the deliveries on their own
```


The latest position of each run is its newest row (`max(id)`): rows are numbered as they arrive, and this avoids `DISTINCT ON`, whose SQLAlchemy spelling changed in 2.1.

**Step 4: Run the tests**

Run: `.venv/bin/pytest tests/test_where_trucks_are.py tests/test_auth.py tests/test_api_keys.py -q`
Expected: `24 passed`. Whole suite: `319 passed`.

**Step 5: Commit**

```bash
git add api
git commit -m "Show the office where each truck on the road was last heard from"
```

### Task 4: ETAs from the stop order and the driver's own pace

**Files:**
- Create: `api/tms/services/eta.py`
- Modify: `api/tms/api/routes/runs.py` (`estimate` on each run)
- Test: `api/tests/test_eta.py`

**Step 1: Write the failing tests**

Create `api/tests/test_eta.py`:
```python
"""ETAs from the stop order and the driver's average time per stop so far,
labelled as estimates. No routing service: distance doesn't come into it."""
from datetime import UTC, datetime, timedelta

from sqlalchemy import update

from deliveries_samples import delivered, failed, on_the_road, planned_run
from tms.models import Run
from tms.services.eta import estimate

T = datetime(2026, 10, 14, 9, 0, tzinfo=UTC)
MIN = timedelta(minutes=1)
TWENTY = 20 * MIN


def at(minutes: int) -> datetime:
    return T + minutes * MIN


def test_before_the_first_stop_each_stop_takes_the_default():
    e = estimate(departed_at=T, done_at=[], left=[31, 32], now=at(5), default=TWENTY)
    assert (e.basis, e.per_stop) == ("default", TWENTY)
    assert e.stops == [(31, at(20)), (32, at(40))]
    # the drive back counts as one stop more
    assert e.back_at_depot == at(60)


def test_after_a_stop_or_two_the_drivers_own_average():
    e = estimate(departed_at=T, done_at=[at(30), at(60)], left=[33, 34], now=at(61),
                 default=TWENTY)
    assert (e.basis, e.per_stop) == ("average", 30 * MIN)
    assert e.stops == [(33, at(90)), (34, at(120))]
    assert e.back_at_depot == at(150)


def test_a_driver_running_late_is_never_due_before_now():
    e = estimate(departed_at=T, done_at=[at(10)], left=[32, 33], now=at(45), default=TWENTY)
    assert e.per_stop == 10 * MIN
    assert e.stops == [(32, at(45)), (33, at(55))]
    assert e.back_at_depot == at(65)


def test_every_stop_done_leaves_the_drive_back():
    e = estimate(departed_at=T, done_at=[at(25), at(50)], left=[], now=at(52), default=TWENTY)
    assert e.stops == [] and e.back_at_depot == at(75)


def test_quick_drops_still_take_a_minute_each():
    e = estimate(departed_at=T, done_at=[T + timedelta(seconds=20)], left=[32], now=at(1),
                 default=TWENTY)
    assert e.per_stop == MIN


# ---- on the run, as the desktop reads it ---------------------------------------

def test_a_run_on_the_road_carries_its_estimates(client, db, dispatcher_headers):
    run, (a, b, c), kim = on_the_road(client, db, dispatcher_headers, n=3)
    db.execute(update(Run).values(departed_at=datetime.now(UTC) - 40 * MIN))
    db.commit()
    delivered(client, kim, run["id"], a, at=(datetime.now(UTC) - 20 * MIN).isoformat())
    failed(client, kim, run["id"], b)
    out = client.get(f"/v1/runs/{run['id']}", headers=dispatcher_headers).json()["estimate"]
    # two stops in 40 minutes, the one that couldn't be delivered too
    assert (out["basis"], out["minutes_per_stop"]) == ("average", 20)
    (nxt,) = out["stops"]
    assert (nxt["consignment_id"], nxt["sequence"]) == (c.id, 3)
    due = datetime.fromisoformat(nxt["at"])
    assert abs(due - (datetime.now(UTC) + 20 * MIN)) < MIN
    back = datetime.fromisoformat(out["back_at_depot"]) - due
    assert abs(back - 20 * MIN) < timedelta(seconds=1)


def test_a_run_not_on_the_road_has_none(client, db, dispatcher_headers):
    run, _ = planned_run(client, db, dispatcher_headers)
    assert client.get(f"/v1/runs/{run['id']}", headers=dispatcher_headers).json()["estimate"] is None
```


**Step 2: Run them to see them fail**

Run: `.venv/bin/pytest tests/test_eta.py -q`
Expected: an error while collecting: `ModuleNotFoundError: No module named 'tms.services.eta'`.

**Step 3: Implement**

Change `api/tms/api/routes/runs.py`:
```diff
--- a/api/tms/api/routes/runs.py
+++ b/api/tms/api/routes/runs.py
@@ -11,8 +11,9 @@ from sqlalchemy import func, select, update
 
 from tms.api.deps import DB, Principal, require
 from tms.api.schemas import Dec, Page
+from tms.config import get_settings
 from tms.models import Consignment, OutboundCall, Pod, PrintJob, Run
-from tms.services import fleet, records, runs
+from tms.services import eta, fleet, records, runs
 from tms.services.load import consignment_totals
 
 router = APIRouter(tags=["runs"])
@@ -93,6 +94,23 @@ class ShippingOut(BaseModel):
     failed: int = 0
 
 
+class StopEta(BaseModel):
+    consignment_id: int
+    sequence: int
+    at: dt.datetime
+
+
+class EstimateOut(BaseModel):
+    """Estimates, and to be shown as such: from the stop order and the
+    driver's average time per stop so far on this run."""
+
+    # average (from the stops done so far) or default (none done yet)
+    basis: str
+    minutes_per_stop: int
+    stops: list[StopEta]
+    back_at_depot: dt.datetime
+
+
 class RunOut(BaseModel):
     id: int
     run_no: str
@@ -110,6 +128,22 @@ class RunOut(BaseModel):
     shipping: ShippingOut
     # the latest pre-start of its truck for it: { id, at, result }, or null
     prestart: dict | None = None
+    # while it is on the road: when it should reach each stop left, and the depot
+    estimate: EstimateOut | None = None
+
+
+def run_estimate(run: Run) -> EstimateOut | None:
+    if run.status != "departed" or run.departed_at is None:
+        return None
+    left = [s for s in run.stops if s.outcome is None]
+    e = eta.estimate(departed_at=run.departed_at,
+                     done_at=[s.outcome_at for s in run.stops if s.outcome_at is not None],
+                     left=[s.consignment_id for s in left], now=dt.datetime.now(dt.UTC),
+                     default=dt.timedelta(minutes=get_settings().eta_minutes_per_stop))
+    return EstimateOut(basis=e.basis, minutes_per_stop=round(e.per_stop.total_seconds() / 60),
+                       stops=[StopEta(consignment_id=s.consignment_id, sequence=s.sequence, at=due)
+                              for s, (_, due) in zip(left, e.stops)],
+                       back_at_depot=e.back_at_depot)
 
 
 def stop_consignment(c: Consignment) -> StopConsignment:
@@ -154,6 +188,7 @@ def run_out(db, run: Run) -> RunOut:
             last_error=sheet.last_error, created_at=sheet.created_at,
             printed_at=sheet.printed_at),
         shipping=ShippingOut(**calls),
+        estimate=run_estimate(run),
     )
 
 
```

Create `api/tms/services/eta.py`:
```python
"""When a run should reach each stop left, and the depot: estimates from the
stop order and the driver's average time per stop so far on the run, from
leaving the depot to the latest stop done. No routing service and no
distances: the average already has the driving in it. Until the first stop
is done, a stop takes TMS_ETA_MINUTES_PER_STOP."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

# however quick the drops, nothing is due sooner than this apart
SHORTEST = timedelta(minutes=1)


@dataclass(frozen=True)
class Estimate:
    # average (from the stops done) or default (none done yet)
    basis: str
    per_stop: timedelta
    # each stop left, in order: (consignment id, when it should be reached)
    stops: list[tuple[int, datetime]]
    # the drive back counts as one stop more
    back_at_depot: datetime


def estimate(*, departed_at: datetime, done_at: list[datetime], left: list[int], now: datetime,
             default: timedelta) -> Estimate:
    """`done_at`: when each stop done so far was done (delivered or not);
    `left`: the stops still to do, in order. Nothing is due before `now`: a
    driver running late is due now, and every stop after moves with it."""
    if done_at:
        last = max(done_at)
        per, basis = (last - departed_at) / len(done_at), "average"
    else:
        last, per, basis = departed_at, default, "default"
    per = max(per, SHORTEST)
    due = max(last + per, now)
    stops = []
    for consignment_id in left:
        stops.append((consignment_id, due))
        due += per
    return Estimate(basis=basis, per_stop=per, stops=stops, back_at_depot=due)
```


**Step 4: Run the tests**

Run: `.venv/bin/pytest tests/test_eta.py -q`
Expected: `7 passed`. Whole suite: `326 passed`.

**Step 5: Commit**

```bash
git add api
git commit -m "Estimate each stop left and the trip back from the driver's own pace"
```

### Task 5: What just happened

**Files:**
- Create: `api/tms/services/feed.py`, `api/tms/api/routes/feed.py`
- Modify: `api/tms/api/app.py`
- Test: `api/tests/test_feed.py`

**Step 1: Write the failing tests**

Create `api/tests/test_feed.py`:
```python
"""What just happened: the day as the office hears it, newest first, from the
records already kept (nothing new is written for it)."""
import uuid

from deliveries_samples import delivered, driver_headers, failed, on_the_road, prestart, planned_run
from fleet_samples import record


def feed(client, headers, **params):
    r = client.get("/v1/feed", params=params, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["items"]


def test_a_run_from_packed_to_back_at_the_depot(client, db, dispatcher_headers):
    run, (a, b), kim = on_the_road(client, db, dispatcher_headers)
    delivered(client, kim, run["id"], a)
    failed(client, kim, run["id"], b, reason="closed")
    client.post(f"/v1/runs/{run['id']}/finish", headers=kim, json={"message_id": str(uuid.uuid4())})
    items = feed(client, dispatcher_headers)
    assert [i["kind"] for i in items] == ["finished", "failed", "delivered", "left", "packed",
                                          "packed"]
    done, couldnt, dropped, left, packed = items[:5]
    assert (done["run_no"], done["driver"], done["rego"]) == (run["run_no"], "Kim", "TMS041")
    assert (couldnt["sequence"], couldnt["customer"], couldnt["reason"]) == (2, "Customer 1",
                                                                             "closed")
    assert (dropped["sequence"], dropped["customer"], dropped["driver"]) == (1, "Customer 0", "Kim")
    assert (left["run_no"], left["driver"], left["rego"]) == (run["run_no"], "Kim", "TMS041")
    assert (packed["wms_ref"], packed["customer"]) == (b.wms_ref, "Customer 1")
    assert all(i["at"] for i in items)


def test_pre_starts_a_grounding_and_marked_fixed(client, db, dispatcher_headers):
    run, _ = planned_run(client, db, dispatcher_headers)
    kim = driver_headers(client)
    assert prestart(client, kim, run["id"]).status_code == 200
    grounded = prestart(client, kim, run["id"], failed=("Brakes",)).json()
    from tms.models import Vehicle
    record(db, db.get(Vehicle, 1), "fixed", note="New pads", check_id=grounded["check_id"])
    fixed, bad, good = feed(client, dispatcher_headers)[:3]
    assert (fixed["kind"], fixed["rego"], fixed["by"], fixed["note"]) == (
        "fixed", "TMS041", "fleet.mgr", "New pads")
    assert (bad["kind"], bad["result"], bad["failed"], bad["driver"]) == (
        "prestart", "grounded", ["Brakes"], "Kim")
    assert (good["kind"], good["result"], good["failed"], good["rego"]) == (
        "prestart", "passed", [], "TMS041")


def test_only_the_newest(client, db, dispatcher_headers):
    run, (a, b), kim = on_the_road(client, db, dispatcher_headers)
    delivered(client, kim, run["id"], a)
    assert [i["kind"] for i in feed(client, dispatcher_headers, limit=2)] == ["delivered", "left"]


def test_for_the_office_not_the_phone(client, db, dispatcher_headers):
    _, _, kim = on_the_road(client, db, dispatcher_headers)
    assert client.get("/v1/feed", headers=kim).status_code == 403
```


**Step 2: Run them to see them fail**

Run: `.venv/bin/pytest tests/test_feed.py -q`
Expected: `4 failed`, each on `/v1/feed` answering `404`.

**Step 3: Implement**

Change `api/tms/api/app.py`:
```diff
--- a/api/tms/api/app.py
+++ b/api/tms/api/app.py
@@ -6,7 +6,7 @@ from sqlalchemy import text
 from tms.api import errors
 from tms.api.deps import DB
 from tms.api.routes import (
-    auth, consignments, deliveries, drivers, fleet, location, pod_files, printing, runs,
+    auth, consignments, deliveries, drivers, feed, fleet, location, pod_files, printing, runs,
     vehicles, wms_events,
 )
 
@@ -33,6 +33,7 @@ v1.include_router(deliveries.router)
 v1.include_router(pod_files.router)
 v1.include_router(fleet.router)
 v1.include_router(location.router)
+v1.include_router(feed.router)
 
 
 @v1.get("/health", tags=["system"])
```

Create `api/tms/api/routes/feed.py`:
```python
"""What just happened, for the map's feed."""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Query
from pydantic import BaseModel

from tms.api.deps import DB, Principal, require
from tms.services import feed

router = APIRouter(tags=["runs"])


class FeedItem(BaseModel):
    # packed, delivered, failed, left, finished, prestart, fixed
    kind: str
    at: dt.datetime
    run_id: int | None
    run_no: str | None
    rego: str | None
    driver: str | None
    sequence: int | None
    customer: str | None
    wms_ref: str | None
    # failed: closed, refused, damaged, wrong_address
    reason: str | None
    # prestart: passed, failed or grounded, and the items that failed
    result: str | None
    failed: list[str]
    # who recorded it: a pre-start's sender, the fleet manager who marked it fixed
    by: str | None
    note: str | None


class FeedOut(BaseModel):
    items: list[FeedItem]


@router.get("/feed", response_model=FeedOut)
def what_just_happened(db: DB, limit: int = Query(default=20, ge=1, le=100),
                       who: Principal = require("runs:read")):
    """Newest first: consignments packed in Simple WMS, delivered or not,
    runs leaving and finishing, pre-starts and trucks marked fixed."""
    return FeedOut(items=[FeedItem(
        kind=h.kind, at=h.at, run_id=h.run.id if h.run else None,
        run_no=h.run.number if h.run else None, rego=h.rego, driver=h.driver,
        sequence=h.sequence, customer=h.customer, wms_ref=h.wms_ref, reason=h.reason,
        result=h.result, failed=h.failed, by=h.by, note=h.note) for h in feed.recent(db, limit)])
```

Create `api/tms/services/feed.py`:
```python
"""What just happened: the latest of what the office hears, newest first,
read from the records already kept: consignments packed in Simple WMS,
delivered or not, runs leaving and finishing, pre-starts, and trucks marked
fixed. Nothing is written for it, and no position is in it."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from tms.models import (
    Consignment, ConsignmentEvent, Driver, Pod, PrestartCheck, Run, Stop, Vehicle, VehicleRecord,
)


@dataclass
class Happened:
    # packed, delivered, failed, left, finished, prestart, fixed
    kind: str
    at: datetime
    run: Run | None = None
    rego: str | None = None
    driver: str | None = None
    sequence: int | None = None
    customer: str | None = None
    wms_ref: str | None = None
    reason: str | None = None
    # a pre-start: passed, failed or grounded, and the items that failed
    result: str | None = None
    failed: list[str] = field(default_factory=list)
    by: str | None = None
    note: str | None = None


def _runs(db: Session, ids: set[int]) -> dict[int, Run]:
    if not ids:
        return {}
    return {r.id: r for r in db.execute(select(Run).where(Run.id.in_(ids)).options(
        selectinload(Run.vehicle), selectinload(Run.driver))).scalars()}


def _consignments(db: Session, limit: int) -> list[Happened]:
    rows = db.execute(
        select(ConsignmentEvent, Consignment).join(Consignment)
        .where(ConsignmentEvent.kind.in_(("packed", "delivered", "failed")))
        .order_by(ConsignmentEvent.id.desc()).limit(limit)).all()
    pod_ids = [e.detail.get("pod_id") for e, _ in rows if e.detail.get("pod_id")]
    pods = {p.id: p for p in db.execute(select(Pod).where(Pod.id.in_(pod_ids))).scalars()}
    stops = {s.id: s for s in db.execute(select(Stop).where(
        Stop.id.in_([p.stop_id for p in pods.values()]))).scalars()}
    runs = _runs(db, {p.run_id for p in pods.values()})
    out = []
    for e, c in rows:
        pod = pods.get(e.detail.get("pod_id"))
        run = runs.get(pod.run_id) if pod else None
        out.append(Happened(
            kind=e.kind, at=e.at, run=run, rego=run.vehicle.rego if run else None,
            driver=run.driver.name if run else None,
            sequence=stops[pod.stop_id].sequence if pod else None,
            customer=c.customer, wms_ref=c.wms_ref, reason=pod.reason if pod else None))
    return out


def _trips(db: Session, limit: int) -> list[Happened]:
    out = []
    for kind, column in (("left", Run.departed_at), ("finished", Run.finished_at)):
        rows = db.execute(select(Run).where(column.is_not(None)).order_by(column.desc())
                          .limit(limit).options(selectinload(Run.vehicle),
                                                selectinload(Run.driver))).scalars()
        out += [Happened(kind=kind, at=getattr(r, column.key), run=r, rego=r.vehicle.rego,
                         driver=r.driver.name) for r in rows]
    return out


def _checks(db: Session, limit: int) -> list[Happened]:
    checks = db.execute(select(PrestartCheck).order_by(PrestartCheck.id.desc())
                        .limit(limit)).scalars().all()
    trucks = {v.id: v.rego for v in db.execute(select(Vehicle).where(
        Vehicle.id.in_({c.vehicle_id for c in checks}))).scalars()}
    drivers = {d.id: d.name for d in db.execute(select(Driver).where(
        Driver.id.in_({c.driver_id for c in checks if c.driver_id}))).scalars()}
    runs = _runs(db, {c.run_id for c in checks})
    return [Happened(kind="prestart", at=c.recorded_at, run=runs.get(c.run_id),
                     rego=trucks[c.vehicle_id], driver=drivers.get(c.driver_id),
                     result=c.result, failed=[i.label for i in c.items if not i.passed],
                     by=c.recorded_by) for c in checks]


def _fixes(db: Session, limit: int) -> list[Happened]:
    rows = db.execute(select(VehicleRecord, Vehicle.rego).join(Vehicle)
                      .where(VehicleRecord.kind == "fixed")
                      .order_by(VehicleRecord.id.desc()).limit(limit)).all()
    return [Happened(kind="fixed", at=r.recorded_at, rego=rego, by=r.recorded_by, note=r.note)
            for r, rego in rows]


def recent(db: Session, limit: int) -> list[Happened]:
    """The newest `limit` of each kind, merged and cut to `limit`."""
    items = _consignments(db, limit) + _trips(db, limit) + _checks(db, limit) + _fixes(db, limit)
    return sorted(items, key=lambda h: h.at, reverse=True)[:limit]
```


**Step 4: Run the tests**

Run: `.venv/bin/pytest tests/test_feed.py -q`
Expected: `4 passed`. Whole suite: `330 passed`.

**Step 5: Commit**

```bash
git add api
git commit -m "Tell the office what just happened, from the records already kept"
```

### Task 6: The worker forgets positions after the retention period

**Files:**
- Modify: `api/tms/worker.py` (`forget_positions`, run on start and hourly)
- Test: `api/tests/test_retention.py`

**Step 1: Write the failing tests**

Create `api/tests/test_retention.py`:
```python
"""Positions are personal information, not business history: once older than
the retention period (90 days unless TMS_POSITION_RETENTION_DAYS says
otherwise) the worker deletes them, and clears the position on a proof of
delivery. Nothing else about a delivery changes."""
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from deliveries_samples import delivered, on_the_road
from tms.models import ConsignmentEvent, Pod, Position
from tms.worker import forget_positions

HERE = {"latitude": "-37.562210", "longitude": "143.850300", "accuracy_m": "12.0"}


def _position(db, run_id, days_ago: float):
    at = datetime.now(UTC) - timedelta(days=days_ago)
    db.add(Position(run_id=run_id, driver_id=1, vehicle_id=1, latitude=-37.5, longitude=143.8,
                    accuracy_m=10, recorded_at=at, received_at=at))
    db.commit()


def test_what_is_older_than_90_days_is_forgotten(client, db, dispatcher_headers):
    run, (a, b), kim = on_the_road(client, db, dispatcher_headers)
    _position(db, run["id"], 91)
    _position(db, run["id"], 89)
    delivered(client, kim, run["id"], a, position=HERE)
    delivered(client, kim, run["id"], b, position=HERE)
    old, recent = db.execute(select(Pod).order_by(Pod.id)).scalars().all()
    db.execute(update(Pod).where(Pod.id == old.id).values(at=Pod.at - timedelta(days=91)))
    db.commit()
    history = db.execute(select(ConsignmentEvent.id, ConsignmentEvent.detail)).all()

    assert forget_positions(db) == (1, 1)

    left = db.execute(select(Position.recorded_at)).scalars().all()
    assert len(left) == 1 and left[0] > datetime.now(UTC) - timedelta(days=90)
    db.expire_all()
    old, recent = db.get(Pod, old.id), db.get(Pod, recent.id)
    assert (old.latitude, old.longitude, old.accuracy_m) == (None, None, None)
    assert old.signed_name == "Priya" and old.outcome == "delivered"
    assert str(recent.latitude) == "-37.562210"
    # the history was never told where anyone was, and is as it was
    assert db.execute(select(ConsignmentEvent.id, ConsignmentEvent.detail)).all() == history
    # once forgotten, there is nothing more to do
    assert forget_positions(db) == (0, 0)


def test_the_period_is_the_businesss_to_set(client, db, dispatcher_headers, monkeypatch):
    from tms.config import get_settings

    run, _, _ = on_the_road(client, db, dispatcher_headers)
    _position(db, run["id"], 31)
    _position(db, run["id"], 29)
    monkeypatch.setattr(get_settings(), "position_retention_days", 30)
    assert forget_positions(db) == (1, 0)
```


**Step 2: Run them to see them fail**

Run: `.venv/bin/pytest tests/test_retention.py -q`
Expected: an error while collecting: `ImportError: cannot import name 'forget_positions' from 'tms.worker'`.

**Step 3: Implement**

Change `api/tms/worker.py`:
```diff
--- a/api/tms/worker.py
+++ b/api/tms/worker.py
@@ -4,6 +4,9 @@ as `python -m tms.worker`.
 A failed try is tried again after 1 min, 5, 30 and 2 h. The try after the
 2 h wait is the last: if it fails too, the row is `failed` and waits for a
 person (retry-ship on the run, or print again).
+
+Once an hour (and when it starts) it also forgets positions older than the
+retention period.
 """
 from __future__ import annotations
 
@@ -12,17 +15,18 @@ import time
 from datetime import UTC, datetime, timedelta
 
 import httpx
-from sqlalchemy import select
+from sqlalchemy import delete, select, update
 from sqlalchemy.orm import Session
 
 from tms.config import get_settings
 from tms.db import get_sessionmaker
-from tms.models import OutboundCall, PrintJob
+from tms.models import OutboundCall, Pod, Position, PrintJob
 from tms.services import printing, wms_client
 
 log = logging.getLogger("tms.worker")
 
 BACKOFF = (60, 300, 1800, 7200)
+FORGET_EVERY_SECONDS = 3600
 
 
 def retry_later(row, now: datetime, error: str) -> None:
@@ -127,6 +131,24 @@ def send_print_jobs(session: Session, http: httpx.Client, now: datetime | None =
     return tried
 
 
+def forget_positions(session: Session, now: datetime | None = None) -> tuple[int, int]:
+    """Positions are personal information, not business history: those taken
+    longer ago than TMS_POSITION_RETENTION_DAYS are deleted, and the position
+    on a proof of delivery is cleared (the delivery itself stays as it was).
+    Returns how many positions went, and how many deliveries lost theirs."""
+    days = get_settings().position_retention_days
+    cutoff = (now or clock()) - timedelta(days=days)
+    gone = session.execute(delete(Position).where(Position.recorded_at < cutoff)).rowcount
+    cleared = session.execute(
+        update(Pod).where(Pod.at < cutoff, Pod.latitude.is_not(None))
+        .values(latitude=None, longitude=None, accuracy_m=None)).rowcount
+    session.commit()
+    if gone or cleared:
+        log.info("forgot %d positions, and where %d deliveries were, older than %d days",
+                 gone, cleared, days)
+    return gone, cleared
+
+
 def wait_for_schema(session: Session, timeout: float = 120, poll: float = 2) -> None:
     """Block until the api container has run the migrations."""
     from sqlalchemy.exc import ProgrammingError
@@ -170,8 +192,16 @@ def main() -> None:
     with make_session() as session:
         wait_for_schema(session)
     log.info("worker started, polling every %ss", settings.worker_poll_seconds)
+    forget_next = 0.0
     with httpx.Client() as http:
         while True:
+            if time.monotonic() >= forget_next:
+                try:
+                    with make_session() as session:
+                        forget_positions(session)
+                except Exception:  # tried again in an hour; the queues go on
+                    log.exception("forgetting old positions failed")
+                forget_next = time.monotonic() + FORGET_EVERY_SECONDS
             try:
                 with make_session() as session:
                     done = run_once(session, http)
```


**Step 4: Run the tests**

Run: `.venv/bin/pytest tests/test_retention.py -q`
Expected: `2 passed`. Whole suite: `caffeinate -i .venv/bin/pytest -q`: `332 passed`.

**Step 5: Commit**

```bash
git add api
git commit -m "Forget positions, and where each delivery was, after the retention period"
```

### Task 7: `docs/api.md`

**Files:**
- Modify: `docs/api.md` (the exception to "never deleted", `location:read`, `estimate` on a run, the new sections Live location and What just happened, a key's scopes)

**Step 1: The reference**

Change `docs/api.md`:
````diff
--- a/docs/api.md
+++ b/docs/api.md
@@ -36,16 +36,23 @@ are at `/docs`.
   stops taken off, never deleted. A consignment's history is append-only, and
   so are pre-start checks and what a fleet manager records about a truck: a
   mistake is put right by a newer record.
+- **Except positions.** Where a truck was (live location, and the position on
+  a proof of delivery) is personal information, not business history: it is
+  never put in a consignment's history, and the worker deletes it once it is
+  older than `TMS_POSITION_RETENTION_DAYS` (90 by default). See Live location.
 
 ## Scopes
 
 | Who | Scopes |
 | --- | --- |
 | `admin` | `*` |
-| `manager` | `fleet:read`, `fleet:write`, `consignments:read`, `runs:read`, `runs:write` |
-| `dispatcher` | `fleet:read`, `consignments:read`, `runs:read`, `runs:write` |
+| `manager` | `fleet:read`, `fleet:write`, `consignments:read`, `runs:read`, `runs:write`, `location:read` |
+| `dispatcher` | `fleet:read`, `consignments:read`, `runs:read`, `runs:write`, `location:read` |
 | a driver | `fleet:read`, `deliveries:read`, `deliveries:write` (on their own runs only) |
-| an API key | what it was made with: some of `printing:write`, `runs:read`, `runs:write`, `consignments:read`, `fleet:read`, `deliveries:read`, `deliveries:write` |
+| an API key | what it was made with: some of `printing:write`, `runs:read`, `runs:write`, `consignments:read`, `fleet:read`, `deliveries:read`, `deliveries:write`, `location:read` |
+
+`location:read` is where the trucks on the road are: personal information
+about the drivers, so a key has it only when it is named.
 
 ## Sign in
 
@@ -320,7 +327,8 @@ Each run:
             "unknown_weight": 0, "unknown_volume": 0, "state": "ok", "message": null },
   "run_sheet": null,
   "shipping": { "pending": 0, "done": 0, "failed": 0 },
-  "prestart": null
+  "prestart": null,
+  "estimate": null
 }
 ```
 `status` is `planned`, `departed`, `done` (the driver finished it) or
@@ -345,6 +353,22 @@ ground it (`passed` or `failed`), made after that truck was last grounded and
 last marked fixed. A check that grounded the truck is never the run's
 pre-start, so once the truck is marked fixed it is `null` until a fresh check.
 
+`estimate` is `null` unless the run is on the road (`departed`). Then it is
+when the run should reach each stop still to go, and the depot:
+```json
+{ "basis": "average", "minutes_per_stop": 18,
+  "stops": [{ "consignment_id": 33, "sequence": 3, "at": "2026-10-14T00:40:00Z" }],
+  "back_at_depot": "2026-10-14T00:58:00Z" }
+```
+These are estimates, and are shown as such. A stop takes the driver's average
+so far on this run (from leaving the depot to the latest stop done, delivered
+or not: `basis` `average`); until the first stop is done, it takes
+`TMS_ETA_MINUTES_PER_STOP` (20 unless set: `basis` `default`). Stops follow
+the run's order, one average apart, the first one average after the latest
+stop done (or leaving), and the drive back is one stop more. Nothing is due
+before now: a driver running late is due now, and the rest move with it. No
+routing service and no distances: the average already has the driving in it.
+
 **Rule 3: a grounded or overdue truck does not go on a run.** Making a run
 for it (`POST /v1/runs`), giving a run that truck (`PATCH`), putting a stop on
 its run (`assign`), printing its run sheet while planned, and departing or
@@ -666,6 +690,81 @@ Simple TMS sends no events of its own yet (`consignment.delivered`,
 `consignment.failed`, `run.completed` in the design): they come with the
 first system that subscribes.
 
+## Live location
+
+While a run is on the road, from **Start** until it is finished, the driver's
+phone sends where it is about every 30 seconds, and the office sees each
+truck's latest position and when it was last heard from. Never before Start,
+never after the run is finished, and the phone says on screen that it is
+sharing. Positions are kept `TMS_POSITION_RETENTION_DAYS` (90 unless set),
+then the worker deletes them, and clears the position on each proof of
+delivery older than that too; a delivery itself is kept for good.
+
+### POST /v1/runs/{id}/position
+`deliveries:write`: the run's driver (another driver's run is `404`), or a
+system with a key (a tracker in the truck, say).
+```json
+{ "at": "2026-10-14T00:12:30Z", "latitude": "-37.562210", "longitude": "143.850300",
+  "accuracy_m": "12" }
+```
+→ `204`. Decimal strings, as for a delivery's position; more than six places
+is rounded. `at` is when the phone took it, never later than it arrived.
+Not a confirmation: no `message_id`, and nothing is queued on the phone. A
+lost one is simply followed by the next; with no signal, the phone sends only
+its latest once it is back.
+
+| Answer | When |
+| --- | --- |
+| `409 run_not_started` | the run hasn't left: nothing is shared before Start |
+| `409 run_done`, `409 run_cancelled` | the run is finished or was cancelled: the phone stops |
+| `422` | `accuracy_m` missing, or rougher than 1,000 m |
+| `429 too_often` | another position for the run arrived less than 10 seconds ago |
+
+### GET /v1/positions?date=2026-10-14
+`location:read`. The latest position of each of that day's runs on the road,
+by run, as `{ "items", "total" }`:
+```json
+{ "run_id": 7, "run_no": "R-0007", "vehicle": { "rego": "TMS041", "type": "rigid" },
+  "driver": { "code": "kim", "name": "Kim" },
+  "latitude": "-37.56221", "longitude": "143.8503", "accuracy_m": "12",
+  "at": "2026-10-14T00:12:30Z", "received_at": "2026-10-14T00:12:31.204Z" }
+```
+`received_at` is when it was last heard from. A run that hasn't left, or is
+finished, has none here, whatever was kept from it. There is no endpoint for
+a truck's earlier positions: the office sees where each truck is now.
+
+### GET /v1/map
+`location:read`. Where the desktop's map tiles come from:
+`{ "tile_url": "https://…/{z}/{x}/{y}.png?key=…", "attribution": "© …" }`, from
+`TMS_MAP_TILE_URL` and `TMS_MAP_ATTRIBUTION`, or both `null` when no tiles are
+set up (the desktop then lists each truck's position without a map). The
+tile address is a provider's (with its key, which the browser sees: restrict
+it to your domain at the provider) or a tile server of your own, an `https`
+address or a path on this server; OpenStreetMap's own tile servers are
+refused at start-up, as their policy asks.
+
+## What just happened
+
+### GET /v1/feed?limit=20
+`runs:read`. The latest of what the office hears, newest first (`limit` 1 to
+100, 20 unless given), read from the records already kept:
+```json
+{ "items": [{ "kind": "delivered", "at": "2026-10-14T00:31:07Z",
+  "run_id": 7, "run_no": "R-0007", "rego": "TMS041", "driver": "Kim",
+  "sequence": 3, "customer": "Main Street Chemist", "wms_ref": "0080034521",
+  "reason": null, "result": null, "failed": [], "by": null, "note": null }] }
+```
+
+| `kind` | What | Fields with something in them |
+| --- | --- | --- |
+| `packed` | a consignment came from Simple WMS | `customer`, `wms_ref` |
+| `delivered`, `failed` | a stop delivered, or not (with the `reason`) | the run, `rego`, `driver`, `sequence`, `customer`, `wms_ref` |
+| `left`, `finished` | a run left the depot, or was finished | the run, `rego`, `driver` |
+| `prestart` | a pre-start check: `result` `passed`, `failed` or `grounded` | the run, `rego`, `driver` (or `by`), `failed` |
+| `fixed` | a fleet manager marked a grounded truck fixed | `rego`, `by`, `note` |
+
+Nothing is written for the feed, and no position is in it.
+
 ## From Simple WMS
 
 ### POST /v1/wms/events
@@ -728,7 +827,8 @@ docker compose exec api tms create-api-key --name platen --scopes printing:write
 docker compose exec api tms revoke-api-key --name platen
 ```
 A key may be given some of `printing:write`, `runs:read`, `runs:write`,
-`consignments:read`, `fleet:read`, `deliveries:read`, `deliveries:write`. `GET /v1/auth/me` with a key answers
+`consignments:read`, `fleet:read`, `deliveries:read`, `deliveries:write`,
+`location:read`. `GET /v1/auth/me` with a key answers
 `"kind": "key"`, its name and scopes. A revoked key is a `401` at once; it is
 kept, not deleted.
 
````


**Step 2: Check it against the routes**

Run: `.venv/bin/python -c "from tms.api.app import app; print(sorted(p for p in app.openapi()['paths'] if any(w in p for w in ('position', 'map', 'feed'))))"`
Expected: `['/v1/feed', '/v1/map', '/v1/positions', '/v1/runs/{id}/position']`, each documented above.

**Step 3: Commit**

```bash
git add docs/api.md
git commit -m "Document live location, ETAs and the feed in the API reference"
```

## Part B — the driver app (`apps/driver`)

### Task 8: Sharing the newest position every 30 seconds, with no backlog

**Files:**
- Create: `apps/driver/src/lib/share.ts` (`Sharer`, and `useSharing` for the screens)
- Test: `apps/driver/src/test/share.test.ts`

**Step 1: Write the failing tests**

Create `apps/driver/src/test/share.test.ts`:
```ts
import { ApiError, Unreachable } from "../api/client";
import { EVERY_MS, Sharer, type Sharing } from "../lib/share";

/** A phone's geolocation: the test says where it is, or that it may not say. */
function fakeGeo() {
  let ok: PositionCallback | null = null;
  let no: PositionErrorCallback | null = null;
  const cleared: number[] = [];
  const geo = {
    watchPosition: (a: PositionCallback, b?: PositionErrorCallback | null) => { ok = a; no = b ?? null; return 42; },
    clearWatch: (id: number) => cleared.push(id),
    getCurrentPosition: () => undefined,
  } as unknown as Geolocation;
  return {
    geo, cleared,
    at: (latitude: number, longitude: number, accuracy = 12) =>
      ok!({ coords: { latitude, longitude, accuracy }, timestamp: Date.now() } as GeolocationPosition),
    refuse: () => no!({ code: 1, PERMISSION_DENIED: 1 } as GeolocationPositionError),
  };
}

describe("sharing where the truck is", () => {
  let sent: { path: string; body: Record<string, string> }[];
  let answer: () => Promise<unknown>;
  let states: Sharing[];

  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-10-14T00:10:00Z"));
    sent = [];
    states = [];
    answer = async () => null;
  });
  afterEach(() => vi.useRealTimers());

  function sharer(geo: Geolocation | undefined) {
    const s = new Sharer(7, async (path, body) => { sent.push({ path, body: body as Record<string, string> }); return answer(); },
                         (state) => states.push(state), geo);
    s.start();
    return s;
  }

  it("sends the first position at once, then the newest about every 30 seconds", async () => {
    const g = fakeGeo();
    sharer(g.geo);
    expect(states).toEqual(["waiting"]);
    g.at(-37.5622104, 143.8503);
    await vi.advanceTimersByTimeAsync(0);
    expect(sent).toEqual([{ path: "/v1/runs/7/position", body: {
      at: "2026-10-14T00:10:00.000Z", latitude: "-37.562210", longitude: "143.850300", accuracy_m: "12.0" } }]);
    expect(states.at(-1)).toBe("on");
    g.at(-37.57, 143.86);
    g.at(-37.58, 143.87);
    await vi.advanceTimersByTimeAsync(EVERY_MS);
    expect(sent.map((s) => s.body.latitude)).toEqual(["-37.562210", "-37.580000"]);
    // nothing new: nothing sent
    await vi.advanceTimersByTimeAsync(EVERY_MS);
    expect(sent).toHaveLength(2);
  });

  it("keeps no backlog in a black spot: once back, only the newest goes", async () => {
    const g = fakeGeo();
    sharer(g.geo);
    answer = async () => { throw new Unreachable(); };
    g.at(-37.1, 143.1);
    await vi.advanceTimersByTimeAsync(0);
    g.at(-37.2, 143.2);
    await vi.advanceTimersByTimeAsync(EVERY_MS);
    g.at(-37.3, 143.3);
    answer = async () => null;
    await vi.advanceTimersByTimeAsync(EVERY_MS);
    expect(sent.map((s) => s.body.latitude)).toEqual(["-37.100000", "-37.200000", "-37.300000"]);
    await vi.advanceTimersByTimeAsync(EVERY_MS * 3);
    expect(sent).toHaveLength(3);
  });

  it("leaves out a position too rough to draw", async () => {
    const g = fakeGeo();
    sharer(g.geo);
    g.at(-37.1, 143.1, 1500);
    await vi.advanceTimersByTimeAsync(EVERY_MS);
    expect(sent).toEqual([]);
  });

  it("stops when the office says the run isn't on the road", async () => {
    const g = fakeGeo();
    sharer(g.geo);
    answer = async () => { throw new ApiError(409, { code: "run_done", detail: "R-0007 is finished" }); };
    g.at(-37.1, 143.1);
    await vi.advanceTimersByTimeAsync(0);
    expect(states.at(-1)).toBe("off");
    expect(g.cleared).toEqual([42]);
    g.at(-37.2, 143.2);
    await vi.advanceTimersByTimeAsync(EVERY_MS * 2);
    expect(sent).toHaveLength(1);
  });

  it("says so when the driver hasn't let it, and sends nothing", async () => {
    const g = fakeGeo();
    sharer(g.geo);
    g.refuse();
    expect(states.at(-1)).toBe("denied");
    await vi.advanceTimersByTimeAsync(EVERY_MS);
    expect(sent).toEqual([]);
  });

  it("stops watching when told to, and on a phone that can't tell where it is never starts", async () => {
    const g = fakeGeo();
    const s = sharer(g.geo);
    s.stop();
    expect(g.cleared).toEqual([42]);
    g.at(-37.1, 143.1);
    await vi.advanceTimersByTimeAsync(EVERY_MS);
    expect(sent).toEqual([]);
    states = [];
    sharer(undefined);
    expect(states).toEqual(["off"]);
  });
});
```


**Step 2: Run them to see them fail**

Run: `npx vitest run src/test/share.test.ts`
Expected: `Failed to resolve import "../lib/share"`.

**Step 3: Implement**

Create `apps/driver/src/lib/share.ts`:
```ts
/** Live location: while the run is on the road, the phone tells the office
 * where it is, about every 30 seconds, and the screen says so. Never before
 * Start, never once the run is finished, and only while the app is open: a
 * web app can't share from the background. A position is not a
 * confirmation: nothing is queued, so a black spot leaves no backlog, and
 * once the signal is back only the newest goes. */
import { useEffect, useState } from "react";
import { api, ApiError } from "../api/client";

export const EVERY_MS = 30_000;
/** rougher than this would put the truck in the wrong suburb: the API refuses it too */
export const ROUGHEST_M = 1000;

/** waiting: watching, nothing heard from the phone's GPS yet; on: sharing;
 * denied: the driver hasn't let the app know where it is; off: not sharing */
export type Sharing = "waiting" | "on" | "denied" | "off";

type Send = (path: string, body: unknown) => Promise<unknown>;

export class Sharer {
  private watch: number | null = null;
  private timer: ReturnType<typeof setInterval> | null = null;
  private latest: Record<string, string> | null = null;
  private sent: Record<string, string> | null = null;
  private stopped = false;

  constructor(private runId: number, private send: Send, private onState: (s: Sharing) => void,
              private geo: Geolocation | undefined = typeof navigator === "undefined" ? undefined : navigator.geolocation) {}

  start() {
    if (!this.geo?.watchPosition) { this.onState("off"); return; }
    this.onState("waiting");
    this.watch = this.geo.watchPosition(
      (p) => {
        if (!Number.isFinite(p.coords.accuracy) || p.coords.accuracy > ROUGHEST_M) return;
        const first = this.latest === null;
        // decimal strings, as for a delivery's position: no float goes to the API
        this.latest = { at: new Date(p.timestamp).toISOString(), latitude: p.coords.latitude.toFixed(6),
                        longitude: p.coords.longitude.toFixed(6), accuracy_m: p.coords.accuracy.toFixed(1) };
        if (first) { this.onState("on"); void this.tick(); }
      },
      (e) => { if (e.code === 1) { this.stop(); this.onState("denied"); } },
      { enableHighAccuracy: true, maximumAge: 15_000, timeout: 60_000 },
    );
    this.timer = setInterval(() => void this.tick(), EVERY_MS);
  }

  stop() {
    this.stopped = true;
    if (this.watch !== null) this.geo?.clearWatch(this.watch);
    if (this.timer !== null) clearInterval(this.timer);
    this.watch = this.timer = null;
  }

  /** The newest position, if it hasn't gone already. One lost to a black
   * spot isn't tried again: the next newest goes instead. */
  private async tick() {
    const fix = this.latest;
    if (this.stopped || !fix || fix === this.sent) return;
    this.sent = fix;
    try {
      await this.send(`/v1/runs/${this.runId}/position`, fix);
    } catch (e) {
      // the run isn't on the road (finished, say, from the office): stop
      if (e instanceof ApiError && e.status === 409) { this.stop(); this.onState("off"); }
    }
  }
}

/** Share while `active` (the run on the road, not finished); say how it is going. */
export function useSharing(runId: number, active: boolean): Sharing {
  const [state, setState] = useState<Sharing>("off");
  useEffect(() => {
    if (!active) { setState("off"); return; }
    const s = new Sharer(runId, (path, body) => api.post(path, body), setState);
    s.start();
    return () => s.stop();
  }, [runId, active]);
  return state;
}
```


**Step 4: Run the tests**

Run: `npx vitest run src/test/share.test.ts && npm run typecheck`
Expected: `6 passed`, no type errors. `npm test`: `95 passed`.

**Step 5: Commit**

```bash
git add apps/driver/src/lib/share.ts apps/driver/src/test/share.test.ts
git commit -m "Share the phone's position every 30 seconds while a run is on the road, with no backlog"
```

### Task 9: The run's screens share while the run is on the road, and say so

**Files:**
- Create: `apps/driver/src/pages/OnTheRun.tsx` (the run's screens under one layout route, and the sharing state they read)
- Modify: `apps/driver/src/App.tsx`, `apps/driver/src/ui/kit.tsx` (the chip, and `SharingNote`), `apps/driver/src/pages/RunHome.tsx`, `apps/driver/src/pages/Today.tsx` (the line before Start)
- Test: `apps/driver/src/test/Sharing.test.tsx`

**Step 1: Write the failing tests**

Create `apps/driver/src/test/Sharing.test.tsx`:
```tsx
import { cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { json, onThePhone, renderApp, RUN, signedIn, stubApi } from "./harness";

/** A phone that knows where it is (or won't say), counting what watches it. */
function phone(allowed = true) {
  const watching = { started: 0, cleared: 0 };
  Object.defineProperty(navigator, "geolocation", { configurable: true, value: {
    watchPosition: (ok: PositionCallback, no: PositionErrorCallback) => {
      watching.started += 1;
      setTimeout(() => (allowed
        ? ok({ coords: { latitude: -37.5622, longitude: 143.8503, accuracy: 9 }, timestamp: Date.now() } as GeolocationPosition)
        : no({ code: 1 } as GeolocationPositionError)), 0);
      return 1;
    },
    clearWatch: () => { watching.cleared += 1; },
    getCurrentPosition: () => undefined,
  } });
  return watching;
}

describe("the phone sharing where it is during the run", () => {
  beforeEach(() => { signedIn(); onThePhone(); });
  afterEach(() => {
    vi.unstubAllGlobals();
    delete (navigator as { geolocation?: unknown }).geolocation;
  });

  it("says it is sharing, and does, from one stop's screen to the next", async () => {
    const watching = phone();
    const { calls } = stubApi((method, url) => (method === "POST" ? new Response(null, { status: 204 }) : url === "/v1/runs/7/stops" ? json(200, RUN) : null));
    renderApp("/run/7");
    expect(await screen.findByText("Sharing location")).toBeInTheDocument();
    expect(screen.getByText(/shares where it is with the office about every 30 seconds/)).toBeInTheDocument();
    await waitFor(() => expect(calls.filter((c) => c.url === "/v1/runs/7/position")).toHaveLength(1));
    expect(calls.find((c) => c.url === "/v1/runs/7/position")!.body).toMatchObject({ latitude: "-37.562200", accuracy_m: "9.0" });
    await userEvent.setup().click(screen.getByRole("link", { name: /Start stop 1/ }));
    expect(await screen.findByText("Sharing location")).toBeInTheDocument();
    // the same watch carries on across the run's screens
    expect(watching).toEqual({ started: 1, cleared: 0 });
    cleanup();   // signed out, or the app closed
    expect(watching.cleared).toBe(1);
  });

  it("shares nothing once the run is finished", async () => {
    const watching = phone();
    const done = { ...RUN, status: "done" as const, stops: RUN.stops.map((s) => ({ ...s, outcome: "delivered" as const })) };
    onThePhone(done);
    const { calls } = stubApi(() => json(200, done));
    renderApp("/run/7");
    expect(await screen.findByText("R-0007 is finished.")).toBeInTheDocument();
    expect(screen.getByText("Location off")).toBeInTheDocument();
    expect(watching.started).toBe(0);
    expect(calls.some((c) => c.url === "/v1/runs/7/position")).toBe(false);
  });

  it("says plainly when the driver hasn't let it", async () => {
    phone(false);
    stubApi(() => json(200, RUN));
    renderApp("/run/7");
    expect(await screen.findByText(/Location is off for this app/)).toBeInTheDocument();
    expect(screen.getByText("Location off")).toBeInTheDocument();
  });
});
```


**Step 2: Run them to see them fail**

Run: `npx vitest run src/test/Sharing.test.tsx`
Expected: `2 failed | 1 passed`: no "Sharing location" and no notice yet (the finished run already says "Location off").

**Step 3: Implement**

Change `apps/driver/src/App.tsx`:
```diff
--- a/apps/driver/src/App.tsx
+++ b/apps/driver/src/App.tsx
@@ -1,6 +1,7 @@
 import { BrowserRouter, Navigate, Outlet, Route, Routes } from "react-router-dom";
 import { SessionProvider, useSession } from "./auth/Session";
 import { CantDeliver } from "./pages/CantDeliver";
+import { OnTheRun } from "./pages/OnTheRun";
 import { Proof } from "./pages/Proof";
 import { RunHome } from "./pages/RunHome";
 import { SignIn } from "./pages/SignIn";
@@ -19,10 +20,12 @@ export function AppRoutes() {
       <Route path="/sign-in" element={<SignIn />} />
       <Route element={<RequireDriver />}>
         <Route index element={<Today />} />
-        <Route path="/run/:runId" element={<RunHome />} />
-        <Route path="/run/:runId/stop/:consignmentId" element={<Stop />} />
-        <Route path="/run/:runId/stop/:consignmentId/proof" element={<Proof />} />
-        <Route path="/run/:runId/stop/:consignmentId/cant" element={<CantDeliver />} />
+        <Route path="/run/:runId" element={<OnTheRun />}>
+          <Route index element={<RunHome />} />
+          <Route path="stop/:consignmentId" element={<Stop />} />
+          <Route path="stop/:consignmentId/proof" element={<Proof />} />
+          <Route path="stop/:consignmentId/cant" element={<CantDeliver />} />
+        </Route>
       </Route>
       <Route path="*" element={<Navigate to="/" replace />} />
     </Routes>
```

Create `apps/driver/src/pages/OnTheRun.tsx`:
```tsx
import { createContext, useContext } from "react";
import { Outlet, useParams } from "react-router-dom";
import { useQueueItems, useSession } from "../auth/Session";
import { finished, loadRun } from "../lib/run";
import { useSharing, type Sharing } from "../lib/share";

const SharingCtx = createContext<Sharing>("off");

/** Every screen of a run, under one roof: the phone shares where it is from
 * here while the run is on the road and not finished, the same watch from
 * one stop's screen to the next. Leaving the run's screens (Today, signing
 * out, closing the app) stops it. */
export function OnTheRun() {
  const runId = Number(useParams().runId);
  const { driver } = useSession();
  const items = useQueueItems(runId);
  const run = loadRun(driver!.id, runId);
  const active = !!run && run.status === "departed" && !finished(run, items);
  const sharing = useSharing(runId, active);
  return <SharingCtx.Provider value={sharing}><Outlet /></SharingCtx.Provider>;
}

export function useSharingState(): Sharing {
  return useContext(SharingCtx);
}
```

Change `apps/driver/src/pages/RunHome.tsx`:
```diff
--- a/apps/driver/src/pages/RunHome.tsx
+++ b/apps/driver/src/pages/RunHome.tsx
@@ -6,7 +6,7 @@ import { useQueueItems, useSession } from "../auth/Session";
 import { addressLine, mapsLink } from "../lib/maps";
 import { finished, loadRun, nextStop, outcomes, plural, saveRun } from "../lib/run";
 import { Icon } from "../ui/icons";
-import { Goal, OfflineBanner, Phone, RunTop, WaitingList } from "../ui/kit";
+import { Goal, OfflineBanner, Phone, RunTop, SharingNote, WaitingList } from "../ui/kit";
 
 /** Between stops (DriverOffline.html): the next stop, what is waiting to
  * send, and the run's end. Works from the phone's copy of the run; reloads
@@ -78,6 +78,7 @@ export function RunHome() {
           </section>
         )}
         <WaitingList items={items} onRetry={(id) => void queue.retry(id)} onDismiss={dismiss} />
+        <SharingNote over={over} />
         <div style={{ display: "flex", gap: 8, alignItems: "flex-start", fontSize: 12, color: "var(--color-muted)" }}>
           <Icon name="info" size={16} width={2.2} />
           <span>Your whole run is saved on this phone, so keep going. Each confirmation has its own number, so sending it twice never records it twice.</span>
```

Change `apps/driver/src/pages/Today.tsx`:
```diff
--- a/apps/driver/src/pages/Today.tsx
+++ b/apps/driver/src/pages/Today.tsx
@@ -212,7 +212,8 @@ export function Today() {
         {canStart && (
           <div style={{ display: "flex", gap: 8, fontSize: 12, color: "var(--color-muted)" }}>
             <Icon name="pin" size={16} width={2.2} />
-            <span>Starting asks to use this phone's location. It is kept with each drop, and only while you're on a run.</span>
+            <span>Starting shares this phone's location with the office about every 30 seconds until you finish the run,
+              and keeps it with each drop. Never outside a run.</span>
           </div>
         )}
       </div>}
```

Change `apps/driver/src/ui/kit.tsx`:
```diff
--- a/apps/driver/src/ui/kit.tsx
+++ b/apps/driver/src/ui/kit.tsx
@@ -6,7 +6,7 @@ import type { Outcome, RunStops } from "../api/types";
 import { waitingRows, type QueueItem } from "../lib/queue";
 import { plural } from "../lib/run";
 import { clock } from "../lib/time";
-import { locationAllowed } from "../lib/where";
+import { useSharingState } from "../pages/OnTheRun";
 import { Icon } from "./icons";
 
 export function Phone({ label, children }: { label: string; children: ReactNode }) {
@@ -22,18 +22,17 @@ export function BackTop({ to, label, title, sub }: { to: string; label: string;
   );
 }
 
-/** The run, the truck, the driver and the time, and whether this phone
- * records where each drop happened. Only during a run, and it says so: a
- * finished run records nothing. */
+/** The run, the truck, the driver and the time, and whether this phone is
+ * sharing where it is. Only during a run, and it says so: a finished run
+ * shares nothing. */
 export function RunTop({ run, over = false }: { run: RunStops; over?: boolean }) {
   const [now, setNow] = useState(() => new Date());
-  const [where, setWhere] = useState<string>("unknown");
+  const sharing = useSharingState();
   useEffect(() => {
-    void locationAllowed().then(setWhere);
     const t = setInterval(() => setNow(new Date()), 30_000);
     return () => clearInterval(t);
   }, []);
-  const on = where === "granted" && !over && run.status !== "done";
+  const on = (sharing === "on" || sharing === "waiting") && !over && run.status !== "done";
   return (
     <div className="top">
       <span className="tile"><Icon name="truck" size={22} /></span>
@@ -42,7 +41,24 @@ export function RunTop({ run, over = false }: { run: RunStops; over?: boolean })
         <div className="st">{run.vehicle.rego} · {run.driver.name} · {clock(now)}</div>
       </div>
       <span className={`chip${on ? " on" : ""}`}><Icon name="pin" size={13} width={2.4} />
-        {on ? "Location at drops" : "Location off"}</span>
+        {on ? "Sharing location" : "Location off"}</span>
+    </div>
+  );
+}
+
+/** What the phone does with where it is, said plainly under the run. */
+export function SharingNote({ over }: { over: boolean }) {
+  const sharing = useSharingState();
+  if (over) return null;
+  const text = sharing === "denied"
+    ? "Location is off for this app, so the office can't see where you are. Each drop is still recorded."
+    : sharing === "off"
+      ? "This phone isn't sharing where it is."
+      : "While this run is on the road, this phone shares where it is with the office about every 30 seconds, "
+        + "with the app open. It stops when you finish the run or sign out.";
+  return (
+    <div style={{ display: "flex", gap: 8, alignItems: "flex-start", fontSize: 12, color: "var(--color-muted)" }}>
+      <Icon name="pin" size={16} width={2.2} /><span>{text}</span>
     </div>
   );
 }
```


`RunTop` no longer asks `navigator.permissions`: the chip follows what the sharing is doing, which is what the driver needs to know. `where.ts` keeps `askForLocation` (Today asks once, after Start) and `getPosition` (each drop).

**Step 4: Run the tests and the build**

Run: `npm test && npm run typecheck && npm run build`
Expected: `98 passed`; a `dist/` with `sw.js`. Step 3's tests are unchanged and pass: `RunHome`'s "Location off" once the run is finished, and Today's "is asked for once the run has started, never read before" (the stub has no `watchPosition`, so the run's screens share nothing there).

**Step 5: Commit**

```bash
git add apps/driver
git commit -m "Say on the run's screens that the phone is sharing where it is, and stop when the run is over"
```

## Part C — the dispatcher's desktop (`apps/desktop`)

### Task 10: Live times, the load left, the feed's words, and where each run is drawn

**Files:**
- Create: `apps/desktop/src/lib/live.ts`
- Modify: `apps/desktop/src/api/types.ts` (`Run.estimate`, `TruckAt`, `MapTiles`, `FeedItem`)
- Test: `apps/desktop/src/test/live.test.ts`

**Step 1: Write the failing tests**

Create `apps/desktop/src/test/live.test.ts`:
```ts
import type { FeedItem, Run } from "../api/types";
import { about, ago, DEPOT_GATE, feedLine, heard, loadLeft, spot, toScreen } from "../lib/live";

const local = (h: number, m: number, s = 0) => new Date(2026, 9, 14, h, m, s);

describe("how the map says where and when", () => {
  it("rounds an estimate to five minutes and says it is one", () => {
    expect(about(local(11, 38).toISOString())).toBe("about 11:40 am");
    expect(about(local(14, 12, 40).toISOString())).toBe("about 2:15 pm");
    expect(about(local(9, 2).toISOString())).toBe("about 9:00 am");
  });

  it("says when a truck was last heard from, and when that is a while", () => {
    const now = local(11, 18);
    expect(heard(local(11, 17, 30).toISOString(), now)).toEqual({ text: "30 s ago", stale: false });
    expect(heard(local(11, 14).toISOString(), now)).toEqual({ text: "4 min ago", stale: false });
    expect(heard(local(11, 6).toISOString(), now)).toEqual({ text: "12 min ago", stale: true });
    expect(heard(local(8, 50).toISOString(), now)).toEqual({ text: "2 h ago", stale: true });
    // the desktop's clock a little behind the server's
    expect(heard(local(11, 18, 5).toISOString(), now).text).toBe("just now");
    expect(ago(local(11, 16).toISOString(), now)).toBe("2 min");
    expect(ago(local(7, 10).toISOString(), now)).toBe("4 h");
    expect(ago(local(11, 17, 40).toISOString(), now)).toBe("now");
  });

  it("weighs what is still on the truck: all but what was delivered", () => {
    const card = (weight_kg: string, volume_m3: string) =>
      ({ id: 1, wms_ref: "", customer: "", suburb: null, status: "", packages: 1, cartons: 1, weight_kg, volume_m3 });
    const run = { stops: [
      { sequence: 1, consignment: card("1200", "4.5"), outcome: "delivered" },
      { sequence: 2, consignment: card("940", "5.3"), outcome: "failed" },
      { sequence: 3, consignment: card("1200", "4.5"), outcome: null },
    ] } as unknown as Run;
    expect(loadLeft(run)).toBe("2,140 kg · 9.8 m³");
  });

  it("puts a run's truck along its own road on the drawn map, further as stops are done", () => {
    expect(toScreen([420, 357])).toEqual(DEPOT_GATE);
    const at = (done: number) => spot(0, done, 4);
    expect(at(0)).not.toEqual(at(2));
    // the first route runs the loop round the bottom block and back to the gate
    expect(spot(0, 4, 4)).not.toEqual(DEPOT_GATE);
    // another run takes another road
    expect(spot(1, 1, 4)).not.toEqual(spot(0, 1, 4));
    // a sixth run shares the first one's road, a little to one side
    expect(spot(5, 1, 4)).not.toEqual(spot(0, 1, 4));
  });
});

describe("what just happened, in words", () => {
  const item = (over: Partial<FeedItem>): FeedItem => ({
    kind: "delivered", at: "2026-10-14T00:31:07Z", run_id: 7, run_no: "R-0007", rego: "TMS041", driver: "Kim",
    sequence: 7, customer: "Main Street Chemist", wms_ref: "0080034521", reason: null, result: null, failed: [],
    by: null, note: null, ...over });

  it("says each kind as the design does", () => {
    expect(feedLine(item({}))).toEqual({ text: "Kim delivered stop 7 · Main Street Chemist", tone: "green" });
    expect(feedLine(item({ kind: "failed", reason: "wrong_address" }))).toEqual(
      { text: "Kim couldn't deliver stop 7 · Main Street Chemist · wrong address", tone: "red" });
    expect(feedLine(item({ kind: "packed", run_id: null, run_no: null, driver: null }))).toEqual(
      { text: "0080034521 packed in Simple WMS · Main Street Chemist", tone: "brown" });
    expect(feedLine(item({ kind: "left" })).text).toBe("Kim left on R-0007 in TMS041");
    expect(feedLine(item({ kind: "finished" })).text).toBe("Kim finished R-0007");
    expect(feedLine(item({ kind: "prestart", result: "passed", driver: "Mel", rego: "TMS052" }))).toEqual(
      { text: "Mel passed the pre-start on TMS052", tone: "violet" });
    expect(feedLine(item({ kind: "prestart", result: "failed", failed: ["Mirrors"] }))).toEqual(
      { text: "Kim's pre-start on TMS041: Mirrors failed", tone: "amber" });
    expect(feedLine(item({ kind: "prestart", result: "grounded", rego: "TMS027", failed: ["Lights", "Brakes"] }))).toEqual(
      { text: "TMS027 grounded · Lights and Brakes failed", tone: "red" });
    expect(feedLine(item({ kind: "fixed", rego: "TMS027", by: "leighton" }))).toEqual(
      { text: "TMS027 marked fixed by leighton", tone: "green" });
    // a check sent with a key names who sent it
    expect(feedLine(item({ kind: "prestart", result: "passed", driver: null, by: "tracker" })).text)
      .toBe("tracker passed the pre-start on TMS041");
  });
});
```


The times are built from the desktop's own clock (`new Date(2026, 9, 14, …)`), so the tests pass in any time zone, CI's UTC included.

**Step 2: Run them to see them fail**

Run: `npx vitest run src/test/live.test.ts`
Expected: `Failed to resolve import "../lib/live"`.

**Step 3: Implement**

Change `apps/desktop/src/api/types.ts`:
```diff
--- a/apps/desktop/src/api/types.ts
+++ b/apps/desktop/src/api/types.ts
@@ -146,4 +146,49 @@ export interface Run {
    * for it that didn't ground it, made since the truck was last grounded
    * and last marked fixed; else null */
   prestart?: { id: number; at: string; result: CheckResult } | null;
+  /** while it is on the road: estimates from the stop order and the driver's
+   * average time per stop so far on the run */
+  estimate?: Estimate | null;
+}
+
+export interface Estimate {
+  basis: "average" | "default";
+  minutes_per_stop: number;
+  stops: { consignment_id: number; sequence: number; at: string }[];
+  back_at_depot: string;
+}
+
+/** A run on the road's latest position (GET /v1/positions). */
+export interface TruckAt {
+  run_id: number;
+  run_no: string;
+  vehicle: { rego: string; type: string };
+  driver: { code: string; name: string };
+  latitude: string;
+  longitude: string;
+  accuracy_m: string;
+  at: string;
+  /** when it was last heard from */
+  received_at: string;
+}
+
+/** Where the map tiles come from (GET /v1/map); null: no map, a list only. */
+export interface MapTiles { tile_url: string | null; attribution: string | null }
+
+/** A line of what just happened (GET /v1/feed). */
+export interface FeedItem {
+  kind: "packed" | "delivered" | "failed" | "left" | "finished" | "prestart" | "fixed";
+  at: string;
+  run_id: number | null;
+  run_no: string | null;
+  rego: string | null;
+  driver: string | null;
+  sequence: number | null;
+  customer: string | null;
+  wms_ref: string | null;
+  reason: string | null;
+  result: CheckResult | null;
+  failed: string[];
+  by: string | null;
+  note: string | null;
 }
```

Create `apps/desktop/src/lib/live.ts`:
```ts
/** How the map says where each truck is and when it will be where: the
 * drawn region (region.svg) with each run's truck on a road of its own,
 * further along as its stops are done, as the design has it ("each run's
 * truck drives its route as stops are delivered"). The drawn region is
 * pretend: true positions are for "Where is it". */
import type { FeedItem, Run } from "../api/types";
import { clock } from "./days";
import { plain, sum } from "./decimal";

const FIVE_MIN = 5 * 60_000;

/** An estimate to the nearest five minutes: "about 11:40 am". */
export function about(iso: string): string {
  return `about ${clock(new Date(Math.round(new Date(iso).getTime() / FIVE_MIN) * FIVE_MIN))}`;
}

/** Five minutes without a word: a black spot, or the app closed. */
export const QUIET_MS = 5 * 60_000;

/** "30 s ago", "4 min ago", "2 h ago"; stale once it has been quiet a while. */
export function heard(iso: string, now = new Date()): { text: string; stale: boolean } {
  const ms = now.getTime() - new Date(iso).getTime();
  const s = Math.round(ms / 1000);
  const text = s < 10 ? "just now" : s < 60 ? `${s} s ago`
    : s < 3600 ? `${Math.floor(s / 60)} min ago` : `${Math.floor(s / 3600)} h ago`;
  return { text, stale: ms > QUIET_MS };
}

/** How long ago, for the feed: "now", "2 min", "4 h". */
export function ago(iso: string, now = new Date()): string {
  const m = Math.floor((now.getTime() - new Date(iso).getTime()) / 60_000);
  return m < 1 ? "now" : m < 60 ? `${m} min` : m < 1440 ? `${Math.floor(m / 60)} h` : `${Math.floor(m / 1440)} d`;
}

/** What is still on the truck: every stop not delivered (one that couldn't
 * be is coming back on it). */
export function loadLeft(run: Run): string {
  const on = run.stops.filter((s) => s.outcome !== "delivered").map((s) => s.consignment);
  const kg = plain(sum(on.map((c) => c.weight_kg)));
  const m3 = plain(sum(on.map((c) => c.volume_m3)));
  return `${kg} kg · ${m3} m³`;
}

// ---- the drawn region ---------------------------------------------------------

type XY = [number, number];

/** region.svg draws its ground flat and turns it with this matrix: a point
 * on the ground, to the picture's 1540 × 1000. */
export function toScreen([x, y]: XY): XY {
  return [Math.round((0.866 * x - 0.866 * y + 712) * 10) / 10, Math.round((0.5 * x + 0.5 * y + 104) * 10) / 10];
}

// on the ground: the depot gate on the main road, and a road out for each run
const GATE: XY = [420, 357];
export const DEPOT_GATE = toScreen(GATE);
const ROUTES: XY[][] = [
  [GATE, [645, 357], [645, 671], [195, 671], [195, 357], GATE],   // round the bottom block
  [GATE, [645, 357], [645, 30], [645, 357], GATE],                // north and back
  [GATE, [880, 357], GATE],                                       // east and back
  [GATE, [20, 357], GATE],                                        // west and back
  [GATE, [645, 357], [645, 800], [645, 357], GATE],               // south and back
];
// in the depot yard: a truck loading, or one that can't go
// far enough apart that two labels don't cover each other
const YARD: XY[] = [[250, 315], [370, 315], [490, 315], [600, 250]];

function along(route: XY[], f: number): XY {
  const legs = route.slice(1).map((p, i) => Math.hypot(p[0] - route[i][0], p[1] - route[i][1]));
  let left = Math.min(Math.max(f, 0), 1) * legs.reduce((a, b) => a + b, 0);
  for (let i = 0; i < legs.length; i++) {
    if (left <= legs[i] || i === legs.length - 1) {
      const t = legs[i] ? Math.min(left / legs[i], 1) : 0;
      const [a, b] = [route[i], route[i + 1]];
      return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t];
    }
    left -= legs[i];
  }
  return route[0];
}

/** Where the `index`th run on the road is drawn: its stops spread along its
 * road, the truck half way from the last one done to the next, and the drive
 * back the last leg. A sixth run takes the first one's road, a little aside. */
export function spot(index: number, done: number, total: number): XY {
  const route = ROUTES[index % ROUTES.length];
  const [x, y] = along(route, (done + 0.5) / (total + 1));
  const aside = Math.floor(index / ROUTES.length) * 14;
  return toScreen([x + aside, y + aside]);
}

/** A truck in the depot yard: the `index`th one loading or that can't go. */
export function yard(index: number): XY {
  const [x, y] = YARD[index % YARD.length];
  return toScreen([x, y - Math.floor(index / YARD.length) * 30]);
}

/** The colour each run is told apart by on the map (design/README.md). */
export const RUN_COLOURS = ["#1F8A5B", "#2F6FB8", "#7B5CC4"];

// ---- what just happened -------------------------------------------------------

export type FeedTone = "green" | "red" | "amber" | "violet" | "blue" | "brown";

const REASONS: Record<string, string> = { closed: "closed", refused: "refused", damaged: "damaged",
                                          wrong_address: "wrong address" };

function and(items: string[]): string {
  return items.length <= 1 ? items.join("") : `${items.slice(0, -1).join(", ")} and ${items.at(-1)}`;
}

/** A feed line as the design words it, and its dot. */
export function feedLine(i: FeedItem): { text: string; tone: FeedTone } {
  const who = i.driver ?? i.by ?? "Someone";
  switch (i.kind) {
    case "packed": return { text: `${i.wms_ref} packed in Simple WMS · ${i.customer}`, tone: "brown" };
    case "delivered": return { text: `${who} delivered stop ${i.sequence} · ${i.customer}`, tone: "green" };
    case "failed":
      return { text: `${who} couldn't deliver stop ${i.sequence} · ${i.customer}`
                 + (i.reason ? ` · ${REASONS[i.reason] ?? i.reason}` : ""), tone: "red" };
    case "left": return { text: `${who} left on ${i.run_no} in ${i.rego}`, tone: "blue" };
    case "finished": return { text: `${who} finished ${i.run_no}`, tone: "blue" };
    case "fixed": return { text: `${i.rego} marked fixed by ${i.by}`, tone: "green" };
    case "prestart":
      if (i.result === "grounded") return { text: `${i.rego} grounded · ${and(i.failed)} failed`, tone: "red" };
      if (i.result === "failed") return { text: `${who}'s pre-start on ${i.rego}: ${and(i.failed)} failed`, tone: "amber" };
      return { text: `${who} passed the pre-start on ${i.rego}`, tone: "violet" };
    default: return { text: "", tone: "green" };
  }
}
```


`toScreen` is `region.svg`'s own matrix (`matrix(.866 .5 -.866 .5 712 104)`): the roads are drawn flat at main road `y = 357`, the cross road `x = 645`, the left road `x = 195` and the lower road `y = 671`, and the depot gate is on the main road at `x = 420`.

**Step 4: Run the tests**

Run: `npx vitest run src/test/live.test.ts && npm run typecheck`
Expected: `5 passed`, no type errors. `npm test`: `75 passed`.

**Step 5: Commit**

```bash
git add apps/desktop/src
git commit -m "Word live times, the load left, the feed and where each run is drawn"
```

### Task 11: The day's trucks on the map, and the picked one's card

**Files:**
- Create: `apps/desktop/src/ui/Trucks.tsx` (`TruckMarkers`, `TruckCard`)
- Modify: `apps/desktop/src/pages/MapHome.tsx`, `apps/desktop/src/index.css` (`.world`, `.truck`, `.truckcard`, `.panel`, `dl.kv`), `apps/desktop/src/ui/icons.tsx` (`target`), `apps/desktop/public/region.svg` (its four drawn trucks out)
- Test: `apps/desktop/src/test/Trucks.test.tsx`, `apps/desktop/src/test/MapHome.test.tsx`, `apps/desktop/src/test/harness.tsx` (`location:read` for the dispatcher and the manager)

**Step 1: Write the failing tests**

Change `apps/desktop/src/test/MapHome.test.tsx`:
```diff
--- a/apps/desktop/src/test/MapHome.test.tsx
+++ b/apps/desktop/src/test/MapHome.test.tsx
@@ -22,7 +22,8 @@ describe("the map", () => {
     expect(screen.getByText("21")).toBeInTheDocument();
     // delivered today: the stops the drivers have delivered on today's runs
     expect(screen.getByText("Delivered").nextSibling).toHaveTextContent("3");
-    expect(calls.find((c) => path(c.url) === "/v1/runs")?.url).toMatch(/status=departed%2Cdone/);
+    // every run of the day: on the road, loading, and back
+    expect(calls.find((c) => path(c.url) === "/v1/runs")?.url).not.toMatch(/status=/);
     await userEvent.setup().click(screen.getByRole("link", { name: /Fleet/ }));
     expect(screen.getByTestId("where")).toHaveTextContent("/fleet");
     expect(screen.getByRole("dialog", { name: "Fleet" })).toBeInTheDocument();
```

Create `apps/desktop/src/test/Trucks.test.tsx`:
```tsx
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { Run } from "../api/types";
import { about } from "../lib/live";
import { DISPATCHER, json, path, renderApp, stubApi } from "./harness";

const card = (id: number, customer: string) => ({ id, wms_ref: `00800345${id}`, customer, suburb: "Ballarat", status: "departed",
                                                  packages: 2, cartons: 2, weight_kg: "120", volume_m3: "0.8" });
const minutes = (n: number) => new Date(Date.now() + n * 60_000).toISOString();

const ON_THE_ROAD = {
  id: 7, run_no: "R-0007", date: "2026-10-14", status: "departed",
  vehicle: { rego: "TMS041", type: "rigid", max_kg: "4500", max_m3: "22", active: true, blocked: null },
  driver: { code: "kim", name: "Kim" }, departed_at: minutes(-40), finished_at: null,
  stops: [{ sequence: 1, consignment: card(31, "Hillside Bakery"), outcome: "delivered", outcome_at: minutes(-20) },
          { sequence: 2, consignment: card(32, "Ridge Auto Workshop"), outcome: null },
          { sequence: 3, consignment: card(33, "Two Rivers Vet"), outcome: null }],
  estimate: { basis: "average", minutes_per_stop: 20, back_at_depot: minutes(60),
              stops: [{ consignment_id: 32, sequence: 2, at: minutes(0) }, { consignment_id: 33, sequence: 3, at: minutes(20) }] },
} as unknown as Run;
const LOADING = { ...ON_THE_ROAD, id: 8, run_no: "R-0008", status: "planned", departed_at: null, estimate: null,
                  vehicle: { ...ON_THE_ROAD.vehicle, rego: "TMS052", type: "van" }, driver: { code: "mel", name: "Mel" },
                  stops: ON_THE_ROAD.stops.map((s) => ({ ...s, outcome: null })) } as unknown as Run;
const HEARD = { run_id: 7, run_no: "R-0007", vehicle: { rego: "TMS041", type: "rigid" }, driver: { code: "kim", name: "Kim" },
                latitude: "-37.56", longitude: "143.85", accuracy_m: "9", at: minutes(-0.5), received_at: new Date(Date.now() - 30_000).toISOString() };

function stub(who: object = DISPATCHER, heard: object[] = [HEARD]) {
  return stubApi((_m, url) => {
    if (path(url) === "/v1/runs") return json(200, { items: [ON_THE_ROAD, LOADING], total: 2 });
    if (path(url) === "/v1/positions") return json(200, { items: heard, total: heard.length });
    if (path(url) === "/v1/vehicles") {
      return json(200, { items: [{ rego: "TMS053", type: "van", active: true, state: "grounded",
                                   blocked: { code: "vehicle_grounded", message: "TMS053 is grounded: Brakes failed the pre-start." } }], total: 1 });
    }
    if (path(url) === "/v1/feed") return json(200, { items: [] });
    return json(200, { items: [], total: 0 });
  }, who);
}

describe("the trucks on the map", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("draws each run's truck, and the one on the road with when it was last heard from", async () => {
    stub();
    renderApp("/");
    const truck = await screen.findByRole("button", { name: /R-0007 · Kim/ });
    expect(truck).toHaveAccessibleName("R-0007 · Kim: on the road, last heard 30 s ago");
    expect(screen.getByRole("button", { name: /R-0008 · loading/ })).toHaveAccessibleName("R-0008 · loading: Mel, TMS052");
    // the first on the road is shown to start with, as Main.html has it
    const shown = screen.getByRole("region", { name: "Selected truck" });
    expect(shown).toHaveTextContent("TMS041 · Kim · rigid");
    expect(shown).toHaveTextContent("Run R-0007");
    expect(within(shown).getByText("On the road")).toBeInTheDocument();
    expect(within(shown).getByText("Last heard 30 s ago")).toBeInTheDocument();
    expect(within(shown).getByRole("img", { name: "1 of 3 stops done" })).toBeInTheDocument();
    expect(shown).toHaveTextContent("Next stop2 · Ridge Auto Workshop");
    expect(shown).toHaveTextContent(`Arrives${about(minutes(0))}`);
    expect(shown).toHaveTextContent("Load left240 kg · 1.6 m³");
    expect(shown).toHaveTextContent(`Back at depot${about(minutes(60))}`);
    expect(shown).toHaveTextContent("Times are estimates from the stop order and Kim's average time per stop today.");
    expect(within(shown).getByRole("link", { name: /Where is it/ })).toHaveAttribute("href", "/where?run=7");
    expect(within(shown).getByRole("link", { name: /Open run/ })).toHaveAttribute("href", "/plan");
  });

  it("shows a run still loading, with no times yet", async () => {
    stub();
    renderApp("/");
    await userEvent.setup().click(await screen.findByRole("button", { name: /R-0008 · loading/ }));
    const shown = screen.getByRole("region", { name: "Selected truck" });
    expect(shown).toHaveTextContent("TMS052 · Mel · van");
    expect(within(shown).getByText("Loading")).toBeInTheDocument();
    expect(shown).toHaveTextContent("Not left yet");
    expect(shown).not.toHaveTextContent("Arrives");
    expect(screen.getByRole("button", { name: /R-0008 · loading/ })).toHaveAttribute("aria-pressed", "true");
  });

  it("says when a truck on the road hasn't been heard from for a while, or yet", async () => {
    stub(DISPATCHER, [{ ...HEARD, received_at: new Date(Date.now() - 12 * 60_000).toISOString() }]);
    renderApp("/");
    const shown = await screen.findByRole("region", { name: "Selected truck" });
    await waitFor(() => expect(within(shown).getByText("Last heard 12 min ago")).toHaveClass("p-amber"));
  });

  it("draws a truck that can't go in grey at the depot, in red, for Fleet", async () => {
    stub();
    renderApp("/");
    const grounded = await screen.findByRole("link", { name: /TMS053 · grounded/ });
    expect(grounded).toHaveAttribute("href", "/fleet");
    expect(grounded).toHaveAccessibleName("TMS053 · grounded: TMS053 is grounded: Brakes failed the pre-start.");
  });

  it("asks where trucks are only of someone who may know", async () => {
    const { calls } = stub({ ...DISPATCHER, scopes: ["fleet:read", "consignments:read", "runs:read", "runs:write"] });
    renderApp("/");
    const shown = await screen.findByRole("region", { name: "Selected truck" });
    expect(shown).toHaveTextContent("Run R-0007");
    expect(calls.some((c) => path(c.url) === "/v1/positions")).toBe(false);
    expect(shown).not.toHaveTextContent("Last heard");
    expect(within(shown).queryByRole("link", { name: /Where is it/ })).not.toBeInTheDocument();
  });
});
```

Change `apps/desktop/src/test/harness.tsx`:
```diff
--- a/apps/desktop/src/test/harness.tsx
+++ b/apps/desktop/src/test/harness.tsx
@@ -12,9 +12,9 @@ export interface Call { method: string; url: string; body: unknown }
 type Handler = (method: string, url: string, body: unknown) => Response | null | Promise<Response | null>;
 
 export const DISPATCHER = { kind: "user", id: 1, name: "dee", display_name: "Dee Dispatch", role: "dispatcher",
-                            scopes: ["fleet:read", "consignments:read", "runs:read", "runs:write"] };
+                            scopes: ["fleet:read", "consignments:read", "runs:read", "runs:write", "location:read"] };
 export const MANAGER = { kind: "user", id: 2, name: "fleet.mgr", display_name: "Fran Fleet", role: "manager",
-                         scopes: ["fleet:read", "fleet:write", "consignments:read", "runs:read", "runs:write"] };
+                         scopes: ["fleet:read", "fleet:write", "consignments:read", "runs:read", "runs:write", "location:read"] };
 
 /** Signed in as a dispatcher (or `who`); `more` answers everything else, or it is a 404. */
 export function stubApi(more: Handler = () => null, who: object = DISPATCHER) {
```


**Step 2: Run them to see them fail**

Run: `npx vitest run src/test/Trucks.test.tsx src/test/MapHome.test.tsx`
Expected: the 5 new tests fail (no truck buttons, no **Selected truck** card), and "shows the day's numbers…" fails: the map still asks for `status=departed,done`.

**Step 3: Implement**

Take the four trucks drawn into the picture out of the app's copy of `region.svg`, so only real runs show (`design/screens/region.svg` stays as it is). They are whole lines 79 (grey), 81 (blue), 84 (violet) and 99 (green):
```
cd apps/desktop/public
shasum region.svg                            # ca6b189c5df5217f238346a3ff19e6da72612922
sed -i '' -e '99d' -e '84d' -e '81d' -e '79d' region.svg
shasum region.svg                            # ca89be6bdce638e5df154f8b2e7fee52c7395884
wc -l region.svg                             # 107
cd ../
```
(On Linux, `sed -i` without the `''`.) If the first checksum differs, stop: the picture has changed since this plan.

Change `apps/desktop/src/index.css`:
```diff
--- a/apps/desktop/src/index.css
+++ b/apps/desktop/src/index.css
@@ -71,7 +71,19 @@
   .live { display: flex; align-items: center; gap: 6px; height: 36px; padding: 0 12px; border-radius: 999px; background: #E3F3EA; color: #146B45; font-size: 13px; font-weight: 700; }
   .avatar { width: 44px; height: 44px; border-radius: 50%; border: 0; background: var(--color-green); color: #fff; font-weight: 800; font-size: 14px; cursor: pointer; }
   .stage { position: relative; flex: 1; overflow: hidden; }
-  .stage > img { display: block; width: 100%; max-width: 1540px; height: auto; margin: 0 auto; }
+  /* the drawn region, and what is drawn on it, at the picture's own scale */
+  .world { position: relative; width: 100%; max-width: 1540px; margin: 0 auto; }
+  .world > img { display: block; width: 100%; height: auto; }
+  .truck { position: absolute; transform: translate(-50%, -100%); display: flex; flex-direction: column; align-items: center; gap: 2px; padding: 0; border: 0; background: none; cursor: pointer; text-decoration: none; color: var(--color-ink); }
+  .truck .tag { display: inline-flex; align-items: center; gap: 6px; height: 28px; padding: 0 10px; border-radius: 14px; background: #fff; border: 2px solid #fff; box-shadow: 0 3px 8px rgba(23, 37, 31, 0.18); font-size: 12.5px; font-weight: 800; white-space: nowrap; }
+  .truck[aria-pressed="true"] .tag { border-color: var(--color-green); }
+  .truck.bad .tag { color: var(--color-red-ink); }
+  .truck:hover .tag { border-color: var(--color-line-2); }
+  .truckcard { top: 20px; right: 24px; width: 330px; padding: 18px; border-radius: 18px; box-shadow: var(--shadow-lift); gap: 14px; }
+  .panel { padding: 16px; border-radius: 16px; background: #fff; box-shadow: var(--shadow-soft); display: flex; flex-direction: column; gap: 10px; }
+  dl.kv { margin: 0; display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px 12px; font-size: 13px; }
+  dl.kv dt { color: var(--color-muted); }
+  dl.kv dd { margin: 2px 0 0; font-weight: 700; }
   .ov { position: absolute; }
   .hud { padding: 10px 12px; border-radius: 14px; background: #fff; box-shadow: var(--shadow-soft); text-decoration: none; color: inherit; }
   .hud .eyebrow { font-size: 11px; }
```

Change `apps/desktop/src/pages/MapHome.tsx`:
```diff
--- a/apps/desktop/src/pages/MapHome.tsx
+++ b/apps/desktop/src/pages/MapHome.tsx
@@ -1,12 +1,13 @@
 import { useCallback, useEffect, useState, type FormEvent } from "react";
 import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
 import { api } from "../api/client";
-import type { Page, Run, Vehicle } from "../api/types";
+import type { Page, Run, TruckAt, Vehicle } from "../api/types";
 import { useAuth } from "../auth/AuthContext";
 import { clock, isoDay } from "../lib/days";
 import { cantGo } from "../lib/fleet";
 import { DrawerBoundary } from "../ui/DrawerBoundary";
 import { Icon, Logo } from "../ui/icons";
+import { TruckCard, TruckMarkers } from "../ui/Trucks";
 
 interface Counts { toPlan: number; onRoad: number; delivered: number }
 
@@ -18,15 +19,21 @@ function initials(name: string): string {
   return name.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join("").toUpperCase() || "?";
 }
 
-/** Main.html: the region as a picture, the day's numbers over it, and the
- * drawers. Trucks moving on the map come with live location (step 5). */
+/** Main.html: the region as a picture with the day's trucks on it, the day's
+ * numbers and the picked truck's card over it, and the drawers. */
 export function MapHome() {
-  const { user, signOut } = useAuth();
+  const { user, signOut, can } = useAuth();
+  const canLocate = can("location:read");
   const navigate = useNavigate();
   const { pathname } = useLocation();
   const [counts, setCounts] = useState<Counts | null>(null);
   // trucks grounded or overdue today: a person needs to look
   const [stuck, setStuck] = useState(0);
+  const [blocked, setBlocked] = useState<Vehicle[]>([]);
+  const [runs, setRuns] = useState<Run[]>([]);
+  const [heardFrom, setHeardFrom] = useState<Map<number, TruckAt>>(new Map());
+  // the truck whose card shows: the first on the road until one is picked
+  const [picked, setPicked] = useState<number | null | "none">(null);
   const [now, setNow] = useState(() => new Date());
   const [menu, setMenu] = useState(false);
   const [q, setQ] = useState("");
@@ -35,16 +42,24 @@ export function MapHome() {
     try {
       // to plan: packed, and what came back undelivered; delivered: today's drops
       const [toPlan, onRoad, today] = await Promise.all([
-        count("packed,failed"), count("departed"),
-        api.get<Page<Run>>("/v1/runs", { date: isoDay(new Date()), status: "departed,done" })]);
+        count("packed,failed"), count("departed"), api.get<Page<Run>>("/v1/runs", { date: isoDay(new Date()) })]);
       const delivered = today.items.reduce(
         (n, run) => n + run.stops.filter((s) => s.outcome === "delivered").length, 0);
       setCounts({ toPlan, onRoad, delivered });
+      setRuns(today.items);
     } catch { /* the numbers wait for the next go */ }
     try {
-      setStuck(cantGo((await api.get<Page<Vehicle>>("/v1/vehicles", { date: isoDay(new Date()), active: true })).items));
+      const trucks = (await api.get<Page<Vehicle>>("/v1/vehicles", { date: isoDay(new Date()), active: true })).items;
+      setStuck(cantGo(trucks));
+      setBlocked(trucks.filter((v) => v.active && (v.state === "grounded" || v.state === "overdue")));
     } catch { /* so does this one */ }
-  }, []);
+    if (canLocate) {
+      try {
+        const at = await api.get<Page<TruckAt>>("/v1/positions", { date: isoDay(new Date()) });
+        setHeardFrom(new Map(at.items.map((p) => [p.run_id, p])));
+      } catch { /* the trucks stay where they were last drawn */ }
+    }
+  }, [canLocate]);
 
   useEffect(() => {
     void load();
@@ -57,6 +72,11 @@ export function MapHome() {
     navigate(`/consignments?q=${encodeURIComponent(q)}`);
   }
 
+  // a truck on a run today is drawn with its run, not as one that can't go
+  const onRun = new Set(runs.filter((r) => r.status === "planned" || r.status === "departed").map((r) => r.vehicle.rego));
+  const shown = picked === "none" ? null
+    : runs.find((r) => r.id === picked) ?? runs.find((r) => r.status === "departed") ?? null;
+
   const weekday = new Intl.DateTimeFormat("en-AU", { weekday: "short" }).format(now);
   return (
     <div className="app">
@@ -88,7 +108,11 @@ export function MapHome() {
       </header>
 
       <main className="stage">
-        <img src="/region.svg" alt="The region: the depot with its carrier bay, the roads and customer stops" />
+        <div className="world">
+          <img src="/region.svg" alt="The region: the depot with its carrier bay, the roads and customer stops" />
+          <TruckMarkers runs={runs} heardFrom={heardFrom} blocked={blocked.filter((v) => !onRun.has(v.rego))}
+                        picked={shown?.id ?? null} onPick={setPicked} now={now} />
+        </div>
         <div className="ov" style={{ top: 20, left: 24, width: 318 }}>
           <div style={{ display: "grid", gridTemplateColumns: "repeat(3,minmax(0,1fr))", gap: 8 }}>
             <NavLink to="/plan" className="hud">
@@ -105,6 +129,10 @@ export function MapHome() {
             </div>
           </div>
         </div>
+        {shown && (
+          <TruckCard run={shown} heardFrom={heardFrom.get(shown.id)} canLocate={canLocate} now={now}
+                     onClose={() => setPicked("none")} />
+        )}
         <nav className="ov nav" aria-label="Main drawers" style={{ bottom: 24, left: "50%", transform: "translateX(-50%)" }}>
           <NavLink to="/plan" className={({ isActive }) => (isActive ? "on" : "")}>
             <Icon name="route" />Plan runs{counts && counts.toPlan > 0 && <i>{counts.toPlan}</i>}
```

Create `apps/desktop/src/ui/Trucks.tsx`:
```tsx
/** The trucks on the drawn map (Main.html): each run on the road on a road of
 * its own, the ones loading and the ones that can't go in the depot yard, and
 * the card of the one picked. The buttons are the text alternative too: each
 * says the run, the driver and when it was last heard from. */
import { Link } from "react-router-dom";
import type { Run, TruckAt, Vehicle } from "../api/types";
import { about, heard, loadLeft, RUN_COLOURS, spot, yard } from "../lib/live";
import { Icon } from "./icons";

const at = ([x, y]: [number, number]) => ({ left: `${x / 15.4}%`, top: `${y / 10}%` });

function TruckGlyph({ colour }: { colour: string }) {
  return (
    <svg width="34" height="24" viewBox="0 0 34 24" aria-hidden="true">
      <ellipse cx="17" cy="21" rx="15" ry="3" fill="#17251F" opacity=".15" />
      <rect x="1" y="3" width="20" height="14" rx="2" fill={colour} />
      <path d="M21 7h6.5L33 12.5V17H21Z" fill={colour} opacity=".85" />
      <circle cx="8" cy="18" r="3" fill="#17251F" /><circle cx="26" cy="18" r="3" fill="#17251F" />
    </svg>
  );
}

const done = (r: Run) => r.stops.filter((s) => s.outcome).length;

export function TruckMarkers({ runs, heardFrom, blocked, picked, onPick, now }: {
  runs: Run[]; heardFrom: Map<number, TruckAt>; blocked: Vehicle[]; picked: number | null;
  onPick: (id: number) => void; now: Date;
}) {
  const onRoad = runs.filter((r) => r.status === "departed");
  const loading = runs.filter((r) => r.status === "planned");
  return (
    <>
      {onRoad.map((r, i) => {
        const h = heardFrom.get(r.id);
        const bad = !!r.vehicle.blocked;
        return (
          <button key={r.id} type="button" className={`truck${bad ? " bad" : ""}`} style={at(spot(i, done(r), r.stops.length))}
                  aria-pressed={picked === r.id} onClick={() => onPick(r.id)}
                  aria-label={`${r.run_no} · ${r.driver.name}: on the road${h ? `, last heard ${heard(h.received_at, now).text}` : ""}`}>
            <span className="tag"><span className="dot" style={{ width: 9, height: 9, background: bad ? "var(--color-red)" : RUN_COLOURS[i % 3] }} />
              {r.run_no} · {r.driver.name}</span>
            <TruckGlyph colour={RUN_COLOURS[i % 3]} />
          </button>
        );
      })}
      {loading.map((r, i) => (
        <button key={r.id} type="button" className="truck" style={at(yard(i))} aria-pressed={picked === r.id}
                onClick={() => onPick(r.id)} aria-label={`${r.run_no} · loading: ${r.driver.name}, ${r.vehicle.rego}`}>
          <span className="tag"><span className="dot" style={{ width: 9, height: 9, background: "var(--color-violet)" }} />
            {r.run_no} · loading</span>
          <TruckGlyph colour="#7B5CC4" />
        </button>
      ))}
      {blocked.map((v, i) => (
        <Link key={v.rego} to="/fleet" className="truck bad" style={at(yard(loading.length + i))}
              aria-label={`${v.rego} · ${v.state}: ${v.blocked?.message ?? ""}`}>
          <span className="tag"><span className="dot" style={{ width: 9, height: 9, background: "var(--color-red)" }} />
            {v.rego} · {v.state}</span>
          <TruckGlyph colour="#9AA6A0" />
        </Link>
      ))}
    </>
  );
}

/** The picked run's card: who and what, when it was last heard from, its
 * stops, and the estimates, labelled as such. */
export function TruckCard({ run, heardFrom, canLocate, now, onClose }: {
  run: Run; heardFrom: TruckAt | undefined; canLocate: boolean; now: Date; onClose: () => void;
}) {
  const total = run.stops.length;
  const finished = done(run);
  const next = run.stops.find((s) => !s.outcome);
  const e = run.estimate;
  const nextAt = e && next ? e.stops.find((s) => s.consignment_id === next.consignment.id)?.at : undefined;
  const h = heardFrom ? heard(heardFrom.received_at, now) : null;
  const pill = run.status === "departed" ? <span className="pill p-green">On the road</span>
    : run.status === "planned" ? <span className="pill p-grey">Loading</span>
    : <span className="pill p-grey">Back at the depot</span>;
  return (
    <section className="ov panel truckcard" aria-label="Selected truck">
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <div style={{ width: 44, height: 44, borderRadius: 12, background: "var(--color-tint)", display: "grid", placeItems: "center", color: "var(--color-green)" }}>
          <Icon name="truck" size={24} /></div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 12, color: "var(--color-muted)", fontWeight: 700 }}>{run.vehicle.rego} · {run.driver.name} · {run.vehicle.type}</div>
          <div style={{ fontSize: 18, fontWeight: 800 }}>Run {run.run_no}</div>
        </div>
        <button type="button" className="closebtn" style={{ width: 36, height: 36 }} aria-label="Close" onClick={onClose}>
          <Icon name="close" size={16} width={2.4} /></button>
      </div>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        {pill}
        {run.status === "departed" && canLocate && (h
          ? <span className={h.stale ? "pill p-amber" : "small"}>Last heard {h.text}</span>
          : <span className="small">Not heard from yet</span>)}
        {run.status === "planned" && <span className="small">Not left yet</span>}
      </div>
      <div>
        <div style={{ display: "flex", justifyContent: "space-between", fontSize: 13, marginBottom: 6 }}>
          <b>{finished} of {total} stops</b><span style={{ color: "var(--color-muted)" }}>{total - finished} to go</span></div>
        <div role="img" aria-label={`${finished} of ${total} stops done`}
             style={{ display: "grid", gridTemplateColumns: `repeat(${Math.max(total, 1)},minmax(0,1fr))`, gap: 3 }}>
          {run.stops.map((s) => (
            <span key={s.sequence} style={{ height: 8, borderRadius: 999,
              background: s.outcome === "failed" ? "var(--color-red)" : s.outcome ? "#1F8A5B" : s === next ? "#7FC3A0" : "#E3EDE6" }} />
          ))}
        </div>
      </div>
      <dl className="kv">
        <div><dt>Next stop</dt><dd>{next ? `${next.sequence} · ${next.consignment.customer}` : "Back to the depot"}</dd></div>
        {nextAt && <div><dt>Arrives</dt><dd>{about(nextAt)}</dd></div>}
        <div><dt>Load left</dt><dd>{loadLeft(run)}</dd></div>
        {e && <div><dt>Back at depot</dt><dd>{about(e.back_at_depot)}</dd></div>}
      </dl>
      {e && (
        <div className="small" style={{ marginTop: -4 }}>
          {e.basis === "average"
            ? `Times are estimates from the stop order and ${run.driver.name}'s average time per stop today.`
            : `Times are estimates from the stop order, at ${e.minutes_per_stop} minutes a stop until ${run.driver.name}'s first drop.`}
        </div>
      )}
      <div style={{ display: "grid", gridTemplateColumns: canLocate && run.status === "departed" ? "repeat(2,minmax(0,1fr))" : "1fr", gap: 8 }}>
        <Link to="/plan" className="btn primary" style={{ height: 46, fontSize: 15 }}>Open run<Icon name="go" /></Link>
        {canLocate && run.status === "departed" && (
          <Link to={`/where?run=${run.id}`} className="btn" style={{ height: 46 }}><Icon name="target" />Where is it</Link>)}
      </div>
    </section>
  );
}
```

Change `apps/desktop/src/ui/icons.tsx`:
```diff
--- a/apps/desktop/src/ui/icons.tsx
+++ b/apps/desktop/src/ui/icons.tsx
@@ -37,5 +37,6 @@ const PATHS = {
   truck: <><path d="M2 6h12v10H2Z" /><path d="M14 9h4.5L22 12.5V16h-8Z" /><circle cx="6.5" cy="17.5" r="2" /><circle cx="17.5" cy="17.5" r="2" /></>,
   go: <path d="M5 12h14M13 6l6 6-6 6" />,
   tick: <path d="m5 12 5 5 9-10" />,
+  target: <><circle cx="12" cy="12" r="7" /><circle cx="12" cy="12" r="2" /><path d="M12 2v3M12 19v3M2 12h3M19 12h3" /></>,
   wrench: <path d="M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18l3 3 6.3-6.3a4 4 0 0 0 5.4-5.4l-2.6 2.6-2.4-.6-.6-2.4Z" />,
 };
```


The map now asks for every run of the day (no `status`), since it draws the ones loading too; the delivered count is the same, as a planned run has nothing delivered.

**Step 4: Run the tests and the build**

Run: `npm test && npm run typecheck && npm run build`
Expected: `80 passed`, no type errors, a `dist/`.

**Step 5: Commit**

```bash
git add apps/desktop/src apps/desktop/public/region.svg
git commit -m "Draw the day's trucks on the map, with the picked one's card, its estimates and when it was last heard from"
```

### Task 12: What just happened, on the map

**Files:**
- Create: `apps/desktop/src/ui/Feed.tsx`
- Modify: `apps/desktop/src/pages/MapHome.tsx`, `apps/desktop/src/index.css` (`.feedpanel`, `.feed`)
- Test: `apps/desktop/src/test/Feed.test.tsx`

**Step 1: Write the failing tests**

Create `apps/desktop/src/test/Feed.test.tsx`:
```tsx
import { screen, within } from "@testing-library/react";
import { json, path, renderApp, stubApi } from "./harness";

// half a minute over, so the map's clock (read when it opened) still counts the whole minutes
const ago = (min: number) => new Date(Date.now() - (min + 0.5) * 60_000).toISOString();
const line = (over: object) => ({ kind: "delivered", at: ago(2), run_id: 7, run_no: "R-0007", rego: "TMS041", driver: "Kim",
  sequence: 7, customer: "Main Street Chemist", wms_ref: "0080034521", reason: null, result: null, failed: [], by: null,
  note: null, ...over });

describe("what just happened", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("tells the latest, newest first, with how long ago", async () => {
    const { calls } = stubApi((_m, url) => {
      if (path(url) === "/v1/feed") {
        return json(200, { items: [
          line({}),
          line({ kind: "prestart", result: "passed", driver: "Mel", rego: "TMS052", at: ago(9) }),
          line({ kind: "packed", wms_ref: "0080030431", customer: "Ballan Hardware", at: ago(14) }),
          line({ kind: "prestart", result: "grounded", rego: "TMS027", failed: ["Lights"], at: ago(240) }),
        ] });
      }
      return json(200, { items: [], total: 0 });
    });
    renderApp("/");
    const feed = await screen.findByRole("region", { name: "What just happened" });
    const rows = await within(feed).findAllByRole("listitem");
    expect(rows.map((r) => r.textContent)).toEqual([
      "Kim delivered stop 7 · Main Street Chemist2 min",
      "Mel passed the pre-start on TMS0529 min",
      "0080030431 packed in Simple WMS · Ballan Hardware14 min",
      "TMS027 grounded · Lights failed4 h",
    ]);
    expect(calls.find((c) => path(c.url) === "/v1/feed")!.url).toMatch(/limit=5/);
  });

  it("says when nothing has happened yet", async () => {
    stubApi(() => json(200, { items: [], total: 0 }));
    renderApp("/");
    const feed = await screen.findByRole("region", { name: "What just happened" });
    expect(await within(feed).findByText("Nothing yet. Deliveries, pre-starts and runs leaving show here as they happen.")).toBeInTheDocument();
  });
});
```


**Step 2: Run them to see them fail**

Run: `npx vitest run src/test/Feed.test.tsx`
Expected: `2 failed`: `Unable to find role="region" and name "What just happened"`.

**Step 3: Implement**

Change `apps/desktop/src/index.css`:
```diff
--- a/apps/desktop/src/index.css
+++ b/apps/desktop/src/index.css
@@ -81,6 +81,11 @@
   .truck:hover .tag { border-color: var(--color-line-2); }
   .truckcard { top: 20px; right: 24px; width: 330px; padding: 18px; border-radius: 18px; box-shadow: var(--shadow-lift); gap: 14px; }
   .panel { padding: 16px; border-radius: 16px; background: #fff; box-shadow: var(--shadow-soft); display: flex; flex-direction: column; gap: 10px; }
+  .feedpanel { bottom: 24px; left: 24px; width: 350px; }
+  .feed { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 10px; }
+  .feed li { display: flex; gap: 10px; align-items: flex-start; font-size: 13px; }
+  .feed .dot { margin-top: 4px; }
+  .feed time { color: var(--color-muted); white-space: nowrap; }
   dl.kv { margin: 0; display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px 12px; font-size: 13px; }
   dl.kv dt { color: var(--color-muted); }
   dl.kv dd { margin: 2px 0 0; font-weight: 700; }
```

Change `apps/desktop/src/pages/MapHome.tsx`:
```diff
--- a/apps/desktop/src/pages/MapHome.tsx
+++ b/apps/desktop/src/pages/MapHome.tsx
@@ -1,11 +1,12 @@
 import { useCallback, useEffect, useState, type FormEvent } from "react";
 import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
 import { api } from "../api/client";
-import type { Page, Run, TruckAt, Vehicle } from "../api/types";
+import type { FeedItem, Page, Run, TruckAt, Vehicle } from "../api/types";
 import { useAuth } from "../auth/AuthContext";
 import { clock, isoDay } from "../lib/days";
 import { cantGo } from "../lib/fleet";
 import { DrawerBoundary } from "../ui/DrawerBoundary";
+import { Feed } from "../ui/Feed";
 import { Icon, Logo } from "../ui/icons";
 import { TruckCard, TruckMarkers } from "../ui/Trucks";
 
@@ -31,6 +32,7 @@ export function MapHome() {
   const [stuck, setStuck] = useState(0);
   const [blocked, setBlocked] = useState<Vehicle[]>([]);
   const [runs, setRuns] = useState<Run[]>([]);
+  const [feed, setFeed] = useState<FeedItem[] | null>(null);
   const [heardFrom, setHeardFrom] = useState<Map<number, TruckAt>>(new Map());
   // the truck whose card shows: the first on the road until one is picked
   const [picked, setPicked] = useState<number | null | "none">(null);
@@ -53,6 +55,9 @@ export function MapHome() {
       setStuck(cantGo(trucks));
       setBlocked(trucks.filter((v) => v.active && (v.state === "grounded" || v.state === "overdue")));
     } catch { /* so does this one */ }
+    try {
+      setFeed((await api.get<{ items: FeedItem[] }>("/v1/feed", { limit: 5 })).items);
+    } catch { /* the feed waits for the next go */ }
     if (canLocate) {
       try {
         const at = await api.get<Page<TruckAt>>("/v1/positions", { date: isoDay(new Date()) });
@@ -133,6 +138,7 @@ export function MapHome() {
           <TruckCard run={shown} heardFrom={heardFrom.get(shown.id)} canLocate={canLocate} now={now}
                      onClose={() => setPicked("none")} />
         )}
+        <Feed items={feed} now={now} />
         <nav className="ov nav" aria-label="Main drawers" style={{ bottom: 24, left: "50%", transform: "translateX(-50%)" }}>
           <NavLink to="/plan" className={({ isActive }) => (isActive ? "on" : "")}>
             <Icon name="route" />Plan runs{counts && counts.toPlan > 0 && <i>{counts.toPlan}</i>}
```

Create `apps/desktop/src/ui/Feed.tsx`:
```tsx
/** WHAT JUST HAPPENED (Main.html): the latest of what the office hears,
 * newest first, each with its dot and how long ago. */
import type { FeedItem } from "../api/types";
import { ago, feedLine, type FeedTone } from "../lib/live";

const DOTS: Record<FeedTone, string> = {
  green: "var(--color-green)", red: "var(--color-red)", amber: "var(--color-amber)",
  violet: "var(--color-violet)", blue: "var(--color-blue)", brown: "#B4844A",
};

export function Feed({ items, now }: { items: FeedItem[] | null; now: Date }) {
  return (
    <section className="ov panel feedpanel" aria-label="What just happened">
      <span className="eyebrow">What just happened</span>
      {items && items.length === 0 && (
        <div className="small">Nothing yet. Deliveries, pre-starts and runs leaving show here as they happen.</div>)}
      {items && items.length > 0 && (
        <ul className="feed">
          {items.map((i, n) => {
            const { text, tone } = feedLine(i);
            return (
              <li key={`${i.kind}-${i.at}-${n}`}>
                <span className="dot" style={{ background: DOTS[tone] }} />
                <span style={{ flex: 1 }}>{text}</span>
                <time dateTime={i.at}>{ago(i.at, now)}</time>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
```


**Step 4: Run the tests**

Run: `npm test && npm run typecheck`
Expected: `82 passed`.

**Step 5: Commit**

```bash
git add apps/desktop/src
git commit -m "Show what just happened on the map"
```

### Task 13: Where is it: a real map when tiles are set up, and a list either way

**Files:**
- Create: `apps/desktop/src/lib/slippy.ts` (Leaflet, loaded only by this drawer), `apps/desktop/src/pages/WhereIsIt.tsx`
- Modify: `apps/desktop/package.json` and `package-lock.json` (Leaflet), `apps/desktop/src/App.tsx` (`/where`), `apps/desktop/src/index.css` (`.slippy`), `deploy/Caddyfile` (the tile origin in the desktop's CSP), `docker-compose.yml` (the new settings), `.env.example`
- Test: `apps/desktop/src/test/WhereIsIt.test.tsx`

**Step 1: Write the failing tests**

Create `apps/desktop/src/test/WhereIsIt.test.tsx`:
```tsx
import { act, screen, waitFor, within } from "@testing-library/react";
import { json, path, renderApp, stubApi } from "./harness";

const slippy = vi.hoisted(() => ({ mounted: [] as unknown[], shown: [] as unknown[], tileError: null as null | (() => void), gone: 0 }));
vi.mock("../lib/slippy", () => ({
  mountMap: (_el: HTMLElement, tiles: unknown, onTileError: () => void) => {
    slippy.mounted.push(tiles);
    slippy.tileError = onTileError;
    return { show: (trucks: unknown, focus: unknown) => slippy.shown.push({ trucks, focus }), destroy: () => { slippy.gone += 1; } };
  },
}));

const KIM = { run_id: 7, run_no: "R-0007", vehicle: { rego: "TMS041", type: "rigid" }, driver: { code: "kim", name: "Kim" },
              latitude: "-37.56221", longitude: "143.8503", accuracy_m: "12", at: new Date().toISOString(),
              received_at: new Date(Date.now() - 40_000).toISOString() };
const MEL = { ...KIM, run_id: 8, run_no: "R-0008", vehicle: { rego: "TMS052", type: "van" }, driver: { code: "mel", name: "Mel" },
              latitude: "-37.6", longitude: "143.9", accuracy_m: "30" };

function stub(tiles: object) {
  return stubApi((_m, url) => {
    if (path(url) === "/v1/map") return json(200, tiles);
    if (path(url) === "/v1/positions") return json(200, { items: [KIM, MEL], total: 2 });
    return json(200, { items: [], total: 0 });
  });
}

describe("where is it", () => {
  beforeEach(() => { slippy.mounted = []; slippy.shown = []; slippy.gone = 0; });
  afterEach(() => vi.unstubAllGlobals());

  it("lists each truck on the road with when it was last heard from, with no map when no tiles are set up", async () => {
    stub({ tile_url: null, attribution: null });
    renderApp("/where?run=7");
    const drawer = await screen.findByRole("dialog", { name: "Where is it" });
    expect(await within(drawer).findByText(/No map tiles are set up/)).toBeInTheDocument();
    const rows = within(within(drawer).getByRole("table")).getAllByRole("row").slice(1);
    expect(rows.map((r) => r.textContent)).toEqual([
      "R-0007TMS041Kim40 s ago-37.56221, 143.8503 (within 12 m)",
      "R-0008TMS052Mel40 s ago-37.6, 143.9 (within 30 m)",
    ]);
    // the truck asked about stands out
    expect(rows[0]).toHaveClass("sel");
    expect(slippy.mounted).toEqual([]);
  });

  it("shows them on a map from the tiles set up, the one asked about in the middle", async () => {
    stub({ tile_url: "/tiles/{z}/{x}/{y}.png", attribution: "© OpenStreetMap contributors" });
    renderApp("/where?run=7");
    await waitFor(() => expect(slippy.mounted).toEqual([{ tile_url: "/tiles/{z}/{x}/{y}.png", attribution: "© OpenStreetMap contributors" }]));
    await waitFor(() => expect(slippy.shown.at(-1)).toEqual({ trucks: [KIM, MEL], focus: 7 }));
    expect(screen.queryByText(/No map tiles are set up/)).not.toBeInTheDocument();
    act(() => slippy.tileError!());
    expect(await screen.findByText(/The map tiles didn't load/)).toBeInTheDocument();
  });
});
```


The map itself is stood in for (`vi.mock("../lib/slippy")`): jsdom draws nothing, and Leaflet is checked in a real browser in Task 16.

**Step 2: Run them to see them fail**

Run: `npx vitest run src/test/WhereIsIt.test.tsx`
Expected: `2 failed`: `Unable to find role="dialog" and name "Where is it"`.

**Step 3: Implement**

Leaflet, pinned, both published well over two weeks ago:
```
npm install --save-exact leaflet@1.9.4
npm install --save-dev --save-exact @types/leaflet@1.9.20
```
Expected: `package.json` gains `"leaflet": "1.9.4"` and `"@types/leaflet": "1.9.20"`; the lock file gains those and `@types/geojson`.

Change `.env.example`:
```diff
--- a/.env.example
+++ b/.env.example
@@ -41,6 +41,25 @@ TMS_PLATEN_KEY=
 # The Platen printer run sheets go to: its id or its name.
 TMS_RUN_SHEET_PRINTER=dispatch
 
+# Live location (optional settings)
+# Positions, and the position kept with each proof of delivery, are deleted
+# this many days after they were taken. Check what your state's workplace
+# surveillance law asks of you (e.g. the NSW Workplace Surveillance Act).
+TMS_POSITION_RETENTION_DAYS=90
+# Map tiles for the desktop's "Where is it". Empty: each truck's position is
+# listed without a map. A raster tile address with {z}, {x} and {y}: a tile
+# provider's, with its key (the browser sees the key: restrict it to your
+# domain at the provider), or a tile server of your own. Not
+# tile.openstreetmap.org: its policy rules out apps. See "Map tiles" in the
+# README.
+TMS_MAP_TILE_URL=
+TMS_MAP_ATTRIBUTION=
+# The provider's origin (https://host, no path), let into the desktop's
+# Content-Security-Policy by Caddy. Empty for tiles on this server.
+TMS_MAP_TILE_ORIGIN=
+# ETAs: the minutes a stop is taken to need until the driver has done one.
+TMS_ETA_MINUTES_PER_STOP=20
+
 # Caddy: the public host name. "localhost" gives a self-signed local cert.
 TMS_DOMAIN=localhost
 # Host ports Caddy listens on. Keep 80 and 443 for a real domain: Let's
```

Change `apps/desktop/src/App.tsx`:
```diff
--- a/apps/desktop/src/App.tsx
+++ b/apps/desktop/src/App.tsx
@@ -6,6 +6,7 @@ import { MapHome } from "./pages/MapHome";
 import { PlanRuns } from "./pages/PlanRuns";
 import { Later } from "./pages/Later";
 import { SignIn } from "./pages/SignIn";
+import { WhereIsIt } from "./pages/WhereIsIt";
 
 /** The map is home; every other screen opens as a drawer over it. */
 export function AppRoutes() {
@@ -23,6 +24,7 @@ export function AppRoutes() {
             <Later title="Carriers" step="build step 6"
                    what="Today's carrier consignments, con note entry and the manifest." />} />
           <Route path="/fleet" element={<Fleet />} />
+          <Route path="/where" element={<WhereIsIt />} />
         </Route>
       </Route>
       <Route path="*" element={<Navigate to="/" replace />} />
```

Change `apps/desktop/src/index.css`:
```diff
--- a/apps/desktop/src/index.css
+++ b/apps/desktop/src/index.css
@@ -81,6 +81,7 @@
   .truck:hover .tag { border-color: var(--color-line-2); }
   .truckcard { top: 20px; right: 24px; width: 330px; padding: 18px; border-radius: 18px; box-shadow: var(--shadow-lift); gap: 14px; }
   .panel { padding: 16px; border-radius: 16px; background: #fff; box-shadow: var(--shadow-soft); display: flex; flex-direction: column; gap: 10px; }
+  .slippy { height: 58vh; min-height: 320px; border-radius: 16px; border: 1px solid var(--color-card-line); overflow: hidden; }
   .feedpanel { bottom: 24px; left: 24px; width: 350px; }
   .feed { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 10px; }
   .feed li { display: flex; gap: 10px; align-items: flex-start; font-size: 13px; }
```

Create `apps/desktop/src/lib/slippy.ts`:
```ts
/** The real map behind "Where is it": Leaflet, raster tiles from wherever
 * TMS_MAP_TILE_URL says (a provider or a tile server of your own; never
 * OpenStreetMap's own servers). Loaded only when the drawer opens, so the
 * map screen doesn't carry it. Each truck is a dot, its accuracy a ring, and
 * its run and driver a label, set as text (never as HTML). */
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import type { MapTiles, TruckAt } from "../api/types";

export interface SlippyMap {
  show(trucks: TruckAt[], focus: number | null): void;
  destroy(): void;
}

export function mountMap(el: HTMLElement, tiles: MapTiles, onTileError: () => void): SlippyMap {
  const map = L.map(el, { zoomControl: true, attributionControl: true }).setView([-37.56, 143.85], 11);
  map.attributionControl.setPrefix(false);
  const layer = L.tileLayer(tiles.tile_url!, { attribution: tiles.attribution ?? "", maxZoom: 19 }).addTo(map);
  let warned = false;
  layer.on("tileerror", () => { if (!warned) { warned = true; onTileError(); } });
  const drawn = L.layerGroup().addTo(map);
  let framed = false;

  return {
    show(trucks, focus) {
      drawn.clearLayers();
      const points: L.LatLngExpression[] = [];
      for (const t of trucks) {
        const where: L.LatLngExpression = [Number(t.latitude), Number(t.longitude)];
        points.push(where);
        const mine = t.run_id === focus;
        L.circle(where, { radius: Number(t.accuracy_m), color: "#1F8A5B", weight: 1, fillOpacity: 0.08 }).addTo(drawn);
        const label = document.createElement("span");
        label.textContent = `${t.run_no} · ${t.driver.name} · ${t.vehicle.rego}`;
        L.circleMarker(where, { radius: mine ? 9 : 7, color: "#fff", weight: 2, fillColor: mine ? "#146B45" : "#1F8A5B", fillOpacity: 1 })
          .bindTooltip(label, { permanent: true, direction: "right", offset: [10, 0] }).addTo(drawn);
      }
      // framed once, so a dispatcher's own panning isn't undone every 30 s
      if (framed || points.length === 0) return;
      framed = true;
      const focused = trucks.find((t) => t.run_id === focus);
      if (focused) map.setView([Number(focused.latitude), Number(focused.longitude)], 14);
      else map.fitBounds(L.latLngBounds(points), { padding: [40, 40], maxZoom: 14 });
    },
    destroy() { map.remove(); },
  };
}
```

Create `apps/desktop/src/pages/WhereIsIt.tsx`:
```tsx
import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { MapTiles, Page, TruckAt } from "../api/types";
import { isoDay } from "../lib/days";
import { heard } from "../lib/live";
import type { SlippyMap } from "../lib/slippy";
import { Drawer } from "../ui/Drawer";

/** "Where is it": each truck on the road where its phone last said, on a real
 * map when tiles are set up (TMS_MAP_TILE_URL), and always as a list, which
 * is all there is without them. Only the latest position: no trails. */
export function WhereIsIt() {
  const focus = Number(useSearchParams()[0].get("run")) || null;
  const [tiles, setTiles] = useState<MapTiles | null>(null);
  const [trucks, setTrucks] = useState<TruckAt[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tilesFailed, setTilesFailed] = useState(false);
  const [now, setNow] = useState(() => new Date());
  const box = useRef<HTMLDivElement>(null);
  const map = useRef<SlippyMap | null>(null);
  // the map is ready to draw on: set once Leaflet has loaded and mounted
  const [ready, setReady] = useState(false);

  const load = useCallback(async () => {
    try {
      setTrucks((await api.get<Page<TruckAt>>("/v1/positions", { date: isoDay(new Date()) })).items);
      setNow(new Date());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Where the trucks are couldn't be loaded.");
    }
  }, []);

  useEffect(() => {
    api.get<MapTiles>("/v1/map").then(setTiles).catch(() => setTiles({ tile_url: null, attribution: null }));
    void load();
    const t = setInterval(() => void load(), 30_000);
    return () => clearInterval(t);
  }, [load]);

  // the map is loaded only if there are tiles to draw it with
  useEffect(() => {
    if (!tiles?.tile_url || !box.current) return;
    let gone = false;
    void import("../lib/slippy").then(({ mountMap }) => {
      if (gone || !box.current) return;
      map.current = mountMap(box.current, tiles, () => setTilesFailed(true));
      setReady(true);
    });
    return () => { gone = true; map.current?.destroy(); map.current = null; setReady(false); };
  }, [tiles]);

  useEffect(() => {
    if (ready && trucks) map.current?.show(trucks, focus);
  }, [ready, trucks, focus]);

  return (
    <Drawer label="Where is it" head={<div><h1>Where is it</h1>
      <div className="sub">Each truck on the road, where its driver's phone last said. Positions are deleted after the retention period.</div></div>}>
      {tiles && !tiles.tile_url && (
        <div className="msg a">No map tiles are set up, so each truck's position is listed without a map. To show a map,
          set TMS_MAP_TILE_URL: see "Map tiles" in the README.</div>)}
      {tilesFailed && (
        <div className="msg r" role="alert">The map tiles didn't load. Check TMS_MAP_TILE_URL, and that the desktop may
          load them (TMS_MAP_TILE_ORIGIN in the README).</div>)}
      {tiles?.tile_url && <div ref={box} className="slippy" aria-label="Map of the trucks on the road" role="region" />}
      {error && <div className="msg r" role="alert">{error}</div>}
      {trucks && trucks.length === 0 && (
        <div className="small">No truck on the road has been heard from today. A phone shares where it is from Start until the run is finished.</div>)}
      {trucks && trucks.length > 0 && (
        <div className="tablewrap" style={{ flex: "none" }}>
          <table>
            <thead><tr><th>RUN</th><th>TRUCK</th><th>DRIVER</th><th>LAST HEARD</th><th>WHERE</th></tr></thead>
            <tbody>
              {trucks.map((t) => {
                const h = heard(t.received_at, now);
                return (
                  <tr key={t.run_id} className={t.run_id === focus ? "sel" : ""}>
                    <td><b>{t.run_no}</b></td><td>{t.vehicle.rego}</td><td>{t.driver.name}</td>
                    <td className={h.stale ? "due-a" : ""}>{h.text}</td>
                    <td className="num">{t.latitude}, {t.longitude} (within {t.accuracy_m} m)</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </Drawer>
  );
}
```

Change `deploy/Caddyfile`:
```diff
--- a/deploy/Caddyfile
+++ b/deploy/Caddyfile
@@ -51,8 +51,9 @@ http://{$TMS_DOMAIN} {
 		# Only the desktop: the API's /docs page loads its scripts from a CDN.
 		# The build has no inline scripts or styles, and its fonts are its own.
 		# Pre-start photos are fetched with the session's token and shown
-		# from blob: URLs.
-		header Content-Security-Policy "default-src 'self'; img-src 'self' blob:; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
+		# from blob: URLs. Map tiles come from TMS_MAP_TILE_ORIGIN (the tile
+		# provider's https://host, or empty for none or tiles on this server).
+		header Content-Security-Policy "default-src 'self'; img-src 'self' blob: {$TMS_MAP_TILE_ORIGIN}; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
 		# the page names the build's files: asked after every time
 		@desktoppage not path /assets/* /favicon.svg /region.svg
 		header @desktoppage Cache-Control "no-cache"
```

Change `docker-compose.yml`:
```diff
--- a/docker-compose.yml
+++ b/docker-compose.yml
@@ -29,6 +29,9 @@ services:
       TMS_WMS_API_KEY: ${TMS_WMS_API_KEY:-}
       TMS_PLATEN_URL: ${TMS_PLATEN_URL:-}
       TMS_RUN_SHEET_PRINTER: ${TMS_RUN_SHEET_PRINTER:-dispatch}
+      TMS_MAP_TILE_URL: ${TMS_MAP_TILE_URL:-}
+      TMS_MAP_ATTRIBUTION: ${TMS_MAP_ATTRIBUTION:-}
+      TMS_ETA_MINUTES_PER_STOP: ${TMS_ETA_MINUTES_PER_STOP:-20}
     volumes:
       # signatures and photos for proof of delivery (TMS_POD_DIR)
       - pod_files:/data/pod
@@ -49,7 +52,9 @@ services:
       start_period: 20s
 
   # Makes the ship calls to Simple WMS and sends run sheets to Platen, from
-  # their queues in the database, with backoff. Waits for the api's migrations.
+  # their queues in the database, with backoff; and once an hour deletes
+  # positions older than TMS_POSITION_RETENTION_DAYS. Waits for the api's
+  # migrations.
   worker:
     build: ./api
     restart: unless-stopped
@@ -62,6 +67,7 @@ services:
       TMS_PLATEN_URL: ${TMS_PLATEN_URL:-}
       TMS_PLATEN_KEY: ${TMS_PLATEN_KEY:-}
       TMS_RUN_SHEET_PRINTER: ${TMS_RUN_SHEET_PRINTER:-dispatch}
+      TMS_POSITION_RETENTION_DAYS: ${TMS_POSITION_RETENTION_DAYS:-90}
     depends_on:
       db:
         condition: service_healthy
@@ -73,6 +79,8 @@ services:
       TMS_DOMAIN: ${TMS_DOMAIN:-localhost}
       TMS_HTTP_PORT: ${TMS_HTTP_PORT:-80}
       TMS_HTTPS_PORT: ${TMS_HTTPS_PORT:-443}
+      # the map tile provider's origin, let into the desktop's CSP
+      TMS_MAP_TILE_ORIGIN: ${TMS_MAP_TILE_ORIGIN:-}
     # The same port inside and out: Caddy names it in its redirect to https.
     ports:
       - "${TMS_HTTP_PORT:-80}:${TMS_HTTP_PORT:-80}"
```


Labels on the map are set with `textContent`, never as HTML (Leaflet's tooltips take HTML strings, and a driver's name is typed by a person). The map is framed once, on the truck asked about (`?run=`) or around them all, so a dispatcher's own panning isn't undone by the 30-second refresh.

**Step 4: Run the tests and the build**

Run: `npm test && npm run typecheck && npm run build`
Expected: `84 passed`; the build lists Leaflet in a chunk of its own, about `slippy-….js 150.79 kB │ gzip: 43.92 kB` and `slippy-….css 15.61 kB`, beside the app's own `index-….js` (about 105 kB gzipped).

Check the Caddyfile reads (Docker, nothing left running):
```
cd "/Users/leightonlauton/Q7 Technology/Software/q7/simple-tms"
for origin in "" https://api.maptiler.com; do
  docker run --rm -v "$PWD/deploy/Caddyfile:/etc/caddy/Caddyfile:ro" -e TMS_DOMAIN=localhost \
    -e TMS_MAP_TILE_ORIGIN=$origin caddy:2 caddy adapt --config /etc/caddy/Caddyfile 2>/dev/null \
    | grep -o "img-src[^;]*;" | sort -u | tr '\n' ' '; echo
done
```
Expected: `img-src 'self' blob: ; img-src 'self' blob:;` then `img-src 'self' blob: https://api.maptiler.com; img-src 'self' blob:;` (the driver app's stays as it was).

**Step 5: Commit**

```bash
git add apps/desktop/src apps/desktop/package.json apps/desktop/package-lock.json deploy/Caddyfile docker-compose.yml .env.example
git commit -m "Open Where is it on a real map when tiles are set up, and as a list either way"
```

## Part D — docs and checks

### Task 14: README and CLAUDE.md

**Files:**
- Modify: `README.md` (the status, how live location works and what is shared, the retention period, a "Map tiles" section, the shared-Caddy snippets' CSP, three known limitations), `CLAUDE.md` (the location rule)

CI needs no change: the new tests run in the existing jobs, and the Compose job's API start runs migration 0006.

**Step 1: The README and the rule**

Change `CLAUDE.md`:
```diff
--- a/CLAUDE.md
+++ b/CLAUDE.md
@@ -64,8 +64,13 @@ copy and states. Do not edit anything under `design/` unless asked.
   tells it what shipped through its API. Nothing in Simple WMS changes for it.
 - Printing: Simple TMS never renders a document. It sends `template` + JSON
   `data` to Platen (or any print service) through the same queue.
-- Location: the driver app shares its position only during a run, and says
-  so on screen. Positions are deleted after the retention period.
+- Location: the driver app shares its position only during a run (from Start
+  until the run is finished), and says so on screen. Positions are personal
+  information, not business history: they never go in a consignment's
+  history, and the worker deletes them, and clears the position on each proof
+  of delivery, after the retention period (`TMS_POSITION_RETENTION_DAYS`, 90
+  days by default). That is the one deletion allowed: rule 1 keeps business
+  records, and a position is not one.
 - Build order is in the design under "Build order". Finish a step so it is
   usable before starting the next.
 - Tests: pytest for the API against a real PostgreSQL, Vitest for the apps.
```

Change `README.md`:
````diff
--- a/README.md
+++ b/README.md
@@ -4,17 +4,19 @@ An open source, self-hosted transport management system for businesses that
 deliver with their own trucks and also send freight with outside carriers.
 It sits beside Simple WMS and Platen and works without either.
 
-Status: **build step 4, fleet care.** Drivers do a pre-start check of the
-truck on their phones before each run; a failed critical item grounds the
-truck until a fleet manager marks it fixed, and the desktop's **Fleet**
-shows each truck's rego and service due (amber a month or 1,000 km out, red
-when overdue). A grounded or overdue truck can't be given a run. On top of
-step 3 (the driver app: start the run, deliver each stop with a signature, a
-photo or both, or say why not, and finish, with or without signal), step 2
-(runs planned on a map-and-drawer desktop, run sheets printed through
-Platen, Simple WMS told each delivery shipped when its run leaves) and step 1
-(sign-in, vehicles and drivers, consignments from Simple WMS's
-`delivery.packed`). Live location and carriers come with the next steps in
+Status: **build step 5, live location.** While a run is on the road the
+driver's phone shares where it is about every 30 seconds, and says so; the
+desktop's map draws the day's trucks, says when each was last heard from and
+when it should reach its next stop and the depot (estimates from the stop
+order and the driver's own pace), shows what just happened, and opens **Where
+is it** on a real map when map tiles are set up. Positions are deleted after
+90 days. On top of step 4 (pre-start checks, grounding, rego and service
+due), step 3 (the driver app: start the run, deliver each stop with a
+signature, a photo or both, or say why not, and finish, with or without
+signal), step 2 (runs planned on a map-and-drawer desktop, run sheets
+printed through Platen, Simple WMS told each delivery shipped when its run
+leaves) and step 1 (sign-in, vehicles and drivers, consignments from Simple
+WMS's `delivery.packed`). Carriers come with the next step in
 `docs/plans/2026-10-10-simple-tms-design.md`.
 
 ## What is in this repo
@@ -49,7 +51,8 @@ self-signed): sign in, then **Plan runs**. The API runs its migrations each
 time it starts; it is at `/v1`, its interactive docs at `/docs`, and it is
 also published on `127.0.0.1:8022` (`TMS_API_PORT`) for local work. The
 `worker` makes the calls to Simple WMS and sends run sheets to Platen, taking
-one of each in turn so neither queue holds up the other; it needs no port. Rebuild the desktop (`npm run build`) after pulling changes to
+one of each in turn so neither queue holds up the other, and deletes positions
+past the retention period; it needs no port. Rebuild the desktop (`npm run build`) after pulling changes to
 `apps/desktop`; Caddy serves `apps/desktop/dist` as it is.
 
 The driver app is at `https://<TMS_DOMAIN>/driver/`. A driver signs in with
@@ -86,6 +89,62 @@ recorded is never due, so record each truck's rego expiry and last service
 when you start. The checklists are the design's six items for each type of
 vehicle; change them with `PUT /v1/checklists/{type}` (see `docs/api.md`).
 
+**Live location.** From **Start run** until the run is finished, the driver's
+phone tells the office where it is about every 30 seconds, and its top bar
+says **Sharing location**; never before Start or after the run is finished.
+It needs the phone's permission (asked once, at Start) and the app open: a
+web app can't share from the background. In a black spot nothing piles up:
+once the signal is back only the newest position goes. On the desktop's map
+each run's truck is drawn on a road of its own, further along as its stops
+are done (the drawn region is a picture, not real geography); pick one to
+see when it was last heard from, its next stop and when it should get there,
+the load left and when it should be back, all estimates from the stop order
+and the driver's average time per stop so far (`TMS_ETA_MINUTES_PER_STOP`,
+20, until the first stop is done). **Where is it** shows each truck where its
+phone last said, on a real map once map tiles are set up (below), and as a
+list either way. Only the latest position is shown; there are no trails.
+Dispatchers, managers and admins see it (`location:read`); a system needs a
+key made with `location:read`.
+
+**Positions are deleted after 90 days** (`TMS_POSITION_RETENTION_DAYS`):
+once an hour the worker deletes older positions and clears the position kept
+with each proof of delivery; the deliveries themselves are kept. Some states
+regulate tracking employees (for example the NSW Workplace Surveillance Act,
+which asks for notice to staff before tracking starts): check your own
+obligations, tell your drivers, and set the retention period to suit.
+
+## Map tiles
+
+**Where is it** draws a real map from raster map tiles. Simple TMS doesn't
+come with any, and won't use OpenStreetMap's own tile servers (their policy
+rules out apps), so until you set them up it lists each truck's position as
+coordinates. Two ways:
+
+- **A tile provider** (MapTiler, Stadia Maps, Thunderforest and others have
+  free tiers for small use; each needs an account). Set
+  `TMS_MAP_TILE_URL` to its raster address with `{z}`, `{x}` and `{y}` and
+  your key, `TMS_MAP_ATTRIBUTION` to the credit it asks for, and
+  `TMS_MAP_TILE_ORIGIN` to its `https://host` so the desktop's
+  Content-Security-Policy lets the tiles in. For example:
+
+  ```
+  TMS_MAP_TILE_URL=https://api.maptiler.com/maps/streets-v2/256/{z}/{x}/{y}.png?key=<your key>
+  TMS_MAP_ATTRIBUTION=© MapTiler © OpenStreetMap contributors
+  TMS_MAP_TILE_ORIGIN=https://api.maptiler.com
+  ```
+
+  The key is in every tile address, so browsers see it: restrict it to your
+  domain in the provider's settings.
+- **Your own tile server** (for example `tileserver-gl` serving an extract
+  for your state). On another host, as above. Behind this Caddy, give it a
+  path, say `/tiles/*`, with `TMS_MAP_TILE_URL=/tiles/{z}/{x}/{y}.png`; it is
+  then the same origin and `TMS_MAP_TILE_ORIGIN` stays empty.
+
+Then `docker compose up -d api` and `docker compose up -d --force-recreate
+caddy`. A tile address the API can't use (no `{z}`, `{x}` and `{y}`, not
+`https`, or OpenStreetMap's own) stops it starting, with the reason in
+`docker compose logs api`. If the tiles don't load, **Where is it** says so.
+
 ## Production on a real domain
 
 Let's Encrypt only checks a domain on ports 80 and 443, so Caddy gets a
@@ -128,6 +187,7 @@ not run the TMS one: start Simple TMS with
   		file_server
   	}
   	handle {
+  		# add your map tile provider's https://host after blob: (see Map tiles)
   		header Content-Security-Policy "default-src 'self'; img-src 'self' blob:; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
   		@desktoppage not path /assets/* /favicon.svg /region.svg
   		header @desktoppage Cache-Control "no-cache"
@@ -185,6 +245,7 @@ not run the TMS one: start Simple TMS with
   		file_server
   	}
   	handle {
+  		# add your map tile provider's https://host after blob: (see Map tiles)
   		header Content-Security-Policy "default-src 'self'; img-src 'self' blob:; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
   		@desktoppage not path /assets/* /favicon.svg /region.svg
   		header @desktoppage Cache-Control "no-cache"
@@ -354,6 +415,19 @@ stood in for by `fake-indexeddb`: `cd apps/driver && npm test`.
 
 ## Known limitations
 
+- **The phone shares only with the app open.** A web app can't use location
+  in the background, so a driver who switches to another app, or locks the
+  phone, isn't heard from until they come back to Simple TMS; the map says
+  how long it has been ("Last heard 12 min ago", in amber after 5 minutes).
+  A native app could share in the background; that is not planned.
+- **Estimates are plain.** ETAs come from the stop order and the driver's
+  average time per stop so far on the run, with no routing service and no
+  distances, so a long drive to one far stop throws them out until it is
+  done. They are labelled as estimates.
+- **The map's region is a picture.** Trucks on the home map are placed along
+  drawn roads by how many stops they have done, not by where they are; **Where
+  is it** shows true positions.
+
 - **The office's Leaves now doesn't ask for a pre-start.** The driver app
   won't start a run until the truck's pre-start is in, but **Leaves now** on
   the desktop (for a driver without a phone, who does a paper check) sends it
````


**Step 2: Commit**

```bash
git add README.md CLAUDE.md
git commit -m "Document live location, map tiles and the retention of positions"
```

### Task 15: Every suite, and a clean Compose start

**Step 1: The suites**

Run:
```
cd "/Users/leightonlauton/Q7 Technology/Software/q7/simple-tms/api" && caffeinate -i .venv/bin/pytest -q
cd ../apps/desktop && npm test && npm run build
cd ../driver && npm test && npm run build
```
Expected: `332 passed`; `84 passed` and a `dist/` with its `slippy-…` chunk; `98 passed` and a `dist/` with `sw.js`.

**Step 2: Compose**

Run (repo root):
```
docker compose up -d --build api worker
docker compose up -d --force-recreate caddy
docker compose ps
docker compose exec -T db psql -U tms -d tms -tAc "select version_num from alembic_version"
docker compose exec -T db psql -U tms -d tms -tAc "select count(*) from pg_trigger where tgrelid = 'position'::regclass and not tgisinternal"
docker compose logs worker | grep "worker started"
curl -skI https://localhost:8543/ | grep -i content-security-policy
```
Expected: `api` healthy, `worker`, `db` and `caddy` up; `0006`; `0` (no trigger on `position`); the worker started; the desktop's CSP with `img-src 'self' blob: ;` (no tile origin set yet). (`--force-recreate`: the Caddyfile is mounted as a single file, and a branch switch replaces the file the container still holds.)

**Step 3: Push**

```bash
git push -u origin step-5-live-location
```
Wait for CI (API, Desktop app, Driver app, Docker Compose comes up) to go green, then do Task 16 before **superpowers:finishing-a-development-branch**.

### Task 16: Manual check on the local stacks: sharing from Start to finish, the map, Where is it, and forgetting

Everything runs on this machine: Simple TMS on this branch (Task 15 Step 2 left it up). Scripts and screenshots go in a working folder of your own in your scratchpad, never `/tmp` and never the repo: set `W` to it in each shell. The phone part takes a minute of real time; keep the Mac awake.

```
export W="<your scratchpad directory>/tms-step5-check"
mkdir -p "$W/shots"
```

**Step 1: Tokens, and Kim's PIN**

```
cd "/Users/leightonlauton/Q7 Technology/Software/q7/simple-tms"
A=http://127.0.0.1:8022
token() { curl -s -X POST $A/v1/auth/login -H 'Content-Type: application/json' \
  -d "{\"username\":\"$1\",\"password\":\"$2\"}" | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])'; }
ADMIN=$(token leighton "a long enough password"); DEE=$(token dee "dispatch password")
curl -s -X PATCH $A/v1/drivers/kim -H "Authorization: Bearer $ADMIN" -H 'Content-Type: application/json' -d '{"pin":"2468"}' >/dev/null
curl -s -X POST $A/v1/drivers/kim/unlock -H "Authorization: Bearer $ADMIN" | python3 -m json.tool | grep locked
curl -s $A/v1/auth/me -H "Authorization: Bearer $DEE" | python3 -c 'import sys,json;print(json.load(sys.stdin)["scopes"])'
```
Expected: `"locked": false`, and the dispatcher's scopes ending in `'location:read'`. (Access tokens last 15 minutes: run `token` again if a later call says `401`.)

**Step 2: A run for Kim today, with two stops**

```
api() { curl -s -X "$1" "$A$2" -H "Authorization: Bearer $DEE" -H 'Content-Type: application/json' ${3:+-d "$3"}; }
TODAY=$(date +%F); REGO=TMS041
api GET "/v1/consignments?status=packed,failed" | python3 -c 'import sys,json;d=json.load(sys.stdin);print(d["total"])'
RUN=$(api POST /v1/runs "{\"date\":\"$TODAY\",\"vehicle\":\"$REGO\",\"driver\":\"kim\"}" | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')
for C in $(api GET "/v1/consignments?status=packed,failed" | python3 -c 'import sys,json;print(" ".join(str(c["id"]) for c in json.load(sys.stdin)["items"][:2]))'); do
  api POST /v1/runs/$RUN/assign "{\"consignment_id\":$C}" | python3 -c 'import sys,json;r=json.load(sys.stdin);print(r["run_no"], len(r["stops"]), "stops")'; done
```
Expected: a count of 2 or more, then `R-… 1 stops`, `R-… 2 stops`. With fewer than two consignments, pack two in Simple WMS first with the script in `docs/plans/2026-10-11-step-3-driver-app.md`, Task 22 Step 3 (saved under `$W`, not `/tmp`). If `POST /v1/runs` says TMS041 already has a run today, set `REGO=TMS052` (or `TMS053`) and run the last two commands again; a grounded or overdue truck is refused, as step 4 made it.

**Step 3: Nothing is shared before Start**

```
KIM=$(curl -s -X POST $A/v1/auth/driver-login -H 'Content-Type: application/json' -d '{"code":"kim","pin":"2468"}' \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')
curl -s -X POST $A/v1/runs/$RUN/position -H "Authorization: Bearer $KIM" -H 'Content-Type: application/json' \
  -d "{\"at\":\"$(date -u +%FT%TZ)\",\"latitude\":\"-37.5\",\"longitude\":\"143.8\",\"accuracy_m\":\"10\"}"; echo
api POST /v1/runs/$RUN/position '{"at":"2026-10-13T00:00:00Z","latitude":"-37.5","longitude":"143.8","accuracy_m":"10"}'; echo
```
Expected: `{"detail":"R-… hasn't left yet, so where it is isn't shared","code":"run_not_started"}`, then `{"detail":"you do not have the deliveries:write scope"}` (a dispatcher can't send one).

**Step 4: Kim's pre-start, so the run can start**

Save as `$W/prestart.py`:
```python
"""Kim's pre-start of the truck named (TMS041 unless told), every item passed, sent as the phone would."""
import datetime, json, sys, urllib.request, uuid

api, token, day = sys.argv[1], sys.argv[2], sys.argv[3]


def call(method, path, body=None):
    req = urllib.request.Request(api + path, method=method, data=json.dumps(body).encode() if body else None,
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        return json.load(r)


rego = sys.argv[4] if len(sys.argv) > 4 else "TMS041"
run = next(r for r in call("GET", f"/v1/driver/runs?date={day}") if r["vehicle"]["rego"] == rego and r["status"] == "planned")
out = call("POST", f"/v1/runs/{run['id']}/prestart", {
    "message_id": str(uuid.uuid4()), "at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "vehicle": rego, "odometer_km": (run["vehicle"]["odometer_km"] or 61000) + 12,
    "items": [{"item_id": i["id"], "result": "pass"} for i in run["checklist"]]})
print(out["run_no"], out["result"])
```
Run: `python3 "$W/prestart.py" $A $KIM $TODAY $REGO`
Expected: `R-… passed`.

**Step 5: Kim starts on the phone, shares, and stops when the office finishes the run**

Playwright drives the Chrome already installed, as a Pixel 7 that allows location and is in Ballarat; half a minute later it is a little down the road. Then the desktop, as `dee`, picks the run's truck and opens **Where is it**; then the office records both stops as not delivered and finishes the run, and the phone, at its next send, hears so and stops.
```
cd "$W" && npm init -y >/dev/null && npm i playwright-core@1 >/dev/null
```
Save as `$W/live.mjs`:
```js
import { createRequire } from "module";
const W = process.env.W, BASE = process.env.BASE, API = process.env.API, REGO = process.env.REGO ?? "TMS041";
const require = createRequire(`${W}/`);
const { chromium, devices } = require("playwright-core");
const shotsDir = `${W}/shots`;

const browser = await chromium.launch({
  executablePath: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  headless: true, args: ["--ignore-certificate-errors"] });

// Kim's phone, in Ballarat, letting the app know where it is
const phoneCtx = await browser.newContext({ ...devices["Pixel 7"], ignoreHTTPSErrors: true,
  permissions: ["geolocation"], geolocation: { latitude: -37.5622, longitude: 143.8503, accuracy: 9 } });
const phone = await phoneCtx.newPage();
phone.on("pageerror", (e) => console.log("phone pageerror:", e.message));
await phone.goto(`${BASE}/driver/`);
await phone.getByLabel("Driver code").fill("kim");
for (const d of "2468") await phone.getByRole("button", { name: d, exact: true }).click();
await phone.getByRole("button", { name: "Sign in" }).click();
await phone.getByRole("radio", { name: new RegExp(REGO) }).click();
await phone.getByText(/Starting shares this phone's location/).waitFor();
await phone.screenshot({ path: `${shotsDir}/1-before-start.png` });
await phone.getByRole("button", { name: "Start run" }).click();
await phone.getByText("Sharing location").waitFor();
await phone.screenshot({ path: `${shotsDir}/2-sharing.png` });
console.log("phone: Sharing location");

// half a minute on, a little further down the road
await phone.waitForTimeout(5_000);
await phoneCtx.setGeolocation({ latitude: -37.5650, longitude: 143.8600, accuracy: 12 });
await phone.waitForTimeout(31_000);

// the office: the run Kim started
const login = await (await fetch(`${API}/v1/auth/login`, { method: "POST", headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ username: "dee", password: "dispatch password" }) })).json();
const auth = { Authorization: `Bearer ${login.token}`, "Content-Type": "application/json" };
const day = new Date().toLocaleDateString("en-CA");
const run = (await (await fetch(`${API}/v1/runs?date=${day}`, { headers: auth })).json()).items.find((r) => r.vehicle.rego === REGO && r.status === "departed");
const kim = await (await fetch(`${API}/v1/auth/driver-login`, { method: "POST", headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ code: "kim", pin: "2468" }) })).json();
const desk = await (await browser.newContext({ viewport: { width: 1440, height: 900 }, ignoreHTTPSErrors: true })).newPage();
desk.on("pageerror", (e) => console.log("desk pageerror:", e.message));
await desk.goto(`${BASE}/`);
await desk.getByLabel("Username").fill("dee");
await desk.getByLabel("Password").fill("dispatch password");
await desk.getByRole("button", { name: "Sign in" }).click();
await desk.getByRole("button", { name: new RegExp(`^${run.run_no} · Kim`) }).click();
const card = desk.getByRole("region", { name: "Selected truck" });
await card.getByText(/Last heard/).waitFor();
console.log("desk card:", (await card.innerText()).replace(/\s+/g, " "));
await desk.screenshot({ path: `${shotsDir}/3-map.png` });
await card.getByRole("link", { name: /Where is it/ }).click();
const row = desk.getByRole("dialog", { name: "Where is it" }).getByRole("row", { name: new RegExp(run.run_no) });
await row.waitFor();
console.log("where is it:", (await row.innerText()).replace(/\s+/g, " "));
await desk.screenshot({ path: `${shotsDir}/4-where.png` });

// the office finishes the run for Kim: the phone hears so at its next send, and stops
for (const s of run.stops) {
  await fetch(`${API}/v1/runs/${run.id}/stops/${s.consignment.id}/fail`, { method: "POST",
    headers: { Authorization: `Bearer ${kim.token}`, "Content-Type": "application/json" },
    body: JSON.stringify({ message_id: crypto.randomUUID(), at: new Date().toISOString(), reason: "closed" }) });
}
const done = await fetch(`${API}/v1/runs/${run.id}/finish`, { method: "POST", headers: auth,
  body: JSON.stringify({ message_id: crypto.randomUUID() }) });
console.log("finished by the office:", done.status);
await phoneCtx.setGeolocation({ latitude: -37.5700, longitude: 143.8700, accuracy: 10 });
await phone.getByText("Location off").waitFor({ timeout: 45_000 });
console.log("phone: Location off");
await phone.screenshot({ path: `${shotsDir}/5-stopped.png` });
await browser.close();
```
Run: `cd "$W" && W="$W" BASE=https://localhost:8543 API=$A REGO=$REGO node live.mjs`
Expected (the times are yours; no `pageerror` lines):
```
phone: Sharing location
desk card: TMS041 · Kim · rigid Run R-… On the road Last heard just now 0 of 2 stops 2 to go Next stop 1 · … Arrives about … Load left … Back at depot about … Times are estimates from the stop order, at 20 minutes a stop until Kim's first drop. Open run Where is it
where is it: R-… TMS041 Kim just now -37.565, 143.86 (within 12 m)
finished by the office: 200
phone: Location off
```
Look at the screenshots beside the designs: `shots/1-before-start.png` (Today: "Starting shares this phone's location with the office about every 30 seconds until you finish the run, and keeps it with each drop. Never outside a run."), `2-sharing.png` beside `design/screens/DriverOffline.html` (the green **Sharing location** chip, and under the next stop "While this run is on the road, this phone shares where it is with the office about every 30 seconds, with the app open. It stops when you finish the run or sign out."), `3-map.png` beside `design/screens/Main.html` (the truck on a drawn road with its label, the **Selected truck** card, **What just happened** with "Kim left on R-… in TMS041" and the pre-start; any truck that can't go stands in the yard, grey, labelled in red), `4-where.png` ("No map tiles are set up, so each truck's position is listed without a map…" and the table), `5-stopped.png` (**Location off**).

**Step 6: Kept from Start to finish only, and never in the history**

```
docker compose exec -T db psql -U tms -d tms -c "
  select p.latitude, p.longitude, p.accuracy_m,
         p.received_at > r.departed_at as after_start, p.received_at < r.finished_at as before_finish
    from position p join run r on r.id = p.run_id where r.id = $RUN order by p.id" -c "
  select count(*) as history_rows_naming_a_position from consignment_event
   where detail::text ~ 'latitude|longitude'"
```
Expected: two rows, `-37.562200 | 143.850300 | 9.0 | t | t` and `-37.565000 | 143.860000 | 12.0 | t | t` (nothing from before Start or after the finish, though the phone moved again after it), and `0`.

**Step 7: The worker forgets what is older than 90 days**

Make one of this run's positions, and where its first stop happened, 91 days old (only this check's own run is touched), then restart the worker, which forgets on start:
```
docker compose exec -T db psql -U tms -d tms -tA -c "
  insert into position (run_id, driver_id, vehicle_id, latitude, longitude, accuracy_m, recorded_at, received_at)
    select run_id, driver_id, vehicle_id, latitude, longitude, accuracy_m,
           now() - interval '91 days', now() - interval '91 days' from position where run_id = $RUN limit 1;
  update pod set latitude = -37.5, longitude = 143.8, accuracy_m = 10, at = now() - interval '91 days'
   where id = (select min(id) from pod where run_id = $RUN);
  select count(*) from position where run_id = $RUN;
  select count(*) from pod where run_id = $RUN and latitude is not null;"
docker compose restart worker
sleep 5; docker compose logs worker | grep forgot
docker compose exec -T db psql -U tms -d tms -tA -c "select count(*) from position where run_id = $RUN" \
  -c "select count(*) from pod where run_id = $RUN and latitude is not null" \
  -c "select outcome, reason from pod where id = (select min(id) from pod where run_id = $RUN)"
```
Expected: `3` and `1`; then `forgot 1 positions, and where 1 deliveries were, older than 90 days` (more if older local data was already past 90 days); then `2`, `0` and `failed|closed`: the stop's record is kept, only where it happened is gone.

**Step 8 (if you have a tile provider key): the real map**

Set `TMS_MAP_TILE_URL`, `TMS_MAP_ATTRIBUTION` and `TMS_MAP_TILE_ORIGIN` in `.env` as in the README's "Map tiles", then:
```
docker compose up -d api && docker compose up -d --force-recreate caddy
curl -skI https://localhost:8543/ | grep -io "img-src[^;]*"
curl -s $A/v1/map -H "Authorization: Bearer $(token dee 'dispatch password')"; echo
```
Expected: `img-src 'self' blob: https://<the provider>` and the tile address and attribution. Plan and start another run (Steps 2–4, the other van), run `live.mjs` again with its `REGO`, and look at `shots/4-where.png`: the map with a green dot labelled "R-… · Kim · TMS0…", the attribution bottom right, and the table under it. Without a key, skip this step: Step 5 showed the list without a map.

**Step 9: Tidy up**

The run is finished; the consignments it couldn't deliver are back in the tray. Kim's PIN stays `2468`. Remove any tile settings you don't want to keep from `.env` (and `docker compose up -d api && docker compose up -d --force-recreate caddy`). Remove `$W` when done.

Nothing to commit.

---

## What is left for later steps

- **Carriers** (step 6), as in the design.
- **Trails, or a day's positions for a dispute** (Decision 8): kept in the database for the retention period, read by an administrator; a screen for them is a decision for the business first.
- **Sharing in the background** needs a native app; a web app shares only while open (Decision 4).
- **Events out** (`consignment.delivered`, `consignment.failed`, `run.completed`, `vehicle.due`) come with the first subscriber.
- **The rest of `Main.html`**: Today's goal, Game touches, sounds, day and night, zoom, and stops drawn as pins along each road.
