# Simple TMS step 6: carriers — implementation plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build step 6 of `docs/plans/2026-10-10-simple-tms-design.md`: outside carriers booked by hand. A dispatcher sends a packed consignment with a carrier instead of a run (from **Plan runs**), types or scans its con note in the **Carriers** drawer (`design/screens/Carriers.html`), prints the carrier's manifest through Platen (template `carrier-manifest`), and marks it collected when the carrier calls, which tells Simple WMS it shipped with the carrier's name and the con note as the tracking number, through the existing queue with a `message_id` per call. A small adapter shape so a carrier's own API can be added later without reshaping the data. The map counts what waits on the carrier bay, the feed says when a con note goes in and when a carrier collects, and the **Consignments** drawer (`design/screens/Consignments.html`, not built until now) finds any consignment by its WMS delivery, customer or con note, with its history and proof of delivery.

**Architecture:** The API grows three tables (`carrier`; `manifest`, one carrier's pickup on a day, open until collected; `booking`, a consignment's place on a manifest with its con note, taken off and never deleted, like `stop`), and `outbound_call` and `print_job` may belong to a manifest instead of a run. Booking, taking off, con notes and collecting take the planning lock that runs already take, so a consignment is on one run or with one carrier, never both. `POST /v1/manifests/{id}/collect` mirrors **Leaves now**: in one transaction each consignment is `collected`, a history row is written and a ship call is queued, with a `message_id` per call and the confirmation itself through `envelope.handle`. Manifests print through `services/printing.py` as run sheets do. The desktop gets the Carriers and Consignments drawers, a **With a carrier…** menu on the Plan runs tray (and carriers in a stop's **Move to…**), the "N for carriers" label over the drawn carrier bay and an amber count on **Carriers**. The driver app doesn't change.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2, Alembic, pytest on a real PostgreSQL 16; Vite 6, React 19, TypeScript 5, Tailwind 4, react-router 7, Vitest 3 with Testing Library; Platen page templates; Caddy 2 and Docker Compose. No new dependencies.

---

## Context

Paths are relative to the Simple TMS repo, `/Users/leightonlauton/Q7 Technology/Software/q7/simple-tms/`. Everything here was checked against `main` today (`6405d67`, "Document every answer POST /v1/runs/{id}/position gives"): each task was built in a scratch copy, in order, against a throwaway PostgreSQL 16, and the counts given at each task are what the suites printed there. API 357 → 399, desktop 91 → 112, driver app 109 → 109; the desktop type-checks and builds. The branch was also started as a throwaway Compose project (its own ports and volumes): migration `0008` applied, the worker started, and the manual check's desktop script (Task 16, Step 6) ran against it in a real Chrome, end to end but for Simple WMS and Platen (four ship calls left `pending` with the carrier's name and each con note, as there was no Simple WMS to send them to). The README's `carrier-manifest` template was rendered with Platen's own `pages.render_document` and a manifest's data, read-only, and printed as intended. The manual check itself has not been run on the local stacks.

### 1. The rules that bind this step (`CLAUDE.md`)

- **Rule 1: append-only.** Every booking, taking off, con note (and each correction of one) and collection is a new `consignment_event` row; nothing is updated in place. A booking taken off the bay keeps its row with `removed_at`, as a stop taken off a run does; a carrier is deactivated, never deleted.
- **Rule 2: recorded once.** Collecting queues one ship call per consignment, each with its own `message_id`, in the same transaction as the status change; the collect request itself carries a `message_id` and goes through `envelope.handle`, so a lost reply sent again gets the first answer and queues nothing more.
- **Rule 3** doesn't touch carriers: a carrier is not our truck.
- **Rule 4: the API is the only door.** The desktop uses only the endpoints below; a partner can book, set con notes and collect with a `runs:write` key.
- **Git.** Subject line and, if needed, a short body. No `Co-Authored-By`, no tool or generated-by trailers, no session links. `CLAUDE.md` itself doesn't change in this step.

### 2. The code as it is on `main`

- **Consignments** (`api/tms/models/consignment.py`): `status` is `packed`, `assigned`, `departed`, `delivered` or `failed`; `consignment_event` is append-only by trigger (`0001`). `GET /v1/consignments` (`api/tms/api/routes/consignments.py`) filters by `status`, `warehouse` and `q` (WMS ref or customer), and the detail adds `history`.
- **Planning** (`api/tms/services/runs.py`): every change takes `lock_planning` (one advisory lock), then reads; `PLANNABLE = ("packed", "assigned", "failed")`; `assign` moves a consignment off another planned run with an `unassigned` row; `depart` queues an `OutboundCall` per consignment with body `{message_id, owner, carrier: "Own fleet", tracking_no: run.number, shipped_by}`.
- **The queue** (`api/tms/models/integration.py`, `api/tms/worker.py`): `outbound_call` and `print_job` rows, each with `run_id NOT NULL`; the worker claims due rows one at a time with `skip locked` and retries after 1 min, 5, 30 and 2 h. It knows nothing of runs: the row carries the path and body.
- **Printing** (`api/tms/services/printing.py`): `queue_run_sheet` refuses `printing_not_set_up`, takes the planning lock, snapshots `run_sheet_data` into a `print_job` (template `run-sheet`, `v1`, printer `TMS_RUN_SHEET_PRINTER`); Platen reports on `POST /v1/print-jobs/{job_id}/status` with a `printing:write` key, through the per-key callback made in step 2.
- **Confirmations** (`api/tms/services/envelope.py`): `handle(db, who, message_id, path, work)` runs `work` once per `message_id`, stores the reply, refuses a reuse on another path. The office's **Finish run** already uses it from the desktop.
- **The feed** (`api/tms/services/feed.py`): read from the records already kept (packed, delivered, failed, left, finished; pre-starts and fixes for `fleet:read`).
- **Roles** (`api/tms/services/sessions.py`): `dispatcher` has `fleet:read`, `consignments:read`, `runs:read`, `runs:write`, `location:read`; `manager` adds `fleet:write`; `admin` has `*`.
- **The desktop** (`apps/desktop`): `/consignments` and `/carriers` are the `Later` placeholder ("This screen arrives with build step 6" for Carriers, "a later build step" for Consignments); the header's search already goes to `/consignments?q=…`. **Plan runs** (`src/pages/PlanRuns.tsx`) has a tray whose cards have a keyboard menu **Put on a run…** (`ActionSelect`) and stops with **Move to…**; it loads `/v1/runs`, `/v1/consignments?status=packed,failed`, `/v1/vehicles`, `/v1/drivers` together. The map (`src/pages/MapHome.tsx`) polls every 30 s; the app's `public/region.svg` has the drawn labels taken out (step 5), so the design's "4 for carriers" label isn't in it. `src/ui/Photo.tsx` shows a picture fetched with the session.
- **Tests.** API: `conftest.py` (`dispatcher_headers`, `manager_headers`, `all_at_once`, `fresh_client`), `runs_samples.py` (`vehicle`, `driver`, `consignment`, `new_run`, `assign`, `DAY = 2026-10-14`), `test_printing.py`'s `platen` fixture and `_platen_key`, `fake_http.FakeHttp`. Desktop: `harness.tsx` (`stubApi` answers 404 to anything not stubbed, `renderApp`).

### 3. Simple WMS and Platen

- **Simple WMS's ship call** (`simple-wms/api/wms/api/routes/outbound.py:362-377`, `docs/api.md` "POST /v1/deliveries/{ref}/ship"): `ShipIn` takes `carrier` and `tracking_no`, each free text of at most 64 characters, `shipped_by` (64), with the envelope's `message_id` and `owner`. `delivery.shipped` then carries `carrier` and `tracking_no` to every subscriber. So a carrier's name is kept to 64 characters, and a con note to 40 (it must also fit on a label and a manifest line).
- **Platen**: a page template gets one record per entry in `data.lines`, the rest of `data` beside each, as for `run-sheet`; the template is looked up by id, so it must be `carrier-manifest`; a resent `job_id` prints nothing new. The `simple-tms` key's callback (step 2) reports to `POST /v1/print-jobs/{job_id}/status`, which this step leaves as it is (it now finds a manifest's job the same way).

### 4. The designs

- `Carriers.html`: eyebrow "BALLARAT DEPOT · TODAY", **Carriers**, "Freight going with outside carriers. Choose the carrier, type or scan the con note, print the manifest." Three stats: **NEEDS A CON NOTE** (amber; "C-30426 · Mill Lane Florist"), **WAITING FOR PICKUP** ("on the carrier bay · 2 carriers"), **COLLECTED TODAY** ("Plains Parcels · 10:15 am"). The con note box (dashed green): "Con note for C-30426 · Mill Lane Florist", "Scan the carrier's label with the desk scanner, or type the number.", **Carrier** select, **Con note number**, **Save con note**. A group per carrier: icon, name, "Pickup about 3:30 pm · 2 consignments · 7 cartons · 96 kg", a pill ("1 needs a con note" amber, **Ready** green, **Collected** green with a tick), **Print manifest** (disabled with "Add the missing con note to print this manifest."), a table CONSIGNMENT / CUSTOMER / CTN / KG / CON NOTE / STATUS ("Not added yet" and "Needs con note" in amber on an amber row; "On the bay" grey). A collected group: "Collected 10:15 am · manifest M-0192 printed by Platen · marked shipped in Simple WMS" and **Reprint**. Footer: "Manifests print on the dispatch printer through Platen. Carrier bookings are by hand in this version: no carrier connections yet." There is no drawn button for marking a carrier as having been (Decision 6).
- `Consignments.html`: search "WMS delivery, customer or con note", a Day select, tabs **All**, **To plan** (amber count), **On a run**, **With carrier**, **Delivered**, **Not delivered** (red count); a table CONSIGNMENT / CUSTOMER / CTN / WMS DELIVERY / STATUS with pills ("To plan", "Needs con note", "With carrier", "On run R-2208", "Delivered", "Not delivered"); an aside with "CONSIGNMENT · DELIVERED", the number and pill, "Hillside Bakery · Buninyong · 4 cartons · 84 kg · 0.6 m³", a box (From WMS, Run "R-2207 · Kim · stop 5 of 12"), **HISTORY** as a timeline (brown dot for Simple WMS), **PROOF OF DELIVERY** with the signature and photo, "Signed by Priya (printed name)", **Print docket** and **Download POD**.
- `Main.html`: an amber "4 for carriers" label over the drawn carrier bay, and **Carriers** in the drawer bar with an amber count.
- The designs' carrier names are samples ("Ridgeline Express (sample)", "Valley Freight (sample)", "Plains Parcels (sample)"); this plan uses only those, in tests, docs and the manual check. The designs number consignments "C-30431"; the app has no such numbers (Decision 10).

### Decisions this plan makes that were not settled

Each is for the user to confirm or change; the plan is built on them. The ones marked **(you choose)** are real choices with more than one good answer; the rest follow from the design and the code.

1. **Carriers are data, changed by a fleet manager through the API; no screen yet. (you choose)** A `carrier` table: `name` (what Simple WMS is told; 1 to 64 characters, one per name whatever its case, and never "Own fleet", which is your own trucks), `account` (your account with them, printed on the manifest), `pickup_time` (when they usually call, by the depot's clock, shown as "Pickup about 3:30 pm"), `adapter` (`manual`), `active`. `POST`/`PATCH /v1/carriers`, deactivate and reactivate need `fleet:write` (a manager or admin); reading needs `runs:read`. No new scope. Nothing is seeded. **Recommendation:** no screen in this step, as vehicles and drivers have none yet (the README's Known limitations already say so for them); a carriers list in the Fleet drawer later, with theirs. The Carriers drawer says how to add one when there are none.
2. **A manifest is one carrier's pickup on a day, like a run is one truck's day. (you choose)** A consignment is booked onto the carrier's open manifest for the day being planned (made if there is none); there is one open manifest per carrier per day (a partial unique index), and once collected the next booking for that carrier and day starts another (a carrier that calls twice). A manifest left open from an earlier day (the carrier didn't come) is listed with today's as "Waiting since Tue 13 Oct", and can be collected or its consignments moved to today. The manifest number (M-0003) is printed on the paper and is the manifest's id, as R-0007 is a run's. The other shape, one open manifest per carrier whatever the day, has no date to print or plan by, and Simple TMS has no time zone of its own (runs take the dispatcher's date for the same reason). **Recommendation:** per carrier per day.
3. **On a run or with a carrier, never both.** Booking, taking off the bay, con notes and collecting take the same planning lock as runs, and `booking` has a partial unique index (one live booking per consignment). A consignment can be booked from the tray (`packed`, or `failed`, back from a run), off a planned run (history `unassigned`, the run renumbered), or off another carrier's manifest (history `unbooked`); `POST /v1/runs/{id}/assign` takes one off the bay the same way. It becomes `booked` (history `booked` with the carrier, manifest and who). Taken off the bay it is `packed` again, as off a run. Once its run has left, it is delivered, or it is collected, it can't be booked (`409 not_plannable`). A deactivated carrier takes no new bookings (`409 carrier_inactive`); what is already on its manifest can still be collected. New statuses: `booked` and `collected`; new history kinds: `booked`, `unbooked`, `con_note`, `collected`.
4. **The dispatcher sends one to a carrier from Plan runs; the Carriers drawer handles what is on the bay. (you choose)** The tray's cards get a second keyboard menu, **With a carrier…** (shown when there are active carriers), beside **Put on a run…**; a planned stop's **Move to…** lists the carriers too ("With a carrier"). Both book for the day being planned. In Carriers, the con note box's **Carrier** select moves a consignment to another carrier (with the con note typed), and each row has **Tray** to put it back. Dragging onto a "carrier bay" lane on the plan board isn't added: the board is the runs', and the menus are the keyboard way the board already has. **Recommendation:** as above; a picker of the tray inside Carriers is easy to add if the person at the carrier desk isn't the one planning.
5. **Con notes: tidied, checked, one consignment's with a carrier for good, editable until collected. (you choose the uniqueness)** Typed or scanned (a desk scanner is a keyboard wedge: it types the number and presses Enter, which submits the box, so no scanner code is needed): spaces around it are taken off and letters made capitals; it must then be 4 to 40 letters, digits and dashes starting with a letter or digit, or it is a `422` on `con_note` (a space inside is refused, not guessed at). A con note is one consignment's with a carrier, for good (a partial unique index on `(carrier_id, con_note)` over bookings not taken off, and, under the planning lock, a check over every booking, taken off or collected too): scanning an old label by mistake is caught, and the `422` names the consignment that has it. The same consignment may have it again (back on the bay, or moved to another day); one put right (a typo corrected) is free. *(Settled after review: unique per carrier for good, including after a consignment is taken off the bay.)* Another carrier's numbers are its own. A con note is put right by setting it again until the carrier collects: each new one is a `con_note` history row naming the one it replaced (`was`); the same again writes nothing. Moved to another day with the same carrier, a consignment keeps its con note; to another carrier it doesn't. **Recommendation:** unique for good. If a carrier reuses numbers after a year or two, the choice is to make it unique only while waiting on the bay (one change to the index's `where`).
6. **Mark collected is the ship call, as Leaves now is for a run.** The moment freight leaves our hands is when Simple WMS should hear it shipped: booking is too early (it can still move to a run), and a con note is often in hours before the carrier calls. So **Mark collected** (a button the design doesn't draw, next to **Print manifest**, with a confirm: "Ridgeline Express (sample) has collected 2 consignments? Each is marked shipped in Simple WMS with its con note.") calls `POST /v1/manifests/{id}/collect` with a `message_id` and the consignment ids the dispatcher saw. In one transaction each consignment becomes `collected` with a `collected` history row and a ship call is queued (`carrier` = the carrier's name, `tracking_no` = the con note, its own `message_id`), and the manifest is collected with when and who. Refused: `409 stale` when what is on it isn't what was seen (someone booked one meanwhile), `con_note_missing` (the detail names the first), `manifest_empty`. Collected again with just those on it is answered as it is (a second desk). A ship call that gives up is tried again with `POST /v1/manifests/{id}/retry-ship`, as for a run. `outbound_call` and `print_job` get a `manifest_id`, `run_id` becomes nullable, and a check constraint keeps exactly one of the two.
7. **Manifests print through Platen, not with a con note missing, and collecting doesn't wait on printing.** `POST /v1/manifests/{id}/print` queues template `carrier-manifest` `v1`, `reference {type: "manifest", ref: "M-0003"}`, the data the README's sample template binds to (the manifest, its date, the carrier, account, pickup time, counts and a line per consignment with its con note, address, packages and kg; text or whole numbers, never null), to `TMS_MANIFEST_PRINTER` (new; empty means the run sheets' printer, as the design says "the dispatch printer"). Each press prints again: before collection, what is on the bay; after, what went (**Reprint**). Refused `409 con_note_missing` (the carrier's driver signs for each line by its con note, and the design disables the button) and `manifest_empty`. Collecting doesn't need a printed manifest: a printer that is down must not hold freight at the dock. The worker and Platen's callback are unchanged.
8. **The adapter shape, and no more.** `carrier.adapter` names how a carrier is booked; `manual` is the only one, and the API refuses any other. `api/tms/services/carrier_adapters.py` holds the shape a carrier's API will fill (`book(carrier, consignment) -> con note | None`, `track(carrier, con_note) -> [events]`), the `Manual` adapter (books nothing: a person types the con note; hears nothing back) and the registry. Its docstring says how one plugs in later: its calls go through the worker's queue as an `outbound_call` of its own kind, never inside a request or the planning lock; the con note it gets is set with `by: "adapter:<name>"`, and what tracking says becomes history rows. Nothing calls it in this step (YAGNI: there is no second adapter to call), and the data needs no change for one.
9. **The map and the feed.** "N for carriers" (amber dot) over the drawn carrier bay counts the consignments on open manifests and opens Carriers; none waiting, no label. The **Carriers** button carries an amber count of those without a con note, as `Main.html` draws it. The feed gains `con_note` ("Con note RX-77310042 for Hilltop Joinery · Ridgeline Express (sample)", amber) and `collected` ("Ridgeline Express (sample) collected 2 consignments · M-0003", green), read from the history and the manifests; `booked` and `unbooked` are left out, as step 5 left out assigning (the dispatcher's own doing). A con note is in because, in many depots, the person scanning labels isn't the one planning.
10. **Build the Consignments drawer now. (you choose)** It is designed, every step so far has pointed at it ("Search by WMS delivery, customer or con note"), the header's search box already opens it, and carriers make it needed: a con note is what a customer quotes on the phone. Built to `Consignments.html`: the search (now also matching the con note), the six tabs with their counts (**To plan** counts `packed` and `failed`, as the map's **To plan** does, so it overlaps **Not delivered**), the table with where each one is now, and the aside with the box, the history as a timeline in words (`src/lib/history.ts`) and the proof of delivery (signature and photo through `Photo`, "Signed by Priya (printed name)"). `GET /v1/consignments` and `/{id}` gain `run` (the run it is on, or was last on) and `carrier` (the carrier it is booked with or collected by, with the con note), read for the page at once. Left out: the **Day** select (no list filter by day yet; "today" would hide yesterday's freight still waiting), **Print docket** (there is no docket template; the design's "delivery dockets" are their own step) and **Download POD** (the pictures show in the aside); and "C-30431" numbers: the WMS delivery ref is the consignment's name everywhere, as Plan runs and the feed already have it. **Recommendation:** build it now, as here; or leave it as the placeholder and cut Task 12 (the API part, Task 6, is still wanted for the con note search and the bay's statuses).
11. **What carriers don't change.** "Came back but still shipped in Simple WMS" (README, Known limitations) stays as it is: carriers don't touch the own-fleet failed-delivery path. And two more gaps of the same kind are said plainly in the README: Simple TMS doesn't follow freight after a carrier collects it (it stays `collected`; the carrier's own tracking says where it is), and freight a carrier brings back isn't recorded (it stays collected, and shipped in Simple WMS). Both wait on a carrier adapter with tracking, and on the Simple WMS side's "came back" design.
12. **Events out stay deferred** (`consignment.collected` would join `consignment.delivered` and the rest), as in steps 3 to 5.

**For you to decide, in short:** a screen for carriers now or later (1, recommended: later, with vehicles and drivers); a manifest per carrier per day (2, recommended); where a consignment is sent with a carrier from (4, recommended: Plan runs, with moves and **Tray** in Carriers); con notes unique for good or only while waiting (5, recommended: for good); building the Consignments drawer in this step (10, recommended: yes). Decisions 6 (collected is the ship call) and 7 (no print without every con note; collecting doesn't wait on printing) are recommended as they stand; say if your carriers work otherwise.

### Ports and addresses, locally

Simple TMS API `127.0.0.1:8022`, Caddy `https://localhost:8543` (desktop at `/`, driver app at `/driver/`), database `127.0.0.1:5434` on migration `0007` before this step. Users `leighton` (admin, `a long enough password`) and `dee` (dispatcher, `dispatch password`); driver `kim`, PIN `2468`. Simple WMS at `127.0.0.1:8011`, with the `simple-tms-ship` key (`tasks:read`, `tasks:write`) in Simple TMS's `.env`. Platen at `http://localhost:18000` (its admin password is in `platen/.env`), with the `simple-tms` key whose callback reports to Simple TMS, and the stand-in page printer `office` on port 9103 (it may not be running). Chrome needs `--ignore-certificate-errors` for Caddy's local certificate.

---

## Before you start

```
cd "/Users/leightonlauton/Q7 Technology/Software/q7/simple-tms"
git switch main && git pull --ff-only
git switch -c step-6-carriers
docker compose up -d db                      # the tests' PostgreSQL on 127.0.0.1:5434
cd api && caffeinate -i .venv/bin/pytest -q  # expect: 357 passed
cd ../apps/desktop && npm test               # expect: 91 passed
cd ../driver && npm test                     # expect: 109 passed
```

The plan has three parts: **A**, the API (Tasks 1–8); **B**, the desktop (Tasks 9–13); **C**, the docs and the checks, ending with the manual check on the local stacks (Tasks 14–16). Part B stubs the API in its tests, so it doesn't wait on Part A except for the manual check. `pytest` runs from `api/` (with `caffeinate -i` in front for the whole suite: this Mac sleeps on battery and the suite's 15-minute tokens then expire mid-run), `npm` from the app's folder. Code is given whole for a new file, and as a unified diff for a changed one, against the file as the tasks before it leave it (`main` for its first change); apply it by hand, or save it and `git apply`. Commit after each task as shown, on the branch.

## Part A — the API

### Task 1: The carrier tables, a ship call or print job of a manifest's, and the manifest printer

**Files:**
- Create: `api/tms/models/carriers.py`, `api/alembic/versions/0008_carriers.py`
- Modify: `api/tms/models/__init__.py`, `api/tms/models/integration.py`, `api/tms/models/consignment.py` (comments only), `api/tms/config.py`
- Test: `api/tests/test_config.py`, `api/tests/test_migrations.py`

**Step 1: Write the failing tests**

Change `api/tests/test_config.py`:
```diff
--- a/api/tests/test_config.py
+++ b/api/tests/test_config.py
@@ -85,3 +85,10 @@ def test_a_retention_period_of_no_days_refuses_to_start():
 def test_a_tile_origin_that_isnt_the_tiles_own_refuses_to_start(url, origin):
     with pytest.raises(ValidationError, match="TMS_MAP_TILE_ORIGIN"):
         Settings(secret_key=KEY, map_tile_url=url, map_tile_origin=origin)
+
+
+def test_manifests_go_to_the_run_sheet_printer_unless_told():
+    s = Settings(secret_key=KEY, run_sheet_printer="office")
+    assert s.manifest_printer == "" and s.manifest_printer_or_default == "office"
+    s = Settings(secret_key=KEY, run_sheet_printer="office", manifest_printer="bay")
+    assert s.manifest_printer_or_default == "bay"
```

Change `api/tests/test_migrations.py`:
```diff
--- a/api/tests/test_migrations.py
+++ b/api/tests/test_migrations.py
@@ -14,7 +14,7 @@ TABLES = {"user", "user_session", "driver", "vehicle", "consignment", "package",
           "consignment_event", "inbound_event", "run", "stop", "outbound_call", "print_job",
           "api_key", "inbound_message", "pod_file", "pod",
           "checklist_item", "prestart_check", "prestart_item", "vehicle_record",
-          "position", "alembic_version"}
+          "position", "carrier", "manifest", "booking", "alembic_version"}
 
 
 @pytest.fixture
```

**Step 2: Run them to see them fail**

Run: `.venv/bin/pytest tests/test_config.py tests/test_migrations.py -q`
Expected: `2 failed, 32 passed`: `AttributeError: 'Settings' object has no attribute 'manifest_printer'`, and `test_migrations_go_up_down_and_up_again` (`carrier`, `manifest` and `booking` are not among the tables).

**Step 3: Implement**

Create `api/tms/models/carriers.py`:
```python
from __future__ import annotations

from datetime import date, datetime, time

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, String, Time, and_, text
from sqlalchemy.orm import Mapped, foreign, mapped_column, relationship

from tms.models.base import Base, created_at_column
from tms.models.consignment import Consignment


class Carrier(Base):
    """An outside carrier the business books freight with. Booked by hand in
    version one: the dispatcher types or scans the con note. `adapter` says
    how a carrier is booked ("manual"); a carrier's own API, added later, is
    another adapter, with no change to the data. Deactivated, never deleted."""

    __tablename__ = "carrier"

    id: Mapped[int] = mapped_column(primary_key=True)
    # what Simple WMS is told as the carrier when it collects (at most 64 there)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    # the business's account with the carrier, printed on the manifest
    account: Mapped[str | None] = mapped_column(String(40))
    # when it usually calls, by the depot's clock: "Pickup about 3:30 pm"
    pickup_time: Mapped[time | None] = mapped_column(Time)
    adapter: Mapped[str] = mapped_column(String(32), default="manual")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = created_at_column()


class Manifest(Base):
    """One carrier's pickup: the consignments waiting on the carrier bay for
    it on a day, then, once it has called, what it took. Open until
    collected; one open manifest per carrier per day, and a carrier that
    calls twice in a day gets a second one."""

    __tablename__ = "manifest"
    __table_args__ = (
        Index("uq_manifest_carrier_id_day_open", "carrier_id", "day", unique=True,
              postgresql_where=text("collected_at is null")),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    carrier_id: Mapped[int] = mapped_column(ForeignKey("carrier.id"), index=True)
    # the day it is for, by the dispatcher's calendar, as a run's is
    day: Mapped[date] = mapped_column(Date, index=True)
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = created_at_column()
    collected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    collected_by: Mapped[str | None] = mapped_column(String(64))

    carrier: Mapped[Carrier] = relationship()
    # the consignments on it now, oldest first; ones taken off keep their row
    lines: Mapped[list[Booking]] = relationship(
        primaryjoin=lambda: and_(Manifest.id == foreign(Booking.manifest_id),
                                 Booking.removed_at.is_(None)),
        order_by=lambda: Booking.id, viewonly=True)

    @property
    def number(self) -> str:
        """What the dispatcher and the carrier's driver call it: M-0042."""
        return f"M-{self.id:04d}"


class Booking(Base):
    """A consignment's place on a carrier's manifest, with its con note once
    it is typed or scanned. Taken off the bay, the row stays with removed_at
    set: cancel, never delete. A con note is put right by setting it again,
    and each one set is a row in the consignment's history. `carrier_id` is
    the manifest's, kept here so a con note is one carrier's once."""

    __tablename__ = "booking"
    __table_args__ = (
        # a consignment is with one carrier at a time, and never on a run too
        # (the planning lock sees to that)
        Index("uq_booking_consignment_id_live", "consignment_id", unique=True,
              postgresql_where=text("removed_at is null")),
        # a con note is one consignment's, with that carrier, for good
        Index("uq_booking_carrier_id_con_note", "carrier_id", "con_note", unique=True,
              postgresql_where=text("removed_at is null and con_note is not null")),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    manifest_id: Mapped[int] = mapped_column(ForeignKey("manifest.id"), index=True)
    carrier_id: Mapped[int] = mapped_column(ForeignKey("carrier.id"))
    consignment_id: Mapped[int] = mapped_column(ForeignKey("consignment.id"))
    con_note: Mapped[str | None] = mapped_column(String(40))
    added_by: Mapped[str] = mapped_column(String(64))
    added_at: Mapped[datetime] = created_at_column()
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    consignment: Mapped[Consignment] = relationship()
```

Create `api/alembic/versions/0008_carriers.py`:
```python
"""carriers: who else takes freight, each carrier's manifest for a pickup,
and each consignment's booking on one with its con note; a ship call and a
print job can be a manifest's

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-15
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

QUEUES = ("outbound_call", "print_job")


def _at(name: str = "created_at") -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.text("now()"),
                     nullable=False)


def upgrade() -> None:
    op.create_table(
        "carrier",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("account", sa.String(length=40), nullable=True),
        sa.Column("pickup_time", sa.Time(), nullable=True),
        sa.Column("adapter", sa.String(length=32), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        _at(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_carrier")),
        sa.UniqueConstraint("name", name=op.f("uq_carrier_name")),
    )
    op.create_table(
        "manifest",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("carrier_id", sa.Integer(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        _at(),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("collected_by", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(["carrier_id"], ["carrier.id"],
                                name=op.f("fk_manifest_carrier_id_carrier")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_manifest")),
    )
    op.create_index(op.f("ix_manifest_carrier_id"), "manifest", ["carrier_id"])
    op.create_index(op.f("ix_manifest_day"), "manifest", ["day"])
    op.create_index("uq_manifest_carrier_id_day_open", "manifest", ["carrier_id", "day"],
                    unique=True, postgresql_where=sa.text("collected_at is null"))
    op.create_table(
        "booking",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("manifest_id", sa.Integer(), nullable=False),
        sa.Column("carrier_id", sa.Integer(), nullable=False),
        sa.Column("consignment_id", sa.Integer(), nullable=False),
        sa.Column("con_note", sa.String(length=40), nullable=True),
        sa.Column("added_by", sa.String(length=64), nullable=False),
        _at("added_at"),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["manifest_id"], ["manifest.id"],
                                name=op.f("fk_booking_manifest_id_manifest")),
        sa.ForeignKeyConstraint(["carrier_id"], ["carrier.id"],
                                name=op.f("fk_booking_carrier_id_carrier")),
        sa.ForeignKeyConstraint(["consignment_id"], ["consignment.id"],
                                name=op.f("fk_booking_consignment_id_consignment")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_booking")),
    )
    op.create_index(op.f("ix_booking_manifest_id"), "booking", ["manifest_id"])
    op.create_index("uq_booking_consignment_id_live", "booking", ["consignment_id"],
                    unique=True, postgresql_where=sa.text("removed_at is null"))
    op.create_index("uq_booking_carrier_id_con_note", "booking", ["carrier_id", "con_note"],
                    unique=True,
                    postgresql_where=sa.text("removed_at is null and con_note is not null"))

    # a ship call or a print job is a run's or, now, a manifest's
    for table in QUEUES:
        op.add_column(table, sa.Column("manifest_id", sa.Integer(), nullable=True))
        op.create_foreign_key(op.f(f"fk_{table}_manifest_id_manifest"), table, "manifest",
                              ["manifest_id"], ["id"])
        op.create_index(op.f(f"ix_{table}_manifest_id"), table, ["manifest_id"])
        op.alter_column(table, "run_id", existing_type=sa.Integer(), nullable=True)
        op.create_check_constraint(op.f(f"ck_{table}_run_or_manifest"), table,
                                   "num_nonnulls(run_id, manifest_id) = 1")


def downgrade() -> None:
    for table in QUEUES:
        # a manifest's calls and prints can't be a run's: they go with the manifests
        op.execute(f"delete from {table} where manifest_id is not null")
        op.drop_constraint(op.f(f"ck_{table}_run_or_manifest"), table, type_="check")
        op.alter_column(table, "run_id", existing_type=sa.Integer(), nullable=False)
        op.drop_index(op.f(f"ix_{table}_manifest_id"), table_name=table)
        op.drop_constraint(op.f(f"fk_{table}_manifest_id_manifest"), table, type_="foreignkey")
        op.drop_column(table, "manifest_id")
    op.drop_index("uq_booking_carrier_id_con_note", table_name="booking")
    op.drop_index("uq_booking_consignment_id_live", table_name="booking")
    op.drop_index(op.f("ix_booking_manifest_id"), table_name="booking")
    op.drop_table("booking")
    op.drop_index("uq_manifest_carrier_id_day_open", table_name="manifest")
    op.drop_index(op.f("ix_manifest_day"), table_name="manifest")
    op.drop_index(op.f("ix_manifest_carrier_id"), table_name="manifest")
    op.drop_table("manifest")
    op.drop_table("carrier")
```

Change `api/tms/models/__init__.py`:
```diff
--- a/api/tms/models/__init__.py
+++ b/api/tms/models/__init__.py
@@ -6,10 +6,11 @@ from tms.models.integration import InboundEvent, OutboundCall, PrintJob
 from tms.models.runs import Run, Stop
 from tms.models.delivery import InboundMessage, Pod, PodFile
 from tms.models.location import Position
+from tms.models.carriers import Booking, Carrier, Manifest
 
 __all__ = [
     "Base", "User", "UserSession", "Driver", "Vehicle", "Consignment", "Package",
     "ConsignmentEvent", "InboundEvent", "Run", "Stop", "OutboundCall", "PrintJob", "ApiKey",
     "InboundMessage", "Pod", "PodFile", "ChecklistItem", "PrestartCheck", "PrestartItem",
-    "VehicleRecord", "Position",
+    "VehicleRecord", "Position", "Carrier", "Manifest", "Booking",
 ]
```

Change `api/tms/models/integration.py`:
```diff
--- a/api/tms/models/integration.py
+++ b/api/tms/models/integration.py
@@ -3,7 +3,7 @@ from __future__ import annotations
 import uuid
 from datetime import datetime
 
-from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Uuid
+from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Uuid
 from sqlalchemy.dialects.postgresql import JSONB
 from sqlalchemy.orm import Mapped, mapped_column
 
@@ -25,12 +25,15 @@ class InboundEvent(Base):
 
 
 class OutboundCall(Base):
-    """A call to Simple WMS, waiting or made: today the ship call when a run
-    departs. The worker makes it, retrying after 1 min, 5, 30 and 2 h; the
-    same message_id goes with every try, so Simple WMS acts on it once."""
+    """A call to Simple WMS, waiting or made: the ship call when a run
+    departs, or when a carrier collects. The worker makes it, retrying after
+    1 min, 5, 30 and 2 h; the same message_id goes with every try, so Simple
+    WMS acts on it once. It belongs to a run or to a manifest, never both."""
 
     __tablename__ = "outbound_call"
-    __table_args__ = (Index("ix_outbound_call_due", "status", "next_attempt_at"),)
+    __table_args__ = (Index("ix_outbound_call_due", "status", "next_attempt_at"),
+                      CheckConstraint("num_nonnulls(run_id, manifest_id) = 1",
+                                      name="run_or_manifest"))
 
     id: Mapped[int] = mapped_column(primary_key=True)
     message_id: Mapped[uuid.UUID] = mapped_column(Uuid, unique=True)
@@ -39,7 +42,8 @@ class OutboundCall(Base):
     # the Simple WMS path, e.g. /v1/deliveries/0080012345/ship, and the body
     path: Mapped[str] = mapped_column(String(200))
     body: Mapped[dict] = mapped_column(JSONB)
-    run_id: Mapped[int] = mapped_column(ForeignKey("run.id"), index=True)
+    run_id: Mapped[int | None] = mapped_column(ForeignKey("run.id"), index=True)
+    manifest_id: Mapped[int | None] = mapped_column(ForeignKey("manifest.id"), index=True)
     consignment_id: Mapped[int] = mapped_column(ForeignKey("consignment.id"), index=True)
     # pending, done, failed
     status: Mapped[str] = mapped_column(String(16), default="pending")
@@ -53,14 +57,18 @@ class OutboundCall(Base):
 class PrintJob(Base):
     """A document for Platen: a template name and JSON, never a rendered
     page. The worker sends it with the same backoff as the ship calls;
-    Platen says printed or failed on POST /v1/print-jobs/{job_id}/status."""
+    Platen says printed or failed on POST /v1/print-jobs/{job_id}/status. A
+    run's run sheet, or a carrier's manifest."""
 
     __tablename__ = "print_job"
-    __table_args__ = (Index("ix_print_job_due", "status", "next_attempt_at"),)
+    __table_args__ = (Index("ix_print_job_due", "status", "next_attempt_at"),
+                      CheckConstraint("num_nonnulls(run_id, manifest_id) = 1",
+                                      name="run_or_manifest"))
 
     id: Mapped[int] = mapped_column(primary_key=True)
     job_id: Mapped[uuid.UUID] = mapped_column(Uuid, unique=True)
-    run_id: Mapped[int] = mapped_column(ForeignKey("run.id"), index=True)
+    run_id: Mapped[int | None] = mapped_column(ForeignKey("run.id"), index=True)
+    manifest_id: Mapped[int | None] = mapped_column(ForeignKey("manifest.id"), index=True)
     template: Mapped[str] = mapped_column(String(64))
     version: Mapped[str] = mapped_column(String(16))
     printer: Mapped[str] = mapped_column(String(64))
```

Change `api/tms/models/consignment.py` (the comments say what the statuses and history kinds now are):
```diff
--- a/api/tms/models/consignment.py
+++ b/api/tms/models/consignment.py
@@ -24,7 +24,9 @@ class Consignment(Base):
     customer: Mapped[str] = mapped_column(String(200))
     ship_to: Mapped[dict] = mapped_column(JSONB, default=dict)
     required_by: Mapped[date | None] = mapped_column(Date)
-    # packed for now; assigned, departed, delivered, failed arrive with runs
+    # packed (in the tray), assigned (on a planned run), departed, delivered,
+    # failed (came back: in the tray again), booked (on the carrier bay),
+    # collected (the carrier has it)
     status: Mapped[str] = mapped_column(String(16), default="packed", index=True)
     created_at: Mapped[datetime] = created_at_column()
 
@@ -61,7 +63,8 @@ class ConsignmentEvent(Base):
 
     id: Mapped[int] = mapped_column(primary_key=True)
     consignment_id: Mapped[int] = mapped_column(ForeignKey("consignment.id"), index=True)
-    # packed, then assigned, departed, delivered, failed with later steps
+    # packed, assigned, unassigned, departed, delivered, failed, booked,
+    # unbooked, con_note, collected
     kind: Mapped[str] = mapped_column(String(16))
     at: Mapped[datetime] = created_at_column()
     detail: Mapped[dict] = mapped_column(JSONB, default=dict)
```

Change `api/tms/config.py`:
```diff
--- a/api/tms/config.py
+++ b/api/tms/config.py
@@ -37,6 +37,8 @@ class Settings(BaseSettings):
     platen_url: str = ""
     platen_key: str = ""
     run_sheet_printer: str = "dispatch"
+    # The Platen printer carrier manifests go to; empty, the run sheets' one.
+    manifest_printer: str = ""
 
     # Proof of delivery: where signatures and photos are kept on disk (a
     # volume in Compose). Only the API reads and writes them.
@@ -67,6 +69,10 @@ class Settings(BaseSettings):
     worker_poll_seconds: float = 5.0
     worker_http_timeout_seconds: float = 10.0
 
+    @property
+    def manifest_printer_or_default(self) -> str:
+        return self.manifest_printer.strip() or self.run_sheet_printer
+
     @field_validator("secret_key")
     @classmethod
     def _strong_secret_key(cls, value: str) -> str:
```

**Step 4: Run them, and the whole suite**

Run: `.venv/bin/pytest tests/test_config.py tests/test_migrations.py -q`
Expected: `34 passed` (`test_the_models_match_the_migrations` included: the models and `0008` agree).

Run: `caffeinate -i .venv/bin/pytest -q`
Expected: `358 passed`. Nothing else notices `run_id` becoming nullable.

**Step 5: Commit**

```bash
git add api/tms/models/carriers.py api/alembic/versions/0008_carriers.py api/tms/models/__init__.py \
  api/tms/models/integration.py api/tms/models/consignment.py api/tms/config.py \
  api/tests/test_config.py api/tests/test_migrations.py
git commit -m "Keep carriers, their manifests and each consignment's booking with its con note"
```

### Task 2: Carriers, added and changed by a fleet manager; the adapter shape

**Files:**
- Create: `api/tms/services/carrier_adapters.py`, `api/tms/api/routes/carriers.py`
- Modify: `api/tms/api/app.py`
- Test: `api/tests/carriers_samples.py` (new), `api/tests/test_carriers.py` (new)

**Step 1: Write the failing tests**

Create `api/tests/carriers_samples.py` (used by the tests of Tasks 2 to 7):
```python
"""Carriers made through the API, and consignments sent with them: what the
carrier tests start from. The names are samples, never real carriers."""
from runs_samples import DAY

RIDGELINE = "Ridgeline Express (sample)"
VALLEY = "Valley Freight (sample)"


def new_carrier(client, headers, name=RIDGELINE, **more) -> dict:
    r = client.post("/v1/carriers", headers=headers, json={"name": name, **more})
    assert r.status_code == 201, r.text
    return r.json()


def book(client, headers, carrier_id: int, consignment_id: int, day=DAY, **more):
    return client.post(f"/v1/carriers/{carrier_id}/book", headers=headers,
                       json={"consignment_id": consignment_id, "date": day.isoformat(), **more})


def con_note(client, headers, manifest_id: int, consignment_id: int, note: str):
    return client.post(f"/v1/manifests/{manifest_id}/con-note", headers=headers,
                       json={"consignment_id": consignment_id, "con_note": note})


def lines(manifest: dict) -> list[tuple[int, str | None]]:
    return [(line["consignment"]["id"], line["con_note"]) for line in manifest["lines"]]
```

Create `api/tests/test_carriers.py`:
```python
"""Carriers: who else takes freight. Added and changed by a fleet manager,
read by the office, deactivated and never deleted. Booked by hand in version
one; an adapter for a carrier's own API slots in later."""
from carriers_samples import RIDGELINE, VALLEY, new_carrier
from tms.services import carrier_adapters


def test_a_fleet_manager_adds_a_carrier(client, manager_headers):
    r = client.post("/v1/carriers", headers=manager_headers,
                    json={"name": "  Ridgeline Express (sample) ", "account": "RX-4410",
                          "pickup_time": "15:30"})
    assert r.status_code == 201, r.text
    c = r.json()
    assert (c["name"], c["account"], c["pickup_time"], c["adapter"], c["active"]) == (
        RIDGELINE, "RX-4410", "15:30", "manual", True)
    assert client.get(f"/v1/carriers/{c['id']}", headers=manager_headers).json() == c


def test_the_office_lists_them_and_only_a_fleet_manager_changes_them(
        client, manager_headers, dispatcher_headers):
    new_carrier(client, manager_headers, VALLEY)
    new_carrier(client, manager_headers, RIDGELINE)
    listed = client.get("/v1/carriers", headers=dispatcher_headers).json()
    assert [c["name"] for c in listed["items"]] == [RIDGELINE, VALLEY] and listed["total"] == 2
    r = client.post("/v1/carriers", headers=dispatcher_headers, json={"name": "Plains Parcels (sample)"})
    assert r.status_code == 403


def test_a_name_is_one_carrier_whatever_its_case(client, manager_headers):
    new_carrier(client, manager_headers)
    r = client.post("/v1/carriers", headers=manager_headers, json={"name": RIDGELINE.upper()})
    assert r.status_code == 422
    assert r.json()["errors"] == [{"field": "name",
                                   "message": f"carrier {RIDGELINE.upper()} already exists"}]


def test_own_fleet_is_not_a_carrier(client, manager_headers):
    r = client.post("/v1/carriers", headers=manager_headers, json={"name": "own FLEET"})
    assert r.status_code == 422
    assert "Own fleet is what Simple WMS is told for your own trucks" in r.json()["errors"][0]["message"]


def test_an_adapter_there_is_no_code_for_is_refused(client, manager_headers):
    r = client.post("/v1/carriers", headers=manager_headers,
                    json={"name": RIDGELINE, "adapter": "teleport"})
    assert r.status_code == 422
    assert r.json()["errors"] == [{"field": "adapter",
                                   "message": "teleport isn't an adapter Simple TMS has: manual"}]


def test_a_change_keeps_what_is_left_out_and_clears_what_is_null(client, manager_headers):
    c = new_carrier(client, manager_headers, account="RX-4410", pickup_time="15:30")
    r = client.patch(f"/v1/carriers/{c['id']}", headers=manager_headers,
                     json={"pickup_time": "14:00", "account": None})
    assert r.status_code == 200, r.text
    assert (r.json()["name"], r.json()["pickup_time"], r.json()["account"]) == (RIDGELINE, "14:00", None)
    other = new_carrier(client, manager_headers, VALLEY)
    r = client.patch(f"/v1/carriers/{other['id']}", headers=manager_headers, json={"name": RIDGELINE})
    assert r.status_code == 422


def test_deactivated_not_deleted(client, manager_headers, dispatcher_headers):
    c = new_carrier(client, manager_headers)
    r = client.post(f"/v1/carriers/{c['id']}/deactivate", headers=manager_headers)
    assert r.status_code == 200 and r.json()["active"] is False
    assert client.get("/v1/carriers", headers=dispatcher_headers, params={"active": True}).json()["total"] == 0
    assert client.get("/v1/carriers", headers=dispatcher_headers).json()["total"] == 1
    r = client.post(f"/v1/carriers/{c['id']}/reactivate", headers=manager_headers)
    assert r.json()["active"] is True
    assert client.get("/v1/carriers/99", headers=dispatcher_headers).status_code == 404


def test_the_manual_adapter_books_nothing_a_person_types_the_con_note():
    manual = carrier_adapters.ADAPTERS["manual"]
    assert isinstance(manual, carrier_adapters.Adapter)
    assert manual.book(None, None) is None
    assert manual.track(None, "RX-77310042") == []
    assert list(carrier_adapters.ADAPTERS) == ["manual"]
```

**Step 2: Run them to see them fail**

Run: `.venv/bin/pytest tests/test_carriers.py -q`
Expected: an error while collecting: `ImportError: cannot import name 'carrier_adapters' from 'tms.services'`.

**Step 3: Implement**

Create `api/tms/services/carrier_adapters.py`:
```python
"""How a consignment is booked with a carrier, and later followed.

Version one books by hand: the dispatcher books with the carrier as they
always have, then types or scans the con note into Simple TMS. That is the
"manual" adapter, and every carrier has it.

A carrier's own API comes later as another adapter, named in `carrier.adapter`.
It changes nothing in the data: a booking's con note is set by the adapter
instead of a person (its history row says `by: "adapter:<name>"`), and what
it hears back from tracking becomes rows in the consignment's history. Its
calls go through the worker's queue (an `outbound_call` of their own kind)
with the same backoff as the ship calls, never inside a request, so a slow
carrier never holds the planning lock or the dispatcher's screen."""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Adapter(Protocol):
    def book(self, carrier, consignment) -> str | None:
        """Book the consignment with the carrier: its con note, or None when
        a person books it and types the con note."""

    def track(self, carrier, con_note: str) -> list[dict]:
        """What the carrier says has happened to it since it was collected,
        oldest first: [{"at", "kind", "detail"}]. None known: []."""


class Manual:
    """Booked by hand: nothing to call, and nothing to hear back."""

    def book(self, carrier, consignment) -> str | None:
        return None

    def track(self, carrier, con_note: str) -> list[dict]:
        return []


# what `carrier.adapter` may name
ADAPTERS: dict[str, Adapter] = {"manual": Manual()}
```

Create `api/tms/api/routes/carriers.py`:
```python
"""Carriers: who else takes freight. Created, changed, deactivated by a
fleet manager; never deleted."""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from tms.api.deps import DB, Principal, require
from tms.api.errors import FieldError, NotFound, violated_constraint
from tms.api.schemas import Page
from tms.models import Carrier
from tms.services.carrier_adapters import ADAPTERS
from tms.services.runs import OWN_FLEET

router = APIRouter(tags=["carriers"])


class CarrierIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    # what Simple WMS is told when it collects: at most 64 there too
    name: str = Field(min_length=1, max_length=64)
    account: str | None = Field(default=None, max_length=40)
    pickup_time: dt.time | None = None
    # how it is booked: "manual" (by hand) is the only one yet
    adapter: str = Field(default="manual", max_length=32)


# PATCH fields that an explicit null clears
CLEARABLE = {"account", "pickup_time"}


class CarrierPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str | None = Field(default=None, min_length=1, max_length=64)
    account: str | None = Field(default=None, max_length=40)
    pickup_time: dt.time | None = None
    adapter: str | None = Field(default=None, max_length=32)


class CarrierOut(BaseModel):
    id: int
    name: str
    account: str | None
    # by the depot's clock, "15:30", or null
    pickup_time: str | None
    adapter: str
    active: bool
    created_at: dt.datetime


def carrier_out(c: Carrier) -> CarrierOut:
    return CarrierOut(id=c.id, name=c.name, account=c.account,
                      pickup_time=c.pickup_time.strftime("%H:%M") if c.pickup_time else None,
                      adapter=c.adapter, active=c.active, created_at=c.created_at)


def get_carrier(db, carrier_id: int) -> Carrier:
    c = db.get(Carrier, carrier_id)
    if c is None:
        raise NotFound(f"no carrier {carrier_id}")
    return c


def _name_free(db, name: str, other_than: int | None = None) -> None:
    """One carrier per name, whatever its case; and never our own trucks."""
    if name.casefold() == OWN_FLEET.casefold():
        raise FieldError("name", f"{OWN_FLEET} is what Simple WMS is told for your own trucks; "
                                 "give the carrier its own name")
    taken = db.execute(select(Carrier.id).where(
        func.lower(Carrier.name) == name.lower(), Carrier.id != (other_than or 0))).first()
    if taken is not None:
        raise FieldError("name", f"carrier {name} already exists")


def _known_adapter(adapter: str | None) -> None:
    if adapter is not None and adapter not in ADAPTERS:
        raise FieldError("adapter", f"{adapter} isn't an adapter Simple TMS has: "
                                    f"{', '.join(ADAPTERS)}")


def _commit(db, name: str) -> None:
    try:
        db.commit()
    except IntegrityError as e:
        # another request took the name between the check and this write
        db.rollback()
        if violated_constraint(e) == "uq_carrier_name":
            raise FieldError("name", f"carrier {name} already exists") from None
        raise


@router.post("/carriers", status_code=201, response_model=CarrierOut)
def create_carrier(body: CarrierIn, db: DB, who: Principal = require("fleet:write")):
    _known_adapter(body.adapter)
    _name_free(db, body.name)
    c = Carrier(name=body.name, account=body.account or None, pickup_time=body.pickup_time,
                adapter=body.adapter, active=True)
    db.add(c)
    _commit(db, body.name)
    return JSONResponse(status_code=201, content=carrier_out(c).model_dump(mode="json"))


@router.get("/carriers", response_model=Page[CarrierOut])
def list_carriers(db: DB, active: bool | None = None, who: Principal = require("runs:read")):
    """By name."""
    q = select(Carrier)
    if active is not None:
        q = q.where(Carrier.active.is_(active))
    rows = db.execute(q.order_by(func.lower(Carrier.name))).scalars().all()
    return Page(items=[carrier_out(c) for c in rows], total=len(rows))


@router.get("/carriers/{id}", response_model=CarrierOut)
def read_carrier(id: int, db: DB, who: Principal = require("runs:read")):
    return carrier_out(get_carrier(db, id))


@router.patch("/carriers/{id}", response_model=CarrierOut)
def change_carrier(id: int, body: CarrierPatch, db: DB, who: Principal = require("fleet:write")):
    """Only what is given changes; `account` or `pickup_time` given as null is
    cleared. A new name is what Simple WMS is told from the next collection on."""
    c = get_carrier(db, id)
    _known_adapter(body.adapter)
    if body.name is not None:
        _name_free(db, body.name, other_than=c.id)
    for field in body.model_fields_set:
        value = getattr(body, field)
        if field == "account":
            value = value or None       # an empty account is none
        if value is not None or field in CLEARABLE:
            setattr(c, field, value)
    _commit(db, c.name)
    return carrier_out(c)


@router.post("/carriers/{id}/deactivate", response_model=CarrierOut)
def deactivate_carrier(id: int, db: DB, who: Principal = require("fleet:write")):
    """No new bookings. What is already on its bay stays, to be collected."""
    c = get_carrier(db, id)
    c.active = False
    db.commit()
    return carrier_out(c)


@router.post("/carriers/{id}/reactivate", response_model=CarrierOut)
def reactivate_carrier(id: int, db: DB, who: Principal = require("fleet:write")):
    c = get_carrier(db, id)
    c.active = True
    db.commit()
    return carrier_out(c)
```

Change `api/tms/api/app.py`:
```diff
--- a/api/tms/api/app.py
+++ b/api/tms/api/app.py
@@ -6,8 +6,8 @@ from sqlalchemy import text
 from tms.api import errors
 from tms.api.deps import DB
 from tms.api.routes import (
-    auth, consignments, deliveries, drivers, feed, fleet, location, pod_files, printing, runs,
-    vehicles, wms_events,
+    auth, carriers, consignments, deliveries, drivers, feed, fleet, location, pod_files, printing,
+    runs, vehicles, wms_events,
 )
 
 logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
@@ -34,6 +34,7 @@ v1.include_router(pod_files.router)
 v1.include_router(fleet.router)
 v1.include_router(location.router)
 v1.include_router(feed.router)
+v1.include_router(carriers.router)
 
 
 @v1.get("/health", tags=["system"])
```

**Step 4: Run them**

Run: `.venv/bin/pytest tests/test_carriers.py -q`
Expected: `8 passed`. The whole suite: `366 passed`.

**Step 5: Commit**

```bash
git add api/tms/services/carrier_adapters.py api/tms/api/routes/carriers.py api/tms/api/app.py \
  api/tests/carriers_samples.py api/tests/test_carriers.py
git commit -m "Add carriers, changed by a fleet manager, booked by hand until an adapter says otherwise"
```

### Task 3: Booking a consignment with a carrier, and its con note

**Files:**
- Create: `api/tms/services/carriers.py`, `api/tms/api/routes/manifests.py`
- Modify: `api/tms/services/runs.py` (`take_off_run`, and `assign` takes one off the bay), `api/tms/api/app.py`
- Test: `api/tests/test_bookings.py` (new)

**Step 1: Write the failing tests**

Create `api/tests/test_bookings.py`:
```python
"""Sending a consignment with a carrier: booked on the carrier's manifest for
the day, its con note typed or scanned and put right, moved between carriers
and runs. On one run or with one carrier, never both, however many people
are at it."""
from datetime import timedelta

from carriers_samples import RIDGELINE, VALLEY, book, con_note, lines, new_carrier
from conftest import all_at_once, fresh_client
from runs_samples import DAY, assign, consignment, driver, new_run, order, vehicle


def _history(client, headers, cid) -> list[tuple[str, dict]]:
    h = client.get(f"/v1/consignments/{cid}", headers=headers).json()["history"]
    return [(e["kind"], e["detail"]) for e in h]


def _status(client, headers, cid) -> str:
    return client.get(f"/v1/consignments/{cid}", headers=headers).json()["status"]


def _bay(client, headers, day=DAY, **params) -> list[dict]:
    r = client.get("/v1/manifests", headers=headers, params={"date": day.isoformat(), **params})
    assert r.status_code == 200, r.text
    return r.json()["items"]


def test_from_the_tray_onto_the_carriers_manifest_for_the_day(
        client, db, manager_headers, dispatcher_headers):
    rx = new_carrier(client, manager_headers, account="RX-4410", pickup_time="15:30")
    c = consignment(db, customer="Mill Lane Florist", suburb="Daylesford", kg="24")
    r = book(client, dispatcher_headers, rx["id"], c.id)
    assert r.status_code == 200, r.text
    m = r.json()
    assert (m["manifest_no"], m["date"], m["status"], m["carrier"]["name"]) == (
        f"M-{m['id']:04d}", DAY.isoformat(), "open", RIDGELINE)
    assert m["carrier"]["pickup_time"] == "15:30"
    line = m["lines"][0]
    assert line["consignment"] == {"id": c.id, "wms_ref": c.wms_ref, "customer": "Mill Lane Florist",
                                   "suburb": "Daylesford", "status": "booked", "packages": 1,
                                   "cartons": 1, "weight_kg": "24", "volume_m3": "0.5"}
    assert line["con_note"] is None
    assert m["totals"] == {"consignments": 1, "packages": 1, "cartons": 1, "weight_kg": "24"}
    assert m["needs_con_note"] == 1 and m["print_job"] is None
    assert m["shipping"] == {"pending": 0, "done": 0, "failed": 0}
    assert _history(client, dispatcher_headers, c.id)[-1] == ("booked", {
        "carrier": RIDGELINE, "carrier_id": rx["id"], "manifest": m["manifest_no"],
        "manifest_id": m["id"], "by": "dee"})
    assert [x["id"] for x in _bay(client, dispatcher_headers)] == [m["id"]]


def test_a_con_note_with_the_booking_is_tidied_and_kept_in_the_history(
        client, db, manager_headers, dispatcher_headers):
    rx = new_carrier(client, manager_headers)
    c = consignment(db)
    m = book(client, dispatcher_headers, rx["id"], c.id, con_note="  rx-77310042 ").json()
    assert lines(m) == [(c.id, "RX-77310042")] and m["needs_con_note"] == 0
    assert _history(client, dispatcher_headers, c.id)[-1] == ("con_note", {
        "carrier": RIDGELINE, "carrier_id": rx["id"], "con_note": "RX-77310042", "was": None,
        "by": "dee"})


def test_a_con_note_is_letters_digits_and_dashes(client, db, manager_headers, dispatcher_headers):
    rx = new_carrier(client, manager_headers)
    c = consignment(db)
    m = book(client, dispatcher_headers, rx["id"], c.id).json()
    for bad in ("RX 7731", "RX", "-RX7731", "RX_7731", "R" * 41):
        r = con_note(client, dispatcher_headers, m["id"], c.id, bad)
        assert r.status_code == 422, bad
        assert r.json()["errors"] == [{"field": "con_note", "message":
                                       "A con note is 4 to 40 letters, digits and dashes, as on "
                                       "the carrier's label"}]


def test_putting_a_con_note_right_is_a_new_row_and_the_same_again_is_nothing(
        client, db, manager_headers, dispatcher_headers):
    rx = new_carrier(client, manager_headers)
    c = consignment(db)
    m = book(client, dispatcher_headers, rx["id"], c.id, con_note="RX-77310024").json()
    r = con_note(client, dispatcher_headers, m["id"], c.id, "RX-77310042")
    assert r.status_code == 200, r.text
    assert lines(r.json()) == [(c.id, "RX-77310042")]
    con_note(client, dispatcher_headers, m["id"], c.id, "rx-77310042")
    notes = [d for k, d in _history(client, dispatcher_headers, c.id) if k == "con_note"]
    assert [(d["con_note"], d["was"]) for d in notes] == [("RX-77310024", None),
                                                         ("RX-77310042", "RX-77310024")]


def test_a_con_note_is_one_consignments_with_a_carrier(
        client, db, manager_headers, dispatcher_headers):
    rx, vf = new_carrier(client, manager_headers), new_carrier(client, manager_headers, VALLEY)
    a = consignment(db, ref="0080034519", customer="Hilltop Joinery")
    b = consignment(db, ref="0080034520")
    c = consignment(db, ref="0080034521")
    book(client, dispatcher_headers, rx["id"], a.id, con_note="RX-77310042")
    r = book(client, dispatcher_headers, rx["id"], b.id, con_note="RX-77310042")
    assert r.status_code == 422
    assert r.json()["errors"] == [{"field": "con_note", "message":
                                   f"RX-77310042 is already the con note of {a.wms_ref} "
                                   f"(Hilltop Joinery) with {RIDGELINE}"}]
    # refused whole: not booked either
    assert _status(client, dispatcher_headers, b.id) == "packed"
    # another carrier's numbers are its own
    assert book(client, dispatcher_headers, vf["id"], c.id, con_note="RX-77310042").status_code == 200


def test_the_same_booking_twice_changes_nothing(client, db, manager_headers, dispatcher_headers):
    rx = new_carrier(client, manager_headers)
    c = consignment(db)
    book(client, dispatcher_headers, rx["id"], c.id, con_note="RX-77310042")
    again = book(client, dispatcher_headers, rx["id"], c.id)
    assert lines(again.json()) == [(c.id, "RX-77310042")]
    assert [k for k, _ in _history(client, dispatcher_headers, c.id)] == ["packed", "booked",
                                                                          "con_note"]


def test_to_another_carrier_without_its_con_note(client, db, manager_headers, dispatcher_headers):
    rx, vf = new_carrier(client, manager_headers), new_carrier(client, manager_headers, VALLEY)
    c = consignment(db)
    first = book(client, dispatcher_headers, rx["id"], c.id, con_note="RX-77310042").json()
    m = book(client, dispatcher_headers, vf["id"], c.id).json()
    assert m["carrier"]["name"] == VALLEY and lines(m) == [(c.id, None)]
    assert [x["carrier"]["name"] for x in _bay(client, dispatcher_headers)] == [VALLEY]
    assert _history(client, dispatcher_headers, c.id)[-2:] == [
        ("unbooked", {"carrier": RIDGELINE, "manifest": first["manifest_no"],
                      "manifest_id": first["id"], "by": "dee"}),
        ("booked", {"carrier": VALLEY, "carrier_id": vf["id"], "manifest": m["manifest_no"],
                    "manifest_id": m["id"], "by": "dee"})]
    # its old number is free again
    d = consignment(db, ref="0080034599")
    assert book(client, dispatcher_headers, rx["id"], d.id, con_note="RX-77310042").status_code == 200


def test_to_another_day_with_the_same_carrier_keeps_its_con_note(
        client, db, manager_headers, dispatcher_headers):
    rx = new_carrier(client, manager_headers)
    c = consignment(db)
    book(client, dispatcher_headers, rx["id"], c.id, con_note="RX-77310042")
    m = book(client, dispatcher_headers, rx["id"], c.id, day=DAY + timedelta(days=1)).json()
    assert m["date"] == (DAY + timedelta(days=1)).isoformat()
    assert lines(m) == [(c.id, "RX-77310042")]


def test_off_a_planned_run_onto_a_carrier_and_back_onto_a_run(
        client, db, manager_headers, dispatcher_headers):
    vehicle(db)
    driver(db)
    rx = new_carrier(client, manager_headers)
    run = new_run(client, dispatcher_headers)
    a, b = consignment(db, ref="0080034519"), consignment(db, ref="0080034520")
    assign(client, dispatcher_headers, run["id"], a.id)
    assign(client, dispatcher_headers, run["id"], b.id)
    assert book(client, dispatcher_headers, rx["id"], a.id).status_code == 200
    now = client.get(f"/v1/runs/{run['id']}", headers=dispatcher_headers).json()
    assert order(now) == [b.id] and now["stops"][0]["sequence"] == 1
    assert [k for k, _ in _history(client, dispatcher_headers, a.id)][-2:] == ["unassigned", "booked"]

    r = assign(client, dispatcher_headers, run["id"], a.id)
    assert r.status_code == 200, r.text
    assert order(r.json()) == [b.id, a.id]
    assert _status(client, dispatcher_headers, a.id) == "assigned"
    assert [k for k, _ in _history(client, dispatcher_headers, a.id)][-2:] == ["unbooked", "assigned"]
    assert _bay(client, dispatcher_headers) == []


def test_what_has_left_cant_go_with_a_carrier(client, db, manager_headers, dispatcher_headers):
    vehicle(db)
    driver(db)
    rx = new_carrier(client, manager_headers)
    run = new_run(client, dispatcher_headers)
    c = consignment(db)
    assign(client, dispatcher_headers, run["id"], c.id)
    client.post(f"/v1/runs/{run['id']}/depart", headers=dispatcher_headers)
    r = book(client, dispatcher_headers, rx["id"], c.id)
    assert r.status_code == 409 and r.json()["code"] == "not_plannable"
    assert r.json()["detail"] == "Station Deli (0080034519) has departed, so it can't go with a carrier"


def test_nothing_new_with_a_deactivated_carrier(client, db, manager_headers, dispatcher_headers):
    rx = new_carrier(client, manager_headers)
    client.post(f"/v1/carriers/{rx['id']}/deactivate", headers=manager_headers)
    r = book(client, dispatcher_headers, rx["id"], consignment(db).id)
    assert r.status_code == 409 and r.json()["code"] == "carrier_inactive"
    assert r.json()["detail"] == f"{RIDGELINE} is deactivated, so nothing more can be booked with it"
    assert book(client, dispatcher_headers, 99, 1).status_code == 404


def test_back_to_the_tray(client, db, manager_headers, dispatcher_headers):
    rx = new_carrier(client, manager_headers)
    c = consignment(db)
    m = book(client, dispatcher_headers, rx["id"], c.id, con_note="RX-77310042").json()
    r = client.post(f"/v1/manifests/{m['id']}/unbook", headers=dispatcher_headers,
                    json={"consignment_id": c.id})
    assert r.status_code == 200, r.text
    assert r.json()["lines"] == []
    assert _status(client, dispatcher_headers, c.id) == "packed"
    assert _bay(client, dispatcher_headers) == []
    again = client.post(f"/v1/manifests/{m['id']}/unbook", headers=dispatcher_headers,
                        json={"consignment_id": c.id})
    assert again.status_code == 200
    assert [k for k, _ in _history(client, dispatcher_headers, c.id)] == [
        "packed", "booked", "con_note", "unbooked"]
    r = con_note(client, dispatcher_headers, m["id"], c.id, "RX-77310043")
    assert r.status_code == 404
    assert r.json()["detail"] == f"{m['manifest_no']} has no consignment {c.id}"


def test_the_bay_today_and_what_is_still_waiting_from_before(
        client, db, manager_headers, dispatcher_headers):
    rx, vf = new_carrier(client, manager_headers), new_carrier(client, manager_headers, VALLEY)
    yesterday = DAY - timedelta(days=1)
    old = book(client, dispatcher_headers, rx["id"], consignment(db, ref="0080034501").id,
               day=yesterday).json()
    today = book(client, dispatcher_headers, vf["id"], consignment(db, ref="0080034502").id).json()
    assert [m["id"] for m in _bay(client, dispatcher_headers)] == [today["id"]]
    assert [m["id"] for m in _bay(client, dispatcher_headers, left_open=True)] == [old["id"], today["id"]]
    got = client.get(f"/v1/manifests/{old['id']}", headers=dispatcher_headers)
    assert got.status_code == 200 and got.json()["date"] == yesterday.isoformat()


def test_on_a_run_or_with_a_carrier_never_both_however_many_at_once(
        client, db, manager_headers, dispatcher_headers):
    vehicle(db)
    driver(db)
    rx = new_carrier(client, manager_headers)
    run = new_run(client, dispatcher_headers)
    c = consignment(db)

    def go(i):
        with fresh_client() as other:
            if i % 2:
                return book(other, dispatcher_headers, rx["id"], c.id).status_code
            return assign(other, dispatcher_headers, run["id"], c.id).status_code

    counter = iter(range(6))
    assert all_at_once(6, lambda: go(next(counter))) == [200] * 6
    on_run = len(client.get(f"/v1/runs/{run['id']}", headers=dispatcher_headers).json()["stops"])
    on_bay = sum(len(m["lines"]) for m in _bay(client, dispatcher_headers))
    assert on_run + on_bay == 1


def test_a_driver_cant_book(client, db, manager_headers):
    from deliveries_samples import driver_headers
    driver(db)
    rx = new_carrier(client, manager_headers)
    assert book(client, driver_headers(client), rx["id"], consignment(db).id).status_code == 403
```

**Step 2: Run them to see them fail**

Run: `.venv/bin/pytest tests/test_bookings.py -q`
Expected: `15 failed`: `POST /v1/carriers/{id}/book` and `/v1/manifests…` answer `404` (`At index 0 diff: 404 != 200`, or `KeyError: 'id'` reading a `404`'s body).

**Step 3: Implement**

Change `api/tms/services/runs.py`. `take_off_run` is `assign`'s own "off another planned run" step, made public so booking with a carrier uses the same one; `assign` now also takes a consignment off the carrier bay (the import is inside the function: `carriers` imports this module for the lock):
```diff
--- a/api/tms/services/runs.py
+++ b/api/tms/services/runs.py
@@ -134,8 +134,9 @@ def cancel(db: Session, run_id: int) -> Run:
 # ---- stops -------------------------------------------------------------------
 
 # a consignment that may still go on a run, or move to another; one the driver
-# couldn't deliver comes back to the depot and is planned again
-PLANNABLE = ("packed", "assigned", "failed")
+# couldn't deliver comes back to the depot and is planned again, and one on
+# the carrier bay can come off it onto a run
+PLANNABLE = ("packed", "assigned", "failed", "booked")
 
 
 def _now() -> datetime:
@@ -169,11 +170,35 @@ def _plannable(db: Session, consignment_id: int) -> Consignment:
     return c
 
 
+def _current_stop(db: Session, consignment_id: int) -> Stop | None:
+    """Its stop on a run now; a stop on a trip already made (it has an
+    outcome) is a record."""
+    return db.execute(select(Stop).where(
+        Stop.consignment_id == consignment_id, Stop.removed_at.is_(None),
+        Stop.outcome.is_(None))).scalar_one_or_none()
+
+
+def take_off_run(db: Session, c: Consignment, by: str) -> None:
+    """Off the planned run it is on, if any, with a history row; the stops
+    left are numbered again. The caller holds the planning lock and says
+    where it goes next."""
+    current = _current_stop(db, c.id)
+    if current is None:
+        return
+    old = _planned(db, current.run_id)          # a departed run keeps its stops
+    current.removed_at = _now()
+    _event(c, "unassigned", run=old.number, run_id=old.id, by=by)
+    db.flush()
+    _renumber(_live_stops(db, old.id))
+
+
 def assign(db: Session, run_id: int, consignment_id: int, position: int | None,
            by: str) -> Run:
     """Put a consignment on this run at a place in the order (1 is first; none
-    is last), moving it off another planned run if it was on one. Asking for
-    what is already so changes nothing."""
+    is last), moving it off another planned run, or off the carrier bay, if
+    it was on one. Asking for what is already so changes nothing."""
+    from tms.services import carriers     # carriers plans with this module's lock
+
     lock_planning(db)
     run = _planned(db, run_id)
     if not run.vehicle.active:
@@ -181,17 +206,11 @@ def assign(db: Session, run_id: int, consignment_id: int, position: int | None,
                        f"{run.vehicle.rego} is deactivated, so {run.number} can't take stops")
     fleet.refuse_if_blocked(db, run.vehicle, run.day)
     c = _plannable(db, consignment_id)
-    # its stop on a run now; a stop on a trip already made (it has an outcome) is a record
-    current = db.execute(select(Stop).where(
-        Stop.consignment_id == c.id, Stop.removed_at.is_(None),
-        Stop.outcome.is_(None))).scalar_one_or_none()
+    carriers.take_off_bay(db, c, by)
+    current = _current_stop(db, c.id)
 
     if current is not None and current.run_id != run.id:
-        old = _planned(db, current.run_id)          # a departed run keeps its stops
-        current.removed_at = _now()
-        _event(c, "unassigned", run=old.number, run_id=old.id, by=by)
-        db.flush()
-        _renumber(_live_stops(db, old.id))
+        take_off_run(db, c, by)
         current = None
 
     stops = [s for s in _live_stops(db, run.id) if s.consignment_id != c.id]
```

Create `api/tms/services/carriers.py`:
```python
"""Freight going with outside carriers. A consignment is booked on a
carrier's manifest for a day (the carrier bay), its con note typed or
scanned and put right until the carrier collects it.

Every change takes the planning lock first, as planning runs does, so a
consignment is on one run or with one carrier, never both, however many
people are at it; the partial unique indexes on booking stand behind it.
Version one books by hand: see carrier_adapters for what comes later."""
from __future__ import annotations

import re
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from tms.api.errors import Conflict, FieldError, NotFound
from tms.models import Booking, Carrier, Consignment, ConsignmentEvent, Manifest
from tms.services import runs
from tms.services.runs import lock_planning

# as the carrier prints it: letters, digits and dashes, 4 to 40 of them
CON_NOTE = re.compile(r"^[A-Z0-9][A-Z0-9-]{3,39}$")
# what may go on the carrier bay: from the tray, off a planned run, or off
# another carrier's bay
BOOKABLE = ("packed", "failed", "assigned", "booked")


def _now() -> datetime:
    return datetime.now(UTC)


def normal_con_note(raw: str) -> str:
    """Typed or scanned: spaces around it go, letters become capitals. Any
    other space, or anything but letters, digits and dashes, is refused
    rather than guessed at."""
    note = raw.strip().upper()
    if not CON_NOTE.match(note):
        raise FieldError("con_note", "A con note is 4 to 40 letters, digits and dashes, as on "
                                     "the carrier's label")
    return note


def _event(c: Consignment, kind: str, **detail) -> None:
    c.events.append(ConsignmentEvent(kind=kind, at=_now(), detail=detail))


def get_manifest(db: Session, manifest_id: int) -> Manifest:
    m = db.execute(
        select(Manifest).where(Manifest.id == manifest_id)
        .options(selectinload(Manifest.lines).selectinload(Booking.consignment)
                 .selectinload(Consignment.packages), selectinload(Manifest.carrier))
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if m is None:
        raise NotFound(f"no manifest {manifest_id}")
    return m


def _open(db: Session, manifest_id: int) -> Manifest:
    """The manifest, read after the lock, and not collected yet."""
    m = get_manifest(db, manifest_id)
    if m.collected_at is not None:
        raise Conflict("manifest_collected",
                       f"{m.carrier.name} has collected {m.number}; what is on it can't change")
    return m


def _live_booking(db: Session, consignment_id: int) -> Booking | None:
    return db.execute(select(Booking).where(
        Booking.consignment_id == consignment_id, Booking.removed_at.is_(None))
        .execution_options(populate_existing=True)).scalar_one_or_none()


def take_off_bay(db: Session, c: Consignment, by: str) -> None:
    """Off the carrier bay, if it is on it, with a history row. The caller
    holds the planning lock and says where it goes next."""
    booking = _live_booking(db, c.id)
    if booking is None:
        return
    m = db.get(Manifest, booking.manifest_id)
    booking.removed_at = _now()
    _event(c, "unbooked", carrier=m.carrier.name, manifest=m.number, manifest_id=m.id, by=by)
    db.flush()


def _con_note_free(db: Session, carrier: Carrier, note: str, booking: Booking) -> None:
    taken = db.execute(select(Booking).where(
        Booking.carrier_id == carrier.id, Booking.con_note == note,
        Booking.removed_at.is_(None), Booking.id != booking.id)).scalar_one_or_none()
    if taken is not None:
        other = taken.consignment
        raise FieldError("con_note", f"{note} is already the con note of {other.wms_ref} "
                                     f"({other.customer}) with {carrier.name}")


def _set_con_note(db: Session, carrier: Carrier, booking: Booking, note: str, by: str) -> None:
    """The same again is nothing; a new one is a history row naming the old."""
    if booking.con_note == note:
        return
    _con_note_free(db, carrier, note, booking)
    was, booking.con_note = booking.con_note, note
    _event(booking.consignment, "con_note", carrier=carrier.name, carrier_id=carrier.id,
           con_note=note, was=was, by=by)
    db.flush()


def _manifest_for(db: Session, carrier: Carrier, day: date, by: str) -> Manifest:
    """The carrier's open manifest for the day, made if there is none yet."""
    m = db.execute(select(Manifest).where(
        Manifest.carrier_id == carrier.id, Manifest.day == day,
        Manifest.collected_at.is_(None))).scalar_one_or_none()
    if m is None:
        m = Manifest(carrier_id=carrier.id, day=day, created_by=by)
        db.add(m)
        db.flush()
    return m


def book(db: Session, carrier_id: int, consignment_id: int, day: date, con_note: str | None,
         by: str) -> Manifest:
    """Put a consignment on the carrier's manifest for the day, from the
    tray, off a planned run or off another carrier's bay, and set its con
    note if one is given. Moved to another day with the same carrier it
    keeps its con note; to another carrier it doesn't (a con note is one
    carrier's). Asking for what is already so changes nothing. A refusal
    refuses the whole: nothing is moved."""
    lock_planning(db)
    carrier = db.get(Carrier, carrier_id)
    if carrier is None:
        raise NotFound(f"no carrier {carrier_id}")
    note = normal_con_note(con_note) if con_note is not None else None
    c = db.execute(select(Consignment).where(Consignment.id == consignment_id)
                   .execution_options(populate_existing=True)).scalar_one_or_none()
    if c is None:
        raise FieldError("consignment_id", f"no consignment {consignment_id}")
    if c.status not in BOOKABLE:
        raise Conflict("not_plannable",
                       f"{c.customer} ({c.wms_ref}) has {c.status}, so it can't go with a carrier")
    booking = _live_booking(db, c.id)
    if booking is not None and booking.carrier_id == carrier.id \
            and db.get(Manifest, booking.manifest_id).day == day:
        m = db.get(Manifest, booking.manifest_id)
    else:
        if not carrier.active:
            raise Conflict("carrier_inactive",
                           f"{carrier.name} is deactivated, so nothing more can be booked with it")
        keep = booking.con_note if booking is not None and booking.carrier_id == carrier.id else None
        take_off_bay(db, c, by)
        runs.take_off_run(db, c, by)
        m = _manifest_for(db, carrier, day, by)
        booking = Booking(manifest_id=m.id, carrier_id=carrier.id, consignment_id=c.id,
                          con_note=keep, added_by=by)
        db.add(booking)
        c.status = "booked"
        _event(c, "booked", carrier=carrier.name, carrier_id=carrier.id, manifest=m.number,
               manifest_id=m.id, by=by)
        db.flush()
    if note is not None:
        _set_con_note(db, carrier, booking, note, by)
    return m


def unbook(db: Session, manifest_id: int, consignment_id: int, by: str) -> Manifest:
    """Back to the tray. Not on this manifest already: nothing to do."""
    lock_planning(db)
    m = _open(db, manifest_id)
    booking = next((b for b in m.lines if b.consignment_id == consignment_id), None)
    if booking is None:
        return m
    c = booking.consignment
    take_off_bay(db, c, by)
    c.status = "packed"
    db.flush()
    return m


def set_con_note(db: Session, manifest_id: int, consignment_id: int, con_note: str,
                 by: str) -> Manifest:
    """Typed or scanned, or put right, until the carrier collects it."""
    lock_planning(db)
    m = _open(db, manifest_id)
    note = normal_con_note(con_note)
    booking = next((b for b in m.lines if b.consignment_id == consignment_id), None)
    if booking is None:
        raise NotFound(f"{m.number} has no consignment {consignment_id}")
    _set_con_note(db, m.carrier, booking, note, by)
    return m
```

Create `api/tms/api/routes/manifests.py`:
```python
"""The carrier bay: consignments booked with outside carriers, on each
carrier's manifest for the day, with their con notes."""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from tms.api.deps import DB, Principal, require
from tms.api.routes.carriers import CarrierOut, carrier_out
from tms.api.routes.runs import RunSheetOut, ShippingOut, StopConsignment, stop_consignment
from tms.api.schemas import Dec, Page
from tms.models import Booking, Manifest, OutboundCall, PrintJob
from tms.services import carriers
from tms.services.load import consignment_totals, total

router = APIRouter(tags=["carriers"])


class LineOut(BaseModel):
    consignment: StopConsignment
    con_note: str | None
    added_at: dt.datetime


class TotalsOut(BaseModel):
    consignments: int
    packages: int
    cartons: int
    weight_kg: Dec


class ManifestOut(BaseModel):
    id: int
    manifest_no: str
    date: dt.date
    # open (on the carrier bay), collected (the carrier has it)
    status: str
    carrier: CarrierOut
    created_by: str
    created_at: dt.datetime
    collected_at: dt.datetime | None
    collected_by: str | None
    lines: list[LineOut]
    totals: TotalsOut
    # how many on it have no con note yet
    needs_con_note: int
    # the latest print of it, or null
    print_job: RunSheetOut | None
    # the ship calls to Simple WMS once it is collected
    shipping: ShippingOut


def manifest_out(db, m: Manifest) -> ManifestOut:
    t = total(consignment_totals(b.consignment.packages) for b in m.lines)
    job = db.execute(select(PrintJob).where(PrintJob.manifest_id == m.id)
                     .order_by(PrintJob.id.desc()).limit(1)).scalar_one_or_none()
    calls = dict(db.execute(select(OutboundCall.status, func.count())
                            .where(OutboundCall.manifest_id == m.id)
                            .group_by(OutboundCall.status)).all())
    return ManifestOut(
        id=m.id, manifest_no=m.number, date=m.day,
        status="open" if m.collected_at is None else "collected",
        carrier=carrier_out(m.carrier), created_by=m.created_by, created_at=m.created_at,
        collected_at=m.collected_at, collected_by=m.collected_by,
        lines=[LineOut(consignment=stop_consignment(b.consignment), con_note=b.con_note,
                       added_at=b.added_at) for b in m.lines],
        totals=TotalsOut(consignments=len(m.lines), packages=t.packages, cartons=t.cartons,
                         weight_kg=t.weight_kg),
        needs_con_note=sum(b.con_note is None for b in m.lines),
        print_job=None if job is None else RunSheetOut(
            job_id=job.job_id, status=job.status, printer=job.printer,
            last_error=job.last_error, created_at=job.created_at, printed_at=job.printed_at),
        shipping=ShippingOut(**calls))


def _fresh(db, manifest_id: int) -> ManifestOut:
    db.commit()
    return manifest_out(db, carriers.get_manifest(db, manifest_id))


class BookIn(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    consignment_id: int = Field(ge=1)
    # the day the carrier is to collect it, by the dispatcher's calendar
    day: dt.date = Field(alias="date")
    con_note: str | None = Field(default=None, max_length=64)


class ConsignmentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    consignment_id: int = Field(ge=1)


class ConNoteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    consignment_id: int = Field(ge=1)
    con_note: str = Field(max_length=64)


@router.post("/carriers/{id}/book", response_model=ManifestOut)
def book(id: int, body: BookIn, db: DB, who: Principal = require("runs:write")):
    """Onto the carrier's manifest for the day; answers with that manifest."""
    m = carriers.book(db, id, body.consignment_id, body.day, body.con_note, by=who.name)
    return _fresh(db, m.id)


@router.get("/manifests", response_model=Page[ManifestOut])
def list_manifests(db: DB, day: dt.date = Query(alias="date"), left_open: bool = False,
                   who: Principal = require("runs:read")):
    """One day's manifests with something on them, open or collected, oldest
    first. With left_open, first the ones from before that day still waiting
    for their carrier."""
    live = select(Booking.manifest_id).where(Booking.removed_at.is_(None))
    query = select(Manifest.id).where(Manifest.day == day, Manifest.id.in_(live))
    if left_open:
        query = query.union(select(Manifest.id).where(
            Manifest.day < day, Manifest.collected_at.is_(None), Manifest.id.in_(live)))
    ids = db.execute(select(Manifest.id).where(Manifest.id.in_(query))
                     .order_by(Manifest.day, Manifest.id)).scalars().all()
    return Page(items=[manifest_out(db, carriers.get_manifest(db, i)) for i in ids],
                total=len(ids))


@router.get("/manifests/{id}", response_model=ManifestOut)
def read_manifest(id: int, db: DB, who: Principal = require("runs:read")):
    return manifest_out(db, carriers.get_manifest(db, id))


@router.post("/manifests/{id}/con-note", response_model=ManifestOut)
def con_note(id: int, body: ConNoteIn, db: DB, who: Principal = require("runs:write")):
    carriers.set_con_note(db, id, body.consignment_id, body.con_note, by=who.name)
    return _fresh(db, id)


@router.post("/manifests/{id}/unbook", response_model=ManifestOut)
def unbook(id: int, body: ConsignmentIn, db: DB, who: Principal = require("runs:write")):
    carriers.unbook(db, id, body.consignment_id, by=who.name)
    return _fresh(db, id)
```

Change `api/tms/api/app.py`:
```diff
--- a/api/tms/api/app.py
+++ b/api/tms/api/app.py
@@ -6,8 +6,8 @@ from sqlalchemy import text
 from tms.api import errors
 from tms.api.deps import DB
 from tms.api.routes import (
-    auth, carriers, consignments, deliveries, drivers, feed, fleet, location, pod_files, printing,
-    runs, vehicles, wms_events,
+    auth, carriers, consignments, deliveries, drivers, feed, fleet, location, manifests, pod_files,
+    printing, runs, vehicles, wms_events,
 )
 
 logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
@@ -35,6 +35,7 @@ v1.include_router(fleet.router)
 v1.include_router(location.router)
 v1.include_router(feed.router)
 v1.include_router(carriers.router)
+v1.include_router(manifests.router)
 
 
 @v1.get("/health", tags=["system"])
```

**Step 4: Run them, and the planning tests they share a lock with**

Run: `.venv/bin/pytest tests/test_bookings.py tests/test_planning.py -q`
Expected: all pass (`15` new). `test_on_a_run_or_with_a_carrier_never_both_however_many_at_once` sends six at once, half to a run and half to a carrier: the consignment ends in exactly one place.

Run: `caffeinate -i .venv/bin/pytest -q`
Expected: `381 passed`.

**Step 5: Commit**

```bash
git add api/tms/services/carriers.py api/tms/api/routes/manifests.py api/tms/services/runs.py \
  api/tms/api/app.py api/tests/test_bookings.py
git commit -m "Send a consignment with a carrier, and type, scan or put right its con note"
```

### Task 4: The carrier collects: a ship call each, with the carrier and its con note

**Files:**
- Modify: `api/tms/services/carriers.py` (`collect`), `api/tms/api/routes/manifests.py` (`collect`, `retry-ship`)
- Test: `api/tests/test_collect.py` (new)

**Step 1: Write the failing tests**

Create `api/tests/test_collect.py`:
```python
"""The carrier calls and takes its manifest: each consignment is collected,
and a ship call to Simple WMS waits in the queue for each, with the carrier's
name and the con note. Once per message_id, and only what the dispatcher saw."""
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from carriers_samples import RIDGELINE, book, con_note, new_carrier
from fake_http import FakeHttp
from runs_samples import DAY, consignment
from tms import worker
from tms.models import OutboundCall

T0 = datetime(2026, 10, 14, 5, 30, tzinfo=UTC)


@pytest.fixture
def bay(client, db, manager_headers, dispatcher_headers):
    """Two consignments on Ridgeline's manifest, each with its con note."""
    rx = new_carrier(client, manager_headers)
    a = consignment(db, ref="0080034519", customer="Hilltop Joinery", kg="72")
    b = consignment(db, ref="0080034520", customer="Old Mill Books", kg="54")
    book(client, dispatcher_headers, rx["id"], a.id, con_note="RX-77310042")
    m = book(client, dispatcher_headers, rx["id"], b.id, con_note="RX-77310043").json()
    return m, a, b


def collect(client, headers, m, ids, message_id=None):
    return client.post(f"/v1/manifests/{m['id']}/collect", headers=headers,
                       json={"message_id": str(message_id or uuid.uuid4()), "consignment_ids": ids})


def _calls(db) -> list[OutboundCall]:
    db.expire_all()
    return list(db.execute(select(OutboundCall).order_by(OutboundCall.id)).scalars())


def test_collected_queues_a_ship_call_each_with_the_carrier_and_con_note(
        client, db, bay, dispatcher_headers):
    m, a, b = bay
    r = collect(client, dispatcher_headers, m, [a.id, b.id])
    assert r.status_code == 200, r.text
    out = r.json()
    assert (out["status"], out["collected_by"]) == ("collected", "dee") and out["collected_at"]
    assert [line["consignment"]["status"] for line in out["lines"]] == ["collected", "collected"]
    assert out["shipping"] == {"pending": 2, "done": 0, "failed": 0}
    calls = _calls(db)
    assert [(c.path, c.manifest_id, c.run_id, c.consignment_id) for c in calls] == [
        ("/v1/deliveries/0080034519/ship", m["id"], None, a.id),
        ("/v1/deliveries/0080034520/ship", m["id"], None, b.id)]
    assert calls[0].body == {"message_id": str(calls[0].message_id), "owner": "DEFAULT",
                             "carrier": RIDGELINE, "tracking_no": "RX-77310042",
                             "shipped_by": "dee"}
    h = client.get(f"/v1/consignments/{a.id}", headers=dispatcher_headers).json()["history"]
    assert (h[-1]["kind"], h[-1]["detail"]) == ("collected", {
        "carrier": RIDGELINE, "con_note": "RX-77310042", "manifest": m["manifest_no"],
        "manifest_id": m["id"], "by": "dee"})


def test_the_worker_tells_simple_wms_as_for_a_run(client, db, bay, dispatcher_headers,
                                                  monkeypatch):
    from tms.config import get_settings
    monkeypatch.setattr(get_settings(), "wms_url", "http://wms.test/")
    m, a, b = bay
    collect(client, dispatcher_headers, m, [a.id, b.id])
    db.execute(OutboundCall.__table__.update().values(next_attempt_at=T0))
    db.commit()
    http = FakeHttp()
    assert worker.send_ship_calls(db, http.client, now=T0) == 2
    assert [(b["carrier"], b["tracking_no"]) for b in http.bodies()] == [
        (RIDGELINE, "RX-77310042"), (RIDGELINE, "RX-77310043")]
    got = client.get(f"/v1/manifests/{m['id']}", headers=dispatcher_headers).json()
    assert got["shipping"] == {"pending": 0, "done": 2, "failed": 0}
    http.client.close()


def test_the_same_message_again_gets_the_same_answer_and_ships_nothing_more(
        client, db, bay, dispatcher_headers):
    m, a, b = bay
    message_id = uuid.uuid4()
    first = collect(client, dispatcher_headers, m, [a.id, b.id], message_id)
    again = collect(client, dispatcher_headers, m, [a.id, b.id], message_id)
    assert again.status_code == 200 and again.json() == first.json()
    assert len(_calls(db)) == 2


def test_collected_twice_from_two_desks_is_collected_once(client, db, bay, dispatcher_headers):
    m, a, b = bay
    collect(client, dispatcher_headers, m, [a.id, b.id])
    other = collect(client, dispatcher_headers, m, [b.id, a.id])
    assert other.status_code == 200 and other.json()["status"] == "collected"
    assert len(_calls(db)) == 2
    stale = collect(client, dispatcher_headers, m, [a.id])
    assert stale.status_code == 409 and stale.json()["code"] == "manifest_collected"


def test_only_what_the_dispatcher_saw(client, db, bay, dispatcher_headers):
    m, a, b = bay
    r = collect(client, dispatcher_headers, m, [a.id])
    assert r.status_code == 409 and r.json()["code"] == "stale"
    assert r.json()["detail"] == (f"{m['manifest_no']} changed while you were looking; it has been "
                                  "reloaded, so check it and try again")
    assert _calls(db) == []


def test_not_without_every_con_note(client, db, manager_headers, dispatcher_headers):
    rx = new_carrier(client, manager_headers)
    a = consignment(db, ref="0080034519", customer="Mill Lane Florist")
    b = consignment(db, ref="0080034520", customer="Hilltop Joinery")
    book(client, dispatcher_headers, rx["id"], a.id)
    m = book(client, dispatcher_headers, rx["id"], b.id, con_note="RX-77310042").json()
    r = collect(client, dispatcher_headers, m, [a.id, b.id])
    assert r.status_code == 409 and r.json()["code"] == "con_note_missing"
    assert r.json()["detail"] == ("Add the con note of Mill Lane Florist (0080034519) first: "
                                  "each consignment needs one before the carrier takes it")
    # the same message can go again once the con note is in: a refusal isn't kept
    message_id = uuid.uuid4()
    collect(client, dispatcher_headers, m, [a.id, b.id], message_id)
    con_note(client, dispatcher_headers, m["id"], a.id, "RX-77310041")
    assert collect(client, dispatcher_headers, m, [a.id, b.id], message_id).status_code == 200


def test_nothing_to_collect(client, db, bay, dispatcher_headers):
    m, a, b = bay
    for c in (a, b):
        client.post(f"/v1/manifests/{m['id']}/unbook", headers=dispatcher_headers,
                    json={"consignment_id": c.id})
    r = collect(client, dispatcher_headers, m, [])
    assert r.status_code == 409 and r.json()["code"] == "manifest_empty"


def test_once_collected_it_stays_as_it_went(client, db, bay, manager_headers, dispatcher_headers):
    m, a, b = bay
    collect(client, dispatcher_headers, m, [a.id, b.id])
    for r in (con_note(client, dispatcher_headers, m["id"], a.id, "RX-77310049"),
              client.post(f"/v1/manifests/{m['id']}/unbook", headers=dispatcher_headers,
                          json={"consignment_id": a.id})):
        assert r.status_code == 409 and r.json()["code"] == "manifest_collected"
    rx = m["carrier"]["id"]
    r = book(client, dispatcher_headers, rx, a.id)
    assert r.status_code == 409 and r.json()["code"] == "not_plannable"
    # the carrier calls again later in the day: a new manifest
    later = book(client, dispatcher_headers, rx, consignment(db, ref="0080034599").id).json()
    assert later["id"] != m["id"] and later["date"] == DAY.isoformat()


def test_ship_calls_that_gave_up_are_tried_again_on_request(client, db, bay, dispatcher_headers):
    m, a, b = bay
    collect(client, dispatcher_headers, m, [a.id, b.id])
    db.execute(OutboundCall.__table__.update().values(status="failed", attempts=5,
                                                      last_error="HTTP 503: down"))
    db.commit()
    r = client.post(f"/v1/manifests/{m['id']}/retry-ship", headers=dispatcher_headers)
    assert r.status_code == 200 and r.json()["shipping"] == {"pending": 2, "done": 0, "failed": 0}
    assert {(c.attempts, c.last_error) for c in _calls(db)} == {(0, None)}
    assert all(c.next_attempt_at <= datetime.now(UTC) + timedelta(seconds=1) for c in _calls(db))
```

**Step 2: Run them to see them fail**

Run: `.venv/bin/pytest tests/test_collect.py -q`
Expected: `9 failed`: `POST /v1/manifests/{id}/collect` and `/retry-ship` are `404`.

**Step 3: Implement**

Change `api/tms/services/carriers.py`:
```diff
--- a/api/tms/services/carriers.py
+++ b/api/tms/services/carriers.py
@@ -9,13 +9,15 @@ Version one books by hand: see carrier_adapters for what comes later."""
 from __future__ import annotations
 
 import re
+import uuid
 from datetime import UTC, date, datetime
+from urllib.parse import quote
 
 from sqlalchemy import select
 from sqlalchemy.orm import Session, selectinload
 
 from tms.api.errors import Conflict, FieldError, NotFound
-from tms.models import Booking, Carrier, Consignment, ConsignmentEvent, Manifest
+from tms.models import Booking, Carrier, Consignment, ConsignmentEvent, Manifest, OutboundCall
 from tms.services import runs
 from tms.services.runs import lock_planning
 
@@ -186,3 +188,48 @@ def set_con_note(db: Session, manifest_id: int, consignment_id: int, con_note: s
         raise NotFound(f"{m.number} has no consignment {consignment_id}")
     _set_con_note(db, m.carrier, booking, note, by)
     return m
+
+
+def collect(db: Session, manifest_id: int, consignment_ids: list[int], by: str) -> Manifest:
+    """The carrier has called and taken what is on the manifest: each
+    consignment is collected, and a ship call to Simple WMS waits in the
+    queue for each, with the carrier's name and the con note, in the same
+    transaction, so none is lost and none is made twice. Only what the
+    dispatcher saw (`consignment_ids`), each with its con note. A manifest
+    already collected with just those on it is left as it is."""
+    lock_planning(db)
+    m = get_manifest(db, manifest_id)
+    on_it = {b.consignment_id for b in m.lines}
+    if m.collected_at is not None:
+        if set(consignment_ids) == on_it:
+            return m
+        raise Conflict("manifest_collected",
+                       f"{m.carrier.name} has collected {m.number}; what is on it can't change")
+    if not m.lines:
+        raise Conflict("manifest_empty", f"{m.number} has nothing on it")
+    if len(consignment_ids) != len(set(consignment_ids)) or set(consignment_ids) != on_it:
+        raise Conflict("stale", f"{m.number} changed while you were looking; it has been "
+                                "reloaded, so check it and try again")
+    missing = [b.consignment for b in m.lines if b.con_note is None]
+    if missing:
+        c = missing[0]
+        raise Conflict("con_note_missing",
+                       f"Add the con note of {c.customer} ({c.wms_ref}) first: each consignment "
+                       "needs one before the carrier takes it")
+    now = _now()
+    for b in m.lines:
+        c = b.consignment
+        c.status = "collected"
+        _event(c, "collected", carrier=m.carrier.name, con_note=b.con_note, manifest=m.number,
+               manifest_id=m.id, by=by)
+        message_id = uuid.uuid4()
+        db.add(OutboundCall(
+            message_id=message_id, kind="ship", manifest_id=m.id, consignment_id=c.id,
+            path=f"/v1/deliveries/{quote(c.wms_ref, safe='')}/ship",
+            body={"message_id": str(message_id), "owner": c.owner, "carrier": m.carrier.name,
+                  "tracking_no": b.con_note, "shipped_by": by[:64]},
+            status="pending", attempts=0, next_attempt_at=now))
+    m.collected_at = now
+    m.collected_by = by
+    db.flush()
+    return m
```

Change `api/tms/api/routes/manifests.py`:
```diff
--- a/api/tms/api/routes/manifests.py
+++ b/api/tms/api/routes/manifests.py
@@ -3,10 +3,11 @@ carrier's manifest for the day, with their con notes."""
 from __future__ import annotations
 
 import datetime as dt
+import uuid
 
 from fastapi import APIRouter, Query
 from pydantic import BaseModel, ConfigDict, Field
-from sqlalchemy import func, select
+from sqlalchemy import func, select, update
 
 from tms.api.deps import DB, Principal, require
 from tms.api.routes.carriers import CarrierOut, carrier_out
@@ -14,6 +15,7 @@ from tms.api.routes.runs import RunSheetOut, ShippingOut, StopConsignment, stop_
 from tms.api.schemas import Dec, Page
 from tms.models import Booking, Manifest, OutboundCall, PrintJob
 from tms.services import carriers
+from tms.services.envelope import handle
 from tms.services.load import consignment_totals, total
 
 router = APIRouter(tags=["carriers"])
@@ -139,3 +141,32 @@ def con_note(id: int, body: ConNoteIn, db: DB, who: Principal = require("runs:wr
 def unbook(id: int, body: ConsignmentIn, db: DB, who: Principal = require("runs:write")):
     carriers.unbook(db, id, body.consignment_id, by=who.name)
     return _fresh(db, id)
+
+
+class CollectIn(BaseModel):
+    model_config = ConfigDict(extra="forbid")
+    message_id: uuid.UUID
+    # what is on the manifest as the dispatcher saw it: anything else is stale
+    consignment_ids: list[int] = Field(max_length=500)
+
+
+@router.post("/manifests/{id}/collect", response_model=ManifestOut)
+def collect(id: int, body: CollectIn, db: DB, who: Principal = require("runs:write")):
+    """The carrier has taken it: each consignment is marked shipped in Simple
+    WMS with the carrier's name and its con note. Once per message_id."""
+    def work():
+        m = carriers.collect(db, id, body.consignment_ids, by=who.name)
+        return manifest_out(db, m)
+    return handle(db, who, body.message_id, f"/v1/manifests/{id}/collect", work)
+
+
+@router.post("/manifests/{id}/retry-ship", response_model=ManifestOut)
+def retry_ship(id: int, db: DB, who: Principal = require("runs:write")):
+    """Ship calls that gave up are tried again from the start, with the same
+    message_id each, so Simple WMS still ships each delivery once."""
+    carriers.get_manifest(db, id)
+    db.execute(update(OutboundCall)
+               .where(OutboundCall.manifest_id == id, OutboundCall.status == "failed")
+               .values(status="pending", attempts=0, last_error=None,
+                       next_attempt_at=func.now()))
+    return _fresh(db, id)
```

**Step 4: Run them**

Run: `.venv/bin/pytest tests/test_collect.py tests/test_ship_calls.py -q`
Expected: all pass (`9` new): the worker sends a manifest's ship calls as it sends a run's, with no change to it.

Run: `caffeinate -i .venv/bin/pytest -q`
Expected: `390 passed`.

**Step 5: Commit**

```bash
git add api/tms/services/carriers.py api/tms/api/routes/manifests.py api/tests/test_collect.py
git commit -m "Mark what a carrier collects shipped in Simple WMS, with the carrier and its con note"
```

### Task 5: Manifests through Platen

**Files:**
- Modify: `api/tms/services/printing.py` (`manifest_data`, `queue_manifest`), `api/tms/services/carriers.py` (`refuse_missing_con_notes`, shared with collecting), `api/tms/api/routes/printing.py` (the print route; a job says its run or its manifest)
- Test: `api/tests/test_manifest_printing.py` (new)

**Step 1: Write the failing tests**

Create `api/tests/test_manifest_printing.py` (it borrows `test_printing.py`'s `platen` fixture and Platen key):
```python
"""Carrier manifests through Platen, as run sheets go: a template name and
JSON, sent by the worker, and Platen says how it went. Not while a
consignment on it has no con note."""
import pytest
from sqlalchemy import select

from carriers_samples import RIDGELINE, book, new_carrier
from runs_samples import consignment
from test_printing import T0, _platen_key, platen  # noqa: F401  (the fixture)
from tms import worker
from tms.models import PrintJob


@pytest.fixture
def manifest(client, db, manager_headers, dispatcher_headers):
    rx = new_carrier(client, manager_headers, account="RX-4410", pickup_time="15:30")
    a = consignment(db, ref="0080034519", customer="Hilltop Joinery", suburb="Bacchus Marsh",
                    kg="72")
    b = consignment(db, ref="0080034520", customer="Old Mill Books", suburb="Castlemaine",
                    kg="54")
    book(client, dispatcher_headers, rx["id"], a.id, con_note="RX-77310042")
    return book(client, dispatcher_headers, rx["id"], b.id, con_note="RX-77310043").json()


def _print(client, headers, m):
    return client.post(f"/v1/manifests/{m['id']}/print", headers=headers)


def _job(db) -> PrintJob:
    db.expire_all()
    return db.execute(select(PrintJob)).scalar_one()


def test_printing_queues_the_manifest(client, db, platen, manifest, dispatcher_headers):
    r = _print(client, dispatcher_headers, manifest)
    assert r.status_code == 202, r.text
    out = r.json()
    assert (out["status"], out["printer"], out["run_id"], out["manifest_id"]) == (
        "pending", "Office", None, manifest["id"])
    job = _job(db)
    assert (job.template, job.version, job.copies) == ("carrier-manifest", "v1", 1)
    assert job.reference == {"type": "manifest", "ref": manifest["manifest_no"]}
    assert job.data == {
        "manifest_no": manifest["manifest_no"], "date": "2026-10-14", "carrier": RIDGELINE,
        "account": "RX-4410", "pickup_time": "15:30", "consignment_count": 2,
        "package_count": 2, "total_kg": "126",
        "lines": [
            {"line": 1, "con_note": "RX-77310042", "wms_ref": "0080034519",
             "customer": "Hilltop Joinery", "address": "1 Main St", "suburb": "Bacchus Marsh",
             "state": "VIC", "postcode": "3342", "packages": 1, "weight_kg": "72"},
            {"line": 2, "con_note": "RX-77310043", "wms_ref": "0080034520",
             "customer": "Old Mill Books", "address": "1 Main St", "suburb": "Castlemaine",
             "state": "VIC", "postcode": "3342", "packages": 1, "weight_kg": "54"},
        ]}
    got = client.get(f"/v1/manifests/{manifest['id']}", headers=dispatcher_headers).json()
    assert got["print_job"]["status"] == "pending" and got["print_job"]["job_id"] == str(job.job_id)


def test_its_own_printer_when_one_is_set(client, db, platen, manifest, dispatcher_headers,
                                         monkeypatch):
    from tms.config import get_settings
    monkeypatch.setattr(get_settings(), "manifest_printer", "Bay")
    assert _print(client, dispatcher_headers, manifest).json()["printer"] == "Bay"


def test_not_while_a_con_note_is_missing(client, db, platen, manifest, dispatcher_headers):
    rx = manifest["carrier"]["id"]
    book(client, dispatcher_headers, rx, consignment(db, ref="0080034521",
                                                    customer="Mill Lane Florist").id)
    r = _print(client, dispatcher_headers, manifest)
    assert r.status_code == 409 and r.json()["code"] == "con_note_missing"
    assert r.json()["detail"].startswith("Add the con note of Mill Lane Florist (0080034521) first")
    assert db.execute(select(PrintJob)).first() is None


def test_nothing_to_print_and_no_platen(client, db, manifest, dispatcher_headers, monkeypatch):
    r = _print(client, dispatcher_headers, manifest)
    assert r.status_code == 409 and r.json()["code"] == "printing_not_set_up"
    from tms.config import get_settings
    monkeypatch.setattr(get_settings(), "platen_url", "http://platen.test/intake/jobs")
    for line in manifest["lines"]:
        client.post(f"/v1/manifests/{manifest['id']}/unbook", headers=dispatcher_headers,
                    json={"consignment_id": line["consignment"]["id"]})
    r = _print(client, dispatcher_headers, manifest)
    assert r.status_code == 409 and r.json()["code"] == "manifest_empty"


def test_the_worker_hands_it_to_platen_and_platen_says_printed(
        client, db, platen, manifest, dispatcher_headers, capsys):
    key = _platen_key(capsys)
    job_id = _print(client, dispatcher_headers, manifest).json()["job_id"]
    db.execute(PrintJob.__table__.update().values(next_attempt_at=T0))
    db.commit()
    assert worker.send_print_jobs(db, platen.client, now=T0) == 1
    assert platen.bodies()[0]["template"] == "carrier-manifest"
    r = client.post(f"/v1/print-jobs/{job_id}/status", headers=key, json={"status": "printed"})
    assert r.status_code == 200 and r.json()["manifest_id"] == manifest["id"]
    got = client.get(f"/v1/manifests/{manifest['id']}", headers=dispatcher_headers).json()
    assert got["print_job"]["status"] == "printed"


def test_a_collected_manifest_prints_again_as_it_went(client, db, platen, manifest,
                                                      dispatcher_headers):
    import uuid
    ids = [line["consignment"]["id"] for line in manifest["lines"]]
    client.post(f"/v1/manifests/{manifest['id']}/collect", headers=dispatcher_headers,
                json={"message_id": str(uuid.uuid4()), "consignment_ids": ids})
    r = _print(client, dispatcher_headers, manifest)
    assert r.status_code == 202
    assert [line["con_note"] for line in _job(db).data["lines"]] == ["RX-77310042", "RX-77310043"]
```

**Step 2: Run them to see them fail**

Run: `.venv/bin/pytest tests/test_manifest_printing.py -q`
Expected: `6 failed`: `POST /v1/manifests/{id}/print` is `404`.

**Step 3: Implement**

Change `api/tms/services/carriers.py`:
```diff
--- a/api/tms/services/carriers.py
+++ b/api/tms/services/carriers.py
@@ -190,6 +190,16 @@ def set_con_note(db: Session, manifest_id: int, consignment_id: int, con_note: s
     return m
 
 
+def refuse_missing_con_notes(m: Manifest) -> None:
+    """The carrier's driver signs for each consignment by its con note."""
+    missing = [b.consignment for b in m.lines if b.con_note is None]
+    if missing:
+        c = missing[0]
+        raise Conflict("con_note_missing",
+                       f"Add the con note of {c.customer} ({c.wms_ref}) first: each consignment "
+                       "needs one before the carrier takes it")
+
+
 def collect(db: Session, manifest_id: int, consignment_ids: list[int], by: str) -> Manifest:
     """The carrier has called and taken what is on the manifest: each
     consignment is collected, and a ship call to Simple WMS waits in the
@@ -210,12 +220,7 @@ def collect(db: Session, manifest_id: int, consignment_ids: list[int], by: str)
     if len(consignment_ids) != len(set(consignment_ids)) or set(consignment_ids) != on_it:
         raise Conflict("stale", f"{m.number} changed while you were looking; it has been "
                                 "reloaded, so check it and try again")
-    missing = [b.consignment for b in m.lines if b.con_note is None]
-    if missing:
-        c = missing[0]
-        raise Conflict("con_note_missing",
-                       f"Add the con note of {c.customer} ({c.wms_ref}) first: each consignment "
-                       "needs one before the carrier takes it")
+    refuse_missing_con_notes(m)
     now = _now()
     for b in m.lines:
         c = b.consignment
```

Change `api/tms/services/printing.py`:
```diff
--- a/api/tms/services/printing.py
+++ b/api/tms/services/printing.py
@@ -1,6 +1,6 @@
-"""Run sheets through Platen. Simple TMS renders nothing: it queues a
-template name and JSON, the worker sends it, Platen prints it and says how
-it went."""
+"""Run sheets and carrier manifests through Platen. Simple TMS renders
+nothing: it queues a template name and JSON, the worker sends it, Platen
+prints it and says how it went."""
 from __future__ import annotations
 
 import uuid
@@ -11,14 +11,16 @@ from sqlalchemy.orm import Session
 
 from tms.api.errors import Conflict
 from tms.config import get_settings
-from tms.models import PrintJob, Run
-from tms.services import fleet
-from tms.services.load import consignment_totals, plain
+from tms.models import Manifest, PrintJob, Run
+from tms.services import carriers, fleet
+from tms.services.load import consignment_totals, plain, total
 from tms.services.runs import get_run, lock_planning, run_load
 
 # The Platen page template that prints it: make one with this id and publish it.
 RUN_SHEET = "run-sheet"
 RUN_SHEET_VERSION = "v1"
+MANIFEST = "carrier-manifest"
+MANIFEST_VERSION = "v1"
 
 
 def run_sheet_data(run: Run) -> dict:
@@ -47,12 +49,16 @@ def run_sheet_data(run: Run) -> dict:
     }
 
 
-def queue_run_sheet(db: Session, run_id: int, by: str) -> PrintJob:
-    settings = get_settings()
-    if not settings.platen_url:
+def _platen_set_up() -> None:
+    if not get_settings().platen_url:
         raise Conflict("printing_not_set_up",
                        "Printing is not set up: TMS_PLATEN_URL is empty. Ask whoever looks "
                        "after Simple TMS to connect Platen.")
+
+
+def queue_run_sheet(db: Session, run_id: int, by: str) -> PrintJob:
+    settings = get_settings()
+    _platen_set_up()
     # read the run as planning leaves it, so a stop dragged on at the same
     # moment cannot slip an over run past the check
     lock_planning(db)
@@ -77,6 +83,53 @@ def queue_run_sheet(db: Session, run_id: int, by: str) -> PrintJob:
     return job
 
 
+def manifest_data(m: Manifest) -> dict:
+    """What the carrier-manifest template binds to: the carrier and the day
+    at the top, one line per consignment with its con note. Text only, never
+    null, as for a run sheet."""
+    lines = []
+    for i, b in enumerate(m.lines, start=1):
+        c = b.consignment
+        to = c.ship_to or {}
+        t = consignment_totals(c.packages)
+        lines.append({
+            "line": i, "con_note": b.con_note or "", "wms_ref": c.wms_ref, "customer": c.customer,
+            "address": to.get("address") or "", "suburb": to.get("suburb") or "",
+            "state": to.get("state") or "", "postcode": to.get("postcode") or "",
+            "packages": t.packages, "weight_kg": plain(t.weight_kg),
+        })
+    t = total(consignment_totals(b.consignment.packages) for b in m.lines)
+    carrier = m.carrier
+    return {
+        "manifest_no": m.number, "date": m.day.isoformat(), "carrier": carrier.name,
+        "account": carrier.account or "",
+        "pickup_time": carrier.pickup_time.strftime("%H:%M") if carrier.pickup_time else "",
+        "consignment_count": len(m.lines), "package_count": t.packages,
+        "total_kg": plain(t.weight_kg), "lines": lines,
+    }
+
+
+def queue_manifest(db: Session, manifest_id: int, by: str) -> PrintJob:
+    """The manifest as it is: the bay before collection, what went once it
+    is collected. Each call prints again. Not with a con note missing: the
+    carrier's driver signs for each line by it."""
+    settings = get_settings()
+    _platen_set_up()
+    lock_planning(db)
+    m = carriers.get_manifest(db, manifest_id)
+    if not m.lines:
+        raise Conflict("manifest_empty", f"{m.number} has nothing on it to print")
+    carriers.refuse_missing_con_notes(m)
+    job = PrintJob(job_id=uuid.uuid4(), manifest_id=m.id, template=MANIFEST,
+                   version=MANIFEST_VERSION, printer=settings.manifest_printer_or_default,
+                   copies=1, reference={"type": "manifest", "ref": m.number},
+                   data=manifest_data(m), requested_by=by, status="pending", attempts=0,
+                   next_attempt_at=datetime.now(UTC))
+    db.add(job)
+    db.flush()
+    return job
+
+
 def send(http: httpx.Client, job: PrintJob) -> str | None:
     """Hand one job to Platen's intake. None when it took it; otherwise why
     not, in Platen's own words where it gave some. Never the key."""
```

Change `api/tms/api/routes/printing.py`:
```diff
--- a/api/tms/api/routes/printing.py
+++ b/api/tms/api/routes/printing.py
@@ -1,4 +1,4 @@
-"""Run sheets, and Platen telling us how each one went."""
+"""Run sheets and carrier manifests, and Platen telling us how each one went."""
 from __future__ import annotations
 
 import uuid
@@ -19,7 +19,9 @@ router = APIRouter(tags=["printing"])
 
 class PrintJobOut(BaseModel):
     job_id: uuid.UUID
-    run_id: int
+    # a run's run sheet, or a carrier's manifest
+    run_id: int | None
+    manifest_id: int | None
     template: str
     printer: str
     status: str
@@ -40,8 +42,9 @@ class JobStatusIn(BaseModel):
 
 
 def job_out(job: PrintJob) -> PrintJobOut:
-    return PrintJobOut(job_id=job.job_id, run_id=job.run_id, template=job.template,
-                       printer=job.printer, status=job.status, attempts=job.attempts,
+    return PrintJobOut(job_id=job.job_id, run_id=job.run_id, manifest_id=job.manifest_id,
+                       template=job.template, printer=job.printer, status=job.status,
+                       attempts=job.attempts,
                        last_error=job.last_error, created_at=job.created_at,
                        sent_at=job.sent_at, printed_at=job.printed_at)
 
@@ -54,6 +57,14 @@ def print_run_sheet(id: int, db: DB, who: Principal = require("runs:write")):
     return job_out(job)
 
 
+@router.post("/manifests/{id}/print", status_code=202, response_model=PrintJobOut)
+def print_manifest(id: int, db: DB, who: Principal = require("runs:write")):
+    """Queue the carrier's manifest for Platen. Each call prints again."""
+    job = printing.queue_manifest(db, id, by=who.name)
+    db.commit()
+    return job_out(job)
+
+
 @router.post("/print-jobs/{job_id}/status", response_model=PrintJobOut)
 def job_status(job_id: uuid.UUID, body: JobStatusIn, db: DB,
                who: Principal = require("printing:write")):
```

**Step 4: Run them**

Run: `.venv/bin/pytest tests/test_manifest_printing.py tests/test_collect.py tests/test_printing.py -q`
Expected: all pass (`6` new; run sheets as they were, now answering `"manifest_id": null`).

Run: `caffeinate -i .venv/bin/pytest -q`
Expected: `396 passed`.

**Step 5: Commit**

```bash
git add api/tms/services/printing.py api/tms/services/carriers.py api/tms/api/routes/printing.py \
  api/tests/test_manifest_printing.py
git commit -m "Print a carrier's manifest through Platen, once every con note is in"
```

### Task 6: Where each consignment is now, and found by its con note

**Files:**
- Modify: `api/tms/api/routes/consignments.py`
- Test: `api/tests/test_consignments.py`

**Step 1: Write the failing tests**

Change `api/tests/test_consignments.py`:
```diff
--- a/api/tests/test_consignments.py
+++ b/api/tests/test_consignments.py
@@ -104,3 +104,41 @@ def test_each_consignment_says_what_it_weighs_and_takes_up(client, wms, dispatch
     assert listed["weight_kg"] == "9.5" and listed["volume_m3"] == "0.096"
     one = client.get(f"/v1/consignments/{cid}", headers=dispatcher_headers).json()
     assert one["weight_kg"] == "9.5" and one["volume_m3"] == "0.096"
+
+
+def test_each_says_where_it_is_now_on_a_run_or_with_a_carrier(
+        client, db, manager_headers, dispatcher_headers):
+    from carriers_samples import RIDGELINE, book, new_carrier
+    from runs_samples import assign, consignment, driver, new_run, vehicle
+    vehicle(db)
+    driver(db)
+    run = new_run(client, dispatcher_headers)
+    rx = new_carrier(client, manager_headers)
+    on_run = consignment(db, ref="0080034519")
+    with_rx = consignment(db, ref="0080034520")
+    waiting = consignment(db, ref="0080034521")
+    assign(client, dispatcher_headers, run["id"], on_run.id)
+    m = book(client, dispatcher_headers, rx["id"], with_rx.id, con_note="RX-77310042").json()
+    items = {c["id"]: c for c in client.get("/v1/consignments", headers=dispatcher_headers).json()["items"]}
+    assert items[on_run.id]["run"] == {"id": run["id"], "run_no": run["run_no"], "driver": "Kim",
+                                       "sequence": 1}
+    assert items[on_run.id]["carrier"] is None
+    assert items[with_rx.id]["carrier"] == {"id": rx["id"], "name": RIDGELINE,
+                                            "con_note": "RX-77310042", "manifest_id": m["id"],
+                                            "manifest_no": m["manifest_no"], "collected_at": None}
+    assert items[with_rx.id]["run"] is None
+    assert (items[waiting.id]["run"], items[waiting.id]["carrier"]) == (None, None)
+    one = client.get(f"/v1/consignments/{with_rx.id}", headers=dispatcher_headers).json()
+    assert one["carrier"]["con_note"] == "RX-77310042"
+
+
+def test_found_by_its_con_note_too(client, db, manager_headers, dispatcher_headers):
+    from carriers_samples import book, new_carrier
+    from runs_samples import consignment
+    rx = new_carrier(client, manager_headers)
+    a = consignment(db, ref="0080034519", customer="Hilltop Joinery")
+    consignment(db, ref="0080034520", customer="Old Mill Books")
+    book(client, dispatcher_headers, rx["id"], a.id, con_note="RX-77310042")
+    found = client.get("/v1/consignments", headers=dispatcher_headers,
+                       params={"q": "rx-7731"}).json()
+    assert [c["id"] for c in found["items"]] == [a.id] and found["total"] == 1
```

**Step 2: Run them to see them fail**

Run: `.venv/bin/pytest tests/test_consignments.py -q`
Expected: `2 failed, 10 passed`: `KeyError: 'run'`, and the search by con note finds nothing (`assert [] == [1]`).

**Step 3: Implement**

Change `api/tms/api/routes/consignments.py`. `whereabouts` reads the runs and carriers of a whole page in two queries (the Plan runs tray asks for up to 2,000):
```diff
--- a/api/tms/api/routes/consignments.py
+++ b/api/tms/api/routes/consignments.py
@@ -11,7 +11,7 @@ from sqlalchemy.orm import selectinload
 from tms.api.deps import DB, Principal, require
 from tms.api.errors import NotFound
 from tms.api.schemas import Dec, Page
-from tms.models import Consignment
+from tms.models import Booking, Consignment, Manifest, Run, Stop
 from tms.services.load import consignment_totals
 
 router = APIRouter(tags=["consignments"])
@@ -33,6 +33,22 @@ class HistoryOut(BaseModel):
     detail: dict
 
 
+class OnRun(BaseModel):
+    id: int
+    run_no: str
+    driver: str
+    sequence: int
+
+
+class WithCarrier(BaseModel):
+    id: int
+    name: str
+    con_note: str | None
+    manifest_id: int
+    manifest_no: str
+    collected_at: datetime | None
+
+
 class ConsignmentOut(BaseModel):
     id: int
     wms_ref: str
@@ -47,12 +63,49 @@ class ConsignmentOut(BaseModel):
     weight_kg: Dec
     volume_m3: Dec
     packages: list[PackageOut]
+    # where it is now: the run it is on (or was last on, if it is delivered
+    # or came back), or the carrier it is booked with
+    run: OnRun | None = None
+    carrier: WithCarrier | None = None
 
 
 class ConsignmentDetail(ConsignmentOut):
     history: list[HistoryOut]
 
 
+ON_A_RUN = ("assigned", "departed", "delivered", "failed")
+WITH_A_CARRIER = ("booked", "collected")
+
+
+def whereabouts(db, cs: list[Consignment]) -> dict[int, dict]:
+    """Each one's run or carrier, read for the whole page at once."""
+    ids = [c.id for c in cs]
+    out: dict[int, dict] = {i: {} for i in ids}
+    if not ids:
+        return out
+    stops = db.execute(
+        select(Stop, Run).join(Run, Run.id == Stop.run_id)
+        .where(Stop.consignment_id.in_(ids), Stop.removed_at.is_(None))
+        .order_by(Stop.id).options(selectinload(Run.driver))).all()
+    for stop, run in stops:     # the latest stop wins
+        out[stop.consignment_id]["run"] = OnRun(id=run.id, run_no=run.number,
+                                                driver=run.driver.name, sequence=stop.sequence)
+    booked = db.execute(
+        select(Booking, Manifest).join(Manifest, Manifest.id == Booking.manifest_id)
+        .where(Booking.consignment_id.in_(ids), Booking.removed_at.is_(None))
+        .options(selectinload(Manifest.carrier))).all()
+    for b, m in booked:
+        out[b.consignment_id]["carrier"] = WithCarrier(
+            id=m.carrier.id, name=m.carrier.name, con_note=b.con_note, manifest_id=m.id,
+            manifest_no=m.number, collected_at=m.collected_at)
+    for c in cs:
+        if c.status not in ON_A_RUN:
+            out[c.id].pop("run", None)
+        if c.status not in WITH_A_CARRIER:
+            out[c.id].pop("carrier", None)
+    return out
+
+
 def consignment_out(c: Consignment) -> dict:
     totals = consignment_totals(c.packages)
     return dict(
@@ -72,7 +125,7 @@ def list_consignments(
     limit: int = Query(default=200, ge=0, le=2000), offset: int = Query(default=0, ge=0),
     who: Principal = require("consignments:read"),
 ):
-    """Newest first. `q` matches the WMS delivery or the customer."""
+    """Newest first. `q` matches the WMS delivery, the customer or the con note."""
     query = select(Consignment).options(selectinload(Consignment.packages))
     if status:
         query = query.where(Consignment.status.in_(status.split(",")))
@@ -80,13 +133,18 @@ def list_consignments(
         query = query.where(Consignment.warehouse == warehouse)
     if q:
         # autoescape: a % or _ typed in the search box is text, not a wildcard
+        con_notes = select(Booking.consignment_id).where(
+            Booking.removed_at.is_(None), Booking.con_note.icontains(q, autoescape=True))
         query = query.where(or_(Consignment.wms_ref.icontains(q, autoescape=True),
-                                Consignment.customer.icontains(q, autoescape=True)))
+                                Consignment.customer.icontains(q, autoescape=True),
+                                Consignment.id.in_(con_notes)))
     total = db.execute(select(func.count()).select_from(query.subquery())).scalar_one()
     rows = db.execute(
         query.order_by(Consignment.id.desc()).limit(limit).offset(offset)
     ).scalars().all()
-    return Page(items=[ConsignmentOut(**consignment_out(c)) for c in rows], total=total)
+    where = whereabouts(db, rows)
+    return Page(items=[ConsignmentOut(**consignment_out(c), **where[c.id]) for c in rows],
+                total=total)
 
 
 @router.get("/consignments/{id}", response_model=ConsignmentDetail)
@@ -99,6 +157,6 @@ def get_consignment(id: int, db: DB, who: Principal = require("consignments:read
     if c is None:
         raise NotFound(f"no consignment {id}")
     return ConsignmentDetail(
-        **consignment_out(c),
+        **consignment_out(c), **whereabouts(db, [c])[c.id],
         history=[HistoryOut(kind=e.kind, at=e.at, detail=e.detail or {}) for e in c.events],
     )
```

**Step 4: Run them**

Run: `.venv/bin/pytest tests/test_consignments.py -q`
Expected: `12 passed`. The whole suite: `398 passed`.

**Step 5: Commit**

```bash
git add api/tms/api/routes/consignments.py api/tests/test_consignments.py
git commit -m "Say which run or carrier each consignment is with, and find it by its con note"
```

### Task 7: What just happened: a con note in, a carrier collecting

**Files:**
- Modify: `api/tms/services/feed.py`, `api/tms/api/routes/feed.py`
- Test: `api/tests/test_feed.py`

**Step 1: Write the failing test**

Change `api/tests/test_feed.py`:
```diff
--- a/api/tests/test_feed.py
+++ b/api/tests/test_feed.py
@@ -86,3 +86,22 @@ def test_more_pre_starts_dont_mean_more_queries(client, db, dispatcher_headers):
     with queries() as four:
         assert len([i for i in feed(client, dispatcher_headers) if i["kind"] == "prestart"]) == 4
     assert four[0] == one[0]
+
+
+def test_a_con_note_in_and_the_carrier_collecting(client, db, manager_headers, dispatcher_headers):
+    from carriers_samples import RIDGELINE, book, new_carrier
+    from runs_samples import consignment
+    rx = new_carrier(client, manager_headers)
+    a = consignment(db, ref="0080034519", customer="Hilltop Joinery")
+    b = consignment(db, ref="0080034520", customer="Old Mill Books")
+    book(client, dispatcher_headers, rx["id"], a.id, con_note="RX-77310042")
+    m = book(client, dispatcher_headers, rx["id"], b.id, con_note="RX-77310043").json()
+    client.post(f"/v1/manifests/{m['id']}/collect", headers=dispatcher_headers,
+                json={"message_id": str(uuid.uuid4()), "consignment_ids": [a.id, b.id]})
+    items = feed(client, dispatcher_headers)
+    assert [i["kind"] for i in items] == ["collected", "con_note", "con_note", "packed", "packed"]
+    went, note = items[0], items[2]
+    assert (went["carrier"], went["manifest_no"], went["count"], went["by"]) == (
+        RIDGELINE, m["manifest_no"], 2, "dee")
+    assert (note["carrier"], note["con_note"], note["customer"], note["wms_ref"], note["by"]) == (
+        RIDGELINE, "RX-77310042", "Hilltop Joinery", "0080034519", "dee")
```

**Step 2: Run it to see it fail**

Run: `.venv/bin/pytest tests/test_feed.py -q`
Expected: `1 failed, 6 passed`: `AssertionError: assert ['packed', 'packed'] == ['collected', …]`.

**Step 3: Implement**

Change `api/tms/services/feed.py` (two queries whatever the number of rows, so `test_more_pre_starts_dont_mean_more_queries` still holds):
```diff
--- a/api/tms/services/feed.py
+++ b/api/tms/services/feed.py
@@ -1,23 +1,25 @@
 """What just happened: the latest of what the office hears, newest first,
 read from the records already kept: consignments packed in Simple WMS,
-delivered or not, runs leaving and finishing, pre-starts, and trucks marked
-fixed. Nothing is written for it, and no position is in it."""
+delivered or not, runs leaving and finishing, con notes in and carriers
+collecting, pre-starts, and trucks marked fixed. Nothing is written for it,
+and no position is in it."""
 from __future__ import annotations
 
 from dataclasses import dataclass, field
 from datetime import datetime
 
-from sqlalchemy import select
+from sqlalchemy import func, select
 from sqlalchemy.orm import Session, selectinload
 
 from tms.models import (
-    Consignment, ConsignmentEvent, Driver, Pod, PrestartCheck, Run, Stop, Vehicle, VehicleRecord,
+    Booking, Consignment, ConsignmentEvent, Driver, Manifest, Pod, PrestartCheck, Run, Stop,
+    Vehicle, VehicleRecord,
 )
 
 
 @dataclass
 class Happened:
-    # packed, delivered, failed, left, finished, prestart, fixed
+    # packed, delivered, failed, left, finished, con_note, collected, prestart, fixed
     kind: str
     at: datetime
     run: Run | None = None
@@ -32,6 +34,11 @@ class Happened:
     failed: list[str] = field(default_factory=list)
     by: str | None = None
     note: str | None = None
+    # a con note in, or a carrier collecting its manifest (and how many on it)
+    carrier: str | None = None
+    con_note: str | None = None
+    manifest_no: str | None = None
+    count: int | None = None
 
 
 def _runs(db: Session, ids: set[int]) -> dict[int, Run]:
@@ -74,6 +81,26 @@ def _trips(db: Session, limit: int) -> list[Happened]:
     return out
 
 
+def _carriers(db: Session, limit: int) -> list[Happened]:
+    notes = db.execute(
+        select(ConsignmentEvent, Consignment).join(Consignment)
+        .where(ConsignmentEvent.kind == "con_note")
+        .order_by(ConsignmentEvent.id.desc()).limit(limit)).all()
+    out = [Happened(kind="con_note", at=e.at, customer=c.customer, wms_ref=c.wms_ref,
+                    carrier=e.detail.get("carrier"), con_note=e.detail.get("con_note"),
+                    by=e.detail.get("by")) for e, c in notes]
+    on_it = (select(func.count()).where(Booking.manifest_id == Manifest.id,
+                                        Booking.removed_at.is_(None))
+             .correlate(Manifest).scalar_subquery())
+    went = db.execute(
+        select(Manifest, on_it).where(Manifest.collected_at.is_not(None))
+        .order_by(Manifest.collected_at.desc()).limit(limit)
+        .options(selectinload(Manifest.carrier))).all()
+    out += [Happened(kind="collected", at=m.collected_at, carrier=m.carrier.name,
+                     manifest_no=m.number, count=n, by=m.collected_by) for m, n in went]
+    return out
+
+
 def _checks(db: Session, limit: int) -> list[Happened]:
     checks = db.execute(select(PrestartCheck).order_by(PrestartCheck.id.desc())
                         .limit(limit).options(selectinload(PrestartCheck.items))).scalars().all()
@@ -100,7 +127,7 @@ def recent(db: Session, who, limit: int) -> list[Happened]:
     """The newest `limit` of each kind, merged and cut to `limit`. Pre-starts
     and trucks marked fixed are the fleet's records: only for a caller who
     may see the fleet (fleet:read)."""
-    items = _consignments(db, limit) + _trips(db, limit)
+    items = _consignments(db, limit) + _trips(db, limit) + _carriers(db, limit)
     if who.has_scope("fleet:read"):
         items += _checks(db, limit) + _fixes(db, limit)
     return sorted(items, key=lambda h: h.at, reverse=True)[:limit]
```

Change `api/tms/api/routes/feed.py`:
```diff
--- a/api/tms/api/routes/feed.py
+++ b/api/tms/api/routes/feed.py
@@ -13,7 +13,7 @@ router = APIRouter(tags=["runs"])
 
 
 class FeedItem(BaseModel):
-    # packed, delivered, failed, left, finished, prestart, fixed
+    # packed, delivered, failed, left, finished, con_note, collected, prestart, fixed
     kind: str
     at: dt.datetime
     run_id: int | None
@@ -28,9 +28,15 @@ class FeedItem(BaseModel):
     # prestart: passed, failed or grounded, and the items that failed
     result: str | None
     failed: list[str]
-    # who recorded it: a pre-start's sender, the fleet manager who marked it fixed
+    # who recorded it: a pre-start's sender, the fleet manager who marked it
+    # fixed, who typed a con note or marked a manifest collected
     by: str | None
     note: str | None
+    # con_note and collected: the carrier, the con note, the manifest and how many on it
+    carrier: str | None
+    con_note: str | None
+    manifest_no: str | None
+    count: int | None
 
 
 class FeedOut(BaseModel):
@@ -41,10 +47,12 @@ class FeedOut(BaseModel):
 def what_just_happened(db: DB, limit: int = Query(default=20, ge=1, le=100),
                        who: Principal = require("runs:read")):
     """Newest first: consignments packed in Simple WMS, delivered or not,
-    runs leaving and finishing; pre-starts and trucks marked fixed only for
-    a caller with fleet:read."""
+    runs leaving and finishing, con notes in and carriers collecting;
+    pre-starts and trucks marked fixed only for a caller with fleet:read."""
     return FeedOut(items=[FeedItem(
         kind=h.kind, at=h.at, run_id=h.run.id if h.run else None,
         run_no=h.run.number if h.run else None, rego=h.rego, driver=h.driver,
         sequence=h.sequence, customer=h.customer, wms_ref=h.wms_ref, reason=h.reason,
-        result=h.result, failed=h.failed, by=h.by, note=h.note) for h in feed.recent(db, who, limit)])
+        result=h.result, failed=h.failed, by=h.by, note=h.note, carrier=h.carrier,
+        con_note=h.con_note, manifest_no=h.manifest_no, count=h.count)
+        for h in feed.recent(db, who, limit)])
```

**Step 4: Run them, and the whole suite**

Run: `.venv/bin/pytest tests/test_feed.py -q`
Expected: `7 passed`.

Run: `caffeinate -i .venv/bin/pytest -q`
Expected: `399 passed`.

**Step 5: Commit**

```bash
git add api/tms/services/feed.py api/tms/api/routes/feed.py api/tests/test_feed.py
git commit -m "Tell the office when a con note is in and when a carrier collects"
```

### Task 8: `docs/api.md`

**Files:**
- Modify: `docs/api.md`

**Step 1: Write it**

A new **Carriers** section (carriers, the carrier bay, collect, manifests) before "The driver app"; the consignment statuses, history kinds, `run` and `carrier`, and `q` matching the con note; `assign` taking one off the bay; the print job's `manifest_id`; the feed's two new kinds; collect among the confirmations with a `message_id`.

Change `docs/api.md`:
````diff
--- a/docs/api.md
+++ b/docs/api.md
@@ -21,8 +21,9 @@ are at `/docs`.
   a `422` too. Something that is not there is `404` with `{ "detail" }`.
   Something well formed that cannot be done as things stand (a run that has
   left, a truck over its limit) is `409` with `{ "detail", "code" }`.
-- **Confirmations from the driver app** (pre-start, start, deliver, fail, finish) carry a
-  `message_id`, a UUID the phone makes when the confirmation is made. The
+- **Confirmations** (from the driver app: pre-start, start, deliver, fail,
+  finish; from the office: finish, and a carrier's manifest collected) carry a
+  `message_id`, a UUID the phone or the desktop makes when the confirmation is made. The
   first time, the API does the work and keeps the reply; the same
   `message_id` again from the same sender gets that reply back (status and
   body) and changes nothing, however long after. A refusal is not kept, so the
@@ -32,8 +33,9 @@ are at `/docs`.
   `message_id` someone else has used is `409 message_id_taken`; one the same
   sender used for another confirmation (another action, or the same on
   another run or stop) is `409 message_id_reused`.
-- **Never deleted:** drivers and vehicles are deactivated, runs cancelled and
-  stops taken off, never deleted. A consignment's history is append-only, and
+- **Never deleted:** drivers, vehicles and carriers are deactivated, runs
+  cancelled, stops taken off and consignments taken off a carrier's manifest,
+  never deleted. A consignment's history is append-only, and
   so are pre-start checks and what a fleet manager records about a truck: a
   mistake is put right by a newer record.
 - **Except positions.** Where a truck was (live location, and the position on
@@ -266,7 +268,7 @@ Each driver: `{ "id", "code", "name", "active", "locked", "failed_attempts", "cr
 
 ### GET /v1/consignments
 Newest first. Filters: `status` (comma separated), `warehouse`, `q` (part of
-the WMS delivery ref or the customer name, any case; `%` and `_` are plain
+the WMS delivery ref, the customer name or the con note, any case; `%` and `_` are plain
 text; at most 64 characters), `limit` (0 to 2000, default 200), `offset`
 (default 0). → `{ "items", "total" }`, where `total` counts every match, not
 just this page. Each:
@@ -295,12 +297,27 @@ The same, plus its history, oldest first:
               "detail": { "wms_event_id": "…", "packages": 1 } }]
 ```
 Statuses: `packed` (in the tray), `assigned` (on a planned run), `departed`
-(its run has left), `delivered`, and `failed` (the driver couldn't deliver
-it: it comes back to the depot and waits in the tray to be planned again).
-The history has a row for each: `packed`, `assigned` and `unassigned` (with
-the run and who), `departed` (with the run, vehicle, driver and who),
-`delivered` and `failed` (see "The driver app"). A consignment planned again
-after `failed` gets `assigned` and `departed` rows again.
+(its run has left), `delivered`, `failed` (the driver couldn't deliver
+it: it comes back to the depot and waits in the tray to be planned again),
+`booked` (on a carrier's manifest, on the carrier bay) and `collected` (the
+carrier has it). The history has a row for each: `packed`, `assigned` and
+`unassigned` (with the run and who), `departed` (with the run, vehicle,
+driver and who), `delivered` and `failed` (see "The driver app"), `booked`
+and `unbooked` (with the carrier, the manifest and who), `con_note` (each con
+note set, with the one it replaced in `was`, the carrier and who) and
+`collected` (with the carrier, the con note, the manifest and who). A
+consignment planned again after `failed` gets `assigned` and `departed` rows
+again.
+
+Each consignment, in the list and on its own, also says where it is now:
+```json
+"run": { "id": 7, "run_no": "R-0007", "driver": "Kim", "sequence": 3 },
+"carrier": null
+```
+`run` is the run it is on, or was last on once it is `delivered` or `failed`;
+`carrier` is the carrier it is booked with or collected by,
+`{ "id", "name", "con_note", "manifest_id", "manifest_no", "collected_at" }`.
+Each is `null` otherwise. `q` also matches the con note.
 
 ## Runs
 
@@ -406,7 +423,8 @@ Each answers with the run as it now is.
 - `POST /v1/runs/{id}/assign` — `{ "consignment_id", "position"? }`. Puts a
   `packed` consignment (or a `failed` one, back from a run) on the run at
   `position` (1 is first; left out, last; past the end, last), or moves one already on this run to that place, or
-  takes it off another planned run and puts it here. Already in that place:
+  takes it off another planned run, or off the carrier bay (history
+  `unbooked`), and puts it here. Already in that place:
   nothing changes (with no `position`, one already on this run moves to last). The history gets `assigned` (and `unassigned` from the other run)
   with the run and who. Past the truck's limits is allowed: the run shows
   `over`, and cannot print or leave until a stop moves.
@@ -423,7 +441,7 @@ Each answers with the run as it now is.
 | `409` code | When |
 | --- | --- |
 | `run_not_planned` | the run has departed or been cancelled; its stops are fixed |
-| `not_plannable` | the consignment has left or been delivered |
+| `not_plannable` | the consignment has left, been delivered or been collected by a carrier |
 | `vehicle_unavailable` | the run's truck has been deactivated |
 | `vehicle_grounded`, `vehicle_overdue` | the run's truck can't go (rule 3) |
 | `stale` | a reorder from an order that is not the run's as it is now |
@@ -471,8 +489,8 @@ logged as an error naming the likely cause. Without `TMS_WMS_URL` and
   `vehicle_grounded`, `vehicle_overdue`.
 
 ```json
-{ "job_id": "uuid", "run_id": 7, "template": "run-sheet", "printer": "dispatch",
-  "status": "pending", "attempts": 0, "last_error": null,
+{ "job_id": "uuid", "run_id": 7, "manifest_id": null, "template": "run-sheet",
+  "printer": "dispatch", "status": "pending", "attempts": 0, "last_error": null,
   "created_at": "…", "sent_at": null, "printed_at": null }
 ```
 The worker sends it to `TMS_PLATEN_URL` (Platen's `https://<platen>/intake/jobs`)
@@ -498,13 +516,165 @@ calls, and `last_error` keeps Platen's answer in its own words.
 
 ### POST /v1/print-jobs/{job_id}/status
 
-How a print went, from Platen: the body Simple WMS takes,
+How a print went, from Platen, for a run sheet or a carrier manifest: the body Simple WMS takes,
 `{ "status": "accepted" | "printed" | "failed", "message"?, "printer"? }`.
 Needs `printing:write` (an API key made for Platen). `printed` sets
 `printed_at`; `failed` keeps `message` as `last_error`; a late `accepted` does
 not undo `printed` or `failed`. Answers with the print job as it now is. An
 unknown `job_id` is `404`, which Platen does not ask again.
 
+## Carriers
+
+Freight going with outside carriers, booked by hand in this version: the
+dispatcher books with the carrier as they always have, then types or scans
+the con note here. The carrier calls, takes what is on its manifest, and the
+dispatcher marks it collected: each consignment is then marked shipped in
+Simple WMS with the carrier's name and the con note, as a run's are when it
+leaves.
+
+A carrier is changed by a fleet manager (`fleet:write`) and read by the
+office (`runs:read`). Each:
+```json
+{ "id": 2, "name": "Ridgeline Express (sample)", "account": "RX-4410",
+  "pickup_time": "15:30", "adapter": "manual", "active": true,
+  "created_at": "2026-10-15T00:02:11Z" }
+```
+`name` is what Simple WMS is told as the carrier (1 to 64 characters, one
+carrier per name whatever its case, and never `Own fleet`, which is your own
+trucks). `account` is your account with the carrier, printed on the manifest
+(at most 40, or `null`). `pickup_time` is when it usually calls, by the
+depot's clock (`HH:MM`, or `null`). `adapter` is how it is booked: `manual`
+(by hand) is the only one yet; anything else is a `422`. A carrier's own API
+comes later as another adapter, with no change to any of this.
+
+- `GET /v1/carriers?active=true` — by name, as `{ "items", "total" }`.
+- `POST /v1/carriers` — `{ "name", "account"?, "pickup_time"?, "adapter"? }` → `201`.
+- `GET /v1/carriers/{id}`
+- `PATCH /v1/carriers/{id}` — only what is given changes; `account` or
+  `pickup_time` given as `null` is cleared. A new name is what Simple WMS is
+  told from the next collection on.
+- `POST /v1/carriers/{id}/deactivate`, `/reactivate` — a deactivated carrier
+  takes no new bookings; what is already on its manifest stays, to be collected.
+
+### The carrier bay
+
+A **manifest** is one carrier's pickup: the consignments waiting on the
+carrier bay for it on a day, then what it took. There is one open manifest
+per carrier per day; once collected, the next booking for that carrier and
+day starts a new one (a carrier that calls twice). `runs:read` to look,
+`runs:write` to change. Each change is made one at a time with the planning
+of runs, so a consignment is on one run or with one carrier, never both.
+```json
+{
+  "id": 3, "manifest_no": "M-0003", "date": "2026-10-15", "status": "open",
+  "carrier": { "id": 2, "name": "Ridgeline Express (sample)", "…": "…" },
+  "created_by": "dee", "created_at": "…", "collected_at": null, "collected_by": null,
+  "lines": [{ "con_note": "RX-77310042", "added_at": "…",
+              "consignment": { "id": 31, "wms_ref": "0080034519", "customer": "Hilltop Joinery",
+                               "suburb": "Bacchus Marsh", "status": "booked", "packages": 5,
+                               "cartons": 5, "weight_kg": "72", "volume_m3": "0.5" } }],
+  "totals": { "consignments": 1, "packages": 5, "cartons": 5, "weight_kg": "72" },
+  "needs_con_note": 0,
+  "print_job": null,
+  "shipping": { "pending": 0, "done": 0, "failed": 0 }
+}
+```
+`status` is `open` or `collected`. `print_job` is its latest print (as a
+run's `run_sheet`), `shipping` its ship calls to Simple WMS by state.
+
+- `GET /v1/manifests?date=2026-10-15` — that day's manifests with something
+  on them, open or collected, oldest first, as `{ "items", "total" }`. With
+  `left_open=true`, first the open ones from earlier days still waiting for
+  their carrier.
+- `GET /v1/manifests/{id}`
+- `POST /v1/carriers/{id}/book` — `{ "consignment_id", "date", "con_note"? }`.
+  Puts a consignment on the carrier's manifest for that day (made if there is
+  none): from the tray (`packed` or `failed`), off a planned run (history
+  `unassigned`), or off another manifest (history `unbooked`). It becomes
+  `booked` (history `booked`). Moved to another day with the same carrier it
+  keeps its con note; to another carrier it doesn't, as a con note is one
+  carrier's. Already there: nothing changes, but for a `con_note` given.
+  Answers with the manifest it is on.
+- `POST /v1/manifests/{id}/con-note` — `{ "consignment_id", "con_note" }`:
+  typed or scanned, or put right, until it is collected. Each new con note is
+  a `con_note` row in the history naming the one it replaced; the same again
+  changes nothing. Not on this manifest: `404`.
+- `POST /v1/manifests/{id}/unbook` — `{ "consignment_id" }`: back to the
+  tray (`packed`, history `unbooked`). Not on it: nothing happens.
+
+A **con note** is read as the carrier prints it: spaces around it are taken
+off and letters made capitals (` rx-77310042 ` is `RX-77310042`); it must
+then be 4 to 40 letters, digits and dashes, starting with a letter or digit,
+or it is a `422` on `con_note` (a space inside is refused, not guessed at). A
+con note is one consignment's with a carrier, for good: another consignment
+with the same carrier and con note is a `422` naming the first. Another
+carrier's numbers are its own.
+
+| `409` code | When |
+| --- | --- |
+| `not_plannable` | the consignment has left on a run, been delivered or been collected |
+| `carrier_inactive` | the carrier is deactivated: nothing new can be booked with it |
+| `manifest_collected` | the carrier has collected it; what is on it can't change |
+
+An unknown consignment is a `422` on `consignment_id`; an unknown carrier or
+manifest `404`.
+
+### POST /v1/manifests/{id}/collect
+
+`{ "message_id", "consignment_ids": [ … ] }`: the carrier has called and
+taken it, with what is on the manifest as you saw it. Each consignment
+becomes `collected` (history `collected` with the carrier, the con note, the
+manifest and who), and a ship call to Simple WMS is queued for each, all in
+one go; the manifest is `collected` with when and who. Once per `message_id`
+(see Shared rules); a manifest already collected with just those on it is
+answered as it is, and nothing more is queued.
+
+| `409` code | When |
+| --- | --- |
+| `stale` | `consignment_ids` isn't exactly what is on it now (someone booked or took one off meanwhile): reload and check |
+| `con_note_missing` | something on it has no con note: the detail names the first |
+| `manifest_empty` | nothing is on it |
+| `manifest_collected` | it was collected with something else on it |
+
+The worker then makes, for each consignment, the same call as for a run
+(see `POST /v1/runs/{id}/depart`), with the carrier and its con note:
+```
+POST {TMS_WMS_URL}/v1/deliveries/{wms_ref}/ship
+{ "message_id": "<its own uuid>", "owner": "DEFAULT",
+  "carrier": "Ridgeline Express (sample)", "tracking_no": "RX-77310042", "shipped_by": "dee" }
+```
+with the same retries, so Simple WMS ships each delivery once and fires
+`delivery.shipped` with that carrier and tracking number.
+
+- `POST /v1/manifests/{id}/retry-ship` — calls that gave up start again from
+  scratch, each with its same `message_id`.
+
+### Manifests
+
+- `POST /v1/manifests/{id}/print` → `202` with the print job, as a run sheet's
+  (`run_id` `null`, `manifest_id` set). Each call prints again: before it is
+  collected, what is on the bay now; after, what went. Refused with `409`:
+  `printing_not_set_up`, `manifest_empty`, and `con_note_missing` (the
+  carrier's driver signs for each consignment by its con note).
+
+The worker sends it to Platen as a run sheet goes, to the printer
+`TMS_MANIFEST_PRINTER` (or `TMS_RUN_SHEET_PRINTER` when that is empty):
+```json
+{ "job_id": "uuid", "template": "carrier-manifest", "version": "v1", "printer": "dispatch",
+  "copies": 1, "reference": { "type": "manifest", "ref": "M-0003" },
+  "data": {
+    "manifest_no": "M-0003", "date": "2026-10-15", "carrier": "Ridgeline Express (sample)",
+    "account": "RX-4410", "pickup_time": "15:30", "consignment_count": 2,
+    "package_count": 7, "total_kg": "126",
+    "lines": [{ "line": 1, "con_note": "RX-77310042", "wms_ref": "0080034519",
+                "customer": "Hilltop Joinery", "address": "4 Mill Rd", "suburb": "Bacchus Marsh",
+                "state": "VIC", "postcode": "3340", "packages": 5, "weight_kg": "72" }]
+  } }
+```
+Every value is text or a whole number, never `null`. In Platen,
+`carrier-manifest` is a page template: one row per entry in `lines`, with the
+rest beside it.
+
 ## The driver app
 
 The driver's phone uses these with a driver token (`deliveries:read`,
@@ -771,7 +941,8 @@ is HTML, as the map shows it.
 { "items": [{ "kind": "delivered", "at": "2026-10-14T00:31:07Z",
   "run_id": 7, "run_no": "R-0007", "rego": "TMS041", "driver": "Kim",
   "sequence": 3, "customer": "Main Street Chemist", "wms_ref": "0080034521",
-  "reason": null, "result": null, "failed": [], "by": null, "note": null }] }
+  "reason": null, "result": null, "failed": [], "by": null, "note": null,
+  "carrier": null, "con_note": null, "manifest_no": null, "count": null }] }
 ```
 
 | `kind` | What | Fields with something in them |
@@ -779,6 +950,8 @@ is HTML, as the map shows it.
 | `packed` | a consignment came from Simple WMS | `customer`, `wms_ref` |
 | `delivered`, `failed` | a stop delivered, or not (with the `reason`) | the run, `rego`, `driver`, `sequence`, `customer`, `wms_ref` |
 | `left`, `finished` | a run left the depot, or was finished | the run, `rego`, `driver` |
+| `con_note` | a con note typed, scanned or put right | `carrier`, `con_note`, `customer`, `wms_ref`, `by` |
+| `collected` | a carrier collected its manifest | `carrier`, `manifest_no`, `count` (how many on it), `by` |
 | `prestart` | a pre-start check: `result` `passed`, `failed` or `grounded` | the run, `rego`, `driver` (or `by`), `failed` |
 | `fixed` | a fleet manager marked a grounded truck fixed | `rego`, `by`, `note` |
 
@@ -797,7 +970,7 @@ POST /v1/subscribers   (on Simple WMS, with an integration:admin key)
 ```
 and give Simple TMS a Simple WMS key with `tasks:read` and `tasks:write` in
 `TMS_WMS_API_KEY` (`tasks:read` to read a packed delivery, `tasks:write` to say
-it shipped: see `POST /v1/runs/{id}/depart`) and the WMS address in
+it shipped: see `POST /v1/runs/{id}/depart` and `POST /v1/manifests/{id}/collect`) and the WMS address in
 `TMS_WMS_URL`.
 
 The body is Simple WMS's event envelope, exactly as sent:
````

**Step 2: Check it against the routes**

Run (repo root):
```
api/.venv/bin/python - <<'PY'
import pathlib, re
doc = pathlib.Path("docs/api.md").read_text()
src = "".join(p.read_text() for p in pathlib.Path("api/tms/api/routes").glob("*.py"))
routes = re.findall(r'@router\.(get|post|patch)\("(/(?:carriers|manifests)[^"]*)"', src)
# a path in full, or a sibling's last part after it ("`/deactivate`, `/reactivate`")
missing = [f"{m.upper()} /v1{p}" for m, p in routes
           if f"/v1{p}" not in doc and f"`/{p.rsplit('/', 1)[-1]}`" not in doc]
print(len(routes), "routes;", missing or "each one is in docs/api.md")
PY
```
Expected: `14 routes; each one is in docs/api.md`.

**Step 3: Commit**

```bash
git add docs/api.md
git commit -m "Document carriers, the carrier bay, collecting and manifests"
```

## Part B — the dispatcher's desktop (`apps/desktop`)

### Task 9: Con notes, the carrier bay's numbers and where a consignment is, in the screens' words

**Files:**
- Create: `apps/desktop/src/lib/carriers.ts`
- Modify: `apps/desktop/src/api/types.ts`, `apps/desktop/src/lib/live.ts` (the feed's two new lines)
- Test: `apps/desktop/src/test/bay.ts` (new: sample manifests, shared by Tasks 9, 10 and 13), `apps/desktop/src/test/carriers.test.ts` (new)

**Step 1: Write the failing tests**

Create `apps/desktop/src/test/bay.ts`:
```ts
/** Manifests on the carrier bay, for the desktop's tests. The carrier names
 * are samples, never real carriers. */
import type { Manifest, ManifestLine } from "../api/types";

export const RIDGELINE = "Ridgeline Express (sample)";
export const VALLEY = "Valley Freight (sample)";
export const PLAINS = "Plains Parcels (sample)";

export const line = (id: number, customer: string, con_note: string | null, cartons = 2, weight_kg = "24"): ManifestLine => ({
  con_note, added_at: "",
  consignment: { id, wms_ref: `00800345${id}`, customer, suburb: "Daylesford", status: "booked", packages: cartons,
                 cartons, weight_kg, volume_m3: "0.1" },
});

export const manifest = (id: number, carrier: string, lines: ManifestLine[], over: Partial<Manifest> = {}): Manifest => ({
  id, manifest_no: `M-000${id}`, date: "2026-10-15", status: "open",
  carrier: { id, name: carrier, account: null, pickup_time: "15:30", adapter: "manual", active: true, created_at: "" },
  created_by: "dee", created_at: "", collected_at: null, collected_by: null, lines,
  totals: { consignments: lines.length, packages: lines.reduce((n, l) => n + l.consignment.packages, 0),
            cartons: lines.reduce((n, l) => n + l.consignment.cartons, 0), weight_kg: "96" },
  needs_con_note: lines.filter((l) => !l.con_note).length, print_job: null,
  shipping: { pending: 0, done: 0, failed: 0 }, ...over,
});
```

Create `apps/desktop/src/test/carriers.test.ts`:
```ts
import type { Consignment, FeedItem } from "../api/types";
import { bayStats, conNoteProblem, nextNeedingConNote, normalConNote, pickupClock, pickupLine,
         whereNow } from "../lib/carriers";
import { feedLine } from "../lib/live";
import { line, manifest, PLAINS, RIDGELINE, VALLEY } from "./bay";

const RX = manifest(1, RIDGELINE, [line(26, "Mill Lane Florist", null), line(33, "Hilltop Joinery", "RX-77310042", 5, "72")]);
const VF = manifest(2, VALLEY, [line(24, "Paddock Produce", "VF-00418821")]);
const PP = manifest(3, PLAINS, [line(20, "Corner Hardware", "PP-1001")],
                    { status: "collected", collected_at: new Date(2026, 9, 15, 10, 15).toISOString(), collected_by: "dee" });

describe("the carrier bay", () => {
  it("reads a con note as the carrier prints it, and says what is wrong with one", () => {
    expect(normalConNote("  rx-77310042 ")).toBe("RX-77310042");
    expect(conNoteProblem("RX-77310042")).toBeNull();
    for (const bad of ["RX 7731", "RX", "-RX7731", "RX_7731", "R".repeat(41)]) {
      expect(conNoteProblem(normalConNote(bad))).toBe("A con note is 4 to 40 letters, digits and dashes, as on the carrier's label.");
    }
  });

  it("counts what needs a con note, what waits for pickup and what went today", () => {
    expect(bayStats([RX, VF, PP])).toEqual({
      needs: 1, firstNeeding: { manifest: RX, consignment: RX.lines[0].consignment },
      waiting: 3, carriers: 2, collected: 1, lastCollected: PP,
    });
    expect(bayStats([])).toEqual({ needs: 0, firstNeeding: null, waiting: 0, carriers: 0, collected: 0, lastCollected: null });
  });

  it("goes on to the next one with no con note", () => {
    const more = manifest(4, VALLEY, [line(40, "Old Mill Books", null)]);
    expect(nextNeedingConNote([RX, more])?.consignment.id).toBe(26);
    expect(nextNeedingConNote([RX, more], 26)?.consignment.id).toBe(40);
    expect(nextNeedingConNote([VF])).toBeNull();
  });

  it("says when each carrier calls and what it takes", () => {
    expect(pickupClock("15:30")).toBe("3:30 pm");
    expect(pickupClock("09:05")).toBe("9:05 am");
    expect(pickupLine(RX)).toBe("Pickup about 3:30 pm · 2 consignments · 7 cartons · 96 kg");
    expect(pickupLine({ ...VF, carrier: { ...VF.carrier, pickup_time: null } })).toBe("1 consignment · 2 cartons · 96 kg");
    expect(pickupLine({ ...PP, print_job: { job_id: "j", status: "printed", printer: "office", last_error: null, created_at: "", printed_at: "" },
                        shipping: { pending: 0, done: 1, failed: 0 } }))
      .toBe("Collected 10:15 am · manifest M-0003 printed by Platen · marked shipped in Simple WMS");
    expect(pickupLine({ ...PP, shipping: { pending: 1, done: 0, failed: 0 } }))
      .toBe("Collected 10:15 am · manifest M-0003 · telling Simple WMS");
  });
});

describe("where a consignment is now", () => {
  const c = (status: string, over: Partial<Consignment> = {}): Consignment => ({
    id: 1, wms_ref: "0080034519", warehouse: "BAL-WH01", owner: "DEFAULT", customer: "Station Deli", ship_to: {},
    required_by: null, status, created_at: "", weight_kg: "1", volume_m3: "1", packages: [], run: null, carrier: null, ...over,
  });
  const rx = { id: 1, name: "Ridgeline Express (sample)", con_note: null, manifest_id: 1, manifest_no: "M-0001", collected_at: null };

  it("in words, with the colour the design gives it", () => {
    expect(whereNow(c("packed"))).toEqual({ text: "To plan", tone: "amber" });
    expect(whereNow(c("failed"))).toEqual({ text: "Not delivered", tone: "red" });
    expect(whereNow(c("assigned", { run: { id: 8, run_no: "R-0008", driver: "Raj", sequence: 2 } })))
      .toEqual({ text: "On run R-0008", tone: "blue" });
    expect(whereNow(c("departed", { run: { id: 7, run_no: "R-0007", driver: "Kim", sequence: 5 } })))
      .toEqual({ text: "On run R-0007", tone: "green" });
    expect(whereNow(c("delivered"))).toEqual({ text: "Delivered", tone: "green" });
    expect(whereNow(c("booked", { carrier: rx }))).toEqual({ text: "Needs con note", tone: "amber" });
    expect(whereNow(c("booked", { carrier: { ...rx, con_note: "RX-1" } }))).toEqual({ text: "With carrier", tone: "grey" });
    expect(whereNow(c("collected", { carrier: { ...rx, con_note: "RX-1" } }))).toEqual({ text: "Collected", tone: "grey" });
  });
});

describe("the feed's carrier lines", () => {
  const item = (over: Partial<FeedItem>): FeedItem => ({
    kind: "con_note", at: "", run_id: null, run_no: null, rego: null, driver: null, sequence: null, customer: "Hilltop Joinery",
    wms_ref: "0080034519", reason: null, result: null, failed: [], by: "dee", note: null, carrier: "Ridgeline Express (sample)",
    con_note: "RX-77310042", manifest_no: null, count: null, ...over,
  });

  it("words a con note in and a carrier collecting", () => {
    expect(feedLine(item({}))).toEqual({ text: "Con note RX-77310042 for Hilltop Joinery · Ridgeline Express (sample)", tone: "amber" });
    expect(feedLine(item({ kind: "collected", manifest_no: "M-0003", count: 2 })))
      .toEqual({ text: "Ridgeline Express (sample) collected 2 consignments · M-0003", tone: "green" });
    expect(feedLine(item({ kind: "collected", manifest_no: "M-0004", count: 1 })).text)
      .toBe("Ridgeline Express (sample) collected 1 consignment · M-0004");
  });
});
```

**Step 2: Run them to see them fail**

Run: `npx vitest run src/test/carriers.test.ts`
Expected: `Failed to resolve import "../lib/carriers"`.

**Step 3: Implement**

Change `apps/desktop/src/api/types.ts` (the new fields on `Consignment` and `FeedItem` are optional, so the existing tests' samples still type-check):
```diff
--- a/apps/desktop/src/api/types.ts
+++ b/apps/desktop/src/api/types.ts
@@ -34,8 +34,17 @@ export interface Consignment {
   weight_kg: string;
   volume_m3: string;
   packages: Package[];
+  /** where it is now: the run it is on (or was last on), or the carrier it is booked with */
+  run?: { id: number; run_no: string; driver: string; sequence: number } | null;
+  carrier?: { id: number; name: string; con_note: string | null; manifest_id: number; manifest_no: string;
+              collected_at: string | null } | null;
 }
 
+/** A line of a consignment's history, oldest first (GET /v1/consignments/{id}). */
+export interface HistoryRow { kind: string; at: string; detail: Record<string, unknown> }
+
+export interface ConsignmentDetail extends Consignment { history: HistoryRow[] }
+
 /** Why a truck can't take a run on a day: grounded, or rego or service overdue. */
 export interface Blocked { code: "vehicle_grounded" | "vehicle_overdue" | string; message: string }
 
@@ -175,9 +184,41 @@ export interface TruckAt {
 /** Where the map tiles come from (GET /v1/map); null: no map, a list only. */
 export interface MapTiles { tile_url: string | null; attribution: string | null }
 
+/** An outside carrier (GET /v1/carriers). */
+export interface Carrier {
+  id: number;
+  name: string;
+  account: string | null;
+  /** when it usually calls, by the depot's clock: "15:30" */
+  pickup_time: string | null;
+  adapter: string;
+  active: boolean;
+  created_at: string;
+}
+
+export interface ManifestLine { consignment: Card; con_note: string | null; added_at: string }
+
+/** One carrier's pickup on the carrier bay (GET /v1/manifests). */
+export interface Manifest {
+  id: number;
+  manifest_no: string;
+  date: string;
+  status: "open" | "collected";
+  carrier: Carrier;
+  created_by: string;
+  created_at: string;
+  collected_at: string | null;
+  collected_by: string | null;
+  lines: ManifestLine[];
+  totals: { consignments: number; packages: number; cartons: number; weight_kg: string };
+  needs_con_note: number;
+  print_job: RunSheet | null;
+  shipping: { pending: number; done: number; failed: number };
+}
+
 /** A line of what just happened (GET /v1/feed). */
 export interface FeedItem {
-  kind: "packed" | "delivered" | "failed" | "left" | "finished" | "prestart" | "fixed";
+  kind: "packed" | "delivered" | "failed" | "left" | "finished" | "prestart" | "fixed" | "con_note" | "collected";
   at: string;
   run_id: number | null;
   run_no: string | null;
@@ -191,4 +232,9 @@ export interface FeedItem {
   failed: string[];
   by: string | null;
   note: string | null;
+  /** con_note and collected: the carrier, the con note, the manifest and how many went */
+  carrier?: string | null;
+  con_note?: string | null;
+  manifest_no?: string | null;
+  count?: number | null;
 }
```

Create `apps/desktop/src/lib/carriers.ts`:
```ts
/** The carrier bay's own logic, kept out of the components so it can be
 * tested on its own: con notes, what needs one, and each carrier's line. */
import type { Card, Consignment, Manifest } from "../api/types";
import { clock } from "./days";
import { plain, toMicro } from "./decimal";

/** As the API reads it: spaces around it off, letters made capitals. */
export function normalConNote(raw: string): string {
  return raw.trim().toUpperCase();
}

const CON_NOTE = /^[A-Z0-9][A-Z0-9-]{3,39}$/;

/** Why the API would refuse this con note, or null. The API checks too. */
export function conNoteProblem(note: string): string | null {
  return CON_NOTE.test(note) ? null : "A con note is 4 to 40 letters, digits and dashes, as on the carrier's label.";
}

export interface Needing { manifest: Manifest; consignment: Card }

export interface BayStats {
  needs: number;
  firstNeeding: Needing | null;
  /** on the carrier bay, and how many carriers they wait for */
  waiting: number;
  carriers: number;
  collected: number;
  lastCollected: Manifest | null;
}

const open = (ms: Manifest[]) => ms.filter((m) => m.status === "open");

/** The three numbers at the top of Carriers.html. */
export function bayStats(manifests: Manifest[]): BayStats {
  const waiting = open(manifests);
  const went = manifests.filter((m) => m.status === "collected");
  return {
    needs: waiting.reduce((n, m) => n + m.needs_con_note, 0),
    firstNeeding: nextNeedingConNote(manifests),
    waiting: waiting.reduce((n, m) => n + m.lines.length, 0),
    carriers: new Set(waiting.map((m) => m.carrier.id)).size,
    collected: went.length,
    lastCollected: went.reduce<Manifest | null>(
      (last, m) => (!last || (m.collected_at ?? "") > (last.collected_at ?? "") ? m : last), null),
  };
}

/** The first consignment on the bay with no con note, after the one just
 * done if given: where the con note box goes next. */
export function nextNeedingConNote(manifests: Manifest[], after?: number): Needing | null {
  const all = open(manifests).flatMap((m) => m.lines.filter((l) => !l.con_note).map((l) => ({ manifest: m, consignment: l.consignment })));
  if (after === undefined) return all[0] ?? null;
  return all.find((n) => n.consignment.id !== after) ?? null;
}

/** "15:30" as the depot's clock says it: "3:30 pm". */
export function pickupClock(hhmm: string): string {
  const [h, m] = hhmm.split(":").map(Number);
  return clock(new Date(2000, 0, 1, h, m));
}

function count(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`;
}

/** Under the carrier's name: when it calls and what it takes, or once it
 * has been, when, its manifest and whether Simple WMS has heard. */
export function pickupLine(m: Manifest): string {
  if (m.status === "collected") {
    const parts = [`Collected ${clock(new Date(m.collected_at!))}`,
                   `manifest ${m.manifest_no}${m.print_job?.status === "printed" ? " printed by Platen" : ""}`];
    const s = m.shipping;
    if (s.failed) parts.push(`${s.failed} not marked shipped in Simple WMS`);
    else if (s.pending) parts.push("telling Simple WMS");
    else parts.push("marked shipped in Simple WMS");
    return parts.join(" · ");
  }
  const parts = m.carrier.pickup_time ? [`Pickup about ${pickupClock(m.carrier.pickup_time)}`] : [];
  parts.push(count(m.totals.consignments, "consignment"), count(m.totals.cartons, "carton"),
             `${plain(toMicro(m.totals.weight_kg))} kg`);
  return parts.join(" · ");
}

export type Tone = "amber" | "red" | "blue" | "green" | "grey";

/** A consignment's status pill in the Consignments drawer. */
export function whereNow(c: Consignment): { text: string; tone: Tone } {
  switch (c.status) {
    case "packed": return { text: "To plan", tone: "amber" };
    case "failed": return { text: "Not delivered", tone: "red" };
    case "assigned": return { text: c.run ? `On run ${c.run.run_no}` : "On a run", tone: "blue" };
    case "departed": return { text: c.run ? `On run ${c.run.run_no}` : "On a run", tone: "green" };
    case "delivered": return { text: "Delivered", tone: "green" };
    case "booked": return c.carrier?.con_note ? { text: "With carrier", tone: "grey" } : { text: "Needs con note", tone: "amber" };
    case "collected": return { text: "Collected", tone: "grey" };
    default: return { text: c.status, tone: "grey" };
  }
}
```

Change `apps/desktop/src/lib/live.ts`:
```diff
--- a/apps/desktop/src/lib/live.ts
+++ b/apps/desktop/src/lib/live.ts
@@ -137,6 +137,10 @@ export function feedLine(i: FeedItem): { text: string; tone: FeedTone } {
     case "left": return { text: `${who} left on ${i.run_no} in ${i.rego}`, tone: "blue" };
     case "finished": return { text: `${who} finished ${i.run_no}`, tone: "blue" };
     case "fixed": return { text: `${i.rego} marked fixed by ${i.by}`, tone: "green" };
+    case "con_note": return { text: `Con note ${i.con_note} for ${i.customer} · ${i.carrier}`, tone: "amber" };
+    case "collected":
+      return { text: `${i.carrier} collected ${i.count} ${i.count === 1 ? "consignment" : "consignments"} · ${i.manifest_no}`,
+               tone: "green" };
     case "prestart":
       if (i.result === "grounded") return { text: `${i.rego} grounded · ${and(i.failed)} failed`, tone: "red" };
       if (i.result === "failed") return { text: `${who}'s pre-start on ${i.rego}: ${and(i.failed)} failed`, tone: "amber" };
```

**Step 4: Run them**

Run: `npx vitest run src/test/carriers.test.ts && npm run typecheck`
Expected: `6 passed`, and no type errors. `npm test`: `97 passed`.

**Step 5: Commit**

```bash
git add apps/desktop/src/lib/carriers.ts apps/desktop/src/api/types.ts apps/desktop/src/lib/live.ts \
  apps/desktop/src/test/bay.ts apps/desktop/src/test/carriers.test.ts
git commit -m "Read con notes, the carrier bay's numbers and where a consignment is, as the screens say them"
```

### Task 10: The Carriers drawer

**Files:**
- Create: `apps/desktop/src/pages/Carriers.tsx`
- Modify: `apps/desktop/src/App.tsx`, `apps/desktop/src/ui/icons.tsx` (the design's scan icon), `apps/desktop/src/index.css` (the design's `.connote`, `.grp`, `.ghead`, `.mono`, and the amber count on the bar)
- Test: `apps/desktop/src/test/Carriers.test.tsx` (new)

**Step 1: Write the failing tests**

Create `apps/desktop/src/test/Carriers.test.tsx`:
```tsx
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { Manifest } from "../api/types";
import { isoDay } from "../lib/days";
import { line, manifest, PLAINS, RIDGELINE, VALLEY } from "./bay";
import { json, path, renderApp, stubApi, type Call } from "./harness";

const today = isoDay(new Date());
const RX = manifest(1, RIDGELINE, [line(26, "Mill Lane Florist", null), line(33, "Hilltop Joinery", "RX-77310042", 5, "72")],
                    { date: today });
const VF = manifest(2, VALLEY, [line(24, "Paddock Produce", "VF-00418821", 4, "88"), line(28, "Old Mill Books", "VF-00418822", 5, "54")],
                    { date: today, carrier: { id: 2, name: VALLEY, account: null, pickup_time: "14:00", adapter: "manual", active: true, created_at: "" } });
const PP = manifest(3, PLAINS, [line(20, "Corner Hardware", "PP-1001")],
                    { date: today, status: "collected", collected_at: new Date(new Date().setHours(10, 15, 0, 0)).toISOString(),
                      collected_by: "dee", print_job: { job_id: "j", status: "printed", printer: "office", last_error: null, created_at: "", printed_at: "" },
                      shipping: { pending: 0, done: 1, failed: 0 } });

function bay(manifests: Manifest[] = [RX, VF, PP], answers: Record<string, (call: Call) => Response> = {}) {
  return stubApi((method, url, body) => {
    const key = `${method} ${path(url)}`;
    if (answers[key]) return answers[key]({ method, url, body });
    if (key === "GET /v1/manifests") return json(200, { items: manifests, total: manifests.length });
    if (key === "GET /v1/carriers") return json(200, { items: [RX.carrier, VF.carrier], total: 2 });
    if (method === "POST") return json(200, RX);
    return null;
  });
}

const group = (name: RegExp) => screen.findByRole("region", { name });

describe("Carriers", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("draws what needs a con note, what waits for pickup and what went today", async () => {
    const { calls } = bay();
    renderApp("/carriers");
    expect(await screen.findByRole("dialog", { name: "Carriers" })).toBeInTheDocument();
    const needs = (await screen.findByText("Needs a con note")).parentElement!;
    expect(needs).toHaveTextContent("1");
    expect(needs).toHaveTextContent("0080034526 · Mill Lane Florist");
    expect((screen.getByText("Waiting for pickup")).parentElement).toHaveTextContent("4on the carrier bay · 2 carriers");
    expect((screen.getByText("Collected today")).parentElement).toHaveTextContent(`1${PLAINS} · 10:15 am`);
    expect(calls.find((c) => path(c.url) === "/v1/manifests")!.url).toContain(`date=${today}`);

    const rx = await group(/Ridgeline Express/);
    expect(within(rx).getByText("Pickup about 3:30 pm · 2 consignments · 7 cartons · 96 kg")).toBeInTheDocument();
    expect(within(rx).getByText("1 needs a con note")).toBeInTheDocument();
    expect(within(rx).getByRole("button", { name: /Print manifest/ })).toBeDisabled();
    expect(within(rx).getByText("Add the missing con note to print this manifest.")).toBeInTheDocument();
    const rows = within(rx).getAllByRole("row");
    expect(rows[1]).toHaveTextContent("0080034526Mill Lane FloristDaylesford224Not added yetNeeds con note");
    expect(rows[2]).toHaveTextContent("RX-77310042On the bay");

    const vf = await group(/Valley Freight/);
    expect(within(vf).getByText("Ready")).toBeInTheDocument();
    expect(within(vf).getByRole("button", { name: /Print manifest/ })).toBeEnabled();

    const pp = await group(/Plains Parcels/);
    expect(within(pp).getByText("Collected 10:15 am · manifest M-0003 printed by Platen · marked shipped in Simple WMS")).toBeInTheDocument();
    expect(within(pp).getByRole("button", { name: /Reprint/ })).toBeInTheDocument();
    expect(within(pp).queryByRole("table")).toBeNull();
  });

  it("takes a scanned con note for the first that needs one, then goes on to the next", async () => {
    const more = manifest(4, VALLEY, [line(40, "Two Rivers Vet", null)], { date: today });
    const { calls } = bay([RX, more]);
    renderApp("/carriers");
    const box = await screen.findByRole("form", { name: "Add a con note" });
    expect(within(box).getByText("Con note for 0080034526 · Mill Lane Florist")).toBeInTheDocument();
    expect(within(box).getByRole("combobox", { name: "Carrier" })).toHaveValue("1");
    const user = userEvent.setup();
    // a desk scanner types the label and presses Enter
    await user.type(within(box).getByRole("textbox", { name: "Con note number" }), " rx-77310049{Enter}");
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    const sent = calls.find((c) => c.method === "POST")!;
    expect(sent.url).toBe("/v1/manifests/1/con-note");
    expect(sent.body).toEqual({ consignment_id: 26, con_note: "RX-77310049" });
    expect(await screen.findByText("Con note RX-77310049 saved for Mill Lane Florist.")).toBeInTheDocument();
    expect(within(box).getByText("Con note for 0080034540 · Two Rivers Vet")).toBeInTheDocument();
    expect(within(box).getByRole("textbox", { name: "Con note number" })).toHaveValue("");
    expect(within(box).getByRole("textbox", { name: "Con note number" })).toHaveFocus();
  });

  it("books it with another carrier when another is chosen", async () => {
    const { calls } = bay();
    renderApp("/carriers");
    const box = await screen.findByRole("form", { name: "Add a con note" });
    const user = userEvent.setup();
    await user.selectOptions(within(box).getByRole("combobox", { name: "Carrier" }), "2");
    await user.type(within(box).getByRole("textbox", { name: "Con note number" }), "VF-00418830");
    await user.click(within(box).getByRole("button", { name: "Save con note" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    const sent = calls.find((c) => c.method === "POST")!;
    expect(sent.url).toBe("/v1/carriers/2/book");
    expect(sent.body).toEqual({ consignment_id: 26, date: today, con_note: "VF-00418830" });
  });

  it("says what is wrong with a con note before sending it, and corrects one from its row", async () => {
    const { calls } = bay();
    renderApp("/carriers");
    const box = await screen.findByRole("form", { name: "Add a con note" });
    const user = userEvent.setup();
    await user.type(within(box).getByRole("textbox", { name: "Con note number" }), "RX 7731{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("A con note is 4 to 40 letters, digits and dashes, as on the carrier's label.");
    expect(calls.some((c) => c.method === "POST")).toBe(false);
    await user.click(screen.getByRole("button", { name: "Con note for Hilltop Joinery" }));
    expect(within(box).getByText("Con note for 0080034533 · Hilltop Joinery")).toBeInTheDocument();
    expect(within(box).getByRole("textbox", { name: "Con note number" })).toHaveValue("RX-77310042");
  });

  it("prints a manifest, marks it collected and puts one back in the tray", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    const { calls } = bay();
    renderApp("/carriers");
    const vf = await group(/Valley Freight/);
    const user = userEvent.setup();
    await user.click(within(vf).getByRole("button", { name: /Print manifest/ }));
    expect(await screen.findByText("M-0002 sent to Platen.")).toBeInTheDocument();
    await user.click(within(vf).getByRole("button", { name: /Mark collected/ }));
    expect(confirm).toHaveBeenCalledWith(`${VALLEY} has collected 2 consignments? Each is marked shipped in Simple WMS with its con note.`);
    const collect = calls.find((c) => path(c.url) === "/v1/manifests/2/collect")!;
    expect(collect.body).toEqual({ message_id: expect.stringMatching(/^[0-9a-f-]{36}$/), consignment_ids: [24, 28] });
    expect(await screen.findByText(`${VALLEY} has collected M-0002. Simple WMS is being told.`)).toBeInTheDocument();
    await user.click(within(vf).getByRole("button", { name: "Put Old Mill Books back in the tray" }));
    await waitFor(() => expect(calls.some((c) => path(c.url) === "/v1/manifests/2/unbook")).toBe(true));
    expect(calls.find((c) => path(c.url) === "/v1/manifests/2/unbook")!.body).toEqual({ consignment_id: 28 });
    confirm.mockRestore();
  });

  it("says plainly when nothing waits, and when there are no carriers yet", async () => {
    stubApi((_m, url) => json(200, { items: [], total: 0 }));
    renderApp("/carriers");
    expect(await screen.findByText(/Nothing is waiting for a carrier/)).toBeInTheDocument();
    expect(await screen.findByText(/No carriers yet\. A fleet manager adds them/)).toBeInTheDocument();
    expect(screen.queryByRole("form", { name: "Add a con note" })).toBeNull();
  });
});
```

**Step 2: Run them to see them fail**

Run: `npx vitest run src/test/Carriers.test.tsx`
Expected: `6 failed`: the drawer is still the placeholder ("This screen arrives with build step 6…"), so there is no "Needs a con note", no form and no groups.

**Step 3: Implement**

Create `apps/desktop/src/pages/Carriers.tsx`. The con note box needs no scanner code: a desk scanner types the number and presses Enter, which submits the form; after each save the box moves to the next consignment without a con note and keeps the focus, so a stack of labels is scanned one after another:
```tsx
import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { useOutletContext } from "react-router-dom";
import { api, ApiError, Unreachable } from "../api/client";
import type { Carrier, Manifest, Page } from "../api/types";
import { bayStats, conNoteProblem, nextNeedingConNote, normalConNote, pickupLine, type Needing } from "../lib/carriers";
import { clock, dayLabel, fromIso, isoDay } from "../lib/days";
import { plain, toMicro } from "../lib/decimal";
import { Drawer } from "../ui/Drawer";
import { Icon } from "../ui/icons";

type Note = { tone: "r" | "a" | "g"; text: string } | null;

function said(e: unknown): string {
  if (e instanceof ApiError && e.code === "stale") return "Someone else changed this manifest. Here it is as it is now: check it and try again.";
  if (e instanceof Unreachable) return e.message;
  return e instanceof ApiError ? e.message : "Simple TMS could not be reached. Check the connection and try again.";
}

function printLine(m: Manifest): Note {
  const p = m.print_job;
  if (!p) return null;
  if (p.status === "printed") return { tone: "g", text: `Manifest printed on ${p.printer}.` };
  if (p.status === "failed") return { tone: "r", text: `Manifest didn't print: ${p.last_error ?? "Platen said it failed"}` };
  if (p.status === "accepted") return { tone: "g", text: `Manifest sent to ${p.printer}.` };
  return { tone: "a", text: p.last_error ? `Manifest waiting to go to Platen: ${p.last_error}` : "Manifest waiting to go to Platen." };
}

/** Carriers.html: freight going with outside carriers, a group per carrier's
 * manifest, the con note box (type it, or scan the label with a desk
 * scanner: it types the number and presses Enter), Print manifest, and
 * Mark collected once the carrier has been. */
export function Carriers() {
  const { reloadCounts } = useOutletContext<{ reloadCounts: () => void }>();
  const today = isoDay(new Date());
  const [manifests, setManifests] = useState<Manifest[] | null>(null);
  const [carriers, setCarriers] = useState<Carrier[]>([]);
  const [picked, setPicked] = useState<number | null>(null);
  const [carrierId, setCarrierId] = useState("");
  const [conNote, setConNote] = useState("");
  const [note, setNote] = useState<Note>(null);
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    const [m, c] = await Promise.all([
      api.get<Page<Manifest>>("/v1/manifests", { date: today, left_open: true }),
      api.get<Page<Carrier>>("/v1/carriers", { active: true }).catch(() => ({ items: [] as Carrier[], total: 0 })),
    ]);
    setManifests(m.items);
    setCarriers(c.items);
    return m.items;
  }, [today]);

  useEffect(() => {
    load().catch((e) => setNote({ tone: "r", text: said(e) }));
    // what another desk does turns up within 20 s
    const t = setInterval(() => { load().catch(() => undefined); }, 20_000);
    return () => clearInterval(t);
  }, [load]);

  const all = manifests ?? [];
  const open = all.filter((m) => m.status === "open");
  // the consignment the box is for: the one picked, else the first without a con note
  const chosen: Needing | null = (() => {
    for (const m of open) {
      const l = m.lines.find((x) => x.consignment.id === picked);
      if (l) return { manifest: m, consignment: l.consignment };
    }
    return nextNeedingConNote(all);
  })();
  const chosenId = chosen?.consignment.id ?? null;
  const chosenLine = chosen?.manifest.lines.find((l) => l.consignment.id === chosenId);

  // the box starts from the consignment's carrier and its con note, if any,
  // each time it moves to another one (not on each poll, over what is typed)
  const chosenKey = chosen ? `${chosen.manifest.id}:${chosenId}` : "";
  useEffect(() => {
    setCarrierId(chosen ? String(chosen.manifest.carrier.id) : "");
    setConNote(chosenLine?.con_note ?? "");
  }, [chosenKey]);

  async function act(fn: () => Promise<unknown>, done: string) {
    setBusy(true);
    try {
      await fn();
      setNote({ tone: "g", text: done });
    } catch (e) {
      setNote({ tone: "r", text: said(e) });
    } finally {
      await load().catch(() => undefined);
      reloadCounts();
      setBusy(false);
    }
  }

  async function save(e: FormEvent) {
    e.preventDefault();
    if (!chosen) return;
    const value = normalConNote(conNote);
    const problem = conNoteProblem(value);
    if (problem) { setNote({ tone: "r", text: problem }); return; }
    const { manifest: m, consignment: c } = chosen;
    setBusy(true);
    try {
      if (String(m.carrier.id) === carrierId) {
        await api.post(`/v1/manifests/${m.id}/con-note`, { consignment_id: c.id, con_note: value });
      } else {
        await api.post(`/v1/carriers/${carrierId}/book`, { consignment_id: c.id, date: m.date, con_note: value });
      }
      setNote({ tone: "g", text: `Con note ${value} saved for ${c.customer}.` });
      const fresh = await load().catch(() => all);
      // on to the next one without a con note, ready for the scanner
      setPicked(nextNeedingConNote(fresh, c.id)?.consignment.id ?? null);
      setConNote("");
      reloadCounts();
      setTimeout(() => input.current?.focus(), 0);
    } catch (err) {
      setNote({ tone: "r", text: said(err) });
    } finally {
      setBusy(false);
    }
  }

  function collected(m: Manifest) {
    const n = m.lines.length;
    if (!window.confirm(`${m.carrier.name} has collected ${n} ${n === 1 ? "consignment" : "consignments"}? Each is marked shipped in Simple WMS with its con note.`)) return;
    void act(() => api.post(`/v1/manifests/${m.id}/collect`, {
      message_id: crypto.randomUUID(), consignment_ids: m.lines.map((l) => l.consignment.id),
    }), `${m.carrier.name} has collected ${m.manifest_no}. Simple WMS is being told.`);
  }

  const stats = bayStats(all);
  const needs = stats.firstNeeding;
  const went = stats.lastCollected;
  return (
    <Drawer label="Carriers" head={
      <div style={{ flex: 1, minWidth: 200 }}>
        <div className="eyebrow" style={{ color: "var(--color-green)" }}>Today · {dayLabel(new Date())}</div>
        <h1>Carriers</h1>
        <div className="sub">Freight going with outside carriers. Choose the carrier, type or scan the con note, print the manifest.</div>
      </div>
    }>
      <div className="stats">
        <div className={`stat${stats.needs ? " amber" : ""}`}><div className="eyebrow">Needs a con note</div><div className="v num">{stats.needs}</div>
          <div className="d">{needs ? `${needs.consignment.wms_ref} · ${needs.consignment.customer}` : "Every con note is in"}</div></div>
        <div className="stat"><div className="eyebrow">Waiting for pickup</div><div className="v num">{stats.waiting}</div>
          <div className="d">on the carrier bay · {stats.carriers} {stats.carriers === 1 ? "carrier" : "carriers"}</div></div>
        <div className="stat"><div className="eyebrow">Collected today</div><div className="v num">{stats.collected}</div>
          <div className="d">{went ? `${went.carrier.name} · ${clock(new Date(went.collected_at!))}` : "Nothing yet"}</div></div>
      </div>

      {note && <div className={`msg ${note.tone}`} role={note.tone === "r" ? "alert" : "status"}>{note.text}</div>}

      {chosen && (
        <form className="connote" aria-label="Add a con note" onSubmit={(e) => void save(e)}>
          <div style={{ display: "flex", alignItems: "center", gap: 12, flex: "1 1 100%" }}>
            <span style={{ color: "var(--color-green)" }}><Icon name="scan" size={28} /></span>
            <div><div style={{ fontSize: 16, fontWeight: 800 }}>Con note for {chosen.consignment.wms_ref} · {chosen.consignment.customer}</div>
              <div className="small">Scan the carrier's label with the desk scanner, or type the number.</div></div>
          </div>
          <label style={{ flex: "1 1 200px" }}>Carrier
            <select className="field" value={carrierId} onChange={(e) => setCarrierId(e.target.value)} disabled={busy}>
              {!carriers.some((c) => c.id === chosen.manifest.carrier.id) && (
                <option value={chosen.manifest.carrier.id}>{chosen.manifest.carrier.name}</option>)}
              {carriers.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select></label>
          <label style={{ flex: "2 1 240px" }}>Con note number
            <input ref={input} className="field mono" type="text" autoComplete="off" spellCheck={false} autoFocus
                   value={conNote} onChange={(e) => setConNote(e.target.value)} maxLength={64} /></label>
          <button type="submit" className="btn primary" disabled={busy || !conNote.trim()}>Save con note</button>
        </form>
      )}

      {manifests && all.length === 0 && (
        <div className="small">Nothing is waiting for a carrier. Send a consignment with a carrier from Plan runs.</div>)}
      {manifests && carriers.length === 0 && (
        <div className="msg a" role="status">No carriers yet. A fleet manager adds them through the API (POST /v1/carriers; see docs/api.md).</div>)}

      {all.map((m) => {
        const isOpen = m.status === "open";
        const ready = isOpen && m.needs_con_note === 0;
        const earlier = isOpen && m.date < today;
        const printed = printLine(m);
        const why = `why-${m.id}`;
        return (
          <section key={m.id} className={`grp${isOpen ? "" : " done"}`} aria-label={`${m.carrier.name} · ${m.manifest_no}`}>
            <div className="ghead">
              <span className="gicon"><Icon name="truck" size={20} /></span>
              <div style={{ flex: 1, minWidth: 200 }}><div className="t">{m.carrier.name}</div>
                <div className="s">{earlier ? `Waiting since ${dayLabel(fromIso(m.date))} · ` : ""}{pickupLine(m)}</div></div>
              {!isOpen ? <span className="pill p-green"><Icon name="tick" size={13} width={3} />Collected</span>
                : ready ? <span className="pill p-green">Ready</span>
                : <span className="pill p-amber">{m.needs_con_note} {m.needs_con_note === 1 ? "needs" : "need"} a con note</span>}
              <button type="button" className={`btn${ready && !m.print_job ? " primary" : ""}`} disabled={busy || (isOpen && !ready)}
                      aria-describedby={isOpen && !ready ? why : undefined}
                      onClick={() => void act(() => api.post(`/v1/manifests/${m.id}/print`), `${m.manifest_no} sent to Platen.`)}>
                <Icon name="print" size={16} />{isOpen ? "Print manifest" : "Reprint"}</button>
              {isOpen && (
                <button type="button" className={`btn${ready && m.print_job ? " primary" : ""}`} disabled={busy || !ready}
                        onClick={() => collected(m)}><Icon name="tick" size={16} width={2.6} />Mark collected</button>)}
              {isOpen && !ready && (
                <span id={why} className="small" style={{ flexBasis: "100%", textAlign: "right" }}>
                  Add the missing con {m.needs_con_note === 1 ? "note" : "notes"} to print this manifest.</span>)}
            </div>
            {printed && <div className={`msg ${printed.tone}`} style={{ margin: "0 16px 10px" }}>{printed.text}</div>}
            {m.shipping.failed > 0 && (
              <div className="msg r" style={{ margin: "0 16px 10px" }}><span>{m.shipping.failed} not marked shipped in Simple WMS after five tries.</span>
                <button type="button" className="btn sm" disabled={busy}
                        onClick={() => void act(() => api.post(`/v1/manifests/${m.id}/retry-ship`), "Trying Simple WMS again.")}>Try again</button></div>)}
            {isOpen && (
              <div style={{ overflowX: "auto" }}><table style={{ minWidth: 700, tableLayout: "fixed" }}>
                <colgroup><col style={{ width: "16%" }} /><col style={{ width: "26%" }} /><col style={{ width: "7%" }} />
                  <col style={{ width: "7%" }} /><col style={{ width: "18%" }} /><col style={{ width: "14%" }} /><col style={{ width: "12%" }} /></colgroup>
                <thead><tr><th>CONSIGNMENT</th><th>CUSTOMER</th><th>CTN</th><th>KG</th><th>CON NOTE</th><th>STATUS</th><th><span className="sr">Back to the tray</span></th></tr></thead>
                <tbody>
                  {m.lines.map((l) => {
                    const c = l.consignment;
                    return (
                      <tr key={c.id} className={`${l.con_note ? "" : "wait"}${c.id === chosenId ? " sel" : ""}`}>
                        <td><button type="button" className="rowbtn" aria-pressed={c.id === chosenId} aria-label={`Con note for ${c.customer}`}
                                    onClick={() => { setPicked(c.id); setTimeout(() => input.current?.focus(), 0); }}>{c.wms_ref}</button></td>
                        <td style={{ whiteSpace: "nowrap" }}><b>{c.customer}</b><div className="small">{c.suburb}</div></td>
                        <td className="num">{c.cartons}</td>
                        <td className="num">{plain(toMicro(c.weight_kg))}</td>
                        <td>{l.con_note ? <span className="mono">{l.con_note}</span>
                          : <span className="small" style={{ color: "var(--color-amber-ink)", fontWeight: 700 }}>Not added yet</span>}</td>
                        <td>{l.con_note ? <span className="pill p-grey">On the bay</span> : <span className="pill p-amber">Needs con note</span>}</td>
                        <td><button type="button" className="btn sm" disabled={busy} aria-label={`Put ${c.customer} back in the tray`}
                                    onClick={() => void act(() => api.post(`/v1/manifests/${m.id}/unbook`, { consignment_id: c.id }),
                                                            `${c.customer} is back in the tray.`)}>Tray</button></td>
                      </tr>
                    );
                  })}
                </tbody>
              </table></div>
            )}
          </section>
        );
      })}

      <div className="small">Manifests print on the dispatch printer through Platen. Carrier bookings are by hand in this version: no carrier connections yet.</div>
    </Drawer>
  );
}
```

Change `apps/desktop/src/App.tsx`:
```diff
--- a/apps/desktop/src/App.tsx
+++ b/apps/desktop/src/App.tsx
@@ -1,6 +1,7 @@
 import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
 import { AuthProvider } from "./auth/AuthContext";
 import { RequireAuth } from "./auth/RequireAuth";
+import { Carriers } from "./pages/Carriers";
 import { Fleet } from "./pages/Fleet";
 import { MapHome } from "./pages/MapHome";
 import { PlanRuns } from "./pages/PlanRuns";
@@ -20,9 +21,7 @@ export function AppRoutes() {
           <Route path="/consignments" element={
             <Later title="Consignments" step="a later build step"
                    what="Search by WMS delivery, customer or con note, and see each one's history." />} />
-          <Route path="/carriers" element={
-            <Later title="Carriers" step="build step 6"
-                   what="Today's carrier consignments, con note entry and the manifest." />} />
+          <Route path="/carriers" element={<Carriers />} />
           <Route path="/fleet" element={<Fleet />} />
           <Route path="/where" element={<WhereIsIt />} />
         </Route>
```

Change `apps/desktop/src/ui/icons.tsx`:
```diff
--- a/apps/desktop/src/ui/icons.tsx
+++ b/apps/desktop/src/ui/icons.tsx
@@ -37,6 +37,7 @@ const PATHS = {
   truck: <><path d="M2 6h12v10H2Z" /><path d="M14 9h4.5L22 12.5V16h-8Z" /><circle cx="6.5" cy="17.5" r="2" /><circle cx="17.5" cy="17.5" r="2" /></>,
   go: <path d="M5 12h14M13 6l6 6-6 6" />,
   tick: <path d="m5 12 5 5 9-10" />,
+  scan: <><path d="M3 7V5a2 2 0 0 1 2-2h2M17 3h2a2 2 0 0 1 2 2v2M21 17v2a2 2 0 0 1-2 2h-2M7 21H5a2 2 0 0 1-2-2v-2" /><path d="M8 7v10M12 7v10M16 7v10" /></>,
   target: <><circle cx="12" cy="12" r="7" /><circle cx="12" cy="12" r="2" /><path d="M12 2v3M12 19v3M2 12h3M19 12h3" /></>,
   wrench: <path d="M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18l3 3 6.3-6.3a4 4 0 0 0 5.4-5.4l-2.6 2.6-2.4-.6-.6-2.4Z" />,
 };
```

Change `apps/desktop/src/index.css`:
```diff
--- a/apps/desktop/src/index.css
+++ b/apps/desktop/src/index.css
@@ -190,6 +190,24 @@
   .due-a { color: var(--color-amber-ink); font-weight: 800; }
   .due-r { color: var(--color-red-ink); font-weight: 800; }
   .nav a i.r { background: var(--color-red); color: #fff; }
+  .nav a i.a { background: var(--color-amber); color: var(--color-ink); }
+
+  /* the Carriers drawer (Carriers.html) */
+  .mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 13px; }
+  .connote { padding: 16px; border-radius: 16px; border: 2px dashed var(--color-green); background: #fff; display: flex; flex-wrap: wrap; gap: 12px; align-items: flex-end; }
+  .connote label { display: flex; flex-direction: column; gap: 6px; font-size: 13px; font-weight: 700; }
+  .grp { border: 1px solid var(--color-card-line); border-radius: 16px; overflow: hidden; }
+  .grp.done { opacity: 0.95; }
+  .ghead { display: flex; flex-wrap: wrap; align-items: center; gap: 10px 14px; padding: 14px 16px; background: #F7FAF8; }
+  .ghead .t { font-size: 16px; font-weight: 800; }
+  .ghead .s { font-size: 13px; color: var(--color-ink-2); }
+  .gicon { width: 40px; height: 40px; border-radius: 12px; background: #fff; border: 1px solid var(--color-card-line); display: grid; place-items: center; color: var(--color-ink-2); flex: none; }
+  .grp table { width: 100%; border-collapse: collapse; font-size: 14px; }
+  .grp th { padding: 12px 10px; font-weight: 800; text-align: left; color: var(--color-muted); font-size: 12px; letter-spacing: 1px; white-space: nowrap; }
+  .grp td { padding: 6px 10px; border-top: 1px solid var(--color-row); height: 52px; }
+  .grp tr.wait td { background: #FFFBEF; }
+  .grp tr.sel td { background: #F1F8F4; }
+  .grp tr.sel .rowbtn { border-color: var(--color-green); background: var(--color-tint); }
 }
 
 @media (max-width: 960px) {
```

**Step 4: Run them**

Run: `npx vitest run src/test/Carriers.test.tsx && npm run typecheck`
Expected: `6 passed`. `npm test`: `103 passed`.

**Step 5: Commit**

```bash
git add apps/desktop/src/pages/Carriers.tsx apps/desktop/src/App.tsx apps/desktop/src/ui/icons.tsx \
  apps/desktop/src/index.css apps/desktop/src/test/Carriers.test.tsx
git commit -m "Build the Carriers drawer: con notes typed or scanned, manifests printed, carriers collecting"
```

### Task 11: Plan runs: With a carrier…

**Files:**
- Modify: `apps/desktop/src/pages/PlanRuns.tsx`
- Test: `apps/desktop/src/test/PlanRuns.test.tsx`

**Step 1: Write the failing tests**

Change `apps/desktop/src/test/PlanRuns.test.tsx`:
```diff
--- a/apps/desktop/src/test/PlanRuns.test.tsx
+++ b/apps/desktop/src/test/PlanRuns.test.tsx
@@ -90,6 +90,29 @@ describe("Plan runs", () => {
       .toEqual({ consignment_id: 31 }));
   });
 
+  it("sends a consignment with a carrier from the tray, and a stop off its run", async () => {
+    const carriers = { items: [{ id: 2, name: "Valley Freight (sample)", account: null, pickup_time: null, adapter: "manual",
+                                 active: true, created_at: "" }], total: 1 };
+    const { calls } = board({ "GET /v1/carriers": () => json(200, carriers) });
+    renderApp("/plan");
+    const user = userEvent.setup();
+    await user.selectOptions(await screen.findByRole("combobox", { name: "Send Paddock Produce with a carrier" }), "2");
+    await waitFor(() => expect(calls.find((c) => c.url === "/v1/carriers/2/book")?.body)
+      .toEqual({ consignment_id: 31, date: isoDay(new Date(Date.now() + 86_400_000)) }));
+    expect(await screen.findByText("Paddock Produce is on the carrier bay for Valley Freight (sample). Add its con note in Carriers."))
+      .toBeInTheDocument();
+    await user.selectOptions(screen.getByRole("combobox", { name: "Move Two Rivers Vet to" }), "carrier:2");
+    await waitFor(() => expect(calls.filter((c) => c.url === "/v1/carriers/2/book").at(-1)?.body)
+      .toEqual({ consignment_id: 12, date: isoDay(new Date(Date.now() + 86_400_000)) }));
+  });
+
+  it("offers no carrier when there is none", async () => {
+    board();
+    renderApp("/plan");
+    await screen.findByRole("combobox", { name: "Put Paddock Produce on a run" });
+    expect(screen.queryByRole("combobox", { name: "Send Paddock Produce with a carrier" })).toBeNull();
+  });
+
   it("moves a stop down from the keyboard by sending the whole order", async () => {
     const { calls } = board();
     renderApp("/plan");
```

**Step 2: Run them to see them fail**

Run: `npx vitest run src/test/PlanRuns.test.tsx`
Expected: `1 failed, 19 passed`: no "Send Paddock Produce with a carrier" menu. ("offers no carrier when there is none" passes already.)

**Step 3: Implement**

Change `apps/desktop/src/pages/PlanRuns.tsx`. The carriers are asked for after the board, and a failure leaves the menus out rather than the board empty:
```diff
--- a/apps/desktop/src/pages/PlanRuns.tsx
+++ b/apps/desktop/src/pages/PlanRuns.tsx
@@ -1,7 +1,7 @@
 import { useCallback, useEffect, useMemo, useRef, useState, type DragEvent, type FormEvent } from "react";
 import { Link, useOutletContext } from "react-router-dom";
 import { api, ApiError, Unreachable } from "../api/client";
-import type { Card, Consignment, Driver, Page, Run, Vehicle } from "../api/types";
+import type { Card, Carrier, Consignment, Driver, Page, Run, Vehicle } from "../api/types";
 import { useAuth } from "../auth/AuthContext";
 import { clock, dayOptions } from "../lib/days";
 import { leftOpen, stillOpenFrom } from "../lib/live";
@@ -109,6 +109,7 @@ export function PlanRuns() {
   const [tray, setTray] = useState<Card[]>([]);
   const [vehicles, setVehicles] = useState<Vehicle[]>([]);
   const [drivers, setDrivers] = useState<Driver[]>([]);
+  const [carriers, setCarriers] = useState<Carrier[]>([]);
   const [filter, setFilter] = useState("");
   const [note, setNote] = useState<Note>(null);
   const [savedAt, setSavedAt] = useState<Date | null>(null);
@@ -163,6 +164,9 @@ export function PlanRuns() {
     setTray(t.items.map(cardOf));
     setVehicles(v.items);
     setDrivers(d.items);
+    // the carriers a consignment can go with instead; the board doesn't wait on them
+    const c = await api.get<Page<Carrier>>("/v1/carriers", { active: true }).catch(() => null);
+    if (c && mine === latestLoad.current) setCarriers(c.items);
   }, [day, days]);
 
   useEffect(() => {
@@ -193,6 +197,12 @@ export function PlanRuns() {
     act(() => api.post(`/v1/runs/${runId}/assign`, { consignment_id: c.id, position }));
   const unassign = (runId: number, c: Card) =>
     act(() => api.post(`/v1/runs/${runId}/unassign`, { consignment_id: c.id }));
+  /** Onto the carrier's manifest for the day being planned, off its run if it was on one. */
+  function book(carrierId: string, c: Card) {
+    const carrier = carriers.find((x) => String(x.id) === carrierId);
+    void act(() => api.post(`/v1/carriers/${carrierId}/book`, { consignment_id: c.id, date: day }),
+             `${c.customer} is on the carrier bay for ${carrier?.name ?? "the carrier"}. Add its con note in Carriers.`);
+  }
   // with the order as drawn, so the API can refuse it if someone else has moved a stop since
   const reorder = (run: Run, ids: number[]) =>
     act(() => api.post(`/v1/runs/${run.id}/reorder`,
@@ -374,6 +384,11 @@ export function PlanRuns() {
                               onChoose={(to) => { focusNext.current = [`[data-stop="${c.id}"]`]; void assign(Number(to), c); }}>
                   {planned.map((r) => <option key={r.id} value={r.id}>{r.run_no} · {r.driver.name}</option>)}
                 </ActionSelect>
+                {carriers.length > 0 && (
+                  <ActionSelect label={`Send ${c.customer} with a carrier`} placeholder="With a carrier…" disabled={busy}
+                                onChoose={(to) => book(to, c)}>
+                    {carriers.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
+                  </ActionSelect>)}
               </div>
             </div>
           ))}
@@ -448,6 +463,8 @@ export function PlanRuns() {
                                         if (to === "tray") {
                                           focusNext.current = [`[data-card="${s.consignment.id}"]`];
                                           void unassign(run.id, s.consignment);
+                                        } else if (to.startsWith("carrier:")) {
+                                          book(to.slice("carrier:".length), s.consignment);
                                         } else {
                                           focusNext.current = [`[data-stop="${s.consignment.id}"]`];
                                           void assign(Number(to), s.consignment);
@@ -455,6 +472,10 @@ export function PlanRuns() {
                                       }}>
                           <option value="tray">Back to the tray</option>
                           {others.map((r) => <option key={r.id} value={r.id}>{r.run_no} · {r.driver.name}</option>)}
+                          {carriers.length > 0 && (
+                            <optgroup label="With a carrier">
+                              {carriers.map((x) => <option key={x.id} value={`carrier:${x.id}`}>{x.name}</option>)}
+                            </optgroup>)}
                         </ActionSelect>
                       </span>
                     )}
@@ -508,7 +529,7 @@ export function PlanRuns() {
           </div>
         )}
       </div></div>
-      <div className="small">Print run sheets sends each run to the dispatch printer through Platen. When a run leaves, each consignment is marked shipped in Simple WMS.</div>
+      <div className="small">Print run sheets sends each run to the dispatch printer through Platen. When a run leaves, each consignment is marked shipped in Simple WMS. A consignment sent with a carrier waits on the carrier bay: see Carriers.</div>
     </Drawer>
   );
 }
```

**Step 4: Run them**

Run: `npx vitest run src/test/PlanRuns.test.tsx`
Expected: `20 passed`. `npm test`: `105 passed`.

**Step 5: Commit**

```bash
git add apps/desktop/src/pages/PlanRuns.tsx apps/desktop/src/test/PlanRuns.test.tsx
git commit -m "Send a consignment with a carrier from Plan runs, from the tray or off a run"
```

### Task 12: The Consignments drawer

**Files:**
- Create: `apps/desktop/src/lib/history.ts`, `apps/desktop/src/pages/Consignments.tsx`
- Modify: `apps/desktop/src/App.tsx`, `apps/desktop/src/index.css` (the design's tabs, timeline, proof of delivery and blue pill)
- Delete: `apps/desktop/src/pages/Later.tsx` (nothing uses it once both drawers are built)
- Test: `apps/desktop/src/test/history.test.ts` (new), `apps/desktop/src/test/Consignments.test.tsx` (new)

**Step 1: Write the failing tests**

Create `apps/desktop/src/test/history.test.ts`:
```ts
import { historyLine, proofOf } from "../lib/history";

const row = (kind: string, detail: Record<string, unknown>) => ({ kind, at: "2026-10-14T21:42:00Z", detail });

describe("a consignment's history, in words", () => {
  it("says each step as the Consignments drawer does", () => {
    expect(historyLine(row("packed", { packages: 4 }))).toEqual({ title: "Packed in Simple WMS", more: "4 packages", from: "wms" });
    expect(historyLine(row("assigned", { run: "R-0007", by: "dee" }))).toEqual({ title: "On run R-0007", more: "dee", from: "tms" });
    expect(historyLine(row("unassigned", { run: "R-0007", by: "dee" })).title).toBe("Off run R-0007");
    expect(historyLine(row("departed", { run: "R-0007", vehicle: "TMS041", driver: "kim", by: "dee" })))
      .toEqual({ title: "Departed", more: "TMS041 · kim", from: "tms" });
    expect(historyLine(row("delivered", { packages: 4, signed_name: "Priya", by: "kim" })))
      .toEqual({ title: "Delivered", more: "4 packages · signed by Priya", from: "tms" });
    expect(historyLine(row("failed", { reason: "wrong_address", by: "kim" })))
      .toEqual({ title: "Not delivered", more: "wrong address", from: "tms" });
    expect(historyLine(row("booked", { carrier: "Valley Freight (sample)", manifest: "M-0003", by: "dee" })))
      .toEqual({ title: "Booked with Valley Freight (sample)", more: "M-0003 · dee", from: "tms" });
    expect(historyLine(row("unbooked", { carrier: "Valley Freight (sample)", by: "dee" })).title)
      .toBe("Off Valley Freight (sample)'s manifest");
    expect(historyLine(row("con_note", { carrier: "Valley Freight (sample)", con_note: "VF-2", was: "VF-1", by: "dee" })))
      .toEqual({ title: "Con note VF-2", more: "Valley Freight (sample) · was VF-1 · dee", from: "tms" });
    expect(historyLine(row("collected", { carrier: "Valley Freight (sample)", con_note: "VF-2", manifest: "M-0003", by: "dee" })))
      .toEqual({ title: "Collected by Valley Freight (sample)", more: "con note VF-2 · M-0003 · dee", from: "tms" });
  });

  it("finds the proof of delivery in the history", () => {
    const h = [row("packed", {}), row("delivered", { signature_file: "s1", photo_file: "p1", signed_name: "Priya" })];
    expect(proofOf(h)).toEqual({ at: "2026-10-14T21:42:00Z", outcome: "delivered", signature: "s1", photo: "p1", signedName: "Priya", reason: null });
    expect(proofOf([row("packed", {})])).toBeNull();
  });
});
```

Create `apps/desktop/src/test/Consignments.test.tsx`:
```tsx
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { Consignment } from "../api/types";
import { json, path, renderApp, stubApi } from "./harness";

const con = (id: number, customer: string, status: string, over: Partial<Consignment> = {}): Consignment => ({
  id, wms_ref: `00800344${id}`, warehouse: "BAL-WH01", owner: "DEFAULT", customer, ship_to: { suburb: "Buninyong" },
  required_by: null, status, created_at: "", weight_kg: "84", volume_m3: "0.6",
  packages: Array.from({ length: 4 }, (_, i) => ({ package_no: i + 1, type: "carton", weight_kg: "21", length_cm: null,
                                                   width_cm: null, height_cm: null, sscc: null })),
  run: null, carrier: null, ...over,
});

const BAKERY = con(88, "Hillside Bakery", "delivered", { run: { id: 7, run_no: "R-0007", driver: "Kim", sequence: 5 } });
const DELI = con(19, "Station Deli", "packed");
const FLORIST = con(97, "Mill Lane Florist", "booked", { carrier: { id: 2, name: "Valley Freight (sample)", con_note: null,
                                                                     manifest_id: 3, manifest_no: "M-0003", collected_at: null } });
const COUNTS: Record<string, number> = { "": 48, "packed,failed": 14, "assigned,departed": 21, "booked,collected": 6,
                                         delivered: 17, failed: 1 };

function desk() {
  return stubApi((_m, url) => {
    const p = new URLSearchParams(url.split("?")[1]);
    if (path(url) === "/v1/consignments" && p.get("limit") === "0") {
      return json(200, { items: [], total: COUNTS[p.get("status") ?? ""] ?? 0 });
    }
    if (path(url) === "/v1/consignments") return json(200, { items: [BAKERY, DELI, FLORIST], total: 3 });
    if (path(url) === "/v1/consignments/88") {
      return json(200, { ...BAKERY, history: [
        { kind: "packed", at: "2026-10-13T20:42:00Z", detail: { packages: 4 } },
        { kind: "assigned", at: "2026-10-13T21:05:00Z", detail: { run: "R-0007", by: "jo" } },
        { kind: "departed", at: "2026-10-13T21:31:00Z", detail: { run: "R-0007", vehicle: "TMS041", driver: "kim" } },
        { kind: "delivered", at: "2026-10-13T23:52:00Z", detail: { packages: 4, signed_name: "Priya", signature_file: "s1",
                                                                   photo_file: "p1" } }] });
    }
    if (path(url) === "/v1/consignments/97") {
      return json(200, { ...FLORIST, history: [{ kind: "booked", at: "2026-10-13T21:05:00Z",
                                                 detail: { carrier: "Valley Freight (sample)", manifest: "M-0003", by: "jo" } }] });
    }
    if (path(url).startsWith("/v1/pod-files/")) {
      return new Response(new Uint8Array([0x89, 0x50]), { status: 200, headers: { "Content-Type": "image/png" } });
    }
    return null;
  });
}

describe("Consignments", () => {
  beforeEach(() => { URL.createObjectURL = vi.fn(() => "blob:pod"); URL.revokeObjectURL = vi.fn(); });
  afterEach(() => vi.unstubAllGlobals());

  it("lists each with where it is now, and counts each tab", async () => {
    desk();
    renderApp("/consignments");
    const tabs = await screen.findByRole("tablist", { name: "Consignment status" });
    await waitFor(() => expect(within(tabs).getByRole("tab", { name: /To plan/ })).toHaveTextContent("To plan14"));
    expect(within(tabs).getByRole("tab", { name: /All/ })).toHaveAttribute("aria-selected", "true");
    expect(within(tabs).getByRole("tab", { name: /With carrier/ })).toHaveTextContent("6");
    const rows = screen.getAllByRole("row");
    expect(rows[1]).toHaveTextContent("0080034488Hillside BakeryBuninyong4Delivered");
    expect(rows[2]).toHaveTextContent("To plan");
    expect(rows[3]).toHaveTextContent("Needs con note");
  });

  it("shows the first one's history and its proof of delivery", async () => {
    desk();
    renderApp("/consignments");
    const aside = await screen.findByRole("complementary", { name: "Consignment details" });
    expect(await within(aside).findByText("Packed in Simple WMS")).toBeInTheDocument();
    expect(within(aside).getByText("Hillside Bakery · Buninyong · 4 cartons · 84 kg · 0.6 m³")).toBeInTheDocument();
    expect(within(aside).getByText("R-0007 · Kim · stop 5")).toBeInTheDocument();
    expect(within(aside).getAllByRole("listitem").map((li) => li.querySelector("b")!.textContent))
      .toEqual(["Packed in Simple WMS", "On run R-0007", "Departed", "Delivered"]);
    expect(await within(aside).findByRole("img", { name: "Signature from Priya" })).toBeInTheDocument();
    expect(within(aside).getByRole("img", { name: "Photo at the drop" })).toBeInTheDocument();
    expect(within(aside).getByText("Priya")).toBeInTheDocument();
  });

  it("finds by what was typed on the map, and by tab", async () => {
    const { calls } = desk();
    renderApp("/consignments?q=vf-0041");
    expect(await screen.findByRole("searchbox", { name: "Search consignments" })).toHaveValue("vf-0041");
    await waitFor(() => expect(calls.some((c) => c.url.includes("q=vf-0041") && !c.url.includes("limit=0"))).toBe(true));
    const user = userEvent.setup();
    await user.click(screen.getByRole("tab", { name: /With carrier/ }));
    await waitFor(() => expect(calls.some((c) => c.url.includes("status=booked%2Ccollected") && !c.url.includes("limit=0")))
      .toBe(true));
    await user.click(screen.getByRole("button", { name: "Show 0080034497" }));
    const aside = await screen.findByRole("complementary", { name: "Consignment details" });
    expect(await within(aside).findByText("Booked with Valley Freight (sample)")).toBeInTheDocument();
    expect(within(aside).getByText("Valley Freight (sample) · no con note yet")).toBeInTheDocument();
  });
});
```

**Step 2: Run them to see them fail**

Run: `npx vitest run src/test/history.test.ts src/test/Consignments.test.tsx`
Expected: `Failed to resolve import "../lib/history"`, and `3 failed` (the placeholder has no tabs, table or aside).

**Step 3: Implement**

Create `apps/desktop/src/lib/history.ts`:
```ts
/** A consignment's history (GET /v1/consignments/{id}), in the words of
 * Consignments.html's timeline, and its proof of delivery. */
import type { HistoryRow } from "../api/types";

const REASONS: Record<string, string> = { closed: "closed", refused: "refused", damaged: "damaged",
                                          wrong_address: "wrong address" };

const text = (v: unknown) => (typeof v === "string" && v ? v : null);
const join = (...parts: (string | null)[]) => parts.filter(Boolean).join(" · ");

/** The title, the small line under it, and whether Simple WMS or Simple TMS did it. */
export function historyLine(h: HistoryRow): { title: string; more: string; from: "wms" | "tms" } {
  const d = h.detail;
  const by = text(d.by);
  switch (h.kind) {
    case "packed": return { title: "Packed in Simple WMS", more: d.packages ? `${d.packages} packages` : "", from: "wms" };
    case "assigned": return { title: `On run ${d.run}`, more: join(by), from: "tms" };
    case "unassigned": return { title: `Off run ${d.run}`, more: join(by), from: "tms" };
    case "departed": return { title: "Departed", more: join(text(d.vehicle), text(d.driver)), from: "tms" };
    case "delivered":
      return { title: "Delivered", more: join(d.packages ? `${d.packages} packages` : null,
                                              text(d.signed_name) ? `signed by ${d.signed_name}` : null), from: "tms" };
    case "failed": return { title: "Not delivered", more: REASONS[String(d.reason)] ?? String(d.reason ?? ""), from: "tms" };
    case "booked": return { title: `Booked with ${d.carrier}`, more: join(text(d.manifest), by), from: "tms" };
    case "unbooked": return { title: `Off ${d.carrier}'s manifest`, more: join(by), from: "tms" };
    case "con_note":
      return { title: `Con note ${d.con_note}`, more: join(text(d.carrier), text(d.was) ? `was ${d.was}` : null, by), from: "tms" };
    case "collected":
      return { title: `Collected by ${d.carrier}`, more: join(`con note ${d.con_note}`, text(d.manifest), by), from: "tms" };
    default: return { title: h.kind, more: join(by), from: "tms" };
  }
}

export interface Proof { at: string; outcome: "delivered" | "failed"; signature: string | null; photo: string | null;
                         signedName: string | null; reason: string | null }

/** The latest delivery, or failed delivery, and its pictures. */
export function proofOf(history: HistoryRow[]): Proof | null {
  const h = [...history].reverse().find((r) => r.kind === "delivered" || r.kind === "failed");
  if (!h) return null;
  return { at: h.at, outcome: h.kind as Proof["outcome"], signature: text(h.detail.signature_file),
           photo: text(h.detail.photo_file), signedName: text(h.detail.signed_name), reason: text(h.detail.reason) };
}
```

Create `apps/desktop/src/pages/Consignments.tsx`. It asks a quarter of a second after the last key, and only the latest answer draws:
```tsx
import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, ApiError, Unreachable } from "../api/client";
import type { Consignment, ConsignmentDetail, Page } from "../api/types";
import { whereNow, type Tone } from "../lib/carriers";
import { clock } from "../lib/days";
import { plain, toMicro } from "../lib/decimal";
import { historyLine, proofOf } from "../lib/history";
import { Drawer } from "../ui/Drawer";
import { Icon } from "../ui/icons";
import { Photo } from "../ui/Photo";

const TABS = [
  { key: "all", label: "All", status: undefined, tone: "" },
  { key: "plan", label: "To plan", status: "packed,failed", tone: "a" },
  { key: "run", label: "On a run", status: "assigned,departed", tone: "" },
  { key: "carrier", label: "With carrier", status: "booked,collected", tone: "" },
  { key: "delivered", label: "Delivered", status: "delivered", tone: "" },
  { key: "failed", label: "Not delivered", status: "failed", tone: "r" },
] as const;

const PILL: Record<Tone, string> = { amber: "p-amber", red: "p-red", blue: "p-blue", green: "p-green", grey: "p-grey" };

function said(e: unknown): string {
  if (e instanceof Unreachable) return e.message;
  return e instanceof ApiError ? e.message : "Simple TMS could not be reached. Check the connection and try again.";
}

function Pill({ c }: { c: Consignment }) {
  const w = whereNow(c);
  return <span className={`pill ${PILL[w.tone]}`}>{w.text}</span>;
}

/** The aside: what it is, where it is, its history, and its proof of delivery. */
function Details({ id }: { id: number }) {
  const [c, setC] = useState<ConsignmentDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    setC(null);
    setError(null);
    api.get<ConsignmentDetail>(`/v1/consignments/${id}`).then((d) => live && setC(d)).catch((e) => live && setError(said(e)));
    return () => { live = false; };
  }, [id]);
  if (error) return <aside className="aside" aria-label="Consignment details"><div className="msg r" role="alert">{error}</div></aside>;
  if (!c) return <aside className="aside" aria-label="Consignment details"><div className="small">Loading…</div></aside>;
  const cartons = c.packages.filter((p) => p.type === "carton").length;
  const pod = proofOf(c.history);
  const w = whereNow(c);
  return (
    <aside className="aside" aria-label="Consignment details">
      <div>
        <div className="eyebrow" style={{ color: "var(--color-green)" }}>Consignment · {w.text}</div>
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <span style={{ fontSize: 22, fontWeight: 800 }}>{c.wms_ref}</span><Pill c={c} /></div>
        <div style={{ fontSize: 13, color: "var(--color-ink-2)" }}>
          {[c.customer, c.ship_to?.suburb, `${cartons} ${cartons === 1 ? "carton" : "cartons"}`,
            `${plain(toMicro(c.weight_kg))} kg`, `${plain(toMicro(c.volume_m3))} m³`].filter(Boolean).join(" · ")}</div>
      </div>
      <div className="box">
        <div><span className="small">From WMS</span><b>{c.wms_ref}</b></div>
        {c.run && <div><span className="small">Run</span><b>{c.run.run_no} · {c.run.driver} · stop {c.run.sequence}</b></div>}
        {c.carrier && <div><span className="small">Carrier</span>
          <b>{c.carrier.name} · {c.carrier.con_note ? `con note ${c.carrier.con_note}` : "no con note yet"}</b></div>}
        {c.carrier?.collected_at && <div><span className="small">Collected</span>
          <b>{clock(new Date(c.carrier.collected_at))} · {c.carrier.manifest_no}</b></div>}
      </div>
      <div>
        <div className="eyebrow" style={{ marginBottom: 10 }}>History</div>
        <ol className="tl">
          {c.history.map((h, i) => {
            const l = historyLine(h);
            return (
              <li key={i}>
                <span className={`m${l.from === "wms" ? " wms" : ""}`}><Icon name={l.from === "wms" ? "box" : "tick"} size={13} width={2.6} /></span>
                <div><b>{l.title}</b><span className="w">{[clock(new Date(h.at)), l.more].filter(Boolean).join(" · ")}</span></div>
              </li>
            );
          })}
        </ol>
      </div>
      {pod && (
        <div className="pod">
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <span className="eyebrow">{pod.outcome === "delivered" ? "Proof of delivery" : "Not delivered"}</span>
            <span className="small">{clock(new Date(pod.at))}</span></div>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            {pod.signature && <Photo fileId={pod.signature} label={`Signature from ${pod.signedName ?? "the customer"}`} />}
            {pod.photo && <Photo fileId={pod.photo} label="Photo at the drop" />}
          </div>
          {pod.signedName && <div style={{ fontSize: 13 }}>Signed by <b>{pod.signedName}</b> (printed name)</div>}
        </div>
      )}
    </aside>
  );
}

/** Consignments.html: every delivery that came from Simple WMS, where it is
 * now (a run, a carrier, delivered), and how it got there. Search by WMS
 * delivery, customer or con note; the map's search box opens it with ?q=. */
export function Consignments() {
  const [params, setParams] = useSearchParams();
  const [q, setQ] = useState(params.get("q") ?? "");
  const [tab, setTab] = useState<(typeof TABS)[number]["key"]>("all");
  const [items, setItems] = useState<Consignment[] | null>(null);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [chosen, setChosen] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const latest = useRef(0);

  useEffect(() => {
    const mine = ++latest.current;
    const status = TABS.find((t) => t.key === tab)!.status;
    // a moment after the last key, not on each one
    const t = setTimeout(() => {
      api.get<Page<Consignment>>("/v1/consignments", { status, q: q.trim(), limit: 100 })
        .then((p) => { if (mine === latest.current) { setItems(p.items); setError(null); } })
        .catch((e) => { if (mine === latest.current) setError(said(e)); });
      Promise.all(TABS.map((x) => api.get<Page<unknown>>("/v1/consignments", { status: x.status, q: q.trim(), limit: 0 })))
        .then((ps) => { if (mine === latest.current) setCounts(Object.fromEntries(TABS.map((x, i) => [x.key, ps[i].total]))); })
        .catch(() => undefined);
    }, 250);
    return () => clearTimeout(t);
  }, [q, tab]);

  function search(value: string) {
    setQ(value);
    setParams(value.trim() ? { q: value } : {}, { replace: true });
  }

  const shown = items ?? [];
  const picked = shown.find((c) => c.id === chosen) ?? shown[0] ?? null;
  return (
    <Drawer label="Consignments" head={
      <div style={{ flex: 1, minWidth: 200 }}>
        <div className="eyebrow" style={{ color: "var(--color-green)" }}>Consignments</div>
        <h1>Consignments</h1>
        <div className="sub">Every delivery that came from Simple WMS, where it is now, and how it got there.</div>
      </div>
    }>
      <label className="search"><Icon name="search" />
        <span className="sr">Search consignments</span>
        <input type="search" placeholder="WMS delivery, customer or con note" value={q} onChange={(e) => search(e.target.value)} /></label>
      <div className="tabs" role="tablist" aria-label="Consignment status">
        {TABS.map((t) => (
          <button key={t.key} type="button" className="tab" role="tab" aria-selected={tab === t.key} onClick={() => setTab(t.key)}>
            {t.label}<i className={t.tone}>{counts[t.key] ?? "–"}</i></button>
        ))}
      </div>
      {error && <div className="msg r" role="alert">{error}</div>}
      <div className="split">
        <div className="tablewrap">
          <table style={{ minWidth: 520 }}>
            <thead><tr><th>WMS DELIVERY</th><th>CUSTOMER</th><th>CTN</th><th>STATUS</th></tr></thead>
            <tbody>
              {shown.map((c) => {
                const on = c.id === picked?.id;
                return (
                  <tr key={c.id} className={on ? "sel" : undefined}>
                    <td><button type="button" className="rowbtn" aria-pressed={on} aria-label={`Show ${c.wms_ref}`}
                                onClick={() => setChosen(c.id)}>{c.wms_ref}</button></td>
                    <td style={{ whiteSpace: "nowrap" }}><b>{c.customer}</b><div className="small">{c.ship_to?.suburb}</div></td>
                    <td className="num">{c.packages.filter((p) => p.type === "carton").length}</td>
                    <td><Pill c={c} /></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {items && shown.length === 0 && <div className="small" style={{ padding: 16 }}>Nothing matches.</div>}
        </div>
        {picked && <Details key={picked.id} id={picked.id} />}
      </div>
    </Drawer>
  );
}
```

Change `apps/desktop/src/App.tsx`:
```diff
--- a/apps/desktop/src/App.tsx
+++ b/apps/desktop/src/App.tsx
@@ -2,10 +2,10 @@ import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
 import { AuthProvider } from "./auth/AuthContext";
 import { RequireAuth } from "./auth/RequireAuth";
 import { Carriers } from "./pages/Carriers";
+import { Consignments } from "./pages/Consignments";
 import { Fleet } from "./pages/Fleet";
 import { MapHome } from "./pages/MapHome";
 import { PlanRuns } from "./pages/PlanRuns";
-import { Later } from "./pages/Later";
 import { SignIn } from "./pages/SignIn";
 import { WhereIsIt } from "./pages/WhereIsIt";
 
@@ -18,9 +18,7 @@ export function AppRoutes() {
         <Route element={<MapHome />}>
           <Route index element={null} />
           <Route path="/plan" element={<PlanRuns />} />
-          <Route path="/consignments" element={
-            <Later title="Consignments" step="a later build step"
-                   what="Search by WMS delivery, customer or con note, and see each one's history." />} />
+          <Route path="/consignments" element={<Consignments />} />
           <Route path="/carriers" element={<Carriers />} />
           <Route path="/fleet" element={<Fleet />} />
           <Route path="/where" element={<WhereIsIt />} />
```

Change `apps/desktop/src/index.css`:
```diff
--- a/apps/desktop/src/index.css
+++ b/apps/desktop/src/index.css
@@ -208,6 +208,26 @@
   .grp tr.wait td { background: #FFFBEF; }
   .grp tr.sel td { background: #F1F8F4; }
   .grp tr.sel .rowbtn { border-color: var(--color-green); background: var(--color-tint); }
+
+  /* the Consignments drawer (Consignments.html) */
+  .p-blue { background: #E4EEF9; color: #235596; }
+  .tabs { display: flex; flex-wrap: wrap; gap: 6px; }
+  .tab { height: 40px; padding: 0 14px; border-radius: 999px; cursor: pointer; font-size: 14px; font-weight: 700; display: flex; align-items: center; gap: 8px; border: 1px solid var(--color-line); background: #fff; color: var(--color-ink); }
+  .tab i { font-style: normal; min-width: 20px; padding: 1px 6px; border-radius: 999px; font-size: 12px; font-weight: 800; background: var(--color-tint); color: var(--color-green-dark); text-align: center; }
+  .tab[aria-selected="true"] { background: var(--color-green); border-color: var(--color-green); color: #fff; }
+  .tab[aria-selected="true"] i { background: rgba(255, 255, 255, 0.22); color: #fff; }
+  .tab i.r { background: var(--color-red-bg); color: var(--color-red-ink); }
+  .tab i.a { background: var(--color-amber-bg); color: var(--color-amber-ink); }
+  .tl { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; }
+  .tl li { display: flex; gap: 12px; position: relative; padding-bottom: 10px; }
+  .tl li:last-child { padding-bottom: 0; }
+  .tl li::before { content: ""; position: absolute; left: 11px; top: 24px; bottom: 0; width: 2px; background: #CFE3D6; }
+  .tl li:last-child::before { display: none; }
+  .tl .m { width: 24px; height: 24px; border-radius: 50%; display: grid; place-items: center; flex: none; background: var(--color-green); color: #fff; }
+  .tl .m.wms { background: #B4844A; }
+  .tl b { display: block; font-size: 14px; }
+  .tl .w { display: block; font-size: 12px; color: var(--color-muted); }
+  .pod { padding: 12px; border-radius: 14px; background: #fff; border: 1px solid var(--color-card-line); display: flex; flex-direction: column; gap: 10px; }
 }
 
 @media (max-width: 960px) {
```

Remove the placeholder: `git rm apps/desktop/src/pages/Later.tsx`

**Step 4: Run them**

Run: `npx vitest run src/test/history.test.ts src/test/Consignments.test.tsx && npm run typecheck`
Expected: `5 passed`. `npm test`: `110 passed`.

**Step 5: Commit**

```bash
git add apps/desktop/src/lib/history.ts apps/desktop/src/pages/Consignments.tsx apps/desktop/src/App.tsx \
  apps/desktop/src/index.css apps/desktop/src/test/history.test.ts apps/desktop/src/test/Consignments.test.tsx
git commit -m "Build the Consignments drawer: find one by delivery, customer or con note, and its history"
```

### Task 13: The carrier bay on the map, and the count on Carriers

**Files:**
- Modify: `apps/desktop/src/ui/Trucks.tsx` (`CarrierBay`), `apps/desktop/src/pages/MapHome.tsx`
- Test: `apps/desktop/src/test/MapHome.test.tsx`

**Step 1: Write the failing tests**

Change `apps/desktop/src/test/MapHome.test.tsx`:
```diff
--- a/apps/desktop/src/test/MapHome.test.tsx
+++ b/apps/desktop/src/test/MapHome.test.tsx
@@ -1,5 +1,6 @@
 import { screen, waitFor } from "@testing-library/react";
 import userEvent from "@testing-library/user-event";
+import { line, manifest, RIDGELINE, VALLEY } from "./bay";
 import { json, path, renderApp, stubApi } from "./harness";
 
 describe("the map", () => {
@@ -43,6 +44,31 @@ describe("the map", () => {
     expect(calls.find((c) => path(c.url) === "/v1/vehicles")!.url).toMatch(/active=true/);
   });
 
+  it("shows how many wait on the carrier bay, and on Carriers how many need a con note", async () => {
+    const bay = [manifest(1, RIDGELINE, [line(26, "Mill Lane Florist", null), line(33, "Hilltop Joinery", "RX-1")]),
+                 manifest(2, VALLEY, [line(24, "Paddock Produce", "VF-1"), line(28, "Old Mill Books", null)]),
+                 manifest(3, VALLEY, [line(20, "Corner Hardware", "VF-0")], { status: "collected", collected_at: "2026-10-15T00:00:00Z" })];
+    const { calls } = stubApi((_m, url) => {
+      if (path(url) === "/v1/manifests") return json(200, { items: bay, total: 3 });
+      return json(200, { items: [], total: 0 });
+    });
+    renderApp("/");
+    const pin = await screen.findByRole("link", { name: "4 consignments wait on the carrier bay" });
+    expect(pin).toHaveTextContent("4 for carriers");
+    const carriers = screen.getByRole("link", { name: /^Carriers/ });
+    await waitFor(() => expect(carriers).toHaveTextContent("Carriers2"));
+    expect(calls.find((c) => path(c.url) === "/v1/manifests")!.url).toMatch(/left_open=true/);
+    await userEvent.setup().click(pin);
+    expect(screen.getByTestId("where")).toHaveTextContent("/carriers");
+  });
+
+  it("draws no carrier bay count when nothing waits", async () => {
+    stubApi(() => json(200, { items: [], total: 0 }));
+    renderApp("/");
+    await screen.findByRole("navigation", { name: "Main drawers" });
+    expect(screen.queryByText(/for carriers/)).toBeNull();
+  });
+
   it("signs out from the account button", async () => {
     const { calls } = stubApi(() => json(200, { items: [], total: 0 }));
     renderApp("/");
```

**Step 2: Run them to see them fail**

Run: `npx vitest run src/test/MapHome.test.tsx`
Expected: `1 failed, 6 passed`: no link "4 consignments wait on the carrier bay".

**Step 3: Implement**

Change `apps/desktop/src/ui/Trucks.tsx`. The label stands where `design/screens/region.svg` draws "4 for carriers" (its pill at 872.6, 421, 125.6 wide and 26 high: bottom centre 935, 447 on the picture), placed as the trucks are:
```diff
--- a/apps/desktop/src/ui/Trucks.tsx
+++ b/apps/desktop/src/ui/Trucks.tsx
@@ -73,6 +73,19 @@ export function TruckMarkers({ runs, heardFrom, blocked, picked, onPick, now }:
   );
 }
 
+/** "4 for carriers" over the carrier bay at the depot (Main.html): what waits
+ * there for a carrier to collect. Opens Carriers. */
+export function CarrierBay({ waiting }: { waiting: number }) {
+  if (waiting === 0) return null;
+  return (
+    <Link to="/carriers" className="truck" style={at([935, 447])}
+          aria-label={`${waiting} ${waiting === 1 ? "consignment waits" : "consignments wait"} on the carrier bay`}>
+      <span className="tag" style={{ color: "var(--color-amber-ink)" }}>
+        <span className="dot" style={{ width: 9, height: 9, background: "var(--color-amber)" }} />{waiting} for carriers</span>
+    </Link>
+  );
+}
+
 /** The picked run's card: who and what, when it was last heard from, its
  * stops, and the estimates, labelled as such. */
 export function TruckCard({ run, heardFrom, canLocate, now, onClose }: {
```

Change `apps/desktop/src/pages/MapHome.tsx`:
```diff
--- a/apps/desktop/src/pages/MapHome.tsx
+++ b/apps/desktop/src/pages/MapHome.tsx
@@ -1,7 +1,7 @@
 import { useCallback, useEffect, useState, type FormEvent } from "react";
 import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
 import { api } from "../api/client";
-import type { FeedItem, Page, Run, TruckAt, Vehicle } from "../api/types";
+import type { FeedItem, Manifest, Page, Run, TruckAt, Vehicle } from "../api/types";
 import { useAuth } from "../auth/AuthContext";
 import { clock, isoDay } from "../lib/days";
 import { cantGo } from "../lib/fleet";
@@ -10,7 +10,8 @@ import { leftOpen } from "../lib/live";
 import { DrawerBoundary } from "../ui/DrawerBoundary";
 import { Feed } from "../ui/Feed";
 import { Icon, Logo } from "../ui/icons";
-import { TruckCard, TruckMarkers } from "../ui/Trucks";
+import { bayStats } from "../lib/carriers";
+import { CarrierBay, TruckCard, TruckMarkers } from "../ui/Trucks";
 
 interface Counts { toPlan: number; onRoad: number; delivered: number }
 
@@ -35,6 +36,8 @@ export function MapHome() {
   const [blocked, setBlocked] = useState<Vehicle[]>([]);
   const [runs, setRuns] = useState<Run[]>([]);
   const [feed, setFeed] = useState<FeedItem[] | null>(null);
+  // on the carrier bay: waiting for a carrier, and how many of those need a con note
+  const [bay, setBay] = useState({ waiting: 0, needs: 0 });
   const [heardFrom, setHeardFrom] = useState<Map<number, TruckAt>>(new Map());
   // the truck whose card shows: the first on the road until one is picked
   const [picked, setPicked] = useState<number | null | "none">(null);
@@ -66,6 +69,10 @@ export function MapHome() {
     try {
       setFeed((await api.get<{ items: FeedItem[] }>("/v1/feed", { limit: 5 })).items);
     } catch { /* the feed waits for the next go */ }
+    try {
+      const s = bayStats((await api.get<Page<Manifest>>("/v1/manifests", { date: isoDay(new Date()), left_open: true })).items);
+      setBay({ waiting: s.waiting, needs: s.needs });
+    } catch { /* the bay waits for the next go */ }
     if (canLocate) {
       try {
         const at = await api.get<Page<TruckAt>>("/v1/positions", { date: isoDay(new Date()) });
@@ -123,6 +130,7 @@ export function MapHome() {
           <img src="/region.svg" alt="The region: the depot with its carrier bay, the roads and customer stops" />
           <TruckMarkers runs={runs} heardFrom={heardFrom} blocked={blocked.filter((v) => !onRun.has(v.rego))}
                         picked={shown?.id ?? null} onPick={setPicked} now={now} />
+          <CarrierBay waiting={bay.waiting} />
         </div>
         <div className="ov" style={{ top: 20, left: 24, width: 318 }}>
           <div style={{ display: "grid", gridTemplateColumns: "repeat(3,minmax(0,1fr))", gap: 8 }}>
@@ -150,7 +158,8 @@ export function MapHome() {
             <Icon name="route" />Plan runs{counts && counts.toPlan > 0 && <i>{counts.toPlan}</i>}
           </NavLink>
           <NavLink to="/consignments" className={({ isActive }) => (isActive ? "on" : "")}><Icon name="box" />Consignments</NavLink>
-          <NavLink to="/carriers" className={({ isActive }) => (isActive ? "on" : "")}><Icon name="hand" />Carriers</NavLink>
+          <NavLink to="/carriers" className={({ isActive }) => (isActive ? "on" : "")}><Icon name="hand" />Carriers
+            {bay.needs > 0 && <i className="a" title={`${bay.needs} without a con note`}>{bay.needs}</i>}</NavLink>
           <NavLink to="/fleet" className={({ isActive }) => (isActive ? "on" : "")}><Icon name="truck" />Fleet
             {stuck > 0 && <i className="r" title={`${stuck} can't take a run`}>{stuck}</i>}</NavLink>
         </nav>
```

**Step 4: Run them, and build**

Run: `npx vitest run src/test/MapHome.test.tsx && npm test && npm run build`
Expected: `7 passed`; `112 passed`; a `dist/` as before (its `slippy-…` chunk too).

**Step 5: Commit**

```bash
git add apps/desktop/src/ui/Trucks.tsx apps/desktop/src/pages/MapHome.tsx apps/desktop/src/test/MapHome.test.tsx
git commit -m "Count what waits on the carrier bay on the map, and what needs a con note on Carriers"
```

## Part C — docs and checks

### Task 14: README, `.env.example` and Compose

**Files:**
- Modify: `README.md` (status, Simple WMS told of a collection, the `carrier-manifest` template, a **Carriers** section, Known limitations), `.env.example`, `docker-compose.yml` (`TMS_MANIFEST_PRINTER` for the `api`, which queues the jobs)

`CLAUDE.md` and CI don't change: the rules already cover this step, and the new tests run in the existing jobs.

**Step 1: Write it**

Change `README.md`:
````diff
--- a/README.md
+++ b/README.md
@@ -4,20 +4,21 @@ An open source, self-hosted transport management system for businesses that
 deliver with their own trucks and also send freight with outside carriers.
 It sits beside Simple WMS and Platen and works without either.
 
-Status: **build step 5, live location.** While a run is on the road the
-driver's phone shares where it is about every 30 seconds, and says so; the
-desktop's map draws the day's trucks, says when each was last heard from and
-when it should reach its next stop and the depot (estimates from the stop
-order and the driver's own pace), shows what just happened, and opens **Where
-is it** on a real map when map tiles are set up. Positions are deleted after
-90 days. On top of step 4 (pre-start checks, grounding, rego and service
-due), step 3 (the driver app: start the run, deliver each stop with a
-signature, a photo or both, or say why not, and finish, with or without
-signal), step 2 (runs planned on a map-and-drawer desktop, run sheets
-printed through Platen, Simple WMS told each delivery shipped when its run
-leaves) and step 1 (sign-in, vehicles and drivers, consignments from Simple
-WMS's `delivery.packed`). Carriers come with the next step in
-`docs/plans/2026-10-10-simple-tms-design.md`.
+Status: **build step 6, carriers**, the last step of version one in
+`docs/plans/2026-10-10-simple-tms-design.md`. A dispatcher sends a
+consignment with an outside carrier from **Plan runs**, types or scans its
+con note in **Carriers**, prints the carrier's manifest through Platen, and
+marks it collected when the carrier calls: Simple WMS is then told it
+shipped, with the carrier's name and the con note. **Consignments** finds
+any consignment by its WMS delivery, customer or con note, with its history
+and proof of delivery. On top of step 5 (live location: the trucks on the
+map, ETAs, what just happened, **Where is it**), step 4 (pre-start checks,
+grounding, rego and service due), step 3 (the driver app: start the run,
+deliver each stop with a signature, a photo or both, or say why not, and
+finish, with or without signal), step 2 (runs planned on a map-and-drawer
+desktop, run sheets printed through Platen, Simple WMS told each delivery
+shipped when its run leaves) and step 1 (sign-in, vehicles and drivers,
+consignments from Simple WMS's `delivery.packed`).
 
 ## What is in this repo
 
@@ -292,7 +293,8 @@ consignment. Details are in `docs/api.md`.
 When a run leaves, Simple TMS calls `POST /v1/deliveries/{ref}/ship` for each
 consignment with carrier "Own fleet" and the run number as the tracking
 number, retrying after 1 min, 5, 30 and 2 h if Simple WMS is away; Simple WMS
-then fires `delivery.shipped` as usual.
+then fires `delivery.shipped` as usual. When a carrier collects, the same
+call goes with the carrier's name and the con note as the tracking number.
 
 ## Connecting Platen
 
@@ -314,6 +316,72 @@ docker compose exec api tms create-api-key --name platen --scopes printing:write
 
 (Platen's keys screen has the two fields; Platen 0014 or later.)
 
+Carrier manifests print the same way, from a page template with the id
+`carrier-manifest` (the fields are in `docs/api.md` under "Manifests"), on
+the printer `TMS_MANIFEST_PRINTER` (empty: the run sheets' printer). A
+starting point, made with Platen's admin session (or in its studio's page
+editor) and then published:
+
+```
+curl -s -b "$PLATEN_COOKIE" -X PUT https://<platen>/pages/carrier-manifest \
+  -H 'Content-Type: application/json' -d @- <<'JSON'
+{
+  "id": "carrier-manifest",
+  "name": "Carrier manifest (Simple TMS)",
+  "folder": "Simple TMS",
+  "paper": "A4",
+  "margin_mm": 15,
+  "header": [
+    {"kind": "text", "value": "{{ carrier }} · manifest {{ manifest_no }}", "size_pt": 18, "bold": true},
+    {"kind": "text", "value": "{{ date }} · account {{ account }} · pickup {{ pickup_time }}", "size_pt": 11},
+    {"kind": "text", "value": "{{ consignment_count }} consignments · {{ package_count }} packages · {{ total_kg }} kg", "size_pt": 10},
+    {"kind": "rule"}
+  ],
+  "body": [
+    {"kind": "barcode", "value": "{{ manifest_no }}", "height_mm": 10},
+    {"kind": "table", "size_pt": 9, "columns": [
+      {"title": "#", "value": "{{ line }}", "width_mm": 8, "align": "right"},
+      {"title": "Con note", "value": "{{ con_note }}", "width_mm": 34},
+      {"title": "Customer", "value": "{{ customer }}"},
+      {"title": "Address", "value": "{{ address }}, {{ suburb }} {{ state }} {{ postcode }}"},
+      {"title": "Pkgs", "value": "{{ packages }}", "width_mm": 12, "align": "right"},
+      {"title": "kg", "value": "{{ weight_kg }}", "width_mm": 16, "align": "right"}
+    ]}
+  ],
+  "footer": [
+    {"kind": "text", "value": "Received by the carrier's driver: name ______________ signed ______________", "size_pt": 9},
+    {"kind": "text", "value": "{{ manifest_no }} · page {{ page }} of {{ pages }}", "size_pt": 8, "align": "right"}
+  ]
+}
+JSON
+curl -s -b "$PLATEN_COOKIE" -X POST https://<platen>/templates/carrier-manifest/publish
+```
+
+## Carriers
+
+Freight going with outside carriers is booked by hand in this version: book
+with the carrier as you always have (its website or a phone call), then in
+Simple TMS:
+
+1. **Add the carriers** you use, once, as a fleet manager or admin (there is
+   no screen for it yet):
+   ```
+   curl -s -X POST https://tms.example.com/v1/carriers -H "Authorization: Bearer <token>" \
+     -H 'Content-Type: application/json' \
+     -d '{"name": "<the carrier, as Simple WMS should record it>", "account": "<your account no.>", "pickup_time": "15:30"}'
+   ```
+2. In **Plan runs**, send a consignment **With a carrier…** (from the tray,
+   or off a planned run with **Move to…**). It goes on that carrier's
+   manifest for the day being planned, and waits on the carrier bay.
+3. In **Carriers**, type the con note, or scan the carrier's label with a
+   desk scanner (any USB or Bluetooth scanner that types what it reads and
+   presses Enter). Put a wrong one right the same way; each is kept in the
+   consignment's history.
+4. **Print manifest** once every con note is in, for the carrier's driver to
+   sign.
+5. When the carrier has been, **Mark collected**: each consignment is marked
+   shipped in Simple WMS with the carrier's name and its con note.
+
 ## Run the API outside Docker
 
 ```
@@ -460,6 +528,18 @@ stood in for by `fake-indexeddb`: `cd apps/driver && npm test`.
   another system to send email or SMS, comes with the first system that
   subscribes.
 
+- **Carriers are booked by hand.** Simple TMS doesn't talk to any carrier:
+  book with the carrier as you always have and type or scan the con note.
+  It doesn't follow the freight after it is collected either: a carrier's
+  consignment stays "Collected" in Simple TMS, and the carrier's own
+  tracking says where it is. A carrier's own API can come later as an
+  adapter (`api/tms/services/carrier_adapters.py`) without changing the data.
+- **Carriers are added through the API.** There is no screen to add or
+  change a carrier yet (see "Carriers" above; `docs/api.md` has the rest).
+- **What a carrier brings back isn't recorded.** If a carrier returns
+  freight it couldn't deliver, Simple TMS has no step for it: the
+  consignment stays collected, and shipped in Simple WMS.
+
 - **What came back is still shipped in Simple WMS.** When a driver can't
   deliver, the consignment comes back to the depot and returns to the
   dispatcher's tray to be planned again. Simple WMS still has it as shipped,
````

Change `.env.example`:
```diff
--- a/.env.example
+++ b/.env.example
@@ -30,7 +30,7 @@ TMS_WMS_WEBHOOK_SECRET=
 TMS_WMS_URL=
 TMS_WMS_API_KEY=
 
-# Platen (optional: without it, run sheets are not printed)
+# Platen (optional: without it, run sheets and carrier manifests are not printed)
 # Its job intake, e.g. https://platen.example.com/intake/jobs, and an
 # operator key from Platen's People > Keys. Give that Platen key a callback to
 # this API (https://<tms>) with a Simple TMS key made by
@@ -40,6 +40,8 @@ TMS_PLATEN_URL=
 TMS_PLATEN_KEY=
 # The Platen printer run sheets go to: its id or its name.
 TMS_RUN_SHEET_PRINTER=dispatch
+# The Platen printer carrier manifests go to. Empty: the run sheets' printer.
+TMS_MANIFEST_PRINTER=
 
 # Live location (optional settings)
 # Positions, and the position kept with each proof of delivery, are deleted
```

Change `docker-compose.yml`:
```diff
--- a/docker-compose.yml
+++ b/docker-compose.yml
@@ -29,6 +29,7 @@ services:
       TMS_WMS_API_KEY: ${TMS_WMS_API_KEY:-}
       TMS_PLATEN_URL: ${TMS_PLATEN_URL:-}
       TMS_RUN_SHEET_PRINTER: ${TMS_RUN_SHEET_PRINTER:-dispatch}
+      TMS_MANIFEST_PRINTER: ${TMS_MANIFEST_PRINTER:-}
       TMS_MAP_TILE_URL: ${TMS_MAP_TILE_URL:-}
       TMS_MAP_ATTRIBUTION: ${TMS_MAP_ATTRIBUTION:-}
       # checked against TMS_MAP_TILE_URL; Caddy puts it in the desktop's CSP
```

**Step 2: Check Compose reads it**

Run (repo root): `docker compose config | grep TMS_MANIFEST_PRINTER`
Expected: `TMS_MANIFEST_PRINTER: ""` under `api`.

**Step 3: Commit**

```bash
git add README.md .env.example docker-compose.yml
git commit -m "Document carriers, the carrier-manifest template and TMS_MANIFEST_PRINTER"
```

### Task 15: Every suite, and a clean Compose start

**Step 1: The suites**

Run:
```
cd "/Users/leightonlauton/Q7 Technology/Software/q7/simple-tms/api" && caffeinate -i .venv/bin/pytest -q
cd ../apps/desktop && npm test && npm run build
cd ../driver && npm test && npm run build
```
Expected: `399 passed`; `112 passed` and a `dist/`; `109 passed` and a `dist/` with `sw.js` (the driver app is unchanged).

**Step 2: Compose**

Run (repo root):
```
docker compose up -d --build api worker
docker compose up -d --force-recreate caddy
docker compose ps
docker compose exec -T db psql -U tms -d tms -tAc "select version_num from alembic_version"
docker compose exec -T db psql -U tms -d tms -tAc "select conname from pg_constraint where conname like 'ck_%run_or_manifest' order by 1"
docker compose logs worker | grep "worker started"
```
Expected: `api` healthy, `worker`, `db` and `caddy` up; `0008`; `ck_outbound_call_run_or_manifest` and `ck_print_job_run_or_manifest` (every existing ship call and run sheet is a run's, so the constraint went on without complaint); the worker started. (`--force-recreate`: the Caddyfile is mounted as a single file, and a branch switch replaces the file the container still holds.)

**Step 3: Push**

```bash
git push -u origin step-6-carriers
```
Wait for CI (API, Desktop app, Driver app, Docker Compose comes up) to go green, then do Task 16 before **superpowers:finishing-a-development-branch**.

### Task 16: Manual check on the local stacks: send, scan, print, collect, shipped in Simple WMS

Everything runs on this machine: Simple TMS on this branch (Task 15 Step 2 left it up), Simple WMS and Platen as they are. Scripts and screenshots go in a working folder of your own in your scratchpad, never `/tmp` and never the repo: set `W` to it in each shell. Keep the Mac awake (`caffeinate -i` in front of the long commands). Docker's clock can lag after the Mac sleeps: if a time below looks wrong by minutes, `docker compose restart api worker`.

```
export W="<your scratchpad directory>/tms-step6-check"
mkdir -p "$W/shots"
```

**Step 1: Tokens**

```
cd "/Users/leightonlauton/Q7 Technology/Software/q7/simple-tms"
A=http://127.0.0.1:8022
token() { curl -s -X POST $A/v1/auth/login -H 'Content-Type: application/json' \
  -d "{\"username\":\"$1\",\"password\":\"$2\"}" | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])'; }
ADMIN=$(token leighton "a long enough password"); DEE=$(token dee "dispatch password")
api() { curl -s -X "$1" "$A$2" -H "Authorization: Bearer $DEE" -H 'Content-Type: application/json' ${3:+-d "$3"}; }
```
(Access tokens last 15 minutes: run the two `token` lines again if a later call says `401`.)

**Step 2: Two carriers, by the admin; none for a dispatcher**

```
for N in "Ridgeline Express (sample)" "Valley Freight (sample)"; do
  curl -s -X POST $A/v1/carriers -H "Authorization: Bearer $ADMIN" -H 'Content-Type: application/json' \
    -d "{\"name\":\"$N\",\"account\":\"SAMPLE-1\",\"pickup_time\":\"15:30\"}"; echo; done
api POST /v1/carriers '{"name":"Plains Parcels (sample)"}'; echo
api GET "/v1/carriers?active=true" | python3 -c 'import sys,json;print([c["name"] for c in json.load(sys.stdin)["items"]])'
```
Expected: each carrier back as JSON with `"adapter":"manual"` (or, if this check has run before, `422` "carrier … already exists": fine); `{"detail":"you do not have the fleet:write scope"}`; `['Ridgeline Express (sample)', 'Valley Freight (sample)']`.

**Step 3: Two deliveries packed in Simple WMS**

A Simple WMS key for this check only (printed once):
```
cd "/Users/leightonlauton/Q7 Technology/Software/q7/simple-wms"
docker compose exec api wms create-api-client --name tms-check-6 --scopes 'tasks:*,integration:*' --warehouses '*' --owner '*'
cd "/Users/leightonlauton/Q7 Technology/Software/q7/simple-tms"
```
Save as `$W/pack.py`:
```python
"""Create, pick and pack one delivery of JKL012 in Simple WMS, which sends
delivery.packed to Simple TMS. Usage: pack.py <wms> <key> <ref> <customer> <suburb>"""
import sys, uuid

import httpx

WMS, KEY, REF, CUSTOMER, SUBURB = sys.argv[1:6]
H = {"Authorization": f"Bearer {KEY}"}


def post(path, body):
    r = httpx.post(f"{WMS}/v1{path}", headers=H, timeout=30, json={"message_id": str(uuid.uuid4()), **body})
    assert r.status_code < 300, f"POST {path}: {r.status_code} {r.text}"
    return r.json()


post("/deliveries", {
    "external_ref": REF, "warehouse": "BAL-WH01", "owner": "DEFAULT",
    "ship_to": {"name": CUSTOMER, "address": "1 Main St", "suburb": SUBURB, "state": "VIC",
                "postcode": "3350", "country": "AU"},
    "lines": [{"delivery_line": 10, "sku": "JKL012", "qty": 1, "uom": "EA"}]})
task = httpx.get(f"{WMS}/v1/tasks", headers=H, params={
    "warehouse": "BAL-WH01", "source_ref": REF, "type": "pick"}).json()["items"][-1]
post(f"/tasks/{task['wms_id']}/start", {"operator": "op-017"})
for line in task["lines"]:
    post(f"/tasks/{task['wms_id']}/lines/{line['line_no']}/confirm",
         {"qty": line["expected_qty"], "uom": line["uom"], "operator": "op-017"})
post(f"/deliveries/{REF}/pack", {
    "warehouse": "BAL-WH01", "packed_by": "op-017", "complete": True,
    "packages": [{"package_no": 1, "type": "carton", "weight_kg": 2.4, "length_cm": 30, "width_cm": 20,
                  "height_cm": 10, "lines": [{"delivery_line": 10, "sku": "JKL012", "qty": 1, "uom": "EA"}]}]})
print("packed", REF)
```
Run (a new pair of references each time this check runs: `S` is a suffix of your own):
```
S=$(date +%H%M)
api/.venv/bin/python "$W/pack.py" http://127.0.0.1:8011 <the tms-check-6 key> TMS-CAR-$S-1 "Mill Lane Florist" Daylesford
api/.venv/bin/python "$W/pack.py" http://127.0.0.1:8011 <the tms-check-6 key> TMS-CAR-$S-2 "Hilltop Joinery" "Bacchus Marsh"
sleep 5; api GET "/v1/consignments?q=TMS-CAR-$S" | python3 -c 'import sys,json;print([(c["wms_ref"],c["status"]) for c in json.load(sys.stdin)["items"]])'
```
Expected: `packed TMS-CAR-…-1`, `packed TMS-CAR-…-2`, then both `packed` in Simple TMS. (If `JKL012` has run out in `BAL-WH01`, use another SKU with stock there.) If an older "Mill Lane Florist" or "Hilltop Joinery" from an earlier run of this check is still in the tray, send that one with a carrier too or take it off first: the script below picks cards by customer.

**Step 4: Platen's `carrier-manifest` template**

Sign in to Platen as its admin and keep the cookie (in `$W`):
```
curl -s -c "$W/platen.cookie" http://localhost:18000/auth/login -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"<PLATEN_ADMIN_PASSWORD from platen/.env>"}'
PLATEN_COOKIE="$W/platen.cookie"
```
Then run the README's two `curl` commands under "Connecting Platen" (the `PUT …/pages/carrier-manifest` with its JSON, and the `publish`) with `https://<platen>` as `http://localhost:18000`. Expected: the page template back as JSON, then the published version. `TMS_MANIFEST_PRINTER` is empty in `.env`, so manifests go to `TMS_RUN_SHEET_PRINTER` (`office`). Whether the stand-in printer on port 9103 is listening decides how Step 6 ends: printed, or Platen's reason it wasn't.

**Step 5: Con notes are checked by the API too**

```
C1=$(api GET "/v1/consignments?q=TMS-CAR-$S-1" | python3 -c 'import sys,json;print(json.load(sys.stdin)["items"][0]["id"])')
RX=$(api GET /v1/carriers | python3 -c 'import sys,json;print(next(c["id"] for c in json.load(sys.stdin)["items"] if c["name"].startswith("Ridgeline")))')
api POST /v1/carriers/$RX/book "{\"consignment_id\":$C1,\"date\":\"$(date +%F)\",\"con_note\":\"RX 7731\"}"; echo
api GET /v1/consignments/$C1 | python3 -c 'import sys,json;print(json.load(sys.stdin)["status"])'
```
Expected: `{"errors":[{"field":"con_note","message":"A con note is 4 to 40 letters, digits and dashes, as on the carrier's label"}]}` and `packed`: refused whole, nothing booked.

**Step 6: The dispatcher's day with a carrier, in a real Chrome**

Playwright drives the Chrome already installed:
```
cd "$W" && npm init -y >/dev/null && npm i playwright-core@1 >/dev/null
```
Save as `$W/carriers.mjs`:
```js
// The dispatcher's day with a carrier, in a real Chrome: send two consignments
// with a carrier from Plan runs, scan their con notes in Carriers, print the
// manifest (when PRINT=1), mark it collected, and find one by its con note.
import { createRequire } from "module";
const W = process.env.W, BASE = process.env.BASE;
const CARRIER = process.env.CARRIER ?? "Ridgeline Express (sample)";
const CUSTOMERS = (process.env.CUSTOMERS ?? "Mill Lane Florist,Hilltop Joinery").split(",");
const NOTES = (process.env.NOTES ?? "RX-77310041,RX-77310042").split(",");
const require = createRequire(`${W}/`);
const { chromium } = require("playwright-core");
const shot = (page, name) => page.screenshot({ path: `${W}/shots/${name}.png` });

const browser = await chromium.launch({
  executablePath: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  headless: true, args: ["--ignore-certificate-errors"] });
const page = await (await browser.newContext({ viewport: { width: 1440, height: 900 }, ignoreHTTPSErrors: true })).newPage();
page.on("pageerror", (e) => console.log("pageerror:", e.message));
page.on("dialog", (d) => { console.log("asked:", d.message()); void d.accept(); });
await page.goto(`${BASE}/`);
await page.getByLabel("Username").fill("dee");
await page.getByLabel("Password").fill("dispatch password");
await page.getByRole("button", { name: "Sign in" }).click();

// Plan runs, today: each consignment With a carrier…
await page.getByRole("link", { name: /Plan runs/ }).click();
const day = page.getByRole("combobox", { name: "Day" });
await day.selectOption({ index: 0 });
for (const customer of CUSTOMERS) {
  // a card's menus show when it is pointed at or tabbed to
  await page.locator(".con", { hasText: customer }).hover();
  await page.getByRole("combobox", { name: `Send ${customer} with a carrier` }).selectOption({ label: CARRIER });
  await page.getByText(`${customer} is on the carrier bay for ${CARRIER}. Add its con note in Carriers.`).waitFor();
  console.log("booked:", customer);
}
await page.keyboard.press("Escape");
const bay = page.getByRole("link", { name: /on the carrier bay$/ });
await bay.getByText(`${CUSTOMERS.length} for carriers`).waitFor();
console.log("map:", await bay.innerText(), "·", (await page.getByRole("link", { name: /^Carriers/ }).innerText()).replace(/\s+/g, ""));
await shot(page, "1-map");

// Carriers: scan each con note (a desk scanner types it and presses Enter)
await bay.click();
const box = page.getByRole("form", { name: "Add a con note" });
for (const note of NOTES) {
  const title = await box.getByText(/^Con note for /).innerText();
  await box.getByRole("textbox", { name: "Con note number" }).pressSequentially(note.toLowerCase());
  await page.keyboard.press("Enter");
  await page.getByText(new RegExp(`Con note ${note} saved for `)).waitFor();
  console.log(`${title}: ${note}`);
}
const group = page.getByRole("region", { name: new RegExp(`^${CARRIER.replace(/[()]/g, "\\$&")} · M-`) });
await group.getByText("Ready").waitFor();
await shot(page, "2-carriers-ready");
if (process.env.PRINT === "1") {
  await group.getByRole("button", { name: /Print manifest/ }).click();
  await page.getByText(/ sent to Platen\.$/).waitFor();
  await group.getByText(/Manifest printed on |Manifest didn't print/).waitFor({ timeout: 60_000 });
  console.log("print:", await group.getByText(/Manifest printed on |Manifest didn't print/).innerText());
}
await group.getByRole("button", { name: /Mark collected/ }).click();
await page.getByText(/has collected M-\d+\. Simple WMS is being told\.$/).waitFor();
await group.getByText(/^Collected \d/).waitFor();
console.log("collected:", (await group.locator(".ghead").innerText()).replace(/\s+/g, " "));
await page.waitForTimeout(12_000);   // the worker's next go, and the drawer's next load
await page.reload();
await page.getByRole("region", { name: new RegExp(`^${CARRIER.replace(/[()]/g, "\\$&")} · M-`) }).getByText(/^Collected \d/).waitFor();
console.log("after:", (await page.getByRole("region", { name: new RegExp(`^${CARRIER.replace(/[()]/g, "\\$&")} · M-`) }).locator(".ghead").innerText()).replace(/\s+/g, " "));
await shot(page, "3-carriers-collected");

// Consignments: found by its con note, with its history
await page.goto(`${BASE}/consignments?q=${NOTES[0].toLowerCase()}`);
const aside = page.getByRole("complementary", { name: "Consignment details" });
await aside.getByText(/^Collected by /).waitFor();
console.log("history:", (await aside.locator("ol.tl b").allInnerTexts()).join(" → "));
await shot(page, "4-consignment");
await browser.close();
```
Run (con notes of your own each time this check runs: a con note is one consignment's with a carrier for good):
```
cd "$W" && W="$W" BASE=https://localhost:8543 PRINT=1 CUSTOMERS="Mill Lane Florist,Hilltop Joinery" \
  NOTES="RX-$S-0001,RX-$S-0002" node carriers.mjs
```
Expected (the times and numbers are yours; no `pageerror` lines):
```
booked: Mill Lane Florist
booked: Hilltop Joinery
map: 2 for carriers · Carriers2
Con note for TMS-CAR-…-1 · Mill Lane Florist: RX-…-0001
Con note for TMS-CAR-…-2 · Hilltop Joinery: RX-…-0002
print: Manifest printed on office.
asked: Ridgeline Express (sample) has collected 2 consignments? Each is marked shipped in Simple WMS with its con note.
collected: Ridgeline Express (sample) Collected 9:15 am · manifest M-0001 printed by Platen · telling Simple WMS Collected Reprint
after: Ridgeline Express (sample) Collected 9:15 am · manifest M-0001 printed by Platen · marked shipped in Simple WMS Collected Reprint
history: Packed in Simple WMS → Booked with Ridgeline Express (sample) → Con note RX-…-0001 → Collected by Ridgeline Express (sample)
```
With the stand-in printer down, `print:` reads `Manifest didn't print: …` in Platen's words and the rest is the same (collecting doesn't wait on printing). With more than the two consignments on Ridgeline's manifest today (an earlier run of this check), the `map:` count is higher and the box asks for those too: give them con notes in the drawer first, or take them back to the tray. Look at the screenshots beside the designs: `shots/1-map.png` beside `design/screens/Main.html` ("2 for carriers" over the carrier bay, the amber count on **Carriers**, the feed's "Con note …" lines once they are in), `2-carriers-ready.png` beside `design/screens/Carriers.html` (the stats, the group **Ready** with **Print manifest** and **Mark collected**, each con note in the table), `3-carriers-collected.png` (the group collected, with **Reprint**), `4-consignment.png` beside `design/screens/Consignments.html` (found by the con note typed in lower case, the aside's box with the carrier and con note, the history timeline). Platen's studio, **Jobs** (`http://localhost:18000/studio/jobs`): a run of `carrier-manifest` on Office, one line per consignment with its con note.

**Step 7: Shipped in Simple WMS with the carrier and the con note**

```
for R in TMS-CAR-$S-1 TMS-CAR-$S-2; do
  curl -s "http://127.0.0.1:8011/v1/deliveries/$R" -H "Authorization: Bearer <the tms-check-6 key>" \
    | python3 -c 'import sys,json;d=json.load(sys.stdin);print(d["external_ref"],d["status"],d["carrier"],d["tracking_no"])'; done
curl -s "http://127.0.0.1:8011/v1/events?event_type=delivery.shipped&external_ref=TMS-CAR-$S-1" \
  -H "Authorization: Bearer <the tms-check-6 key>" | python3 -c 'import sys,json;print([(e["subscriber"],e["status"]) for e in json.load(sys.stdin)["items"]])'
docker compose exec -T db psql -U tms -d tms -tA -c "select status, attempts, body->>'carrier', body->>'tracking_no' from outbound_call where manifest_id is not null order by id desc limit 2"
```
Expected: `TMS-CAR-…-1 shipped Ridgeline Express (sample) RX-…-0001` and the same for `-2` with `RX-…-0002` (what `delivery.shipped` carries as `carrier` and `tracking_no`); one `delivery.shipped` per Simple WMS subscriber (the `simple-tms` subscriber's copy `delivered`: Simple TMS answers it `ignored`), so it shipped once; the two ship calls `done|1|Ridgeline Express (sample)|RX-…`.

**Step 8: Collected once, and only what was seen**

```
M=$(api GET "/v1/consignments/$C1" | python3 -c 'import sys,json;print(json.load(sys.stdin)["carrier"]["manifest_id"])')
api POST /v1/manifests/$M/collect "{\"message_id\":\"$(uuidgen)\",\"consignment_ids\":[$C1]}" | python3 -c 'import sys,json;d=json.load(sys.stdin);print(d.get("code"),d.get("detail"))'
api POST /v1/carriers/$RX/book "{\"consignment_id\":$C1,\"date\":\"$(date +%F)\"}" | python3 -c 'import sys,json;print(json.load(sys.stdin)["code"])'
docker compose exec -T db psql -U tms -d tms -tA -c "select count(*) from outbound_call where consignment_id = $C1"
```
Expected: `manifest_collected Ridgeline Express (sample) has collected M-…; what is on it can't change` (the manifest went with two on it), `not_plannable`, and `1`: one ship call for it, however often it is asked.

**Step 9: Tidy up**

In Simple WMS revoke the check key (with it: `GET /v1/api-clients` to find its id, then `POST /v1/api-clients/{id}/revoke`). The two sample carriers can stay for later checks, or be deactivated (`POST /v1/carriers/{id}/deactivate` as the admin). Remove `$W` when done.

Nothing to commit.

---

## What is left for later steps

- **A carrier's own API** (Decision 8): an adapter in `api/tms/services/carrier_adapters.py`, its calls queued for the worker; labels and tracking with it. Until then, carriers are booked by hand and not followed after collection.
- **Freight a carrier brings back**, and the Simple WMS side's "came back" design for both own-fleet and carriers (Decision 11).
- **Screens to add carriers, vehicles and drivers** (Decision 1).
- **The rest of `Consignments.html`**: the Day select, **Print docket** (a `delivery-docket` template) and **Download POD** (Decision 10).
- **Events out** (`consignment.delivered`, `consignment.failed`, `consignment.collected`, `run.completed`, `vehicle.due`) come with the first subscriber.
- **The rest of `Main.html`**: Today's goal, Game touches, sounds, day and night, zoom, and stops drawn as pins along each road.
