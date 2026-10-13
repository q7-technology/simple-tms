# Simple TMS step 4: fleet care — implementation plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build step 4 of `docs/plans/2026-10-10-simple-tms-design.md`: pre-start checks on the driver's phone (a checklist per vehicle type, a failed item with a photo and a note, the odometer), a failed critical item grounding the truck until a fleet manager marks it fixed with a note, rego and service due by date or kilometres (amber 30 days or 1,000 km out, red when overdue), rule 3 enforced everywhere a truck can be given or start a run, and the desktop's **Fleet** drawer built to `design/screens/Fleet.html`.

**Architecture:** The API grows `checklist_item` (each type's checklist, seeded with the design's six items, changed by a fleet manager through the API, lines retired never deleted), `prestart_check` and `prestart_item` (a driver's check, kept as sent behind 0001's `block_change` trigger), `vehicle_record` (what a fleet manager records: `fixed`, `service`, `rego`, `odometer`, append-only too) and two columns on `vehicle` (`service_every_months`, `service_every_km`). Where a truck stands (odometer, grounded, rego and service due, blocked) is never stored: `services/fleet.py` works it out from the checks and records for a given day, the run's own day when planning. Every write about a truck takes step 2's planning lock, so a check that grounds a truck and a run leaving on it take turns. The phone's check is a confirmation like a delivery: `POST /v1/runs/{id}/prestart` with a `message_id` through step 3's envelope and offline queue, its photos through `PUT /v1/pod-files`. The driver's Start needs the check in first (`409 prestart_needed`); a grounded or overdue truck is refused with `409 vehicle_grounded` / `vehicle_overdue` on making a run, changing its truck, assigning, printing, departing and starting.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2, Alembic, pytest on a real PostgreSQL 16; Vite 6, React 19, TypeScript 5, Tailwind 4, react-router 7, Vitest 3 with Testing Library; Caddy 2 and Docker Compose.

---

## Context

Paths are relative to the Simple TMS repo, `/Users/leightonlauton/Q7 Technology/Software/q7/simple-tms/`. Everything here was checked against `main` today (`6c252fb`, "Note that what came back is still shipped in Simple WMS"): each task was built in a scratch copy, in order, against a throwaway PostgreSQL 16, and the counts given at each task are what the suites printed there. API 244 → 278, desktop 54 → 67, driver app 76 → 84; both apps type-check and build. The manual check (Task 16) was written against the real selectors but has not been run on the local stacks.

### 1. The rules that bind this step (`CLAUDE.md`)

- **Rule 3: a grounded or overdue truck does not go on a run.** A failed critical pre-start item, or rego or service overdue, blocks it until a person clears it with a note. This step makes the rule true on every path that gives a truck a run or sends it out.
- **Rule 1: append-only.** "Pre-start checks are kept the same way" as a consignment's history. Checks, their items and every fleet record get the `block_change` trigger (`api/alembic/versions/0001_skeleton.py:136-148`). Marking fixed is a new record; a check is never changed.
- **Rule 2: recorded once.** The check from the phone carries a `message_id` through `tms/services/envelope.py`'s `handle`, like start, deliver, fail and finish.
- **Rule 4: the API is the only door.** The desktop and the phone use only the endpoints below; a partner with `deliveries:write` can send a check, one with `fleet:write` can do what the Fleet drawer does.
- **Git.** Subject line and, if needed, a short body. No `Co-Authored-By`, no tool or generated-by trailers, no session links.

### 2. The code as it is on `main` (step 3 went well past its plan)

- **Roles.** `ROLE_SCOPES` (`api/tms/services/sessions.py:28-32`): `admin` `*`; `manager` `fleet:read`, `fleet:write`, `consignments:read`, `runs:read`, `runs:write`; `dispatcher` the same without `fleet:write`. Drivers: `fleet:read`, `deliveries:read`, `deliveries:write`. So a **fleet manager is the `manager` role (or an admin)**, by `fleet:write`; nothing new is needed.
- **Vehicles** (`api/tms/models/fleet.py`, `api/tms/api/routes/vehicles.py`): `rego`, `type` (`van`, `rigid`, `ute`), `max_kg`, `max_m3`, `active`. `GET /v1/vehicles` has no paging. `vehicle_out(v)` is called from six routes.
- **Runs** (`api/tms/services/runs.py`): every planning change takes `lock_planning` (one advisory lock, `PLANNING_LOCK`), then reads. The paths that give a truck a run or send it out: `create` (`:89`), `change` with a rego (`:100`), `assign` (`:155`, already refuses a deactivated truck with `vehicle_unavailable`), `depart` (`:240`, the dispatcher's **Leaves now**), `printing.queue_run_sheet` (`api/tms/services/printing.py:48`), and the driver's `deliveries.start` (`api/tms/services/deliveries.py:56`), which calls `runs.depart`. `unassign`, `reorder`, `cancel` and a driver-only `change` don't send a truck anywhere.
- **The driver app's confirmations** go through `RetryQueue.submit` (`apps/driver/src/lib/queue.ts:332`): pictures first (`PUT /v1/pod-files/{id}?kind=photo`, each under an id chosen on the phone, kept in IndexedDB), then the confirmation; per-driver queue, merge across tabs, retry and dismiss of a refused one, `NOT_KEPT` when the phone can't keep it. A picture's id goes into a **top-level** body field only (`body[p.field] = id`). `own_run` (`deliveries.py:34`) gives a driver only their own runs (another's is `404`). A picture may be named once: `_file` (`deliveries.py:95`) checks the pods.
- **Today** (`apps/driver/src/pages/Today.tsx`): the top of `DriverPrestart.html` (a radio per run: rego, type, run number) and **Start run**, which needs signal (it loads the whole run first), with a comment saying step 4 puts the check between picking and starting.
- **Desktop.** `/fleet` is `Later` (`apps/desktop/src/App.tsx:24-26`); **Plan runs** already has the unavailable slot (`Unavailable`, `apps/desktop/src/pages/PlanRuns.tsx:39-53`, used for deactivated trucks only) and fetches `/v1/vehicles` without a date. The map (`MapHome.tsx`) is the static `region.svg` with three counts; there is no truck drawn from data and no "What just happened" feed. The desktop's CSP is `default-src 'self'` with no `img-src` (`deploy/Caddyfile:57`, and twice in `README.md`), so a `blob:` picture would be blocked.
- **Tests.** `api/tests/conftest.py` truncates every table after each test (`restart identity`). `deliveries_samples.py` has `planned_run`, `on_the_road`, `driver_headers`, `put_file`, `delivered`; `runs_samples.py` has `vehicle` (a `van`), `driver`, `consignment`, `new_run`, `DAY = 2026-10-14`. Driver tests: `harness.tsx` (`stubApi`, `renderApp`, `signedIn`), `samples.ts` (`RUN`); desktop: `harness.tsx` signs in as a dispatcher.

### 3. The designs

- `DriverPrestart.html`: title "Pre-start check", YOUR TRUCK picker (a grounded truck greyed with "Grounded" in red), **Odometer** with "km", "RIGID CHECKLIST · 5 OF 6 PASS", a row per item with Pass/Fail and "Critical item" under Lights and Brakes; a failed item opens "A fail needs a photo and a note.", **Retake photo**, **Note**, and "Not a critical item, so you can still go. The fleet manager will see it."; **Finish check**.
- `Fleet.html`: stats (VEHICLES, GROUNDED, DUE SOON, PRE-STARTS TODAY), a table (REGO, TYPE, DRIVER, REGO EXPIRY, NEXT SERVICE "14 Jan 2027 / or 61,900 km" or "in 640 km / or 30 Nov 2026", LAST PRE-START, STATUS pill), and an aside "PRE-START CHECK · FAILED" with each item, the driver's photo and note, "A failed critical item grounds the truck…", **MARK FIXED** ("What was done", "Every check is kept as it was. Marking fixed adds a new record; it never changes this one."). Footer: "Amber 30 days or 1,000 km before it's due, red once it's overdue."
- `PlanRuns.html`: the grounded truck as a dashed lane, red **Grounded** pill, "Failed pre-start at 6:40 am: brake light.", "Can't take a run until a fleet manager marks it fixed.", **Open in Fleet**.
- `Main.html`: a grounded truck on the map and a feed line "TMS-027 grounded · brake light out".

### Decisions this plan makes that were not settled

Each of these is for the user to confirm or change; the plan is built on them.

1. **Checklists are data, not code: seeded defaults, changed through the API only.** Every business has its own list (a tail lift, a fridge unit, a dangerous-goods kit), and Simple TMS is open source and self-hosted: a list in code means a fork to change it. The cost is one table, two endpoints and seven tests. YAGNI wins on the screen, though: no design draws an editor, so it is `GET /v1/checklists` and `PUT /v1/checklists/{type}` (`fleet:write`), documented, and no desktop UI. A fresh install (and each test) has the design's six items for each type: Tyres, Lights (critical), Brakes (critical), Mirrors, Load restraints, Damage. A `PUT` replaces the type's whole list: a line with the same label and critical flag keeps its id; the rest are **retired**, never deleted. Each check keeps its own copy of the labels and flags it answered, so changing a list never rewrites a check. A check that doesn't answer the list as it is now is `409 checklist_changed` ("Do the check again"); with ids kept for unchanged lines that only happens when a fleet manager adds or changes a line while a check is on its way.
2. **The pre-start belongs to the run: one before Start, every run.** `POST /v1/runs/{id}/prestart`, the run's driver only (another's run is `404`, as for deliveries). There is one live run per truck per day (step 2's index), so "per run" is "once per truck per day" whenever a truck has one run a day, and a truck that changes drivers or comes back for a second trip gets a fresh check, which is what a pre-start is for. The check names the truck the driver inspected (`vehicle`, the rego the phone showed them), and is kept against that truck: its checklist, its odometer, and any grounding. If the office gave the run another truck meanwhile, the check is still kept (a failed critical item must ground the truck that failed), and the answer is `409 vehicle_changed` naming the run's truck now; the phone says plainly that the run moved to another truck and asks for a fresh check of it. That 409 is stored with the `message_id` like a success, not dropped like other refusals: the work was done, so a repeat must get the same answer and never record the check twice. A 409 rather than a 200 with a flag, because the phone's queue (and any other client) treats a 2xx as done and moves on, while the driver must be stopped and told; the run's truck has had no check, so Start stays `prestart_needed` until it does. A check is taken on a planned run and on one already on the road (one the phone kept in a black spot still counts: it is kept and can ground the truck), refused once the run is done or cancelled, and on a planned run whose day is more than a day after today in UTC (`409 too_early`: a check is of the truck as it is on the day; the day's grace covers an Australian morning, which is still yesterday in UTC). More than one check per run is allowed, each kept.
3. **Start needs the check in; the office's Leaves now does not.** The driver's `POST /v1/runs/{id}/start` on a planned run is `409 prestart_needed` until a check of its truck for it is in. The dispatcher's **Leaves now** is for a driver without a phone, who does a paper check, so it doesn't ask for one; **Plan runs** shows "No pre-start yet." on such a run, and the README lists it under Known limitations. Both refuse a grounded or overdue truck. (The alternative, requiring a check for Leaves now too, would strand a driver without a phone.)
4. **No signal: the check waits on the phone, and Start waits for it.** The check goes through the offline queue like a delivery (its own `message_id`, its photos first), so it can be done in a black spot and is recorded once. But a check can ground the truck, so the run can't start until the office has it and the phone has heard back: until then Today says "Your pre-start is kept on this phone. It goes as soon as there's signal, and then you can start." with **Send it now**. Starting already needs signal (step 3: the whole run is loaded at Start), so this adds no new need for signal; it only puts the check through the same door first. A refused check (say `checklist_changed`, or an odometer below the last reading) shows why, with **Do the check again** (it dismisses the refused one, pictures and all). Today's footer ("1 confirmation wasn't taken", "2 confirmations weren't taken. Open the run to see why.") leaves out the refused pre-start shown there, since its reason is already on screen.
5. **A failed critical item grounds the truck at once; a failed other item is kept and the truck may go.** `result` is `passed`, `failed` or `grounded`. A failed item needs a photo and a note (`422` without; a passed item takes neither). Photos reuse step 3's `PUT /v1/pod-files?kind=photo` and the queue; a photo is named by one thing only, a stop or a check item (`own_file` now checks both, with the picture's row locked, so a delivery and a pre-start naming one photo at once take turns and the second is refused). A later check that passes does **not** clear a grounding: only a fleet manager does. And the check that grounded the truck is never the run's pre-start: Start, the run's `prestart` field and **No pre-start yet.** count only a check of the run's truck that didn't ground it (`passed`, or `failed` on a non-critical item), made after the truck was last grounded and last marked fixed. So after **Mark fixed** the driver does a fresh check of the fixed truck before Start, and Today asks for one.
6. **Mark fixed: a `fixed` record by a fleet manager, with a note, naming the check.** `POST /v1/vehicles/{rego}/fix {check_id, note}` (`fleet:write`: `manager` and `admin`). It clears that check and every one before it. The `check_id` must be the latest check that grounded the truck, so a second failed check that came in while the fleet manager was looking is `409 stale`, not cleared unseen (the same idea as step 2's `seen` on reorder). `409 not_grounded` when it isn't. The check is never changed; `GET /v1/prestarts/{id}` shows it with the record that cleared it.
7. **Rego and service are records, the schedule is on the vehicle.** `vehicle.service_every_months` and `service_every_km` (either, both or neither; set on `POST`/`PATCH /v1/vehicles`). `vehicle_record` rows: `rego` (`expires_on`), `service` (`done_on`, `odometer_km` optional), `odometer` (a reading put right), `fixed`; each with a required note (rule 3: a person clears it with a note), by whom and when. The newest record of a kind is the one that counts, so a typo is put right by a newer record and the old one stays. Nothing about a truck's standing is stored; it is worked out each time. Because the schedule sits on the vehicle, a `PATCH` could make an overdue truck stop being overdue without a note; so while its service is overdue (judged for today) a `PATCH` that clears or lengthens an interval, or changes its type, is `409 vehicle_overdue` ("Record the service first"), and a new type is refused while its rego has run out. A shorter interval, or the limits, may still change. The `PATCH` takes the planning lock like every other write about a truck.
8. **Due, judged for a day.** Rego: `due` when it runs out in 30 days or less, `overdue` from the day after it runs out (it is good on its expiry date). Service: due on `last service date + months` (calendar months, clamped to month end) and at `last service reading + km`, whichever comes first; `due` 30 days or 1,000 km out, `overdue` the day after the date or past the kilometres. Nothing recorded is `unknown` and **does not block** (otherwise every truck on an upgraded install stops the morning after; the README tells the business to record each truck's rego and last service). **Overdue is judged for the run's own day** when planning: a truck whose rego runs out on the 14th can go on the 14th's run but can't be planned for the 15th. Leaving (`depart`, `start`) is judged for the later of the run's day and today in UTC, so a run planned for an earlier day and never sent can't leave on a rego or service that has run out since. That also sidesteps the server's UTC clock for planning; the Fleet screen and Plan runs ask with the desktop's own date (`GET /v1/vehicles?date=`), as step 3's driver runs do.
9. **Kilometres are whole numbers (`Integer`), not decimals.** An odometer shows whole kilometres, intervals are whole kilometres, and integers keep comparisons and the phone's arithmetic plain (no BigInt decimals). Weights and volumes stay decimal strings.
10. **The odometer never goes back, unless a fleet manager says so.** The truck's reading is the highest from any pre-start or service since the latest `odometer` record (or that record's reading). A check below it is `422 odometer_km` ("61,200 km is less than TMS041's last reading, 61,208 km. Check the odometer and try again."); the phone shows the last reading and refuses a lower one before it is ever sent (**Finish check** stays off while it is lower, and the phone says why once the driver leaves the field or presses Go, not while they are still typing: "6" on the way to "61,208" is no mistake). A service recorded late with a lower reading can't drag it back. A typo upward (612,080) would make the service look overdue and block the truck, so a fleet manager can put the reading right (`POST /v1/vehicles/{rego}/odometer`, in the Fleet drawer as "The odometer put right"); readings before it no longer count. A replaced odometer is the same record.
11. **Rule 3, path by path.** Refused with `409 vehicle_grounded` or `vehicle_overdue` (with the message from `blocked`): `POST /v1/runs`, `PATCH /v1/runs/{id}` with a new truck, `assign`, `print` (planned runs only: a sheet would send someone out in it), `depart`, `start`. Still allowed: `unassign`, `reorder`, `cancel`, a new driver, and everything on a run already on the road (a truck grounded by a late check finishes its deliveries; its lane says so plainly). A planned run whose truck becomes grounded or overdue stays as it is: its lane says why in red, takes no more stops, can't print or leave, and offers **Another truck…** (a `PATCH`) or moving its stops off. Every write about a truck (checks, records, fixes, checklist changes) takes the planning lock, so a run can't leave on a truck grounded a moment before.
12. **Desktop scope.** The Fleet drawer is built to `Fleet.html`: stats (with an OVERDUE card added when any is), the table, and the aside showing the check that grounded the truck (else its last check) with each item, the driver's photos and notes, **Mark fixed** for a fleet manager only, three small forms for a fleet manager (**A new rego expiry**, **A service done**, **The odometer put right**) and the truck's history. Departures: the "who" chip, **Add a driver** and **Add a vehicle** are left out (vehicles, drivers, schedules and checklists stay API-only this step; Known limitations says so); the DRIVER column is today's run's driver. Photos are fetched with the session token and shown from `blob:` URLs, so the desktop's CSP gains `img-src 'self' blob:` (the Caddyfile and both README snippets).
13. **Plan runs and the map.** A blocked truck without a run that day takes the unavailable slot, "Grounded" or "Overdue" with the API's message; it isn't offered for a new run or counted free. The map's truck and "What just happened" feed are **deferred to step 5**: the map is a static picture until live location draws trucks, and there is no feed to put a line in. Instead, cheaply, the map's **Fleet** button carries a red count of trucks that can't go today.
14. **Phone copy and small departures from `DriverPrestart.html`.** The title stays "Today's run" (the screen still lists the day's runs); the checklist shows below the picker once a run is chosen and has no check. "Last 61,208 km" under **Odometer**. A failed critical item says "A critical item: the truck can't go until a fleet manager marks it fixed. The office sees it as soon as it's sent." After the check: "Pre-start done 6:52 am: passed." (or "an item failed, and the office will see it"). A grounded truck: the API's message and "Don't take it out: call the office.", the radio reading "Grounded · R-0007", no **Start run**. No spare trucks (as step 3).
15. **`vehicle.due` is deferred**, as step 3 deferred its events: Simple TMS has no subscriber mechanism, and nothing consumes it yet. Due dates are on screen only.

### Ports and addresses, locally

Simple TMS API `127.0.0.1:8022`, Caddy `https://localhost:8543` (desktop at `/`, driver app at `/driver/`), database `127.0.0.1:5434` on migration `0004` before this step. Users `leighton` (admin, `a long enough password`) and `dee` (dispatcher, `dispatch password`); driver `kim`, PIN `2468`; vehicles `TMS041` (rigid), `TMS052` and `TMS053` (vans); run R-0001 today, done. Simple WMS at `127.0.0.1:8011`; TMS's `.env` has a `simple-tms-ship` key.

---

## Before you start

```
cd "/Users/leightonlauton/Q7 Technology/Software/q7/simple-tms"
git switch main && git pull --ff-only
git switch -c step-4-fleet-care
docker compose up -d db                      # the tests' PostgreSQL on 127.0.0.1:5434
cd api && .venv/bin/pytest -q                # expect: 244 passed
cd ../apps/desktop && npm test               # expect: 54 passed
cd ../driver && npm test                     # expect: 76 passed
```

The plan has four parts: **A**, the API (Tasks 1–8); **B**, the driver app (Tasks 9–10); **C**, the desktop (Tasks 11–13); **D**, the docs and the checks, ending with the manual check on the local stacks (Tasks 14–16). Parts B and C stub the API in their tests, so they don't wait on Part A except for the manual check. `pytest` runs from `api/`, `npm` from the app's folder. Code is given whole for a new file and as a unified diff for a changed one (apply it by hand, or save it and `git apply`). Commit after each task as shown, on the branch.

## Part A — the API

### Task 1: The fleet tables, kept append-only, with the design's checklists

**Files:**
- Modify: `api/tms/models/fleet.py` (`Vehicle`'s service schedule; `ChecklistItem`, `PrestartCheck`, `PrestartItem`, `VehicleRecord`)
- Modify: `api/tms/models/__init__.py`
- Create: `api/alembic/versions/0005_fleet_care.py`
- Modify: `api/tests/conftest.py` (the checklists put back after each test's truncate)
- Test: `api/tests/test_migrations.py`, `api/tests/test_fleet_records.py`

**Step 1: Write the failing tests**

Change `api/tests/conftest.py`:
```diff
--- a/api/tests/conftest.py
+++ b/api/tests/conftest.py
@@ -66,6 +66,23 @@ def db(engine):
         with engine.begin() as conn:
             # TRUNCATE is not UPDATE or DELETE, so the append-only trigger lets it by
             conn.execute(text(f"truncate {tables} restart identity cascade"))
+            seed_checklists(conn)
+
+
+# The checklist migration 0005 gives a fresh install, put back after each
+# test as it was: van items 1-6, rigid 7-12, ute 13-18. (label, critical)
+DEFAULT_CHECKLIST = [("Tyres", False), ("Lights", True), ("Brakes", True), ("Mirrors", False),
+                     ("Load restraints", False), ("Damage", False)]
+
+
+def seed_checklists(conn):
+    conn.execute(text("delete from checklist_item"))
+    conn.execute(text("alter sequence checklist_item_id_seq restart"))
+    conn.execute(
+        text("insert into checklist_item (vehicle_type, position, label, critical) "
+             "values (:t, :p, :l, :c)"),
+        [{"t": t, "p": i, "l": label, "c": critical} for t in ("van", "rigid", "ute")
+         for i, (label, critical) in enumerate(DEFAULT_CHECKLIST, start=1)])
 
 
 @pytest.fixture
```

Create `api/tests/test_fleet_records.py`:
```python
"""Pre-start checks and what a fleet manager records are kept as they were
sent: never changed, never deleted (rule 1)."""
import uuid
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from runs_samples import DAY, driver, vehicle
from tms.models import ChecklistItem, PrestartCheck, PrestartItem, Run, VehicleRecord


def test_a_fresh_install_has_the_designs_checklist_for_each_type(db):
    rows = db.execute(select(ChecklistItem).where(ChecklistItem.retired_at.is_(None))
                      .order_by(ChecklistItem.id)).scalars().all()
    assert {r.vehicle_type for r in rows} == {"van", "rigid", "ute"}
    rigid = [(r.label, r.critical) for r in rows if r.vehicle_type == "rigid"]
    assert rigid == [("Tyres", False), ("Lights", True), ("Brakes", True), ("Mirrors", False),
                     ("Load restraints", False), ("Damage", False)]


def _a_check(db) -> PrestartCheck:
    v, d = vehicle(db), driver(db)
    run = Run(day=DAY, vehicle_id=v.id, driver_id=d.id, status="planned", created_by="dee")
    db.add(run)
    db.flush()
    now = datetime.now(UTC)
    check = PrestartCheck(message_id=uuid.uuid4(), run_id=run.id, vehicle_id=v.id, driver_id=d.id,
                          at=now, odometer_km=61208, result="passed", recorded_by="kim",
                          recorded_at=now)
    check.items.append(PrestartItem(position=1, checklist_item_id=1, label="Tyres",
                                    critical=False, passed=True))
    db.add(check)
    db.add(VehicleRecord(vehicle_id=v.id, kind="rego", expires_on=date(2027, 3, 3),
                         note="Renewed online", recorded_by="fleet.mgr", recorded_at=now))
    db.commit()
    return check


@pytest.mark.parametrize("table", ["prestart_check", "prestart_item", "vehicle_record"])
def test_a_check_or_a_record_cannot_be_changed_or_deleted(db, table):
    _a_check(db)
    column = {"prestart_check": "odometer_km = 1", "prestart_item": "passed = false",
              "vehicle_record": "note = 'changed'"}[table]
    for sql in (f"update {table} set {column}", f"delete from {table}"):
        with pytest.raises(DBAPIError, match="append-only"):
            db.execute(text(sql))
        db.rollback()
```

Change `api/tests/test_migrations.py`:
```diff
--- a/api/tests/test_migrations.py
+++ b/api/tests/test_migrations.py
@@ -13,6 +13,7 @@ MIGRATION_URL = TEST_DATABASE_URL.rsplit("/", 1)[0] + "/tms_migration_check"
 TABLES = {"user", "user_session", "driver", "vehicle", "consignment", "package",
           "consignment_event", "inbound_event", "run", "stop", "outbound_call", "print_job",
           "api_key", "inbound_message", "pod_file", "pod",
+          "checklist_item", "prestart_check", "prestart_item", "vehicle_record",
           "alembic_version"}
 
 
@@ -57,7 +58,8 @@ def test_migrations_go_up_down_and_up_again(blank_database):
     with engine.connect() as conn:
         triggers = conn.execute(text(
             "select tgname from pg_trigger where not tgisinternal")).scalars().all()
-    assert "consignment_event_append_only" in triggers
+    assert {"consignment_event_append_only", "prestart_check_append_only",
+            "prestart_item_append_only", "vehicle_record_append_only"} <= set(triggers)
 
     command.downgrade(cfg, "base")
     assert _tables(engine) == {"alembic_version"}
```

`seed_checklists` puts the checklist back after each test's `truncate … restart identity`, as migration 0005 gives a fresh install, so every test can rely on van items 1–6, rigid 7–12 and ute 13–18.

**Step 2: Run them to see them fail**

Run: `.venv/bin/pytest tests/test_migrations.py tests/test_fleet_records.py -q`
Expected: errors: `ImportError: cannot import name 'ChecklistItem' from 'tms.models'` (and the `checklist_item_id_seq` the conftest restarts doesn't exist yet).

**Step 3: Implement**

Create `api/alembic/versions/0005_fleet_care.py`:
```python
"""fleet care: pre-start checklists and checks, records a fleet manager keeps,
and how often each truck is serviced

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-12
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# The design's checklist, the same for each type to start with; a fleet
# manager changes it with PUT /v1/checklists/{type}. (label, critical)
DEFAULT_CHECKLIST = [("Tyres", False), ("Lights", True), ("Brakes", True), ("Mirrors", False),
                     ("Load restraints", False), ("Damage", False)]
APPEND_ONLY = ("prestart_check", "prestart_item", "vehicle_record")


def upgrade() -> None:
    op.add_column("vehicle", sa.Column("service_every_months", sa.Integer(), nullable=True))
    op.add_column("vehicle", sa.Column("service_every_km", sa.Integer(), nullable=True))

    op.create_table(
        "checklist_item",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("vehicle_type", sa.String(length=8), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(length=80), nullable=False),
        sa.Column("critical", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
                  nullable=False),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_checklist_item")),
    )
    op.create_index(op.f("ix_checklist_item_vehicle_type"), "checklist_item", ["vehicle_type"])
    items = sa.table("checklist_item", sa.column("vehicle_type", sa.String),
                     sa.column("position", sa.Integer), sa.column("label", sa.String),
                     sa.column("critical", sa.Boolean))
    op.bulk_insert(items, [
        {"vehicle_type": t, "position": i, "label": label, "critical": critical}
        for t in ("van", "rigid", "ute")
        for i, (label, critical) in enumerate(DEFAULT_CHECKLIST, start=1)])

    op.create_table(
        "prestart_check",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("vehicle_id", sa.Integer(), nullable=False),
        sa.Column("driver_id", sa.Integer(), nullable=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("odometer_km", sa.Integer(), nullable=False),
        sa.Column("result", sa.String(length=16), nullable=False),
        sa.Column("recorded_by", sa.String(length=64), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["run.id"], name=op.f("fk_prestart_check_run_id_run")),
        sa.ForeignKeyConstraint(["vehicle_id"], ["vehicle.id"],
                                name=op.f("fk_prestart_check_vehicle_id_vehicle")),
        sa.ForeignKeyConstraint(["driver_id"], ["driver.id"],
                                name=op.f("fk_prestart_check_driver_id_driver")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_prestart_check")),
        sa.UniqueConstraint("message_id", name=op.f("uq_prestart_check_message_id")),
    )
    op.create_index(op.f("ix_prestart_check_run_id"), "prestart_check", ["run_id"])
    op.create_index(op.f("ix_prestart_check_vehicle_id"), "prestart_check", ["vehicle_id"])

    op.create_table(
        "prestart_item",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("check_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("checklist_item_id", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(length=80), nullable=False),
        sa.Column("critical", sa.Boolean(), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column("photo_file_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(["check_id"], ["prestart_check.id"],
                                name=op.f("fk_prestart_item_check_id_prestart_check")),
        sa.ForeignKeyConstraint(["checklist_item_id"], ["checklist_item.id"],
                                name=op.f("fk_prestart_item_checklist_item_id_checklist_item")),
        sa.ForeignKeyConstraint(["photo_file_id"], ["pod_file.id"],
                                name=op.f("fk_prestart_item_photo_file_id_pod_file")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_prestart_item")),
        sa.UniqueConstraint("photo_file_id", name=op.f("uq_prestart_item_photo_file_id")),
    )
    op.create_index(op.f("ix_prestart_item_check_id"), "prestart_item", ["check_id"])

    op.create_table(
        "vehicle_record",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("vehicle_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("check_id", sa.Integer(), nullable=True),
        sa.Column("done_on", sa.Date(), nullable=True),
        sa.Column("expires_on", sa.Date(), nullable=True),
        sa.Column("odometer_km", sa.Integer(), nullable=True),
        sa.Column("note", sa.String(length=500), nullable=False),
        sa.Column("recorded_by", sa.String(length=64), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["vehicle_id"], ["vehicle.id"],
                                name=op.f("fk_vehicle_record_vehicle_id_vehicle")),
        sa.ForeignKeyConstraint(["check_id"], ["prestart_check.id"],
                                name=op.f("fk_vehicle_record_check_id_prestart_check")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_vehicle_record")),
    )
    op.create_index(op.f("ix_vehicle_record_vehicle_id"), "vehicle_record", ["vehicle_id"])

    # kept as they were sent, like a consignment's history (block_change is 0001's)
    for table in APPEND_ONLY:
        op.execute(f"create trigger {table}_append_only before update or delete on {table} "
                   "for each row execute function block_change()")


def downgrade() -> None:
    for table in APPEND_ONLY:
        op.execute(f"drop trigger if exists {table}_append_only on {table}")
    op.drop_index(op.f("ix_vehicle_record_vehicle_id"), table_name="vehicle_record")
    op.drop_table("vehicle_record")
    op.drop_index(op.f("ix_prestart_item_check_id"), table_name="prestart_item")
    op.drop_table("prestart_item")
    op.drop_index(op.f("ix_prestart_check_vehicle_id"), table_name="prestart_check")
    op.drop_index(op.f("ix_prestart_check_run_id"), table_name="prestart_check")
    op.drop_table("prestart_check")
    op.drop_index(op.f("ix_checklist_item_vehicle_type"), table_name="checklist_item")
    op.drop_table("checklist_item")
    op.drop_column("vehicle", "service_every_km")
    op.drop_column("vehicle", "service_every_months")
```

Change `api/tms/models/__init__.py`:
```diff
--- a/api/tms/models/__init__.py
+++ b/api/tms/models/__init__.py
@@ -1,6 +1,6 @@
 from tms.models.base import Base
 from tms.models.access import ApiKey, Driver, User, UserSession
-from tms.models.fleet import Vehicle
+from tms.models.fleet import ChecklistItem, PrestartCheck, PrestartItem, Vehicle, VehicleRecord
 from tms.models.consignment import Consignment, ConsignmentEvent, Package
 from tms.models.integration import InboundEvent, OutboundCall, PrintJob
 from tms.models.runs import Run, Stop
@@ -9,5 +9,6 @@ from tms.models.delivery import InboundMessage, Pod, PodFile
 __all__ = [
     "Base", "User", "UserSession", "Driver", "Vehicle", "Consignment", "Package",
     "ConsignmentEvent", "InboundEvent", "Run", "Stop", "OutboundCall", "PrintJob", "ApiKey",
-    "InboundMessage", "Pod", "PodFile",
+    "InboundMessage", "Pod", "PodFile", "ChecklistItem", "PrestartCheck", "PrestartItem",
+    "VehicleRecord",
 ]
```

Change `api/tms/models/fleet.py`:
```diff
--- a/api/tms/models/fleet.py
+++ b/api/tms/models/fleet.py
@@ -1,9 +1,10 @@
 from __future__ import annotations
 
-from datetime import datetime
+import uuid
+from datetime import date, datetime
 
-from sqlalchemy import Boolean, String
-from sqlalchemy.orm import Mapped, mapped_column
+from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Uuid
+from sqlalchemy.orm import Mapped, mapped_column, relationship
 
 from tms.models.base import Base, Dec, created_at_column
 
@@ -20,4 +21,93 @@ class Vehicle(Base):
     max_kg: Mapped[Dec]
     max_m3: Mapped[Dec]
     active: Mapped[bool] = mapped_column(Boolean, default=True)
+    # how often it is serviced: every so many months, kilometres, or both
+    # (whichever comes first); none, and the service is never due
+    service_every_months: Mapped[int | None] = mapped_column(Integer)
+    service_every_km: Mapped[int | None] = mapped_column(Integer)
     created_at: Mapped[datetime] = created_at_column()
+
+
+class ChecklistItem(Base):
+    """One line of the pre-start checklist for a type of vehicle. A critical
+    item that fails grounds the truck. Changed by retiring the line and
+    adding another, never deleted; each check keeps its own copy of what it
+    asked."""
+
+    __tablename__ = "checklist_item"
+
+    id: Mapped[int] = mapped_column(primary_key=True)
+    # van, rigid, ute
+    vehicle_type: Mapped[str] = mapped_column(String(8), index=True)
+    position: Mapped[int] = mapped_column(Integer)
+    label: Mapped[str] = mapped_column(String(80))
+    critical: Mapped[bool] = mapped_column(Boolean, default=False)
+    created_at: Mapped[datetime] = created_at_column()
+    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
+
+
+class PrestartCheck(Base):
+    """A driver's pre-start check of the truck for a run, kept as it was sent,
+    for good: a trigger refuses UPDATE and DELETE. A critical item failed
+    grounds the truck until a fleet manager marks it fixed (a VehicleRecord)."""
+
+    __tablename__ = "prestart_check"
+
+    id: Mapped[int] = mapped_column(primary_key=True)
+    message_id: Mapped[uuid.UUID] = mapped_column(Uuid, unique=True)
+    run_id: Mapped[int] = mapped_column(ForeignKey("run.id"), index=True)
+    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicle.id"), index=True)
+    # the driver who did it; none when a system sent it with a key
+    driver_id: Mapped[int | None] = mapped_column(ForeignKey("driver.id"))
+    # when it was done, by the phone's clock (never later than it arrived)
+    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
+    odometer_km: Mapped[int] = mapped_column(Integer)
+    # passed, failed (a non-critical item: the truck may go), grounded
+    result: Mapped[str] = mapped_column(String(16))
+    recorded_by: Mapped[str] = mapped_column(String(64))
+    # when it arrived, taken under the planning lock: what came first
+    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
+
+    items: Mapped[list[PrestartItem]] = relationship(
+        order_by="PrestartItem.position", cascade="all", lazy="selectin")
+
+
+class PrestartItem(Base):
+    """One answer on a check, with the item as the checklist had it then.
+    A failed item has a note and a photo."""
+
+    __tablename__ = "prestart_item"
+
+    id: Mapped[int] = mapped_column(primary_key=True)
+    check_id: Mapped[int] = mapped_column(ForeignKey("prestart_check.id"), index=True)
+    position: Mapped[int] = mapped_column(Integer)
+    checklist_item_id: Mapped[int] = mapped_column(ForeignKey("checklist_item.id"))
+    label: Mapped[str] = mapped_column(String(80))
+    critical: Mapped[bool] = mapped_column(Boolean)
+    passed: Mapped[bool] = mapped_column(Boolean)
+    note: Mapped[str | None] = mapped_column(String(500))
+    photo_file_id: Mapped[uuid.UUID | None] = mapped_column(
+        ForeignKey("pod_file.id"), unique=True)
+
+
+class VehicleRecord(Base):
+    """What a fleet manager records about a truck, each with a note: `fixed`
+    (what a failed pre-start found is put right), `service` (done on a day,
+    perhaps at a reading), `rego` (the new expiry) and `odometer` (a reading
+    put right). Append-only, like the checks: a mistake is put right by a
+    newer record, never by changing this one."""
+
+    __tablename__ = "vehicle_record"
+
+    id: Mapped[int] = mapped_column(primary_key=True)
+    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicle.id"), index=True)
+    # fixed, service, rego, odometer
+    kind: Mapped[str] = mapped_column(String(16))
+    # fixed: the latest failed check it clears (and every one before it)
+    check_id: Mapped[int | None] = mapped_column(ForeignKey("prestart_check.id"))
+    done_on: Mapped[date | None] = mapped_column(Date)
+    expires_on: Mapped[date | None] = mapped_column(Date)
+    odometer_km: Mapped[int | None] = mapped_column(Integer)
+    note: Mapped[str] = mapped_column(String(500))
+    recorded_by: Mapped[str] = mapped_column(String(64))
+    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
```

There is no foreign key from a check to the checklist line beyond `prestart_item.checklist_item_id`, and none back from `pod_file`: no cycle, so `Base.metadata.sorted_tables` (the truncate) stays quiet. `recorded_at` is set by the API after it takes the planning lock (Task 4), so it orders checks and records by when they arrived.

**Step 4: Run the tests**

Run: `.venv/bin/pytest -q`
Expected: `248 passed`.

**Step 5: Commit**

```bash
git add api/tms/models/fleet.py api/tms/models/__init__.py api/alembic/versions/0005_fleet_care.py \
        api/tests/conftest.py api/tests/test_migrations.py api/tests/test_fleet_records.py
git commit -m "Keep pre-start checks and fleet records append-only, with the design's checklists"
```

### Task 2: Each type's checklist, changed by a fleet manager

**Files:**
- Create: `api/tms/services/checklists.py`
- Create: `api/tms/api/routes/fleet.py`
- Modify: `api/tms/api/app.py`
- Test: `api/tests/test_checklists.py`

**Step 1: Write the failing tests**

Create `api/tests/test_checklists.py`:
```python
"""The pre-start checklist for each type: the design's to start with, and a
fleet manager's own after that. A line that goes is retired, never deleted."""
from sqlalchemy import select

from deliveries_samples import driver_headers
from runs_samples import driver
from tms.models import ChecklistItem

VAN = [{"label": "Tyres", "critical": False}, {"label": "Lights", "critical": True},
       {"label": "Brakes", "critical": True}, {"label": "Mirrors", "critical": False},
       {"label": "Load restraints", "critical": False}, {"label": "Damage", "critical": False}]


def test_anyone_who_sees_the_fleet_sees_the_checklists(client, db, dispatcher_headers):
    driver(db)
    for headers in (dispatcher_headers, driver_headers(client)):
        r = client.get("/v1/checklists", headers=headers)
        assert r.status_code == 200, r.text
        assert list(r.json()) == ["van", "rigid", "ute"]
        assert [{"label": i["label"], "critical": i["critical"]} for i in r.json()["van"]] == VAN
    assert [i["id"] for i in r.json()["rigid"]] == [7, 8, 9, 10, 11, 12]


def test_a_fleet_manager_changes_a_types_list_and_what_goes_is_retired(client, db,
                                                                       manager_headers):
    new = [{"label": "Brakes", "critical": True}, {"label": "Tyres", "critical": False},
           {"label": "Tail lift", "critical": True}]
    r = client.put("/v1/checklists/van", headers=manager_headers, json={"items": new})
    assert r.status_code == 200, r.text
    # the same line keeps its id, in its new place; a new line gets a new one
    assert r.json() == [{"id": 3, "label": "Brakes", "critical": True},
                        {"id": 1, "label": "Tyres", "critical": False},
                        {"id": 19, "label": "Tail lift", "critical": True}]
    # Lights was critical and isn't now: a different line
    r = client.put("/v1/checklists/van", headers=manager_headers,
                   json={"items": [{"label": "Lights"}]})
    assert r.json() == [{"id": 20, "label": "Lights", "critical": False}]
    rows = db.execute(select(ChecklistItem).where(ChecklistItem.vehicle_type == "van")
                      .order_by(ChecklistItem.id)).scalars().all()
    assert len(rows) == 8 and [r.id for r in rows if r.retired_at is None] == [20]
    # the other types are as they were
    assert len(client.get("/v1/checklists", headers=manager_headers).json()["rigid"]) == 6


def test_only_a_fleet_manager_changes_a_list_and_it_must_make_sense(client, manager_headers,
                                                                    dispatcher_headers):
    body = {"items": [{"label": "Tyres"}]}
    assert client.put("/v1/checklists/van", headers=dispatcher_headers, json=body).status_code == 403
    for bad in ({"items": []}, {"items": [{"label": "Tyres"}, {"label": "tyres "}]},
                {"items": [{"label": " "}]}):
        assert client.put("/v1/checklists/van", headers=manager_headers, json=bad).status_code == 422
    assert client.put("/v1/checklists/bicycle", headers=manager_headers, json=body).status_code == 422
```

**Step 2: Run them to see them fail**

Run: `.venv/bin/pytest tests/test_checklists.py -q`
Expected: `3 failed`: `/v1/checklists` is `404`.

**Step 3: Implement**

Change `api/tms/api/app.py`:
```diff
--- a/api/tms/api/app.py
+++ b/api/tms/api/app.py
@@ -6,7 +6,8 @@ from sqlalchemy import text
 from tms.api import errors
 from tms.api.deps import DB
 from tms.api.routes import (
-    auth, consignments, deliveries, drivers, pod_files, printing, runs, vehicles, wms_events,
+    auth, consignments, deliveries, drivers, fleet, pod_files, printing, runs, vehicles,
+    wms_events,
 )
 
 logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
@@ -30,6 +31,7 @@ v1.include_router(runs.router)
 v1.include_router(printing.router)
 v1.include_router(deliveries.router)
 v1.include_router(pod_files.router)
+v1.include_router(fleet.router)
 
 
 @v1.get("/health", tags=["system"])
```

Create `api/tms/api/routes/fleet.py`:
```python
"""Fleet care: the pre-start checklists, the checks drivers send, and what a
fleet manager records about each truck. Checks and records are kept as they
were sent; a truck's standing is worked out from them."""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, field_validator

from tms.api.deps import DB, Principal, require
from tms.api.routes.vehicles import VehicleType
from tms.models import ChecklistItem
from tms.services import checklists

router = APIRouter(tags=["fleet"])


# ---- checklists ----------------------------------------------------------------

class ChecklistLine(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    label: str = Field(min_length=1, max_length=80)
    critical: bool = False


class ChecklistIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[ChecklistLine] = Field(min_length=1, max_length=30)

    @field_validator("items")
    @classmethod
    def _once_each(cls, items: list[ChecklistLine]) -> list[ChecklistLine]:
        labels = [i.label.casefold() for i in items]
        if len(labels) != len(set(labels)):
            raise ValueError("each item once: two have the same label")
        return items


class ChecklistItemOut(BaseModel):
    id: int
    label: str
    critical: bool


def item_out(i: ChecklistItem) -> ChecklistItemOut:
    return ChecklistItemOut(id=i.id, label=i.label, critical=i.critical)


@router.get("/checklists", response_model=dict[str, list[ChecklistItemOut]])
def get_checklists(db: DB, who: Principal = require("fleet:read")):
    """Each type's checklist, in order: { "van": [...], "rigid": [...], "ute": [...] }."""
    return {t: [item_out(i) for i in checklists.live(db, t)] for t in checklists.TYPES}


@router.put("/checklists/{vehicle_type}", response_model=list[ChecklistItemOut])
def put_checklist(vehicle_type: VehicleType, body: ChecklistIn, db: DB,
                  who: Principal = require("fleet:write")):
    """The whole list for one type, in order. Lines that go are retired."""
    rows = checklists.replace(db, vehicle_type, [(i.label, i.critical) for i in body.items])
    db.commit()
    return [item_out(i) for i in rows]
```

Create `api/tms/services/checklists.py`:
```python
"""The pre-start checklist for each type of vehicle. A fleet manager may
change it; a line that goes is retired, never deleted, and a line kept as it
was keeps its id, so a check a phone is still sending finds it. Each check
keeps its own copy of the lines it answered."""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from tms.models import ChecklistItem
from tms.services.runs import lock_planning

TYPES = ("van", "rigid", "ute")


def live(db: Session, vehicle_type: str) -> list[ChecklistItem]:
    return list(db.execute(
        select(ChecklistItem).where(ChecklistItem.vehicle_type == vehicle_type,
                                    ChecklistItem.retired_at.is_(None))
        .order_by(ChecklistItem.position, ChecklistItem.id)).scalars())


def replace(db: Session, vehicle_type: str, lines: list[tuple[str, bool]]) -> list[ChecklistItem]:
    """The list as given, in that order. A line with the same label and
    critical flag as before is the same line; the rest are retired."""
    # one change at a time, and a check being recorded sees one list or the other
    lock_planning(db)
    kept = {(i.label, i.critical): i for i in live(db, vehicle_type)}
    out = []
    for position, (label, critical) in enumerate(lines, start=1):
        row = kept.pop((label, critical), None)
        if row is None:
            row = ChecklistItem(vehicle_type=vehicle_type, label=label, critical=critical)
            db.add(row)
        row.position = position
        out.append(row)
    now = datetime.now(UTC)
    for row in kept.values():
        row.retired_at = now
    db.flush()
    return out
```

**Step 4: Run the tests**

Run: `.venv/bin/pytest -q`
Expected: `251 passed`.

**Step 5: Commit**

```bash
git add api/tms/services/checklists.py api/tms/api/routes/fleet.py api/tms/api/app.py \
        api/tests/test_checklists.py
git commit -m "Let a fleet manager change each type's pre-start checklist"
```

### Task 3: Where each truck stands: rego and service due, its odometer, grounded

**Files:**
- Create: `api/tms/services/fleet.py`
- Modify: `api/tms/api/routes/vehicles.py` (the schedule on `POST`/`PATCH`; the standing on every answer; `date=` on `GET`)
- Create: `api/tests/fleet_samples.py` (checks and records made straight in the database)
- Test: `api/tests/test_vehicle_standing.py`

**Step 1: Write the failing tests**

Create `api/tests/fleet_samples.py`:
```python
"""Checks and records made straight in the database, so a test of where a
truck stands starts from its history."""
import uuid
from datetime import UTC, date, datetime

from sqlalchemy import select

from runs_samples import DAY
from tms.models import Driver, PrestartCheck, PrestartItem, Run, Vehicle, VehicleRecord


def _run_for(db, v: Vehicle) -> Run:
    run = db.execute(select(Run).where(Run.vehicle_id == v.id)).scalars().first()
    if run is None:
        d = db.execute(select(Driver)).scalars().first()
        if d is None:
            d = Driver(code="tom", name="Tom", pin_hash="x", failed_attempts=0, locked=False,
                       active=True)
            db.add(d)
            db.flush()
        run = Run(day=DAY, vehicle_id=v.id, driver_id=d.id, status="done", created_by="dee")
        db.add(run)
        db.flush()
    return run


def check(db, v: Vehicle, km: int, failed: tuple[str, ...] = (), by="tom") -> PrestartCheck:
    """A pre-start of the design's six items, with these failed (Lights and
    Brakes are critical)."""
    labels = [("Tyres", False), ("Lights", True), ("Brakes", True), ("Mirrors", False),
              ("Load restraints", False), ("Damage", False)]
    critical = any(c for label, c in labels if label in failed)
    now = datetime.now(UTC)
    c = PrestartCheck(message_id=uuid.uuid4(), run_id=_run_for(db, v).id, vehicle_id=v.id,
                      at=now, odometer_km=km, recorded_by=by, recorded_at=now,
                      result="grounded" if critical else "failed" if failed else "passed")
    for i, (label, crit) in enumerate(labels, start=1):
        c.items.append(PrestartItem(position=i, checklist_item_id=i, label=label, critical=crit,
                                    passed=label not in failed,
                                    note="Not right" if label in failed else None))
    db.add(c)
    db.commit()
    return c


def record(db, v: Vehicle, kind: str, note="Done", **fields) -> VehicleRecord:
    r = VehicleRecord(vehicle_id=v.id, kind=kind, note=note, recorded_by="fleet.mgr",
                      recorded_at=datetime.now(UTC), **fields)
    db.add(r)
    db.commit()
    return r


def serviced(db, v: Vehicle, on: date, km: int | None = None) -> VehicleRecord:
    return record(db, v, "service", done_on=on, odometer_km=km)
```

Create `api/tests/test_vehicle_standing.py`:
```python
"""Where a truck stands: rego and service due (amber 30 days or 1,000 km
out, red when overdue), its odometer, and grounded by a failed pre-start."""
from datetime import date

import pytest

from fleet_samples import check, record, serviced
from runs_samples import vehicle
from tms.services.fleet import add_months


@pytest.mark.parametrize("start,months,end", [
    (date(2026, 7, 14), 6, date(2027, 1, 14)),
    (date(2026, 8, 31), 6, date(2027, 2, 28)),
    (date(2027, 8, 31), 6, date(2028, 2, 29)),
    (date(2026, 10, 14), 12, date(2027, 10, 14)),
])
def test_months_are_added_by_the_calendar(start, months, end):
    assert add_months(start, months) == end


def _get(client, headers, day="2026-10-14", rego="TMS041"):
    r = client.get(f"/v1/vehicles/{rego}", headers=headers, params={"date": day})
    assert r.status_code == 200, r.text
    return r.json()


def test_a_truck_with_nothing_recorded_is_free_to_go(client, db, manager_headers):
    r = client.post("/v1/vehicles", headers=manager_headers, json={
        "rego": "TMS052", "type": "van", "max_kg": "1200", "max_m3": "9",
        "service_every_months": 6, "service_every_km": 15000})
    assert r.status_code == 201, r.text
    out = r.json()
    assert (out["service_every_months"], out["service_every_km"]) == (6, 15000)
    assert out["odometer_km"] is None and out["grounded"] is None
    assert out["last_prestart"] is None and out["blocked"] is None and out["state"] == "ok"
    assert out["rego_due"] == {"expires_on": None, "days_left": None, "state": "unknown"}
    assert out["service_due"]["state"] == "unknown"
    r = client.patch("/v1/vehicles/TMS052", headers=manager_headers, json={"service_every_km": 20000})
    assert r.json()["service_every_km"] == 20000 and r.json()["service_every_months"] == 6


def test_rego_is_amber_a_month_out_and_red_once_it_has_run_out(client, db, dispatcher_headers):
    v = vehicle(db)
    record(db, v, "rego", expires_on=date(2026, 10, 25))
    assert _get(client, dispatcher_headers, "2026-09-24")["rego_due"]["state"] == "ok"
    soon = _get(client, dispatcher_headers, "2026-10-13")
    assert soon["rego_due"] == {"expires_on": "2026-10-25", "days_left": 12, "state": "due"}
    assert soon["state"] == "due" and soon["blocked"] is None
    # good on the day it runs out, overdue the day after
    assert _get(client, dispatcher_headers, "2026-10-25")["rego_due"]["state"] == "due"
    late = _get(client, dispatcher_headers, "2026-10-26")
    assert late["state"] == "overdue"
    assert late["blocked"] == {"code": "vehicle_overdue", "message":
                               "TMS041's rego runs out on 25 Oct 2026. Record the new expiry "
                               "in Fleet first."}
    # a newer record puts it right; the old one stays
    record(db, v, "rego", expires_on=date(2027, 10, 25))
    assert _get(client, dispatcher_headers, "2026-10-26")["state"] == "ok"


def test_service_is_due_by_date_or_kilometres_whichever_comes_first(client, db, manager_headers):
    v = vehicle(db)
    client.patch("/v1/vehicles/TMS041", headers=manager_headers,
                 json={"service_every_months": 6, "service_every_km": 15000})
    serviced(db, v, date(2026, 4, 30), km=46900)
    check(db, v, km=50000)
    out = _get(client, manager_headers)
    assert out["odometer_km"] == 50000
    assert out["service_due"] == {"last_on": "2026-04-30", "last_km": 46900,
                                  "due_on": "2026-10-30", "due_km": 61900, "days_left": 16,
                                  "km_left": 11900, "state": "due"}
    check(db, v, km=61208)
    assert _get(client, manager_headers, "2026-09-01")["service_due"]["km_left"] == 692
    assert _get(client, manager_headers, "2026-09-01")["service_due"]["state"] == "due"
    check(db, v, km=62000)
    over = _get(client, manager_headers, "2026-09-01")
    assert over["state"] == "overdue" and over["blocked"]["message"] == (
        "TMS041's service was due at 61,900 km and it has done 62,000. "
        "Record the service in Fleet first.")
    assert _get(client, manager_headers, "2026-11-02")["blocked"]["message"] == (
        "TMS041's service was due by 30 Oct 2026. Record the service in Fleet first.")
    serviced(db, v, date(2026, 10, 14), km=62010)
    assert _get(client, manager_headers, "2026-11-02")["state"] == "ok"


def test_the_odometer_only_goes_up_until_a_fleet_manager_puts_it_right(client, db,
                                                                      dispatcher_headers):
    v = vehicle(db)
    check(db, v, km=612080)                     # a slip of the finger
    serviced(db, v, date(2026, 10, 1), km=61000)  # recorded late: it can't take it back
    assert _get(client, dispatcher_headers)["odometer_km"] == 612080
    record(db, v, "odometer", odometer_km=61208, note="Typed one digit too many")
    assert _get(client, dispatcher_headers)["odometer_km"] == 61208
    check(db, v, km=61300)
    assert _get(client, dispatcher_headers)["odometer_km"] == 61300


def test_a_failed_critical_item_grounds_the_truck_until_it_is_marked_fixed(client, db,
                                                                         dispatcher_headers):
    v = vehicle(db)
    check(db, v, km=61000, failed=("Mirrors",))
    out = _get(client, dispatcher_headers)
    assert out["state"] == "ok" and out["last_prestart"]["result"] == "failed"
    bad = check(db, v, km=61010, failed=("Brakes", "Mirrors"))
    out = _get(client, dispatcher_headers)
    assert out["state"] == "grounded"
    assert out["grounded"] == {"check_id": bad.id, "at": out["grounded"]["at"], "failed": ["Brakes"]}
    assert out["blocked"] == {"code": "vehicle_grounded", "message":
                              "TMS041 is grounded: Brakes failed the pre-start. A fleet manager "
                              "has to mark it fixed first."}
    # a check that passes doesn't clear it: only a fleet manager does
    check(db, v, km=61020)
    assert _get(client, dispatcher_headers)["state"] == "grounded"
    record(db, v, "fixed", check_id=bad.id, note="New brake pads, tested")
    out = _get(client, dispatcher_headers)
    assert out["state"] == "ok" and out["grounded"] is None and out["blocked"] is None
```

**Step 2: Run them to see them fail**

Run: `.venv/bin/pytest tests/test_vehicle_standing.py -q`
Expected: collection error: `ModuleNotFoundError: No module named 'tms.services.fleet'`.

**Step 3: Implement**

Change `api/tms/api/routes/vehicles.py`:
```diff
--- a/api/tms/api/routes/vehicles.py
+++ b/api/tms/api/routes/vehicles.py
@@ -1,11 +1,12 @@
 """Vehicles: created, changed, deactivated. Never deleted."""
 from __future__ import annotations
 
+import datetime as dt
 from datetime import datetime
 from decimal import Decimal
 from typing import Literal
 
-from fastapi import APIRouter
+from fastapi import APIRouter, Query
 from fastapi.responses import JSONResponse
 from pydantic import BaseModel, ConfigDict, Field, field_validator
 from sqlalchemy import select
@@ -15,6 +16,7 @@ from tms.api.deps import DB, Principal, require
 from tms.api.errors import FieldError, NotFound, violated_constraint
 from tms.api.schemas import Dec, Page
 from tms.models import Vehicle
+from tms.services import fleet
 
 router = APIRouter(tags=["vehicles"])
 
@@ -32,6 +34,9 @@ class VehicleIn(BaseModel):
     type: VehicleType
     max_kg: Decimal = Field(gt=0, max_digits=12, decimal_places=3)
     max_m3: Decimal = Field(gt=0, max_digits=12, decimal_places=3)
+    # serviced every so many months and/or kilometres, whichever comes first
+    service_every_months: int | None = Field(default=None, ge=1, le=120)
+    service_every_km: int | None = Field(default=None, ge=1, le=1_000_000)
 
     @field_validator("rego", mode="before")
     @classmethod
@@ -44,6 +49,44 @@ class VehiclePatch(BaseModel):
     type: VehicleType | None = None
     max_kg: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=3)
     max_m3: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=3)
+    service_every_months: int | None = Field(default=None, ge=1, le=120)
+    service_every_km: int | None = Field(default=None, ge=1, le=1_000_000)
+
+
+class RegoDue(BaseModel):
+    expires_on: dt.date | None
+    days_left: int | None
+    # ok, due (30 days or less), overdue, unknown (no expiry recorded)
+    state: str
+
+
+class ServiceDue(BaseModel):
+    last_on: dt.date | None
+    last_km: int | None
+    due_on: dt.date | None
+    due_km: int | None
+    days_left: int | None
+    km_left: int | None
+    # ok, due (30 days or 1,000 km or less), overdue, unknown
+    state: str
+
+
+class Grounded(BaseModel):
+    check_id: int
+    at: datetime
+    failed: list[str]
+
+
+class CheckBrief(BaseModel):
+    id: int
+    at: datetime
+    result: str
+    by: str
+
+
+class Blocked(BaseModel):
+    code: str
+    message: str
 
 
 class VehicleOut(BaseModel):
@@ -54,11 +97,45 @@ class VehicleOut(BaseModel):
     max_m3: Dec
     active: bool
     created_at: datetime
-
-
-def vehicle_out(v: Vehicle) -> VehicleOut:
-    return VehicleOut(id=v.id, rego=v.rego, type=v.type, max_kg=v.max_kg, max_m3=v.max_m3,
-                      active=v.active, created_at=v.created_at)
+    service_every_months: int | None
+    service_every_km: int | None
+    odometer_km: int | None
+    rego_due: RegoDue
+    service_due: ServiceDue
+    # the check that grounded it, until a fleet manager marks it fixed
+    grounded: Grounded | None
+    last_prestart: CheckBrief | None
+    # grounded, overdue, due or ok, on the day asked about
+    state: str
+    # why it can't take a run that day, or null
+    blocked: Blocked | None
+
+
+def blocked_out(s: fleet.Standing) -> Blocked | None:
+    why = s.blocked
+    return None if why is None else Blocked(code=why.code, message=why.message)
+
+
+def vehicle_out(db, v: Vehicle, day: dt.date | None = None) -> VehicleOut:
+    s = fleet.standing(db, v, day or dt.date.today())
+    g, last = s.grounded_by, s.last_check
+    return VehicleOut(
+        id=v.id, rego=v.rego, type=v.type, max_kg=v.max_kg, max_m3=v.max_m3,
+        active=v.active, created_at=v.created_at, service_every_months=v.service_every_months,
+        service_every_km=v.service_every_km, odometer_km=s.odometer_km,
+        rego_due=RegoDue(expires_on=s.rego_expires_on, days_left=s.rego_days_left,
+                         state=s.rego_state),
+        service_due=ServiceDue(
+            last_on=s.last_service.done_on if s.last_service else None,
+            last_km=s.last_service.odometer_km if s.last_service else None,
+            due_on=s.service_due_on, due_km=s.service_due_km, days_left=s.service_days_left,
+            km_left=s.service_km_left, state=s.service_state),
+        grounded=None if g is None else Grounded(check_id=g.id, at=g.at,
+                                                 failed=fleet.failed_critical(g)),
+        last_prestart=None if last is None else CheckBrief(id=last.id, at=last.at,
+                                                           result=last.result,
+                                                           by=last.recorded_by),
+        state=s.state, blocked=blocked_out(s))
 
 
 def _get(db, rego: str) -> Vehicle:
@@ -73,7 +150,8 @@ def create_vehicle(body: VehicleIn, db: DB, who: Principal = require("fleet:writ
     if db.execute(select(Vehicle).where(Vehicle.rego == body.rego)).scalar_one_or_none():
         raise FieldError("rego", f"vehicle {body.rego} already exists")
     v = Vehicle(rego=body.rego, type=body.type, max_kg=body.max_kg, max_m3=body.max_m3,
-                active=True)
+                service_every_months=body.service_every_months,
+                service_every_km=body.service_every_km, active=True)
     db.add(v)
     try:
         db.commit()
@@ -83,21 +161,26 @@ def create_vehicle(body: VehicleIn, db: DB, who: Principal = require("fleet:writ
         if violated_constraint(e) == "uq_vehicle_rego":
             raise FieldError("rego", f"vehicle {body.rego} already exists") from None
         raise
-    return JSONResponse(status_code=201, content=vehicle_out(v).model_dump(mode="json"))
+    return JSONResponse(status_code=201, content=vehicle_out(db, v).model_dump(mode="json"))
 
 
 @router.get("/vehicles", response_model=Page[VehicleOut])
-def list_vehicles(db: DB, active: bool | None = None, who: Principal = require("fleet:read")):
+def list_vehicles(db: DB, active: bool | None = None,
+                  day: dt.date | None = Query(default=None, alias="date"),
+                  who: Principal = require("fleet:read")):
+    """`date` is the day due and overdue are judged for: the caller's today
+    (the server's is UTC). Left out, the server's."""
     q = select(Vehicle)
     if active is not None:
         q = q.where(Vehicle.active.is_(active))
     rows = db.execute(q.order_by(Vehicle.rego)).scalars().all()
-    return Page(items=[vehicle_out(v) for v in rows], total=len(rows))
+    return Page(items=[vehicle_out(db, v, day) for v in rows], total=len(rows))
 
 
 @router.get("/vehicles/{rego}", response_model=VehicleOut)
-def get_vehicle(rego: str, db: DB, who: Principal = require("fleet:read")):
-    return vehicle_out(_get(db, rego))
+def get_vehicle(rego: str, db: DB, day: dt.date | None = Query(default=None, alias="date"),
+                who: Principal = require("fleet:read")):
+    return vehicle_out(db, _get(db, rego), day)
 
 
 @router.patch("/vehicles/{rego}", response_model=VehicleOut)
@@ -106,7 +189,7 @@ def update_vehicle(rego: str, body: VehiclePatch, db: DB, who: Principal = requi
     for field, value in body.model_dump(exclude_none=True).items():
         setattr(v, field, value)
     db.commit()
-    return vehicle_out(v)
+    return vehicle_out(db, v)
 
 
 @router.post("/vehicles/{rego}/deactivate", response_model=VehicleOut)
@@ -115,7 +198,7 @@ def deactivate_vehicle(rego: str, db: DB, who: Principal = require("fleet:write"
     v = _get(db, rego)
     v.active = False
     db.commit()
-    return vehicle_out(v)
+    return vehicle_out(db, v)
 
 
 @router.post("/vehicles/{rego}/reactivate", response_model=VehicleOut)
@@ -123,4 +206,4 @@ def reactivate_vehicle(rego: str, db: DB, who: Principal = require("fleet:write"
     v = _get(db, rego)
     v.active = True
     db.commit()
-    return vehicle_out(v)
+    return vehicle_out(db, v)
```

Create `api/tms/services/fleet.py`:
```python
"""Where a truck stands: grounded by a failed pre-start, and when its rego
and service fall due. Worked out from the checks and records, never kept on
the truck itself, so the history is the truth.

Due means amber: 30 days or 1,000 km out. Overdue means red, and an overdue
truck can't take a run, any more than a grounded one (rule 3). Days are
judged for a given day: a run's own day when planning it, so a truck whose
rego runs out tomorrow can go today but not on tomorrow's run, whatever the
server's clock (UTC) says. Kilometres are whole numbers: an odometer shows
whole kilometres, and so does every interval."""
from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tms.api.errors import Conflict
from tms.models import PrestartCheck, Vehicle, VehicleRecord

DUE_DAYS = 30
DUE_KM = 1_000
WORST = {"unknown": 0, "ok": 0, "due": 1, "overdue": 2}


def add_months(d: date, months: int) -> date:
    """14 Jul plus 6 months is 14 Jan; 31 Aug plus 6 is the last day of February."""
    m = d.month - 1 + months
    year, month = d.year + m // 12, m % 12 + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def long_date(d: date) -> str:
    """3 Oct 2026"""
    return f"{d.day} {d.strftime('%b')} {d.year}"


def latest(db: Session, vehicle_id: int, kind: str) -> VehicleRecord | None:
    """The newest record of a kind: a newer one puts an older one right."""
    return db.execute(select(VehicleRecord).where(
        VehicleRecord.vehicle_id == vehicle_id, VehicleRecord.kind == kind)
        .order_by(VehicleRecord.id.desc()).limit(1)).scalar_one_or_none()


def odometer(db: Session, vehicle_id: int) -> int | None:
    """The truck's reading: the highest from a pre-start or a service since the
    last time a fleet manager put the reading right (or that reading, if
    higher). An odometer only goes up, so a service recorded late can't
    take it back."""
    put_right = latest(db, vehicle_id, "odometer")
    since = put_right.recorded_at if put_right else None
    checks = select(func.max(PrestartCheck.odometer_km)).where(
        PrestartCheck.vehicle_id == vehicle_id)
    services = select(func.max(VehicleRecord.odometer_km)).where(
        VehicleRecord.vehicle_id == vehicle_id, VehicleRecord.kind == "service")
    if since is not None:
        checks = checks.where(PrestartCheck.recorded_at > since)
        services = services.where(VehicleRecord.recorded_at > since)
    readings = [db.execute(checks).scalar(), db.execute(services).scalar(),
                put_right.odometer_km if put_right else None]
    known = [r for r in readings if r is not None]
    return max(known) if known else None


def grounding(db: Session, vehicle_id: int) -> PrestartCheck | None:
    """The latest check with a failed critical item that no fleet manager has
    marked fixed since. A `fixed` record clears its check and every one before."""
    fixed_up_to = db.execute(select(func.max(VehicleRecord.check_id)).where(
        VehicleRecord.vehicle_id == vehicle_id, VehicleRecord.kind == "fixed")).scalar() or 0
    return db.execute(select(PrestartCheck).where(
        PrestartCheck.vehicle_id == vehicle_id, PrestartCheck.result == "grounded",
        PrestartCheck.id > fixed_up_to).order_by(PrestartCheck.id.desc()).limit(1)
    ).scalar_one_or_none()


def last_check(db: Session, vehicle_id: int) -> PrestartCheck | None:
    return db.execute(select(PrestartCheck).where(PrestartCheck.vehicle_id == vehicle_id)
                      .order_by(PrestartCheck.id.desc()).limit(1)).scalar_one_or_none()


def failed_critical(check: PrestartCheck) -> list[str]:
    return [i.label for i in check.items if i.critical and not i.passed]


def _by_days(left: int | None) -> str:
    if left is None:
        return "unknown"
    return "overdue" if left < 0 else "due" if left <= DUE_DAYS else "ok"


def _by_km(left: int | None) -> str:
    if left is None:
        return "unknown"
    return "overdue" if left < 0 else "due" if left <= DUE_KM else "ok"


@dataclass
class Standing:
    """A truck as things stand, for a day."""

    vehicle: Vehicle
    day: date
    odometer_km: int | None
    grounded_by: PrestartCheck | None
    last_check: PrestartCheck | None
    rego_expires_on: date | None
    last_service: VehicleRecord | None

    @property
    def rego_days_left(self) -> int | None:
        return None if self.rego_expires_on is None else (self.rego_expires_on - self.day).days

    @property
    def rego_state(self) -> str:
        return _by_days(self.rego_days_left)

    @property
    def service_due_on(self) -> date | None:
        months = self.vehicle.service_every_months
        if not months or self.last_service is None:
            return None
        return add_months(self.last_service.done_on, months)

    @property
    def service_due_km(self) -> int | None:
        km = self.vehicle.service_every_km
        if not km or self.last_service is None or self.last_service.odometer_km is None:
            return None
        return self.last_service.odometer_km + km

    @property
    def service_days_left(self) -> int | None:
        due = self.service_due_on
        return None if due is None else (due - self.day).days

    @property
    def service_km_left(self) -> int | None:
        due = self.service_due_km
        return None if due is None or self.odometer_km is None else due - self.odometer_km

    @property
    def service_state(self) -> str:
        by_date, by_km = _by_days(self.service_days_left), _by_km(self.service_km_left)
        if by_date == by_km == "unknown":
            return "unknown"
        return max(by_date, by_km, key=WORST.__getitem__)

    @property
    def state(self) -> str:
        """grounded, overdue (red), due (amber) or ok."""
        if self.grounded_by is not None:
            return "grounded"
        worst = max(self.rego_state, self.service_state, key=WORST.__getitem__)
        return worst if worst in ("overdue", "due") else "ok"

    @property
    def blocked(self) -> Conflict | None:
        """Why it can't take a run on its day, or None. Grounded first, then
        rego, then service; each says what puts it right."""
        rego = self.vehicle.rego
        if self.grounded_by is not None:
            items = " and ".join(failed_critical(self.grounded_by)) or "a critical item"
            return Conflict("vehicle_grounded",
                            f"{rego} is grounded: {items} failed the pre-start. "
                            "A fleet manager has to mark it fixed first.")
        if self.rego_state == "overdue":
            return Conflict("vehicle_overdue",
                            f"{rego}'s rego runs out on {long_date(self.rego_expires_on)}. "
                            "Record the new expiry in Fleet first.")
        if _by_days(self.service_days_left) == "overdue":
            return Conflict("vehicle_overdue",
                            f"{rego}'s service was due by {long_date(self.service_due_on)}. "
                            "Record the service in Fleet first.")
        if _by_km(self.service_km_left) == "overdue":
            return Conflict("vehicle_overdue",
                            f"{rego}'s service was due at {self.service_due_km:,} km and it "
                            f"has done {self.odometer_km:,}. Record the service in Fleet first.")
        return None


def standing(db: Session, vehicle: Vehicle, day: date) -> Standing:
    rego = latest(db, vehicle.id, "rego")
    return Standing(vehicle=vehicle, day=day, odometer_km=odometer(db, vehicle.id),
                    grounded_by=grounding(db, vehicle.id), last_check=last_check(db, vehicle.id),
                    rego_expires_on=rego.expires_on if rego else None,
                    last_service=latest(db, vehicle.id, "service"))


def refuse_if_blocked(db: Session, vehicle: Vehicle, day: date) -> None:
    """Rule 3: a grounded or overdue truck does not go on a run."""
    why = standing(db, vehicle, day).blocked
    if why is not None:
        raise why
```

`fleet.py` imports nothing from `services/runs.py`, so `runs.py` can import it in Task 7 without a cycle; the writes that need the planning lock live in `services/records.py` (Task 4).

**Step 4: Run the tests**

Run: `.venv/bin/pytest -q`
Expected: `260 passed` (`test_vehicles.py` passes unchanged: it checks fields one by one).

**Step 5: Commit**

```bash
git add api/tms/services/fleet.py api/tms/api/routes/vehicles.py api/tests/fleet_samples.py \
        api/tests/test_vehicle_standing.py
git commit -m "Say where each truck stands: rego and service due, its odometer, grounded"
```

### Task 4: A new rego expiry, a service, an odometer put right; and the history

**Files:**
- Create: `api/tms/services/records.py`
- Modify: `api/tms/api/routes/fleet.py`
- Test: `api/tests/test_vehicle_records.py`

**Step 1: Write the failing tests**

Create `api/tests/test_vehicle_records.py`:
```python
"""What a fleet manager records about a truck: a new rego expiry, a service,
an odometer put right. Each a new record with a note; the history keeps all."""
from datetime import date

from fleet_samples import check
from runs_samples import vehicle


def _vehicle(client, headers, day="2026-10-14"):
    return client.get("/v1/vehicles/TMS041", headers=headers, params={"date": day}).json()


def test_a_fleet_manager_records_the_new_rego_expiry(client, db, manager_headers,
                                                     dispatcher_headers):
    vehicle(db)
    body = {"expires_on": "2027-03-03", "note": "Renewed online, receipt 4471"}
    assert client.post("/v1/vehicles/TMS041/rego", headers=dispatcher_headers,
                       json=body).status_code == 403
    r = client.post("/v1/vehicles/tms041/rego", headers=manager_headers, json=body)
    assert r.status_code == 201, r.text
    out = r.json()
    assert {k: out[k] for k in ("vehicle", "kind", "expires_on", "note", "recorded_by")} == {
        "vehicle": "TMS041", "kind": "rego", "expires_on": "2027-03-03",
        "note": "Renewed online, receipt 4471", "recorded_by": "fleet.mgr"}
    assert _vehicle(client, manager_headers)["rego_due"]["expires_on"] == "2027-03-03"
    # a note is needed: a person clears it with a note (rule 3)
    r = client.post("/v1/vehicles/TMS041/rego", headers=manager_headers,
                    json={"expires_on": "2027-03-03", "note": "  "})
    assert r.status_code == 422
    assert client.post("/v1/vehicles/NOPE/rego", headers=manager_headers,
                       json=body).status_code == 404


def test_a_service_and_an_odometer_put_right_are_kept_with_the_checks(client, db,
                                                                       manager_headers):
    v = vehicle(db)
    client.patch("/v1/vehicles/TMS041", headers=manager_headers, json={"service_every_km": 15000})
    check(db, v, km=61208)
    r = client.post("/v1/vehicles/TMS041/service", headers=manager_headers,
                    json={"done_on": "2026-10-13", "odometer_km": 61190,
                          "note": "60,000 km service at Ballarat Diesel"})
    assert r.status_code == 201, r.text
    assert _vehicle(client, manager_headers)["service_due"]["due_km"] == 76190
    r = client.post("/v1/vehicles/TMS041/odometer", headers=manager_headers,
                    json={"odometer_km": 61250, "note": "Read it off the dash"})
    assert r.status_code == 201 and _vehicle(client, manager_headers)["odometer_km"] == 61250
    client.post("/v1/vehicles/TMS041/rego", headers=manager_headers,
                json={"expires_on": "2026-12-01", "note": "Typo"})
    client.post("/v1/vehicles/TMS041/rego", headers=manager_headers,
                json={"expires_on": "2027-12-01", "note": "The right year"})
    history = client.get("/v1/vehicles/TMS041/history", headers=manager_headers).json()
    assert [h["kind"] for h in history] == ["rego", "rego", "odometer", "service", "prestart"]
    assert [h.get("expires_on") for h in history[:2]] == ["2027-12-01", "2026-12-01"]
    assert history[4] | {"recorded_at": None} == {
        "kind": "prestart", "id": 1, "recorded_at": None, "by": "tom", "note": None,
        "result": "passed", "failed": [], "odometer_km": 61208, "done_on": None,
        "expires_on": None, "check_id": None}
    assert _vehicle(client, manager_headers)["rego_due"]["expires_on"] == "2027-12-01"
    assert date.fromisoformat(history[3]["done_on"]) == date(2026, 10, 13)
```

**Step 2: Run them to see them fail**

Run: `.venv/bin/pytest tests/test_vehicle_records.py -q`
Expected: `2 failed`: `POST /v1/vehicles/TMS041/rego` is `404`.

**Step 3: Implement**

Change `api/tms/api/routes/fleet.py`:
```diff
--- a/api/tms/api/routes/fleet.py
+++ b/api/tms/api/routes/fleet.py
@@ -3,13 +3,17 @@ fleet manager records about each truck. Checks and records are kept as they
 were sent; a truck's standing is worked out from them."""
 from __future__ import annotations
 
-from fastapi import APIRouter
+import datetime as dt
+
+from fastapi import APIRouter, Query
+from fastapi.responses import JSONResponse
 from pydantic import BaseModel, ConfigDict, Field, field_validator
+from sqlalchemy import select
 
 from tms.api.deps import DB, Principal, require
 from tms.api.routes.vehicles import VehicleType
-from tms.models import ChecklistItem
-from tms.services import checklists
+from tms.models import ChecklistItem, PrestartCheck, VehicleRecord
+from tms.services import checklists, records
 
 router = APIRouter(tags=["fleet"])
 
@@ -58,3 +62,107 @@ def put_checklist(vehicle_type: VehicleType, body: ChecklistIn, db: DB,
     rows = checklists.replace(db, vehicle_type, [(i.label, i.critical) for i in body.items])
     db.commit()
     return [item_out(i) for i in rows]
+
+
+# ---- what a fleet manager records -----------------------------------------------
+
+class RegoIn(BaseModel):
+    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
+    expires_on: dt.date
+    note: str = Field(min_length=1, max_length=500)
+
+
+class ServiceIn(BaseModel):
+    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
+    done_on: dt.date
+    odometer_km: int | None = Field(default=None, ge=0, le=9_999_999)
+    note: str = Field(min_length=1, max_length=500)
+
+
+class OdometerIn(BaseModel):
+    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
+    odometer_km: int = Field(ge=0, le=9_999_999)
+    note: str = Field(min_length=1, max_length=500)
+
+
+class RecordOut(BaseModel):
+    id: int
+    vehicle: str
+    kind: str
+    check_id: int | None
+    done_on: dt.date | None
+    expires_on: dt.date | None
+    odometer_km: int | None
+    note: str
+    recorded_by: str
+    recorded_at: dt.datetime
+
+
+def record_out(r: VehicleRecord, rego: str) -> RecordOut:
+    return RecordOut(id=r.id, vehicle=rego, kind=r.kind, check_id=r.check_id,
+                     done_on=r.done_on, expires_on=r.expires_on, odometer_km=r.odometer_km,
+                     note=r.note, recorded_by=r.recorded_by, recorded_at=r.recorded_at)
+
+
+def _created(db, row: VehicleRecord, rego: str) -> JSONResponse:
+    db.commit()
+    out = record_out(row, records.vehicle_by_rego(db, rego).rego)
+    return JSONResponse(status_code=201, content=out.model_dump(mode="json"))
+
+
+@router.post("/vehicles/{rego}/rego", status_code=201, response_model=RecordOut)
+def record_rego(rego: str, body: RegoIn, db: DB, who: Principal = require("fleet:write")):
+    """The rego's new expiry. The newest record is the one that counts."""
+    row = records.add(db, rego, "rego", by=who.name, note=body.note, expires_on=body.expires_on)
+    return _created(db, row, rego)
+
+
+@router.post("/vehicles/{rego}/service", status_code=201, response_model=RecordOut)
+def record_service(rego: str, body: ServiceIn, db: DB, who: Principal = require("fleet:write")):
+    """A service done: the next one falls due from it, by date and by km."""
+    row = records.add(db, rego, "service", by=who.name, note=body.note, done_on=body.done_on,
+                      odometer_km=body.odometer_km)
+    return _created(db, row, rego)
+
+
+@router.post("/vehicles/{rego}/odometer", status_code=201, response_model=RecordOut)
+def record_odometer(rego: str, body: OdometerIn, db: DB, who: Principal = require("fleet:write")):
+    """The reading put right (a slip of the finger, a new odometer): readings
+    from before it no longer count."""
+    row = records.add(db, rego, "odometer", by=who.name, note=body.note,
+                      odometer_km=body.odometer_km)
+    return _created(db, row, rego)
+
+
+class HistoryItem(BaseModel):
+    # prestart, fixed, service, rego, odometer
+    kind: str
+    id: int
+    recorded_at: dt.datetime
+    by: str
+    note: str | None = None
+    # a pre-start: passed, failed or grounded, and each item that failed
+    result: str | None = None
+    failed: list[str] = []
+    odometer_km: int | None = None
+    done_on: dt.date | None = None
+    expires_on: dt.date | None = None
+    check_id: int | None = None
+
+
+@router.get("/vehicles/{rego}/history", response_model=list[HistoryItem])
+def vehicle_history(rego: str, db: DB, limit: int = Query(default=50, ge=1, le=200),
+                    who: Principal = require("fleet:read")):
+    """Its checks and records, newest first. Nothing in it is ever changed."""
+    v = records.vehicle_by_rego(db, rego)
+    checks = db.execute(select(PrestartCheck).where(PrestartCheck.vehicle_id == v.id)
+                        .order_by(PrestartCheck.id.desc()).limit(limit)).scalars().all()
+    rows = db.execute(select(VehicleRecord).where(VehicleRecord.vehicle_id == v.id)
+                      .order_by(VehicleRecord.id.desc()).limit(limit)).scalars().all()
+    items = [HistoryItem(kind="prestart", id=c.id, recorded_at=c.recorded_at, by=c.recorded_by,
+                         result=c.result, failed=[i.label for i in c.items if not i.passed],
+                         odometer_km=c.odometer_km) for c in checks]
+    items += [HistoryItem(kind=r.kind, id=r.id, recorded_at=r.recorded_at, by=r.recorded_by,
+                          note=r.note, odometer_km=r.odometer_km, done_on=r.done_on,
+                          expires_on=r.expires_on, check_id=r.check_id) for r in rows]
+    return sorted(items, key=lambda i: i.recorded_at, reverse=True)[:limit]
```

Create `api/tms/services/records.py`:
```python
"""What is recorded about a truck: the records a fleet manager keeps (rego,
service, an odometer put right, and later fixed). Each is a new row, written
under the planning lock, so it and any run leaving take turns: a run never
leaves on a truck grounded or overdue a moment before."""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from tms.api.errors import NotFound
from tms.models import Vehicle, VehicleRecord
from tms.services.runs import lock_planning


def vehicle_by_rego(db: Session, rego: str) -> Vehicle:
    v = db.execute(select(Vehicle).where(
        Vehicle.rego == "".join(rego.split()).upper())).scalar_one_or_none()
    if v is None:
        raise NotFound(f"no vehicle {rego}")
    return v


def add(db: Session, rego: str, kind: str, *, by: str, note: str, **fields) -> VehicleRecord:
    lock_planning(db)
    v = vehicle_by_rego(db, rego)
    row = VehicleRecord(vehicle_id=v.id, kind=kind, note=note, recorded_by=by,
                        recorded_at=datetime.now(UTC), **fields)
    db.add(row)
    db.flush()
    return row
```

**Step 4: Run the tests**

Run: `.venv/bin/pytest -q`
Expected: `262 passed`.

**Step 5: Commit**

```bash
git add api/tms/services/records.py api/tms/api/routes/fleet.py api/tests/test_vehicle_records.py
git commit -m "Record a new rego expiry, a service and an odometer put right, and show the history"
```

### Task 5: The driver's pre-start, once per message, grounding the truck on a failed critical item

**Files:**
- Modify: `api/tms/services/records.py` (`prestart`, `run_check`)
- Modify: `api/tms/services/deliveries.py` (`_file` becomes `own_file`, and checks pre-start items too)
- Modify: `api/tms/api/routes/fleet.py` (`POST /v1/runs/{id}/prestart`)
- Modify: `api/tms/api/routes/deliveries.py` (today's runs carry the truck's reading, why it can't go, the run's check and the checklist)
- Modify: `api/tests/deliveries_samples.py` (`prestart`, `VAN_ITEMS`)
- Test: `api/tests/test_prestart.py`, `api/tests/test_driver_runs.py`

**Step 1: Write the failing tests**

Change `api/tests/deliveries_samples.py`:
```diff
--- a/api/tests/deliveries_samples.py
+++ b/api/tests/deliveries_samples.py
@@ -62,3 +62,26 @@ def failed(client, headers, run_id, c, message_id=None, **over):
     body = {"message_id": str(message_id or uuid.uuid4()), "at": now(), "reason": "closed",
             **over}
     return client.post(f"/v1/runs/{run_id}/stops/{c.id}/fail", headers=headers, json=body)
+
+
+# the design's checklist for a van, as a fresh install has it: (id, label)
+VAN_ITEMS = [(1, "Tyres"), (2, "Lights"), (3, "Brakes"), (4, "Mirrors"), (5, "Load restraints"),
+             (6, "Damage")]
+
+
+def prestart(client, headers, run_id, km=61208, failed=(), message_id=None, items=VAN_ITEMS):
+    """Kim's pre-start of the run's van: every item passed but those named,
+    each failed one with its own photo (sent first) and a note."""
+    answers = []
+    for item_id, label in items:
+        if label in failed:
+            photo, r = put_file(client, headers, kind="photo", data=JPEG,
+                                content_type="image/jpeg")
+            assert r.status_code == 201, r.text
+            answers.append({"item_id": item_id, "result": "fail", "note": f"{label} not right",
+                            "photo_file": str(photo)})
+        else:
+            answers.append({"item_id": item_id, "result": "pass"})
+    body = {"message_id": str(message_id or uuid.uuid4()), "at": now(), "odometer_km": km,
+            "items": answers}
+    return client.post(f"/v1/runs/{run_id}/prestart", headers=headers, json=body)
```

Change `api/tests/test_driver_runs.py`:
```diff
--- a/api/tests/test_driver_runs.py
+++ b/api/tests/test_driver_runs.py
@@ -13,9 +13,13 @@ def test_a_driver_sees_their_own_runs_for_the_day(client, db, dispatcher_headers
     kim = driver_headers(client)
     r = client.get("/v1/driver/runs", params={"date": DAY.isoformat()}, headers=kim)
     assert r.status_code == 200, r.text
-    assert r.json() == [{"id": run["id"], "run_no": run["run_no"], "date": "2026-10-14",
-                         "status": "planned", "vehicle": {"rego": "TMS041", "type": "van"},
-                         "stops": 2, "packages": 2, "delivered": 0, "failed": 0}]
+    out = r.json()
+    assert len(out[0].pop("checklist")) == 6
+    assert out == [{"id": run["id"], "run_no": run["run_no"], "date": "2026-10-14",
+                    "status": "planned",
+                    "vehicle": {"rego": "TMS041", "type": "van", "odometer_km": None,
+                                "blocked": None},
+                    "stops": 2, "packages": 2, "delivered": 0, "failed": 0, "prestart": None}]
     later = client.get("/v1/driver/runs", params={"date": "2026-10-15"}, headers=kim)
     assert later.json() == []
```

Create `api/tests/test_prestart.py`:
```python
"""The driver's pre-start check, from the phone: every item of the truck's
checklist passed or failed, a failed one with a photo and a note, and the
odometer. A failed critical item grounds the truck at once. Kept as sent,
once per message_id."""
import uuid

from sqlalchemy import select

from conftest import all_at_once, fresh_client
from deliveries_samples import (
    JPEG, VAN_ITEMS, delivered, driver_headers, on_the_road, planned_run, prestart, put_file,
)
from runs_samples import DAY, driver
from tms.models import InboundMessage, PrestartCheck


def _vehicle(client, headers):
    return client.get("/v1/vehicles/TMS041", headers=headers,
                      params={"date": DAY.isoformat()}).json()


def _my_run(client, kim, run_id):
    runs = client.get("/v1/driver/runs", params={"date": DAY.isoformat()}, headers=kim).json()
    return next(r for r in runs if r["id"] == run_id)


def test_a_passed_check_is_kept_once_with_the_odometer(client, db, dispatcher_headers):
    run, _ = planned_run(client, db, dispatcher_headers)
    kim = driver_headers(client)
    mine = _my_run(client, kim, run["id"])
    assert mine["prestart"] is None
    assert mine["checklist"][1] == {"id": 2, "label": "Lights", "critical": True}
    message_id = uuid.uuid4()
    r = prestart(client, kim, run["id"], km=61208, message_id=message_id)
    assert r.status_code == 200, r.text
    out = r.json()
    assert {k: out[k] for k in ("message_id", "run_no", "vehicle", "result", "failed",
                                "grounded", "odometer_km")} == {
        "message_id": str(message_id), "run_no": run["run_no"], "vehicle": "TMS041",
        "result": "passed", "failed": [], "grounded": False, "odometer_km": 61208}
    # the same message again: the same reply, nothing more kept
    again = prestart(client, kim, run["id"], km=61208, message_id=message_id)
    assert again.status_code == 200 and again.json() == out
    check = db.execute(select(PrestartCheck)).scalar_one()
    assert [(i.label, i.passed) for i in check.items][:2] == [("Tyres", True), ("Lights", True)]
    assert check.recorded_by == "kim" and check.driver_id is not None
    assert db.execute(select(InboundMessage)).scalar_one().duplicates == 1
    mine = _my_run(client, kim, run["id"])
    assert mine["prestart"]["id"] == out["check_id"] and mine["prestart"]["result"] == "passed"
    assert mine["vehicle"]["odometer_km"] == 61208


def test_a_failed_item_needs_a_photo_and_a_note_and_the_truck_may_still_go(
        client, db, dispatcher_headers):
    run, _ = planned_run(client, db, dispatcher_headers)
    kim = driver_headers(client)
    answers = [{"item_id": i, "result": "pass"} for i in range(1, 7)]
    answers[4] = {"item_id": 5, "result": "fail", "note": "Rear strap frayed"}
    r = client.post(f"/v1/runs/{run['id']}/prestart", headers=kim, json={
        "message_id": str(uuid.uuid4()), "at": "2026-10-14T06:40:00+11:00",
        "odometer_km": 61208, "items": answers})
    assert r.status_code == 422
    assert "a failed item needs a photo and a note" in r.text
    r = prestart(client, kim, run["id"], failed=("Load restraints",))
    assert r.status_code == 200, r.text
    assert (r.json()["result"], r.json()["failed"], r.json()["grounded"]) == (
        "failed", ["Load restraints"], False)
    item = db.execute(select(PrestartCheck)).scalar_one().items[4]
    assert (item.label, item.passed, item.note) == ("Load restraints", False,
                                                    "Load restraints not right")
    assert item.photo_file_id is not None
    assert _vehicle(client, dispatcher_headers)["state"] == "ok"


def test_a_failed_critical_item_grounds_the_truck_there_and_then(client, db, dispatcher_headers):
    run, _ = planned_run(client, db, dispatcher_headers)
    kim = driver_headers(client)
    r = prestart(client, kim, run["id"], failed=("Brakes",))
    assert r.status_code == 200, r.text
    assert (r.json()["result"], r.json()["grounded"]) == ("grounded", True)
    v = _vehicle(client, dispatcher_headers)
    assert v["state"] == "grounded" and v["grounded"]["failed"] == ["Brakes"]
    assert _my_run(client, kim, run["id"])["vehicle"]["blocked"]["code"] == "vehicle_grounded"


def test_the_odometer_never_goes_back(client, db, dispatcher_headers):
    run, _ = planned_run(client, db, dispatcher_headers)
    kim = driver_headers(client)
    assert prestart(client, kim, run["id"], km=61208).status_code == 200
    r = prestart(client, kim, run["id"], km=61200)
    assert r.status_code == 422 and r.json()["errors"] == [{
        "field": "odometer_km", "message": "61,200 km is less than TMS041's last reading, "
                                           "61,208 km. Check the odometer and try again."}]
    assert prestart(client, kim, run["id"], km=61208).status_code == 200


def test_each_item_answered_once_from_the_list_as_it_is(client, db, dispatcher_headers,
                                                       manager_headers):
    run, _ = planned_run(client, db, dispatcher_headers)
    kim = driver_headers(client)
    r = prestart(client, kim, run["id"], items=[(1, "Tyres"), (2, "Lights")])
    assert r.status_code == 409 and r.json()["code"] == "checklist_changed"
    # the fleet manager adds a line while the phone has the old list
    client.put("/v1/checklists/van", headers=manager_headers, json={"items": [
        {"label": "Tyres"}, {"label": "Lights", "critical": True},
        {"label": "Brakes", "critical": True}, {"label": "Mirrors"},
        {"label": "Load restraints"}, {"label": "Damage"}, {"label": "Tail lift"}]})
    r = prestart(client, kim, run["id"])
    assert r.status_code == 409 and r.json() == {
        "code": "checklist_changed",
        "detail": "The van checklist has changed since this check began. Do the check again."}
    assert prestart(client, kim, run["id"], items=[*VAN_ITEMS, (19, "Tail lift")]).status_code == 200


def test_a_photo_is_the_drivers_own_and_goes_with_one_thing(client, db, dispatcher_headers):
    run, (a, _), kim = on_the_road(client, db, dispatcher_headers)
    photo, _ = put_file(client, kim, kind="photo", data=JPEG, content_type="image/jpeg")
    assert delivered(client, kim, run["id"], a, photo_file=str(photo)).status_code == 200
    answers = [{"item_id": i, "result": "pass"} for i in range(1, 7)]
    answers[0] = {"item_id": 1, "result": "fail", "note": "Bald", "photo_file": str(photo)}
    r = client.post(f"/v1/runs/{run['id']}/prestart", headers=kim, json={
        "message_id": str(uuid.uuid4()), "at": "2026-10-14T06:40:00Z", "odometer_km": 1,
        "items": answers})
    assert r.status_code == 422 and r.json()["errors"][0]["field"] == "photo_file"


def test_only_the_runs_driver_and_only_until_it_is_finished(client, db, dispatcher_headers):
    run, (a,), kim = on_the_road(client, db, dispatcher_headers, n=1)
    driver(db, code="mel", name="Mel")
    assert prestart(client, driver_headers(client, code="mel"), run["id"]).status_code == 404
    assert prestart(client, dispatcher_headers, run["id"]).status_code == 403
    # on the road it is still taken: the check was done, so it is kept
    assert prestart(client, kim, run["id"]).status_code == 200
    delivered(client, kim, run["id"], a)
    client.post(f"/v1/runs/{run['id']}/finish", headers=kim,
                json={"message_id": str(uuid.uuid4())})
    r = prestart(client, kim, run["id"])
    assert r.status_code == 409 and r.json()["code"] == "run_done"


def test_two_copies_at_once_keep_one_check(client, db, dispatcher_headers):
    run, _ = planned_run(client, db, dispatcher_headers)
    kim = driver_headers(client)
    message_id = uuid.uuid4()

    def send():
        with fresh_client() as c:
            return prestart(c, kim, run["id"], message_id=message_id).status_code

    assert all_at_once(3, send) == [200, 200, 200]
    assert len(db.execute(select(PrestartCheck)).scalars().all()) == 1
```

**Step 2: Run them to see them fail**

Run: `.venv/bin/pytest tests/test_prestart.py tests/test_driver_runs.py -q`
Expected: failures: `POST /v1/runs/{id}/prestart` is `404`, and today's runs lack `checklist` (`KeyError`).

**Step 3: Implement**

Change `api/tms/api/routes/deliveries.py`:
```diff
--- a/api/tms/api/routes/deliveries.py
+++ b/api/tms/api/routes/deliveries.py
@@ -15,7 +15,7 @@ from tms.api.deps import DB, Principal, require, require_any
 from tms.api.errors import Forbidden
 from tms.api.schemas import Dec
 from tms.models import Run
-from tms.services import deliveries
+from tms.services import checklists, deliveries, fleet, records
 from tms.services.envelope import handle
 
 router = APIRouter(tags=["deliveries"])
@@ -28,11 +28,16 @@ class DriverRunOut(BaseModel):
     run_no: str
     date: dt.date
     status: str
+    # rego, type, its last odometer reading, and why it can't go (or null)
     vehicle: dict
     stops: int
     packages: int
     delivered: int
     failed: int
+    # the latest pre-start of this truck for this run: { id, at, result }, or null
+    prestart: dict | None
+    # the truck's checklist: [{ id, label, critical }]
+    checklist: list[dict]
 
 
 class PackageLine(BaseModel):
@@ -65,13 +70,22 @@ class RunStopsOut(BaseModel):
     stops: list[StopDetail]
 
 
-def driver_run_out(run: Run) -> DriverRunOut:
+def driver_run_out(db, run: Run) -> DriverRunOut:
+    v = run.vehicle
+    s = fleet.standing(db, v, run.day)
+    why = s.blocked
+    check = records.run_check(db, run)
     return DriverRunOut(
         id=run.id, run_no=run.number, date=run.day, status=run.status,
-        vehicle={"rego": run.vehicle.rego, "type": run.vehicle.type},
+        vehicle={"rego": v.rego, "type": v.type, "odometer_km": s.odometer_km,
+                 "blocked": None if why is None else {"code": why.code, "message": why.message}},
         stops=len(run.stops), packages=sum(len(s.consignment.packages) for s in run.stops),
         delivered=sum(s.outcome == "delivered" for s in run.stops),
-        failed=sum(s.outcome == "failed" for s in run.stops))
+        failed=sum(s.outcome == "failed" for s in run.stops),
+        prestart=None if check is None else {"id": check.id, "at": check.at,
+                                             "result": check.result},
+        checklist=[{"id": i.id, "label": i.label, "critical": i.critical}
+                   for i in checklists.live(db, v.type)])
 
 
 def run_stops_out(run: Run) -> RunStopsOut:
@@ -95,7 +109,7 @@ def my_runs(db: DB, day: dt.date = Query(alias="date"),
     of theirs that left on an earlier day and were never finished."""
     if who.kind != "driver":
         raise Forbidden("only a driver has runs of their own")
-    return [driver_run_out(r) for r in deliveries.driver_runs(db, who.id, day)]
+    return [driver_run_out(db, r) for r in deliveries.driver_runs(db, who.id, day)]
 
 
 @router.get("/runs/{id}/stops", response_model=RunStopsOut)
```

Change `api/tms/api/routes/fleet.py`:
```diff
--- a/api/tms/api/routes/fleet.py
+++ b/api/tms/api/routes/fleet.py
@@ -4,16 +4,19 @@ were sent; a truck's standing is worked out from them."""
 from __future__ import annotations
 
 import datetime as dt
+import uuid
+from typing import Literal
 
 from fastapi import APIRouter, Query
 from fastapi.responses import JSONResponse
-from pydantic import BaseModel, ConfigDict, Field, field_validator
+from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator
 from sqlalchemy import select
 
 from tms.api.deps import DB, Principal, require
 from tms.api.routes.vehicles import VehicleType
-from tms.models import ChecklistItem, PrestartCheck, VehicleRecord
-from tms.services import checklists, records
+from tms.models import ChecklistItem, PrestartCheck, Run, VehicleRecord
+from tms.services import checklists, fleet, records
+from tms.services.envelope import handle
 
 router = APIRouter(tags=["fleet"])
 
@@ -64,6 +67,64 @@ def put_checklist(vehicle_type: VehicleType, body: ChecklistIn, db: DB,
     return [item_out(i) for i in rows]
 
 
+# ---- the driver's pre-start ---------------------------------------------------
+
+class Answer(BaseModel):
+    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
+    item_id: int
+    result: Literal["pass", "fail"]
+    note: str | None = Field(default=None, max_length=500)
+    photo_file: uuid.UUID | None = None
+
+    @model_validator(mode="after")
+    def _a_fail_says_why(self):
+        if self.result == "fail" and (not self.note or not self.photo_file):
+            raise ValueError("a failed item needs a photo and a note")
+        if self.result == "pass" and (self.note or self.photo_file):
+            raise ValueError("only a failed item takes a note and a photo")
+        return self
+
+
+class PrestartIn(BaseModel):
+    model_config = ConfigDict(extra="forbid")
+    message_id: uuid.UUID
+    at: AwareDatetime
+    odometer_km: int = Field(ge=0, le=9_999_999)
+    items: list[Answer] = Field(min_length=1, max_length=30)
+
+
+class PrestartOut(BaseModel):
+    message_id: uuid.UUID
+    check_id: int
+    run_id: int
+    run_no: str
+    vehicle: str
+    # passed, failed (a non-critical item: the truck may go), grounded
+    result: str
+    failed: list[str]
+    # whether the truck is grounded now, by this check or one before it
+    grounded: bool
+    odometer_km: int
+    at: dt.datetime
+
+
+@router.post("/runs/{id}/prestart", response_model=PrestartOut)
+def send_prestart(id: int, body: PrestartIn, db: DB,
+                  who: Principal = require("deliveries:write")):
+    """The driver's pre-start check of the run's truck, once per message_id."""
+    def work():
+        check = records.prestart(db, who, id, message_id=body.message_id, at=body.at,
+                                 odometer_km=body.odometer_km,
+                                 answers=[a.model_dump() for a in body.items])
+        run = db.get(Run, check.run_id)
+        return PrestartOut(message_id=body.message_id, check_id=check.id, run_id=run.id,
+                           run_no=run.number, vehicle=run.vehicle.rego, result=check.result,
+                           failed=[i.label for i in check.items if not i.passed],
+                           grounded=fleet.grounding(db, check.vehicle_id) is not None,
+                           odometer_km=check.odometer_km, at=check.at)
+    return handle(db, who, body.message_id, f"/v1/runs/{id}/prestart", work)
+
+
 # ---- what a fleet manager records -----------------------------------------------
 
 class RegoIn(BaseModel):
```

Change `api/tms/services/deliveries.py`:
```diff
--- a/api/tms/services/deliveries.py
+++ b/api/tms/services/deliveries.py
@@ -14,7 +14,7 @@ from sqlalchemy.exc import IntegrityError
 from sqlalchemy.orm import Session
 
 from tms.api.errors import Conflict, FieldError, NotFound, violated_constraint
-from tms.models import ConsignmentEvent, Pod, PodFile, Run, Stop
+from tms.models import ConsignmentEvent, Pod, PodFile, PrestartItem, Run, Stop
 from tms.services import runs
 from tms.services.envelope import sender_of
 
@@ -92,7 +92,8 @@ def _open_stop(db: Session, run: Run, consignment_id: int) -> Stop:
     return stop
 
 
-def _file(db: Session, who, file_id: uuid.UUID | None, kind: str) -> PodFile | None:
+def own_file(db: Session, who, file_id: uuid.UUID | None, kind: str) -> PodFile | None:
+    """A picture this sender sent, not yet named by a stop or a pre-start item."""
     if file_id is None:
         return None
     row = db.get(PodFile, file_id)
@@ -102,6 +103,8 @@ def _file(db: Session, who, file_id: uuid.UUID | None, kind: str) -> PodFile | N
         or_(Pod.signature_file_id == file_id, Pod.photo_file_id == file_id))).first()
     if used is not None:
         raise FieldError(f"{kind}_file", f"{kind} {file_id} already belongs to another stop")
+    if db.execute(select(PrestartItem.id).where(PrestartItem.photo_file_id == file_id)).first():
+        raise FieldError(f"{kind}_file", f"{kind} {file_id} already belongs to a pre-start check")
     return row
 
 
@@ -150,8 +153,8 @@ def deliver(db: Session, who, run_id: int, consignment_id: int, *, message_id: u
     expected = sorted(p.package_no for p in stop.consignment.packages)
     if sorted(p["package_no"] for p in packages) != expected:
         raise FieldError("packages", f"name each of the {len(expected)} packages once")
-    signature = _file(db, who, signature_file, "signature")
-    photo = _file(db, who, photo_file, "photo")
+    signature = own_file(db, who, signature_file, "signature")
+    photo = own_file(db, who, photo_file, "photo")
     pod = _record(db, who, run, stop, message_id=message_id, outcome="delivered", at=at,
                   where=where, packages=packages, signed_name=signed_name, note=note,
                   signature_file_id=signature.id if signature else None,
@@ -170,7 +173,7 @@ def fail(db: Session, who, run_id: int, consignment_id: int, *, message_id: uuid
     run = _locked(db, who, run_id)
     _on_the_road(run)
     stop = _open_stop(db, run, consignment_id)
-    photo = _file(db, who, photo_file, "photo")
+    photo = own_file(db, who, photo_file, "photo")
     pod = _record(db, who, run, stop, message_id=message_id, outcome="failed", at=at,
                   where=where, reason=reason, note=note, packages=[],
                   photo_file_id=photo.id if photo else None,
```

Change `api/tms/services/records.py`:
```diff
--- a/api/tms/services/records.py
+++ b/api/tms/services/records.py
@@ -1,16 +1,19 @@
-"""What is recorded about a truck: the records a fleet manager keeps (rego,
-service, an odometer put right, and later fixed). Each is a new row, written
-under the planning lock, so it and any run leaving take turns: a run never
-leaves on a truck grounded or overdue a moment before."""
+"""What is recorded about a truck: a driver's pre-start check, and the
+records a fleet manager keeps (rego, service, an odometer put right, fixed).
+Each is a new row, written under the planning lock, so it and any run leaving
+take turns: a run never leaves on a truck grounded or overdue a moment
+before."""
 from __future__ import annotations
 
+import uuid
 from datetime import UTC, datetime
 
 from sqlalchemy import select
 from sqlalchemy.orm import Session
 
-from tms.api.errors import NotFound
-from tms.models import Vehicle, VehicleRecord
+from tms.api.errors import Conflict, FieldError, NotFound
+from tms.models import PrestartCheck, PrestartItem, Run, Vehicle, VehicleRecord
+from tms.services import checklists, deliveries, fleet
 from tms.services.runs import lock_planning
 
 
@@ -30,3 +33,57 @@ def add(db: Session, rego: str, kind: str, *, by: str, note: str, **fields) -> V
     db.add(row)
     db.flush()
     return row
+
+
+def run_check(db: Session, run: Run) -> PrestartCheck | None:
+    """The latest pre-start for this run, of the truck it has now."""
+    return db.execute(select(PrestartCheck).where(
+        PrestartCheck.run_id == run.id, PrestartCheck.vehicle_id == run.vehicle_id)
+        .order_by(PrestartCheck.id.desc()).limit(1)).scalar_one_or_none()
+
+
+def prestart(db: Session, who, run_id: int, *, message_id: uuid.UUID, at: datetime,
+             odometer_km: int, answers: list[dict]) -> PrestartCheck:
+    """The driver's check of the run's truck, every item of its checklist
+    answered once. A failed critical item grounds the truck there and then; a
+    failed other item is kept and the truck may go. Kept as sent, for good."""
+    lock_planning(db)
+    run = deliveries.own_run(db, who, run_id)
+    if run.status == "done":
+        raise Conflict("run_done", f"{run.number} is finished")
+    if run.status == "cancelled":
+        raise Conflict("run_cancelled", f"{run.number} was cancelled")
+    v = run.vehicle
+    lines = checklists.live(db, v.type)
+    given = {a["item_id"]: a for a in answers}
+    if len(given) != len(answers) or set(given) != {line.id for line in lines}:
+        raise Conflict("checklist_changed",
+                       f"The {v.type} checklist has changed since this check began. "
+                       "Do the check again.")
+    reading = fleet.odometer(db, v.id)
+    if reading is not None and odometer_km < reading:
+        raise FieldError("odometer_km",
+                         f"{odometer_km:,} km is less than {v.rego}'s last reading, "
+                         f"{reading:,} km. Check the odometer and try again.")
+    photos = [a["photo_file"] for a in answers if a["photo_file"]]
+    if len(photos) != len(set(photos)):
+        raise FieldError("photo_file", "each failed item needs a photo of its own")
+    for photo in photos:
+        deliveries.own_file(db, who, photo, "photo")
+    failed = [line for line in lines if given[line.id]["result"] == "fail"]
+    now = datetime.now(UTC)
+    check = PrestartCheck(
+        message_id=message_id, run_id=run.id, vehicle_id=v.id,
+        driver_id=who.id if who.kind == "driver" else None, at=min(at, now),
+        odometer_km=odometer_km, recorded_by=who.name, recorded_at=now,
+        result=("grounded" if any(line.critical for line in failed)
+                else "failed" if failed else "passed"))
+    for position, line in enumerate(lines, start=1):
+        a = given[line.id]
+        check.items.append(PrestartItem(
+            position=position, checklist_item_id=line.id, label=line.label,
+            critical=line.critical, passed=a["result"] == "pass", note=a["note"],
+            photo_file_id=a["photo_file"]))
+    db.add(check)
+    db.flush()
+    return check
```

**Step 4: Run the tests**

Run: `.venv/bin/pytest -q`
Expected: `270 passed`.

**Step 5: Commit**

```bash
git add api/tms/services/records.py api/tms/services/deliveries.py api/tms/api/routes/fleet.py \
        api/tms/api/routes/deliveries.py api/tests/deliveries_samples.py api/tests/test_prestart.py \
        api/tests/test_driver_runs.py
git commit -m "Take the driver's pre-start check, grounding the truck on a failed critical item"
```

### Task 6: Mark fixed, and each check as it was sent

**Files:**
- Modify: `api/tms/services/records.py` (`fix`, `fixed_by`)
- Modify: `api/tms/api/routes/fleet.py` (`POST /v1/vehicles/{rego}/fix`, `GET /v1/prestarts/{id}`)
- Test: `api/tests/test_mark_fixed.py`

**Step 1: Write the failing tests**

Create `api/tests/test_mark_fixed.py`:
```python
"""A grounded truck stays grounded until a fleet manager marks it fixed, with
a note: a new record, never a change to the failed check."""
import uuid

from deliveries_samples import driver_headers, planned_run, prestart
from runs_samples import DAY


def _grounded(client, db, dispatcher_headers):
    run, _ = planned_run(client, db, dispatcher_headers)
    kim = driver_headers(client)
    r = prestart(client, kim, run["id"], failed=("Lights", "Mirrors"))
    assert r.status_code == 200 and r.json()["result"] == "grounded"
    return run, kim, r.json()["check_id"]


def _state(client, headers):
    return client.get("/v1/vehicles/TMS041", headers=headers,
                      params={"date": DAY.isoformat()}).json()["state"]


def test_the_office_sees_the_failed_check_with_its_photos_and_notes(client, db,
                                                                   dispatcher_headers):
    run, _, check_id = _grounded(client, db, dispatcher_headers)
    r = client.get(f"/v1/prestarts/{check_id}", headers=dispatcher_headers)
    assert r.status_code == 200, r.text
    out = r.json()
    assert {k: out[k] for k in ("run_no", "vehicle", "vehicle_type", "driver", "odometer_km",
                                "result", "fixed")} == {
        "run_no": run["run_no"], "vehicle": "TMS041", "vehicle_type": "van",
        "driver": {"code": "kim", "name": "Kim"}, "odometer_km": 61208, "result": "grounded",
        "fixed": None}
    lights = out["items"][1]
    assert (lights["label"], lights["critical"], lights["passed"], lights["note"]) == (
        "Lights", True, False, "Lights not right")
    assert out["items"][0] == {"label": "Tyres", "critical": False, "passed": True, "note": None,
                               "photo_file": None}
    photo = client.get(f"/v1/pod-files/{lights['photo_file']}", headers=dispatcher_headers)
    assert photo.status_code == 200 and photo.headers["content-type"] == "image/jpeg"
    assert client.get("/v1/prestarts/999", headers=dispatcher_headers).status_code == 404


def test_a_fleet_manager_marks_it_fixed_with_a_note(client, db, dispatcher_headers,
                                                    manager_headers):
    _, _, check_id = _grounded(client, db, dispatcher_headers)
    body = {"check_id": check_id, "note": "Replaced left brake globe, tested"}
    assert client.post("/v1/vehicles/TMS041/fix", headers=dispatcher_headers,
                       json=body).status_code == 403
    assert client.post("/v1/vehicles/TMS041/fix", headers=manager_headers,
                       json={"check_id": check_id, "note": ""}).status_code == 422
    r = client.post("/v1/vehicles/TMS041/fix", headers=manager_headers, json=body)
    assert r.status_code == 201, r.text
    assert (r.json()["kind"], r.json()["check_id"], r.json()["recorded_by"]) == (
        "fixed", check_id, "fleet.mgr")
    assert _state(client, dispatcher_headers) == "ok"
    out = client.get(f"/v1/prestarts/{check_id}", headers=dispatcher_headers).json()
    assert out["result"] == "grounded"                  # the check is as it was sent
    assert out["fixed"]["note"] == "Replaced left brake globe, tested"
    assert out["fixed"]["by"] == "fleet.mgr"
    history = client.get("/v1/vehicles/TMS041/history", headers=dispatcher_headers).json()
    assert [h["kind"] for h in history] == ["fixed", "prestart"]
    r = client.post("/v1/vehicles/TMS041/fix", headers=manager_headers, json=body)
    assert r.status_code == 409 and r.json() == {"code": "not_grounded",
                                                 "detail": "TMS041 isn't grounded"}


def test_a_failed_check_that_came_in_meanwhile_is_not_cleared_unseen(client, db,
                                                                    dispatcher_headers,
                                                                    manager_headers):
    run, kim, first = _grounded(client, db, dispatcher_headers)
    second = prestart(client, kim, run["id"], failed=("Brakes",), message_id=uuid.uuid4())
    r = client.post("/v1/vehicles/TMS041/fix", headers=manager_headers,
                    json={"check_id": first, "note": "New globe"})
    assert r.status_code == 409 and r.json()["code"] == "stale"
    assert _state(client, dispatcher_headers) == "grounded"
    r = client.post("/v1/vehicles/TMS041/fix", headers=manager_headers,
                    json={"check_id": second.json()["check_id"], "note": "Globe and brake pads"})
    assert r.status_code == 201 and _state(client, dispatcher_headers) == "ok"
    # the one fix clears both: each check names it
    out = client.get(f"/v1/prestarts/{first}", headers=dispatcher_headers).json()
    assert out["fixed"]["note"] == "Globe and brake pads"
```

**Step 2: Run them to see them fail**

Run: `.venv/bin/pytest tests/test_mark_fixed.py -q`
Expected: `3 failed`: `GET /v1/prestarts/1` and `POST /v1/vehicles/TMS041/fix` are `404`.

**Step 3: Implement**

Change `api/tms/api/routes/fleet.py`:
```diff
--- a/api/tms/api/routes/fleet.py
+++ b/api/tms/api/routes/fleet.py
@@ -13,8 +13,9 @@ from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validato
 from sqlalchemy import select
 
 from tms.api.deps import DB, Principal, require
+from tms.api.errors import NotFound
 from tms.api.routes.vehicles import VehicleType
-from tms.models import ChecklistItem, PrestartCheck, Run, VehicleRecord
+from tms.models import ChecklistItem, Driver, PrestartCheck, Run, Vehicle, VehicleRecord
 from tms.services import checklists, fleet, records
 from tms.services.envelope import handle
 
@@ -195,6 +196,72 @@ def record_odometer(rego: str, body: OdometerIn, db: DB, who: Principal = requir
     return _created(db, row, rego)
 
 
+class FixIn(BaseModel):
+    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
+    # the check that grounded it, as the fleet manager saw it
+    check_id: int
+    note: str = Field(min_length=1, max_length=500)
+
+
+@router.post("/vehicles/{rego}/fix", status_code=201, response_model=RecordOut)
+def mark_fixed(rego: str, body: FixIn, db: DB, who: Principal = require("fleet:write")):
+    """What grounded it is put right: a new record with what was done. The
+    failed check is never changed."""
+    row = records.fix(db, rego, check_id=body.check_id, by=who.name, note=body.note)
+    return _created(db, row, rego)
+
+
+class PrestartItemOut(BaseModel):
+    label: str
+    critical: bool
+    passed: bool
+    note: str | None
+    photo_file: uuid.UUID | None
+
+
+class FixedOut(BaseModel):
+    id: int
+    note: str
+    by: str
+    at: dt.datetime
+
+
+class PrestartDetail(BaseModel):
+    id: int
+    run_id: int
+    run_no: str
+    vehicle: str
+    vehicle_type: str
+    driver: dict | None
+    at: dt.datetime
+    recorded_at: dt.datetime
+    odometer_km: int
+    result: str
+    items: list[PrestartItemOut]
+    # for a check that grounded the truck: the record that cleared it, or null
+    fixed: FixedOut | None
+
+
+@router.get("/prestarts/{check_id}", response_model=PrestartDetail)
+def get_prestart(check_id: int, db: DB, who: Principal = require("fleet:read")):
+    """One check as it was sent, with each item, note and photo (the photos
+    are at GET /v1/pod-files/{id})."""
+    check = db.get(PrestartCheck, check_id)
+    if check is None:
+        raise NotFound(f"no pre-start check {check_id}")
+    run, v = db.get(Run, check.run_id), db.get(Vehicle, check.vehicle_id)
+    d = db.get(Driver, check.driver_id) if check.driver_id else None
+    fix = records.fixed_by(db, check)
+    return PrestartDetail(
+        id=check.id, run_id=run.id, run_no=run.number, vehicle=v.rego, vehicle_type=v.type,
+        driver=None if d is None else {"code": d.code, "name": d.name}, at=check.at,
+        recorded_at=check.recorded_at, odometer_km=check.odometer_km, result=check.result,
+        items=[PrestartItemOut(label=i.label, critical=i.critical, passed=i.passed, note=i.note,
+                               photo_file=i.photo_file_id) for i in check.items],
+        fixed=None if fix is None else FixedOut(id=fix.id, note=fix.note, by=fix.recorded_by,
+                                                at=fix.recorded_at))
+
+
 class HistoryItem(BaseModel):
     # prestart, fixed, service, rego, odometer
     kind: str
```

Change `api/tms/services/records.py`:
```diff
--- a/api/tms/services/records.py
+++ b/api/tms/services/records.py
@@ -87,3 +87,32 @@ def prestart(db: Session, who, run_id: int, *, message_id: uuid.UUID, at: dateti
     db.add(check)
     db.flush()
     return check
+
+
+def fix(db: Session, rego: str, *, check_id: int, by: str, note: str) -> VehicleRecord:
+    """A fleet manager has put right what grounded the truck. The record names
+    the check they looked at, which must be the latest that grounded it: if
+    another came in meanwhile, it is refused rather than cleared unseen."""
+    lock_planning(db)
+    v = vehicle_by_rego(db, rego)
+    grounded_by = fleet.grounding(db, v.id)
+    if grounded_by is None:
+        raise Conflict("not_grounded", f"{v.rego} isn't grounded")
+    if grounded_by.id != check_id:
+        raise Conflict("stale", f"Another failed check of {v.rego} has come in; it has been "
+                                "reloaded, so look at it and mark it fixed again")
+    row = VehicleRecord(vehicle_id=v.id, kind="fixed", check_id=check_id, note=note,
+                        recorded_by=by, recorded_at=datetime.now(UTC))
+    db.add(row)
+    db.flush()
+    return row
+
+
+def fixed_by(db: Session, check: PrestartCheck) -> VehicleRecord | None:
+    """For a check that grounded its truck: the record that cleared it, if any."""
+    if check.result != "grounded":
+        return None
+    return db.execute(select(VehicleRecord).where(
+        VehicleRecord.vehicle_id == check.vehicle_id, VehicleRecord.kind == "fixed",
+        VehicleRecord.check_id >= check.id).order_by(VehicleRecord.id).limit(1)
+    ).scalar_one_or_none()
```

**Step 4: Run the tests**

Run: `.venv/bin/pytest -q`
Expected: `273 passed`.

**Step 5: Commit**

```bash
git add api/tms/services/records.py api/tms/api/routes/fleet.py api/tests/test_mark_fixed.py
git commit -m "Mark a grounded truck fixed with a note, and show each check as it was sent"
```

### Task 7: Rule 3 on every path, and Start after the pre-start

**Files:**
- Modify: `api/tms/services/runs.py` (`create`, `change`, `assign`, `depart`)
- Modify: `api/tms/services/printing.py` (`queue_run_sheet`)
- Modify: `api/tms/services/deliveries.py` (`start`: `prestart_needed`)
- Modify: `api/tms/api/routes/runs.py` (`vehicle.blocked` and `prestart` on each run)
- Modify: `api/tests/test_start_run.py`, `api/tests/test_deliver.py` (a pre-start before each driver's start), `api/tests/test_runs.py`
- Test: `api/tests/test_grounded_runs.py`

**Step 1: Write the failing tests**

Change `api/tests/test_deliver.py`:
```diff
--- a/api/tests/test_deliver.py
+++ b/api/tests/test_deliver.py
@@ -6,7 +6,7 @@ from sqlalchemy import select
 
 from conftest import all_at_once, fresh_client
 from deliveries_samples import (
-    JPEG, delivered, driver_headers, now, on_the_road, planned_run, put_file,
+    JPEG, delivered, driver_headers, now, on_the_road, planned_run, prestart, put_file,
 )
 from runs_samples import driver
 from tms.models import ConsignmentEvent, InboundMessage, Pod
@@ -159,6 +159,7 @@ def test_a_driver_delivers_only_on_their_own_run(client, db, dispatcher_headers)
 def test_a_message_id_used_for_another_action_is_refused(client, db, dispatcher_headers):
     run, (a, b) = planned_run(client, db, dispatcher_headers)
     kim = driver_headers(client)
+    prestart(client, kim, run["id"])
     message_id = uuid.uuid4()
     started = client.post(f"/v1/runs/{run['id']}/start", headers=kim,
                           json={"message_id": str(message_id)})
```

Create `api/tests/test_grounded_runs.py`:
```python
"""Rule 3: a grounded or overdue truck does not go on a run. Refused
wherever a truck is given a run or sent out (a new run, a change of truck, a
stop added, a run sheet, leaving); a run already planned for it says so
plainly, and can be given another truck or have its stops moved."""
import uuid
from datetime import date, timedelta

import pytest

from deliveries_samples import delivered, driver_headers, on_the_road, planned_run, prestart
from fleet_samples import record
from runs_samples import DAY, assign, consignment, new_run, vehicle

GROUNDED = ("TMS041 is grounded: Brakes failed the pre-start. "
            "A fleet manager has to mark it fixed first.")


@pytest.fixture
def platen(monkeypatch):
    from tms.config import get_settings
    monkeypatch.setattr(get_settings(), "platen_url", "http://platen.test/intake/jobs")


def _grounded_run(client, db, headers):
    run, cons = planned_run(client, db, headers)
    kim = driver_headers(client)
    assert prestart(client, kim, run["id"], failed=("Brakes",)).json()["result"] == "grounded"
    return run, cons, kim


def _refused(r, code="vehicle_grounded", detail=GROUNDED):
    assert r.status_code == 409, r.text
    assert r.json() == {"code": code, "detail": detail}


def test_a_grounded_truck_is_refused_everywhere_it_could_be_sent_out(client, db,
                                                                    dispatcher_headers, platen):
    run, _, kim = _grounded_run(client, db, dispatcher_headers)
    rid = run["id"]
    extra = consignment(db, ref="0080039001")
    _refused(assign(client, dispatcher_headers, rid, extra.id))
    _refused(client.post(f"/v1/runs/{rid}/print", headers=dispatcher_headers))
    _refused(client.post(f"/v1/runs/{rid}/depart", headers=dispatcher_headers))
    _refused(client.post(f"/v1/runs/{rid}/start", headers=kim,
                         json={"message_id": str(uuid.uuid4())}))
    tomorrow = (DAY + timedelta(days=1)).isoformat()
    _refused(client.post("/v1/runs", headers=dispatcher_headers,
                         json={"date": tomorrow, "vehicle": "TMS041", "driver": "kim"}))
    vehicle(db, rego="TMS052")
    other = new_run(client, dispatcher_headers, rego="TMS052", day=DAY + timedelta(days=1))
    _refused(client.patch(f"/v1/runs/{other['id']}", headers=dispatcher_headers,
                          json={"vehicle": "TMS041"}))


def test_a_run_planned_for_a_truck_now_grounded_says_so_and_can_be_mended(
        client, db, dispatcher_headers):
    run, (a, b), _ = _grounded_run(client, db, dispatcher_headers)
    lane = client.get(f"/v1/runs/{run['id']}", headers=dispatcher_headers).json()
    assert lane["vehicle"]["blocked"] == {"code": "vehicle_grounded", "message": GROUNDED}
    assert lane["prestart"]["result"] == "grounded"
    # its stops can still be put in order or taken off, and the driver changed
    r = client.post(f"/v1/runs/{run['id']}/reorder", headers=dispatcher_headers,
                    json={"consignment_ids": [b.id, a.id], "seen": [a.id, b.id]})
    assert r.status_code == 200
    # or it is given another truck, and goes
    vehicle(db, rego="TMS052")
    r = client.patch(f"/v1/runs/{run['id']}", headers=dispatcher_headers,
                     json={"vehicle": "TMS052"})
    assert r.status_code == 200 and r.json()["vehicle"]["blocked"] is None
    assert r.json()["prestart"] is None          # TMS052 has had no check for it
    assert client.post(f"/v1/runs/{run['id']}/depart",
                       headers=dispatcher_headers).status_code == 200


def test_rego_overdue_on_the_runs_day_is_refused_and_the_day_before_is_not(
        client, db, dispatcher_headers, manager_headers):
    v = vehicle(db)
    from runs_samples import driver
    driver(db)
    record(db, v, "rego", expires_on=DAY)
    assert new_run(client, dispatcher_headers, day=DAY)["vehicle"]["blocked"] is None
    r = client.post("/v1/runs", headers=dispatcher_headers, json={
        "date": (DAY + timedelta(days=1)).isoformat(), "vehicle": "TMS041", "driver": "kim"})
    _refused(r, "vehicle_overdue",
             "TMS041's rego runs out on 14 Oct 2026. Record the new expiry in Fleet first.")
    r = client.post("/v1/vehicles/TMS041/rego", headers=manager_headers,
                    json={"expires_on": "2027-10-14", "note": "Renewed for a year"})
    assert r.status_code == 201
    assert client.post("/v1/runs", headers=dispatcher_headers, json={
        "date": (DAY + timedelta(days=1)).isoformat(), "vehicle": "TMS041",
        "driver": "kim"}).status_code == 201


def test_the_driver_does_the_pre_start_before_starting(client, db, dispatcher_headers,
                                                       manager_headers):
    run, _ = planned_run(client, db, dispatcher_headers)
    kim = driver_headers(client)
    start = {"message_id": str(uuid.uuid4())}
    r = client.post(f"/v1/runs/{run['id']}/start", headers=kim, json=start)
    _refused(r, "prestart_needed", f"Do the pre-start check of TMS041 before starting "
                                   f"{run['run_no']}.")
    check = prestart(client, kim, run["id"], failed=("Brakes",)).json()
    _refused(client.post(f"/v1/runs/{run['id']}/start", headers=kim, json=start))
    client.post("/v1/vehicles/TMS041/fix", headers=manager_headers,
                json={"check_id": check["check_id"], "note": "New pads"})
    # the same start, sent again now that the truck is fixed, goes
    r = client.post(f"/v1/runs/{run['id']}/start", headers=kim, json=start)
    assert r.status_code == 200 and r.json()["status"] == "departed"


def test_a_truck_grounded_on_the_road_finishes_its_run(client, db, dispatcher_headers):
    run, (a, b), kim = on_the_road(client, db, dispatcher_headers)
    # a check the phone kept in a black spot arrives after the office sent it
    assert prestart(client, kim, run["id"], failed=("Lights",)).json()["grounded"] is True
    lane = client.get(f"/v1/runs/{run['id']}", headers=dispatcher_headers).json()
    assert lane["vehicle"]["blocked"]["code"] == "vehicle_grounded"
    assert delivered(client, kim, run["id"], a).status_code == 200
    assert date.fromisoformat(lane["date"]) == DAY
```

Change `api/tests/test_runs.py`:
```diff
--- a/api/tests/test_runs.py
+++ b/api/tests/test_runs.py
@@ -9,7 +9,7 @@ def test_a_dispatcher_makes_a_run_for_a_day(client, db, dispatcher_headers):
     assert run["run_no"] == f"R-{run['id']:04d}"
     assert run["date"] == "2026-10-14" and run["status"] == "planned"
     assert run["vehicle"] == {"rego": "TMS041", "type": "van", "max_kg": "1200", "max_m3": "9",
-                              "active": True}
+                              "active": True, "blocked": None}
     assert run["driver"] == {"code": "kim", "name": "Kim"}
     assert run["created_by"] == "dee"
     assert run["stops"] == []
@@ -18,6 +18,7 @@ def test_a_dispatcher_makes_a_run_for_a_day(client, db, dispatcher_headers):
                            "message": None}
     assert run["run_sheet"] is None
     assert run["shipping"] == {"pending": 0, "done": 0, "failed": 0}
+    assert run["prestart"] is None
 
 
 def test_the_rego_is_read_however_it_is_typed(client, db, dispatcher_headers):
```

Change `api/tests/test_start_run.py`:
```diff
--- a/api/tests/test_start_run.py
+++ b/api/tests/test_start_run.py
@@ -5,7 +5,7 @@ import uuid
 from sqlalchemy import select
 
 from conftest import all_at_once, fresh_client
-from deliveries_samples import driver_headers, on_the_road, planned_run
+from deliveries_samples import driver_headers, on_the_road, planned_run, prestart
 from runs_samples import driver
 from tms.models import InboundMessage, OutboundCall
 
@@ -18,6 +18,7 @@ def _start(client, headers, run_id, message_id=None):
 def test_starting_a_planned_run_departs_it(client, db, dispatcher_headers):
     run, cons = planned_run(client, db, dispatcher_headers)
     kim = driver_headers(client)
+    prestart(client, kim, run["id"])
     message_id = uuid.uuid4()
     r = _start(client, kim, run["id"], message_id)
     assert r.status_code == 200, r.text
@@ -40,17 +41,20 @@ def test_starting_a_run_the_office_already_sent_changes_nothing(client, db, disp
 def test_the_same_start_twice_gets_the_same_reply(client, db, dispatcher_headers):
     run, _ = planned_run(client, db, dispatcher_headers)
     kim = driver_headers(client)
+    prestart(client, kim, run["id"])
     message_id = uuid.uuid4()
     first = _start(client, kim, run["id"], message_id)
     again = _start(client, kim, run["id"], message_id)
     assert again.status_code == 200 and again.json() == first.json()
-    seen = db.execute(select(InboundMessage)).scalar_one()
-    assert seen.message_id == message_id and seen.duplicates == 1 and seen.sender.startswith("driver:")
+    seen = db.execute(select(InboundMessage).where(InboundMessage.message_id == message_id)
+                      ).scalar_one()
+    assert seen.duplicates == 1 and seen.sender.startswith("driver:")
 
 
 def test_a_start_sent_four_times_at_once_departs_once(client, db, dispatcher_headers):
     run, _ = planned_run(client, db, dispatcher_headers)
     kim = driver_headers(client)
+    prestart(client, kim, run["id"])
     message_id = str(uuid.uuid4())
 
     def start():
@@ -74,6 +78,7 @@ def test_a_driver_starts_only_their_own_run(client, db, dispatcher_headers):
 def test_a_message_id_is_one_senders(client, db, dispatcher_headers):
     run, _ = planned_run(client, db, dispatcher_headers)
     message_id = uuid.uuid4()
+    prestart(client, driver_headers(client), run["id"])
     _start(client, driver_headers(client), run["id"], message_id)
     driver(db, code="mel", name="Mel")
     r = _start(client, driver_headers(client, code="mel"), run["id"], message_id)
@@ -85,6 +90,8 @@ def test_an_over_full_run_is_not_started_from_the_phone(client, db, dispatcher_h
     run, _ = planned_run(client, db, dispatcher_headers)
     heavy = consignment(db, ref="0080039999", kg="1100")
     assign(client, dispatcher_headers, run["id"], heavy.id)
+    prestart(client, driver_headers(client), run["id"])
     r = _start(client, driver_headers(client), run["id"])
     assert r.status_code == 409 and r.json()["code"] == "over_capacity"
-    assert db.execute(select(InboundMessage)).first() is None
+    assert db.execute(select(InboundMessage).where(
+        InboundMessage.path.endswith("/start"))).first() is None
```

The existing start tests now do Kim's pre-start first; `test_the_same_start_twice_gets_the_same_reply` and `test_an_over_full_run_is_not_started_from_the_phone` look for the start's own `inbound_message`, since the check is one too.

**Step 2: Run them to see them fail**

Run: `.venv/bin/pytest tests/test_grounded_runs.py tests/test_runs.py -q`
Expected: `6 failed`: the grounded and overdue truck is taken (`200`/`201`, not `409`), the start goes without a check, and a run has no `blocked` or `prestart`.

**Step 3: Implement**

Change `api/tms/api/routes/runs.py`:
```diff
--- a/api/tms/api/routes/runs.py
+++ b/api/tms/api/routes/runs.py
@@ -12,7 +12,7 @@ from sqlalchemy import func, select, update
 from tms.api.deps import DB, Principal, require
 from tms.api.schemas import Dec, Page
 from tms.models import Consignment, OutboundCall, Pod, PrintJob, Run
-from tms.services import runs
+from tms.services import fleet, records, runs
 from tms.services.load import consignment_totals
 
 router = APIRouter(tags=["runs"])
@@ -69,6 +69,8 @@ class RunVehicle(BaseModel):
     max_kg: Dec
     max_m3: Dec
     active: bool
+    # why the truck can't take this run (grounded, or overdue on its day), or null
+    blocked: dict | None = None
 
 
 class RunDriver(BaseModel):
@@ -106,6 +108,8 @@ class RunOut(BaseModel):
     load: LoadOut
     run_sheet: RunSheetOut | None
     shipping: ShippingOut
+    # the latest pre-start of its truck for it: { id, at, result }, or null
+    prestart: dict | None = None
 
 
 def stop_consignment(c: Consignment) -> StopConsignment:
@@ -126,10 +130,16 @@ def run_out(db, run: Run) -> RunOut:
                             .where(OutboundCall.run_id == run.id)
                             .group_by(OutboundCall.status)).all())
     v = run.vehicle
+    why = fleet.standing(db, v, run.day).blocked
+    check = records.run_check(db, run)
     return RunOut(
         id=run.id, run_no=run.number, date=run.day, status=run.status,
         vehicle=RunVehicle(rego=v.rego, type=v.type, max_kg=v.max_kg, max_m3=v.max_m3,
-                           active=v.active),
+                           active=v.active,
+                           blocked=None if why is None else {"code": why.code,
+                                                             "message": why.message}),
+        prestart=None if check is None else {"id": check.id, "at": check.at,
+                                             "result": check.result},
         driver=RunDriver(code=run.driver.code, name=run.driver.name),
         created_by=run.created_by, created_at=run.created_at, departed_at=run.departed_at,
         finished_at=run.finished_at,
```

Change `api/tms/services/deliveries.py`:
```diff
--- a/api/tms/services/deliveries.py
+++ b/api/tms/services/deliveries.py
@@ -14,7 +14,7 @@ from sqlalchemy.exc import IntegrityError
 from sqlalchemy.orm import Session
 
 from tms.api.errors import Conflict, FieldError, NotFound, violated_constraint
-from tms.models import ConsignmentEvent, Pod, PodFile, PrestartItem, Run, Stop
+from tms.models import ConsignmentEvent, Pod, PodFile, PrestartCheck, PrestartItem, Run, Stop
 from tms.services import runs
 from tms.services.envelope import sender_of
 
@@ -52,15 +52,26 @@ def driver_runs(db: Session, driver_id: int, day: date) -> list[Run]:
     return [runs.get_run(db, i) for i in ids]
 
 
+def _checked(db: Session, run: Run) -> bool:
+    """Whether the run's truck has had its pre-start for this run."""
+    return db.execute(select(PrestartCheck.id).where(
+        PrestartCheck.run_id == run.id, PrestartCheck.vehicle_id == run.vehicle_id)
+    ).first() is not None
+
+
 def start(db: Session, who, run_id: int) -> Run:
     """The driver is leaving: a planned run departs exactly as the
-    dispatcher's "Leaves now" does (ship calls and all); one that has already
-    left is left as it is."""
+    dispatcher's "Leaves now" does (ship calls and all), once the driver has
+    done the pre-start of its truck; one that has already left is left as it
+    is."""
     run = own_run(db, who, run_id)
     if run.status == "done":
         raise Conflict("run_done", f"{run.number} is finished")
     if run.status == "cancelled":
         raise Conflict("run_cancelled", f"{run.number} was cancelled")
+    if run.status == "planned" and not _checked(db, run):
+        raise Conflict("prestart_needed",
+                       f"Do the pre-start check of {run.vehicle.rego} before starting {run.number}.")
     return runs.depart(db, run_id, by=who.name)
```

Change `api/tms/services/printing.py`:
```diff
--- a/api/tms/services/printing.py
+++ b/api/tms/services/printing.py
@@ -12,6 +12,7 @@ from sqlalchemy.orm import Session
 from tms.api.errors import Conflict
 from tms.config import get_settings
 from tms.models import PrintJob, Run
+from tms.services import fleet
 from tms.services.load import consignment_totals, plain
 from tms.services.runs import get_run, lock_planning, run_load
 
@@ -58,6 +59,9 @@ def queue_run_sheet(db: Session, run_id: int, by: str) -> PrintJob:
     run = get_run(db, run_id)
     if run.status == "cancelled":
         raise Conflict("run_cancelled", f"{run.number} was cancelled")
+    if run.status == "planned":
+        # a sheet for a run its truck can't take would send someone out in it
+        fleet.refuse_if_blocked(db, run.vehicle, run.day)
     if not run.stops:
         raise Conflict("run_empty", f"{run.number} has no stops to print")
     ld = run_load(run)
```

Change `api/tms/services/runs.py`:
```diff
--- a/api/tms/services/runs.py
+++ b/api/tms/services/runs.py
@@ -21,6 +21,7 @@ from tms.api.errors import Conflict, FieldError, NotFound
 from tms.models import (
     Consignment, ConsignmentEvent, Driver, OutboundCall, Run, Stop, Vehicle,
 )
+from tms.services import fleet
 from tms.services.load import Load, consignment_totals, load, total
 
 PLANNING_LOCK = 7_302_201
@@ -91,6 +92,7 @@ def _truck_free(db: Session, vehicle: Vehicle, day: date, other_than: int | None
 def create(db: Session, *, day: date, rego: str, driver_code: str, by: str) -> Run:
     lock_planning(db)
     vehicle, driver = _vehicle(db, rego), _driver(db, driver_code)
+    fleet.refuse_if_blocked(db, vehicle, day)
     _truck_free(db, vehicle, day)
     run = Run(day=day, vehicle_id=vehicle.id, driver_id=driver.id, status="planned",
               created_by=by)
@@ -104,6 +106,7 @@ def change(db: Session, run_id: int, *, rego: str | None, driver_code: str | Non
     run = _planned(db, run_id)
     if rego is not None:
         vehicle = _vehicle(db, rego)
+        fleet.refuse_if_blocked(db, vehicle, run.day)
         _truck_free(db, vehicle, run.day, other_than=run.id)
         run.vehicle_id = vehicle.id
     if driver_code is not None:
@@ -176,6 +179,7 @@ def assign(db: Session, run_id: int, consignment_id: int, position: int | None,
     if not run.vehicle.active:
         raise Conflict("vehicle_unavailable",
                        f"{run.vehicle.rego} is deactivated, so {run.number} can't take stops")
+    fleet.refuse_if_blocked(db, run.vehicle, run.day)
     c = _plannable(db, consignment_id)
     # its stop on a run now; a stop on a trip already made (it has an outcome) is a record
     current = db.execute(select(Stop).where(
@@ -260,6 +264,7 @@ def depart(db: Session, run_id: int, by: str) -> Run:
         raise Conflict("run_empty", f"{run.number} has no stops")
     if not run.vehicle.active:
         raise Conflict("vehicle_unavailable", f"{run.vehicle.rego} is deactivated")
+    fleet.refuse_if_blocked(db, run.vehicle, run.day)
     over = run_load(run)
     if over.state == "over":
         raise Conflict("over_capacity", f"{run.number}: {over.message}")
```

**Step 4: Run the tests**

Run: `.venv/bin/pytest -q`
Expected: `278 passed`.

**Step 5: Commit**

```bash
git add api/tms/services/runs.py api/tms/services/printing.py api/tms/services/deliveries.py \
        api/tms/api/routes/runs.py api/tests/test_grounded_runs.py api/tests/test_start_run.py \
        api/tests/test_deliver.py api/tests/test_runs.py
git commit -m "Keep a grounded or overdue truck off every run, and start only after the pre-start"
```

### Task 8: `docs/api.md`

**Files:**
- Modify: `docs/api.md`

**Step 1: The reference**

Change `docs/api.md`:
```diff
--- a/docs/api.md
+++ b/docs/api.md
@@ -21,7 +21,7 @@ are at `/docs`.
   a `422` too. Something that is not there is `404` with `{ "detail" }`.
   Something well formed that cannot be done as things stand (a run that has
   left, a truck over its limit) is `409` with `{ "detail", "code" }`.
-- **Confirmations from the driver app** (start, deliver, fail, finish) carry a
+- **Confirmations from the driver app** (pre-start, start, deliver, fail, finish) carry a
   `message_id`, a UUID the phone makes when the confirmation is made. The
   first time, the API does the work and keeps the reply; the same
   `message_id` again from the same sender gets that reply back (status and
@@ -31,7 +31,9 @@ are at `/docs`.
   sender used for another confirmation (another action, or the same on
   another run or stop) is `409 message_id_reused`.
 - **Never deleted:** drivers and vehicles are deactivated, runs cancelled and
-  stops taken off, never deleted. A consignment's history is append-only.
+  stops taken off, never deleted. A consignment's history is append-only, and
+  so are pre-start checks and what a fleet manager records about a truck: a
+  mistake is put right by a newer record.
 
 ## Scopes
 
@@ -111,11 +113,101 @@ password, at least 12 characters, is asked for, or given with `--password`).
   of them (no paging).
 - `GET /v1/vehicles/{rego}` — the rego in the URL is read the same way, so
   `/v1/vehicles/1ab2cd` finds `1AB2CD`.
-- `PATCH /v1/vehicles/{rego}` — any of `type`, `max_kg`, `max_m3`. The rego
-  does not change.
+- `PATCH /v1/vehicles/{rego}` — any of `type`, `max_kg`, `max_m3`,
+  `service_every_months`, `service_every_km`. The rego does not change.
 - `POST /v1/vehicles/{rego}/deactivate`, `/reactivate`
 
-Each vehicle: `{ "id", "rego", "type", "max_kg", "max_m3", "active", "created_at" }`.
+`POST` and `PATCH` also take `service_every_months` (1 to 120) and
+`service_every_km` (1 to 1,000,000): how often it is serviced, whichever
+comes first. Leave both out and its service is never due. `GET` takes
+`date=YYYY-MM-DD`, the day due and overdue are judged for: the caller's
+today (the server's clock is UTC); left out, the server's date.
+
+Each vehicle, with where it stands that day:
+```json
+{ "id": 1, "rego": "TMS041", "type": "rigid", "max_kg": "4500", "max_m3": "22",
+  "active": true, "created_at": "…", "service_every_months": 6, "service_every_km": 15000,
+  "odometer_km": 61208,
+  "rego_due": { "expires_on": "2027-03-03", "days_left": 140, "state": "ok" },
+  "service_due": { "last_on": "2026-07-14", "last_km": 46900, "due_on": "2027-01-14",
+                   "due_km": 61900, "days_left": 92, "km_left": 692, "state": "due" },
+  "grounded": null,
+  "last_prestart": { "id": 12, "at": "…", "result": "passed", "by": "kim" },
+  "state": "due", "blocked": null }
+```
+Kilometres are whole numbers. `odometer_km` is the highest reading from a
+pre-start or a service since the last time a fleet manager put the reading
+right (or that reading). `rego_due` and `service_due` are each `ok`, `due`
+(30 days or 1,000 km or less), `overdue` (the day after the expiry or due
+date, or past the due kilometres) or `unknown` (nothing recorded to judge
+by: it does not stop the truck). The service falls due `service_every_months`
+after the last service's date and `service_every_km` after its reading,
+whichever comes first. `grounded` is the check that grounded it,
+`{ "check_id", "at", "failed": ["Brakes"] }`, until a fleet manager marks it
+fixed. `state` is the worst of them: `grounded`, `overdue`, `due` or `ok`.
+`blocked` is `{ "code", "message" }` when it can't take a run that day (rule
+3): `vehicle_grounded` ("TMS041 is grounded: Brakes failed the pre-start. A
+fleet manager has to mark it fixed first.") or `vehicle_overdue` ("TMS041's
+rego runs out on 3 Oct 2026. Record the new expiry in Fleet first.", or the
+service by date or kilometres).
+
+## Fleet care
+
+### Checklists
+- `GET /v1/checklists` (`fleet:read`) — each type's pre-start checklist, in
+  order: `{ "van": [{ "id": 1, "label": "Tyres", "critical": false }, …],
+  "rigid": […], "ute": […] }`. A fresh install has the design's six for each
+  type: Tyres, Lights (critical), Brakes (critical), Mirrors, Load
+  restraints, Damage.
+- `PUT /v1/checklists/{type}` (`fleet:write`) — `{ "items": [{ "label",
+  "critical"? }] }`, 1 to 30, each label once (any case): the type's whole
+  list, in order. A line with the same label and `critical` as before keeps
+  its id; the others are retired, never deleted. Each check keeps its own
+  copy of what it asked, so changing a list never changes a check.
+
+### What a fleet manager records
+`fleet:write` (managers and admins). Each is a new record with a `note`
+(required, at most 500) and answers `201`:
+```json
+{ "id": 4, "vehicle": "TMS041", "kind": "service", "check_id": null, "done_on": "2026-10-13",
+  "expires_on": null, "odometer_km": 61190, "note": "60,000 km service",
+  "recorded_by": "fleet.mgr", "recorded_at": "…" }
+```
+- `POST /v1/vehicles/{rego}/rego` — `{ "expires_on", "note" }`. The newest
+  rego record is the expiry.
+- `POST /v1/vehicles/{rego}/service` — `{ "done_on", "odometer_km"?, "note" }`.
+  The next service falls due from the newest one.
+- `POST /v1/vehicles/{rego}/odometer` — `{ "odometer_km", "note" }`: the reading
+  put right (a slip of the finger, a new odometer). Readings from before it
+  no longer count; it may be lower than them.
+- `POST /v1/vehicles/{rego}/fix` — `{ "check_id", "note" }`: what grounded it
+  is put right. `check_id` is the check the fleet manager looked at, which
+  must be the latest that grounded the truck: `409 stale` if another came in
+  meanwhile (look at it, then mark it fixed again), `409 not_grounded` if it
+  isn't grounded. It clears that check and every one before it; the checks
+  themselves never change.
+- `GET /v1/vehicles/{rego}/history?limit=50` (`fleet:read`) — its checks and
+  records, newest first: `[{ "kind": "prestart" | "fixed" | "service" | "rego"
+  | "odometer", "id", "recorded_at", "by", "note", "result", "failed",
+  "odometer_km", "done_on", "expires_on", "check_id" }]` (a check's `failed`
+  names every item that failed).
+
+### GET /v1/prestarts/{id}
+`fleet:read`. One check as the driver sent it:
+```json
+{ "id": 12, "run_id": 7, "run_no": "R-0007", "vehicle": "TMS027", "vehicle_type": "ute",
+  "driver": { "code": "tom", "name": "Tom" }, "at": "…", "recorded_at": "…",
+  "odometer_km": 84312, "result": "grounded",
+  "items": [{ "label": "Lights", "critical": true, "passed": false,
+              "note": "Left brake light out.", "photo_file": "3f2a…" }],
+  "fixed": { "id": 5, "note": "Replaced the globe, tested", "by": "fleet.mgr", "at": "…" } }
+```
+`result` is `passed`, `failed` (an item that isn't critical failed: the truck
+may go) or `grounded`. `fixed` is the record that cleared a check that
+grounded the truck, else `null`. The photos are at `GET /v1/pod-files/{id}`.
+
+Simple TMS doesn't send `vehicle.due` yet: due dates show on the desktop
+only. It comes with the first system that subscribes.
 
 ## Drivers
 
@@ -194,7 +286,8 @@ Each run:
 ```json
 {
   "id": 7, "run_no": "R-0007", "date": "2026-10-14", "status": "planned",
-  "vehicle": { "rego": "TMS041", "type": "rigid", "max_kg": "4500", "max_m3": "22", "active": true },
+  "vehicle": { "rego": "TMS041", "type": "rigid", "max_kg": "4500", "max_m3": "22", "active": true,
+               "blocked": null },
   "driver": { "code": "kim", "name": "Kim" },
   "created_by": "dee", "created_at": "2026-10-13T22:01:09.114Z", "departed_at": null,
   "finished_at": null,
@@ -205,7 +298,8 @@ Each run:
   "load": { "weight_kg": "72", "volume_m3": "0.5", "max_kg": "4500", "max_m3": "22",
             "unknown_weight": 0, "unknown_volume": 0, "state": "ok", "message": null },
   "run_sheet": null,
-  "shipping": { "pending": 0, "done": 0, "failed": 0 }
+  "shipping": { "pending": 0, "done": 0, "failed": 0 },
+  "prestart": null
 }
 ```
 `status` is `planned`, `departed`, `done` (the driver finished it) or
@@ -221,7 +315,20 @@ rounded down. `unknown_weight` and `unknown_volume` count packages with no
 weight, or without all three sides, which add nothing. `run_sheet` is the
 latest print of it (see Run sheets), `null` until it is first printed:
 `{ "job_id", "status", "printer", "last_error", "created_at", "printed_at" }`.
-`shipping` counts its ship calls to Simple WMS by state.
+`shipping` counts its ship calls to Simple WMS by state. `vehicle.blocked`
+is why its truck can't take it, on its day (see Vehicles), else `null`: a run
+planned for a truck that is then grounded stays as it is and says so.
+`prestart` is the latest pre-start of its truck for it, `{ "id", "at",
+"result" }`, else `null`.
+
+**Rule 3: a grounded or overdue truck does not go on a run.** Making a run
+for it (`POST /v1/runs`), giving a run that truck (`PATCH`), putting a stop on
+its run (`assign`), printing its run sheet while planned, and departing or
+starting it are each `409 vehicle_grounded` or `409 vehicle_overdue`, with
+the message from `blocked`. Overdue is judged for the run's own day, so a
+truck whose rego runs out on the 14th can go on the 14th but not on the 15th.
+Taking stops off, putting them in order, a new driver and cancelling are
+still allowed, and so is everything on a run that has already left.
 
 - `GET /v1/runs?date=2026-10-14` — that day's runs, oldest first, as
   `{ "items", "total" }`. Cancelled runs only with `status=cancelled` (or a
@@ -234,7 +341,8 @@ latest print of it (see Run sheets), `null` until it is first printed:
   `vehicle` or `driver`.
 - `GET /v1/runs/{id}`
 - `PATCH /v1/runs/{id}` — `vehicle` and/or `driver`, read and refused as
-  above, while it is planned (`409 run_not_planned` after).
+  above, while it is planned (`409 run_not_planned` after). Giving a run
+  whose truck can't go another truck that can is how it is mended.
 - `POST /v1/runs/{id}/cancel` — only an empty planned run: `409 run_not_empty`
   while it has stops, `409 run_not_planned` once it has departed. Cancelling a
   cancelled run is a no-op.
@@ -265,6 +373,7 @@ Each answers with the run as it now is.
 | `run_not_planned` | the run has departed or been cancelled; its stops are fixed |
 | `not_plannable` | the consignment has left or been delivered |
 | `vehicle_unavailable` | the run's truck has been deactivated |
+| `vehicle_grounded`, `vehicle_overdue` | the run's truck can't go (rule 3) |
 | `stale` | a reorder from an order that is not the run's as it is now |
 
 An unknown consignment is a `422` on `consignment_id`; an unknown run `404`.
@@ -277,7 +386,9 @@ queued for each, all in one go. Departing a departed run changes nothing and
 queues nothing more. Refused with `409`: `run_empty`, `vehicle_unavailable`,
 `over_capacity` (the detail is the run number and the load's message:
 "R-0007: 280 kg too heavy. Move a stop to another run before printing the run
-sheet."), `run_not_planned` for a cancelled run. The driver's
+sheet."), `vehicle_grounded`, `vehicle_overdue`, `run_not_planned` for a
+cancelled run. It doesn't need a pre-start: the office sends a driver
+without a phone. The driver's
 `POST /v1/runs/{id}/start` does the same.
 
 The worker then makes, for each consignment:
@@ -302,7 +413,8 @@ logged as an error naming the likely cause. Without `TMS_WMS_URL` and
 
 - `POST /v1/runs/{id}/print` → `202` with the print job. Each call prints
   again. Refused with `409`: `printing_not_set_up` (no `TMS_PLATEN_URL`),
-  `run_empty`, `run_cancelled`, `over_capacity`.
+  `run_empty`, `run_cancelled`, `over_capacity`, and for a planned run
+  `vehicle_grounded`, `vehicle_overdue`.
 
 ```json
 { "job_id": "uuid", "run_id": 7, "template": "run-sheet", "printer": "dispatch",
@@ -351,9 +463,43 @@ is UTC), not cancelled; and any of theirs from earlier days that left and
 were never finished. Only for a driver: anyone else is `403`.
 ```json
 [{ "id": 7, "run_no": "R-0007", "date": "2026-10-14", "status": "planned",
-   "vehicle": { "rego": "TMS041", "type": "rigid" },
-   "stops": 12, "packages": 46, "delivered": 0, "failed": 0 }]
+   "vehicle": { "rego": "TMS041", "type": "rigid", "odometer_km": 61208, "blocked": null },
+   "stops": 12, "packages": 46, "delivered": 0, "failed": 0,
+   "prestart": null,
+   "checklist": [{ "id": 7, "label": "Tyres", "critical": false }, …] }]
+```
+`vehicle.odometer_km` is the truck's last reading (a new one may not be
+lower), `vehicle.blocked` why it can't take the run (see Vehicles),
+`prestart` the latest pre-start of the truck for this run, and `checklist`
+the truck's checklist as it is now.
+
+### POST /v1/runs/{id}/prestart
+The pre-start check of the run's truck, before starting:
+```json
+{ "message_id": "…", "at": "2026-10-14T06:40:00+11:00", "odometer_km": 61208,
+  "items": [{ "item_id": 7, "result": "pass" },
+            { "item_id": 11, "result": "fail", "note": "Rear strap frayed",
+              "photo_file": "…" }] }
+```
+Each item of the truck's checklist answered once (`409 checklist_changed` if
+the list has changed since: do the check again). A failed item needs a
+`note` and a `photo_file` (a photo sent first with `PUT /v1/pod-files`, by
+the same sender, not already a stop's or another item's); a passed one takes
+neither (`422`). `odometer_km` is whole kilometres and may not be lower than
+the truck's last reading (`422` on `odometer_km`). →
+```json
+{ "message_id": "…", "check_id": 12, "run_id": 7, "run_no": "R-0007", "vehicle": "TMS041",
+  "result": "failed", "failed": ["Load restraints"], "grounded": false,
+  "odometer_km": 61208, "at": "…" }
 ```
+A failed critical item grounds the truck there and then (`"result":
+"grounded"`); any other failed item is kept and the truck may go
+(`"failed"`). `grounded` says whether the truck is grounded now, by this
+check or an earlier one. Taken on a planned run or one on the road (a check
+kept on the phone in a black spot still counts); `409 run_done`,
+`run_cancelled` after. Kept as sent, for good, once per `message_id`. Another
+check may follow (each is kept); a passed one never clears a grounding: only
+a fleet manager does.
 
 ### GET /v1/runs/{id}/stops
 The whole run as the phone keeps it for the day: the run's driver with
@@ -378,11 +524,15 @@ The whole run as the phone keeps it for the day: the run's driver with
 The driver is leaving: a planned run departs exactly as
 `POST /v1/runs/{id}/depart` does (history `departed` by the driver's code, a
 ship call to Simple WMS per consignment); a run that has already left is left
-as it is. `409`: `run_done`, `run_cancelled`, and depart's `run_empty`,
-`vehicle_unavailable`, `over_capacity`.
+as it is. A planned run starts only once its truck's pre-start for it is in:
+`409 prestart_needed` ("Do the pre-start check of TMS041 before starting
+R-0007.") until then. `409`: `run_done`, `run_cancelled`, `prestart_needed`,
+and depart's `run_empty`, `vehicle_unavailable`, `vehicle_grounded`,
+`vehicle_overdue`, `over_capacity`.
 
 ### PUT /v1/pod-files/{file_id}?kind=signature|photo
-A signature or a photo, sent before the confirmation that names it.
+A signature or a photo, sent before the confirmation that names it (a
+delivery, a failed delivery, or a pre-start's failed item).
 `file_id` is a UUID the phone chose when the picture was made; the body is
 the picture (`Content-Type: image/png` or `image/jpeg`). What counts is its
 first bytes: a signature is a PNG of at most 1 MB, a photo a JPEG or PNG of
@@ -452,6 +602,8 @@ left as it is.
 | `message_id_taken` | someone else sent that `message_id` |
 | `message_id_reused` | that `message_id` was used for another confirmation |
 | `file_id_taken` | that file id is already another picture |
+| `prestart_needed` | start before the truck's pre-start for the run is in |
+| `checklist_changed` | a pre-start that doesn't answer the checklist as it is now |
 
 A consignment that is not on the run is `404`.
```

**Step 2: Check it against the routes**

Run: `cd api && TMS_SECRET_KEY=0123456789abcdef0123456789abcdef01 .venv/bin/python -c "from tms.api.app import app; print(sorted(p for p in app.openapi()['paths'] if 'checklist' in p or 'prestart' in p or p.count('/')==4 and p.startswith('/v1/vehicles/{rego}/') and not p.endswith('activate')))"`
Expected: `['/v1/checklists', '/v1/checklists/{vehicle_type}', '/v1/prestarts/{check_id}', '/v1/runs/{id}/prestart', '/v1/vehicles/{rego}/fix', '/v1/vehicles/{rego}/history', '/v1/vehicles/{rego}/odometer', '/v1/vehicles/{rego}/rego', '/v1/vehicles/{rego}/service']`, each one in `docs/api.md`.

**Step 3: Commit**

```bash
git add docs/api.md
git commit -m "Document fleet care in the API reference"
```

## Part B — the driver app (`apps/driver`)

### Task 9: A picture inside a list, and the pre-start's types

**Files:**
- Modify: `apps/driver/src/api/types.ts` (`DriverRun`'s truck, check and checklist; `PrestartOut`)
- Modify: `apps/driver/src/lib/queue.ts` (`outcome: "prestart"`; a picture's field may be a dotted path; the body is copied whole)
- Test: `apps/driver/src/test/queue.test.ts`

**Step 1: Write the failing test**

Change `apps/driver/src/test/queue.test.ts`:
```diff
--- a/apps/driver/src/test/queue.test.ts
+++ b/apps/driver/src/test/queue.test.ts
@@ -84,6 +84,20 @@ describe("the offline queue", () => {
     expect(files.size()).toBe(0);
   });
 
+  it("puts a picture's id inside a list when the body names it there, and leaves the caller's body alone", async () => {
+    const q = queue();
+    const items = [{ item_id: 1, result: "pass" }, { item_id: 2, result: "fail", note: "Out" }];
+    const a = await q.submit({
+      path: "/v1/runs/7/prestart", body: { odometer_km: 61208, items }, label: "Pre-start · TMS041",
+      run_id: 7, consignment_id: null, outcome: "prestart",
+      pictures: [{ kind: "photo", field: "items.1.photo_file", blob: PNG() }],
+    });
+    await q.drain();
+    expect(body(sent[1])).toEqual({ message_id: a.message_id, odometer_km: 61208, items: [
+      { item_id: 1, result: "pass" }, { item_id: 2, result: "fail", note: "Out", photo_file: a.files[0].id }] });
+    expect(items[1]).toEqual({ item_id: 2, result: "fail", note: "Out" });
+  });
+
   it("waits without signal, then sends everything in order, once each", async () => {
     online = false;
     const q = queue();
```

**Step 2: Run it to see it fail**

Run: `npx vitest run src/test/queue.test.ts`
Expected: `1 failed`: the id lands in a top-level `"items.1.photo_file"` field, and the caller's `items[1]` is changed.

**Step 3: Implement**

Change `apps/driver/src/api/types.ts`:
```diff
--- a/apps/driver/src/api/types.ts
+++ b/apps/driver/src/api/types.ts
@@ -15,16 +15,40 @@ export interface DriverSessionOut {
   driver: Me;
 }
 
+/** Why a truck can't take its run: grounded, or rego or service overdue. */
+export interface Blocked { code: string; message: string }
+
+/** One line of the truck's pre-start checklist. */
+export interface ChecklistLine { id: number; label: string; critical: boolean }
+
+export type CheckResult = "passed" | "failed" | "grounded";
+
 export interface DriverRun {
   id: number;
   run_no: string;
   date: string;
   status: "planned" | "departed" | "done" | "cancelled";
-  vehicle: { rego: string; type: string };
+  vehicle: { rego: string; type: string; odometer_km?: number | null; blocked?: Blocked | null };
   stops: number;
   packages: number;
   delivered: number;
   failed: number;
+  /** the latest pre-start of this truck for this run, if done */
+  prestart?: { id: number; at: string; result: CheckResult } | null;
+  checklist?: ChecklistLine[];
+}
+
+export interface PrestartOut {
+  message_id: string;
+  check_id: number;
+  run_id: number;
+  run_no: string;
+  vehicle: string;
+  result: CheckResult;
+  failed: string[];
+  grounded: boolean;
+  odometer_km: number;
+  at: string;
 }
 
 export interface ShipTo {
```

Change `apps/driver/src/lib/queue.ts`:
```diff
--- a/apps/driver/src/lib/queue.ts
+++ b/apps/driver/src/lib/queue.ts
@@ -44,9 +44,9 @@ export interface QueueItem {
   files: QueuedFile[];
   label: string;
   run_id: number;
-  /** the stop it records, with what it says happened there */
+  /** the stop it records, with what it says happened there (a pre-start is the truck's) */
   consignment_id: number | null;
-  outcome: "delivered" | "failed" | "finished";
+  outcome: "delivered" | "failed" | "finished" | "prestart";
   created_at: string;
   status: "queued" | "sent" | "failed";
   attempts: number;
@@ -64,7 +64,8 @@ export interface NewItem {
   run_id: number;
   consignment_id: number | null;
   outcome: QueueItem["outcome"];
-  /** each picture, and the body field its id goes in (signature_file, photo_file) */
+  /** each picture, and the body field its id goes in (signature_file, photo_file; a
+   * dotted path into the body for one inside a list: items.4.photo_file) */
   pictures?: { kind: PictureKind; field: string; blob: Blob }[];
 }
 
@@ -104,6 +105,14 @@ function refused(status: number): boolean {
   return status >= 400 && status < 500 && status !== 401 && status !== 408 && status !== 429;
 }
 
+/** Put a picture's id where the body names it: photo_file, or items.4.photo_file. */
+function place(body: Record<string, unknown>, field: string, id: string) {
+  const keys = field.split(".");
+  let at = body as Record<string, unknown>;
+  for (const k of keys.slice(0, -1)) at = at[k] as Record<string, unknown>;
+  at[keys[keys.length - 1]] = id;
+}
+
 export function describe(body: unknown, status: number): string {
   const errors = (body as { errors?: { field: string; message: string }[] })?.errors;
   if (errors?.length) return errors.map((e) => e.message).join("; ");
@@ -300,7 +309,8 @@ export class RetryQueue {
     const driver = this.driver;
     if (!driver) throw new Error("Sign in again to keep this on the phone.");
     const files: QueuedFile[] = [];
-    const body = { ...input.body };
+    // a copy of its own, lists and all: the ids go into it, not the caller's
+    const body = JSON.parse(JSON.stringify(input.body)) as Record<string, unknown>;
     for (const p of input.pictures ?? []) {
       const id = crypto.randomUUID();
       // on the phone before the confirmation that names it, so a reload never finds one without the other
@@ -311,7 +321,7 @@ export class RetryQueue {
         throw new Error(NOT_KEPT);
       }
       files.push({ id, kind: p.kind, sent: false });
-      body[p.field] = id;
+      place(body, p.field, id);
     }
     const item: QueueItem = {
       id: crypto.randomUUID(), message_id: crypto.randomUUID(), driver_id: driver.id, driver_name: driver.name,
```

**Step 4: Run the tests**

Run: `npm test && npm run typecheck`
Expected: `77 passed`, and no type errors.

**Step 5: Commit**

```bash
git add apps/driver/src/api/types.ts apps/driver/src/lib/queue.ts apps/driver/src/test/queue.test.ts
git commit -m "Let a queued confirmation carry pictures inside a list"
```

### Task 10: The pre-start check on the phone, between picking the run and Start

**Files:**
- Create: `apps/driver/src/pages/PrestartCheck.tsx`
- Modify: `apps/driver/src/pages/Today.tsx`
- Modify: `apps/driver/src/test/samples.ts` (`CHECKLIST`, `TODAY`), `apps/driver/src/test/harness.tsx`, `apps/driver/src/test/Today.test.tsx`
- Test: `apps/driver/src/test/Prestart.test.tsx`

**Step 1: Write the failing tests**

Create `apps/driver/src/test/Prestart.test.tsx`:
```tsx
import { act, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { DriverRun } from "../api/types";
import { readOdometer } from "../pages/PrestartCheck";
import { json, renderApp, signedIn, stubApi, TODAY } from "./harness";

vi.mock("../lib/picture", () => ({
  shrinkPhoto: async () => new Blob([new Uint8Array([0xff, 0xd8, 0xff])], { type: "image/jpeg" }),
  toBlob: async () => new Blob([new Uint8Array([0x89, 0x50])], { type: "image/png" }),
}));

const UNCHECKED: DriverRun = { ...TODAY, prestart: null };
const GROUNDED = "TMS041 is grounded: Brakes failed the pre-start. A fleet manager has to mark it fixed first.";

/** The runs list as the office has it: unchecked until a pre-start arrives, then
 * as `after` says (checked and free to go, unless told otherwise). */
function office(after: Partial<DriverRun> = {}) {
  let checked = false;
  return stubApi((method, url) => {
    if (url.startsWith("/v1/driver/runs?date=")) {
      return json(200, [checked ? { ...TODAY, ...after } : UNCHECKED]);
    }
    if (method === "PUT") return json(201, {});
    if (url === "/v1/runs/7/prestart") {
      checked = true;
      return json(200, { result: after.vehicle?.blocked ? "grounded" : "passed" });
    }
    return null;
  });
}

async function passAll(user: ReturnType<typeof userEvent.setup>, except: string[] = []) {
  for (const label of ["Tyres", "Lights", "Brakes", "Mirrors", "Load restraints", "Damage"]) {
    if (except.includes(label)) continue;
    await user.click(within(screen.getByRole("radiogroup", { name: label })).getByRole("radio", { name: "Pass" }));
  }
}

describe("the pre-start check", () => {
  beforeEach(() => {
    signedIn();
    URL.createObjectURL = vi.fn(() => "blob:photo");
    URL.revokeObjectURL = vi.fn();
  });
  afterEach(() => vi.unstubAllGlobals());

  it("reads the odometer as the driver types it", () => {
    expect(readOdometer("61,208")).toBe(61208);
    expect(readOdometer(" 61 208 ")).toBe(61208);
    expect(readOdometer("61.2")).toBeNull();
    expect(readOdometer("")).toBeNull();
  });

  it("comes before Start, and once the office has it the run can start", async () => {
    const { calls } = office();
    renderApp("/");
    const user = userEvent.setup();
    expect(await screen.findByRole("region", { name: "Checklist for a rigid" })).toHaveTextContent("rigid checklist · 0 of 6 pass");
    expect(screen.queryByRole("button", { name: "Start run" })).not.toBeInTheDocument();
    const finish = screen.getByRole("button", { name: "Finish check" });
    await passAll(user);
    expect(finish).toBeDisabled();                 // no odometer yet
    await user.type(screen.getByLabelText(/Odometer/), "61,208");
    await user.click(finish);
    expect(await screen.findByRole("button", { name: "Start run" })).toBeInTheDocument();
    expect(screen.getByText(/Pre-start done .*: passed\./)).toBeInTheDocument();
    const sent = calls.find((c) => c.url === "/v1/runs/7/prestart")!.body as Record<string, unknown>;
    expect(sent).toEqual({ message_id: expect.any(String), at: expect.any(String), odometer_km: 61208,
                           items: [7, 8, 9, 10, 11, 12].map((id) => ({ item_id: id, result: "pass" })) });
  });

  it("asks a photo and a note of a failed item, and sends the photo first", async () => {
    const { calls } = office({ prestart: { id: 2, at: "2026-10-14T19:52:00Z", result: "failed" } });
    renderApp("/");
    const user = userEvent.setup();
    await screen.findByRole("region", { name: "Checklist for a rigid" });
    await user.type(screen.getByLabelText(/Odometer/), "61208");
    await passAll(user, ["Load restraints"]);
    await user.click(within(screen.getByRole("radiogroup", { name: "Load restraints" })).getByRole("radio", { name: "Fail" }));
    expect(screen.getByText("A fail needs a photo and a note.")).toBeInTheDocument();
    expect(screen.getByText(/Not a critical item, so you can still go/)).toBeInTheDocument();
    const finish = screen.getByRole("button", { name: "Finish check" });
    await user.type(screen.getByLabelText("Load restraints: note"), "Rear strap frayed near the hook.");
    expect(finish).toBeDisabled();
    await user.upload(screen.getByLabelText(/Add a photo/), new File(["x"], "strap.jpg", { type: "image/jpeg" }));
    await user.click(finish);
    expect(await screen.findByText(/Pre-start done .*: an item failed, and the office will see it\./)).toBeInTheDocument();
    const put = calls.find((c) => c.method === "PUT")!;
    const items = (calls.find((c) => c.url === "/v1/runs/7/prestart")!.body as { items: unknown[] }).items;
    expect(items[4]).toEqual({ item_id: 11, result: "fail", note: "Rear strap frayed near the hook.",
                               photo_file: put.url.split("/")[3].split("?")[0] });
    expect(calls.findIndex((c) => c.method === "PUT")).toBeLessThan(calls.findIndex((c) => c.url === "/v1/runs/7/prestart"));
  });

  it("says plainly when a critical item grounds the truck, and offers no Start", async () => {
    office({ prestart: { id: 2, at: "2026-10-14T19:52:00Z", result: "grounded" },
             vehicle: { ...TODAY.vehicle, blocked: { code: "vehicle_grounded", message: GROUNDED } } });
    renderApp("/");
    const user = userEvent.setup();
    await screen.findByRole("region", { name: "Checklist for a rigid" });
    await user.type(screen.getByLabelText(/Odometer/), "61208");
    await passAll(user, ["Brakes"]);
    await user.click(within(screen.getByRole("radiogroup", { name: "Brakes" })).getByRole("radio", { name: "Fail" }));
    expect(screen.getByText(/A critical item: the truck can't go/)).toBeInTheDocument();
    await user.type(screen.getByLabelText("Brakes: note"), "Pedal goes to the floor.");
    await user.upload(screen.getByLabelText(/Add a photo/), new File(["x"], "brakes.jpg", { type: "image/jpeg" }));
    await user.click(screen.getByRole("button", { name: "Finish check" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(`${GROUNDED} Don't take it out: call the office.`);
    expect(screen.getByRole("radio", { name: /TMS041/ })).toHaveTextContent("Grounded · R-0007");
    expect(screen.queryByRole("button", { name: "Start run" })).not.toBeInTheDocument();
  });

  it("keeps a check made with no signal, and the run starts once it has gone", async () => {
    let online = false;
    const { calls } = office();
    const { queue } = renderApp("/", { online: () => online });
    const user = userEvent.setup();
    await screen.findByRole("region", { name: "Checklist for a rigid" });
    await user.type(screen.getByLabelText(/Odometer/), "61208");
    await passAll(user);
    await user.click(screen.getByRole("button", { name: "Finish check" }));
    expect(await screen.findByText(/Your pre-start is kept on this phone/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Start run" })).not.toBeInTheDocument();
    expect(calls.some((c) => c.url === "/v1/runs/7/prestart")).toBe(false);
    online = true;
    await act(async () => { await queue.drain(); });
    expect(await screen.findByRole("button", { name: "Start run" })).toBeInTheDocument();
    expect(calls.filter((c) => c.url === "/v1/runs/7/prestart")).toHaveLength(1);
  });

  it("won't take a reading below the truck's last one", async () => {
    office();
    renderApp("/");
    const user = userEvent.setup();
    await screen.findByRole("region", { name: "Checklist for a rigid" });
    expect(screen.getByText("Last 61,000 km")).toBeInTheDocument();
    await passAll(user);
    await user.type(screen.getByLabelText(/Odometer/), "6100");
    expect(screen.getByRole("alert")).toHaveTextContent("That's less than the last reading, 61,000 km.");
    expect(screen.getByRole("button", { name: "Finish check" })).toBeDisabled();
  });

  it("says why a check wasn't taken, and lets the driver do it again", async () => {
    stubApi((_method, url) => {
      if (url.startsWith("/v1/driver/runs?date=")) return json(200, [UNCHECKED]);
      if (url === "/v1/runs/7/prestart") {
        return json(409, { code: "checklist_changed", detail: "The rigid checklist has changed since this check began. Do the check again." });
      }
      return null;
    });
    renderApp("/");
    const user = userEvent.setup();
    await screen.findByRole("region", { name: "Checklist for a rigid" });
    await user.type(screen.getByLabelText(/Odometer/), "61208");
    await passAll(user);
    await user.click(screen.getByRole("button", { name: "Finish check" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Your pre-start wasn't taken: The rigid checklist has changed");
    await user.click(screen.getByRole("button", { name: "Do the check again" }));
    expect(await screen.findByRole("region", { name: "Checklist for a rigid" })).toBeInTheDocument();
  });
});
```

Change `apps/driver/src/test/Today.test.tsx`:
```diff
--- a/apps/driver/src/test/Today.test.tsx
+++ b/apps/driver/src/test/Today.test.tsx
@@ -1,10 +1,7 @@
 import { act, cleanup, screen, waitFor } from "@testing-library/react";
 import userEvent from "@testing-library/user-event";
 import { loadRun } from "../lib/run";
-import { json, KIM, MEL, onThePhone, renderApp, RUN, signedIn, stubApi } from "./harness";
-
-const TODAY = { id: 7, run_no: "R-0007", date: "2026-10-14", status: "planned",
-                vehicle: { rego: "TMS041", type: "rigid" }, stops: 2, packages: 4, delivered: 0, failed: 0 };
+import { json, KIM, MEL, onThePhone, renderApp, RUN, signedIn, stubApi, TODAY } from "./harness";
 
 describe("today's run", () => {
   beforeEach(() => signedIn());
```

Change `apps/driver/src/test/harness.tsx`:
```diff
--- a/apps/driver/src/test/harness.tsx
+++ b/apps/driver/src/test/harness.tsx
@@ -9,7 +9,7 @@ import { RetryQueue } from "../lib/queue";
 import { saveRun } from "../lib/run";
 import { RUN } from "./samples";
 
-export { RUN, stop } from "./samples";
+export { CHECKLIST, RUN, stop, TODAY } from "./samples";
 
 export function json(status: number, body: unknown) {
   return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
```

Change `apps/driver/src/test/samples.ts`:
```diff
--- a/apps/driver/src/test/samples.ts
+++ b/apps/driver/src/test/samples.ts
@@ -1,5 +1,6 @@
-/** A run of two stops, as GET /v1/runs/{id}/stops gives it. */
-import type { RunStops, StopDetail } from "../api/types";
+/** A run of two stops, as GET /v1/runs/{id}/stops gives it; and as today's
+ * runs list it, with its truck's checklist. */
+import type { ChecklistLine, DriverRun, RunStops, StopDetail } from "../api/types";
 
 export const stop = (n: number, customer: string, over: Partial<StopDetail> = {}): StopDetail => ({
   sequence: n, consignment_id: 30 + n, wms_ref: `00800345${n}`, customer,
@@ -18,3 +19,17 @@ export const RUN: RunStops = {
   departed_at: "2026-10-14T21:10:00Z", finished_at: null,
   stops: [stop(1, "Hillside Bakery"), stop(2, "Two Rivers Vet")],
 };
+
+export const CHECKLIST: ChecklistLine[] = [
+  { id: 7, label: "Tyres", critical: false }, { id: 8, label: "Lights", critical: true },
+  { id: 9, label: "Brakes", critical: true }, { id: 10, label: "Mirrors", critical: false },
+  { id: 11, label: "Load restraints", critical: false }, { id: 12, label: "Damage", critical: false },
+];
+
+/** R-0007 as GET /v1/driver/runs gives it: planned, its pre-start done unless said. */
+export const TODAY: DriverRun = {
+  id: 7, run_no: "R-0007", date: "2026-10-14", status: "planned",
+  vehicle: { rego: "TMS041", type: "rigid", odometer_km: 61000, blocked: null },
+  stops: 2, packages: 4, delivered: 0, failed: 0,
+  prestart: { id: 1, at: "2026-10-14T19:52:00Z", result: "passed" }, checklist: CHECKLIST,
+};
```

`TODAY` moves to `samples.ts` with its pre-start done, so the existing Today tests still start the run straight away.

**Step 2: Run them to see them fail**

Run: `npx vitest run src/test/Prestart.test.tsx`
Expected: `Failed to resolve import "../pages/PrestartCheck"`.

**Step 3: Implement**

Create `apps/driver/src/pages/PrestartCheck.tsx`:
```tsx
import { useRef, useState } from "react";
import type { ChecklistLine, DriverRun } from "../api/types";
import { useSession } from "../auth/Session";
import { PhotoPicker } from "../ui/pictures";

interface Answer { result: "pass" | "fail" | null; note: string; photo: Blob | null }

const km = (n: number) => n.toLocaleString("en-AU");

/** "61,208" or "61 208" as typed: whole kilometres, or null. */
export function readOdometer(text: string): number | null {
  const digits = text.replace(/[\s,]/g, "");
  return /^\d{1,7}$/.test(digits) ? Number(digits) : null;
}

function PassFail({ line, answer, onChange }: { line: ChecklistLine; answer: Answer; onChange: (a: Answer) => void }) {
  const button = (value: "pass" | "fail", label: string, on: string) => {
    const chosen = answer.result === value;
    return (
      <button type="button" role="radio" aria-checked={chosen} onClick={() => onChange({ ...answer, result: value })}
              style={{ height: 38, minWidth: 60, borderRadius: 12, fontWeight: 800, fontSize: 14, cursor: "pointer",
                       border: `1px solid ${chosen ? on : "var(--color-line-2)"}`, background: chosen ? on : "#fff",
                       color: chosen ? "#fff" : "var(--color-ink-2)" }}>{label}</button>
    );
  };
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "6px 12px", borderTop: "1px solid var(--color-row)" }}>
      <div style={{ flex: 1, minWidth: 0 }}><div style={{ fontSize: 15, fontWeight: 800 }}>{line.label}</div>
        {line.critical && <div className="small">Critical item</div>}</div>
      <div role="radiogroup" aria-label={line.label} style={{ display: "flex", gap: 6 }}>
        {button("pass", "Pass", "var(--color-green)")}{button("fail", "Fail", "var(--color-red)")}
      </div>
    </div>
  );
}

/** The checklist half of DriverPrestart.html: the odometer, then each item
 * passed or failed, a failed one with a photo and a note. It goes through
 * the queue like any confirmation, with its own message_id, so a check made
 * in a black spot goes once there's signal, and is recorded once. */
export function PrestartCheck({ run }: { run: DriverRun }) {
  const { queue } = useSession();
  const lines = run.checklist ?? [];
  const last = run.vehicle.odometer_km ?? null;
  const [odometer, setOdometer] = useState("");
  const [answers, setAnswers] = useState<Record<number, Answer>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // set at once, unlike state: a second tap before the screen updates does nothing
  const going = useRef(false);

  const answer = (id: number): Answer => answers[id] ?? { result: null, note: "", photo: null };
  const reading = readOdometer(odometer);
  const backwards = reading !== null && last !== null && reading < last;
  const passed = lines.filter((l) => answer(l.id).result === "pass").length;
  const ready = reading !== null && !backwards && lines.length > 0 && lines.every((l) => {
    const a = answer(l.id);
    return a.result === "pass" || (a.result === "fail" && !!a.photo && a.note.trim() !== "");
  });

  async function finish() {
    if (!ready || going.current) return;
    going.current = true;
    setBusy(true);
    setError(null);
    try {
      const failed = lines.map((l, i) => ({ l, i })).filter(({ l }) => answer(l.id).result === "fail");
      await queue.submit({
        path: `/v1/runs/${run.id}/prestart`,
        body: { at: new Date().toISOString(), odometer_km: reading,
                items: lines.map((l) => answer(l.id).result === "pass"
                  ? { item_id: l.id, result: "pass" }
                  : { item_id: l.id, result: "fail", note: answer(l.id).note.trim() }) },
        label: `Pre-start · ${run.vehicle.rego}`, run_id: run.id, consignment_id: null, outcome: "prestart",
        pictures: failed.map(({ l, i }) => ({ kind: "photo" as const, field: `items.${i}.photo_file`, blob: answer(l.id).photo! })),
      });
    } catch (e) {
      going.current = false;
      setError(e instanceof Error ? e.message : "That couldn't be kept on the phone. Try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="scroll">
        <label style={{ display: "flex", alignItems: "center", gap: 10, fontSize: 14, fontWeight: 800, padding: "8px 12px",
                        borderRadius: 16, background: "#fff", border: "1px solid var(--color-line)" }}>
          <span style={{ flex: 1 }}>Odometer{last !== null && <span className="small" style={{ display: "block", fontWeight: 600 }}>Last {km(last)} km</span>}</span>
          <input className="num" type="text" inputMode="numeric" autoComplete="off" value={odometer}
                 onChange={(e) => setOdometer(e.target.value)}
                 style={{ width: 130, height: 44, padding: "0 12px", borderRadius: 12, border: "2px solid var(--color-line-2)",
                          fontSize: 17, fontWeight: 800, textAlign: "right" }} />
          <span style={{ color: "var(--color-ink-2)" }}>km</span>
        </label>
        {backwards && <div className="msg r" role="alert">That's less than the last reading, {km(last!)} km. Check the odometer.</div>}
        <section aria-label={`Checklist for a ${run.vehicle.type}`}
                 style={{ borderRadius: 18, background: "#fff", border: "1px solid var(--color-line)", overflow: "hidden" }}>
          <div className="lbl" style={{ padding: "10px 12px 6px" }}>{run.vehicle.type} checklist · {passed} of {lines.length} pass</div>
          {lines.map((l) => {
            const a = answer(l.id);
            const set = (next: Answer) => setAnswers((all) => ({ ...all, [l.id]: next }));
            return (
              <div key={l.id}>
                <PassFail line={l} answer={a} onChange={set} />
                {a.result === "fail" && (
                  <div style={{ padding: "4px 12px 12px", display: "flex", flexDirection: "column", gap: 8,
                                background: "#FFF8F8", borderTop: "1px solid var(--color-red-line)" }}>
                    <div style={{ fontSize: 12, color: "var(--color-red-ink)", fontWeight: 700, paddingTop: 6 }}>A fail needs a photo and a note.</div>
                    <PhotoPicker photo={a.photo} onPhoto={(photo) => set({ ...a, photo })} hint={`${l.label}: needed`} />
                    <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 13, fontWeight: 800 }}>{l.label}: note
                      <input className="field" type="text" maxLength={500} value={a.note} onChange={(e) => set({ ...a, note: e.target.value })} /></label>
                    <div className="small">{l.critical
                      ? "A critical item: the truck can't go until a fleet manager marks it fixed. The office sees it as soon as it's sent."
                      : "Not a critical item, so you can still go. The fleet manager will see it."}</div>
                  </div>
                )}
              </div>
            );
          })}
        </section>
        {error && <div className="msg r" role="alert">{error}</div>}
      </div>
      <button type="button" className="big primary" style={{ flex: "none" }} disabled={!ready || busy} onClick={() => void finish()}>
        Finish check</button>
    </>
  );
}
```

Change `apps/driver/src/pages/Today.tsx`:
```diff
--- a/apps/driver/src/pages/Today.tsx
+++ b/apps/driver/src/pages/Today.tsx
@@ -4,10 +4,11 @@ import { api, ApiError, Unreachable } from "../api/client";
 import type { DriverRun, RunStops, StartOut } from "../api/types";
 import { useSession } from "../auth/Session";
 import { currentRun, loadRun, plural, rememberCurrent, saveRun } from "../lib/run";
-import { dayLabel, isoDay } from "../lib/time";
+import { clock, dayLabel, isoDay } from "../lib/time";
 import { askForLocation } from "../lib/where";
 import { Icon } from "../ui/icons";
 import { Phone } from "../ui/kit";
+import { PrestartCheck } from "./PrestartCheck";
 
 function said(e: unknown): string {
   if (e instanceof Unreachable) return "No signal. Starting a run needs a connection, to load the whole run onto this phone.";
@@ -15,15 +16,25 @@ function said(e: unknown): string {
   return e instanceof Error ? e.message : "Something went wrong. Try again.";
 }
 
+// the office has a reason the run can't start as the phone had it: look again
+const RELOAD = new Set(["prestart_needed", "vehicle_grounded", "vehicle_overdue"]);
+
+const CHECKED = { passed: "passed", failed: "an item failed, and the office will see it", grounded: "a critical item failed" };
+
 function state(r: DriverRun): string {
-  if (r.status === "planned") return "Ready to go. Starting tells the office you've left, and loads the whole run onto this phone.";
+  if (r.status === "planned" && r.prestart) {
+    return `Pre-start done ${clock(new Date(r.prestart.at))}: ${CHECKED[r.prestart.result]}. `
+      + "Starting tells the office you've left, and loads the whole run onto this phone.";
+  }
+  if (r.status === "planned") return "Do the pre-start check of the truck, then start the run.";
   if (r.status === "departed") return `On the road: ${r.delivered + r.failed} of ${plural(r.stops, "stop")} done.`;
   return `Finished: ${r.delivered} delivered, ${r.failed} coming back.`;
 }
 
-/** The top of DriverPrestart.html: the driver's run and truck for today.
- * The pre-start check that goes between picking and starting comes with
- * build step 4; until then the run starts from here. */
+/** DriverPrestart.html: the driver's run and truck for today, the pre-start
+ * check of the truck, then Start. The check may be done with no signal: it
+ * waits on the phone like any confirmation, but the run starts only once the
+ * office has it, since a failed critical item grounds the truck. */
 export function Today() {
   const { driver, queue, waiting, others, signOut } = useSession();
   const me = driver!.id;
@@ -36,6 +47,10 @@ export function Today() {
   const startIds = useRef<Record<number, string>>({});
   const saved = currentRun(me);
   const savedRun = saved ? loadRun(me, saved) : null;
+  // the pre-start waiting on the phone, or sent, shows as it goes
+  const [, bump] = useState(0);
+  useEffect(() => queue.subscribe(() => bump((n) => n + 1)), [queue]);
+  const reloadedFor = useRef(new Set<string>());
 
   const load = useCallback(async () => {
     setError(null);
@@ -71,6 +86,7 @@ export function Today() {
       navigate(`/run/${r.id}`);
     } catch (e) {
       setError(said(e));
+      if (e instanceof ApiError && e.code && RELOAD.has(e.code)) void load();
     } finally {
       setBusy(false);
     }
@@ -83,6 +99,24 @@ export function Today() {
   }
 
   const run = runs?.find((r) => r.id === chosen) ?? null;
+  const blocked = run?.vehicle.blocked ?? null;
+  // the newest pre-start this phone made for the run
+  const check = run ? queue.forRun(run.id).find((i) => i.outcome === "prestart") : undefined;
+  const checkSent = check?.status === "sent" && !run?.prestart;
+  // the office has it now: load the run again to hear how it went
+  useEffect(() => {
+    if (check?.status === "sent" && !reloadedFor.current.has(check.id)) {
+      reloadedFor.current.add(check.id);
+      void load();
+    }
+  }, [check?.status, check?.id, load]);
+  const checking = run?.status === "planned" && !blocked && !run.prestart && !check;
+  const canStart = run?.status === "planned" && !blocked && !!run.prestart;
+
+  function again() {
+    if (check) void queue.dismiss(check.id);
+  }
+
   return (
     <Phone label="Today's run">
       <div className="top">
@@ -98,14 +132,31 @@ export function Today() {
               <button key={r.id} type="button" role="radio" aria-checked={r.id === chosen} onClick={() => setChosen(r.id)}
                       className="choice" style={{ flex: 1, minWidth: 0, minHeight: 0, padding: "8px 6px", textAlign: "center", alignItems: "center" }}>
                 <b style={{ display: "block", fontSize: 14 }}>{r.vehicle.rego}</b>
-                <span style={{ fontSize: 11, fontWeight: 700, color: "var(--color-ink-2)", textTransform: "capitalize" }}>
-                  {r.vehicle.type} · {r.run_no}</span>
+                {r.vehicle.blocked
+                  ? <span style={{ fontSize: 11, fontWeight: 700, color: "var(--color-red-ink)" }}>
+                      {r.vehicle.blocked.code === "vehicle_grounded" ? "Grounded" : "Overdue"} · {r.run_no}</span>
+                  : <span style={{ fontSize: 11, fontWeight: 700, color: "var(--color-ink-2)", textTransform: "capitalize" }}>
+                      {r.vehicle.type} · {r.run_no}</span>}
               </button>
             ))}
           </div>
         </div>
       )}
-      <div className="scroll">
+      {checking && run && <PrestartCheck run={run} />}
+      {!checking && <div className="scroll">
+        {run && blocked && run.status !== "done" && (
+          <div className="msg r" role="alert">{blocked.message}{" "}
+            {run.status === "planned" ? "Don't take it out: call the office." : "Call the office."}</div>
+        )}
+        {run && check?.status === "queued" && !run.prestart && (
+          <div className="msg a" role="status">Your pre-start is kept on this phone. It goes as soon as there's signal,
+            and then you can start.</div>
+        )}
+        {run && checkSent && <div className="msg g" role="status">Your pre-start has reached the office.</div>}
+        {run && check?.status === "failed" && !run.prestart && (
+          <div className="msg r" role="alert">Your pre-start wasn't taken: {check.error}
+            <div style={{ marginTop: 8 }}><button type="button" className="chip" onClick={again}>Do the check again</button></div></div>
+        )}
         {run && (
           <section className="card" aria-label={`Run ${run.run_no}`} style={{ display: "flex", flexDirection: "column", gap: 6 }}>
             <div className="lbl">{run.run_no}{run.date !== isoDay(new Date()) ? ` · from ${dayLabel(new Date(`${run.date}T00:00`))}` : ""}</div>
@@ -126,17 +177,23 @@ export function Today() {
         {error && savedRun && savedRun.status !== "done" && (
           <Link to={`/run/${savedRun.id}`} className="big ghost">Carry on with {savedRun.run_no} from memory</Link>
         )}
-        {run?.status === "planned" && (
+        {canStart && (
           <div style={{ display: "flex", gap: 8, fontSize: 12, color: "var(--color-muted)" }}>
             <Icon name="pin" size={16} width={2.2} />
             <span>Starting asks to use this phone's location. It is kept with each drop, and only while you're on a run.</span>
           </div>
         )}
-      </div>
+      </div>}
       {!run && (
         <button type="button" className="big ghost" style={{ flex: "none" }} onClick={() => void load()}>Look again</button>
       )}
-      {run && run.status !== "done" && (
+      {run && check?.status === "queued" && !run.prestart && (
+        <button type="button" className="big ghost" style={{ flex: "none" }} onClick={() => void queue.drain()}>Send it now</button>
+      )}
+      {run && (blocked || checkSent) && run.status === "planned" && (
+        <button type="button" className="big ghost" style={{ flex: "none" }} onClick={() => void load()}>Look again</button>
+      )}
+      {run && (canStart || run.status === "departed") && (
         <button type="button" className="big primary" style={{ flex: "none" }} disabled={busy} onClick={() => void go(run)}>
           <Icon name="truck" size={22} />{run.status === "planned" ? "Start run" : "Carry on"}</button>
       )}
```

Today watches the queue: once the check it made for the chosen run has been sent, it loads today's runs again (once per check), and the answer says whether the truck can go. Starting refused with `prestart_needed`, `vehicle_grounded` or `vehicle_overdue` loads them again too.

**Step 4: Run the tests and the build**

Run: `npm test && npm run build`
Expected: `84 passed`, and a `dist/` with `sw.js`.

**Step 5: Commit**

```bash
git add apps/driver/src/pages/PrestartCheck.tsx apps/driver/src/pages/Today.tsx \
        apps/driver/src/test/samples.ts apps/driver/src/test/harness.tsx \
        apps/driver/src/test/Today.test.tsx apps/driver/src/test/Prestart.test.tsx
git commit -m "Do the pre-start check on the phone before starting the run"
```

## Part C — the dispatcher's desktop (`apps/desktop`)

### Task 11: Photos fetched with the session, and a CSP that lets them show

**Files:**
- Modify: `apps/desktop/src/api/client.ts` (`blob`)
- Modify: `apps/desktop/src/api/types.ts` (a vehicle's standing, `PrestartDetail`, `HistoryItem`, a run's `blocked` and `prestart`)
- Modify: `deploy/Caddyfile`, `README.md` (the desktop's CSP, both snippets)
- Test: `apps/desktop/src/test/client.test.ts`

**Step 1: Write the failing test**

Change `apps/desktop/src/test/client.test.ts`:
```diff
--- a/apps/desktop/src/test/client.test.ts
+++ b/apps/desktop/src/test/client.test.ts
@@ -55,6 +55,18 @@ describe("ApiClient", () => {
     expect(client.session?.refresh_token).toBe("r2");
   });
 
+  it("fetches a picture with the token, refreshing once on a 401", async () => {
+    client.setSession({ token: "old", refresh_token: "r1", expires_in: 900 });
+    answers.push(json(401, {}));
+    answers.push(json(200, { token: "new", refresh_token: "r2", expires_in: 900 }));
+    answers.push(new Response(new Uint8Array([0xff, 0xd8, 0xff]), { status: 200, headers: { "Content-Type": "image/jpeg" } }));
+    const picture = await client.blob("/v1/pod-files/3f2a");
+    expect(picture.type).toBe("image/jpeg");
+    expect(new Headers(calls[2].init.headers).get("Authorization")).toBe("Bearer new");
+    answers.push(json(404, { detail: "no file" }));
+    await expect(client.blob("/v1/pod-files/nope")).rejects.toThrow("That picture couldn't be loaded (404).");
+  });
+
   it("signs out when the refresh fails too", async () => {
     const signedOut = vi.fn();
     client.onSignedOut = signedOut;
```

**Step 2: Run it to see it fail**

Run: `npx vitest run src/test/client.test.ts`
Expected: `1 failed`: `client.blob is not a function`.

**Step 3: Implement**

Change `README.md`:
```diff
--- a/README.md
+++ b/README.md
@@ -103,7 +103,7 @@ not run the TMS one: start Simple TMS with
   		file_server
   	}
   	handle {
-  		header Content-Security-Policy "default-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
+  		header Content-Security-Policy "default-src 'self'; img-src 'self' blob:; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
   		@desktoppage not path /assets/* /favicon.svg /region.svg
   		header @desktoppage Cache-Control "no-cache"
   		root * /path/to/simple-tms/apps/desktop/dist
@@ -160,7 +160,7 @@ not run the TMS one: start Simple TMS with
   		file_server
   	}
   	handle {
-  		header Content-Security-Policy "default-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
+  		header Content-Security-Policy "default-src 'self'; img-src 'self' blob:; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
   		@desktoppage not path /assets/* /favicon.svg /region.svg
   		header @desktoppage Cache-Control "no-cache"
   		root * /srv/tms-desktop
```

Change `apps/desktop/src/api/client.ts`:
```diff
--- a/apps/desktop/src/api/client.ts
+++ b/apps/desktop/src/api/client.ts
@@ -166,6 +166,24 @@ export class ApiClient {
     return parsed as T;
   }
 
+  /** A picture (a pre-start photo) as bytes, with the session's token: an
+   * <img> can't send one, so the page shows it from a blob: URL. */
+  async blob(path: string, retry = true): Promise<Blob> {
+    const headers: Record<string, string> = {};
+    if (this.session?.token) headers.Authorization = `Bearer ${this.session.token}`;
+    let res: Response;
+    try {
+      res = await this.fetcher(path, { method: "GET", headers });
+    } catch {
+      throw new Unreachable();
+    }
+    if (res.status === 401 && retry && this.session?.refresh_token) {
+      if (await this.refresh()) return this.blob(path, false);
+    }
+    if (!res.ok) throw new ApiError(res.status, { detail: `That picture couldn't be loaded (${res.status}).` });
+    return res.blob();
+  }
+
   get<T>(path: string, params?: Record<string, string | number | boolean | null | undefined>) {
     const qs = params
       ? Object.entries(params)
```

Change `apps/desktop/src/api/types.ts`:
```diff
--- a/apps/desktop/src/api/types.ts
+++ b/apps/desktop/src/api/types.ts
@@ -36,6 +36,13 @@ export interface Consignment {
   packages: Package[];
 }
 
+/** Why a truck can't take a run on a day: grounded, or rego or service overdue. */
+export interface Blocked { code: "vehicle_grounded" | "vehicle_overdue" | string; message: string }
+
+export type DueState = "ok" | "due" | "overdue" | "unknown";
+export type CheckResult = "passed" | "failed" | "grounded";
+
+/** A vehicle, and (from step 4) where it stands on the day asked about. */
 export interface Vehicle {
   id: number;
   rego: string;
@@ -43,6 +50,47 @@ export interface Vehicle {
   max_kg: string;
   max_m3: string;
   active: boolean;
+  service_every_months?: number | null;
+  service_every_km?: number | null;
+  odometer_km?: number | null;
+  rego_due?: { expires_on: string | null; days_left: number | null; state: DueState };
+  service_due?: { last_on: string | null; last_km: number | null; due_on: string | null; due_km: number | null;
+                  days_left: number | null; km_left: number | null; state: DueState };
+  grounded?: { check_id: number; at: string; failed: string[] } | null;
+  last_prestart?: { id: number; at: string; result: CheckResult; by: string } | null;
+  state?: "ok" | "due" | "overdue" | "grounded";
+  blocked?: Blocked | null;
+}
+
+/** One pre-start check as the driver sent it (GET /v1/prestarts/{id}). */
+export interface PrestartDetail {
+  id: number;
+  run_id: number;
+  run_no: string;
+  vehicle: string;
+  vehicle_type: string;
+  driver: { code: string; name: string } | null;
+  at: string;
+  recorded_at: string;
+  odometer_km: number;
+  result: CheckResult;
+  items: { label: string; critical: boolean; passed: boolean; note: string | null; photo_file: string | null }[];
+  fixed: { id: number; note: string; by: string; at: string } | null;
+}
+
+/** A line of a truck's history, newest first (GET /v1/vehicles/{rego}/history). */
+export interface HistoryItem {
+  kind: "prestart" | "fixed" | "service" | "rego" | "odometer";
+  id: number;
+  recorded_at: string;
+  by: string;
+  note: string | null;
+  result: CheckResult | null;
+  failed: string[];
+  odometer_km: number | null;
+  done_on: string | null;
+  expires_on: string | null;
+  check_id: number | null;
 }
 
 export interface Driver {
@@ -79,7 +127,7 @@ export interface Run {
   run_no: string;
   date: string;
   status: "planned" | "departed" | "done" | "cancelled";
-  vehicle: { rego: string; type: string; max_kg: string; max_m3: string; active: boolean };
+  vehicle: { rego: string; type: string; max_kg: string; max_m3: string; active: boolean; blocked?: Blocked | null };
   driver: { code: string; name: string };
   created_by: string;
   created_at: string;
@@ -94,4 +142,6 @@ export interface Run {
   };
   run_sheet: RunSheet | null;
   shipping: { pending: number; done: number; failed: number };
+  /** the latest pre-start of its truck for it */
+  prestart?: { id: number; at: string; result: CheckResult } | null;
 }
```

Change `deploy/Caddyfile`:
```diff
--- a/deploy/Caddyfile
+++ b/deploy/Caddyfile
@@ -50,7 +50,9 @@ http://{$TMS_DOMAIN} {
 	handle {
 		# Only the desktop: the API's /docs page loads its scripts from a CDN.
 		# The build has no inline scripts or styles, and its fonts are its own.
-		header Content-Security-Policy "default-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
+		# Pre-start photos are fetched with the session's token and shown
+		# from blob: URLs.
+		header Content-Security-Policy "default-src 'self'; img-src 'self' blob:; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
 		# the page names the build's files: asked after every time
 		@desktoppage not path /assets/* /favicon.svg /region.svg
 		header @desktoppage Cache-Control "no-cache"
```

**Step 4: Run the tests**

Run: `npm test && npm run typecheck`
Expected: `55 passed`, and no type errors.

**Step 5: Commit**

```bash
git add apps/desktop/src/api/client.ts apps/desktop/src/api/types.ts apps/desktop/src/test/client.test.ts \
        deploy/Caddyfile README.md
git commit -m "Fetch drivers' photos with the session, and let the desktop show them"
```

### Task 12: The Fleet drawer

**Files:**
- Create: `apps/desktop/src/lib/fleet.ts` (how a truck's standing is worded)
- Create: `apps/desktop/src/ui/Photo.tsx`
- Create: `apps/desktop/src/pages/Fleet.tsx`
- Modify: `apps/desktop/src/App.tsx`, `apps/desktop/src/index.css`, `apps/desktop/src/ui/icons.tsx`
- Modify: `apps/desktop/src/test/harness.tsx` (sign in as a fleet manager too), `apps/desktop/src/test/MapHome.test.tsx`
- Test: `apps/desktop/src/test/fleet.test.ts`, `apps/desktop/src/test/Fleet.test.tsx`

**Step 1: Write the failing tests**

Create `apps/desktop/src/test/Fleet.test.tsx`:
```tsx
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { PrestartDetail, Vehicle } from "../api/types";
import { json, MANAGER, path, renderApp, stubApi, type Call } from "./harness";

const due = { expires_on: "2027-03-03", days_left: 142, state: "ok" as const };
const service = { last_on: "2026-07-14", last_km: 46900, due_on: "2027-01-14", due_km: 61900,
                  days_left: 92, km_left: 3000, state: "ok" as const };
const truck = (rego: string, over: Partial<Vehicle> = {}): Vehicle => ({
  id: 1, rego, type: "rigid", max_kg: "4500", max_m3: "22", active: true, service_every_months: 6,
  service_every_km: 15000, odometer_km: 58900, rego_due: due, service_due: service, grounded: null,
  last_prestart: null, state: "ok", blocked: null, ...over,
});

const GROUNDED = truck("TMS027", {
  type: "ute", state: "grounded", grounded: { check_id: 12, at: "2026-10-13T19:40:00Z", failed: ["Lights"] },
  last_prestart: { id: 12, at: "2026-10-13T19:40:00Z", result: "grounded", by: "tom" },
  blocked: { code: "vehicle_grounded", message: "TMS027 is grounded: Lights failed the pre-start. A fleet manager has to mark it fixed first." },
});
const FLEET = [
  truck("TMS041", { last_prestart: { id: 9, at: "2026-10-13T19:52:00Z", result: "passed", by: "kim" } }),
  truck("TMS018", { type: "van", state: "due", rego_due: { expires_on: "2026-10-25", days_left: 12, state: "due" } }),
  GROUNDED,
];
const CHECK: PrestartDetail = {
  id: 12, run_id: 4, run_no: "R-0004", vehicle: "TMS027", vehicle_type: "ute", driver: { code: "tom", name: "Tom" },
  at: "2026-10-13T19:40:00Z", recorded_at: "2026-10-13T19:40:05Z", odometer_km: 84312, result: "grounded",
  items: [
    { label: "Tyres", critical: false, passed: true, note: null, photo_file: null },
    { label: "Lights", critical: true, passed: false, note: "Left brake light out. Bulb or fuse, not sure.", photo_file: "3f2a" },
  ],
  fixed: null,
};

function depot(answers: Record<string, (c: Call) => Response | null> = {}, who?: object) {
  return stubApi((method, url, body) => {
    const key = `${method} ${path(url)}`;
    if (answers[key]) return answers[key]({ method, url, body });
    if (key === "GET /v1/vehicles") return json(200, { items: FLEET, total: FLEET.length });
    if (key === "GET /v1/runs") {
      return json(200, { items: [{ id: 1, run_no: "R-0001", status: "departed", vehicle: { rego: "TMS041" },
                                   driver: { code: "kim", name: "Kim" } }], total: 1 });
    }
    if (key === "GET /v1/prestarts/12") return json(200, CHECK);
    if (key.startsWith("GET /v1/vehicles/") && key.endsWith("/history")) {
      return json(200, [{ kind: "prestart", id: 12, recorded_at: "2026-10-13T19:40:05Z", by: "tom", note: null,
                          result: "grounded", failed: ["Lights"], odometer_km: 84312, done_on: null, expires_on: null, check_id: null }]);
    }
    if (key === "GET /v1/pod-files/3f2a") {
      return new Response(new Uint8Array([0xff, 0xd8, 0xff]), { status: 200, headers: { "Content-Type": "image/jpeg" } });
    }
    return null;
  }, who);
}

describe("Fleet", () => {
  beforeEach(() => {
    URL.createObjectURL = vi.fn(() => "blob:photo");
    URL.revokeObjectURL = vi.fn();
  });
  afterEach(() => vi.unstubAllGlobals());

  it("lists each truck with when its rego and service fall due, and opens on the grounded one", async () => {
    const { calls } = depot();
    renderApp("/fleet");
    const table = await screen.findByRole("table");
    const row = (rego: string) => within(table).getByRole("button", { name: `Show ${rego}` }).closest("tr")!;
    await waitFor(() => expect(row("TMS041")).toHaveTextContent("3 Mar 2027"));
    expect(row("TMS041")).toHaveTextContent("Kim");
    expect(row("TMS041")).toHaveTextContent("14 Jan 2027or 61,900 km");
    expect(row("TMS041")).toHaveTextContent("On a run");
    expect(row("TMS018")).toHaveTextContent("25 Oct 2026due in 12 days");
    expect(row("TMS018")).toHaveTextContent("Rego due");
    expect(row("TMS027")).toHaveTextContent("Grounded");
    expect(within(table).getByRole("button", { name: "Show TMS027" })).toHaveAttribute("aria-pressed", "true");
    expect(calls.find((c) => path(c.url) === "/v1/vehicles")!.url).toMatch(/date=\d{4}-\d{2}-\d{2}/);

    const record = screen.getByRole("complementary", { name: "Pre-start check record" });
    expect(await within(record).findByText("PRE-START CHECK · FAILED")).toBeInTheDocument();
    expect(record).toHaveTextContent("Ute · Tom ·");
    expect(record).toHaveTextContent("odometer 84,312 km");
    expect(record).toHaveTextContent("Tom's note · LightsLeft brake light out. Bulb or fuse, not sure.");
    expect(await within(record).findByRole("img", { name: "Tom's photo: Lights" })).toHaveAttribute("src", "blob:photo");
    expect(record).toHaveTextContent("It can't go on a run until a fleet manager marks it fixed.");
    // a dispatcher sees it, but marking fixed is for a fleet manager
    expect(within(record).queryByRole("button", { name: "Mark fixed" })).not.toBeInTheDocument();
    expect(screen.getByText("GROUNDED").parentElement).toHaveTextContent("TMS027 · lights");
  });

  it("lets a fleet manager mark it fixed with what was done", async () => {
    let fixed = false;
    const { calls } = depot({
      "POST /v1/vehicles/TMS027/fix": () => { fixed = true; return json(201, { kind: "fixed" }); },
      "GET /v1/vehicles": () => json(200, { items: fixed ? [FLEET[0], FLEET[1], truck("TMS027", { type: "ute" })] : FLEET, total: 3 }),
    }, MANAGER);
    renderApp("/fleet");
    const user = userEvent.setup();
    const button = await screen.findByRole("button", { name: "Mark fixed" });
    expect(button).toBeDisabled();
    await user.type(screen.getByLabelText("What was done"), "Replaced left brake globe, tested");
    await user.click(button);
    expect(await screen.findByText("TMS027 is marked fixed, and can take a run.")).toHaveAttribute("role", "status");
    expect(calls.find((c) => c.url === "/v1/vehicles/TMS027/fix")!.body).toEqual(
      { check_id: 12, note: "Replaced left brake globe, tested" });
    expect(screen.queryByRole("button", { name: "Mark fixed" })).not.toBeInTheDocument();
  });

  it("says so when another failed check came in meanwhile", async () => {
    depot({ "POST /v1/vehicles/TMS027/fix": () => json(409, { code: "stale",
      detail: "Another failed check of TMS027 has come in; it has been reloaded, so look at it and mark it fixed again" }) }, MANAGER);
    renderApp("/fleet");
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("What was done"), "New globe");
    await user.click(screen.getByRole("button", { name: "Mark fixed" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Another failed check of TMS027 has come in");
  });

  it("records a service with its reading and a note", async () => {
    const { calls } = depot({ "POST /v1/vehicles/TMS041/service": () => json(201, { kind: "service" }) }, MANAGER);
    renderApp("/fleet");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Show TMS041" }));
    const record = screen.getByRole("complementary", { name: "Pre-start check record" });
    await user.click(within(record).getByText("A service done"));
    const form = within(record).getByRole("form", { name: "A service done" });
    await user.type(within(form).getByLabelText("Done on"), "2026-10-13");
    await user.type(within(form).getByLabelText("Odometer (km)"), "61190");
    await user.type(within(form).getByLabelText("Note"), "60,000 km service at Ballarat Diesel");
    await user.click(within(form).getByRole("button", { name: "Record the service" }));
    expect(await screen.findByText("TMS041's service is recorded.")).toHaveAttribute("role", "status");
    expect(calls.find((c) => c.url === "/v1/vehicles/TMS041/service")!.body).toEqual(
      { done_on: "2026-10-13", odometer_km: 61190, note: "60,000 km service at Ballarat Diesel" });
  });
});
```

Change `apps/desktop/src/test/MapHome.test.tsx`:
```diff
--- a/apps/desktop/src/test/MapHome.test.tsx
+++ b/apps/desktop/src/test/MapHome.test.tsx
@@ -25,7 +25,7 @@ describe("the map", () => {
     expect(calls.find((c) => path(c.url) === "/v1/runs")?.url).toMatch(/status=departed%2Cdone/);
     await userEvent.setup().click(screen.getByRole("link", { name: /Fleet/ }));
     expect(screen.getByTestId("where")).toHaveTextContent("/fleet");
-    expect(screen.getByText(/This screen arrives with build step 4/)).toBeInTheDocument();
+    expect(screen.getByRole("dialog", { name: "Fleet" })).toBeInTheDocument();
   });
 
   it("signs out from the account button", async () => {
```

Create `apps/desktop/src/test/fleet.test.ts`:
```ts
import type { Vehicle } from "../api/types";
import { cantGo, checkCell, longDate, regoCell, serviceCell, statusPill } from "../lib/fleet";

const base: Vehicle = { id: 1, rego: "TMS041", type: "rigid", max_kg: "4500", max_m3: "22", active: true, state: "ok" };
const service = (over: Partial<NonNullable<Vehicle["service_due"]>>): Vehicle => ({
  ...base, service_every_months: 6, service_every_km: 15000,
  service_due: { last_on: "2026-07-14", last_km: 46900, due_on: "2027-01-14", due_km: 61900,
                 days_left: 93, km_left: 3000, state: "ok", ...over },
});

describe("how Fleet words a truck's standing", () => {
  it("gives dates as the calendar has them", () => {
    expect(longDate("2027-03-03")).toBe("3 Mar 2027");
  });

  it("says when the rego runs out, amber a month out and red after", () => {
    expect(regoCell(base)).toEqual({ text: "Not recorded", sub: null, tone: "" });
    const rego = (days_left: number, state: "ok" | "due" | "overdue") =>
      regoCell({ ...base, rego_due: { expires_on: "2026-10-25", days_left, state } });
    expect(rego(142, "ok")).toEqual({ text: "25 Oct 2026", sub: null, tone: "" });
    expect(rego(12, "due")).toEqual({ text: "25 Oct 2026", sub: "due in 12 days", tone: "a" });
    expect(rego(0, "due").sub).toBe("runs out today");
    expect(rego(-3, "overdue")).toEqual({ text: "25 Oct 2026", sub: "ran out 3 days ago", tone: "r" });
  });

  it("puts the nearer of the service date and kilometres first", () => {
    expect(serviceCell(service({}))).toEqual({ text: "14 Jan 2027", sub: "or 61,900 km", tone: "" });
    expect(serviceCell(service({ km_left: 640, state: "due" }))).toEqual(
      { text: "in 640 km", sub: "or 14 Jan 2027", tone: "a" });
    expect(serviceCell(service({ km_left: -100, days_left: 12, state: "overdue" }))).toEqual(
      { text: "100 km over", sub: "or 14 Jan 2027", tone: "r" });
    expect(serviceCell(service({ days_left: -2, state: "overdue" })).tone).toBe("r");
    expect(serviceCell(base).text).toBe("No schedule");
    expect(serviceCell({ ...base, service_every_km: 15000 }).text).toBe("Not recorded");
  });

  it("says when the last pre-start was and how it went", () => {
    const now = new Date(2026, 9, 14, 9, 0);
    const at = new Date(2026, 9, 14, 6, 52).toISOString();
    expect(checkCell({ ...base, last_prestart: { id: 1, at, result: "passed", by: "kim" } }, now))
      .toEqual({ text: "Today 6:52 am", sub: "Passed", tone: "" });
    expect(checkCell({ ...base, last_prestart: { id: 1, at, result: "grounded", by: "kim" } }, now).tone).toBe("r");
    expect(checkCell(base, now).text).toBe("None yet");
  });

  it("shows what most needs a person first", () => {
    expect(statusPill({ ...base, state: "grounded" }, true)).toEqual({ text: "Grounded", tone: "red" });
    expect(statusPill({ ...base, state: "overdue", rego_due: { expires_on: "2026-10-01", days_left: -13, state: "overdue" } }, false))
      .toEqual({ text: "Rego overdue", tone: "red" });
    expect(statusPill({ ...base, state: "due" }, false)).toEqual({ text: "Service due", tone: "amber" });
    expect(statusPill(base, true)).toEqual({ text: "On a run", tone: "green" });
    expect(statusPill({ ...base, active: false, state: "grounded" }, false).text).toBe("Deactivated");
    expect(cantGo([{ ...base, state: "grounded" }, { ...base, state: "overdue", active: false }, base])).toBe(1);
  });
});
```

Change `apps/desktop/src/test/harness.tsx`:
```diff
--- a/apps/desktop/src/test/harness.tsx
+++ b/apps/desktop/src/test/harness.tsx
@@ -11,17 +11,19 @@ export function json(status: number, body: unknown) {
 export interface Call { method: string; url: string; body: unknown }
 type Handler = (method: string, url: string, body: unknown) => Response | null | Promise<Response | null>;
 
-/** Signed in as a dispatcher; `more` answers everything else, or it is a 404. */
-export function stubApi(more: Handler = () => null) {
+export const DISPATCHER = { kind: "user", id: 1, name: "dee", display_name: "Dee Dispatch", role: "dispatcher",
+                            scopes: ["fleet:read", "consignments:read", "runs:read", "runs:write"] };
+export const MANAGER = { kind: "user", id: 2, name: "fleet.mgr", display_name: "Fran Fleet", role: "manager",
+                         scopes: ["fleet:read", "fleet:write", "consignments:read", "runs:read", "runs:write"] };
+
+/** Signed in as a dispatcher (or `who`); `more` answers everything else, or it is a 404. */
+export function stubApi(more: Handler = () => null, who: object = DISPATCHER) {
   const calls: Call[] = [];
   const fetchMock = vi.fn(async (url: string, init: RequestInit = {}) => {
     const method = init.method ?? "GET";
     const body = init.body ? JSON.parse(init.body as string) : undefined;
     calls.push({ method, url, body });
-    if (url === "/v1/auth/me") {
-      return json(200, { kind: "user", id: 1, name: "dee", display_name: "Dee Dispatch", role: "dispatcher",
-                         scopes: ["fleet:read", "consignments:read", "runs:read", "runs:write"] });
-    }
+    if (url === "/v1/auth/me") return json(200, who);
     if (url === "/v1/auth/logout") return new Response(null, { status: 204 });
     const answer = await more(method, url, body);
     return answer ?? json(404, { detail: `no stub for ${method} ${url}` });
```

**Step 2: Run them to see them fail**

Run: `npx vitest run src/test/fleet.test.ts src/test/Fleet.test.tsx src/test/MapHome.test.tsx`
Expected: both new files fail with `Failed to resolve import "../lib/fleet"` (the map test passes either way: the old placeholder is a drawer called Fleet too).

**Step 3: Implement**

Change `apps/desktop/src/App.tsx`:
```diff
--- a/apps/desktop/src/App.tsx
+++ b/apps/desktop/src/App.tsx
@@ -1,6 +1,7 @@
 import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
 import { AuthProvider } from "./auth/AuthContext";
 import { RequireAuth } from "./auth/RequireAuth";
+import { Fleet } from "./pages/Fleet";
 import { MapHome } from "./pages/MapHome";
 import { PlanRuns } from "./pages/PlanRuns";
 import { Later } from "./pages/Later";
@@ -21,9 +22,7 @@ export function AppRoutes() {
           <Route path="/carriers" element={
             <Later title="Carriers" step="build step 6"
                    what="Today's carrier consignments, con note entry and the manifest." />} />
-          <Route path="/fleet" element={
-            <Later title="Fleet" step="build step 4"
-                   what="Vehicles, drivers, pre-start checks, and rego and service due." />} />
+          <Route path="/fleet" element={<Fleet />} />
         </Route>
       </Route>
       <Route path="*" element={<Navigate to="/" replace />} />
```

Change `apps/desktop/src/index.css`:
```diff
--- a/apps/desktop/src/index.css
+++ b/apps/desktop/src/index.css
@@ -141,6 +141,37 @@
   .kb button, .kb select { height: 30px; border-radius: 8px; border: 1px solid var(--color-line); background: #fff; font-size: 12px; cursor: pointer; }
   .kb button { width: 30px; display: grid; place-items: center; padding: 0; }
   .kb select { max-width: 120px; padding: 0 4px; }
+
+  /* the Fleet drawer (Fleet.html) */
+  .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 12px; }
+  .stat { padding: 16px; border-radius: 16px; border: 1px solid var(--color-card-line); }
+  .stat .v { font-size: 30px; font-weight: 800; }
+  .stat .d { font-size: 13px; color: var(--color-ink-2); }
+  .stat.amber { border-color: var(--color-amber-line); background: #FFFBEF; }
+  .stat.amber .eyebrow, .stat.amber .v { color: var(--color-amber-ink); }
+  .stat.red { border-color: var(--color-red-line); background: #FFF6F6; }
+  .stat.red .eyebrow, .stat.red .v { color: var(--color-red-ink); }
+  .split { display: flex; flex-wrap: wrap; gap: 16px; align-items: flex-start; }
+  .tablewrap { flex: 999 1 460px; min-width: 0; overflow-x: auto; border: 1px solid var(--color-card-line); border-radius: 16px; }
+  .tablewrap table { width: 100%; border-collapse: collapse; font-size: 14px; }
+  .tablewrap th { padding: 12px 10px; font-weight: 800; text-align: left; color: var(--color-muted); font-size: 12px; letter-spacing: 1px; white-space: nowrap; }
+  .tablewrap td { padding: 6px 10px; border-top: 1px solid var(--color-row); height: 56px; }
+  .tablewrap tr.sel td { background: #F1F8F4; }
+  .tablewrap tr.sel.bad td { background: #FFF6F6; }
+  .rowbtn { min-height: 44px; padding: 0 10px; border-radius: 10px; font-weight: 800; font-size: 14px; cursor: pointer; white-space: nowrap; border: 2px solid transparent; background: transparent; color: var(--color-ink); }
+  tr.sel .rowbtn { border-color: var(--color-green); background: var(--color-tint); }
+  tr.sel.bad .rowbtn { border-color: var(--color-red); background: var(--color-red-bg); }
+  .aside { flex: 0 1 380px; max-width: 380px; min-width: 0; padding: 18px; border-radius: 18px; background: #F7FAF8; border: 1px solid var(--color-card-line); display: flex; flex-direction: column; gap: 14px; }
+  .box { border-radius: 12px; background: #fff; border: 1px solid var(--color-card-line); }
+  .box > div { display: flex; justify-content: space-between; gap: 8px; padding: 8px 12px; border-top: 1px solid var(--color-row); font-size: 13px; }
+  .box > div:first-child { border-top: 0; }
+  .box > .chk { justify-content: flex-start; align-items: flex-start; gap: 10px; padding: 9px 12px; }
+  .chk .ic { width: 22px; height: 22px; border-radius: 50%; display: grid; place-items: center; flex: none; color: #fff; }
+  .chk .ok { background: var(--color-green); }
+  .chk .bad { background: var(--color-red); }
+  .due-a { color: var(--color-amber-ink); font-weight: 800; }
+  .due-r { color: var(--color-red-ink); font-weight: 800; }
+  .nav a i.r { background: var(--color-red); color: #fff; }
 }
 
 @media (max-width: 960px) {
```

Create `apps/desktop/src/lib/fleet.ts`:
```ts
/** How the Fleet drawer words each truck's standing (Fleet.html). The API
 * works out what is due; this only says it, in the depot's own dates. Whole
 * kilometres throughout, as the API keeps them. */
import type { Vehicle } from "../api/types";
import { clock, dayLabel, isoDay } from "./days";

export type Tone = "" | "a" | "r";
export interface Cell { text: string; sub: string | null; tone: Tone }
export interface Pill { text: string; tone: "green" | "amber" | "red" | "grey" }

/** 61900 -> "61,900" */
export function km(n: number): string {
  return n.toLocaleString("en-AU");
}

/** "2027-03-03" -> "3 Mar 2027", read as the calendar date it is */
export function longDate(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  const month = new Intl.DateTimeFormat("en-AU", { month: "short" }).format(new Date(y, m - 1, d));
  return `${d} ${month} ${y}`;
}

const RANK = { unknown: 0, ok: 0, due: 1, overdue: 2 } as const;
const tone = (state: keyof typeof RANK): Tone => (state === "overdue" ? "r" : state === "due" ? "a" : "");

function days(n: number): string {
  return `${n} ${n === 1 ? "day" : "days"}`;
}

export function regoCell(v: Vehicle): Cell {
  const r = v.rego_due;
  if (!r?.expires_on) return { text: "Not recorded", sub: null, tone: "" };
  const left = r.days_left ?? 0;
  const sub = r.state === "overdue" ? `ran out ${days(-left)} ago`
    : r.state === "due" ? (left === 0 ? "runs out today" : `due in ${days(left)}`) : null;
  return { text: longDate(r.expires_on), sub, tone: tone(r.state) };
}

/** The next service by date or kilometres, the nearer first: the one in
 * worse standing, or the date when they stand alike. */
export function serviceCell(v: Vehicle): Cell {
  const s = v.service_due;
  if (!s || (!s.due_on && s.due_km == null)) {
    return { text: v.service_every_months || v.service_every_km ? "Not recorded" : "No schedule", sub: null, tone: "" };
  }
  const byDays = s.days_left == null ? "unknown" : s.days_left < 0 ? "overdue" : s.days_left <= 30 ? "due" : "ok";
  const byKm = s.km_left == null ? "unknown" : s.km_left < 0 ? "overdue" : s.km_left <= 1000 ? "due" : "ok";
  const kmText = s.km_left == null ? null : s.km_left < 0 ? `${km(-s.km_left)} km over` : `in ${km(s.km_left)} km`;
  if (kmText && (!s.due_on || RANK[byKm] > RANK[byDays])) {
    return { text: kmText, sub: s.due_on ? `or ${longDate(s.due_on)}` : null, tone: tone(byKm) };
  }
  return { text: longDate(s.due_on!), sub: s.due_km != null ? `or ${km(s.due_km)} km` : null,
           tone: tone(byDays === "unknown" ? "ok" : byDays) };
}

/** When the last pre-start was, by the depot's clock: "Today 6:52 am", "Mon 6:58 am". */
export function when(at: string, now = new Date()): string {
  const d = new Date(at);
  if (isoDay(d) === isoDay(now)) return `Today ${clock(d)}`;
  const age = (now.getTime() - d.getTime()) / 86_400_000;
  return age < 6 ? `${dayLabel(d).split(" ")[0]} ${clock(d)}` : dayLabel(d);
}

export function checkCell(v: Vehicle, now = new Date()): Cell {
  const c = v.last_prestart;
  if (!c) return { text: "None yet", sub: null, tone: "" };
  const sub = c.result === "passed" ? "Passed" : c.result === "grounded" ? "Failed" : "An item failed";
  return { text: when(c.at, now), sub, tone: c.result === "grounded" ? "r" : c.result === "failed" ? "a" : "" };
}

/** STATUS: what most needs a person, else where the truck is today. */
export function statusPill(v: Vehicle, onRun: boolean): Pill {
  if (!v.active) return { text: "Deactivated", tone: "grey" };
  if (v.state === "grounded") return { text: "Grounded", tone: "red" };
  if (v.state === "overdue") {
    return { text: v.rego_due?.state === "overdue" ? "Rego overdue" : "Service overdue", tone: "red" };
  }
  if (v.state === "due") return { text: v.rego_due?.state === "due" ? "Rego due" : "Service due", tone: "amber" };
  return onRun ? { text: "On a run", tone: "green" } : { text: "At depot", tone: "grey" };
}

/** Trucks that can't take a run today: for the map's Fleet button. */
export function cantGo(vehicles: Vehicle[]): number {
  return vehicles.filter((v) => v.active && (v.state === "grounded" || v.state === "overdue")).length;
}
```

Create `apps/desktop/src/pages/Fleet.tsx`:
```tsx
import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { useOutletContext } from "react-router-dom";
import { api, ApiError, Unreachable } from "../api/client";
import type { HistoryItem, Page, PrestartDetail, Run, Vehicle } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { clock, dayLabel, isoDay } from "../lib/days";
import { checkCell, km, longDate, regoCell, serviceCell, statusPill, when, type Cell } from "../lib/fleet";
import { Drawer } from "../ui/Drawer";
import { Icon } from "../ui/icons";
import { Photo } from "../ui/Photo";

type Note = { tone: "r" | "g"; text: string } | null;

function said(e: unknown): string {
  if (e instanceof Unreachable) return e.message;
  return e instanceof ApiError ? e.message : "Simple TMS could not be reached. Check the connection and try again.";
}

function CellOut({ c }: { c: Cell }) {
  const colour = c.tone === "r" ? "var(--color-red-ink)" : c.tone === "a" ? "var(--color-amber-ink)" : undefined;
  return (
    <td style={{ whiteSpace: "nowrap" }}>
      <span className={c.tone ? `due-${c.tone}` : undefined} style={c.text === "Not recorded" || c.text === "No schedule" || c.text === "None yet" ? { color: "var(--color-muted)" } : undefined}>{c.text}</span>
      {c.sub && <div className="small" style={colour ? { color: colour } : undefined}>{c.sub}</div>}
    </td>
  );
}

const RESULT = { passed: "PASSED", failed: "AN ITEM FAILED", grounded: "FAILED" } as const;
const KIND = { prestart: "Pre-start", fixed: "Marked fixed", service: "Service", rego: "Rego", odometer: "Odometer put right" } as const;

function historyLine(h: HistoryItem): string {
  if (h.kind === "prestart") {
    const how = h.result === "passed" ? "passed" : `failed: ${h.failed.join(", ")}`;
    return `Pre-start ${how} · ${km(h.odometer_km ?? 0)} km`;
  }
  const what = h.kind === "service" ? `${longDate(h.done_on!)}${h.odometer_km != null ? ` at ${km(h.odometer_km)} km` : ""}`
    : h.kind === "rego" ? `runs out ${longDate(h.expires_on!)}`
    : h.kind === "odometer" ? `${km(h.odometer_km ?? 0)} km` : "";
  return [KIND[h.kind], what, h.note].filter(Boolean).join(" · ");
}

/** One small form: a few fields and a note, sent as a new record. */
function RecordForm({ title, button, fields, send }: {
  title: string; button: string; fields: { name: string; label: string; type: "date" | "number"; required?: boolean }[];
  send: (values: Record<string, string>) => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = e.currentTarget;
    const values = Object.fromEntries([...new FormData(form).entries()].map(([k, v]) => [k, String(v).trim()]));
    setBusy(true);
    try { await send(values); form.reset(); } finally { setBusy(false); }
  }
  return (
    <details className="box" style={{ padding: "8px 12px" }}>
      <summary style={{ cursor: "pointer", fontSize: 13, fontWeight: 800 }}>{title}</summary>
      <form onSubmit={(e) => void submit(e)} aria-label={title} style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 8 }}>
        {fields.map((f) => (
          <label key={f.name} className="small" style={{ display: "flex", flexDirection: "column", gap: 4, fontWeight: 700 }}>{f.label}
            <input className="field" name={f.name} type={f.type} min={f.type === "number" ? 0 : undefined} required={f.required} /></label>
        ))}
        <label className="small" style={{ display: "flex", flexDirection: "column", gap: 4, fontWeight: 700 }}>Note
          <input className="field" name="note" type="text" maxLength={500} required /></label>
        <button type="submit" className="btn sm primary" disabled={busy} style={{ alignSelf: "flex-start" }}>{button}</button>
      </form>
    </details>
  );
}

/** The aside: the check that grounded the truck (or its last one), Mark
 * fixed, what a fleet manager records, and the truck's history. */
function VehicleAside({ v, driverName, onChanged }: { v: Vehicle; driverName: string | null; onChanged: (note: Note) => Promise<void> }) {
  const { can } = useAuth();
  const manager = can("fleet:write");
  const checkId = v.grounded?.check_id ?? v.last_prestart?.id ?? null;
  const [check, setCheck] = useState<PrestartDetail | null>(null);
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [fixNote, setFixNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    setCheck(null);
    setError(null);
    if (checkId !== null) {
      api.get<PrestartDetail>(`/v1/prestarts/${checkId}`).then((c) => live && setCheck(c)).catch((e) => live && setError(said(e)));
    }
    api.get<HistoryItem[]>(`/v1/vehicles/${v.rego}/history`, { limit: 8 }).then((h) => live && setHistory(h)).catch(() => undefined);
    return () => { live = false; };
  }, [v.rego, checkId, v.last_prestart?.id, v.odometer_km, v.rego_due?.expires_on, v.service_due?.last_on]);

  async function record(path: string, body: Record<string, unknown>, done: string) {
    setError(null);
    try {
      await api.post(`/v1/vehicles/${v.rego}/${path}`, body);
      setFixNote("");
      await onChanged({ tone: "g", text: done });
    } catch (e) {
      setError(said(e));
      // a failed check that came in meanwhile: show it
      if (e instanceof ApiError && e.code === "stale") await onChanged(null);
    }
  }

  async function fix(e: FormEvent) {
    e.preventDefault();
    if (!check || !fixNote.trim()) return;
    setBusy(true);
    await record("fix", { check_id: check.id, note: fixNote.trim() }, `${v.rego} is marked fixed, and can take a run.`);
    setBusy(false);
  }

  const grounded = v.state === "grounded" && check?.id === v.grounded?.check_id;
  const failed = check?.items.filter((i) => !i.passed) ?? [];
  const who = check?.driver?.name ?? driverName;
  return (
    <aside className="aside" aria-label="Pre-start check record">
      <div>
        <div className="eyebrow" style={{ color: check?.result === "grounded" ? "var(--color-red-ink)" : undefined }}>
          {check ? `PRE-START CHECK · ${RESULT[check.result]}` : checkId !== null ? "PRE-START CHECK" : "NO PRE-START YET"}</div>
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <span style={{ fontSize: 22, fontWeight: 800 }}>{v.rego}</span>
          {v.state === "grounded" && <span className="pill p-red">Grounded</span>}
          {v.state === "overdue" && <span className="pill p-red">Overdue</span>}</div>
        {check && (
          <div style={{ fontSize: 13, color: "var(--color-ink-2)", textTransform: "none" }}>
            {[v.type[0].toUpperCase() + v.type.slice(1), who, `${dayLabel(new Date(check.at))}, ${clock(new Date(check.at))}`,
              `odometer ${km(check.odometer_km)} km`].filter(Boolean).join(" · ")}</div>)}
      </div>
      {v.blocked && v.state === "overdue" && <div className="msg r">{v.blocked.message}</div>}
      {check && (
        <div className="box">
          {check.items.map((i) => (
            <div key={i.label} className="chk">
              <span className={`ic ${i.passed ? "ok" : "bad"}`}><Icon name={i.passed ? "tick" : "close"} size={12} width={3.2} /></span>
              <div style={{ flex: 1 }}><b>{i.label}</b>{i.critical && !i.passed && <div className="small" style={{ marginTop: 2 }}>Critical item</div>}</div>
              <span className="small" style={{ fontWeight: 700, color: i.passed ? "var(--color-muted)" : "var(--color-red-ink)" }}>{i.passed ? "Pass" : "Fail"}</span>
            </div>
          ))}
        </div>
      )}
      {failed.map((i) => (
        <div key={i.label} style={{ padding: 12, borderRadius: 14, background: "#fff", border: "1px solid var(--color-red-line)", display: "flex", gap: 12, alignItems: "flex-start" }}>
          {i.photo_file && <Photo fileId={i.photo_file} label={`${who ?? "The driver"}'s photo: ${i.label}`} />}
          <div style={{ fontSize: 13 }}><b>{who ? `${who}'s note` : "Note"} · {i.label}</b>
            <div style={{ color: "var(--color-ink-2)", marginTop: 2 }}>{i.note}</div></div>
        </div>
      ))}
      {check?.fixed && <div className="msg g">Marked fixed by {check.fixed.by}: {check.fixed.note}</div>}
      {error && <div className="msg r" role="alert">{error}</div>}
      {grounded && <div className="small">A failed critical item grounds the truck. It can't go on a run until a fleet manager marks it fixed.</div>}
      {grounded && manager && (
        <form onSubmit={(e) => void fix(e)} aria-label="Mark fixed"
              style={{ padding: 14, borderRadius: 14, background: "#fff", border: "1px solid var(--color-card-line)", display: "flex", flexDirection: "column", gap: 10 }}>
          <div className="eyebrow">MARK FIXED</div>
          <label style={{ display: "flex", flexDirection: "column", gap: 6, fontSize: 13, fontWeight: 700 }}>What was done
            <input className="field" type="text" maxLength={500} placeholder="e.g. Replaced left brake globe, tested"
                   value={fixNote} onChange={(e) => setFixNote(e.target.value)} /></label>
          <button type="submit" className="btn primary" disabled={busy || !fixNote.trim()}><Icon name="wrench" size={16} />Mark fixed</button>
          <div className="small">Every check is kept as it was. Marking fixed adds a new record; it never changes this one.</div>
        </form>
      )}
      {manager && (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <div className="eyebrow">RECORD</div>
          <RecordForm title="A new rego expiry" button="Record the rego" fields={[{ name: "expires_on", label: "Runs out on", type: "date", required: true }]}
                      send={(f) => record("rego", { expires_on: f.expires_on, note: f.note }, `${v.rego}'s rego is recorded.`)} />
          <RecordForm title="A service done" button="Record the service"
                      fields={[{ name: "done_on", label: "Done on", type: "date", required: true }, { name: "odometer_km", label: "Odometer (km)", type: "number" }]}
                      send={(f) => record("service", { done_on: f.done_on, note: f.note,
                                                       ...(f.odometer_km ? { odometer_km: Number(f.odometer_km) } : {}) },
                                          `${v.rego}'s service is recorded.`)} />
          <RecordForm title="The odometer put right" button="Record the reading"
                      fields={[{ name: "odometer_km", label: "Reading (km)", type: "number", required: true }]}
                      send={(f) => record("odometer", { odometer_km: Number(f.odometer_km), note: f.note }, `${v.rego}'s odometer is put right.`)} />
        </div>
      )}
      {history.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <div className="eyebrow">HISTORY</div>
          <div className="box">
            {history.map((h) => (
              <div key={`${h.kind}-${h.id}`}><span>{historyLine(h)}</span>
                <span className="small" style={{ whiteSpace: "nowrap" }}>{when(h.recorded_at)} · {h.by}</span></div>))}
          </div>
        </div>
      )}
    </aside>
  );
}

/** Fleet.html: the trucks, whether each can go, the pre-start that
 * grounded one, and what a fleet manager records. */
export function Fleet() {
  const { reloadCounts } = useOutletContext<{ reloadCounts: () => void }>();
  const today = useMemo(() => isoDay(new Date()), []);
  const [vehicles, setVehicles] = useState<Vehicle[] | null>(null);
  const [runs, setRuns] = useState<Run[]>([]);
  const [q, setQ] = useState("");
  const [chosen, setChosen] = useState<string | null>(null);
  const [note, setNote] = useState<Note>(null);

  const load = useCallback(async () => {
    const [v, r] = await Promise.all([
      api.get<Page<Vehicle>>("/v1/vehicles", { date: today }),
      api.get<Page<Run>>("/v1/runs", { date: today }),
    ]);
    setVehicles(v.items);
    setRuns(r.items);
  }, [today]);

  useEffect(() => {
    load().catch((e) => setNote({ tone: "r", text: said(e) }));
    const t = setInterval(() => { load().catch(() => undefined); }, 30_000);
    return () => clearInterval(t);
  }, [load]);

  async function changed(n: Note) {
    setNote(n);
    await load().catch(() => undefined);
    reloadCounts();
  }

  const runOf = (rego: string) => runs.find((r) => r.vehicle.rego === rego && r.status !== "cancelled");
  const all = vehicles ?? [];
  const shown = all.filter((v) => {
    const s = q.trim().toLowerCase();
    return !s || v.rego.toLowerCase().includes(s) || (runOf(v.rego)?.driver.name ?? "").toLowerCase().includes(s);
  });
  const selected = all.find((v) => v.rego === chosen) ?? all.find((v) => v.state === "grounded") ?? all[0] ?? null;
  const active = all.filter((v) => v.active);
  const grounded = active.filter((v) => v.state === "grounded");
  const overdue = active.filter((v) => v.state === "overdue");
  const due = active.filter((v) => v.state === "due");
  const checked = active.filter((v) => v.last_prestart && isoDay(new Date(v.last_prestart.at)) === today);
  const types = ["rigid", "van", "ute"].map((t) => [t, active.filter((v) => v.type === t).length] as const).filter(([, n]) => n > 0);

  return (
    <Drawer label="Fleet" head={
      <div style={{ flex: 1, minWidth: 200 }}>
        <div className="eyebrow" style={{ color: "var(--color-green)" }}>Own fleet</div>
        <h1>Fleet</h1>
        <div className="sub">Your trucks, pre-start checks, and when rego and service fall due.</div>
      </div>
    }>
      <label className="search" style={{ maxWidth: 420 }}><Icon name="search" />
        <span className="sr">Search vehicles</span>
        <input type="search" placeholder="Rego or driver" value={q} onChange={(e) => setQ(e.target.value)} /></label>
      {note && <div className={`msg ${note.tone}`} role={note.tone === "r" ? "alert" : "status"}>{note.text}</div>}
      <div className="stats">
        <div className="stat"><div className="eyebrow">VEHICLES</div><div className="v num">{active.length}</div>
          <div className="d">{types.map(([t, n]) => `${n} ${t}`).join(" · ")}</div></div>
        <div className={`stat${grounded.length ? " red" : ""}`}><div className="eyebrow">GROUNDED</div><div className="v num">{grounded.length}</div>
          <div className="d">{grounded.map((v) => `${v.rego} · ${v.grounded?.failed.join(", ").toLowerCase()}`).join("; ") || "None"}</div></div>
        {overdue.length > 0 && (
          <div className="stat red"><div className="eyebrow">OVERDUE</div><div className="v num">{overdue.length}</div>
            <div className="d">{overdue.map((v) => v.rego).join(", ")}</div></div>)}
        <div className={`stat${due.length ? " amber" : ""}`}><div className="eyebrow">DUE SOON</div><div className="v num">{due.length}</div>
          <div className="d">{due.map((v) => `${v.rego} ${v.rego_due?.state === "due" ? "rego" : "service"}`).join(" · ") || "Nothing in the next 30 days"}</div></div>
        <div className="stat"><div className="eyebrow">PRE-STARTS TODAY</div><div className="v num">{checked.length}</div>
          <div className="d">{checked.filter((v) => v.last_prestart!.result === "passed").length} passed · {checked.filter((v) => v.last_prestart!.result !== "passed").length} failed</div></div>
      </div>
      <div className="split">
        <div className="tablewrap">
          <table style={{ minWidth: 720 }}>
            <thead><tr><th>REGO</th><th>TYPE</th><th>DRIVER</th><th>REGO EXPIRY</th><th>NEXT SERVICE</th><th>LAST PRE-START</th><th>STATUS</th></tr></thead>
            <tbody>
              {shown.map((v) => {
                const run = runOf(v.rego);
                const pill = statusPill(v, run?.status === "departed");
                const on = selected?.rego === v.rego;
                return (
                  <tr key={v.rego} className={on ? `sel${v.state === "grounded" || v.state === "overdue" ? " bad" : ""}` : undefined}>
                    <td><button type="button" className="rowbtn" aria-pressed={on} aria-label={`Show ${v.rego}`} onClick={() => setChosen(v.rego)}>{v.rego}</button></td>
                    <td style={{ textTransform: "capitalize" }}>{v.type}</td>
                    <td>{run?.driver.name ?? <span className="small">No run today</span>}</td>
                    <CellOut c={regoCell(v)} />
                    <CellOut c={serviceCell(v)} />
                    <CellOut c={checkCell(v)} />
                    <td><span className={`pill p-${pill.tone}`}>{pill.text}</span></td>
                  </tr>
                );
              })}
              {vehicles?.length === 0 && <tr><td colSpan={7} className="small">No vehicles yet. Add them with POST /v1/vehicles.</td></tr>}
            </tbody>
          </table>
        </div>
        {selected && <VehicleAside key={selected.rego} v={selected} driverName={runOf(selected.rego)?.driver.name ?? null} onChanged={changed} />}
      </div>
      <div className="small">Amber 30 days or 1,000 km before it's due, red once it's overdue. A grounded or overdue truck can't take a run.
        Due dates show here only.</div>
    </Drawer>
  );
}
```

Create `apps/desktop/src/ui/Photo.tsx`:
```tsx
import { useEffect, useState } from "react";
import { api } from "../api/client";

/** A driver's photo. An <img> can't send the session's token, so the bytes
 * are fetched with it and shown from a blob: URL, let go when it is gone. */
export function Photo({ fileId, label }: { fileId: string; label: string }) {
  const [url, setUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let live = true;
    let made: string | null = null;
    api.blob(`/v1/pod-files/${fileId}`)
      .then((b) => { if (live) { made = URL.createObjectURL(b); setUrl(made); } })
      .catch(() => { if (live) setFailed(true); });
    return () => { live = false; if (made) URL.revokeObjectURL(made); };
  }, [fileId]);
  if (failed) return <span className="small">The photo couldn't be loaded.</span>;
  if (!url) return <span className="small">Loading the photo…</span>;
  return <img src={url} alt={label} width={120} height={80} style={{ objectFit: "cover", borderRadius: 10, display: "block", flex: "none" }} />;
}
```

Change `apps/desktop/src/ui/icons.tsx`:
```diff
--- a/apps/desktop/src/ui/icons.tsx
+++ b/apps/desktop/src/ui/icons.tsx
@@ -36,4 +36,6 @@ const PATHS = {
   hand: <><path d="M3 11l4-4 4 2 3-2 7 5-3 3" /><path d="M7 7v6l4 4 3-1 4-2" /></>,
   truck: <><path d="M2 6h12v10H2Z" /><path d="M14 9h4.5L22 12.5V16h-8Z" /><circle cx="6.5" cy="17.5" r="2" /><circle cx="17.5" cy="17.5" r="2" /></>,
   go: <path d="M5 12h14M13 6l6 6-6 6" />,
+  tick: <path d="m5 12 5 5 9-10" />,
+  wrench: <path d="M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18l3 3 6.3-6.3a4 4 0 0 0 5.4-5.4l-2.6 2.6-2.4-.6-.6-2.4Z" />,
 };
```

**Step 4: Run the tests and the build**

Run: `npm test && npm run build`
Expected: `64 passed`, and a `dist/`.

**Step 5: Commit**

```bash
git add apps/desktop/src/lib/fleet.ts apps/desktop/src/ui/Photo.tsx apps/desktop/src/pages/Fleet.tsx \
        apps/desktop/src/App.tsx apps/desktop/src/index.css apps/desktop/src/ui/icons.tsx \
        apps/desktop/src/test/harness.tsx apps/desktop/src/test/MapHome.test.tsx \
        apps/desktop/src/test/fleet.test.ts apps/desktop/src/test/Fleet.test.tsx
git commit -m "Build the Fleet drawer: due dates, the failed check, Mark fixed, records"
```

### Task 13: Trucks that can't go, on Plan runs and the map

**Files:**
- Modify: `apps/desktop/src/pages/PlanRuns.tsx` (trucks as they stand on the day; a blocked run's lane; the unavailable slot; another truck; the pre-start line)
- Modify: `apps/desktop/src/pages/MapHome.tsx` (the Fleet button's count)
- Test: `apps/desktop/src/test/PlanRuns.test.tsx`, `apps/desktop/src/test/MapHome.test.tsx`

**Step 1: Write the failing tests**

Change `apps/desktop/src/test/MapHome.test.tsx`:
```diff
--- a/apps/desktop/src/test/MapHome.test.tsx
+++ b/apps/desktop/src/test/MapHome.test.tsx
@@ -28,6 +28,20 @@ describe("the map", () => {
     expect(screen.getByRole("dialog", { name: "Fleet" })).toBeInTheDocument();
   });
 
+  it("counts on the Fleet button the trucks that can't take a run today", async () => {
+    const { calls } = stubApi((_m, url) => {
+      if (path(url) === "/v1/vehicles") {
+        return json(200, { items: [{ rego: "TMS027", active: true, state: "grounded" }, { rego: "TMS018", active: true, state: "due" },
+                                   { rego: "TMS041", active: true, state: "overdue" }], total: 3 });
+      }
+      return json(200, { items: [], total: 0 });
+    });
+    renderApp("/");
+    const fleet = await screen.findByRole("link", { name: /Fleet/ });
+    await waitFor(() => expect(fleet).toHaveTextContent("Fleet2"));
+    expect(calls.find((c) => path(c.url) === "/v1/vehicles")!.url).toMatch(/active=true/);
+  });
+
   it("signs out from the account button", async () => {
     const { calls } = stubApi(() => json(200, { items: [], total: 0 }));
     renderApp("/");
```

Change `apps/desktop/src/test/PlanRuns.test.tsx`:
```diff
--- a/apps/desktop/src/test/PlanRuns.test.tsx
+++ b/apps/desktop/src/test/PlanRuns.test.tsx
@@ -326,4 +326,50 @@ describe("Plan runs", () => {
     expect(await screen.findByText("R-0001 is finished.")).toBeInTheDocument();
     confirm.mockRestore();
   });
+
+  describe("a truck that can't go (rule 3)", () => {
+    const GROUNDED = { code: "vehicle_grounded",
+                       message: "TMS041 is grounded: Brakes failed the pre-start. A fleet manager has to mark it fixed first." };
+    const FLEET = [
+      { id: 1, rego: "TMS041", type: "rigid", max_kg: "4500", max_m3: "22", active: true, blocked: GROUNDED },
+      { id: 2, rego: "TMS052", type: "rigid", max_kg: "4500", max_m3: "22", active: true, blocked: null },
+      { id: 3, rego: "TMS063", type: "van", max_kg: "1200", max_m3: "9", active: true, blocked: null },
+      { id: 4, rego: "TMS018", type: "van", max_kg: "1200", max_m3: "9", active: true,
+        blocked: { code: "vehicle_overdue", message: "TMS018's rego runs out on 10 Oct 2026. Record the new expiry in Fleet first." } },
+    ];
+    const grounded = { ...KIM, vehicle: { ...KIM.vehicle, blocked: GROUNDED },
+                       prestart: { id: 12, at: "2026-10-13T19:40:00Z", result: "grounded" as const } };
+
+    it("says so on its run's lane, takes nothing more on it, and gives the run another truck", async () => {
+      const { calls } = board({
+        "GET /v1/runs": () => json(200, { items: [grounded, MEL], total: 2 }),
+        "GET /v1/vehicles": () => json(200, { items: FLEET, total: 4 }),
+      });
+      renderApp("/plan");
+      const kim = await lane("Run R-0001");
+      expect(within(kim).getByText("Grounded")).toBeInTheDocument();
+      expect(within(kim).getByText(GROUNDED.message)).toBeInTheDocument();
+      expect(within(kim).getByText(/Give the run another truck, or move its stops/)).toBeInTheDocument();
+      expect(within(kim).getByText(/Pre-start .*: failed\./)).toBeInTheDocument();
+      expect(within(kim).getByRole("button", { name: /Leaves now/ })).toBeDisabled();
+      expect(within(kim).getByRole("button", { name: /Print/ })).toBeDisabled();
+      // the tray offers only the runs that can take stops
+      const put = screen.getByRole("combobox", { name: "Put Paddock Produce on a run" });
+      expect(within(put).queryByRole("option", { name: /R-0001/ })).not.toBeInTheDocument();
+      // the overdue truck with no run waits with the ones that can't go; it isn't free
+      expect(within(await lane("TMS018, unavailable")).getByText("Overdue")).toBeInTheDocument();
+      expect(screen.getByText(/1 truck free: TMS063/)).toBeInTheDocument();
+      await userEvent.setup().selectOptions(within(kim).getByRole("combobox", { name: "Give R-0001 another truck" }), "TMS063");
+      await waitFor(() => expect(calls.find((c) => c.method === "PATCH")).toEqual(
+        { method: "PATCH", url: "/v1/runs/1", body: { vehicle: "TMS063" } }));
+    });
+
+    it("asks for the trucks as they stand on the day being planned", async () => {
+      const { calls } = board();
+      renderApp("/plan");
+      await lane("Run R-0001");
+      expect(calls.find((c) => path(c.url) === "/v1/vehicles")!.url).toMatch(/^\/v1\/vehicles\?date=\d{4}-\d{2}-\d{2}$/);
+      expect(within(await lane("Run R-0001")).getByText("No pre-start yet.")).toBeInTheDocument();
+    });
+  });
 });
```

**Step 2: Run them to see them fail**

Run: `npx vitest run src/test/PlanRuns.test.tsx src/test/MapHome.test.tsx`
Expected: `3 failed`: no "Grounded" on R-0001's lane, `/v1/vehicles` asked without a `date`, and the Fleet button reads "Fleet".

**Step 3: Implement**

Change `apps/desktop/src/pages/MapHome.tsx`:
```diff
--- a/apps/desktop/src/pages/MapHome.tsx
+++ b/apps/desktop/src/pages/MapHome.tsx
@@ -1,9 +1,10 @@
 import { useCallback, useEffect, useState, type FormEvent } from "react";
 import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
 import { api } from "../api/client";
-import type { Page, Run } from "../api/types";
+import type { Page, Run, Vehicle } from "../api/types";
 import { useAuth } from "../auth/AuthContext";
 import { clock, isoDay } from "../lib/days";
+import { cantGo } from "../lib/fleet";
 import { DrawerBoundary } from "../ui/DrawerBoundary";
 import { Icon, Logo } from "../ui/icons";
 
@@ -24,6 +25,8 @@ export function MapHome() {
   const navigate = useNavigate();
   const { pathname } = useLocation();
   const [counts, setCounts] = useState<Counts | null>(null);
+  // trucks grounded or overdue today: a person needs to look
+  const [stuck, setStuck] = useState(0);
   const [now, setNow] = useState(() => new Date());
   const [menu, setMenu] = useState(false);
   const [q, setQ] = useState("");
@@ -38,6 +41,9 @@ export function MapHome() {
         (n, run) => n + run.stops.filter((s) => s.outcome === "delivered").length, 0);
       setCounts({ toPlan, onRoad, delivered });
     } catch { /* the numbers wait for the next go */ }
+    try {
+      setStuck(cantGo((await api.get<Page<Vehicle>>("/v1/vehicles", { date: isoDay(new Date()), active: true })).items));
+    } catch { /* so does this one */ }
   }, []);
 
   useEffect(() => {
@@ -105,7 +111,8 @@ export function MapHome() {
           </NavLink>
           <NavLink to="/consignments" className={({ isActive }) => (isActive ? "on" : "")}><Icon name="box" />Consignments</NavLink>
           <NavLink to="/carriers" className={({ isActive }) => (isActive ? "on" : "")}><Icon name="hand" />Carriers</NavLink>
-          <NavLink to="/fleet" className={({ isActive }) => (isActive ? "on" : "")}><Icon name="truck" />Fleet</NavLink>
+          <NavLink to="/fleet" className={({ isActive }) => (isActive ? "on" : "")}><Icon name="truck" />Fleet
+            {stuck > 0 && <i className="r" title={`${stuck} can't take a run`}>{stuck}</i>}</NavLink>
         </nav>
       </main>
       <DrawerBoundary key={pathname}><Outlet context={{ reloadCounts: load }} /></DrawerBoundary>
```

Change `apps/desktop/src/pages/PlanRuns.tsx`:
```diff
--- a/apps/desktop/src/pages/PlanRuns.tsx
+++ b/apps/desktop/src/pages/PlanRuns.tsx
@@ -35,8 +35,13 @@ function Meter({ label, unit, g }: { label: string; unit: string; g: Gauge }) {
   );
 }
 
-/** A truck that can't take a run. Deactivated for now; grounded and overdue
- * arrive with fleet care (step 4) and use the same slot. */
+/** Why a truck can't go, in a word: grounded, overdue or deactivated. */
+function blockedWord(b: { code: string }): string {
+  return b.code === "vehicle_grounded" ? "Grounded" : "Overdue";
+}
+
+/** A truck that can't take a run: deactivated, grounded by a failed
+ * pre-start, or with its rego or service overdue on the day. */
 function Unavailable({ v, reason, detail }: { v: Vehicle; reason: string; detail: string }) {
   return (
     <section className="lane out" aria-label={`${v.rego}, unavailable`}>
@@ -142,7 +147,8 @@ export function PlanRuns() {
         api.get<Page<Run>>("/v1/runs", { date: day }),
         // packed, and any the driver couldn't deliver, back to be planned again
         api.get<Page<Consignment>>("/v1/consignments", { status: "packed,failed", limit: 2000 }),
-        api.get<Page<Vehicle>>("/v1/vehicles"),
+        // where each truck stands on the day being planned
+        api.get<Page<Vehicle>>("/v1/vehicles", { date: day }),
         api.get<Page<Driver>>("/v1/drivers", { active: true }),
       ]);
     } catch (e) {
@@ -179,7 +185,8 @@ export function PlanRuns() {
     }
   }
 
-  const planned = runs.filter((r) => r.status === "planned" && r.vehicle.active);
+  // the runs that can take stops: planned, on a truck that can go
+  const planned = runs.filter((r) => r.status === "planned" && r.vehicle.active && !r.vehicle.blocked);
   const assign = (runId: number, c: Card, position?: number) =>
     act(() => api.post(`/v1/runs/${runId}/assign`, { consignment_id: c.id, position }));
   const unassign = (runId: number, c: Card) =>
@@ -278,8 +285,13 @@ export function PlanRuns() {
 
   // ---- adding a run ---------------------------------------------------------
   const busyTrucks = new Set(runs.map((r) => r.vehicle.rego));
-  const free = vehicles.filter((v) => v.active && !busyTrucks.has(v.rego));
-  const unavailable = vehicles.filter((v) => !v.active);
+  const free = vehicles.filter((v) => v.active && !v.blocked && !busyTrucks.has(v.rego));
+  // a blocked truck with a run shows it on its lane instead
+  const unavailable = vehicles.filter((v) => !v.active || (v.blocked && !busyTrucks.has(v.rego)));
+
+  /** A run whose truck can't go, given one that can. */
+  const swap = (run: Run, rego: string) =>
+    act(() => api.patch(`/v1/runs/${run.id}`, { vehicle: rego }), `${run.run_no} goes on ${rego} now.`);
 
   function addRun(e: FormEvent<HTMLFormElement>) {
     e.preventDefault();
@@ -369,11 +381,15 @@ export function PlanRuns() {
         {runs.map((run, i) => {
           const colour = runColour(i);
           const open = run.status === "planned";
+          const blocked = run.vehicle.blocked ?? null;
+          // a planned run on a truck that can't go takes no more stops
+          const takes = open && run.vehicle.active && !blocked;
           const l = loadOf(run.stops.map((s) => s.consignment), run.vehicle.max_kg, run.vehicle.max_m3);
           const ids = run.stops.map((s) => s.consignment.id);
           const others = planned.filter((r) => r.id !== run.id);
           const sheet = sheetLine(run);
-          const pill = run.status === "done" ? <span className="pill p-grey">Done</span>
+          const pill = open && blocked ? <span className="pill p-red"><Icon name="ban" size={13} width={2.4} />{blockedWord(blocked)}</span>
+            : run.status === "done" ? <span className="pill p-grey">Done</span>
             : !open ? <span className="pill p-grey">Departed</span>
             : l.state === "over" ? <span className="pill p-red">Over</span>
             : l.state === "near" ? <span className="pill p-amber">Nearly full</span>
@@ -383,11 +399,19 @@ export function PlanRuns() {
           return (
             <section key={run.id} aria-label={`Run ${run.run_no}`}
                      className={`lane${l.state === "over" ? " over" : l.state === "near" ? " near" : ""}${hint ? " target" : ""}`}
-                     onDragOver={open && run.vehicle.active ? (e) => overRun(e, run) : undefined}
-                     onDrop={open && run.vehicle.active ? (e) => dropOnRun(e, run) : undefined}>
+                     onDragOver={takes ? (e) => overRun(e, run) : undefined}
+                     onDrop={takes ? (e) => dropOnRun(e, run) : undefined}>
               <div className="lhead"><span className="swatch" style={{ background: colour }} />
                 <div style={{ flex: 1, minWidth: 0 }}><div className="t">{run.run_no} · {run.driver.name}</div>
                   <div className="s">{run.vehicle.rego} · {run.vehicle.type}</div></div>{pill}</div>
+              {blocked && run.status !== "done" && (
+                <div className="msg r" role="status"><Icon name="ban" size={14} width={2.4} /><span><b>{blocked.message}</b>
+                  {open ? " Give the run another truck, or move its stops to another run." : " It is out on this run: call the driver."}</span></div>)}
+              {open && blocked && free.length > 0 && (
+                <ActionSelect label={`Give ${run.run_no} another truck`} placeholder="Another truck…" disabled={busy}
+                              onChoose={(rego) => void swap(run, rego)}>
+                  {free.map((v) => <option key={v.rego} value={v.rego}>{v.rego} · {v.type}</option>)}
+                </ActionSelect>)}
               <Meter label="Weight" unit="kg" g={l.weight} />
               <Meter label="Volume" unit="m³" g={l.volume} />
               {l.state === "over" && <div className="msg r"><Icon name="warn" size={14} width={2.4} /><span><b>{l.headline}</b> {l.message}</span></div>}
@@ -440,6 +464,10 @@ export function PlanRuns() {
               {(run.load.unknown_weight > 0 || run.load.unknown_volume > 0) && (
                 <div className="small">Some packages have no weight or size, so the bars may read low.</div>)}
               {sheet && <div className={`msg ${sheet.tone}`}>{sheet.text}</div>}
+              {open && (
+                <div className="small">{run.prestart
+                  ? `Pre-start ${clock(new Date(run.prestart.at))}: ${run.prestart.result === "passed" ? "passed" : run.prestart.result === "failed" ? "an item failed" : "failed"}.`
+                  : "No pre-start yet."}</div>)}
               {!open && <div className="small">{tripLine(run)}</div>}
               {!open && (
                 <div className="small">Marked shipped in Simple WMS: {run.shipping.done} of {run.shipping.done + run.shipping.pending + run.shipping.failed}.</div>)}
@@ -449,11 +477,11 @@ export function PlanRuns() {
                           onClick={() => void act(() => api.post(`/v1/runs/${run.id}/retry-ship`), "Trying Simple WMS again.")}>Try again</button></div>)}
               <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                 {run.stops.length > 0 && (
-                  <button type="button" className="btn sm" disabled={busy || l.state === "over"}
+                  <button type="button" className="btn sm" disabled={busy || l.state === "over" || (open && !!blocked)}
                           onClick={() => void act(() => api.post(`/v1/runs/${run.id}/print`), `${run.run_no} run sheet sent to Platen.`)}>
                     <Icon name="print" size={14} />Print</button>)}
                 {open && run.stops.length > 0 && (
-                  <button type="button" className="btn sm primary" disabled={busy || l.state === "over"} onClick={() => depart(run)}>
+                  <button type="button" className="btn sm primary" disabled={busy || l.state === "over" || !!blocked} onClick={() => depart(run)}>
                     Leaves now<Icon name="go" size={14} width={2.4} /></button>)}
                 {run.status === "departed" && can("runs:write") && run.stops.length > 0
                   && run.stops.every((s) => s.outcome) && (
@@ -470,8 +498,10 @@ export function PlanRuns() {
         {unavailable.length > 0 && (
           <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
             {unavailable.map((v) => (
-              <Unavailable key={v.rego} v={v} reason="Deactivated"
-                           detail="Can't take a run until a fleet manager reactivates it." />))}
+              v.active && v.blocked
+                ? <Unavailable key={v.rego} v={v} reason={blockedWord(v.blocked)} detail={v.blocked.message} />
+                : <Unavailable key={v.rego} v={v} reason="Deactivated"
+                               detail="Can't take a run until a fleet manager reactivates it." />))}
           </div>
         )}
       </div></div>
```

**Step 4: Run the tests and the build**

Run: `npm test && npm run build`
Expected: `67 passed`, and a `dist/`.

**Step 5: Commit**

```bash
git add apps/desktop/src/pages/PlanRuns.tsx apps/desktop/src/pages/MapHome.tsx \
        apps/desktop/src/test/PlanRuns.test.tsx apps/desktop/src/test/MapHome.test.tsx
git commit -m "Show the trucks that can't go on Plan runs and the map, and give a run another truck"
```

## Part D — docs and checks

### Task 14: README

**Files:**
- Modify: `README.md` (the status, how fleet care is used, three known limitations)

`CLAUDE.md` needs no change: rule 1 already names pre-start checks and rule 3 is what this step builds; the layout and commands are as they were. CI needs none either: the new tests run in the existing jobs, and the migration runs when the Compose job's API starts.

**Step 1: The README**

Change `README.md`:
```diff
--- a/README.md
+++ b/README.md
@@ -4,14 +4,18 @@ An open source, self-hosted transport management system for businesses that
 deliver with their own trucks and also send freight with outside carriers.
 It sits beside Simple WMS and Platen and works without either.
 
-Status: **build step 3, the driver app.** Drivers sign in on their phones,
-start the run, deliver each stop with a signature, a photo or both (or say
-why they couldn't), and finish the run, with or without signal; on top of
-step 2 (runs planned on a map-and-drawer desktop, run sheets printed through
+Status: **build step 4, fleet care.** Drivers do a pre-start check of the
+truck on their phones before each run; a failed critical item grounds the
+truck until a fleet manager marks it fixed, and the desktop's **Fleet**
+shows each truck's rego and service due (amber a month or 1,000 km out, red
+when overdue). A grounded or overdue truck can't be given a run. On top of
+step 3 (the driver app: start the run, deliver each stop with a signature, a
+photo or both, or say why not, and finish, with or without signal), step 2
+(runs planned on a map-and-drawer desktop, run sheets printed through
 Platen, Simple WMS told each delivery shipped when its run leaves) and step 1
 (sign-in, vehicles and drivers, consignments from Simple WMS's
-`delivery.packed`). Fleet care, live location and carriers come with the
-next steps in `docs/plans/2026-10-10-simple-tms-design.md`.
+`delivery.packed`). Live location and carriers come with the next steps in
+`docs/plans/2026-10-10-simple-tms-design.md`.
 
 ## What is in this repo
 
@@ -61,6 +65,22 @@ coming back). Rebuild it (`npm run build` in `apps/driver`) after pulling
 changes; Caddy serves `apps/driver/dist` as it is. Signatures and photos are
 kept in the `pod_files` volume: back it up with the database.
 
+**Fleet care.** Before starting a run the driver does the truck's pre-start
+check on the phone: the odometer, then each item passed or failed, a failed
+one with a photo and a note. It goes through the phone's queue like a
+delivery, so it can be done with no signal, but the run starts only once the
+office has it. A failed critical item (Lights and Brakes, to start with)
+grounds the truck; a fleet manager (the `manager` role, or an admin) opens
+**Fleet**, looks at the check with its photos and notes, and marks it fixed
+with what was done. In **Fleet** a fleet manager also records each new rego
+expiry and each service (with its odometer reading), and puts a wrong
+odometer reading right; set how often a truck is serviced with
+`service_every_months` and/or `service_every_km` on `PATCH /v1/vehicles/{rego}`.
+A truck with nothing recorded is never due, so record each truck's rego
+expiry and last service when you start. The checklists are the design's six
+items for each type of vehicle; change them with `PUT /v1/checklists/{type}`
+(see `docs/api.md`).
+
 ## Production on a real domain
 
 Let's Encrypt only checks a domain on ports 80 and 443, so Caddy gets a
@@ -323,6 +343,18 @@ stood in for by `fake-indexeddb`: `cd apps/driver && npm test`.
 
 ## Known limitations
 
+- **The office's Leaves now doesn't ask for a pre-start.** The driver app
+  won't start a run until the truck's pre-start is in, but **Leaves now** on
+  the desktop (for a driver without a phone, who does a paper check) sends it
+  without one. **Plan runs** says "No pre-start yet" on such a run. A
+  grounded or overdue truck is refused either way.
+- **Fleet doesn't add vehicles or drivers yet.** Add them, and set how often
+  each is serviced, through the API (`POST /v1/vehicles`, `POST /v1/drivers`,
+  `PATCH …`); the checklists too (`PUT /v1/checklists/{type}`).
+- **Due dates are on screen only.** The `vehicle.due` event in the design, for
+  another system to send email or SMS, comes with the first system that
+  subscribes.
+
 - **What came back is still shipped in Simple WMS.** When a driver can't
   deliver, the consignment comes back to the depot and returns to the
   dispatcher's tray to be planned again. Simple WMS still has it as shipped,
```

**Step 2: Commit**

```bash
git add README.md
git commit -m "Document fleet care in the README"
```

### Task 15: Every suite, and a clean Compose start

**Step 1: The suites**

Run:
```
cd "/Users/leightonlauton/Q7 Technology/Software/q7/simple-tms/api" && .venv/bin/pytest -q
cd ../apps/desktop && npm test && npm run build
cd ../driver && npm test && npm run build
```
Expected: `278 passed`; `67 passed` and a `dist/`; `84 passed` and a `dist/` with `sw.js`.

**Step 2: Compose**

Run (repo root):
```
docker compose up -d --build api worker
docker compose up -d --force-recreate caddy
docker compose ps
docker compose exec -T db psql -U tms -d tms -tAc "select version_num from alembic_version"
docker compose exec -T db psql -U tms -d tms -tAc "select vehicle_type, count(*) from checklist_item where retired_at is null group by 1 order by 1"
curl -skI https://localhost:8543/ | grep -i content-security-policy
```
Expected: `api` healthy, `worker`, `db` and `caddy` up; `0005`; `rigid|6`, `ute|6`, `van|6`; the desktop's CSP with `img-src 'self' blob:`. (`--force-recreate`: the Caddyfile is mounted as a single file, and a branch switch replaces the file the container still holds.)

**Step 3: Push**

```bash
git push -u origin step-4-fleet-care
```
Wait for CI (API, Desktop app, Driver app, Docker Compose comes up) to go green, then do Task 16 before **superpowers:finishing-a-development-branch**.

### Task 16: Manual check on the local stacks: grounded and refused, Mark fixed, overdue rego, service due by the odometer, a check with no signal

Everything runs on this machine: Simple TMS on this branch (Task 15 Step 2 left it up), Simple WMS at `127.0.0.1:8011`. Scripts and screenshots go in a working folder of your own in your scratchpad, never `/tmp` and never the repo: set `W` to it in each shell.

```
export W="<your scratchpad directory>/tms-step4-check"
mkdir -p "$W/shots"
```

**Step 1: Tokens, and Kim's PIN**

```
cd "/Users/leightonlauton/Q7 Technology/Software/q7/simple-tms"
token() { curl -s -X POST http://127.0.0.1:8022/v1/auth/login -H 'Content-Type: application/json' \
  -d "{\"username\":\"$1\",\"password\":\"$2\"}" | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])'; }
ADMIN=$(token leighton "a long enough password"); DEE=$(token dee "dispatch password")
curl -s -X PATCH http://127.0.0.1:8022/v1/drivers/kim -H "Authorization: Bearer $ADMIN" \
  -H 'Content-Type: application/json' -d '{"pin":"2468"}' >/dev/null
curl -s -X POST http://127.0.0.1:8022/v1/drivers/kim/unlock -H "Authorization: Bearer $ADMIN" | python3 -m json.tool | grep locked
```
Expected: `"locked": false`. (Access tokens last 15 minutes: run `token` again if a later call says `401`.)

**Step 2: What the fleet manager knows about TMS052 and TMS053**

TMS052 is serviced every 6 months or 15,000 km and was last serviced at 46,900 km; TMS053's rego ran out yesterday.
```
api() { curl -s -X "$1" "http://127.0.0.1:8022$2" -H "Authorization: Bearer $ADMIN" -H 'Content-Type: application/json' ${3:+-d "$3"}; }
TODAY=$(date +%F); YESTERDAY=$(date -v-1d +%F); NEXT_YEAR=$(date -v+1y +%F)
api PATCH /v1/vehicles/TMS052 '{"service_every_months":6,"service_every_km":15000}' >/dev/null
api POST /v1/vehicles/TMS052/service "{\"done_on\":\"$(date -v-3m +%F)\",\"odometer_km\":46900,\"note\":\"Last service, from the logbook\"}"
api POST /v1/vehicles/TMS053/rego "{\"expires_on\":\"$YESTERDAY\",\"note\":\"From the rego papers\"}"
api GET "/v1/vehicles?date=$TODAY" | python3 -c 'import sys,json
for v in json.load(sys.stdin)["items"]: print(v["rego"], v["state"], v["blocked"] and v["blocked"]["code"], v["service_due"]["due_km"])'
```
Expected: two records (`"kind": "service"`, `"kind": "rego"`, `"recorded_by": "leighton"`), then `TMS041 ok None None`, `TMS052 ok None 61900`, `TMS053 overdue vehicle_overdue None`.

**Step 3: Two consignments to plan**

```
curl -s "http://127.0.0.1:8022/v1/consignments?status=packed,failed" -H "Authorization: Bearer $DEE" \
  | python3 -c 'import sys,json;d=json.load(sys.stdin);print(d["total"],[(c["id"],c["customer"]) for c in d["items"]])'
```
With two or more, go on. Otherwise pack two in Simple WMS with the script in `docs/plans/2026-10-11-step-3-driver-app.md`, Task 22 Step 3 (a key made for it, `REF=TMS-FLT-1` and `REF=TMS-FLT-2`; save the script under `$W`, not `/tmp`).

**Step 4: The overdue truck is refused, and the other is planned**

```
api POST /v1/runs "{\"date\":\"$TODAY\",\"vehicle\":\"TMS053\",\"driver\":\"kim\"}"
```
Expected: `{"detail":"TMS053's rego runs out on <yesterday, e.g. 11 Oct 2026>. Record the new expiry in Fleet first.","code":"vehicle_overdue"}`.

In the desktop (`https://localhost:8543`, sign in as `dee` / `dispatch password`), **Plan runs**, **Day: Today**: a dashed **TMS053, unavailable** lane with a red **Overdue** pill and that message; the header's free trucks and **Add a run**'s list leave TMS053 out. **Add a run**: `TMS052`, Kim. Drag one consignment onto it. Its lane says "No pre-start yet." The map's **Fleet** button shows a red **1**.

**Step 5: Kim fails Brakes on TMS052, at 61,208 km**

Playwright drives the Chrome already installed:
```
cd "$W" && npm init -y >/dev/null && npm i playwright-core@1 >/dev/null
sips -s format jpeg "/Users/leightonlauton/Q7 Technology/Software/q7/simple-tms/apps/driver/public/icon-512.png" --out "$W/fault.jpg"
```
Save as `$W/prestart.mjs`:
```js
import { createRequire } from "module";
const W = process.env.W;
const require = createRequire(`${W}/`);
const { chromium, devices } = require("playwright-core");
const BASE = "https://localhost:8543";
// grounded: TMS052, Brakes failed with a photo and a note. offline: TMS053, all passed, in a black spot.
const MODE = process.argv[2];
const REGO = MODE === "grounded" ? "TMS052" : "TMS053";

const browser = await chromium.launch({
  executablePath: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  headless: true, args: ["--ignore-certificate-errors"] });
const ctx = await browser.newContext({ ...devices["Pixel 7"], ignoreHTTPSErrors: true });
const page = await ctx.newPage();
page.on("pageerror", (e) => console.log("pageerror:", e.message));
const shot = (n) => page.screenshot({ path: `${W}/shots/${MODE}-${n}.png` });

await page.goto(`${BASE}/driver/`);
await page.getByLabel("Driver code").fill("kim");
for (const d of "2468") await page.getByRole("button", { name: d, exact: true }).click();
await page.getByRole("button", { name: "Sign in" }).click();
await page.getByRole("radio", { name: new RegExp(REGO) }).click();
const list = page.getByRole("region", { name: "Checklist for a van" });
await list.waitFor();
await page.getByLabel(/Odometer/).fill(MODE === "grounded" ? "61208" : "30500");
for (const item of ["Tyres", "Lights", "Brakes", "Mirrors", "Load restraints", "Damage"]) {
  const fail = MODE === "grounded" && item === "Brakes";
  await list.getByRole("radiogroup", { name: item }).getByRole("radio", { name: fail ? "Fail" : "Pass" }).click();
}
if (MODE === "grounded") {
  await page.locator('input[type="file"]').setInputFiles(`${W}/fault.jpg`);
  await page.getByRole("img", { name: "The photo taken" }).waitFor();
  await page.getByLabel("Brakes: note").fill("Pedal goes most of the way to the floor.");
}
await shot("1-check");
if (MODE === "offline") await ctx.setOffline(true);
await page.getByRole("button", { name: "Finish check" }).click();

if (MODE === "grounded") {
  await page.getByText(/TMS052 is grounded: Brakes failed the pre-start/).waitFor();
  await shot("2-grounded");
  console.log("start offered:", (await page.getByRole("button", { name: "Start run" }).count()) > 0);
} else {
  await page.getByText(/Your pre-start is kept on this phone/).waitFor();
  console.log("start offered with no signal:", (await page.getByRole("button", { name: "Start run" }).count()) > 0);
  await shot("2-kept");
  await ctx.setOffline(false);
  await page.evaluate(() => window.dispatchEvent(new Event("online")));
  await page.getByRole("button", { name: "Start run" }).waitFor({ timeout: 30_000 });
  await shot("3-sent");
  await page.getByRole("button", { name: "Start run" }).click();
  await page.getByRole("link", { name: /Start stop 1/ }).waitFor();
  console.log("started: ok");
}
await browser.close();
```
Run: `cd "$W" && W="$W" node prestart.mjs grounded`
Expected: `start offered: false`. Look at `shots/grounded-1-check.png` and `-2-grounded.png` beside `design/screens/DriverPrestart.html`: odometer, the van checklist with "Critical item" under Lights and Brakes, the red fail block with the photo and note; then the red "TMS052 is grounded: Brakes failed the pre-start. A fleet manager has to mark it fixed first. Don't take it out: call the office." and the radio reading "Grounded · R-…".

**Step 6: The office can't send it, and sees why**

```
RUN52=$(api GET "/v1/runs?date=$TODAY" | python3 -c 'import sys,json;print([r["id"] for r in json.load(sys.stdin)["items"] if r["vehicle"]["rego"]=="TMS052"][0])')
curl -s -X POST "http://127.0.0.1:8022/v1/runs/$RUN52/depart" -H "Authorization: Bearer $DEE"; echo
CID=$(curl -s "http://127.0.0.1:8022/v1/consignments?status=packed,failed" -H "Authorization: Bearer $DEE" \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["items"][0]["id"])')
curl -s -X POST "http://127.0.0.1:8022/v1/runs/$RUN52/assign" -H "Authorization: Bearer $DEE" \
  -H 'Content-Type: application/json' -d "{\"consignment_id\":$CID}"; echo
```
Expected: both `{"detail":"TMS052 is grounded: Brakes failed the pre-start. A fleet manager has to mark it fixed first.","code":"vehicle_grounded"}`.

In the desktop as `dee`: **Plan runs**: TMS052's lane has a red **Grounded** pill, the message and "Give the run another truck, or move its stops to another run.", "Pre-start …: failed.", **Leaves now** and **Print** greyed, and it isn't in the tray's **Put on a run…** list. The map's **Fleet** button shows **2**. **Fleet**: TMS052 is selected, red **Grounded** pill, "PRE-START CHECK · FAILED", "Van · Kim · <today>, <time> · odometer 61,208 km", Brakes failed with "Critical item", Kim's photo (the TMS icon) and note, "It can't go on a run until a fleet manager marks it fixed.", and **no Mark fixed** (a dispatcher). TMS053's row: its rego date in red, "ran out 1 day ago", **Rego overdue**.

**Step 7: Mark fixed; then service due by the odometer**

Sign out, sign in as `leighton`. **Fleet**, TMS052: **MARK FIXED**, "What was done": `New brake pads, road tested`, **Mark fixed**. Expected: "TMS052 is marked fixed, and can take a run."; its row's status is now amber **Service due**, NEXT SERVICE "in 692 km" over "or <date>" (61,900 − 61,208), and the aside shows "Marked fixed by leighton: New brake pads, road tested" and the history (Marked fixed, Pre-start failed: Brakes, Service). Then the record is new and the check unchanged:
```
docker compose exec -T db psql -U tms -d tms -c "
  select kind, check_id, note, recorded_by from vehicle_record order by id desc limit 2" -c "
  select id, result, odometer_km from prestart_check order by id desc limit 1"
docker compose exec -T db psql -U tms -d tms -c "update prestart_check set result = 'passed'"
```
Expected: `fixed | <check id> | New brake pads, road tested | leighton` and the rego record; the check still `grounded | 61208`; the update refused: `prestart_check is append-only: rows cannot be updated or deleted`.

Kim's phone can start now (the check is in and the truck is fixed):
```
KIM=$(curl -s -X POST http://127.0.0.1:8022/v1/auth/driver-login -H 'Content-Type: application/json' \
  -d '{"code":"kim","pin":"2468"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')
curl -s "http://127.0.0.1:8022/v1/driver/runs?date=$TODAY" -H "Authorization: Bearer $KIM" \
  | python3 -c 'import sys,json;print([(r["vehicle"]["rego"], r["vehicle"]["blocked"], r["prestart"] and r["prestart"]["result"]) for r in json.load(sys.stdin)])'
```
Expected: TMS052 with `None` (not blocked) and `'grounded'` (the check as sent). Leave it planned.

**Step 8: TMS053's new rego, and a check with no signal**

As `leighton`, **Fleet**, TMS053, **A new rego expiry**: runs out on a date a year from now, note `Renewed online`, **Record the rego**. Expected: "TMS053's rego is recorded.", its row back to **At depot**, the map's **Fleet** count gone. In **Plan runs**: **Add a run**, `TMS053`, Kim, and drag the other consignment onto it.

Run: `cd "$W" && W="$W" node prestart.mjs offline`
Expected:
```
start offered with no signal: false
started: ok
```
and `shots/offline-2-kept.png` showing "Your pre-start is kept on this phone. It goes as soon as there's signal, and then you can start." with **Send it now**. Then, recorded once:
```
docker compose exec -T db psql -U tms -d tms -c "
  select c.id, v.rego, c.result, c.odometer_km, m.duplicates
    from prestart_check c join vehicle v on v.id = c.vehicle_id
    join inbound_message m on m.message_id = c.message_id order by c.id desc limit 2"
```
Expected: `TMS053 | passed | 30500 | 0` and the TMS052 check; and R-… on TMS053 is **Departed** in Plan runs.

**Step 9: Tidy up**

Revoke any Simple WMS key made for Step 3. The records stay (they are history); TMS053's runs and TMS052's planned run can be finished or cancelled from the desktop as usual. Kim's PIN stays `2468`. Remove `$W` when done.

Nothing to commit.

---

## What is left for later steps

- **Live location** (step 5) draws trucks on the map; a grounded truck there and the "What just happened" feed (with "TMS-027 grounded") come with it.
- **`vehicle.due`** and the other events out come with the first subscriber.
- **Adding vehicles and drivers, the service schedule and the checklists from the desktop**: the API has them; the forms are their own piece of work.
- **The office's Leaves now** sends a run without a pre-start (Decision 3): if the business wants every run checked on a phone, that is a one-line change in `runs.depart`, and a decision for them.
