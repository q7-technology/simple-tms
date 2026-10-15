# Simple TMS: the admin screens, and Settings — implementation plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Let a fleet manager (or an admin) add and change vehicles, drivers, pre-start checklists and carriers from the desktop, each in the drawer it belongs to (vehicles, drivers and checklists in **Fleet**, carriers in **Carriers**), with nothing deleted, and let an admin change Simple TMS's runtime settings in a **Settings** drawer instead of editing `.env`.

**Architecture:** The API already has almost every endpoint the screens need; two small gaps are filled (a truck or driver with a run not finished can't be deactivated, `409 on_a_run`; a new PIN unlocks a locked driver). The desktop gets a small form kit (`ui/Field.tsx`: labelled fields with their problem linked and focused) and checks mirrored from the API's rules (`lib/admin.ts`), then the forms: **Fleet** grows tabs (**Vehicles**, **Drivers**, **Pre-start checklists**) and **Carriers** a **Your carriers** section, all shown only with `fleet:write`. Settings: a `setting` table (the value given on screen, changed in place, never deleted) and an append-only `setting_change` record; `config.get_settings()` becomes `.env`'s settings with those in their place, read again every 5 seconds in each process, so the API and the worker both take a change without a restart and no caller changes; `GET`/`PATCH /v1/settings` for `settings:read`/`settings:write` (admins only), checked with `config.py`'s own rules; a **Settings** drawer from the account menu.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2 (pydantic-settings), SQLAlchemy 2, Alembic, pytest on a real PostgreSQL 16; Vite 6, React 19, TypeScript 5, Tailwind 4, react-router 7, Vitest 3 with Testing Library. No new dependencies.

---

## Context

Paths are relative to the Simple TMS repo, `/Users/leightonlauton/Q7 Technology/Software/q7/simple-tms/`. Everything here was checked against `main` today (`0974b10`, "Drop two unused names from the Carriers tests"): each task was built in a scratch copy, in order, against a throwaway PostgreSQL 16, and the counts given at each task are what the suites printed there: API 407 → 442, desktop 123 → 160, driver app 109 → 109; the desktop type-checks and builds. The branch was also started as a throwaway Compose project (its own ports and volumes): migration `0010` applied, and the running worker container took a Simple WMS address and key given in **Settings** within seconds, without a restart, and logged neither (Task 13, Step 3). The manual check's browser script (Task 14) ran end to end against the branch's API and desktop on a throwaway database. The manual check itself has not been run on the local stack.

### 1. The rules that bind this work (`CLAUDE.md`)

- **Rule 1: nothing is deleted.** Vehicles and drivers are deactivated and reactivated; carriers are retired (`deactivate`) and brought back; a checklist line taken out is retired by the API (`retired_at`), and every check keeps its own copy of what it asked. A setting put back to `.env`'s keeps its row (`value` null), and every change to a setting is an append-only `setting_change` row (Decision 10).
- **Rule 2** isn't touched: no confirmation changes.
- **Rule 3: a grounded or overdue truck does not go on a run.** Editing a truck can't get round it: the API already refuses easing an overdue truck's service schedule or changing its type (`409 vehicle_overdue`), and the form shows that refusal as it is.
- **Rule 4: the API is the only door.** Every screen here is an ordinary API client; what a manager does on screen a partner can do with the same calls. The controls are hidden from dispatchers, and the API refuses them anyway (`403`).
- **Git.** Subject line and, if needed, a short body. No `Co-Authored-By`, no tool or generated-by trailers, no session links. `CLAUDE.md` itself doesn't change.

### 2. The code as it is on `main`

- **Vehicles** (`api/tms/api/routes/vehicles.py`): `POST /v1/vehicles` (rego normalised upper case without spaces, `^[A-Z0-9-]{1,12}$`, a rego on file is `422` on `rego`; `type` van/rigid/ute; `max_kg`, `max_m3` decimals above 0 with at most 3 places and 12 digits; `service_every_months` 1–120, `service_every_km` 1–1,000,000), `PATCH` (any of type, limits, intervals; `null` clears an interval; `409 vehicle_overdue` when easing an overdue truck), `deactivate`, `reactivate`. All `fleet:write`. Deactivating checks nothing.
- **Drivers** (`api/tms/api/routes/drivers.py`): `POST /v1/drivers` (code kept lower case, `^[A-Za-z0-9._-]+$` up to 32, a code on file whatever its case is `422` on `code`; name 1–120; PIN `^[0-9]{4,8}$`), `PATCH` (name, pin; a new PIN ends the driver's sessions through the token stamp), `deactivate`, `reactivate`, `unlock`. A new PIN leaves a lock as it is. A deactivated driver's sessions stop at once (`deps.get_principal` checks `active`).
- **Checklists** (`api/tms/api/routes/fleet.py`, `services/checklists.py`): `GET /v1/checklists` (`fleet:read`) gives each type's live lines; `PUT /v1/checklists/{type}` (`fleet:write`) takes the whole list (1–30 lines, labels 1–80 once each whatever the case); a line with the same label and critical flag keeps its id, the rest are retired. A driver's check that doesn't answer the live list is `409 checklist_changed`, which the driver app already shows with **Do the check again** (`apps/driver/src/test/Prestart.test.tsx`).
- **Carriers** (`api/tms/api/routes/carriers.py`): `POST`/`PATCH /v1/carriers` (name 1–64, one per name whatever its case and never `Own fleet`, `422` on `name`; account ≤ 40 or null; `pickup_time` `HH:MM` or null; `adapter` only `manual`), `deactivate`, `reactivate`; `fleet:write` to change, `runs:read` to read.
- **Planning** (`api/tms/services/runs.py`): a deactivated truck or driver can't be given a new run (`422`), a planned run on a deactivated truck takes no stops and can't leave (`409 vehicle_unavailable`); every planning write takes `lock_planning`.
- **Settings** (`api/tms/config.py`): one pydantic-settings `Settings` from the environment (`TMS_*`), `get_settings()` cached with `lru_cache` and read everywhere (`db.py`, `sessions.py`, `worker.py`, `wms_client.py`, `printing.py`, `wms_events.py`, `location.py`, `runs.py`). Tests change it with `monkeypatch.setattr(get_settings(), …)`. The worker reads `worker_poll_seconds` once at start.
- **Scopes** (`api/tms/services/sessions.py`): admin `*`; manager adds `fleet:write` to the dispatcher's `fleet:read`, `consignments:read`, `runs:read`, `runs:write`, `location:read`. Keys (`services/api_keys.py`) can't be given anything outside their `SCOPES` list.
- **The desktop** (`apps/desktop`): **Fleet** (`src/pages/Fleet.tsx`) is the vehicles table with an aside (the check that grounded a truck, **Mark fixed**, and three record forms, each for `can("fleet:write")`); its empty table says "Add them with POST /v1/vehicles". **Carriers** (`src/pages/Carriers.tsx`) loads `/v1/carriers?active=true` for its con note box and says "A fleet manager adds them through the API" when there are none. `AuthContext.can(scope)` reads the scopes from `/v1/auth/me`. The account menu (`src/pages/MapHome.tsx`) has **Sign out**. `api/client.ts` has `get`, `post`, `patch` (no `put`); an `ApiError` carries `status`, `code`, `errors` (the 422 field list) and a message. `ui/ActionSelect.tsx` is a keyboard-safe action menu (not needed here: every action is a button). Tests: `src/test/harness.tsx` (`stubApi`, answering 404 to anything not stubbed; `DISPATCHER`, `MANAGER`; `renderApp`).
- **Users** (dispatchers, managers, admins) have no endpoints beyond sign-in and `tms create-user`; the user didn't ask for a screen (Decision 9).

### 3. The designs

- `Fleet.html` draws **Add a driver** and **Add a vehicle** beside the search, and the sub-heading "Your trucks, who drives them, pre-start checks, and when rego and service fall due." There is no drawn form, no drivers table and no checklist editor.
- `Carriers.html` draws nothing for adding or changing a carrier.
- `design/README.md`: Manrope, white cards on a light page, road green for buttons and the current choice, amber and red text always in their darker ink, red only when a person needs to look.

So the forms follow the drawers' own patterns: the white `adminform` box (like the con note box without the dashed border), `field` inputs 44 px high, `btn` and `btn primary`, `msg r`/`msg g` for what happened (an alert or a status), the aside for one thing's controls, the `tabs` of Consignments. Plain and accessible: every field has a visible label, its hint and its problem are read with it (`aria-describedby`), a field with a problem is `aria-invalid` and gets the focus, every button says what it does to what ("Move Brakes up", "Retire Valley Freight (sample)"), nothing depends on colour alone, and everything is reachable and usable from the keyboard.

### Decisions

Each is for the user to confirm or change; the plan is built on them. The ones marked **(you choose)** are real choices with more than one good answer; the rest follow from the user's decision, the design and the code.

1. **In the drawer each belongs to, for `fleet:write` only (the user's decision).** **Fleet** gets three tabs: **Vehicles** (the table and aside as they are, with **Add a vehicle** beside the search, and in the aside **Change type, limits or service** and **Deactivate**/**Reactivate**), **Drivers** (a table of everyone with their status, and for a fleet manager **Add a driver** and an aside: name, a new PIN, **Unlock**, **Deactivate**/**Reactivate**), and **Pre-start checklists** (a fleet manager's only). **Carriers** gets **Your carriers** at the bottom (a fleet manager's only): every carrier, retired ones too, with **Add a carrier**, **Change** and **Retire**/**Bring back**. Whether the controls show is `can("fleet:write")`, from the scopes `/v1/auth/me` gives: managers and admins. Dispatchers see the Drivers table (who drives, who is locked out) but none of the controls, and no checklists tab; drivers never see the desktop. One small departure from `Fleet.html`: **Add a driver** sits on the Drivers tab rather than beside **Add a vehicle**, because the tabs put each list on its own. **(you choose)** tabs, or one long drawer with the drivers and checklists under the trucks. **Recommendation:** tabs; the vehicles tab stays exactly the screen drivers and dispatchers already know.
2. **API gaps: two small ones filled, the rest already there.** Checked endpoint by endpoint (Context 2): reactivating (vehicles, drivers, carriers), renaming a carrier, changing a truck's limits, type and service intervals, unlocking a driver and replacing a checklist all exist. Filled: deactivating a truck or driver with a run not finished is refused (Decision 3), and a new PIN unlocks (Decision 4). The desktop's client gains `put` (for `PUT /v1/checklists/{type}`). Left as they are: a truck's rego never changes (it is the truck's name in every run, check and record; a re-plated truck is added under its new rego and the old one deactivated), and two managers saving the same checklist at once: the last save wins, each one a complete list the manager saw. **Recommendation:** as above; a "seen" list on the checklist `PUT`, as reordering stops has, is a small later change if two people ever edit checklists at once.
3. **A truck or driver with a run not finished can't be deactivated: `409 on_a_run`. (you choose)** Today the API deactivates anyway, and then a planned run on that truck silently can't take stops or leave (`vehicle_unavailable`), and a driver deactivated while on the road is signed out at once with deliveries still waiting on their phone: stuck until someone reactivates them. So `POST /v1/vehicles/{rego}/deactivate` and `POST /v1/drivers/{code}/deactivate` take the planning lock and refuse while the truck or driver has a run planned (any day, so an old planned run that never left counts too) or on the road, naming every one: "TMS041 is on R-0042 (planned for 16 Oct 2026). Give that run another truck, or cancel it, first."; on the road, "Kim (kim) is on R-0042 (on the road now). Deactivate them once that run is finished." A finished or cancelled run doesn't hold them. The form shows the message as it is. The other way, allowing it and flagging the run, needs a new state on **Plan runs** for something that is always a mistake while the run stands. **Recommendation:** refuse.
4. **PINs: set by a fleet manager, and a new one unlocks. (you choose the unlock)** **Add a driver** takes code, name and a first PIN (shown while typed: the manager tells the driver; "Tell the driver: it isn't shown again."). **Set the PIN** gives a new one; as today it ends the driver's sessions, so the form says "Their phone asks them to sign in again with it; anything it hasn't sent waits until they do." (the phone keeps its queue through a 401). A locked driver has usually forgotten their PIN, so a new PIN now also clears the lock and the count; **Unlock** stays for one who remembers it. **Recommendation:** a new PIN unlocks.
5. **Checklists: one type at a time, saved whole.** **Van**, **Rigid** and **Ute** buttons; each line is a text box ("Line 3"), a **Critical** tick box, **Move … up**, **Move … down** and **Take out …**; **Add a line** adds an empty one with the focus in it; **Save the van checklist** (only when something changed) sends the whole list, in order, through `PUT`; **Put back as it was** forgets the changes. Changes to each type are kept while another is looked at, marked "not saved". The screen says what saving does: "Before each run the driver answers every line. A failed critical line grounds the truck until a fleet manager marks it fixed. A line taken out, or reworded, is retired: checks already done keep it as it was. A driver part way through a check when you save is asked to do it again." That last is the API's `409 checklist_changed`, which the driver app already handles with **Do the check again**: the driver app doesn't change. (A line reworded, or made critical or not, is a new line to the API, since a line's id is its label and flag; that is what makes a check mid-way fail safe.)
6. **Checked inline, as the API checks.** `lib/admin.ts` says what the API would refuse, in the screens' words, and the forms (`noValidate`, so the browser's own bubbles never stand in) show each problem under its field, mark the field invalid and put the focus on the first. The API still checks everything: a `422`'s field errors go beside their fields by name, anything else (`409 vehicle_overdue`, `on_a_run`) shows as an alert in the form or aside. The rules: a rego is read as the API reads it (spaces out, upper case), letters, digits and dashes, at most 12, and not one already on file (a deactivated one says "reactivate it instead"); kg and m³ are decimals above 0, at most 3 places and 9 whole digits, kept as the typed text (commas and spaces taken out), never a float; service intervals are whole numbers in range, blank for none; a driver's code is lower-cased as typed, letters, digits, `.`, `_` and `-`, at most 32, and not taken whatever its case; a PIN is 4 to 8 digits; a carrier's name is 1 to 64, not `Own fleet`, and not on file whatever its case (a retired one says "bring it back instead"); an account is at most 40; the pickup time is a time box (`HH:MM`).
7. **Carriers' words.** The API's deactivate and reactivate are **Retire** and **Bring back** on screen ("Retire Ridgeline Express (sample)? It takes no new bookings. What is on its bay stays, to be collected."). How a carrier is booked is shown, not chosen: "Booked: **manual**, by hand: book with the carrier as you always have, then type or scan the con note here. Carrier connections come later." A new name says "Simple WMS is told the new name from its next collection." The con note box's carrier list is loaded again after each change, and still lists active carriers only.
8. **Asking first.** Deactivating a truck or a driver and retiring a carrier ask first (`window.confirm`, as **Mark collected** does), saying what it means and that nothing is lost. Reactivating and bringing back don't ask.
9. **No users screen.** Dispatchers, fleet managers and admins are still made with `tms create-user`; README's Known limitations says so in place of the two bullets this work makes untrue. A users screen (add, change role, reset a password, deactivate, never leaving no admin) needs its own endpoints, and is a possible later item.
10. **Settings (the user's added requirement): what may be changed on screen.** Every `TMS_*` setting the API reads (`config.Settings`), and the rest of `.env`:

    | Setting | Kind | Why |
    | --- | --- | --- |
    | `TMS_RUN_SHEET_PRINTER`, `TMS_MANIFEST_PRINTER` | on screen | a printer's name: wrong means a job Platen can't place, said on screen |
    | `TMS_PLATEN_URL` | on screen, with its key (11) | where jobs go; empty switches printing off |
    | `TMS_WMS_URL` | on screen, with its key (11) | where ship calls and ship-to reads go |
    | `TMS_WMS_TIMEOUT_SECONDS` | on screen | a wait (above 0, at most 60 s) |
    | `TMS_WORKER_POLL_SECONDS`, `TMS_WORKER_HTTP_TIMEOUT_SECONDS` | on screen | waits (1 to 300 s; above 0, at most 120 s) |
    | `TMS_POSITION_RETENTION_DAYS` | on screen | at least 1; the hint says a lower number deletes within the hour |
    | `TMS_ETA_MINUTES_PER_STOP` | on screen | 1 to 600 |
    | `TMS_WMS_API_KEY`, `TMS_PLATEN_KEY` | secret | replaced on screen, never shown, returned or logged again |
    | `TMS_WMS_WEBHOOK_SECRET` | secret | as above; 16 to 128 characters, as Simple WMS's subscriber secret |
    | `TMS_DATABASE_URL` | file only | the API and the worker need it to reach the database at all |
    | `TMS_SECRET_KEY` | file only | signs every session: a wrong one signs everyone out |
    | `TMS_POD_DIR` | file only | the volume Compose mounts for signatures and photos |
    | `TMS_MAP_TILE_URL`, `TMS_MAP_TILE_ORIGIN`, `TMS_MAP_ATTRIBUTION` | file only | Caddy builds the desktop's Content-Security-Policy from `TMS_MAP_TILE_ORIGIN` when it starts, and the API refuses to start unless it is the tile URL's own origin; the credit is HTML shown as it is |
    | `POSTGRES_*`, `TMS_API_PORT`, `TMS_DOMAIN`, `TMS_HTTP_PORT`, `TMS_HTTPS_PORT` | file only | Compose and Caddy read them when they start; the API never sees them |

    **The map tiles (you choose).** A tile URL changed on screen while Caddy still lets in only the old origin would leave the map blank, with nothing said; letting the screen change the CSP would need Caddy reloaded from the API, a door this design doesn't have. So the three map settings stay in `.env`, shown read-only in **Settings** with why. Another way, later: allow on screen only a tile URL whose origin is the one Caddy already lets in (or a path on this server). **Recommendation:** file only.
11. **An address and its key move together.** A new `TMS_WMS_URL` or `TMS_PLATEN_URL` given on screen needs its key given again in the same save (`422` on the key otherwise: "Type the Platen key again with a new address: a key on file is only sent where it was given for"). Otherwise an admin, who can't read a secret, could point the address at a server of their own and receive it. Putting an address back to `.env`'s needs nothing more: `.env`'s pair is the host's own.
12. **How it is kept. (you choose the secrets' storage)** `.env` gives each setting its default. A value given on screen is in `setting` (one row per setting, changed in place like a truck's limits; put back to `.env`'s, the row stays with `value` null, so nothing is deleted), and every change is a row of `setting_change` (who, when, set or reset, the value), append-only by the same `block_change` trigger as the checks and records (rule 1's spirit for settings: a record of who changed what). A secret's value is never copied into `setting_change`: only its current value is kept, in `setting`, so an old key doesn't linger after it is replaced. It is kept as `.env` keeps it, in plain text, so the database's backups now hold the keys given on screen. **Recommendation:** plain text, as `.env`. Encrypting them with a key made from `TMS_SECRET_KEY` is possible, but rotating that key (the advice after a leak) would then lose every secret given on screen.
13. **How it takes effect.** `config.get_settings()` becomes `.env`'s settings (`file_settings()`, read once) with those given on screen in their place, checked with `Settings` itself, read again from the database at most every 5 seconds in each process (`REFRESH_SECONDS`), and at once in the process that saved them. So the API and the worker both use a change within 5 seconds with no restart, and no caller changes: `wms_client.py`, `printing.py`, `wms_events.py`, `location.py` and `runs.py` keep calling `get_settings()`. The worker reads its poll wait each time it waits. The database's address and the session key come from `file_settings()` only. If the database can't be read, the settings last read stay in force; if those given on screen no longer fit `.env` (it changed under them), `.env`'s are used and the log says so, never with a value.
14. **Checked with `config.py`'s own rules, which grow a little.** A save is checked by building `Settings` from `.env`'s values with the changes in place, so the screen and `.env` can never disagree about what is allowed; all the changes in a save are kept, or none. `config.py` gains the rules it lacked for settings that are now changeable: `TMS_WMS_URL` and `TMS_PLATEN_URL` must be `http://` or `https://` addresses (or empty), `TMS_WMS_TIMEOUT_SECONDS` above 0 and at most 60, `TMS_WORKER_POLL_SECONDS` 1 to 300 and `TMS_WORKER_HTTP_TIMEOUT_SECONDS` above 0 and at most 120. These apply to `.env` too, so an install with an odd value there now refuses to start and says which; the defaults and every value in this repo's docs pass. A secret given on screen can't be blank (use `.env`'s instead), and the webhook secret is 16 to 128 characters (shorter in `.env` still counts as not set, as now).
15. **Where Settings lives: a drawer from the account menu, for admins only. (you choose)** **Settings** in the account menu (top right, beside **Sign out**) for `settings:write`, at `/settings`, a drawer like the others: a form per group (**Simple WMS**, **Printing through Platen**, **Live location and ETAs**, **The worker**), each field saying where its value comes from ("From .env (TMS_RUN_SHEET_PRINTER)." or "Set here by leighton, Today 2:57 pm. .env says dispatch.") with **Use .env's** beside one given on screen, secrets as password boxes ("Set: type a new one to replace it"), then **In .env only** (read-only, with why) and **Recent changes**. New scopes `settings:read` and `settings:write` come only with an admin's `*`; no API key can be given them, and a manager's `fleet:write` doesn't reach them. **Recommendation:** as above; it isn't a job of the depot's day, so it stays out of the drawer bar.

**For you to decide, in short:** tabs in Fleet (1, recommended); refusing to deactivate a truck or driver with a run not finished (3, recommended: refuse); a new PIN unlocks (4, recommended: yes); the map tiles in `.env` only (10, recommended); secrets given on screen kept in plain text like `.env` (12, recommended); Settings in the account menu, admins only (15, recommended). The stricter `config.py` rules (14) could stop an install with an odd `.env` value from starting: say if any of yours have one.

### Ports and addresses, locally

Simple TMS API `127.0.0.1:8022`, Caddy `https://localhost:8543` (desktop at `/`, driver app at `/driver/`), database `127.0.0.1:5434` on migration `0009` before this work. Users `leighton` (admin, `a long enough password`) and `dee` (dispatcher, `dispatch password`). The local data is to be reset to fresh demo data; nothing here depends on what is in it. Chrome needs `--ignore-certificate-errors` for Caddy's local certificate.

### Working beside the "came back" plan

Another plan is being written at the same time for a "came back" flow in Simple TMS's ship-call code. This plan doesn't touch `api/tms/services/wms_client.py`, the worker's ship calls (`_ship_one`, `send_ship_calls`) or the fail paths in `api/tms/services/deliveries.py`. Where the two could meet:

- `api/tms/worker.py`: Task 9 changes only `main()` (the poll wait is read each time through `poll_seconds()`), not the ship-call code.
- `api/tms/config.py`: Task 9 turns `get_settings()` into `.env`'s settings with the on-screen ones in their place. `wms_client.py` keeps calling `get_settings()` as it does and gets the effective Simple WMS address, key and wait with no change to its text.
- Migration number: this plan adds `0010_runtime_settings.py` (`down_revision = "0009"`). If the other plan lands a `0010` first, rename this one to the next number and set its `down_revision` to that plan's.
- `README.md` Known limitations: Task 12 removes two bullets and adds one near the "What came back is still shipped in Simple WMS" bullet, which it leaves as it is.

---

## Before you start

```
cd "/Users/leightonlauton/Q7 Technology/Software/q7/simple-tms"
git switch main && git pull --ff-only
git switch -c admin-screens
docker compose up -d db                      # the tests' PostgreSQL on 127.0.0.1:5434
cd api && caffeinate -i .venv/bin/pytest -q  # expect: 407 passed
cd ../apps/desktop && npm test               # expect: 123 passed
cd ../driver && npm test                     # expect: 109 passed
```

Four parts: **A**, the API for the admin screens (Tasks 1–2); **B**, the desktop's admin screens (Tasks 3–8); **C**, Settings (Tasks 9–11); **D**, the docs and the checks, ending with the manual check on the local stack (Tasks 12–14). Part B stubs the API in its tests, so it doesn't wait on Part A except for the manual check. `pytest` runs from `api/` (with `caffeinate -i` in front for the whole suite: this Mac sleeps on battery and the suite's 15-minute tokens then expire mid-run), `npm` and `npx vitest` from `apps/desktop/`, and `git` from the repo root. Code is given whole for a new file, and as a unified diff for a changed one, against the file as the tasks before it leave it (`main` for its first change); apply it by hand, or save it and `git apply`. Commit after each task as shown, on the branch.

## Part A — the API for the admin screens

### Task 1: A truck or driver with a run not finished isn't deactivated

Decision 3. `refuse_if_on_a_run` sits with the planning code it guards, and both deactivations take the planning lock first, so a run being made for the truck at that moment is either seen (and the deactivation refused) or made after it (and refused itself, as now: "is deactivated, so it can't take a run").

**Files:**
- Modify: `api/tms/services/runs.py`, `api/tms/api/routes/vehicles.py`, `api/tms/api/routes/drivers.py`, `docs/api.md`
- Test: `api/tests/test_deactivate_on_a_run.py` (new), `api/tests/test_planning.py`

**Step 1: Write the failing tests**

Create `api/tests/test_deactivate_on_a_run.py`:
```python
"""A truck or a driver with a run not finished isn't deactivated: the run
couldn't leave, and a driver on the road would be signed out with
deliveries still to send. Done, or cancelled, it may go."""
import uuid

from deliveries_samples import delivered, on_the_road, planned_run
from runs_samples import driver, new_run, vehicle


def _refused(r, detail):
    assert r.status_code == 409, r.text
    assert r.json() == {"code": "on_a_run", "detail": detail}


def test_a_truck_on_a_planned_run_stays_until_the_run_has_another(client, db, dispatcher_headers,
                                                                  manager_headers):
    run, _ = planned_run(client, db, dispatcher_headers)
    r = client.post("/v1/vehicles/tms041/deactivate", headers=manager_headers)
    _refused(r, "TMS041 is on R-0001 (planned for 14 Oct 2026). "
                "Give that run another truck, or cancel it, first.")
    assert client.get("/v1/vehicles/TMS041", headers=manager_headers).json()["active"] is True
    # given another truck, the first may go
    vehicle(db, rego="TMS018")
    r = client.patch(f"/v1/runs/{run['id']}", headers=dispatcher_headers, json={"vehicle": "TMS018"})
    assert r.status_code == 200, r.text
    r = client.post("/v1/vehicles/TMS041/deactivate", headers=manager_headers)
    assert r.status_code == 200 and r.json()["active"] is False


def test_a_driver_on_the_road_stays_until_the_run_is_finished(client, db, dispatcher_headers,
                                                             manager_headers):
    run, cons, kim = on_the_road(client, db, dispatcher_headers, n=1)
    r = client.post("/v1/drivers/KIM/deactivate", headers=manager_headers)
    _refused(r, "Kim (kim) is on R-0001 (on the road now). "
                "Deactivate them once that run is finished.")
    # their phone still works
    assert delivered(client, kim, run["id"], cons[0]).status_code == 200
    r = client.post(f"/v1/runs/{run['id']}/finish", headers=kim,
                    json={"message_id": str(uuid.uuid4())})
    assert r.status_code == 200, r.text
    r = client.post("/v1/drivers/kim/deactivate", headers=manager_headers)
    assert r.status_code == 200 and r.json()["active"] is False


def test_every_run_not_finished_is_named(client, db, dispatcher_headers, manager_headers):
    from datetime import date

    vehicle(db)
    driver(db)
    new_run(client, dispatcher_headers, day=date(2026, 10, 20))
    vehicle(db, rego="TMS018")
    new_run(client, dispatcher_headers, rego="TMS018", day=date(2026, 10, 21))
    r = client.post("/v1/drivers/kim/deactivate", headers=manager_headers)
    _refused(r, "Kim (kim) is on R-0001 (planned for 20 Oct 2026), "
                "R-0002 (planned for 21 Oct 2026). "
                "Give those runs another driver, or cancel them, first.")


def test_a_cancelled_run_doesnt_hold_its_truck_or_driver(client, db, dispatcher_headers,
                                                         manager_headers):
    vehicle(db)
    driver(db)
    run = new_run(client, dispatcher_headers)
    assert client.post(f"/v1/runs/{run['id']}/cancel", headers=dispatcher_headers).status_code == 200
    assert client.post("/v1/vehicles/TMS041/deactivate", headers=manager_headers).status_code == 200
    assert client.post("/v1/drivers/kim/deactivate", headers=manager_headers).status_code == 200


def test_deactivating_takes_the_planning_lock(client, db, engine, dispatcher_headers,
                                              manager_headers):
    """A run being made for the truck at that moment is either seen (and the
    deactivation refused) or made after it (and refused itself)."""
    from sqlalchemy import text

    from conftest import blocked_on_a_lock, fresh_client, in_background
    from tms.services.runs import PLANNING_LOCK

    vehicle(db)
    with engine.connect() as holder:
        holder.execute(text("select pg_advisory_xact_lock(:k)"), {"k": PLANNING_LOCK})
        future = in_background(lambda: fresh_client().post(
            "/v1/vehicles/TMS041/deactivate", headers=manager_headers))
        assert blocked_on_a_lock(engine, future)
        holder.rollback()
    assert future.result(timeout=10).status_code == 200
```

`test_planning.py` deactivated the truck of a planned run through the API, which is now refused; the guard it tests (a run on a deactivated truck takes no stops) still matters for a truck deactivated before this change, so the test makes it so in the database.

Change `api/tests/test_planning.py`:
```diff
--- a/api/tests/test_planning.py
+++ b/api/tests/test_planning.py
@@ -4,7 +4,7 @@ from sqlalchemy import func, select
 
 from conftest import all_at_once, fresh_client
 from runs_samples import assign, consignment, driver, new_run, order, vehicle
-from tms.models import Stop
+from tms.models import Stop, Vehicle
 
 
 def _depot(client, db, headers, n=3):
@@ -159,8 +159,11 @@ def test_an_unknown_consignment_is_named(client, db, dispatcher_headers):
 
 
 def test_a_run_on_a_deactivated_truck_takes_no_more_stops(client, db, manager_headers):
+    """The API won't deactivate a truck with a run planned (409 on_a_run), but
+    one deactivated before that rule, or in the database, is still refused."""
     run, _, (a, *_) = _depot(client, db, manager_headers)
-    client.post("/v1/vehicles/TMS041/deactivate", headers=manager_headers)
+    db.execute(select(Vehicle).where(Vehicle.rego == "TMS041")).scalar_one().active = False
+    db.commit()
     r = assign(client, manager_headers, run["id"], a.id)
     assert r.status_code == 409 and r.json()["code"] == "vehicle_unavailable"
 
```

**Step 2: Run them to see them fail**

Run: `.venv/bin/pytest tests/test_deactivate_on_a_run.py tests/test_planning.py -q`
Expected: `4 failed, 16 passed`: each deactivation answers `200` (`AssertionError: {"id":1,"code":"kim",…,"active":false,…}`), and `test_deactivating_takes_the_planning_lock` finds nothing waiting on a lock. (`test_a_cancelled_run_doesnt_hold_its_truck_or_driver` already passes.)

**Step 3: Implement**

Change `api/tms/services/runs.py`:
```diff
--- a/api/tms/services/runs.py
+++ b/api/tms/services/runs.py
@@ -89,6 +89,36 @@ def _truck_free(db: Session, vehicle: Vehicle, day: date, other_than: int | None
         raise FieldError("vehicle", f"{vehicle.rego} already has {taken.number} on {day}")
 
 
+# a run its truck and driver are still needed for: not left yet, or on the road
+LIVE = ("planned", "departed")
+
+
+def refuse_if_on_a_run(db: Session, *, vehicle: Vehicle | None = None,
+                       driver: Driver | None = None) -> None:
+    """A truck or a driver with a run not finished isn't deactivated: a
+    planned run could no longer leave, and a driver on the road would be
+    signed out with deliveries still to send. Every such run is named, so
+    the office knows what to change first. The caller holds the planning lock."""
+    q = select(Run).where(Run.status.in_(LIVE))
+    q = q.where(Run.vehicle_id == vehicle.id) if vehicle else q.where(Run.driver_id == driver.id)
+    runs = db.execute(q.order_by(Run.day, Run.id)).scalars().all()
+    if not runs:
+        return
+    who = vehicle.rego if vehicle else f"{driver.name} ({driver.code})"
+    where = ", ".join(f"{r.number} (" + ("on the road now" if r.status == "departed"
+                                         else f"planned for {fleet.long_date(r.day)}") + ")"
+                      for r in runs)
+    one = len(runs) == 1
+    that = "that run" if one else "those runs"
+    if any(r.status == "departed" for r in runs):
+        then = (f"Deactivate {'it' if vehicle else 'them'} once {that} "
+                f"{'is' if one else 'are'} finished.")
+    else:
+        then = (f"Give {that} another {'truck' if vehicle else 'driver'}, "
+                f"or cancel {'it' if one else 'them'}, first.")
+    raise Conflict("on_a_run", f"{who} is on {where}. {then}")
+
+
 def create(db: Session, *, day: date, rego: str, driver_code: str, by: str) -> Run:
     lock_planning(db)
     vehicle, driver = _vehicle(db, rego), _driver(db, driver_code)
```

Change `api/tms/api/routes/vehicles.py`:
```diff
--- a/api/tms/api/routes/vehicles.py
+++ b/api/tms/api/routes/vehicles.py
@@ -17,7 +17,7 @@ from tms.api.errors import Conflict, FieldError, NotFound, violated_constraint
 from tms.api.schemas import Dec, Page
 from tms.models import Vehicle
 from tms.services import fleet
-from tms.services.runs import lock_planning
+from tms.services.runs import lock_planning, refuse_if_on_a_run
 
 router = APIRouter(tags=["vehicles"])
 
@@ -231,8 +231,11 @@ def update_vehicle(rego: str, body: VehiclePatch, db: DB, who: Principal = requi
 
 @router.post("/vehicles/{rego}/deactivate", response_model=VehicleOut)
 def deactivate_vehicle(rego: str, db: DB, who: Principal = require("fleet:write")):
-    """Off the road for good, or sold. Its history stays."""
+    """Off the road for good, or sold. Its history stays. Not while it has a
+    run planned or on the road (409 on_a_run)."""
+    lock_planning(db)
     v = _get(db, rego)
+    refuse_if_on_a_run(db, vehicle=v)
     v.active = False
     db.commit()
     return vehicle_out(db, v)
```

Change `api/tms/api/routes/drivers.py`:
```diff
--- a/api/tms/api/routes/drivers.py
+++ b/api/tms/api/routes/drivers.py
@@ -14,6 +14,7 @@ from tms.api.errors import FieldError, NotFound, violated_constraint
 from tms.api.schemas import Page
 from tms.models import Driver
 from tms.services.access import hash_password
+from tms.services.runs import lock_planning, refuse_if_on_a_run
 
 router = APIRouter(tags=["drivers"])
 
@@ -106,8 +107,11 @@ def update_driver(code: str, body: DriverPatch, db: DB, who: Principal = require
 
 @router.post("/drivers/{code}/deactivate", response_model=DriverOut)
 def deactivate_driver(code: str, db: DB, who: Principal = require("fleet:write")):
-    """Deactivated, never deleted: their runs and deliveries stay theirs."""
+    """Deactivated, never deleted: their runs and deliveries stay theirs. Not
+    while they have a run planned or on the road (409 on_a_run)."""
+    lock_planning(db)
     d = _get(db, code)
+    refuse_if_on_a_run(db, driver=d)
     d.active = False
     db.commit()
     return driver_out(d)
```

Change `docs/api.md`:
```diff
--- a/docs/api.md
+++ b/docs/api.md
@@ -128,7 +128,11 @@ password, at least 12 characters, is asked for, or given with `--password`).
 - `PATCH /v1/vehicles/{rego}` — any of `type`, `max_kg`, `max_m3`,
   `service_every_months`, `service_every_km`. A field left out is kept;
   `null` clears a service interval. The rego does not change.
-- `POST /v1/vehicles/{rego}/deactivate`, `/reactivate`
+- `POST /v1/vehicles/{rego}/deactivate`, `/reactivate` — a deactivated truck
+  takes no run. One with a run planned or on the road is `409 on_a_run`,
+  naming each run ("TMS041 is on R-0042 (planned for 16 Oct 2026). Give that
+  run another truck, or cancel it, first."; on the road: "Deactivate it once
+  that run is finished."). A finished or cancelled run doesn't hold it.
 
 `POST` and `PATCH` also take `service_every_months` (1 to 120) and
 `service_every_km` (1 to 1,000,000): how often it is serviced, whichever
@@ -257,7 +261,10 @@ only. It comes with the first system that subscribes.
 - `GET /v1/drivers/{code}`
 - `PATCH /v1/drivers/{code}` — `name` and/or a new `pin`. A new PIN ends the
   driver's sessions.
-- `POST /v1/drivers/{code}/deactivate`, `/reactivate`
+- `POST /v1/drivers/{code}/deactivate`, `/reactivate` — a deactivated driver
+  can't sign in, and their sessions end. One with a run planned or on the
+  road is `409 on_a_run`, as for a truck ("Kim (kim) is on R-0042 (on the
+  road now). Deactivate them once that run is finished.").
 - `POST /v1/drivers/{code}/unlock` — clears a PIN lock and the count.
 
 Each driver: `{ "id", "code", "name", "active", "locked", "failed_attempts", "created_at" }`.
```

**Step 4: Run them, then the suite**

Run: `.venv/bin/pytest tests/test_deactivate_on_a_run.py tests/test_planning.py -q`
Expected: `20 passed`.
Run: `caffeinate -i .venv/bin/pytest -q`
Expected: `412 passed`.

**Step 5: Commit**

```bash
git add api/tms/services/runs.py api/tms/api/routes/vehicles.py api/tms/api/routes/drivers.py docs/api.md \
        api/tests/test_deactivate_on_a_run.py api/tests/test_planning.py
git commit -m "Keep a truck or driver with a run not finished from being deactivated"
```

### Task 2: A new PIN unlocks a locked driver

Decision 4.

**Files:**
- Modify: `api/tms/api/routes/drivers.py`, `docs/api.md`
- Test: `api/tests/test_drivers.py`

**Step 1: Write the failing tests**

Change `api/tests/test_drivers.py`:
```diff
--- a/api/tests/test_drivers.py
+++ b/api/tests/test_drivers.py
@@ -135,6 +135,29 @@ def test_a_new_pin_ends_the_driver_sessions_signed_in_with_the_old_one(client, m
     assert client.get("/v1/auth/me", headers=bearer(new)).status_code == 200
 
 
+def test_a_new_pin_unlocks_a_locked_driver(client, manager_headers):
+    """Locked out, a driver has usually forgotten the PIN: the new one given
+    by a manager works straight away, and the count starts again."""
+    make_driver(client, manager_headers)
+    for _ in range(5):
+        client.post("/v1/auth/driver-login", json={"code": "drv-01", "pin": "0000"})
+    assert client.get("/v1/drivers/drv-01", headers=manager_headers).json()["locked"] is True
+
+    r = client.patch("/v1/drivers/drv-01", headers=manager_headers, json={"pin": "8642"})
+    assert r.status_code == 200
+    assert r.json()["locked"] is False and r.json()["failed_attempts"] == 0
+    r = client.post("/v1/auth/driver-login", json={"code": "drv-01", "pin": "8642"})
+    assert r.status_code == 200
+
+
+def test_a_new_name_leaves_a_lock_as_it_is(client, manager_headers):
+    make_driver(client, manager_headers)
+    for _ in range(5):
+        client.post("/v1/auth/driver-login", json={"code": "drv-01", "pin": "0000"})
+    r = client.patch("/v1/drivers/drv-01", headers=manager_headers, json={"name": "Alex Tran"})
+    assert r.json()["locked"] is True and r.json()["failed_attempts"] == 5
+
+
 def test_a_new_name_keeps_the_driver_signed_in(client, manager_headers):
     make_driver(client, manager_headers)
     token = client.post("/v1/auth/driver-login", json={"code": "drv-01", "pin": "2468"}).json()["token"]
```

**Step 2: Run them to see them fail**

Run: `.venv/bin/pytest tests/test_drivers.py -q`
Expected: `1 failed, 18 passed`: `test_a_new_pin_unlocks_a_locked_driver`, at `assert r.json()["locked"] is False and r.json()["failed_attempts"] == 0`.

**Step 3: Implement**

Change `api/tms/api/routes/drivers.py`:
```diff
--- a/api/tms/api/routes/drivers.py
+++ b/api/tms/api/routes/drivers.py
@@ -96,11 +96,15 @@ def get_driver(code: str, db: DB, who: Principal = require("fleet:read")):
 
 @router.patch("/drivers/{code}", response_model=DriverOut)
 def update_driver(code: str, body: DriverPatch, db: DB, who: Principal = require("fleet:write")):
+    """A new PIN ends the driver's sessions, and unlocks them: the lock is
+    against guessing the old one."""
     d = _get(db, code)
     if body.name is not None:
         d.name = body.name
     if body.pin is not None:
         d.pin_hash = hash_password(body.pin)
+        d.locked = False
+        d.failed_attempts = 0
     db.commit()
     return driver_out(d)
 
```

Change `docs/api.md`:
```diff
--- a/docs/api.md
+++ b/docs/api.md
@@ -260,7 +260,8 @@ only. It comes with the first system that subscribes.
   of them.
 - `GET /v1/drivers/{code}`
 - `PATCH /v1/drivers/{code}` — `name` and/or a new `pin`. A new PIN ends the
-  driver's sessions.
+  driver's sessions (a phone on the road asks them to sign in again, and
+  keeps what it hadn't sent), and unlocks a locked driver.
 - `POST /v1/drivers/{code}/deactivate`, `/reactivate` — a deactivated driver
   can't sign in, and their sessions end. One with a run planned or on the
   road is `409 on_a_run`, as for a truck ("Kim (kim) is on R-0042 (on the
```

**Step 4: Run them, then the suite**

Run: `.venv/bin/pytest tests/test_drivers.py -q`
Expected: `19 passed`.
Run: `caffeinate -i .venv/bin/pytest -q`
Expected: `414 passed`.

**Step 5: Commit**

```bash
git add api/tms/api/routes/drivers.py docs/api.md api/tests/test_drivers.py
git commit -m "Unlock a driver when a manager gives them a new PIN"
```

## Part B — the desktop's admin screens (`apps/desktop`)

### Task 3: The API's rules, said before a form sends; and `put`

Decision 6. Pure functions, tested on their own; the forms in Tasks 4 to 8 use them. `said(e)` is the one way the new screens word a failed request (the pages before them each have their own copy, left as they are).

**Files:**
- Create: `apps/desktop/src/lib/admin.ts`
- Modify: `apps/desktop/src/api/client.ts`, `apps/desktop/src/api/types.ts`
- Test: `apps/desktop/src/test/admin.test.ts` (new), `apps/desktop/src/test/client.test.ts`

**Step 1: Write the failing tests**

Create `apps/desktop/src/test/admin.test.ts`:
```ts
import { ApiError } from "../api/client";
import type { Carrier, Driver, Vehicle } from "../api/types";
import {
  amount, amountProblem, apiProblems, carrierNameProblem, checklistProblems, codeProblem, found,
  interval, intervalProblem, nameProblem, normalCode, normalRego, pinProblem, regoProblem,
} from "../lib/admin";

const van = (rego: string, active = true) => ({ id: 1, rego, type: "van", max_kg: "1200", max_m3: "8.5", active }) as Vehicle;
const kim: Driver = { id: 1, code: "kim", name: "Kim", active: true };
const carrier = (id: number, name: string, active = true): Carrier =>
  ({ id, name, account: null, pickup_time: null, adapter: "manual", active, created_at: "" });

describe("the admin forms' checks", () => {
  it("reads a rego as the API does, and says when it is taken", () => {
    expect(normalRego(" abc 123 ")).toBe("ABC123");
    expect(regoProblem("ABC123", [])).toBeNull();
    expect(regoProblem("", [])).toBe("Type the rego.");
    expect(regoProblem("ABC_123", [])).toBe("A rego is letters, digits and dashes, at most 12.");
    expect(regoProblem("A".repeat(13), [])).toBe("A rego is letters, digits and dashes, at most 12.");
    expect(regoProblem("TMS041", [van("TMS041")])).toBe("TMS041 is already on file.");
    expect(regoProblem("TMS041", [van("TMS041", false)])).toBe("TMS041 is already on file, deactivated: reactivate it instead.");
  });

  it("takes a load limit as a decimal, never a float", () => {
    expect(amount(" 1,200 ")).toBe("1200");
    expect(amount("8.50")).toBe("8.5");
    expect(amount("12.000")).toBe("12");
    expect(amountProblem("1200", "kg")).toBeNull();
    expect(amountProblem("0.125", "m³")).toBeNull();
    expect(amountProblem("", "kg")).toBe("Give the most it carries, in kg.");
    expect(amountProblem("about 2t", "kg")).toBe("A number, like 1200.");
    expect(amountProblem("-3", "m³")).toBe("A number, like 8.5.");
    expect(amountProblem("8.1234", "m³")).toBe("At most 3 decimal places.");
    expect(amountProblem("0.000", "kg")).toBe("More than 0.");
    expect(amountProblem("1234567890", "kg")).toBe("That is more than any truck carries.");
  });

  it("leaves a service interval blank for none, else a whole number in range", () => {
    expect(interval("")).toBeNull();
    expect(interval("15,000")).toBe(15000);
    expect(intervalProblem("", 120, "months")).toBeNull();
    expect(intervalProblem("6", 120, "months")).toBeNull();
    expect(intervalProblem("0", 120, "months")).toBe("From 1 to 120 months, or leave it blank.");
    expect(intervalProblem("1000001", 1_000_000, "km")).toBe("From 1 to 1,000,000 km, or leave it blank.");
    expect(intervalProblem("6.5", 120, "months")).toBe("Whole months, or leave it blank.");
  });

  it("keeps a driver's code lower case, once, and a PIN 4 to 8 digits", () => {
    expect(normalCode(" KIM-07 ")).toBe("kim-07");
    expect(codeProblem("kim-07", [kim])).toBeNull();
    expect(codeProblem("", [])).toBe("Type the code the driver signs in with.");
    expect(codeProblem("kim 07", [])).toBe("Letters, digits, dots, dashes and underscores, at most 32.");
    expect(codeProblem("kim", [kim])).toBe("kim is already Kim's.");
    expect(codeProblem("kim", [{ ...kim, active: false }])).toBe("kim is already Kim's, deactivated: reactivate them instead.");
    expect(pinProblem("2468")).toBeNull();
    expect(pinProblem("12345678")).toBeNull();
    for (const bad of ["246", "123456789", "24 68", "abcd"]) expect(pinProblem(bad)).toBe("A PIN is 4 to 8 digits.");
    expect(nameProblem("  ", 120)).toBe("Type the name.");
    expect(nameProblem("x".repeat(121), 120)).toBe("At most 120 characters.");
  });

  it("holds a carrier's name one whatever its case, and never Own fleet", () => {
    const all = [carrier(1, "Ridgeline Express (sample)"), carrier(2, "Plains Parcels (sample)", false)];
    expect(carrierNameProblem("Valley Freight (sample)", all)).toBeNull();
    expect(carrierNameProblem(" ", all)).toBe("Type the carrier's name.");
    expect(carrierNameProblem("own FLEET", all)).toBe("Own fleet is what Simple WMS is told for your own trucks: give the carrier its own name.");
    expect(carrierNameProblem("RIDGELINE EXPRESS (SAMPLE)", all)).toBe("Ridgeline Express (sample) is already on file.");
    expect(carrierNameProblem("plains parcels (sample)", all)).toBe("Plains Parcels (sample) is already on file, retired: bring it back instead.");
    // renaming one to its own name in another case is fine
    expect(carrierNameProblem("RIDGELINE EXPRESS (SAMPLE)", all, 1)).toBeNull();
    expect(carrierNameProblem("x".repeat(65), all)).toBe("At most 64 characters.");
  });

  it("finds a checklist's blank and repeated lines, and a list too short or long", () => {
    const line = (label: string) => ({ label, critical: false });
    expect(checklistProblems([line("Tyres"), line("Lights")])).toEqual({ lines: [null, null], list: null });
    expect(checklistProblems([line("Tyres"), line(" "), line("tyres ")])).toEqual({
      lines: [null, "Type what to check, or take the line out.", "Line 1 says this already."], list: null });
    expect(checklistProblems([]).list).toBe("A checklist needs at least one line.");
    expect(checklistProblems(Array.from({ length: 31 }, (_, i) => line(`Item ${i}`))).list).toBe("At most 30 lines.");
    expect(checklistProblems([line("x".repeat(81))]).lines).toEqual(["At most 80 characters."]);
  });

  it("reads the API's field errors, and only those", () => {
    const e = new ApiError(422, { errors: [{ field: "rego", message: "vehicle TMS041 already exists" }] });
    expect(apiProblems(e)).toEqual({ rego: "vehicle TMS041 already exists" });
    expect(apiProblems(new ApiError(409, { code: "on_a_run", detail: "TMS041 is on R-0001" }))).toBeNull();
    expect(apiProblems(new Error("x"))).toBeNull();
    expect(found({ rego: null, max_kg: "More than 0." })).toEqual({ max_kg: "More than 0." });
  });
});
```

Change `apps/desktop/src/test/client.test.ts`:
```diff
--- a/apps/desktop/src/test/client.test.ts
+++ b/apps/desktop/src/test/client.test.ts
@@ -35,6 +35,15 @@ describe("ApiClient", () => {
 
   const refreshedWith = (i: number) => JSON.parse(calls[i].init.body as string).refresh_token;
 
+  it("puts a whole thing, as a checklist is sent", async () => {
+    client.setSession({ token: "tms_s.abc", refresh_token: "tms_r.xyz", expires_in: 900 });
+    answers.push(json(200, [{ id: 19, label: "Tail lift", critical: true }]));
+    await client.put("/v1/checklists/van", { items: [{ label: "Tail lift", critical: true }] });
+    expect(calls[0].url).toBe("/v1/checklists/van");
+    expect(calls[0].init.method).toBe("PUT");
+    expect(JSON.parse(calls[0].init.body as string)).toEqual({ items: [{ label: "Tail lift", critical: true }] });
+  });
+
   it("sends the bearer token and reads JSON", async () => {
     client.setSession({ token: "tms_s.abc", refresh_token: "tms_r.xyz", expires_in: 900 });
     answers.push(json(200, { items: [], total: 0 }));
```

**Step 2: Run them to see them fail**

Run: `npx vitest run src/test/admin.test.ts src/test/client.test.ts`
Expected: `admin.test.ts` fails to load (`Failed to resolve import "../lib/admin"`), and `Tests  1 failed | 13 passed (14)`: `TypeError: client.put is not a function`.

**Step 3: Implement**

Change `apps/desktop/src/api/client.ts`:
```diff
--- a/apps/desktop/src/api/client.ts
+++ b/apps/desktop/src/api/client.ts
@@ -195,6 +195,7 @@ export class ApiClient {
   }
   post<T>(path: string, body?: unknown) { return this.request<T>("POST", path, body ?? {}); }
   patch<T>(path: string, body: unknown) { return this.request<T>("PATCH", path, body); }
+  put<T>(path: string, body: unknown) { return this.request<T>("PUT", path, body); }
 }
 
 export const api = new ApiClient();
```

Change `apps/desktop/src/api/types.ts` (the driver's lock, which the API already sends, and the checklist's shapes):
```diff
--- a/apps/desktop/src/api/types.ts
+++ b/apps/desktop/src/api/types.ts
@@ -107,8 +107,20 @@ export interface Driver {
   code: string;
   name: string;
   active: boolean;
+  /** five wrong PINs lock a driver until a manager unlocks them or gives a new PIN */
+  locked?: boolean;
+  failed_attempts?: number;
+  created_at?: string;
 }
 
+/** A line of a pre-start checklist as it is sent (PUT /v1/checklists/{type}). */
+export interface ChecklistLine { label: string; critical: boolean }
+
+/** A live line (GET /v1/checklists): a line kept as it was keeps its id. */
+export interface ChecklistItem extends ChecklistLine { id: number }
+
+export type Checklists = Record<"van" | "rigid" | "ute", ChecklistItem[]>;
+
 /** A consignment as the plan shows it, on a run or in the tray. */
 export interface Card {
   id: number;
```

Create `apps/desktop/src/lib/admin.ts`:
```ts
/** What the admin forms (vehicles, drivers, checklists, carriers) check
 * before they send, with the API's own rules said in the screens' words.
 * The API checks everything again: these only save a trip and say what is
 * wrong beside the field. */
import { ApiError, Unreachable } from "../api/client";
import type { Carrier, ChecklistLine, Driver, Vehicle } from "../api/types";

/** A field's name, as the API names it, and what is wrong with it. */
export type Problems = Record<string, string>;

export const VEHICLE_TYPES = ["van", "rigid", "ute"] as const;
export type VehicleType = (typeof VEHICLE_TYPES)[number];
export const typeName = (t: string) => t[0].toUpperCase() + t.slice(1);

/** "1,200" and " 8.5 " as typed: spaces and thousands commas go. */
function digits(raw: string): string {
  return raw.replace(/[\s,]/g, "");
}

/** As the API stores it: "abc 123" and "ABC123" are the same plate. */
export function normalRego(raw: string): string {
  return raw.replace(/\s+/g, "").toUpperCase();
}

export function regoProblem(rego: string, vehicles: Vehicle[]): string | null {
  if (!rego) return "Type the rego.";
  if (!/^[A-Z0-9-]{1,12}$/.test(rego)) return "A rego is letters, digits and dashes, at most 12.";
  const there = vehicles.find((v) => v.rego === rego);
  if (there) return there.active ? `${rego} is already on file.` : `${rego} is already on file, deactivated: reactivate it instead.`;
  return null;
}

/** The most a truck carries, in kg or m³: above 0, at most 3 decimal places
 * and 9 whole digits (the API's 12 digits in all). Kept as the typed text,
 * never a float. */
export function amount(raw: string): string {
  const t = digits(raw);
  return t.includes(".") ? t.replace(/0+$/, "").replace(/\.$/, "") : t;
}

export function amountProblem(raw: string, unit: "kg" | "m³"): string | null {
  const t = amount(raw);
  if (!t) return `Give the most it carries, in ${unit}.`;
  const m = /^(\d+)(?:\.(\d+))?$/.exec(t);
  if (!m) return `A number, like ${unit === "kg" ? "1200" : "8.5"}.`;
  if ((m[2] ?? "").length > 3) return "At most 3 decimal places.";
  if (m[1].replace(/^0+/, "").length > 9) return "That is more than any truck carries.";
  if (!/[1-9]/.test(t)) return "More than 0.";
  return null;
}

/** A service interval: blank is none (never due that way), else a whole
 * number from 1 to the API's most. */
export function interval(raw: string): number | null {
  const t = digits(raw);
  return t ? Number(t) : null;
}

export function intervalProblem(raw: string, most: number, unit: string): string | null {
  const t = digits(raw);
  if (!t) return null;
  if (!/^\d+$/.test(t)) return `Whole ${unit}, or leave it blank.`;
  const n = Number(t);
  if (n < 1 || n > most) return `From 1 to ${most.toLocaleString("en-AU")} ${unit}, or leave it blank.`;
  return null;
}

/** As the API keeps it: lower case, so KIM and kim are one driver. */
export function normalCode(raw: string): string {
  return raw.trim().toLowerCase();
}

export function codeProblem(code: string, drivers: Driver[]): string | null {
  if (!code) return "Type the code the driver signs in with.";
  if (!/^[a-z0-9._-]{1,32}$/.test(code)) return "Letters, digits, dots, dashes and underscores, at most 32.";
  const there = drivers.find((d) => d.code === code);
  if (there) return there.active ? `${code} is already ${there.name}'s.` : `${code} is already ${there.name}'s, deactivated: reactivate them instead.`;
  return null;
}

export function nameProblem(name: string, most: number, what = "the name"): string | null {
  const t = name.trim();
  if (!t) return `Type ${what}.`;
  if (t.length > most) return `At most ${most} characters.`;
  return null;
}

export function pinProblem(pin: string): string | null {
  return /^[0-9]{4,8}$/.test(pin) ? null : "A PIN is 4 to 8 digits.";
}

/** One carrier per name whatever its case, and never "Own fleet": that is
 * what Simple WMS is told for your own trucks. */
export function carrierNameProblem(name: string, carriers: Carrier[], otherThan?: number): string | null {
  const t = name.trim();
  const short = nameProblem(t, 64, "the carrier's name");
  if (short) return short;
  if (t.toLowerCase() === "own fleet") return "Own fleet is what Simple WMS is told for your own trucks: give the carrier its own name.";
  const there = carriers.find((c) => c.id !== otherThan && c.name.toLowerCase() === t.toLowerCase());
  if (there) return there.active ? `${there.name} is already on file.` : `${there.name} is already on file, retired: bring it back instead.`;
  return null;
}

/** A checklist as it will be sent: each line's problem by its place, and
 * one for the list as a whole. */
export function checklistProblems(lines: ChecklistLine[]): { lines: (string | null)[]; list: string | null } {
  const seen = new Map<string, number>();
  const out = lines.map((l, i) => {
    const label = l.label.trim();
    if (!label) return "Type what to check, or take the line out.";
    if (label.length > 80) return "At most 80 characters.";
    const key = label.toLowerCase();
    if (seen.has(key)) return `Line ${seen.get(key)! + 1} says this already.`;
    seen.set(key, i);
    return null;
  });
  const list = lines.length === 0 ? "A checklist needs at least one line."
    : lines.length > 30 ? "At most 30 lines." : null;
  return { lines: out, list };
}

/** The API's own field errors (a 422), by field, or null for anything else. */
export function apiProblems(e: unknown): Problems | null {
  if (!(e instanceof ApiError) || e.status !== 422 || e.errors.length === 0) return null;
  return Object.fromEntries(e.errors.map((f) => [f.field, f.message]));
}

/** What to say when a request didn't work: the API's own words, or that
 * it couldn't be reached. */
export function said(e: unknown): string {
  if (e instanceof Unreachable) return e.message;
  return e instanceof ApiError ? e.message : "Simple TMS could not be reached. Check the connection and try again.";
}

/** Only the problems there are. */
export function found(problems: Record<string, string | null>): Problems {
  return Object.fromEntries(Object.entries(problems).filter((p): p is [string, string] => p[1] !== null));
}
```

**Step 4: Run them, then the suite**

Run: `npx vitest run src/test/admin.test.ts src/test/client.test.ts`
Expected: `Tests  21 passed (21)`.
Run: `npm test && npm run typecheck`
Expected: `Tests  131 passed (131)`, and no type errors.

**Step 5: Commit**

```bash
git add apps/desktop/src/lib/admin.ts apps/desktop/src/api/client.ts apps/desktop/src/api/types.ts \
        apps/desktop/src/test/admin.test.ts apps/desktop/src/test/client.test.ts
git commit -m "Say what the admin forms will refuse before they send, in the API's own rules"
```

### Task 4: Add a vehicle

Decisions 1 and 6. `ui/Field.tsx` is the form kit every admin form uses: a visible label tied to its input, the hint and the problem under it and read with it (`aria-describedby`), `aria-invalid` while there is a problem, and `focusFirstProblem`. `VehicleForm` adds a truck, or (Task 5) changes one. **Add a vehicle** sits beside the search, as `Fleet.html` draws it, for a fleet manager; it opens the form under the tools with the focus in **Rego**, and closing it puts the focus back on the button. `fleetDepot.ts` is the stubbed API the Fleet admin tests share.

**Files:**
- Create: `apps/desktop/src/ui/Field.tsx`, `apps/desktop/src/admin/VehicleForm.tsx`
- Modify: `apps/desktop/src/pages/Fleet.tsx`, `apps/desktop/src/index.css`
- Test: `apps/desktop/src/test/fleetDepot.ts` (new), `apps/desktop/src/test/FleetVehicles.test.tsx` (new)

**Step 1: Write the failing tests**

Create `apps/desktop/src/test/fleetDepot.ts`:
```ts
/** A depot for the Fleet drawer's admin tests: trucks, drivers and the
 * checklists, as the API gives them; anything else answered by `answers`. */
import type { ChecklistItem, Driver, Vehicle } from "../api/types";
import { json, MANAGER, path, stubApi, type Call } from "./harness";

const due = { expires_on: "2027-03-03", days_left: 142, state: "ok" as const };
const service = { last_on: "2026-07-14", last_km: 46900, due_on: "2027-01-14", due_km: 61900,
                  days_left: 92, km_left: 3000, state: "ok" as const };

export const truck = (rego: string, over: Partial<Vehicle> = {}): Vehicle => ({
  id: 1, rego, type: "rigid", max_kg: "4500", max_m3: "22", active: true, service_every_months: 6,
  service_every_km: 15000, odometer_km: 58900, rego_due: due, service_due: service, grounded: null,
  last_prestart: null, state: "ok", blocked: null, ...over,
});

export const person = (code: string, name: string, over: Partial<Driver> = {}): Driver => ({
  id: 1, code, name, active: true, locked: false, failed_attempts: 0, created_at: "2026-10-01T00:00:00Z", ...over,
});

const line = (id: number, label: string, critical = false): ChecklistItem => ({ id, label, critical });
export const VAN_LIST = [line(1, "Tyres"), line(2, "Lights", true), line(3, "Brakes", true), line(4, "Mirrors"),
                         line(5, "Load restraints"), line(6, "Damage")];

export function fleetDepot(answers: Record<string, (c: Call) => Response | null> = {}, who: object = MANAGER, state: {
  vehicles?: Vehicle[]; drivers?: Driver[];
} = {}) {
  return stubApi((method, url, body) => {
    const key = `${method} ${path(url)}`;
    if (answers[key]) return answers[key]({ method, url, body });
    if (key === "GET /v1/vehicles") {
      const items = state.vehicles ?? [truck("TMS041"), truck("TMS018", { type: "van", max_kg: "1200", max_m3: "8.5" })];
      return json(200, { items, total: items.length });
    }
    if (key === "GET /v1/drivers") {
      const items = state.drivers ?? [person("kim", "Kim"), person("tom", "Tom", { locked: true, failed_attempts: 5 })];
      return json(200, { items, total: items.length });
    }
    if (key === "GET /v1/runs") return json(200, { items: [], total: 0 });
    if (key === "GET /v1/checklists") {
      return json(200, { van: VAN_LIST, rigid: VAN_LIST.map((l) => ({ ...l, id: l.id + 6 })),
                         ute: VAN_LIST.map((l) => ({ ...l, id: l.id + 12 })) });
    }
    if (key.startsWith("GET /v1/vehicles/") && key.endsWith("/history")) return json(200, []);
    return null;
  }, who);
}
```

Create `apps/desktop/src/test/FleetVehicles.test.tsx`:
```tsx
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { truck, fleetDepot } from "./fleetDepot";
import { DISPATCHER, json } from "./harness";
import { renderApp } from "./harness";

describe("Fleet: adding a vehicle", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("is for a fleet manager: a dispatcher sees no Add a vehicle", async () => {
    fleetDepot({}, DISPATCHER);
    renderApp("/fleet");
    await screen.findByRole("button", { name: "Show TMS041" });
    expect(screen.queryByRole("button", { name: "Add a vehicle" })).not.toBeInTheDocument();
  });

  it("adds one, its rego as the API keeps it and its limits as typed", async () => {
    let added = false;
    const made = truck("TMS050", { service_every_km: null });
    const { calls } = fleetDepot({
      "POST /v1/vehicles": () => { added = true; return json(201, made); },
      "GET /v1/vehicles": () => json(200, { items: added ? [truck("TMS041"), made] : [truck("TMS041")], total: 1 }),
    });
    renderApp("/fleet");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Add a vehicle" }));
    const form = screen.getByRole("form", { name: "Add a vehicle" });
    expect(within(form).getByLabelText("Rego")).toHaveFocus();
    await user.type(within(form).getByLabelText("Rego"), " tms 050");
    await user.selectOptions(within(form).getByLabelText("Type"), "rigid");
    await user.type(within(form).getByLabelText("Most it carries (kg)"), "4,500");
    await user.type(within(form).getByLabelText("Most it carries (m³)"), "22.50");
    await user.type(within(form).getByLabelText("Service every (months)"), "6");
    await user.click(within(form).getByRole("button", { name: "Add the vehicle" }));

    expect(await screen.findByText("TMS050 is added. Record its rego expiry and last service from its row.")).toHaveAttribute("role", "status");
    expect(calls.find((c) => c.method === "POST" && c.url === "/v1/vehicles")!.body).toEqual({
      rego: "TMS050", type: "rigid", max_kg: "4500", max_m3: "22.5", service_every_months: 6, service_every_km: null });
    expect(screen.queryByRole("form", { name: "Add a vehicle" })).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: "Show TMS050" })).toHaveAttribute("aria-pressed", "true"));
    expect(screen.getByRole("button", { name: "Add a vehicle" })).toHaveFocus();
  });

  it("says beside each field what is wrong, sends nothing, and puts the focus on the first", async () => {
    const { calls } = fleetDepot();
    renderApp("/fleet");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Add a vehicle" }));
    const form = screen.getByRole("form", { name: "Add a vehicle" });
    await user.type(within(form).getByLabelText("Rego"), "tms041");
    await user.type(within(form).getByLabelText("Most it carries (kg)"), "1200.1234");
    await user.type(within(form).getByLabelText("Service every (km)"), "0");
    await user.click(within(form).getByRole("button", { name: "Add the vehicle" }));

    const rego = within(form).getByLabelText("Rego");
    expect(rego).toHaveAttribute("aria-invalid", "true");
    expect(rego).toHaveAccessibleDescription("As on the plate. Spaces don't count. TMS041 is already on file.");
    expect(within(form).getByLabelText("Most it carries (kg)")).toHaveAccessibleDescription("At most 3 decimal places.");
    expect(within(form).getByLabelText("Most it carries (m³)")).toHaveAccessibleDescription("Give the most it carries, in m³.");
    expect(within(form).getByLabelText("Service every (km)")).toHaveAccessibleDescription("From 1 to 1,000,000 km, or leave it blank.");
    expect(within(form).getByLabelText("Service every (months)")).not.toHaveAttribute("aria-invalid");
    await waitFor(() => expect(rego).toHaveFocus());
    expect(calls.some((c) => c.method === "POST")).toBe(false);
  });

  it("shows the API's own refusal beside its field", async () => {
    fleetDepot({ "POST /v1/vehicles": () => json(422, { errors: [{ field: "rego", message: "vehicle TMS050 already exists" }] }) });
    renderApp("/fleet");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Add a vehicle" }));
    const form = screen.getByRole("form", { name: "Add a vehicle" });
    await user.type(within(form).getByLabelText("Rego"), "TMS050");
    await user.type(within(form).getByLabelText("Most it carries (kg)"), "1200");
    await user.type(within(form).getByLabelText("Most it carries (m³)"), "8.5");
    await user.click(within(form).getByRole("button", { name: "Add the vehicle" }));
    await waitFor(() => expect(within(form).getByLabelText("Rego")).toHaveAccessibleDescription(
      "As on the plate. Spaces don't count. vehicle TMS050 already exists"));
  });
});
```

**Step 2: Run them to see them fail**

Run: `npx vitest run src/test/FleetVehicles.test.tsx`
Expected: `Tests  3 failed | 1 passed (4)`: `Unable to find role="button" and name "Add a vehicle"` (the dispatcher's test passes already).

**Step 3: Implement**

Create `apps/desktop/src/ui/Field.tsx`:
```tsx
import { useId, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes } from "react";

/** A labelled field with its hint and its problem under it, both read out
 * with it (aria-describedby), and marked invalid while it has a problem.
 * The admin forms check for themselves (noValidate), so the browser's own
 * bubbles never stand in for these. */
function useDescribed(hint?: ReactNode, error?: string | null) {
  const id = useId();
  const describedBy = [hint ? `${id}-hint` : "", error ? `${id}-err` : ""].filter(Boolean).join(" ") || undefined;
  const below = (
    <>
      {hint && <div id={`${id}-hint`} className="small">{hint}</div>}
      {error && <div id={`${id}-err`} className="ferr">{error}</div>}
    </>
  );
  return { id, describedBy, below };
}

export function Field({ label, hint, error, ...input }: {
  label: string; hint?: ReactNode; error?: string | null;
} & InputHTMLAttributes<HTMLInputElement>) {
  const { id, describedBy, below } = useDescribed(hint, error);
  return (
    <div className="fld">
      <label htmlFor={id}>{label}</label>
      <input id={id} className="field" type="text" autoComplete="off" aria-invalid={error ? true : undefined}
             aria-describedby={describedBy} {...input} />
      {below}
    </div>
  );
}

export function SelectField({ label, hint, error, children, ...select }: {
  label: string; hint?: ReactNode; error?: string | null; children: ReactNode;
} & SelectHTMLAttributes<HTMLSelectElement>) {
  const { id, describedBy, below } = useDescribed(hint, error);
  return (
    <div className="fld">
      <label htmlFor={id}>{label}</label>
      <select id={id} className="field" aria-invalid={error ? true : undefined} aria-describedby={describedBy} {...select}>
        {children}
      </select>
      {below}
    </div>
  );
}

/** Once a form's problems are on screen, the first one gets the focus, so a
 * keyboard or screen reader user lands on what to put right. */
export function focusFirstProblem(form: HTMLFormElement | null) {
  setTimeout(() => form?.querySelector<HTMLElement>('[aria-invalid="true"]')?.focus(), 0);
}
```

Create `apps/desktop/src/admin/VehicleForm.tsx`:
```tsx
import { useRef, useState, type FormEvent } from "react";
import { api } from "../api/client";
import type { Vehicle } from "../api/types";
import {
  amount, amountProblem, apiProblems, found, interval, intervalProblem, normalRego, regoProblem,
  said, typeName, VEHICLE_TYPES, type Problems,
} from "../lib/admin";
import { Field, focusFirstProblem, SelectField } from "../ui/Field";

/** Add a vehicle (no `vehicle`), or change one: its type, what it carries
 * and how often it is serviced. The rego is the truck's name for good. For
 * a fleet manager; the API says no to anyone else. */
export function VehicleForm({ vehicle, vehicles, onDone, onCancel }: {
  vehicle?: Vehicle; vehicles: Vehicle[];
  onDone: (message: string, rego: string) => void | Promise<void>; onCancel?: () => void;
}) {
  const adding = !vehicle;
  const [rego, setRego] = useState("");
  const [type, setType] = useState(vehicle?.type ?? "van");
  const [kg, setKg] = useState(vehicle?.max_kg ?? "");
  const [m3, setM3] = useState(vehicle?.max_m3 ?? "");
  const [months, setMonths] = useState(vehicle?.service_every_months?.toString() ?? "");
  const [km, setKm] = useState(vehicle?.service_every_km?.toString() ?? "");
  const [problems, setProblems] = useState<Problems>({});
  const [trouble, setTrouble] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const form = useRef<HTMLFormElement>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setTrouble(null);
    const plate = normalRego(rego);
    const mine = found({
      rego: adding ? regoProblem(plate, vehicles) : null,
      max_kg: amountProblem(kg, "kg"),
      max_m3: amountProblem(m3, "m³"),
      service_every_months: intervalProblem(months, 120, "months"),
      service_every_km: intervalProblem(km, 1_000_000, "km"),
    });
    setProblems(mine);
    if (Object.keys(mine).length) { focusFirstProblem(form.current); return; }
    const body = { type, max_kg: amount(kg), max_m3: amount(m3),
                   service_every_months: interval(months), service_every_km: interval(km) };
    setBusy(true);
    try {
      if (adding) {
        const made = await api.post<Vehicle>("/v1/vehicles", { rego: plate, ...body });
        await onDone(`${made.rego} is added. Record its rego expiry and last service from its row.`, made.rego);
      } else {
        await api.patch<Vehicle>(`/v1/vehicles/${vehicle.rego}`, body);
        await onDone(`${vehicle.rego} is changed.`, vehicle.rego);
      }
    } catch (err) {
      const theirs = apiProblems(err);
      if (theirs) { setProblems(theirs); focusFirstProblem(form.current); }
      else setTrouble(said(err));
    } finally {
      setBusy(false);
    }
  }

  const title = adding ? "Add a vehicle" : `Change ${vehicle.rego}`;
  return (
    <form ref={form} className="adminform" aria-label={title} noValidate onSubmit={(e) => void submit(e)}>
      {adding && <div className="eyebrow">ADD A VEHICLE</div>}
      <div className="row">
        {adding && <Field label="Rego" value={rego} onChange={(e) => setRego(e.target.value)} error={problems.rego}
                          maxLength={20} spellCheck={false} hint="As on the plate. Spaces don't count." autoFocus />}
        <SelectField label="Type" value={type} onChange={(e) => setType(e.target.value)} error={problems.type}
                     hint="Which pre-start checklist its driver answers.">
          {VEHICLE_TYPES.map((t) => <option key={t} value={t}>{typeName(t)}</option>)}
        </SelectField>
      </div>
      <div className="row">
        <Field label="Most it carries (kg)" inputMode="decimal" value={kg} onChange={(e) => setKg(e.target.value)} error={problems.max_kg} />
        <Field label="Most it carries (m³)" inputMode="decimal" value={m3} onChange={(e) => setM3(e.target.value)} error={problems.max_m3} />
      </div>
      <div className="row">
        <Field label="Service every (months)" inputMode="numeric" value={months} onChange={(e) => setMonths(e.target.value)}
               error={problems.service_every_months} />
        <Field label="Service every (km)" inputMode="numeric" value={km} onChange={(e) => setKm(e.target.value)}
               error={problems.service_every_km} />
      </div>
      <div className="small">The service falls due by months or kilometres, whichever comes first. Leave both blank and it is never due.</div>
      {trouble && <div className="msg r" role="alert">{trouble}</div>}
      <div className="acts">
        <button type="submit" className="btn primary" disabled={busy}>{busy ? "Saving…" : adding ? "Add the vehicle" : "Save changes"}</button>
        {onCancel && <button type="button" className="btn" onClick={onCancel} disabled={busy}>Cancel</button>}
      </div>
    </form>
  );
}
```

Change `apps/desktop/src/pages/Fleet.tsx`:
```diff
--- a/apps/desktop/src/pages/Fleet.tsx
+++ b/apps/desktop/src/pages/Fleet.tsx
@@ -1,5 +1,6 @@
-import { useCallback, useEffect, useState, type FormEvent } from "react";
+import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
 import { useOutletContext } from "react-router-dom";
+import { VehicleForm } from "../admin/VehicleForm";
 import { api, ApiError, Unreachable } from "../api/client";
 import type { HistoryItem, Page, PrestartDetail, Run, Vehicle } from "../api/types";
 import { useAuth } from "../auth/AuthContext";
@@ -200,6 +201,8 @@ function VehicleAside({ v, driverName, onChanged }: { v: Vehicle; driverName: st
  * grounded one, and what a fleet manager records. */
 export function Fleet() {
   const { reloadCounts } = useOutletContext<{ reloadCounts: () => void }>();
+  const { can } = useAuth();
+  const manager = can("fleet:write");
   // the day asked about, worked out again on each load: a drawer left open
   // past midnight asks about the new day
   const [today, setToday] = useState(() => isoDay(new Date()));
@@ -208,6 +211,8 @@ export function Fleet() {
   const [q, setQ] = useState("");
   const [chosen, setChosen] = useState<string | null>(null);
   const [note, setNote] = useState<Note>(null);
+  const [adding, setAdding] = useState(false);
+  const addButton = useRef<HTMLButtonElement>(null);
 
   const load = useCallback(async () => {
     const day = isoDay(new Date());
@@ -232,6 +237,11 @@ export function Fleet() {
     reloadCounts();
   }
 
+  function closeAdd() {
+    setAdding(false);
+    setTimeout(() => addButton.current?.focus(), 0);
+  }
+
   const runOf = (rego: string) => runs.find((r) => r.vehicle.rego === rego && r.status !== "cancelled");
   const all = vehicles ?? [];
   const shown = all.filter((v) => {
@@ -254,9 +264,17 @@ export function Fleet() {
         <div className="sub">Your trucks, pre-start checks, and when rego and service fall due.</div>
       </div>
     }>
-      <label className="search" style={{ maxWidth: 420 }}><Icon name="search" />
-        <span className="sr">Search vehicles</span>
-        <input type="search" placeholder="Rego or driver" value={q} onChange={(e) => setQ(e.target.value)} /></label>
+      <div className="tools">
+        <label className="search" style={{ flex: "0 1 420px" }}><Icon name="search" />
+          <span className="sr">Search vehicles</span>
+          <input type="search" placeholder="Rego or driver" value={q} onChange={(e) => setQ(e.target.value)} /></label>
+        {manager && (
+          <button ref={addButton} type="button" className="btn primary" aria-expanded={adding} onClick={() => setAdding((a) => !a)}>
+            <Icon name="plus" size={16} width={2.6} />Add a vehicle</button>)}
+      </div>
+      {adding && (
+        <VehicleForm vehicles={all} onCancel={closeAdd}
+                     onDone={async (text, rego) => { closeAdd(); setChosen(rego); await changed({ tone: "g", text }); }} />)}
       {note && <div className={`msg ${note.tone}`} role={note.tone === "r" ? "alert" : "status"}>{note.text}</div>}
       <div className="stats">
         <div className="stat"><div className="eyebrow">VEHICLES</div><div className="v num">{active.length}</div>
@@ -292,7 +310,8 @@ export function Fleet() {
                   </tr>
                 );
               })}
-              {vehicles?.length === 0 && <tr><td colSpan={7} className="small">No vehicles yet. Add them with POST /v1/vehicles.</td></tr>}
+              {vehicles?.length === 0 && (
+                <tr><td colSpan={7} className="small">{manager ? "No vehicles yet. Add your trucks with Add a vehicle." : "No vehicles yet. A fleet manager adds them here."}</td></tr>)}
             </tbody>
           </table>
         </div>
```

Change `apps/desktop/src/index.css`:
```diff
--- a/apps/desktop/src/index.css
+++ b/apps/desktop/src/index.css
@@ -209,6 +209,17 @@
   .grp tr.sel td { background: #F1F8F4; }
   .grp tr.sel .rowbtn { border-color: var(--color-green); background: var(--color-tint); }
 
+  /* the admin forms: no drawn design, so the drawers' own fields and boxes */
+  .tools { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; }
+  .adminform { padding: 16px; border-radius: 16px; border: 1px solid var(--color-card-line); background: #fff; display: flex; flex-direction: column; gap: 12px; }
+  .adminform .row { display: flex; flex-wrap: wrap; gap: 12px; }
+  .adminform .row > .fld { flex: 1 1 160px; }
+  .acts { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
+  .fld { display: flex; flex-direction: column; gap: 4px; font-size: 13px; font-weight: 700; min-width: 0; }
+  .fld .small { font-weight: 400; }
+  .ferr { font-size: 12px; font-weight: 700; color: var(--color-red-ink); }
+  .field[aria-invalid="true"] { border-color: var(--color-red); box-shadow: 0 0 0 1px var(--color-red); }
+
   /* the Consignments drawer (Consignments.html) */
   .p-blue { background: #E4EEF9; color: #235596; }
   .tabs { display: flex; flex-wrap: wrap; gap: 6px; }
```

**Step 4: Run them, then the suite**

Run: `npx vitest run src/test/FleetVehicles.test.tsx`
Expected: `Tests  4 passed (4)`.
Run: `npm test && npm run typecheck`
Expected: `Tests  135 passed (135)`, and no type errors.

**Step 5: Commit**

```bash
git add apps/desktop/src/ui/Field.tsx apps/desktop/src/admin/VehicleForm.tsx apps/desktop/src/pages/Fleet.tsx \
        apps/desktop/src/index.css apps/desktop/src/test/fleetDepot.ts \
        apps/desktop/src/test/FleetVehicles.test.tsx
git commit -m "Add a vehicle from the Fleet drawer"
```

### Task 5: Change, deactivate and reactivate a vehicle

Decisions 3, 6 and 8. In the aside, for a fleet manager, under **RECORD**: **VEHICLE**, a line saying what the truck is ("Rigid · up to 4,500 kg and 22 m³ · serviced every 6 months or 15,000 km"), **Change type, limits or service** (the same form, without the rego, sending every field: an interval left blank is cleared) and **Deactivate**/**Reactivate**. A refusal (`409 vehicle_overdue`, `on_a_run`) shows as it comes; a deactivated truck says so beside its rego.

**Files:**
- Modify: `apps/desktop/src/pages/Fleet.tsx`
- Test: `apps/desktop/src/test/FleetVehicles.test.tsx`

**Step 1: Write the failing tests**

Change `apps/desktop/src/test/FleetVehicles.test.tsx` (the imports tidied too):
```diff
--- a/apps/desktop/src/test/FleetVehicles.test.tsx
+++ b/apps/desktop/src/test/FleetVehicles.test.tsx
@@ -1,8 +1,7 @@
 import { screen, waitFor, within } from "@testing-library/react";
 import userEvent from "@testing-library/user-event";
-import { truck, fleetDepot } from "./fleetDepot";
-import { DISPATCHER, json } from "./harness";
-import { renderApp } from "./harness";
+import { fleetDepot, truck } from "./fleetDepot";
+import { DISPATCHER, json, renderApp } from "./harness";
 
 describe("Fleet: adding a vehicle", () => {
   afterEach(() => vi.unstubAllGlobals());
@@ -77,3 +76,78 @@ describe("Fleet: adding a vehicle", () => {
       "As on the plate. Spaces don't count. vehicle TMS050 already exists"));
   });
 });
+
+describe("Fleet: changing a vehicle", () => {
+  afterEach(() => vi.unstubAllGlobals());
+
+  async function open(rego: string) {
+    const user = userEvent.setup();
+    await user.click(await screen.findByRole("button", { name: `Show ${rego}` }));
+    return { user, aside: screen.getByRole("complementary", { name: "Pre-start check record" }) };
+  }
+
+  it("shows a dispatcher what it carries, with nothing to change", async () => {
+    fleetDepot({}, DISPATCHER);
+    renderApp("/fleet");
+    const { aside } = await open("TMS041");
+    expect(within(aside).queryByRole("button", { name: "Change type, limits or service" })).not.toBeInTheDocument();
+    expect(within(aside).queryByRole("button", { name: "Deactivate" })).not.toBeInTheDocument();
+  });
+
+  it("changes its limits and clears a service interval left blank", async () => {
+    const { calls } = fleetDepot({ "PATCH /v1/vehicles/TMS041": () => json(200, truck("TMS041")) });
+    renderApp("/fleet");
+    const { user, aside } = await open("TMS041");
+    expect(aside).toHaveTextContent("Rigid · up to 4,500 kg and 22 m³ · serviced every 6 months or 15,000 km");
+    await user.click(within(aside).getByRole("button", { name: "Change type, limits or service" }));
+    const form = within(aside).getByRole("form", { name: "Change TMS041" });
+    expect(within(form).queryByLabelText("Rego")).not.toBeInTheDocument();
+    expect(within(form).getByLabelText("Most it carries (kg)")).toHaveValue("4500");
+    await user.clear(within(form).getByLabelText("Most it carries (kg)"));
+    await user.type(within(form).getByLabelText("Most it carries (kg)"), "4750.5");
+    await user.clear(within(form).getByLabelText("Service every (km)"));
+    await user.click(within(form).getByRole("button", { name: "Save changes" }));
+    expect(await screen.findByText("TMS041 is changed.")).toHaveAttribute("role", "status");
+    expect(calls.find((c) => c.method === "PATCH")!.body).toEqual({
+      type: "rigid", max_kg: "4750.5", max_m3: "22", service_every_months: 6, service_every_km: null });
+  });
+
+  it("says why an overdue truck's schedule can't be eased", async () => {
+    fleetDepot({ "PATCH /v1/vehicles/TMS041": () => json(409, { code: "vehicle_overdue",
+      detail: "TMS041's service is overdue. Record the service first: its schedule can't be eased or its type changed while it is overdue." }) });
+    renderApp("/fleet");
+    const { user, aside } = await open("TMS041");
+    await user.click(within(aside).getByRole("button", { name: "Change type, limits or service" }));
+    const form = within(aside).getByRole("form", { name: "Change TMS041" });
+    await user.type(within(form).getByLabelText("Service every (months)"), "2");
+    await user.click(within(form).getByRole("button", { name: "Save changes" }));
+    expect(await within(form).findByRole("alert")).toHaveTextContent("TMS041's service is overdue. Record the service first");
+  });
+
+  it("deactivates one after asking, and says which run holds it when the API refuses", async () => {
+    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
+    let off = false;
+    const { calls } = fleetDepot({
+      "POST /v1/vehicles/TMS018/deactivate": () => { off = true; return json(200, truck("TMS018", { active: false })); },
+      "POST /v1/vehicles/TMS041/deactivate": () => json(409, { code: "on_a_run",
+        detail: "TMS041 is on R-0001 (planned for 16 Oct 2026). Give that run another truck, or cancel it, first." }),
+      "GET /v1/vehicles": () => json(200, { items: [truck("TMS041"), truck("TMS018", { active: !off })], total: 2 }),
+    });
+    renderApp("/fleet");
+    let { user, aside } = await open("TMS041");
+    await user.click(within(aside).getByRole("button", { name: "Deactivate" }));
+    expect(confirm).toHaveBeenCalledWith("Deactivate TMS041? It can't take a run until it is reactivated. Its checks and history stay.");
+    expect(await within(aside).findByRole("alert")).toHaveTextContent(
+      "TMS041 is on R-0001 (planned for 16 Oct 2026). Give that run another truck, or cancel it, first.");
+
+    ({ user, aside } = await open("TMS018"));
+    await user.click(within(aside).getByRole("button", { name: "Deactivate" }));
+    expect(await screen.findByText("TMS018 is deactivated.")).toBeInTheDocument();
+    aside = screen.getByRole("complementary", { name: "Pre-start check record" });
+    expect(await within(aside).findByRole("button", { name: "Reactivate" })).toBeInTheDocument();
+    expect(within(aside).getByText("Deactivated")).toBeInTheDocument();
+    expect(calls.filter((c) => c.method === "POST").map((c) => c.url)).toEqual(
+      ["/v1/vehicles/TMS041/deactivate", "/v1/vehicles/TMS018/deactivate"]);
+    confirm.mockRestore();
+  });
+});
```

**Step 2: Run them to see them fail**

Run: `npx vitest run src/test/FleetVehicles.test.tsx`
Expected: `Tests  3 failed | 5 passed (8)`: `Unable to find … role "button" and name "Change type, limits or service"` and `… "Deactivate"`.

**Step 3: Implement**

Change `apps/desktop/src/pages/Fleet.tsx`:
```diff
--- a/apps/desktop/src/pages/Fleet.tsx
+++ b/apps/desktop/src/pages/Fleet.tsx
@@ -5,6 +5,7 @@ import { api, ApiError, Unreachable } from "../api/client";
 import type { HistoryItem, Page, PrestartDetail, Run, Vehicle } from "../api/types";
 import { useAuth } from "../auth/AuthContext";
 import { clock, dayLabel, isoDay } from "../lib/days";
+import { plain, toMicro } from "../lib/decimal";
 import { checkCell, km, longDate, regoCell, serviceCell, statusPill, when, type Cell } from "../lib/fleet";
 import { Drawer } from "../ui/Drawer";
 import { Icon } from "../ui/icons";
@@ -41,6 +42,14 @@ function historyLine(h: HistoryItem): string {
   return [KIND[h.kind], what, h.note].filter(Boolean).join(" · ");
 }
 
+/** "Rigid · up to 4,500 kg and 22 m³ · serviced every 6 months or 15,000 km" */
+function vehicleLine(v: Vehicle): string {
+  const every = [v.service_every_months && `${v.service_every_months} months`,
+                 v.service_every_km && `${km(v.service_every_km)} km`].filter(Boolean).join(" or ");
+  return [`${v.type[0].toUpperCase() + v.type.slice(1)}`, `up to ${plain(toMicro(v.max_kg))} kg and ${plain(toMicro(v.max_m3))} m³`,
+          every ? `serviced every ${every}` : "no service schedule"].join(" · ");
+}
+
 /** One small form: a few fields and a note, sent as a new record. */
 function RecordForm({ title, button, fields, send }: {
   title: string; button: string; fields: { name: string; label: string; type: "date" | "number"; required?: boolean }[];
@@ -72,7 +81,9 @@ function RecordForm({ title, button, fields, send }: {
 
 /** The aside: the check that grounded the truck (or its last one), Mark
  * fixed, what a fleet manager records, and the truck's history. */
-function VehicleAside({ v, driverName, onChanged }: { v: Vehicle; driverName: string | null; onChanged: (note: Note) => Promise<void> }) {
+function VehicleAside({ v, vehicles, driverName, onChanged }: {
+  v: Vehicle; vehicles: Vehicle[]; driverName: string | null; onChanged: (note: Note) => Promise<void>;
+}) {
   const { can } = useAuth();
   const manager = can("fleet:write");
   const checkId = v.grounded?.check_id ?? v.last_prestart?.id ?? null;
@@ -84,6 +95,7 @@ function VehicleAside({ v, driverName, onChanged }: { v: Vehicle; driverName: st
   // bumped after each record: Mark fixed leaves the check id it shows the
   // same, and a record can leave the truck's dates as they were
   const [recorded, setRecorded] = useState(0);
+  const [changing, setChanging] = useState(false);
 
   useEffect(() => {
     let live = true;
@@ -110,6 +122,22 @@ function VehicleAside({ v, driverName, onChanged }: { v: Vehicle; driverName: st
     }
   }
 
+  async function switchActive() {
+    const deactivating = v.active;
+    if (deactivating && !window.confirm(`Deactivate ${v.rego}? It can't take a run until it is reactivated. Its checks and history stay.`)) return;
+    setError(null);
+    setBusy(true);
+    try {
+      await api.post(`/v1/vehicles/${v.rego}/${deactivating ? "deactivate" : "reactivate"}`);
+      await onChanged({ tone: "g", text: deactivating ? `${v.rego} is deactivated.` : `${v.rego} is reactivated, and can take a run.` });
+    } catch (e) {
+      // on a run not finished: the API names each run
+      setError(said(e));
+    } finally {
+      setBusy(false);
+    }
+  }
+
   async function fix(e: FormEvent) {
     e.preventDefault();
     if (!check || !fixNote.trim()) return;
@@ -129,7 +157,8 @@ function VehicleAside({ v, driverName, onChanged }: { v: Vehicle; driverName: st
         <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
           <span style={{ fontSize: 22, fontWeight: 800 }}>{v.rego}</span>
           {v.state === "grounded" && <span className="pill p-red">Grounded</span>}
-          {v.state === "overdue" && <span className="pill p-red">Overdue</span>}</div>
+          {v.state === "overdue" && <span className="pill p-red">Overdue</span>}
+          {!v.active && <span className="pill p-grey">Deactivated</span>}</div>
         {check && (
           <div style={{ fontSize: 13, color: "var(--color-ink-2)", textTransform: "none" }}>
             {[v.type[0].toUpperCase() + v.type.slice(1), who, `${dayLabel(new Date(check.at))}, ${clock(new Date(check.at))}`,
@@ -183,6 +212,19 @@ function VehicleAside({ v, driverName, onChanged }: { v: Vehicle; driverName: st
                       send={(f) => record("odometer", { odometer_km: Number(f.odometer_km), note: f.note }, `${v.rego}'s odometer is put right.`)} />
         </div>
       )}
+      {manager && (
+        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
+          <div className="eyebrow">VEHICLE</div>
+          <div className="small">{vehicleLine(v)}</div>
+          <div className="acts">
+            <button type="button" className="btn sm" aria-expanded={changing} onClick={() => setChanging((c) => !c)}>Change type, limits or service</button>
+            <button type="button" className="btn sm" disabled={busy} onClick={() => void switchActive()}>{v.active ? "Deactivate" : "Reactivate"}</button>
+          </div>
+          {changing && (
+            <VehicleForm vehicle={v} vehicles={vehicles} onCancel={() => setChanging(false)}
+                         onDone={async (text) => { setChanging(false); await onChanged({ tone: "g", text }); }} />)}
+        </div>
+      )}
       {history.length > 0 && (
         <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
           <div className="eyebrow">HISTORY</div>
@@ -316,7 +358,7 @@ export function Fleet() {
           </table>
         </div>
         {/* stay on the truck it changed: once fixed, it's no longer the grounded one shown by default */}
-        {selected && <VehicleAside key={selected.rego} v={selected} driverName={runOf(selected.rego)?.driver.name ?? null}
+        {selected && <VehicleAside key={selected.rego} v={selected} vehicles={all} driverName={runOf(selected.rego)?.driver.name ?? null}
                                    onChanged={(n) => { setChosen(selected.rego); return changed(n); }} />}
       </div>
       <div className="small">Amber 30 days or 1,000 km before it's due, red once it's overdue. A grounded or overdue truck can't take a run.
```

**Step 4: Run them, then the suite**

Run: `npx vitest run src/test/FleetVehicles.test.tsx`
Expected: `Tests  8 passed (8)`.
Run: `npm test && npm run typecheck`
Expected: `Tests  139 passed (139)`, and no type errors.

**Step 5: Commit**

```bash
git add apps/desktop/src/pages/Fleet.tsx apps/desktop/src/test/FleetVehicles.test.tsx
git commit -m "Change, deactivate and reactivate a vehicle from its row in Fleet"
```

### Task 6: Fleet's tabs, and the Drivers tab

Decisions 1, 3, 4, 6 and 8. **Fleet** gets a tab list (**Vehicles**, **Drivers**; Task 7 adds the third), each panel labelled by its tab; the message line sits under the tabs and clears when the tab changes. The sub-heading becomes `Fleet.html`'s, now true. On **Drivers** everyone sees the table (code, name, **Active**, **Locked** in red, **Deactivated** in grey); a fleet manager also gets **Add a driver** and, for the driver picked (a locked one first), an aside: **Name** with **Save name**, **New PIN** with **Set the PIN**, **Unlock** when locked, and **Deactivate**/**Reactivate**.

**Files:**
- Create: `apps/desktop/src/admin/Drivers.tsx`
- Modify: `apps/desktop/src/pages/Fleet.tsx`, `apps/desktop/src/index.css`
- Test: `apps/desktop/src/test/FleetDrivers.test.tsx` (new)

**Step 1: Write the failing tests**

Create `apps/desktop/src/test/FleetDrivers.test.tsx`:
```tsx
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { fleetDepot, person } from "./fleetDepot";
import { DISPATCHER, json, renderApp } from "./harness";

async function driversTab() {
  const user = userEvent.setup();
  await user.click(await screen.findByRole("tab", { name: "Drivers" }));
  const panel = screen.getByRole("tabpanel", { name: "Drivers" });
  await within(panel).findByRole("table");
  return { user, panel };
}

describe("Fleet: drivers", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("lists the drivers and who is locked out for a dispatcher, with nothing to change", async () => {
    fleetDepot({}, DISPATCHER);
    renderApp("/fleet");
    const { panel } = await driversTab();
    expect(screen.getByRole("tab", { name: "Drivers" })).toHaveAttribute("aria-selected", "true");
    const rows = within(panel).getAllByRole("row");
    expect(rows[1]).toHaveTextContent("kimKimActive");
    expect(rows[2]).toHaveTextContent("tomTomLocked");
    expect(within(panel).queryByRole("button", { name: "Add a driver" })).not.toBeInTheDocument();
    expect(within(panel).queryByRole("complementary")).not.toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "Pre-start checklists" })).not.toBeInTheDocument();
  });

  it("adds a driver, the code kept lower case", async () => {
    let added = false;
    const { calls } = fleetDepot({
      "POST /v1/drivers": () => { added = true; return json(201, person("sam-02", "Sam")); },
      "GET /v1/drivers": () => json(200, { items: [person("kim", "Kim"), ...(added ? [person("sam-02", "Sam")] : [])], total: 1 }),
    });
    renderApp("/fleet");
    const { user, panel } = await driversTab();
    await user.click(within(panel).getByRole("button", { name: "Add a driver" }));
    const form = within(panel).getByRole("form", { name: "Add a driver" });
    expect(within(form).getByLabelText("Code")).toHaveFocus();
    await user.type(within(form).getByLabelText("Code"), " SAM-02 ");
    await user.type(within(form).getByLabelText("Name"), "Sam");
    await user.type(within(form).getByLabelText("PIN"), "1357");
    await user.click(within(form).getByRole("button", { name: "Add the driver" }));
    expect(await screen.findByText("Sam (sam-02) is added. They sign in with sam-02 and the PIN you gave.")).toHaveAttribute("role", "status");
    expect(calls.find((c) => c.method === "POST")!.body).toEqual({ code: "sam-02", name: "Sam", pin: "1357" });
    await waitFor(() => expect(within(panel).getByRole("button", { name: "Show Sam (sam-02)" })).toHaveAttribute("aria-pressed", "true"));
  });

  it("refuses a code already taken and a short PIN beside each field", async () => {
    const { calls } = fleetDepot();
    renderApp("/fleet");
    const { user, panel } = await driversTab();
    await user.click(within(panel).getByRole("button", { name: "Add a driver" }));
    const form = within(panel).getByRole("form", { name: "Add a driver" });
    await user.type(within(form).getByLabelText("Code"), "KIM");
    await user.type(within(form).getByLabelText("PIN"), "12");
    await user.click(within(form).getByRole("button", { name: "Add the driver" }));
    expect(within(form).getByLabelText("Code")).toHaveAccessibleDescription("What they sign in with, in any case. Kept lower case. kim is already Kim's.");
    expect(within(form).getByLabelText("Name")).toHaveAccessibleDescription("Type the name.");
    expect(within(form).getByLabelText("PIN")).toHaveAccessibleDescription("4 to 8 digits. Tell the driver: it isn't shown again. A PIN is 4 to 8 digits.");
    expect(calls.some((c) => c.method === "POST")).toBe(false);
  });

  it("opens on a locked driver, and unlocks them or gives them a new PIN", async () => {
    const { calls } = fleetDepot({
      "POST /v1/drivers/tom/unlock": () => json(200, person("tom", "Tom")),
      "PATCH /v1/drivers/tom": () => json(200, person("tom", "Tom")),
    });
    renderApp("/fleet");
    const { user, panel } = await driversTab();
    const aside = await within(panel).findByRole("complementary", { name: "Driver tom" });
    expect(aside).toHaveTextContent("Locked after 5 wrong PINs.");
    await user.click(within(aside).getByRole("button", { name: "Unlock" }));
    expect(await screen.findByText("Tom is unlocked.")).toBeInTheDocument();

    const pin = within(aside).getByRole("form", { name: "A new PIN" });
    await user.type(within(pin).getByLabelText("New PIN"), "86420");
    await user.click(within(pin).getByRole("button", { name: "Set the PIN" }));
    expect(await screen.findByText("Tom has a new PIN, and is unlocked.")).toBeInTheDocument();
    expect(calls.filter((c) => c.method !== "GET").map((c) => [c.method, c.url, c.body])).toEqual([
      ["POST", "/v1/drivers/tom/unlock", {}], ["PATCH", "/v1/drivers/tom", { pin: "86420" }]]);
    expect(within(pin).getByLabelText("New PIN")).toHaveValue("");
  });

  it("renames a driver, and says which run holds one the API won't deactivate", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    const { calls } = fleetDepot({
      "PATCH /v1/drivers/kim": () => json(200, person("kim", "Kim Lee")),
      "POST /v1/drivers/kim/deactivate": () => json(409, { code: "on_a_run",
        detail: "Kim (kim) is on R-0001 (on the road now). Deactivate them once that run is finished." }),
    });
    renderApp("/fleet");
    const { user, panel } = await driversTab();
    await user.click(within(panel).getByRole("button", { name: "Show Kim (kim)" }));
    const aside = within(panel).getByRole("complementary", { name: "Driver kim" });
    const name = within(aside).getByRole("form", { name: "Name" });
    await user.clear(within(name).getByLabelText("Name"));
    await user.type(within(name).getByLabelText("Name"), "Kim Lee");
    await user.click(within(name).getByRole("button", { name: "Save name" }));
    expect(await screen.findByText("kim's name is now Kim Lee.")).toBeInTheDocument();
    expect(calls.find((c) => c.method === "PATCH")!.body).toEqual({ name: "Kim Lee" });

    await user.click(within(aside).getByRole("button", { name: "Deactivate" }));
    expect(confirm).toHaveBeenCalledWith("Deactivate Kim (kim)? They can't sign in until they are reactivated. Their runs and deliveries stay theirs.");
    expect(await within(aside).findByRole("alert")).toHaveTextContent("Kim (kim) is on R-0001 (on the road now). Deactivate them once that run is finished.");
    confirm.mockRestore();
  });
});
```

**Step 2: Run them to see them fail**

Run: `npx vitest run src/test/FleetDrivers.test.tsx`
Expected: `Tests  5 failed (5)`: `Unable to find role="tab" and name "Drivers"`.

**Step 3: Implement**

Create `apps/desktop/src/admin/Drivers.tsx`:
```tsx
import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { api } from "../api/client";
import type { Driver, Page } from "../api/types";
import { apiProblems, codeProblem, found, nameProblem, normalCode, pinProblem, said, type Problems } from "../lib/admin";
import { Field, focusFirstProblem } from "../ui/Field";
import { Icon } from "../ui/icons";

export type Say = (note: { tone: "r" | "g"; text: string } | null) => void;

function status(d: Driver): { text: string; tone: string } {
  if (!d.active) return { text: "Deactivated", tone: "grey" };
  if (d.locked) return { text: "Locked", tone: "red" };
  return { text: "Active", tone: "green" };
}

/** Add a driver: the code they sign in with, their name and a first PIN. */
function DriverForm({ drivers, onDone, onCancel }: {
  drivers: Driver[]; onDone: (text: string, code: string) => Promise<void>; onCancel: () => void;
}) {
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [pin, setPin] = useState("");
  const [problems, setProblems] = useState<Problems>({});
  const [trouble, setTrouble] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const form = useRef<HTMLFormElement>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setTrouble(null);
    const c = normalCode(code);
    const mine = found({ code: codeProblem(c, drivers), name: nameProblem(name, 120), pin: pinProblem(pin) });
    setProblems(mine);
    if (Object.keys(mine).length) { focusFirstProblem(form.current); return; }
    setBusy(true);
    try {
      const made = await api.post<Driver>("/v1/drivers", { code: c, name: name.trim(), pin });
      await onDone(`${made.name} (${made.code}) is added. They sign in with ${made.code} and the PIN you gave.`, made.code);
    } catch (err) {
      const theirs = apiProblems(err);
      if (theirs) { setProblems(theirs); focusFirstProblem(form.current); } else setTrouble(said(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form ref={form} className="adminform" aria-label="Add a driver" noValidate onSubmit={(e) => void submit(e)}>
      <div className="eyebrow">ADD A DRIVER</div>
      <div className="row">
        <Field label="Code" value={code} onChange={(e) => setCode(e.target.value)} error={problems.code} maxLength={40}
               spellCheck={false} autoFocus hint="What they sign in with, in any case. Kept lower case." />
        <Field label="Name" value={name} onChange={(e) => setName(e.target.value)} error={problems.name} maxLength={130} />
        <Field label="PIN" inputMode="numeric" value={pin} onChange={(e) => setPin(e.target.value)} error={problems.pin}
               maxLength={8} hint="4 to 8 digits. Tell the driver: it isn't shown again." />
      </div>
      {trouble && <div className="msg r" role="alert">{trouble}</div>}
      <div className="acts">
        <button type="submit" className="btn primary" disabled={busy}>{busy ? "Saving…" : "Add the driver"}</button>
        <button type="button" className="btn" onClick={onCancel} disabled={busy}>Cancel</button>
      </div>
    </form>
  );
}

/** One field and its button, sent on its own: a new name, or a new PIN. */
function OneField({ title, label, button, hint, initial = "", numeric, check, send }: {
  title: string; label: string; button: string; hint?: string; initial?: string; numeric?: boolean;
  check: (value: string) => string | null; send: (value: string) => Promise<void>;
}) {
  const [value, setValue] = useState(initial);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const form = useRef<HTMLFormElement>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    const mine = check(value);
    setProblem(mine);
    if (mine) { focusFirstProblem(form.current); return; }
    setBusy(true);
    try {
      await send(value);
      if (!initial) setValue("");
    } catch (err) {
      const theirs = apiProblems(err);
      setProblem(theirs ? Object.values(theirs).join("; ") : said(err));
      focusFirstProblem(form.current);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form ref={form} aria-label={title} noValidate onSubmit={(e) => void submit(e)} className="acts" style={{ alignItems: "flex-end" }}>
      <div style={{ flex: "1 1 160px" }}>
        <Field label={label} value={value} onChange={(e) => setValue(e.target.value)} error={problem} hint={hint}
               inputMode={numeric ? "numeric" : undefined} maxLength={numeric ? 8 : 130} />
      </div>
      <button type="submit" className="btn sm" disabled={busy}>{button}</button>
    </form>
  );
}

/** A fleet manager's controls for one driver. */
function DriverAside({ d, onChanged }: { d: Driver; onChanged: (text: string) => Promise<void> }) {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const who = `${d.name} (${d.code})`;

  async function act(path: string, done: string, ask?: string) {
    if (ask && !window.confirm(ask)) return;
    setError(null);
    setBusy(true);
    try {
      await api.post(`/v1/drivers/${d.code}/${path}`);
      await onChanged(done);
    } catch (e) {
      setError(said(e));
    } finally {
      setBusy(false);
    }
  }

  const pill = status(d);
  return (
    <aside className="aside" aria-label={`Driver ${d.code}`}>
      <div>
        <div className="eyebrow">DRIVER</div>
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <span style={{ fontSize: 22, fontWeight: 800 }}>{d.name}</span>
          <span className={`pill p-${pill.tone}`}>{pill.text}</span></div>
        <div className="small">Signs in as <b>{d.code}</b></div>
      </div>
      {error && <div className="msg r" role="alert">{error}</div>}
      {d.locked && (
        <div className="msg a" style={{ alignItems: "center", flexWrap: "wrap" }}>
          <span style={{ flex: 1 }}>Locked after {d.failed_attempts ?? 5} wrong PINs. Unlock keeps their PIN; a new PIN unlocks them too.</span>
          <button type="button" className="btn sm" disabled={busy} onClick={() => void act("unlock", `${d.name} is unlocked.`)}>Unlock</button>
        </div>
      )}
      <OneField title="Name" label="Name" button="Save name" initial={d.name} check={(v) => nameProblem(v, 120)}
                send={async (v) => { await api.patch(`/v1/drivers/${d.code}`, { name: v.trim() }); await onChanged(`${d.code}'s name is now ${v.trim()}.`); }} />
      <OneField title="A new PIN" label="New PIN" button="Set the PIN" numeric check={pinProblem}
                hint="Their phone asks them to sign in again with it; anything it hasn't sent waits until they do."
                send={async (v) => { await api.patch(`/v1/drivers/${d.code}`, { pin: v }); await onChanged(`${d.name} has a new PIN${d.locked ? ", and is unlocked" : ""}.`); }} />
      <div className="acts">
        {d.active
          ? <button type="button" className="btn sm" disabled={busy}
                    onClick={() => void act("deactivate", `${who} is deactivated.`,
                                            `Deactivate ${who}? They can't sign in until they are reactivated. Their runs and deliveries stay theirs.`)}>Deactivate</button>
          : <button type="button" className="btn sm" disabled={busy} onClick={() => void act("reactivate", `${who} is reactivated.`)}>Reactivate</button>}
      </div>
    </aside>
  );
}

/** The Drivers tab of Fleet: everyone sees who drives and who is locked
 * out; a fleet manager adds, renames, sets PINs, unlocks and deactivates. */
export function Drivers({ manager, say }: { manager: boolean; say: Say }) {
  const [drivers, setDrivers] = useState<Driver[] | null>(null);
  const [chosen, setChosen] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const addButton = useRef<HTMLButtonElement>(null);

  const load = useCallback(async () => {
    setDrivers((await api.get<Page<Driver>>("/v1/drivers")).items);
  }, []);

  useEffect(() => {
    load().catch((e) => say({ tone: "r", text: said(e) }));
  }, [load]);

  function closeAdd() {
    setAdding(false);
    setTimeout(() => addButton.current?.focus(), 0);
  }

  async function changed(text: string, code?: string) {
    if (code) setChosen(code);
    say({ tone: "g", text });
    await load().catch(() => undefined);
  }

  const all = drivers ?? [];
  const selected = manager ? all.find((d) => d.code === chosen) ?? all.find((d) => d.locked && d.active) ?? all[0] ?? null : null;
  return (
    <>
      {manager && (
        <div className="tools">
          <button ref={addButton} type="button" className="btn primary" aria-expanded={adding} onClick={() => setAdding((a) => !a)}>
            <Icon name="plus" size={16} width={2.6} />Add a driver</button>
        </div>)}
      {adding && <DriverForm drivers={all} onCancel={closeAdd} onDone={async (text, code) => { closeAdd(); await changed(text, code); }} />}
      <div className="split">
        <div className="tablewrap">
          <table>
            <thead><tr><th>CODE</th><th>NAME</th><th>STATUS</th></tr></thead>
            <tbody>
              {all.map((d) => {
                const on = selected?.code === d.code;
                const pill = status(d);
                return (
                  <tr key={d.code} className={on ? "sel" : undefined}>
                    <td>{manager
                      ? <button type="button" className="rowbtn" aria-pressed={on} aria-label={`Show ${d.name} (${d.code})`} onClick={() => setChosen(d.code)}>{d.code}</button>
                      : <b style={{ padding: "0 10px" }}>{d.code}</b>}</td>
                    <td>{d.name}</td>
                    <td><span className={`pill p-${pill.tone}`}>{pill.text}</span></td>
                  </tr>
                );
              })}
              {drivers?.length === 0 && (
                <tr><td colSpan={3} className="small">{manager ? "No drivers yet. Add each one with Add a driver." : "No drivers yet. A fleet manager adds them here."}</td></tr>)}
            </tbody>
          </table>
        </div>
        {selected && <DriverAside key={selected.code} d={selected} onChanged={(text) => changed(text, selected.code)} />}
      </div>
      <div className="small">A driver signs in to the driver app with their code, in any case, and their PIN. Five wrong PINs lock them out until a fleet manager unlocks them or gives them a new PIN.</div>
    </>
  );
}
```

Change `apps/desktop/src/pages/Fleet.tsx` (the vehicles' content moves, as it is, into its tab panel):
```diff
--- a/apps/desktop/src/pages/Fleet.tsx
+++ b/apps/desktop/src/pages/Fleet.tsx
@@ -1,5 +1,6 @@
 import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
 import { useOutletContext } from "react-router-dom";
+import { Drivers } from "../admin/Drivers";
 import { VehicleForm } from "../admin/VehicleForm";
 import { api, ApiError, Unreachable } from "../api/client";
 import type { HistoryItem, Page, PrestartDetail, Run, Vehicle } from "../api/types";
@@ -239,6 +240,8 @@ function VehicleAside({ v, vehicles, driverName, onChanged }: {
   );
 }
 
+type Tab = "vehicles" | "drivers" | "checklists";
+
 /** Fleet.html: the trucks, whether each can go, the pre-start that
  * grounded one, and what a fleet manager records. */
 export function Fleet() {
@@ -254,6 +257,8 @@ export function Fleet() {
   const [chosen, setChosen] = useState<string | null>(null);
   const [note, setNote] = useState<Note>(null);
   const [adding, setAdding] = useState(false);
+  const [tab, setTab] = useState<Tab>("vehicles");
+  const tabs: [Tab, string][] = [["vehicles", "Vehicles"], ["drivers", "Drivers"]];
   const addButton = useRef<HTMLButtonElement>(null);
 
   const load = useCallback(async () => {
@@ -303,66 +308,79 @@ export function Fleet() {
       <div style={{ flex: 1, minWidth: 200 }}>
         <div className="eyebrow" style={{ color: "var(--color-green)" }}>Own fleet</div>
         <h1>Fleet</h1>
-        <div className="sub">Your trucks, pre-start checks, and when rego and service fall due.</div>
+        <div className="sub">Your trucks, who drives them, pre-start checks, and when rego and service fall due.</div>
       </div>
     }>
-      <div className="tools">
-        <label className="search" style={{ flex: "0 1 420px" }}><Icon name="search" />
-          <span className="sr">Search vehicles</span>
-          <input type="search" placeholder="Rego or driver" value={q} onChange={(e) => setQ(e.target.value)} /></label>
-        {manager && (
-          <button ref={addButton} type="button" className="btn primary" aria-expanded={adding} onClick={() => setAdding((a) => !a)}>
-            <Icon name="plus" size={16} width={2.6} />Add a vehicle</button>)}
+      <div className="tabs" role="tablist" aria-label="Fleet">
+        {tabs.map(([key, label]) => (
+          <button key={key} type="button" className="tab" role="tab" id={`fleet-tab-${key}`} aria-selected={tab === key}
+                  aria-controls={`fleet-${key}`} onClick={() => { setTab(key); setNote(null); }}>{label}</button>))}
       </div>
-      {adding && (
-        <VehicleForm vehicles={all} onCancel={closeAdd}
-                     onDone={async (text, rego) => { closeAdd(); setChosen(rego); await changed({ tone: "g", text }); }} />)}
       {note && <div className={`msg ${note.tone}`} role={note.tone === "r" ? "alert" : "status"}>{note.text}</div>}
-      <div className="stats">
-        <div className="stat"><div className="eyebrow">VEHICLES</div><div className="v num">{active.length}</div>
-          <div className="d">{types.map(([t, n]) => `${n} ${t}`).join(" · ")}</div></div>
-        <div className={`stat${grounded.length ? " red" : ""}`}><div className="eyebrow">GROUNDED</div><div className="v num">{grounded.length}</div>
-          <div className="d">{grounded.map((v) => `${v.rego} · ${v.grounded?.failed.join(", ").toLowerCase()}`).join("; ") || "None"}</div></div>
-        {overdue.length > 0 && (
-          <div className="stat red"><div className="eyebrow">OVERDUE</div><div className="v num">{overdue.length}</div>
-            <div className="d">{overdue.map((v) => v.rego).join(", ")}</div></div>)}
-        <div className={`stat${due.length ? " amber" : ""}`}><div className="eyebrow">DUE SOON</div><div className="v num">{due.length}</div>
-          <div className="d">{due.map((v) => `${v.rego} ${v.rego_due?.state === "due" ? "rego" : "service"}`).join(" · ") || "Nothing in the next 30 days"}</div></div>
-        <div className="stat"><div className="eyebrow">PRE-STARTS TODAY</div><div className="v num">{checked.length}</div>
-          <div className="d">{checked.filter((v) => v.last_prestart!.result === "passed").length} passed · {checked.filter((v) => v.last_prestart!.result !== "passed").length} failed</div></div>
-      </div>
-      <div className="split">
-        <div className="tablewrap">
-          <table style={{ minWidth: 720 }}>
-            <thead><tr><th>REGO</th><th>TYPE</th><th>DRIVER</th><th>REGO EXPIRY</th><th>NEXT SERVICE</th><th>LAST PRE-START</th><th>STATUS</th></tr></thead>
-            <tbody>
-              {shown.map((v) => {
-                const run = runOf(v.rego);
-                const pill = statusPill(v, run?.status === "departed");
-                const on = selected?.rego === v.rego;
-                return (
-                  <tr key={v.rego} className={on ? `sel${v.state === "grounded" || v.state === "overdue" ? " bad" : ""}` : undefined}>
-                    <td><button type="button" className="rowbtn" aria-pressed={on} aria-label={`Show ${v.rego}`} onClick={() => setChosen(v.rego)}>{v.rego}</button></td>
-                    <td style={{ textTransform: "capitalize" }}>{v.type}</td>
-                    <td>{run?.driver.name ?? <span className="small">No run today</span>}</td>
-                    <CellOut c={regoCell(v)} />
-                    <CellOut c={serviceCell(v)} />
-                    <CellOut c={checkCell(v)} />
-                    <td><span className={`pill p-${pill.tone}`}>{pill.text}</span></td>
-                  </tr>
-                );
-              })}
-              {vehicles?.length === 0 && (
-                <tr><td colSpan={7} className="small">{manager ? "No vehicles yet. Add your trucks with Add a vehicle." : "No vehicles yet. A fleet manager adds them here."}</td></tr>)}
-            </tbody>
-          </table>
+      {tab === "drivers" && (
+        <div role="tabpanel" id="fleet-drivers" aria-labelledby="fleet-tab-drivers" className="tabpanel">
+          <Drivers manager={manager} say={setNote} />
+        </div>)}
+      {tab === "vehicles" && (
+        <div role="tabpanel" id="fleet-vehicles" aria-labelledby="fleet-tab-vehicles" className="tabpanel">
+          <div className="tools">
+            <label className="search" style={{ flex: "0 1 420px" }}><Icon name="search" />
+              <span className="sr">Search vehicles</span>
+              <input type="search" placeholder="Rego or driver" value={q} onChange={(e) => setQ(e.target.value)} /></label>
+            {manager && (
+              <button ref={addButton} type="button" className="btn primary" aria-expanded={adding} onClick={() => setAdding((a) => !a)}>
+                <Icon name="plus" size={16} width={2.6} />Add a vehicle</button>)}
+          </div>
+          {adding && (
+            <VehicleForm vehicles={all} onCancel={closeAdd}
+                         onDone={async (text, rego) => { closeAdd(); setChosen(rego); await changed({ tone: "g", text }); }} />)}
+          <div className="stats">
+            <div className="stat"><div className="eyebrow">VEHICLES</div><div className="v num">{active.length}</div>
+              <div className="d">{types.map(([t, n]) => `${n} ${t}`).join(" · ")}</div></div>
+            <div className={`stat${grounded.length ? " red" : ""}`}><div className="eyebrow">GROUNDED</div><div className="v num">{grounded.length}</div>
+              <div className="d">{grounded.map((v) => `${v.rego} · ${v.grounded?.failed.join(", ").toLowerCase()}`).join("; ") || "None"}</div></div>
+            {overdue.length > 0 && (
+              <div className="stat red"><div className="eyebrow">OVERDUE</div><div className="v num">{overdue.length}</div>
+                <div className="d">{overdue.map((v) => v.rego).join(", ")}</div></div>)}
+            <div className={`stat${due.length ? " amber" : ""}`}><div className="eyebrow">DUE SOON</div><div className="v num">{due.length}</div>
+              <div className="d">{due.map((v) => `${v.rego} ${v.rego_due?.state === "due" ? "rego" : "service"}`).join(" · ") || "Nothing in the next 30 days"}</div></div>
+            <div className="stat"><div className="eyebrow">PRE-STARTS TODAY</div><div className="v num">{checked.length}</div>
+              <div className="d">{checked.filter((v) => v.last_prestart!.result === "passed").length} passed · {checked.filter((v) => v.last_prestart!.result !== "passed").length} failed</div></div>
+          </div>
+          <div className="split">
+            <div className="tablewrap">
+              <table style={{ minWidth: 720 }}>
+                <thead><tr><th>REGO</th><th>TYPE</th><th>DRIVER</th><th>REGO EXPIRY</th><th>NEXT SERVICE</th><th>LAST PRE-START</th><th>STATUS</th></tr></thead>
+                <tbody>
+                  {shown.map((v) => {
+                    const run = runOf(v.rego);
+                    const pill = statusPill(v, run?.status === "departed");
+                    const on = selected?.rego === v.rego;
+                    return (
+                      <tr key={v.rego} className={on ? `sel${v.state === "grounded" || v.state === "overdue" ? " bad" : ""}` : undefined}>
+                        <td><button type="button" className="rowbtn" aria-pressed={on} aria-label={`Show ${v.rego}`} onClick={() => setChosen(v.rego)}>{v.rego}</button></td>
+                        <td style={{ textTransform: "capitalize" }}>{v.type}</td>
+                        <td>{run?.driver.name ?? <span className="small">No run today</span>}</td>
+                        <CellOut c={regoCell(v)} />
+                        <CellOut c={serviceCell(v)} />
+                        <CellOut c={checkCell(v)} />
+                        <td><span className={`pill p-${pill.tone}`}>{pill.text}</span></td>
+                      </tr>
+                    );
+                  })}
+                  {vehicles?.length === 0 && (
+                    <tr><td colSpan={7} className="small">{manager ? "No vehicles yet. Add your trucks with Add a vehicle." : "No vehicles yet. A fleet manager adds them here."}</td></tr>)}
+                </tbody>
+              </table>
+            </div>
+            {/* stay on the truck it changed: once fixed, it's no longer the grounded one shown by default */}
+            {selected && <VehicleAside key={selected.rego} v={selected} vehicles={all} driverName={runOf(selected.rego)?.driver.name ?? null}
+                                       onChanged={(n) => { setChosen(selected.rego); return changed(n); }} />}
+          </div>
+          <div className="small">Amber 30 days or 1,000 km before it's due, red once it's overdue. A grounded or overdue truck can't take a run.
+            Due dates show here only.</div>
         </div>
-        {/* stay on the truck it changed: once fixed, it's no longer the grounded one shown by default */}
-        {selected && <VehicleAside key={selected.rego} v={selected} vehicles={all} driverName={runOf(selected.rego)?.driver.name ?? null}
-                                   onChanged={(n) => { setChosen(selected.rego); return changed(n); }} />}
-      </div>
-      <div className="small">Amber 30 days or 1,000 km before it's due, red once it's overdue. A grounded or overdue truck can't take a run.
-        Due dates show here only.</div>
+      )}
     </Drawer>
   );
 }
```

Change `apps/desktop/src/index.css`:
```diff
--- a/apps/desktop/src/index.css
+++ b/apps/desktop/src/index.css
@@ -210,6 +210,7 @@
   .grp tr.sel .rowbtn { border-color: var(--color-green); background: var(--color-tint); }
 
   /* the admin forms: no drawn design, so the drawers' own fields and boxes */
+  .tabpanel { display: flex; flex-direction: column; gap: 16px; }
   .tools { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; }
   .adminform { padding: 16px; border-radius: 16px; border: 1px solid var(--color-card-line); background: #fff; display: flex; flex-direction: column; gap: 12px; }
   .adminform .row { display: flex; flex-wrap: wrap; gap: 12px; }
```

**Step 4: Run them, then the suite**

Run: `npx vitest run src/test/FleetDrivers.test.tsx`
Expected: `Tests  5 passed (5)`.
Run: `npm test && npm run typecheck`
Expected: `Tests  144 passed (144)` (Fleet's earlier tests still pass on the Vehicles tab), and no type errors.

**Step 5: Commit**

```bash
git add apps/desktop/src/admin/Drivers.tsx apps/desktop/src/pages/Fleet.tsx apps/desktop/src/index.css \
        apps/desktop/src/test/FleetDrivers.test.tsx
git commit -m "Fleet: a Drivers tab to add, rename, set a PIN, unlock and deactivate"
```

### Task 7: The Pre-start checklists tab

Decision 5. A fleet manager's only. Moving a line keeps the focus on it (on its other arrow at the top or bottom), taking one out moves the focus to the next line, and **Add a line** puts it in the new one.

**Files:**
- Create: `apps/desktop/src/admin/Checklists.tsx`
- Modify: `apps/desktop/src/pages/Fleet.tsx`, `apps/desktop/src/index.css`
- Test: `apps/desktop/src/test/FleetChecklists.test.tsx` (new)

**Step 1: Write the failing tests**

Create `apps/desktop/src/test/FleetChecklists.test.tsx`:
```tsx
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { fleetDepot, VAN_LIST } from "./fleetDepot";
import { json, renderApp } from "./harness";

async function checklists() {
  const user = userEvent.setup();
  await user.click(await screen.findByRole("tab", { name: "Pre-start checklists" }));
  const panel = screen.getByRole("tabpanel", { name: "Pre-start checklists" });
  const list = await within(panel).findByRole("region", { name: "Van checklist" });
  return { user, panel, list };
}

const labels = (list: HTMLElement) => within(list).getAllByRole("textbox").map((i) => (i as HTMLInputElement).value);

describe("Fleet: pre-start checklists", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("shows each type's lines in order, the critical ones ticked", async () => {
    fleetDepot();
    renderApp("/fleet");
    const { list } = await checklists();
    expect(labels(list)).toEqual(["Tyres", "Lights", "Brakes", "Mirrors", "Load restraints", "Damage"]);
    expect(within(list).getByRole("checkbox", { name: "Critical (Lights)" })).toBeChecked();
    expect(within(list).getByRole("checkbox", { name: "Critical (Tyres)" })).not.toBeChecked();
    expect(within(list).getByRole("button", { name: "Save the van checklist" })).toBeDisabled();
    expect(within(list).getByRole("button", { name: "Move Tyres up" })).toBeDisabled();
  });

  it("adds, moves and takes out lines, then sends the whole list", async () => {
    const { calls } = fleetDepot({
      "PUT /v1/checklists/van": (c) => json(200, (c.body as { items: { label: string; critical: boolean }[] }).items
        .map((l, i) => ({ id: 100 + i, ...l }))),
    });
    renderApp("/fleet");
    const { user, panel, list } = await checklists();
    await user.click(within(list).getByRole("button", { name: "Add a line" }));
    await waitFor(() => expect(within(list).getByRole("textbox", { name: "Line 7" })).toHaveFocus());
    await user.keyboard("Tail lift");
    await user.click(within(list).getByRole("checkbox", { name: "Critical (Tail lift)" }));
    await user.click(within(list).getByRole("button", { name: "Move Tail lift up" }));
    await waitFor(() => expect(within(list).getByRole("button", { name: "Move Tail lift up" })).toHaveFocus());
    await user.click(within(list).getByRole("button", { name: "Take out Mirrors" }));
    expect(labels(list)).toEqual(["Tyres", "Lights", "Brakes", "Load restraints", "Tail lift", "Damage"]);
    expect(within(panel).getByRole("button", { name: /^Van/ })).toHaveTextContent("not saved");

    await user.click(within(list).getByRole("button", { name: "Save the van checklist" }));
    expect(await screen.findByText("The van checklist is saved: 6 lines.")).toHaveAttribute("role", "status");
    expect(calls.find((c) => c.method === "PUT")!.body).toEqual({ items: [
      { label: "Tyres", critical: false }, { label: "Lights", critical: true }, { label: "Brakes", critical: true },
      { label: "Load restraints", critical: false }, { label: "Tail lift", critical: true }, { label: "Damage", critical: false }] });
    expect(within(list).getByRole("button", { name: "Save the van checklist" })).toBeDisabled();
  });

  it("keeps each type's changes while another is looked at, and puts one back", async () => {
    fleetDepot();
    renderApp("/fleet");
    const { user, panel, list } = await checklists();
    await user.click(within(list).getByRole("button", { name: "Take out Damage" }));
    await user.click(within(panel).getByRole("button", { name: "Rigid" }));
    const rigid = within(panel).getByRole("region", { name: "Rigid checklist" });
    expect(labels(rigid)).toHaveLength(6);
    await user.click(within(panel).getByRole("button", { name: /^Van/ }));
    const van = within(panel).getByRole("region", { name: "Van checklist" });
    expect(labels(van)).toHaveLength(5);
    await user.click(within(van).getByRole("button", { name: "Put back as it was" }));
    expect(labels(van)).toEqual(VAN_LIST.map((l) => l.label));
  });

  it("says beside a line what is wrong with it, and sends nothing", async () => {
    const { calls } = fleetDepot();
    renderApp("/fleet");
    const { user, list } = await checklists();
    await user.clear(within(list).getByRole("textbox", { name: "Line 4" }));
    await user.type(within(list).getByRole("textbox", { name: "Line 4" }), "tyres");
    await user.click(within(list).getByRole("button", { name: "Add a line" }));
    await user.click(within(list).getByRole("button", { name: "Save the van checklist" }));
    expect(within(list).getByRole("textbox", { name: "Line 4" })).toHaveAccessibleDescription("Line 1 says this already.");
    expect(within(list).getByRole("textbox", { name: "Line 7" })).toHaveAccessibleDescription("Type what to check, or take the line out.");
    await waitFor(() => expect(within(list).getByRole("textbox", { name: "Line 4" })).toHaveFocus());
    expect(calls.some((c) => c.method === "PUT")).toBe(false);
  });
});
```

**Step 2: Run them to see them fail**

Run: `npx vitest run src/test/FleetChecklists.test.tsx`
Expected: `Tests  4 failed (4)`: `Unable to find role="tab" and name "Pre-start checklists"`.

**Step 3: Implement**

Create `apps/desktop/src/admin/Checklists.tsx`:
```tsx
import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "../api/client";
import type { ChecklistItem, Checklists as Lists } from "../api/types";
import { checklistProblems, said, typeName, VEHICLE_TYPES, type VehicleType } from "../lib/admin";
import { Icon } from "../ui/icons";
import type { Say } from "./Drivers";

interface Row { key: string; id?: number; label: string; critical: boolean }

let next = 0;
const fresh = (): Row => ({ key: `new-${++next}`, label: "", critical: false });
const rows = (items: ChecklistItem[]): Row[] => items.map((i) => ({ key: `id-${i.id}`, id: i.id, label: i.label, critical: i.critical }));
const same = (a: Row[], b: ChecklistItem[]) =>
  a.length === b.length && a.every((r, i) => r.label.trim() === b[i].label && r.critical === b[i].critical);

/** The Pre-start checklists tab of Fleet, for a fleet manager: each type's
 * lines in order, each critical or not. Saving sends the type's whole list
 * (PUT /v1/checklists/{type}); a line kept as it was keeps its id, and one
 * taken out or reworded is retired, never deleted. */
export function Checklists({ say }: { say: Say }) {
  const [saved, setSaved] = useState<Lists | null>(null);
  const [type, setType] = useState<VehicleType>("van");
  const [drafts, setDrafts] = useState<Partial<Record<VehicleType, Row[]>>>({});
  const [checked, setChecked] = useState(false);
  const [trouble, setTrouble] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const list = useRef<HTMLOListElement>(null);

  useEffect(() => {
    api.get<Lists>("/v1/checklists").then(setSaved).catch((e) => say({ tone: "r", text: said(e) }));
  }, []);

  if (!saved) return null;
  const lines = drafts[type] ?? rows(saved[type]);
  const changed = (t: VehicleType) => drafts[t] !== undefined && !same(drafts[t]!, saved[t]);
  const problems = checklistProblems(lines);

  function edit(change: (rows: Row[]) => Row[]) {
    setDrafts((d) => ({ ...d, [type]: change(d[type] ?? rows(saved![type])) }));
    setTrouble(null);
  }

  /** after the list is drawn again: focus one of a row's controls */
  function focus(key: string, what: string) {
    setTimeout(() => list.current?.querySelector<HTMLElement>(`[data-key="${key}"] [data-act="${what}"]`)?.focus(), 0);
  }

  function move(i: number, by: -1 | 1) {
    const key = lines[i].key;
    edit((rs) => { const out = [...rs]; [out[i], out[i + by]] = [out[i + by], out[i]]; return out; });
    // at the end, the button pressed goes: stay on the row by its other one
    const lands = i + by;
    focus(key, (by === -1 && lands === 0) ? "down" : (by === 1 && lands === lines.length - 1) ? "up" : by === -1 ? "up" : "down");
  }

  function takeOut(i: number) {
    const after = lines[i + 1] ?? lines[i - 1];
    edit((rs) => rs.filter((_, n) => n !== i));
    if (after) focus(after.key, "label");
  }

  function add() {
    const row = fresh();
    edit((rs) => [...rs, row]);
    focus(row.key, "label");
  }

  function choose(t: VehicleType) {
    setType(t);
    setChecked(false);
    setTrouble(null);
  }

  async function save() {
    setChecked(true);
    if (problems.list || problems.lines.some(Boolean)) {
      setTimeout(() => list.current?.querySelector<HTMLElement>('[aria-invalid="true"]')?.focus(), 0);
      return;
    }
    setBusy(true);
    try {
      const items = await api.put<ChecklistItem[]>(`/v1/checklists/${type}`,
                                                    { items: lines.map((l) => ({ label: l.label.trim(), critical: l.critical })) });
      setSaved((s) => ({ ...s!, [type]: items }));
      setDrafts((d) => ({ ...d, [type]: undefined }));
      setChecked(false);
      say({ tone: "g", text: `The ${type} checklist is saved: ${items.length} ${items.length === 1 ? "line" : "lines"}.` });
    } catch (e) {
      setTrouble(e instanceof ApiError && e.errors.length ? e.errors.map((x) => x.message).join("; ") : said(e));
    } finally {
      setBusy(false);
    }
  }

  const show = checked ? problems : { lines: lines.map(() => null), list: null };
  return (
    <>
      <div className="small">Before each run the driver answers every line. A failed critical line grounds the truck until a fleet
        manager marks it fixed. A line taken out, or reworded, is retired: checks already done keep it as it was. A driver part way
        through a check when you save is asked to do it again.</div>
      <div className="tabs" role="group" aria-label="Checklist for">
        {VEHICLE_TYPES.map((t) => (
          <button key={t} type="button" className="tab" aria-pressed={t === type} onClick={() => choose(t)}>
            {typeName(t)}{changed(t) && <i className="a">not saved</i>}</button>))}
      </div>
      <section className="adminform" aria-label={`${typeName(type)} checklist`}>
        <div className="eyebrow">{typeName(type)} checklist · {lines.length} {lines.length === 1 ? "line" : "lines"}</div>
        <ol ref={list} className="lines">
          {lines.map((l, i) => {
            const err = show.lines[i];
            const name = l.label.trim() || `line ${i + 1}`;
            return (
              <li key={l.key} data-key={l.key}>
                <span className="n" aria-hidden="true">{i + 1}</span>
                <div className="fld" style={{ flex: "1 1 220px" }}>
                  <input className="field" data-act="label" aria-label={`Line ${i + 1}`} value={l.label} maxLength={90}
                         aria-invalid={err ? true : undefined} aria-describedby={err ? `${l.key}-err` : undefined}
                         onChange={(e) => edit((rs) => rs.map((r) => (r.key === l.key ? { ...r, label: e.target.value } : r)))} />
                  {err && <div id={`${l.key}-err`} className="ferr">{err}</div>}
                </div>
                <label className="crit"><input type="checkbox" checked={l.critical}
                  onChange={(e) => edit((rs) => rs.map((r) => (r.key === l.key ? { ...r, critical: e.target.checked } : r)))} />
                  Critical<span className="sr"> ({name})</span></label>
                <div className="acts">
                  <button type="button" className="btn sm" data-act="up" aria-label={`Move ${name} up`} disabled={i === 0} onClick={() => move(i, -1)}>
                    <Icon name="up" size={16} /></button>
                  <button type="button" className="btn sm" data-act="down" aria-label={`Move ${name} down`} disabled={i === lines.length - 1} onClick={() => move(i, 1)}>
                    <Icon name="down" size={16} /></button>
                  <button type="button" className="btn sm" aria-label={`Take out ${name}`} onClick={() => takeOut(i)}>
                    <Icon name="close" size={16} /></button>
                </div>
              </li>
            );
          })}
        </ol>
        {show.list && <div className="msg r" role="alert">{show.list}</div>}
        {trouble && <div className="msg r" role="alert">{trouble}</div>}
        <div className="acts">
          <button type="button" className="btn" onClick={add} disabled={lines.length >= 30}><Icon name="plus" size={16} width={2.6} />Add a line</button>
          <button type="button" className="btn primary" disabled={busy || !changed(type)} onClick={() => void save()}>
            {busy ? "Saving…" : `Save the ${type} checklist`}</button>
          <button type="button" className="btn" disabled={busy || drafts[type] === undefined}
                  onClick={() => { setDrafts((d) => ({ ...d, [type]: undefined })); setChecked(false); setTrouble(null); }}>Put back as it was</button>
        </div>
      </section>
    </>
  );
}
```

Change `apps/desktop/src/pages/Fleet.tsx`:
```diff
--- a/apps/desktop/src/pages/Fleet.tsx
+++ b/apps/desktop/src/pages/Fleet.tsx
@@ -1,5 +1,6 @@
 import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
 import { useOutletContext } from "react-router-dom";
+import { Checklists } from "../admin/Checklists";
 import { Drivers } from "../admin/Drivers";
 import { VehicleForm } from "../admin/VehicleForm";
 import { api, ApiError, Unreachable } from "../api/client";
@@ -258,7 +259,8 @@ export function Fleet() {
   const [note, setNote] = useState<Note>(null);
   const [adding, setAdding] = useState(false);
   const [tab, setTab] = useState<Tab>("vehicles");
-  const tabs: [Tab, string][] = [["vehicles", "Vehicles"], ["drivers", "Drivers"]];
+  const tabs: [Tab, string][] = [["vehicles", "Vehicles"], ["drivers", "Drivers"],
+                                 ...(manager ? [["checklists", "Pre-start checklists"] as [Tab, string]] : [])];
   const addButton = useRef<HTMLButtonElement>(null);
 
   const load = useCallback(async () => {
@@ -321,6 +323,10 @@ export function Fleet() {
         <div role="tabpanel" id="fleet-drivers" aria-labelledby="fleet-tab-drivers" className="tabpanel">
           <Drivers manager={manager} say={setNote} />
         </div>)}
+      {tab === "checklists" && manager && (
+        <div role="tabpanel" id="fleet-checklists" aria-labelledby="fleet-tab-checklists" className="tabpanel">
+          <Checklists say={setNote} />
+        </div>)}
       {tab === "vehicles" && (
         <div role="tabpanel" id="fleet-vehicles" aria-labelledby="fleet-tab-vehicles" className="tabpanel">
           <div className="tools">
```

Change `apps/desktop/src/index.css`:
```diff
--- a/apps/desktop/src/index.css
+++ b/apps/desktop/src/index.css
@@ -219,6 +219,12 @@
   .fld { display: flex; flex-direction: column; gap: 4px; font-size: 13px; font-weight: 700; min-width: 0; }
   .fld .small { font-weight: 400; }
   .ferr { font-size: 12px; font-weight: 700; color: var(--color-red-ink); }
+  .lines { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 8px; }
+  .lines li { display: flex; flex-wrap: wrap; align-items: flex-start; gap: 8px 10px; }
+  .lines .n { width: 24px; height: 44px; display: grid; place-items: center; font-weight: 800; color: var(--color-muted); flex: none; }
+  .lines .btn.sm { width: 44px; height: 44px; padding: 0; }
+  .crit { display: flex; align-items: center; gap: 6px; height: 44px; font-size: 13px; font-weight: 700; cursor: pointer; }
+  .crit input { width: 18px; height: 18px; accent-color: var(--color-green); }
   .field[aria-invalid="true"] { border-color: var(--color-red); box-shadow: 0 0 0 1px var(--color-red); }
 
   /* the Consignments drawer (Consignments.html) */
```

**Step 4: Run them, then the suite**

Run: `npx vitest run src/test/FleetChecklists.test.tsx`
Expected: `Tests  4 passed (4)`.
Run: `npm test && npm run typecheck`
Expected: `Tests  148 passed (148)`, and no type errors.

**Step 5: Commit**

```bash
git add apps/desktop/src/admin/Checklists.tsx apps/desktop/src/pages/Fleet.tsx apps/desktop/src/index.css \
        apps/desktop/src/test/FleetChecklists.test.tsx
git commit -m "Fleet: edit each type's pre-start checklist"
```

### Task 8: Your carriers, in Carriers

Decisions 1, 6, 7 and 8. At the bottom of **Carriers**, for a fleet manager, in the drawer's own group style. The "no carriers yet" line no longer points at the API: a fleet manager is sent to **Your carriers**, a dispatcher told a fleet manager adds them there (the existing Carriers test still finds "No carriers yet. A fleet manager adds them").

**Files:**
- Create: `apps/desktop/src/admin/YourCarriers.tsx`
- Modify: `apps/desktop/src/pages/Carriers.tsx`
- Test: `apps/desktop/src/test/CarriersAdmin.test.tsx` (new)

**Step 1: Write the failing tests**

Create `apps/desktop/src/test/CarriersAdmin.test.tsx`:
```tsx
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { Carrier } from "../api/types";
import { PLAINS, RIDGELINE, VALLEY } from "./bay";
import { DISPATCHER, json, MANAGER, path, renderApp, stubApi, type Call } from "./harness";

const carrier = (id: number, name: string, over: Partial<Carrier> = {}): Carrier => ({
  id, name, account: null, pickup_time: null, adapter: "manual", active: true, created_at: "", ...over,
});
const RX = carrier(1, RIDGELINE, { account: "RX-4410", pickup_time: "15:30" });
const PP = carrier(3, PLAINS, { active: false });

function office(answers: Record<string, (c: Call) => Response> = {}, who: object = MANAGER, all: () => Carrier[] = () => [RX, PP]) {
  return stubApi((method, url, body) => {
    const key = `${method} ${path(url)}`;
    if (answers[key]) return answers[key]({ method, url, body });
    if (key === "GET /v1/manifests") return json(200, { items: [], total: 0 });
    if (key === "GET /v1/carriers") {
      const items = url.includes("active=true") ? all().filter((c) => c.active) : all();
      return json(200, { items, total: items.length });
    }
    return null;
  }, who);
}

const yours = () => screen.findByRole("region", { name: "Your carriers" });

describe("Carriers: your carriers", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("is for a fleet manager: a dispatcher doesn't see it", async () => {
    office({}, DISPATCHER);
    renderApp("/carriers");
    expect(await screen.findByText(/Nothing is waiting for a carrier/)).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Your carriers" })).not.toBeInTheDocument();
  });

  it("lists every carrier, retired ones too, with how each is booked", async () => {
    office();
    renderApp("/carriers");
    const list = await yours();
    await within(list).findByText(RIDGELINE);
    const rows = within(list).getAllByRole("row");
    expect(rows[1]).toHaveTextContent(`${RIDGELINE}RX-44103:30 pmmanualActive`);
    expect(rows[2]).toHaveTextContent(`${PLAINS}NoneNot setmanualRetired`);
    expect(within(list).getByRole("button", { name: `Bring back ${PLAINS}` })).toBeInTheDocument();
  });

  it("adds a carrier, and the con note box's list gets it", async () => {
    let added = false;
    const { calls } = office({ "POST /v1/carriers": () => { added = true; return json(201, carrier(2, VALLEY)); } }, MANAGER,
                             () => (added ? [RX, carrier(2, VALLEY), PP] : [RX, PP]));
    renderApp("/carriers");
    const user = userEvent.setup();
    const list = await yours();
    await user.click(within(list).getByRole("button", { name: "Add a carrier" }));
    const form = within(list).getByRole("form", { name: "Add a carrier" });
    expect(within(form).getByLabelText("Name")).toHaveFocus();
    expect(form).toHaveTextContent("Booked: manual, by hand");
    await user.type(within(form).getByLabelText("Name"), ` ${VALLEY} `);
    await user.type(within(form).getByLabelText("Usually calls at"), "14:00");
    await user.click(within(form).getByRole("button", { name: "Add the carrier" }));
    expect(await screen.findByText(`${VALLEY} is added. Send consignments with it from Plan runs.`)).toHaveAttribute("role", "status");
    expect(calls.find((c) => c.method === "POST")!.body).toEqual({ name: VALLEY, account: null, pickup_time: "14:00" });
    // the drawer's own list of active carriers is asked for again
    await waitFor(() => expect(calls.filter((c) => c.url.startsWith("/v1/carriers?active=true")).length).toBeGreaterThan(1));
    expect(await within(list).findByText(VALLEY)).toBeInTheDocument();
  });

  it("refuses a name already on file whatever its case, and Own fleet", async () => {
    const { calls } = office();
    renderApp("/carriers");
    const user = userEvent.setup();
    const list = await yours();
    await user.click(within(list).getByRole("button", { name: "Add a carrier" }));
    const form = within(list).getByRole("form", { name: "Add a carrier" });
    await user.type(within(form).getByLabelText("Name"), RIDGELINE.toUpperCase());
    await user.click(within(form).getByRole("button", { name: "Add the carrier" }));
    expect(within(form).getByLabelText("Name")).toHaveAccessibleDescription(
      `What Simple WMS is told the freight went with. ${RIDGELINE} is already on file.`);
    await user.clear(within(form).getByLabelText("Name"));
    await user.type(within(form).getByLabelText("Name"), "own fleet");
    await user.click(within(form).getByRole("button", { name: "Add the carrier" }));
    expect(within(form).getByLabelText("Name")).toHaveAccessibleDescription(
      "What Simple WMS is told the freight went with. Own fleet is what Simple WMS is told for your own trucks: give the carrier its own name.");
    expect(calls.some((c) => c.method === "POST")).toBe(false);
  });

  it("renames one, clearing its account, and says Simple WMS gets the new name from the next collection", async () => {
    const { calls } = office({ "PATCH /v1/carriers/1": () => json(200, RX) });
    renderApp("/carriers");
    const user = userEvent.setup();
    const list = await yours();
    await user.click(await within(list).findByRole("button", { name: `Change ${RIDGELINE}` }));
    const form = within(list).getByRole("form", { name: `Change ${RIDGELINE}` });
    expect(within(form).getByLabelText("Usually calls at")).toHaveValue("15:30");
    await user.clear(within(form).getByLabelText("Name"));
    await user.type(within(form).getByLabelText("Name"), "Ridgeline Freight (sample)");
    await user.clear(within(form).getByLabelText("Your account with them"));
    await user.click(within(form).getByRole("button", { name: "Save changes" }));
    expect(await screen.findByText(`${RIDGELINE} is now Ridgeline Freight (sample). Simple WMS is told the new name from its next collection.`))
      .toBeInTheDocument();
    expect(calls.find((c) => c.method === "PATCH")!.body).toEqual({ name: "Ridgeline Freight (sample)", account: null, pickup_time: "15:30" });
  });

  it("retires one after asking, and brings one back", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    const { calls } = office({
      "POST /v1/carriers/1/deactivate": () => json(200, { ...RX, active: false }),
      "POST /v1/carriers/3/reactivate": () => json(200, { ...PP, active: true }),
    });
    renderApp("/carriers");
    const user = userEvent.setup();
    const list = await yours();
    await user.click(await within(list).findByRole("button", { name: `Retire ${RIDGELINE}` }));
    expect(confirm).toHaveBeenCalledWith(`Retire ${RIDGELINE}? It takes no new bookings. What is on its bay stays, to be collected.`);
    expect(await screen.findByText(`${RIDGELINE} is retired.`)).toBeInTheDocument();
    await user.click(within(list).getByRole("button", { name: `Bring back ${PLAINS}` }));
    expect(await screen.findByText(`${PLAINS} is back, and takes bookings.`)).toBeInTheDocument();
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(calls.filter((c) => c.method === "POST").map((c) => c.url)).toEqual(["/v1/carriers/1/deactivate", "/v1/carriers/3/reactivate"]);
    confirm.mockRestore();
  });
});
```

**Step 2: Run them to see them fail**

Run: `npx vitest run src/test/CarriersAdmin.test.tsx`
Expected: `Tests  5 failed | 1 passed (6)`: `Unable to find role="region" and name "Your carriers"` (the dispatcher's test passes already).

**Step 3: Implement**

Create `apps/desktop/src/admin/YourCarriers.tsx`:
```tsx
import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { api } from "../api/client";
import type { Carrier, Page } from "../api/types";
import { pickupClock } from "../lib/carriers";
import { apiProblems, carrierNameProblem, found, said, type Problems } from "../lib/admin";
import { Field, focusFirstProblem } from "../ui/Field";
import { Icon } from "../ui/icons";

type Say = (note: { tone: "r" | "a" | "g"; text: string } | null) => void;

/** Add a carrier (no `carrier`), or change one: its name (what Simple WMS
 * is told), your account with it and when it usually calls. How it is
 * booked is shown, not chosen: by hand is the only way yet. */
function CarrierForm({ carrier, carriers, onDone, onCancel }: {
  carrier?: Carrier; carriers: Carrier[]; onDone: (text: string) => Promise<void>; onCancel: () => void;
}) {
  const [name, setName] = useState(carrier?.name ?? "");
  const [account, setAccount] = useState(carrier?.account ?? "");
  const [pickup, setPickup] = useState(carrier?.pickup_time ?? "");
  const [problems, setProblems] = useState<Problems>({});
  const [trouble, setTrouble] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const form = useRef<HTMLFormElement>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setTrouble(null);
    const mine = found({
      name: carrierNameProblem(name, carriers, carrier?.id),
      account: account.trim().length > 40 ? "At most 40 characters." : null,
    });
    setProblems(mine);
    if (Object.keys(mine).length) { focusFirstProblem(form.current); return; }
    const body = { name: name.trim(), account: account.trim() || null, pickup_time: pickup || null };
    setBusy(true);
    try {
      if (!carrier) {
        await api.post<Carrier>("/v1/carriers", body);
        await onDone(`${body.name} is added. Send consignments with it from Plan runs.`);
      } else {
        await api.patch<Carrier>(`/v1/carriers/${carrier.id}`, body);
        await onDone(carrier.name === body.name ? `${body.name} is changed.`
          : `${carrier.name} is now ${body.name}. Simple WMS is told the new name from its next collection.`);
      }
    } catch (err) {
      const theirs = apiProblems(err);
      if (theirs) { setProblems(theirs); focusFirstProblem(form.current); } else setTrouble(said(err));
    } finally {
      setBusy(false);
    }
  }

  const title = carrier ? `Change ${carrier.name}` : "Add a carrier";
  return (
    <form ref={form} className="adminform" aria-label={title} noValidate onSubmit={(e) => void submit(e)}>
      <div className="eyebrow">{carrier ? "CHANGE A CARRIER" : "ADD A CARRIER"}</div>
      <div className="row">
        <Field label="Name" value={name} onChange={(e) => setName(e.target.value)} error={problems.name} maxLength={70} autoFocus
               hint="What Simple WMS is told the freight went with." />
        <Field label="Your account with them" value={account} onChange={(e) => setAccount(e.target.value)} error={problems.account}
               maxLength={50} hint="Printed on the manifest. May be left blank." />
        <Field label="Usually calls at" type="time" value={pickup} onChange={(e) => setPickup(e.target.value)}
               error={problems.pickup_time} hint="By the depot's clock. May be left blank." />
      </div>
      <div className="small">Booked: <b>manual</b>, by hand: book with the carrier as you always have, then type or scan the
        con note here. Carrier connections come later.</div>
      {trouble && <div className="msg r" role="alert">{trouble}</div>}
      <div className="acts">
        <button type="submit" className="btn primary" disabled={busy}>{busy ? "Saving…" : carrier ? "Save changes" : "Add the carrier"}</button>
        <button type="button" className="btn" onClick={onCancel} disabled={busy}>Cancel</button>
      </div>
    </form>
  );
}

/** The bottom of the Carriers drawer, for a fleet manager: every carrier,
 * retired ones too, to add, rename, retire or bring back. */
export function YourCarriers({ say, onChanged }: { say: Say; onChanged: () => Promise<unknown> }) {
  const [carriers, setCarriers] = useState<Carrier[] | null>(null);
  // "new", a carrier's id being changed, or nothing open
  const [open, setOpen] = useState<"new" | number | null>(null);
  const [busy, setBusy] = useState(false);
  const addButton = useRef<HTMLButtonElement>(null);

  const load = useCallback(async () => {
    setCarriers((await api.get<Page<Carrier>>("/v1/carriers")).items);
  }, []);

  useEffect(() => {
    load().catch((e) => say({ tone: "r", text: said(e) }));
  }, [load]);

  async function done(text: string) {
    setOpen(null);
    say({ tone: "g", text });
    await Promise.all([load(), onChanged()]).catch(() => undefined);
  }

  async function retire(c: Carrier) {
    if (c.active && !window.confirm(`Retire ${c.name}? It takes no new bookings. What is on its bay stays, to be collected.`)) return;
    setBusy(true);
    try {
      await api.post(`/v1/carriers/${c.id}/${c.active ? "deactivate" : "reactivate"}`);
      await done(c.active ? `${c.name} is retired.` : `${c.name} is back, and takes bookings.`);
    } catch (e) {
      say({ tone: "r", text: said(e) });
    } finally {
      setBusy(false);
    }
  }

  const all = carriers ?? [];
  const changing = all.find((c) => c.id === open);
  return (
    <section className="grp" aria-label="Your carriers">
      <div className="ghead">
        <span className="gicon"><Icon name="hand" size={20} /></span>
        <div style={{ flex: 1, minWidth: 200 }}><div className="t">Your carriers</div>
          <div className="s">Who else takes your freight. A retired carrier takes no new bookings; its history stays.</div></div>
        <button ref={addButton} type="button" className="btn primary" aria-expanded={open === "new"}
                onClick={() => setOpen(open === "new" ? null : "new")}><Icon name="plus" size={16} width={2.6} />Add a carrier</button>
      </div>
      {open !== null && (
        <div style={{ padding: "0 16px 12px" }}>
          <CarrierForm key={String(open)} carrier={changing} carriers={all} onDone={done}
                       onCancel={() => { setOpen(null); setTimeout(() => addButton.current?.focus(), 0); }} />
        </div>)}
      <div style={{ overflowX: "auto" }}><table style={{ minWidth: 640 }}>
        <thead><tr><th>NAME</th><th>ACCOUNT</th><th>USUALLY CALLS</th><th>BOOKED</th><th>STATUS</th><th><span className="sr">Change</span></th></tr></thead>
        <tbody>
          {all.map((c) => (
            <tr key={c.id}>
              <td><b>{c.name}</b></td>
              <td>{c.account ?? <span className="small">None</span>}</td>
              <td>{c.pickup_time ? pickupClock(c.pickup_time) : <span className="small">Not set</span>}</td>
              <td><span className="mono">{c.adapter}</span></td>
              <td>{c.active ? <span className="pill p-green">Active</span> : <span className="pill p-grey">Retired</span>}</td>
              <td style={{ whiteSpace: "nowrap" }}>
                <button type="button" className="btn sm" aria-label={`Change ${c.name}`} aria-expanded={open === c.id}
                        onClick={() => setOpen(open === c.id ? null : c.id)}>Change</button>{" "}
                <button type="button" className="btn sm" disabled={busy} aria-label={`${c.active ? "Retire" : "Bring back"} ${c.name}`}
                        onClick={() => void retire(c)}>{c.active ? "Retire" : "Bring back"}</button></td>
            </tr>))}
          {carriers?.length === 0 && <tr><td colSpan={6} className="small">No carriers yet. Add the ones you use with Add a carrier.</td></tr>}
        </tbody>
      </table></div>
    </section>
  );
}
```

Change `apps/desktop/src/pages/Carriers.tsx`:
```diff
--- a/apps/desktop/src/pages/Carriers.tsx
+++ b/apps/desktop/src/pages/Carriers.tsx
@@ -2,6 +2,8 @@ import { useCallback, useEffect, useRef, useState, type FormEvent } from "react"
 import { useOutletContext } from "react-router-dom";
 import { api, ApiError, Unreachable } from "../api/client";
 import type { Carrier, Manifest, Page } from "../api/types";
+import { YourCarriers } from "../admin/YourCarriers";
+import { useAuth } from "../auth/AuthContext";
 import { bayStats, conNoteProblem, nextNeedingConNote, normalConNote, pickupLine, type Needing } from "../lib/carriers";
 import { clock, dayLabel, fromIso, isoDay } from "../lib/days";
 import { plain, toMicro } from "../lib/decimal";
@@ -31,6 +33,7 @@ function printLine(m: Manifest): Note {
  * Mark collected once the carrier has been. */
 export function Carriers() {
   const { reloadCounts } = useOutletContext<{ reloadCounts: () => void }>();
+  const manager = useAuth().can("fleet:write");
   const today = isoDay(new Date());
   const [manifests, setManifests] = useState<Manifest[] | null>(null);
   const [carriers, setCarriers] = useState<Carrier[]>([]);
@@ -213,7 +216,8 @@ export function Carriers() {
       {manifests && all.length === 0 && (
         <div className="small">Nothing is waiting for a carrier. Send a consignment with a carrier from Plan runs.</div>)}
       {manifests && carriers.length === 0 && (
-        <div className="msg a" role="status">No carriers yet. A fleet manager adds them through the API (POST /v1/carriers; see docs/api.md).</div>)}
+        <div className="msg a" role="status">{manager ? "No carriers yet. Add the ones you use under Your carriers, below."
+          : "No carriers yet. A fleet manager adds them here, in Carriers."}</div>)}
 
       {all.map((m) => {
         const isOpen = m.status === "open";
@@ -277,6 +281,8 @@ export function Carriers() {
         );
       })}
 
+      {manager && <YourCarriers say={setNote} onChanged={load} />}
+
       <div className="small">Manifests print on the dispatch printer through Platen. Carrier bookings are by hand in this version: no carrier connections yet.</div>
     </Drawer>
   );
```

**Step 4: Run them, then the suite**

Run: `npx vitest run src/test/CarriersAdmin.test.tsx`
Expected: `Tests  6 passed (6)`.
Run: `npm test && npm run typecheck && npm run build`
Expected: `Tests  154 passed (154)`, no type errors, and the build ends `✓ built in …`.

**Step 5: Commit**

```bash
git add apps/desktop/src/admin/YourCarriers.tsx apps/desktop/src/pages/Carriers.tsx \
        apps/desktop/src/test/CarriersAdmin.test.tsx
git commit -m "Carriers: add, rename, retire and bring back a carrier"
```

## Part C — Settings

### Task 9: Settings given on screen, kept in the database and read back by the API and the worker

Decisions 12 to 14. Two tables (`0010`), the classification and the loader (`services/runtime_settings.py`), and `config.get_settings()` made the settings in force. Nothing calls the database to change them yet (Task 10); the tests put a row in as the API would from another process. The tests' `conftest.py` reads the settings afresh before each test (never in the middle of one, where a re-read would add a query to the tests that count them) and makes the timed re-read wait for ever, except where `test_settings.py` shows it. `db.py` and `sessions.py` read `.env`'s settings directly: the database's address and the session key never come from the screen.

**Files:**
- Create: `api/tms/models/settings.py`, `api/alembic/versions/0010_runtime_settings.py`, `api/tms/services/runtime_settings.py`
- Modify: `api/tms/config.py`, `api/tms/db.py`, `api/tms/services/sessions.py`, `api/tms/models/__init__.py`, `api/tms/worker.py`
- Test: `api/tests/test_settings.py` (new), `api/tests/test_config.py`, `api/tests/test_migrations.py`, `api/tests/conftest.py`

**Step 1: Write the failing tests**

Create `api/tests/test_settings.py`:
```python
"""Settings an admin gives on screen in place of .env's: every one has a
kind, and the API and the worker read them back without a restart."""
import logging
from datetime import UTC, datetime

from tms import config, worker
from tms.config import Settings, get_settings
from tms.models import Setting
from tms.services import runtime_settings


def test_every_setting_has_a_kind_and_none_is_left_out():
    """A new TMS_* setting must be put in one of the three kinds."""
    named = set(runtime_settings.ON_SCREEN) | set(runtime_settings.SECRETS) | set(runtime_settings.FILE_ONLY)
    assert named == set(Settings.model_fields)
    assert not set(runtime_settings.ON_SCREEN) & set(runtime_settings.SECRETS)


def test_the_worker_takes_a_change_made_elsewhere_without_a_restart(db, monkeypatch):
    """The API saves it in another process; the worker reads it again within
    REFRESH_SECONDS, and not before (one query each time, not one a call)."""
    clock = [1000.0]
    monkeypatch.setattr(config, "_now", lambda: clock[0])
    monkeypatch.setattr(config, "REFRESH_SECONDS", 5.0)
    config.forget_settings()
    assert worker.poll_seconds() == 5.0 and not worker._printing_set_up()

    now = datetime.now(UTC)
    db.add_all([Setting(name="worker_poll_seconds", value="2.0", changed_by="leighton", changed_at=now),
                Setting(name="platen_url", value="http://platen.test/intake/jobs", changed_by="leighton",
                        changed_at=now)])
    db.commit()
    clock[0] += 4.9
    assert worker.poll_seconds() == 5.0
    clock[0] += 0.2
    assert worker.poll_seconds() == 2.0 and worker._printing_set_up()


def test_values_on_screen_that_no_longer_fit_env_are_set_aside(db, caplog):
    db.add(Setting(name="eta_minutes_per_stop", value="0", changed_by="leighton",
                   changed_at=datetime.now(UTC)))
    db.commit()
    config.forget_settings()
    with caplog.at_level(logging.WARNING, logger="tms.settings"):
        assert get_settings().eta_minutes_per_stop == 20
    assert "don't fit .env any more" in caplog.text


def test_a_database_that_cant_be_read_leaves_the_settings_in_force(monkeypatch):
    from sqlalchemy.exc import OperationalError

    def down():
        raise OperationalError("select", {}, Exception("no database"))

    monkeypatch.setattr("tms.db.get_engine", down)
    config.forget_settings()
    assert get_settings() is config.file_settings()
```

Change `api/tests/test_config.py`:
```diff
--- a/api/tests/test_config.py
+++ b/api/tests/test_config.py
@@ -92,3 +92,21 @@ def test_manifests_go_to_the_run_sheet_printer_unless_told():
     assert s.manifest_printer == "" and s.manifest_printer_or_default == "office"
     s = Settings(secret_key=KEY, run_sheet_printer="office", manifest_printer="bay")
     assert s.manifest_printer_or_default == "bay"
+
+
+@pytest.mark.parametrize("field", ["wms_url", "platen_url"])
+@pytest.mark.parametrize("url", ["wms.example.com", "ftp://wms.example.com", "https://"])
+def test_an_address_that_isnt_http_refuses_to_start(field, url):
+    with pytest.raises(ValidationError, match=f"TMS_{field.upper()} must be an http:// or https:// address"):
+        Settings(secret_key=KEY, **{field: url})
+
+
+def test_an_address_is_kept_without_spaces_around_it():
+    assert Settings(secret_key=KEY, wms_url=" http://wms.test/ ").wms_url == "http://wms.test/"
+
+
+@pytest.mark.parametrize("field, value", [("worker_poll_seconds", 0), ("worker_poll_seconds", 301),
+                                          ("worker_http_timeout_seconds", 0), ("wms_timeout_seconds", 61)])
+def test_a_wait_out_of_range_refuses_to_start(field, value):
+    with pytest.raises(ValidationError, match=field):
+        Settings(secret_key=KEY, **{field: value})
```

Change `api/tests/test_migrations.py`:
```diff
--- a/api/tests/test_migrations.py
+++ b/api/tests/test_migrations.py
@@ -14,7 +14,8 @@ TABLES = {"user", "user_session", "driver", "vehicle", "consignment", "package",
           "consignment_event", "inbound_event", "run", "stop", "outbound_call", "print_job",
           "api_key", "inbound_message", "pod_file", "pod",
           "checklist_item", "prestart_check", "prestart_item", "vehicle_record",
-          "position", "carrier", "manifest", "booking", "alembic_version"}
+          "position", "carrier", "manifest", "booking", "setting", "setting_change",
+          "alembic_version"}
 
 
 @pytest.fixture
@@ -59,7 +60,8 @@ def test_migrations_go_up_down_and_up_again(blank_database):
         triggers = conn.execute(text(
             "select tgname from pg_trigger where not tgisinternal")).scalars().all()
     assert {"consignment_event_append_only", "prestart_check_append_only",
-            "prestart_item_append_only", "vehicle_record_append_only"} <= set(triggers)
+            "prestart_item_append_only", "vehicle_record_append_only",
+            "setting_change_append_only"} <= set(triggers)
 
     command.downgrade(cfg, "base")
     assert _tables(engine) == {"alembic_version"}
```

Change `api/tests/conftest.py`:
```diff
--- a/api/tests/conftest.py
+++ b/api/tests/conftest.py
@@ -50,10 +50,23 @@ def engine():
     cfg.set_main_option("sqlalchemy.url", TEST_DATABASE_URL)
     cfg.attributes["configure_logger"] = False
     command.upgrade(cfg, "head")
+    fresh_settings()
     yield engine
     engine.dispose()
 
 
+def fresh_settings():
+    """Settings read again from .env and the (empty) database, before a test
+    starts rather than in the middle of one, where it would add a query to
+    those counted. Read again only when told (forget_settings) in tests:
+    test_settings.py shows the timed re-read on its own."""
+    from tms import config
+
+    config.REFRESH_SECONDS = float("inf")
+    config.forget_settings()
+    config.get_settings()
+
+
 @pytest.fixture
 def db(engine):
     from tms.models import Base
@@ -67,6 +80,7 @@ def db(engine):
             # TRUNCATE is not UPDATE or DELETE, so the append-only trigger lets it by
             conn.execute(text(f"truncate {tables} restart identity cascade"))
             seed_checklists(conn)
+    fresh_settings()
 
 
 # The checklist migration 0005 gives a fresh install, put back after each
```

**Step 2: Run them to see them fail**

Run: `.venv/bin/pytest tests/test_settings.py tests/test_config.py tests/test_migrations.py -q`
Expected: `1 error` while collecting `test_settings.py` (`ImportError: cannot import name 'Setting' from 'tms.models'`), and the run stops there.

**Step 3: Implement**

Create `api/tms/models/settings.py`:
```python
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from tms.models.base import Base, created_at_column


class Setting(Base):
    """A setting an admin has given on screen, in place of .env's. One row
    per setting, changed in place like a vehicle's limits; `value` null is
    .env's again (a reset keeps the row: nothing is deleted). Only the
    current value of a secret is kept, here and nowhere else."""

    __tablename__ = "setting"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text)
    changed_by: Mapped[str] = mapped_column(String(64))
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SettingChange(Base):
    """Who changed a setting, to what, and when: append-only, like a
    truck's records. A secret's new value is never copied here, so an old
    key doesn't linger once it is replaced."""

    __tablename__ = "setting_change"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), index=True)
    # set (a value given on screen) or reset (back to .env's)
    action: Mapped[str] = mapped_column(String(8))
    # what it was set to; null for a reset, and always for a secret
    value: Mapped[str | None] = mapped_column(Text)
    secret: Mapped[bool] = mapped_column(Boolean, default=False)
    changed_by: Mapped[str] = mapped_column(String(64))
    changed_at: Mapped[datetime] = created_at_column()
```

Change `api/tms/models/__init__.py`:
```diff
--- a/api/tms/models/__init__.py
+++ b/api/tms/models/__init__.py
@@ -7,10 +7,11 @@ from tms.models.runs import Run, Stop
 from tms.models.delivery import InboundMessage, Pod, PodFile
 from tms.models.location import Position
 from tms.models.carriers import Booking, Carrier, Manifest
+from tms.models.settings import Setting, SettingChange
 
 __all__ = [
     "Base", "User", "UserSession", "Driver", "Vehicle", "Consignment", "Package",
     "ConsignmentEvent", "InboundEvent", "Run", "Stop", "OutboundCall", "PrintJob", "ApiKey",
     "InboundMessage", "Pod", "PodFile", "ChecklistItem", "PrestartCheck", "PrestartItem",
-    "VehicleRecord", "Position", "Carrier", "Manifest", "Booking",
+    "VehicleRecord", "Position", "Carrier", "Manifest", "Booking", "Setting", "SettingChange",
 ]
```

Create `api/alembic/versions/0010_runtime_settings.py` (see "Working beside the 'came back' plan" if a `0010` is already there):
```python
"""settings an admin gives on screen in place of .env's, and the record of
each change (append-only)

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-15
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "setting",
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("value", sa.Text(), nullable=True),
        sa.Column("changed_by", sa.String(64), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("name", name=op.f("pk_setting")),
    )
    op.create_table(
        "setting_change",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("action", sa.String(8), nullable=False),
        sa.Column("value", sa.Text(), nullable=True),
        sa.Column("secret", sa.Boolean(), nullable=False),
        sa.Column("changed_by", sa.String(64), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_setting_change")),
    )
    op.create_index(op.f("ix_setting_change_name"), "setting_change", ["name"])
    # each change is kept as it was made (block_change is 0001's)
    op.execute("create trigger setting_change_append_only before update or delete on "
               "setting_change for each row execute function block_change()")


def downgrade() -> None:
    op.execute("drop trigger if exists setting_change_append_only on setting_change")
    op.drop_index(op.f("ix_setting_change_name"), table_name="setting_change")
    op.drop_table("setting_change")
    op.drop_table("setting")
```

Create `api/tms/services/runtime_settings.py`:
```python
"""Settings an admin may change on screen, in place of .env's.

Every TMS_* setting is one of three kinds:

- **on screen**: safe to change while running, and can't lock anyone out or
  weaken security (printers, the Simple WMS and Platen addresses, waits,
  how long positions are kept, the ETA's first guess);
- **secret**: may be replaced on screen, but is never shown or returned
  again, and never written to the log or the record of changes (the Simple
  WMS key and webhook secret, the Platen key);
- **file only**: could lock everyone out, break the app or weaken security,
  or is read by something other than the API when it starts (the database,
  the session key, where proof-of-delivery files are kept, and the map
  tiles, which Caddy's Content-Security-Policy is built from).

.env gives each its default. A value given on screen is kept in `setting`
(one row each, changed in place) with every change in `setting_change`
(append-only, secrets' values left out), and is checked with config.py's
own rules before it is kept. config.get_settings() reads them back, so the
API and the worker both use them, within REFRESH_SECONDS, without a
restart."""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from tms.config import Settings
from tms.models import Setting, SettingChange

log = logging.getLogger("tms.settings")

ON_SCREEN = ("wms_url", "wms_timeout_seconds", "platen_url", "run_sheet_printer",
             "manifest_printer", "position_retention_days", "eta_minutes_per_stop",
             "worker_poll_seconds", "worker_http_timeout_seconds")
SECRETS = ("wms_api_key", "wms_webhook_secret", "platen_key")
# why each stays in .env
FILE_ONLY = {
    "database_url": "the API and the worker need it to reach the database at all",
    "secret_key": "it signs every session: a wrong one signs everyone out",
    "pod_dir": "signatures and photos are kept there, in a volume Compose mounts",
    "map_tile_url": "Caddy lets the tiles in through the desktop's Content-Security-Policy, "
                    "from TMS_MAP_TILE_ORIGIN, when it starts",
    "map_tile_origin": "Caddy reads it when it starts, for the desktop's "
                       "Content-Security-Policy",
    "map_attribution": "it is HTML the desktop shows as it is, and belongs with the tile URL",
}


def env_name(name: str) -> str:
    return f"TMS_{name.upper()}"


def kind(name: str) -> str:
    return "secret" if name in SECRETS else "screen" if name in ON_SCREEN else "file"


def _stored(rows) -> dict[str, str]:
    return {n: v for n, v in rows if v is not None and (n in ON_SCREEN or n in SECRETS)}


def load(base: Settings) -> Settings | None:
    """.env's settings with those given on screen in their place. None when
    the database can't be read (not up, or before the migration): the
    caller keeps what it had."""
    from tms.db import get_engine

    try:
        with get_engine().connect() as conn:
            given = _stored(conn.execute(select(Setting.name, Setting.value)).all())
    except SQLAlchemyError:
        return None
    if not given:
        return base
    try:
        return Settings.model_validate({**base.model_dump(), **given})
    except ValidationError:
        # .env changed under them: never say the values (a secret may be one)
        log.warning("settings given on screen don't fit .env any more; .env's are in force "
                    "until an admin saves them again")
        return base
```

Change `api/tms/config.py`:
```diff
--- a/api/tms/config.py
+++ b/api/tms/config.py
@@ -1,7 +1,6 @@
-from functools import lru_cache
-
 import re
-
+import time
+from functools import lru_cache
 from urllib.parse import urlsplit
 
 from pydantic import Field, field_validator, model_validator
@@ -29,7 +28,7 @@ class Settings(BaseSettings):
     wms_url: str = ""
     wms_api_key: str = ""
     # Shorter than the WMS worker's own 10 s wait, so it hears our answer.
-    wms_timeout_seconds: float = 5.0
+    wms_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
 
     # Platen, or any print service that takes the same job: its intake
     # address (https://<platen>/intake/jobs), a Platen key, and the printer
@@ -66,8 +65,8 @@ class Settings(BaseSettings):
     eta_minutes_per_stop: int = Field(default=20, ge=1, le=600)
 
     # The worker that sends the ship calls and print jobs.
-    worker_poll_seconds: float = 5.0
-    worker_http_timeout_seconds: float = 10.0
+    worker_poll_seconds: float = Field(default=5.0, ge=1, le=300)
+    worker_http_timeout_seconds: float = Field(default=10.0, gt=0, le=120)
 
     @property
     def manifest_printer_or_default(self) -> str:
@@ -82,6 +81,15 @@ class Settings(BaseSettings):
                 "example; make one with: openssl rand -hex 32")
         return value
 
+    @field_validator("wms_url", "platen_url")
+    @classmethod
+    def _an_address(cls, value: str, info) -> str:
+        value = value.strip()
+        if value and not re.match(r"^https?://[^\s/?#]+", value):
+            raise ValueError(f"TMS_{info.field_name.upper()} must be an http:// or https:// "
+                             "address, or empty")
+        return value
+
     @field_validator("map_tile_url")
     @classmethod
     def _usable_tiles(cls, value: str) -> str:
@@ -120,5 +128,39 @@ class Settings(BaseSettings):
 
 
 @lru_cache
-def get_settings() -> Settings:
+def file_settings() -> Settings:
+    """What .env (the environment) says, read once at start. The database's
+    address and the session key come from here and nowhere else."""
     return Settings()
+
+
+# How often the API and the worker look again for settings an admin has
+# changed on screen: each process takes a change within this many seconds,
+# without a restart.
+REFRESH_SECONDS = 5.0
+_now = time.monotonic
+_effective: Settings | None = None
+_read_at = 0.0
+
+
+def get_settings() -> Settings:
+    """The settings in force: .env's, with any an admin has given on screen
+    in their place (services/runtime_settings.py), read again at most every
+    REFRESH_SECONDS. With none given on screen it is file_settings() itself.
+    If the database can't be read, the last settings read stay in force."""
+    global _effective, _read_at
+    now = _now()
+    if _effective is None or now - _read_at >= REFRESH_SECONDS:
+        # here, not at the top: the database is reached through file_settings()
+        from tms.services import runtime_settings
+
+        _effective = runtime_settings.load(file_settings()) or _effective or file_settings()
+        _read_at = now
+    return _effective
+
+
+def forget_settings() -> None:
+    """Read them again on the next get_settings(): after a change on screen,
+    so the process that made it has it at once."""
+    global _effective
+    _effective = None
```

Change `api/tms/db.py`:
```diff
--- a/api/tms/db.py
+++ b/api/tms/db.py
@@ -3,12 +3,12 @@ from functools import lru_cache
 from sqlalchemy import create_engine
 from sqlalchemy.orm import Session, sessionmaker
 
-from tms.config import get_settings
+from tms.config import file_settings
 
 
 @lru_cache
 def get_engine():
-    return create_engine(get_settings().database_url, pool_pre_ping=True)
+    return create_engine(file_settings().database_url, pool_pre_ping=True)
 
 
 @lru_cache
```

Change `api/tms/services/sessions.py`:
```diff
--- a/api/tms/services/sessions.py
+++ b/api/tms/services/sessions.py
@@ -13,7 +13,7 @@ from datetime import UTC, datetime, timedelta
 from sqlalchemy import select, update
 from sqlalchemy.orm import Session
 
-from tms.config import get_settings
+from tms.config import file_settings
 from tms.models import Driver, User, UserSession
 from tms.services import access
 
@@ -52,7 +52,7 @@ def _unb64(text: str) -> bytes:
 
 
 def _sign(payload: str) -> str:
-    key = get_settings().secret_key.encode()
+    key = file_settings().secret_key.encode()
     return _b64(hmac.new(key, payload.encode(), hashlib.sha256).digest())
 
 
```

Change `api/tms/worker.py` (`main()` only; the ship calls are as they were):
```diff
--- a/api/tms/worker.py
+++ b/api/tms/worker.py
@@ -105,6 +105,12 @@ def _ships_set_up() -> bool:
     return bool(settings.wms_url and settings.wms_api_key)
 
 
+def poll_seconds() -> float:
+    """How long to wait when there was nothing to do: read each time, so a
+    change an admin makes on screen takes without a restart."""
+    return get_settings().worker_poll_seconds
+
+
 def _printing_set_up() -> bool:
     return bool(get_settings().platen_url)
 
@@ -196,11 +202,10 @@ def run_once(session: Session, http: httpx.Client, now: datetime | None = None,
 def main() -> None:
     logging.basicConfig(level=logging.INFO,
                         format="%(asctime)s %(levelname)s %(name)s %(message)s")
-    settings = get_settings()
     make_session = get_sessionmaker()
     with make_session() as session:
         wait_for_schema(session)
-    log.info("worker started, polling every %ss", settings.worker_poll_seconds)
+    log.info("worker started, polling every %ss", poll_seconds())
     forgot_at: float | None = None
     with httpx.Client() as http:
         while True:
@@ -218,7 +223,7 @@ def main() -> None:
                 log.exception("worker loop error")
                 done = 0
             if done == 0:
-                time.sleep(settings.worker_poll_seconds)
+                time.sleep(poll_seconds())
 
 
 if __name__ == "__main__":
```

**Step 4: Run them, then the suite**

Run: `.venv/bin/pytest tests/test_settings.py tests/test_config.py tests/test_migrations.py -q`
Expected: `52 passed`.
Run: `caffeinate -i .venv/bin/pytest -q`
Expected: `429 passed`.

**Step 5: Commit**

```bash
git add api/tms/models/settings.py api/tms/models/__init__.py api/alembic/versions/0010_runtime_settings.py \
        api/tms/services/runtime_settings.py api/tms/config.py api/tms/db.py api/tms/services/sessions.py api/tms/worker.py \
        api/tests/test_settings.py api/tests/test_config.py api/tests/test_migrations.py api/tests/conftest.py
git commit -m "Keep settings given on screen in the database, read back by the API and the worker"
```

### Task 10: `GET` and `PATCH /v1/settings`, and the record of changes

Decisions 10 to 15. `runtime_settings.change` checks a whole save with `Settings` itself and keeps it all or none, under a lock of its own so two admins take turns; `FieldErrors` is a `422` with a field error on each setting that is wrong. The process that saved forgets its settings at once (`committed()`); the others re-read within 5 seconds. `view` never puts a secret's value, the database's address or the session key in an answer.

**Files:**
- Create: `api/tms/api/routes/settings.py`
- Modify: `api/tms/services/runtime_settings.py`, `api/tms/api/errors.py`, `api/tms/api/app.py`, `docs/api.md`
- Test: `api/tests/test_settings.py`

**Step 1: Write the failing tests**

Change `api/tests/test_settings.py`:
```diff
--- a/api/tests/test_settings.py
+++ b/api/tests/test_settings.py
@@ -1,13 +1,27 @@
-"""Settings an admin gives on screen in place of .env's: every one has a
-kind, and the API and the worker read them back without a restart."""
+"""Settings an admin changes on screen in place of .env's: which may be,
+checked with config.py's own rules, used by the API and the worker without a
+restart, put back to .env's, and secrets never shown or logged again."""
 import logging
 from datetime import UTC, datetime
 
+import pytest
+from sqlalchemy import select
+
 from tms import config, worker
 from tms.config import Settings, get_settings
-from tms.models import Setting
+from tms.models import Setting, SettingChange
 from tms.services import runtime_settings
 
+KEY = "plt_on-screen-0123456789"
+
+
+def _patch(client, headers, body):
+    return client.patch("/v1/settings", headers=headers, json=body)
+
+
+def _one(settings: list[dict], name: str) -> dict:
+    return next(s for s in settings if s["name"] == name)
+
 
 def test_every_setting_has_a_kind_and_none_is_left_out():
     """A new TMS_* setting must be put in one of the three kinds."""
@@ -16,6 +30,120 @@ def test_every_setting_has_a_kind_and_none_is_left_out():
     assert not set(runtime_settings.ON_SCREEN) & set(runtime_settings.SECRETS)
 
 
+def test_only_an_admin_reads_or_changes_settings(client, admin_headers, manager_headers,
+                                                 dispatcher_headers):
+    for headers in (manager_headers, dispatcher_headers):
+        assert client.get("/v1/settings", headers=headers).status_code == 403
+        assert _patch(client, headers, {"run_sheet_printer": "Office"}).status_code == 403
+    assert client.get("/v1/settings", headers=admin_headers).status_code == 200
+
+
+def test_each_is_listed_with_its_kind_and_no_secret_is_shown(client, admin_headers, monkeypatch):
+    monkeypatch.setattr(get_settings(), "wms_api_key", "wms_from-env-key")
+    out = client.get("/v1/settings", headers=admin_headers).json()
+    assert {s["name"] for s in out} == set(Settings.model_fields)
+    printer = _one(out, "run_sheet_printer")
+    assert printer | {"changed_at": None} == {
+        "name": "run_sheet_printer", "env": "TMS_RUN_SHEET_PRINTER", "kind": "screen",
+        "value": "dispatch", "default": "dispatch", "is_set": True, "on_screen": False,
+        "changed_by": None, "changed_at": None, "why_file_only": None}
+    key = _one(out, "wms_api_key")
+    assert (key["kind"], key["value"], key["default"], key["is_set"]) == ("secret", None, None, True)
+    for name in ("database_url", "secret_key"):
+        assert _one(out, name)["kind"] == "file" and _one(out, name)["value"] is None
+    assert _one(out, "map_tile_origin")["why_file_only"].startswith("Caddy reads it")
+    assert "wms_from-env-key" not in str(out) and get_settings().secret_key not in str(out)
+
+
+@pytest.mark.parametrize("name, says", [
+    ("database_url", "TMS_DATABASE_URL is set in .env only"),
+    ("secret_key", "TMS_SECRET_KEY is set in .env only"),
+    ("map_tile_url", "TMS_MAP_TILE_URL is set in .env only"),
+    ("map_tile_origin", "TMS_MAP_TILE_ORIGIN is set in .env only"),
+    ("pod_dir", "TMS_POD_DIR is set in .env only"),
+    ("domain", "not a setting Simple TMS has"),
+])
+def test_a_file_only_setting_is_refused(client, db, admin_headers, name, says):
+    r = _patch(client, admin_headers, {name: "https://elsewhere.example", "run_sheet_printer": "Office"})
+    assert r.status_code == 422
+    assert r.json()["errors"][0]["field"] == name and says in r.json()["errors"][0]["message"]
+    # and nothing else in the same request was kept
+    assert db.execute(select(SettingChange)).first() is None
+    assert get_settings().run_sheet_printer == "dispatch"
+
+
+def test_a_value_given_on_screen_is_used_until_it_is_put_back_to_envs(client, admin_headers):
+    r = _patch(client, admin_headers, {"run_sheet_printer": " Office 2 ", "eta_minutes_per_stop": "25"})
+    assert r.status_code == 200, r.text
+    printer = _one(r.json(), "run_sheet_printer")
+    assert (printer["value"], printer["default"], printer["on_screen"], printer["changed_by"]) == (
+        "Office 2", "dispatch", True, "leighton")
+    assert get_settings().run_sheet_printer == "Office 2"
+    assert get_settings().eta_minutes_per_stop == 25
+
+    r = _patch(client, admin_headers, {"run_sheet_printer": None})
+    assert r.status_code == 200
+    assert _one(r.json(), "run_sheet_printer")["on_screen"] is False
+    assert get_settings().run_sheet_printer == "dispatch"
+    assert get_settings().eta_minutes_per_stop == 25
+
+    history = client.get("/v1/settings/history", headers=admin_headers).json()
+    assert [(h["name"], h["action"], h["value"], h["changed_by"]) for h in history] == [
+        ("run_sheet_printer", "reset", None, "leighton"),
+        ("eta_minutes_per_stop", "set", "25", "leighton"),
+        ("run_sheet_printer", "set", "Office 2", "leighton")]
+
+
+def test_values_are_checked_with_configs_own_rules_all_or_none(client, admin_headers):
+    r = _patch(client, admin_headers, {"eta_minutes_per_stop": 0, "worker_poll_seconds": "soon",
+                                       "wms_url": "ftp://wms.example", "manifest_printer": "Bay"})
+    assert r.status_code == 422
+    assert {e["field"]: e["message"] for e in r.json()["errors"]} == {
+        "eta_minutes_per_stop": "Input should be greater than or equal to 1",
+        "worker_poll_seconds": "Input should be a valid number, unable to parse string as a number",
+        "wms_url": "TMS_WMS_URL must be an http:// or https:// address, or empty",
+        # a new address needs its key with it, too
+        "wms_api_key": "Type the Simple WMS key again with a new address: a key on file is only "
+                       "sent where it was given for"}
+    assert get_settings().manifest_printer == ""
+
+
+def test_a_secret_is_replaced_but_never_shown_kept_in_the_record_or_logged(client, db, admin_headers,
+                                                                           caplog):
+    with caplog.at_level(logging.DEBUG):
+        r = _patch(client, admin_headers, {"platen_key": f"  {KEY} "})
+        assert r.status_code == 200, r.text
+        key = _one(r.json(), "platen_key")
+        assert (key["value"], key["is_set"], key["on_screen"]) == (None, True, True)
+        assert KEY not in r.text
+        assert KEY not in client.get("/v1/settings", headers=admin_headers).text
+        history = client.get("/v1/settings/history", headers=admin_headers).json()
+        assert history[0] | {"changed_at": None, "id": None} == {
+            "id": None, "name": "platen_key", "env": "TMS_PLATEN_KEY", "action": "set",
+            "value": None, "secret": True, "changed_by": "leighton", "changed_at": None}
+    assert get_settings().platen_key == KEY
+    assert KEY not in caplog.text
+    assert db.execute(select(SettingChange.value)).scalars().all() == [None]
+
+
+def test_a_secret_cant_be_blank_and_a_webhook_secret_is_as_long_as_simple_wmss(client, admin_headers):
+    r = _patch(client, admin_headers, {"platen_key": " ", "wms_webhook_secret": "short"})
+    assert {e["field"]: e["message"] for e in r.json()["errors"]} == {
+        "platen_key": "Type the new value, or use .env's",
+        "wms_webhook_secret": "16 to 128 characters, the same as Simple WMS's"}
+
+
+def test_a_new_address_needs_its_key_given_with_it(client, admin_headers):
+    r = _patch(client, admin_headers, {"platen_url": "https://platen.example/intake/jobs"})
+    assert r.status_code == 422 and r.json()["errors"][0]["field"] == "platen_key"
+    r = _patch(client, admin_headers, {"platen_url": "https://platen.example/intake/jobs",
+                                       "platen_key": KEY})
+    assert r.status_code == 200, r.text
+    # the same address again needs nothing more, and neither does going back to .env's
+    assert _patch(client, admin_headers, {"platen_url": "https://platen.example/intake/jobs"}).status_code == 200
+    assert _patch(client, admin_headers, {"platen_url": None}).status_code == 200
+
+
 def test_the_worker_takes_a_change_made_elsewhere_without_a_restart(db, monkeypatch):
     """The API saves it in another process; the worker reads it again within
     REFRESH_SECONDS, and not before (one query each time, not one a call)."""
```

**Step 2: Run them to see them fail**

Run: `.venv/bin/pytest tests/test_settings.py -q`
Expected: `13 failed, 4 passed`: there is no `/v1/settings` yet (`404`).

**Step 3: Implement**

Change `api/tms/services/runtime_settings.py`:
```diff
--- a/api/tms/services/runtime_settings.py
+++ b/api/tms/services/runtime_settings.py
@@ -25,10 +25,12 @@ import logging
 from datetime import UTC, datetime
 
 from pydantic import ValidationError
-from sqlalchemy import select
+from sqlalchemy import select, text
 from sqlalchemy.exc import SQLAlchemyError
+from sqlalchemy.orm import Session
 
-from tms.config import Settings
+from tms.api.errors import FieldErrors
+from tms.config import Settings, file_settings, forget_settings
 from tms.models import Setting, SettingChange
 
 log = logging.getLogger("tms.settings")
@@ -48,6 +50,13 @@ FILE_ONLY = {
                        "Content-Security-Policy",
     "map_attribution": "it is HTML the desktop shows as it is, and belongs with the tile URL",
 }
+# an address and the key that is sent to it: a new address needs its key
+# given again with it, so a key on file is never sent somewhere it wasn't given for
+KEY_FOR = {"wms_url": "wms_api_key", "platen_url": "platen_key"}
+NAMES = {"wms_url": "Simple WMS", "platen_url": "Platen"}
+MIN_WEBHOOK_SECRET, MAX_WEBHOOK_SECRET = 16, 128
+# two admins saving at once take turns
+SETTINGS_LOCK = 7_302_202
 
 
 def env_name(name: str) -> str:
@@ -82,3 +91,100 @@ def load(base: Settings) -> Settings | None:
         log.warning("settings given on screen don't fit .env any more; .env's are in force "
                     "until an admin saves them again")
         return base
+
+
+def _problems(e: ValidationError, names) -> list[dict]:
+    out = []
+    for err in e.errors():
+        field = str(err["loc"][0]) if err.get("loc") else "body"
+        msg = err["msg"].removeprefix("Value error, ")
+        out.append({"field": field if field in names else "body", "message": msg})
+    return out
+
+
+def change(db: Session, changes: dict[str, object], by: str) -> None:
+    """Each named setting given a value (kept in place of .env's) or None
+    (.env's again), checked together with config.py's rules: all of them, or
+    none (422 with a field error on each that is wrong)."""
+    base = file_settings()
+    errors = []
+    for name in changes:
+        if name in FILE_ONLY:
+            errors.append({"field": name, "message": f"{env_name(name)} is set in .env only: "
+                                                     f"{FILE_ONLY[name]}"})
+        elif kind(name) == "file":
+            errors.append({"field": name, "message": "not a setting Simple TMS has"})
+    if errors:
+        raise FieldErrors(errors)
+
+    db.execute(text("select pg_advisory_xact_lock(:k)"), {"k": SETTINGS_LOCK})
+    rows = {r.name: r for r in db.execute(select(Setting)).scalars()}
+    wanted = _stored((n, r.value) for n, r in rows.items())
+    for name, value in changes.items():
+        if value is None:
+            wanted.pop(name, None)
+            continue
+        typed = str(value).strip()
+        if name in SECRETS and not typed:
+            errors.append({"field": name, "message": "Type the new value, or use .env's"})
+        elif name == "wms_webhook_secret" and not (MIN_WEBHOOK_SECRET <= len(typed) <= MAX_WEBHOOK_SECRET):
+            errors.append({"field": name, "message": f"{MIN_WEBHOOK_SECRET} to {MAX_WEBHOOK_SECRET} "
+                                                     "characters, the same as Simple WMS's"})
+        wanted[name] = typed
+    current = load(base) or base
+    for address, key in KEY_FOR.items():
+        new = changes.get(address)
+        if new is not None and str(new).strip() != getattr(current, address) and changes.get(key) is None:
+            errors.append({"field": key, "message": f"Type the {NAMES[address]} key again with a new "
+                                                    "address: a key on file is only sent where it was given for"})
+    try:
+        checked = Settings.model_validate({**base.model_dump(), **wanted})
+    except ValidationError as e:
+        errors += _problems(e, set(changes))
+    if errors:
+        raise FieldErrors(errors)
+
+    now = datetime.now(UTC)
+    for name, value in changes.items():
+        kept = None if value is None else str(getattr(checked, name))
+        row = rows.get(name)
+        if row is None:
+            row = Setting(name=name)
+            db.add(row)
+        row.value, row.changed_by, row.changed_at = kept, by, now
+        secret = name in SECRETS
+        db.add(SettingChange(name=name, action="reset" if kept is None else "set",
+                             value=None if secret else kept, secret=secret, changed_by=by,
+                             changed_at=now))
+    db.flush()
+
+
+def committed() -> None:
+    """After a change is committed: this process uses it at once (the
+    others within config.REFRESH_SECONDS)."""
+    forget_settings()
+
+
+def view(db: Session) -> list[dict]:
+    """Every TMS_* setting, as the Settings drawer shows it. A secret's
+    value, and the database's address and session key, are never in it."""
+    base = file_settings()
+    now = load(base) or base
+    rows = {r.name: r for r in db.execute(select(Setting)).scalars()}
+    out = []
+    for name in (*ON_SCREEN, *SECRETS, *FILE_ONLY):
+        row = rows.get(name)
+        on_screen = row is not None and row.value is not None
+        k = kind(name)
+        hidden = k == "secret" or name in ("database_url", "secret_key")
+        out.append({
+            "name": name, "env": env_name(name), "kind": k,
+            "value": None if hidden else getattr(now, name),
+            "default": None if hidden or k == "file" else getattr(base, name),
+            "is_set": bool(str(getattr(now, name)).strip()),
+            "on_screen": on_screen,
+            "changed_by": row.changed_by if row else None,
+            "changed_at": row.changed_at if row else None,
+            "why_file_only": FILE_ONLY.get(name),
+        })
+    return out
```

Change `api/tms/api/errors.py`:
```diff
--- a/api/tms/api/errors.py
+++ b/api/tms/api/errors.py
@@ -15,6 +15,14 @@ class FieldError(Exception):
         super().__init__(f"{field}: {message}")
 
 
+class FieldErrors(Exception):
+    """Several fields wrong at once, each with what is wrong with it."""
+
+    def __init__(self, errors: list[dict]):
+        self.errors = errors
+        super().__init__("; ".join(f"{e['field']}: {e['message']}" for e in errors))
+
+
 class NotFound(Exception):
     def __init__(self, message: str = "not found"):
         self.message = message
@@ -79,6 +87,10 @@ def install(app: FastAPI) -> None:
             status_code=422, content={"errors": [{"field": exc.field, "message": exc.message}]}
         )
 
+    @app.exception_handler(FieldErrors)
+    async def _fields(request: Request, exc: FieldErrors):
+        return JSONResponse(status_code=422, content={"errors": exc.errors})
+
     @app.exception_handler(NotFound)
     async def _not_found(request: Request, exc: NotFound):
         return JSONResponse(status_code=404, content={"detail": exc.message})
```

Create `api/tms/api/routes/settings.py`:
```python
"""Settings an admin changes on screen in place of .env's: see
services/runtime_settings.py for which may, and how they take effect."""
from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Body, Query
from pydantic import BaseModel
from sqlalchemy import select

from tms.api.deps import DB, Principal, require
from tms.api.errors import FieldError
from tms.models import SettingChange
from tms.services import runtime_settings

router = APIRouter(tags=["settings"])


class SettingOut(BaseModel):
    name: str
    env: str
    # screen, secret or file
    kind: str
    # in force now; never a secret's, the database's address or the session key
    value: str | int | float | None
    # .env's, for one that may be changed on screen
    default: str | int | float | None
    is_set: bool
    on_screen: bool
    changed_by: str | None
    changed_at: datetime | None
    why_file_only: str | None


class ChangeOut(BaseModel):
    id: int
    name: str
    env: str
    action: str
    value: str | None
    secret: bool
    changed_by: str
    changed_at: datetime


@router.get("/settings", response_model=list[SettingOut])
def read_settings(db: DB, who: Principal = require("settings:read")):
    return runtime_settings.view(db)


@router.patch("/settings", response_model=list[SettingOut])
def change_settings(db: DB, body: Annotated[dict[str, str | int | float | None], Body()],
                    who: Principal = require("settings:write")):
    """{ "run_sheet_printer": "office", "platen_key": null }: each named
    setting given a value in place of .env's, or null for .env's again. All
    are checked, and kept, together."""
    if not body:
        raise FieldError("body", "name at least one setting to change")
    runtime_settings.change(db, body, by=who.name)
    db.commit()
    runtime_settings.committed()
    return runtime_settings.view(db)


@router.get("/settings/history", response_model=list[ChangeOut])
def settings_history(db: DB, limit: int = Query(default=50, ge=1, le=200),
                     who: Principal = require("settings:read")):
    """Newest first. A secret's value is never in it."""
    rows = db.execute(select(SettingChange).order_by(SettingChange.id.desc()).limit(limit)).scalars()
    return [ChangeOut(id=r.id, name=r.name, env=runtime_settings.env_name(r.name), action=r.action,
                      value=r.value, secret=r.secret, changed_by=r.changed_by,
                      changed_at=r.changed_at) for r in rows]
```

Change `api/tms/api/app.py`:
```diff
--- a/api/tms/api/app.py
+++ b/api/tms/api/app.py
@@ -7,7 +7,7 @@ from tms.api import errors
 from tms.api.deps import DB
 from tms.api.routes import (
     auth, carriers, consignments, deliveries, drivers, feed, fleet, location, manifests, pod_files,
-    printing, runs, vehicles, wms_events,
+    printing, runs, settings, vehicles, wms_events,
 )
 
 logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
@@ -36,6 +36,7 @@ v1.include_router(location.router)
 v1.include_router(feed.router)
 v1.include_router(carriers.router)
 v1.include_router(manifests.router)
+v1.include_router(settings.router)
 
 
 @v1.get("/health", tags=["system"])
```

Change `docs/api.md`:
````diff
--- a/docs/api.md
+++ b/docs/api.md
@@ -53,6 +53,9 @@ are at `/docs`.
 | a driver | `fleet:read`, `deliveries:read`, `deliveries:write`, `location:write` (on their own runs only) |
 | an API key | what it was made with: some of `printing:write`, `runs:read`, `runs:write`, `consignments:read`, `fleet:read`, `deliveries:read`, `deliveries:write`, `location:read`, `location:write` |
 
+`settings:read` and `settings:write` (the Settings drawer, below) come only
+with `*`: an admin's. No key is ever made with them.
+
 `location:read` is where the trucks on the road are: personal information
 about the drivers, so a key has it only when it is named. `location:write`
 is sending where a run is; a key has it only when it is named, too.
@@ -1041,6 +1044,49 @@ failed, with the reason, once its retries run out.
 A delivery has at most one consignment per owner, however many events arrive
 for it, and at once. Transfers are not consignments yet.
 
+## Settings
+
+An admin changes some of `.env`'s `TMS_*` settings on screen, without
+editing `.env` or restarting anything. `.env` gives each its default; a
+value given here is used in its place by the API and the worker (each reads
+them again within 5 seconds) until it is put back to `.env`'s. Three kinds:
+
+| Kind | Settings | On screen |
+| --- | --- | --- |
+| `screen` | `wms_url`, `wms_timeout_seconds`, `platen_url`, `run_sheet_printer`, `manifest_printer`, `position_retention_days`, `eta_minutes_per_stop`, `worker_poll_seconds`, `worker_http_timeout_seconds` | shown and changed |
+| `secret` | `wms_api_key`, `wms_webhook_secret`, `platen_key` | replaced, never shown again |
+| `file` | `database_url`, `secret_key`, `pod_dir`, `map_tile_url`, `map_tile_origin`, `map_attribution` | `.env` only |
+
+A `file` one could lock everyone out, break the app or weaken security, or is
+read by something else when it starts: Caddy builds the desktop's
+Content-Security-Policy from `TMS_MAP_TILE_ORIGIN`, which must be the tile
+URL's own origin, so the map's three stay together in `.env`. (The ports,
+`TMS_DOMAIN` and `POSTGRES_*` are Compose's and Caddy's, not the API's.)
+
+- `GET /v1/settings` (`settings:read`) — every setting:
+  ```json
+  [{ "name": "run_sheet_printer", "env": "TMS_RUN_SHEET_PRINTER", "kind": "screen",
+     "value": "Office 2", "default": "dispatch", "is_set": true, "on_screen": true,
+     "changed_by": "leighton", "changed_at": "…", "why_file_only": null }]
+  ```
+  `value` is what is in force and `default` what `.env` says; both are
+  `null` for a secret, and `value` for `database_url` and `secret_key`.
+  `is_set` says whether a secret has a value at all.
+- `PATCH /v1/settings` (`settings:write`) — `{ "run_sheet_printer": "Office 2",
+  "platen_key": null }`: each named setting given a value, or `null` for
+  `.env`'s again. They are checked together with `config.py`'s own rules and
+  kept all or none: `422` with a field error on each that is wrong, a `file`
+  one ("TMS_SECRET_KEY is set in .env only: …"), or a name that isn't a
+  setting. A secret can't be blank; `wms_webhook_secret` is 16 to 128
+  characters, as Simple WMS's subscriber secret is. A new `wms_url` or
+  `platen_url` needs its key (`wms_api_key`, `platen_key`) given again in
+  the same request (`422` on the key otherwise), so a key on file is never
+  sent to an address it wasn't given for. Answers as `GET`.
+- `GET /v1/settings/history?limit=50` (`settings:read`) — each change, newest
+  first: `[{ "id", "name", "env", "action": "set" | "reset", "value",
+  "secret", "changed_by", "changed_at" }]`. A secret's `value` is always
+  `null`: only its current value is kept, and only to be used.
+
 ## API keys
 
 For a system rather than a person; today, Platen saying how a run sheet
````

**Step 4: Run them, then the suite**

Run: `.venv/bin/pytest tests/test_settings.py -q`
Expected: `17 passed`.
Run: `caffeinate -i .venv/bin/pytest -q`
Expected: `442 passed`.

**Step 5: Commit**

```bash
git add api/tms/api/routes/settings.py api/tms/services/runtime_settings.py api/tms/api/errors.py api/tms/api/app.py \
        docs/api.md api/tests/test_settings.py
git commit -m "Settings: read and change them on screen, an admin's only, secrets never shown again"
```

### Task 11: The Settings drawer

Decision 15. **Settings** in the account menu for `settings:write` (an admin's `*`); `/settings` opened by anyone else says "Settings are for an admin." and asks the API nothing. Each group saves only what was changed in it (a secret only when something is typed in it); the API's field errors go beside their fields. The groups' **Save** buttons are named for their group ("Save Printing through Platen") for a screen reader.

**Files:**
- Create: `apps/desktop/src/lib/settings.ts`, `apps/desktop/src/pages/Settings.tsx`
- Modify: `apps/desktop/src/App.tsx`, `apps/desktop/src/pages/MapHome.tsx`, `apps/desktop/src/api/types.ts`
- Test: `apps/desktop/src/test/Settings.test.tsx` (new)

**Step 1: Write the failing tests**

Create `apps/desktop/src/test/Settings.test.tsx`:
```tsx
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { SettingRow } from "../api/types";
import { changesFor } from "../lib/settings";
import { json, MANAGER, path, renderApp, stubApi, type Call } from "./harness";

const ADMIN = { ...MANAGER, id: 3, name: "leighton", display_name: "Leighton", role: "admin", scopes: ["*"] };

const row = (name: string, kind: SettingRow["kind"], value: SettingRow["value"], over: Partial<SettingRow> = {}): SettingRow => ({
  name, env: `TMS_${name.toUpperCase()}`, kind, value, default: kind === "screen" ? value : null,
  is_set: kind === "secret" ? false : value !== "" && value !== null, on_screen: false, changed_by: null, changed_at: null,
  why_file_only: null, ...over,
});

const SETTINGS: SettingRow[] = [
  row("wms_url", "screen", "https://wms.example.com"), row("wms_api_key", "secret", null, { is_set: true }),
  row("wms_webhook_secret", "secret", null), row("wms_timeout_seconds", "screen", 5),
  row("platen_url", "screen", ""), row("platen_key", "secret", null),
  row("run_sheet_printer", "screen", "Office 2", { default: "dispatch", on_screen: true, changed_by: "leighton", changed_at: new Date().toISOString() }),
  row("manifest_printer", "screen", ""),
  row("position_retention_days", "screen", 90), row("eta_minutes_per_stop", "screen", 20),
  row("worker_poll_seconds", "screen", 5), row("worker_http_timeout_seconds", "screen", 10),
  row("database_url", "file", null, { why_file_only: "the API and the worker need it to reach the database at all" }),
  row("secret_key", "file", null, { why_file_only: "it signs every session: a wrong one signs everyone out" }),
  row("map_tile_origin", "file", "https://tiles.example.com", { why_file_only: "Caddy reads it when it starts, for the desktop's Content-Security-Policy" }),
];

function office(answers: Record<string, (c: Call) => Response> = {}, who: object = ADMIN) {
  return stubApi((method, url, body) => {
    const key = `${method} ${path(url)}`;
    if (answers[key]) return answers[key]({ method, url, body });
    if (key === "GET /v1/settings") return json(200, SETTINGS);
    if (key === "GET /v1/settings/history") {
      return json(200, [{ id: 2, name: "platen_key", env: "TMS_PLATEN_KEY", action: "set", value: null, secret: true,
                          changed_by: "leighton", changed_at: new Date().toISOString() },
                        { id: 1, name: "run_sheet_printer", env: "TMS_RUN_SHEET_PRINTER", action: "set", value: "Office 2",
                          secret: false, changed_by: "leighton", changed_at: new Date().toISOString() }]);
    }
    return null;
  }, who);
}

describe("Settings", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("sends what was changed, a secret only when typed", () => {
    expect(changesFor(SETTINGS, { run_sheet_printer: "Office 2", wms_timeout_seconds: " 8 ", wms_api_key: "" })).toEqual({ wms_timeout_seconds: "8" });
    expect(changesFor(SETTINGS, { platen_key: "plt_new" })).toEqual({ platen_key: "plt_new" });
  });

  it("is in the account menu for an admin only", async () => {
    office({}, MANAGER);
    renderApp("/");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: /^Account/ }));
    expect(screen.getByRole("button", { name: "Sign out" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Settings" })).not.toBeInTheDocument();
  });

  it("opens from the account menu, shows where each comes from, and never a secret", async () => {
    office();
    renderApp("/");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: /^Account/ }));
    await user.click(screen.getByRole("link", { name: "Settings" }));
    expect(await screen.findByRole("dialog", { name: "Settings" })).toBeInTheDocument();
    const printing = await screen.findByRole("form", { name: "Printing through Platen" });
    expect(within(printing).getByLabelText("Run sheet printer")).toHaveValue("Office 2");
    expect(within(printing).getByLabelText("Run sheet printer")).toHaveAccessibleDescription(/Set here by leighton, Today .*\. \.env says dispatch\./);
    expect(within(printing).getByLabelText("Carrier manifest printer")).toHaveAccessibleDescription(/From \.env \(TMS_MANIFEST_PRINTER\)\./);
    const wms = screen.getByRole("form", { name: "Simple WMS" });
    const key = within(wms).getByLabelText("Simple WMS key");
    expect(key).toHaveAttribute("type", "password");
    expect(key).toHaveValue("");
    expect(key).toHaveAttribute("placeholder", "Set: type a new one to replace it");
    const fileOnly = screen.getByRole("region", { name: "In .env only" });
    expect(fileOnly).toHaveTextContent("Map tiles' origin TMS_MAP_TILE_ORIGIN · https://tiles.example.com");
    expect(fileOnly).toHaveTextContent("it signs every session");
    expect(screen.getByText("Platen key: replaced")).toBeInTheDocument();
  });

  it("saves a group's changes together, and shows the API's refusal beside the field", async () => {
    const { calls } = office({
      "PATCH /v1/settings": (c) => ((c.body as Record<string, string>).platen_url && !(c.body as Record<string, string>).platen_key
        ? json(422, { errors: [{ field: "platen_key", message: "Type the Platen key again with a new address: a key on file is only sent where it was given for" }] })
        : json(200, SETTINGS)),
    });
    renderApp("/settings");
    const user = userEvent.setup();
    const printing = await screen.findByRole("form", { name: "Printing through Platen" });
    await user.type(within(printing).getByLabelText("Platen job intake"), "https://platen.example.com/intake/jobs");
    await user.click(within(printing).getByRole("button", { name: "Save Printing through Platen" }));
    await waitFor(() => expect(within(printing).getByLabelText("Platen key")).toHaveAccessibleDescription(
      /Type the Platen key again with a new address/));
    await waitFor(() => expect(within(printing).getByLabelText("Platen key")).toHaveFocus());

    await user.type(within(printing).getByLabelText("Platen key"), "plt_new-key");
    await user.click(within(printing).getByRole("button", { name: "Save Printing through Platen" }));
    expect(await screen.findByText("Saved. The API and the worker use it within 5 seconds.")).toHaveAttribute("role", "status");
    expect(calls.filter((c) => c.method === "PATCH").map((c) => c.body)).toEqual([
      { platen_url: "https://platen.example.com/intake/jobs" },
      { platen_url: "https://platen.example.com/intake/jobs", platen_key: "plt_new-key" }]);
    expect(within(printing).getByLabelText("Platen key")).toHaveValue("");
  });

  it("puts one back to .env's", async () => {
    const { calls } = office({ "PATCH /v1/settings": () => json(200, SETTINGS) });
    renderApp("/settings");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Use .env's run sheet printer" }));
    expect(await screen.findByText("Run sheet printer is .env's again.")).toBeInTheDocument();
    expect(calls.find((c) => c.method === "PATCH")!.body).toEqual({ run_sheet_printer: null });
  });

  it("tells anyone else it is for an admin, and asks the API nothing", async () => {
    const { calls } = office({}, MANAGER);
    renderApp("/settings");
    expect(await screen.findByText("Settings are for an admin.")).toBeInTheDocument();
    expect(calls.some((c) => path(c.url) === "/v1/settings")).toBe(false);
  });
});
```

**Step 2: Run them to see them fail**

Run: `npx vitest run src/test/Settings.test.tsx`
Expected: the file fails to load: `Failed to resolve import "../lib/settings"`.

**Step 3: Implement**

Change `apps/desktop/src/api/types.ts`:
```diff
--- a/apps/desktop/src/api/types.ts
+++ b/apps/desktop/src/api/types.ts
@@ -254,3 +254,30 @@ export interface FeedItem {
   manifest_no?: string | null;
   count?: number | null;
 }
+
+/** A TMS_* setting (GET /v1/settings): `screen` changed on screen, `secret`
+ * replaced but never shown, `file` in .env only. */
+export interface SettingRow {
+  name: string;
+  env: string;
+  kind: "screen" | "secret" | "file";
+  value: string | number | null;
+  default: string | number | null;
+  is_set: boolean;
+  on_screen: boolean;
+  changed_by: string | null;
+  changed_at: string | null;
+  why_file_only: string | null;
+}
+
+/** One change to a setting (GET /v1/settings/history): a secret's value never. */
+export interface SettingChange {
+  id: number;
+  name: string;
+  env: string;
+  action: "set" | "reset";
+  value: string | null;
+  secret: boolean;
+  changed_by: string;
+  changed_at: string;
+}
```

Create `apps/desktop/src/lib/settings.ts`:
```ts
/** The Settings drawer's words for each TMS_* setting, and what a save
 * sends. Which may be changed, and how, is the API's (GET /v1/settings). */
import type { SettingRow } from "../api/types";

export interface Copy { label: string; hint?: string; numeric?: boolean }

export const COPY: Record<string, Copy> = {
  wms_url: { label: "Simple WMS address", hint: "Where Simple WMS's API is, e.g. https://wms.example.com. Empty: Simple TMS works without it." },
  wms_api_key: { label: "Simple WMS key", hint: "A WMS API key with tasks:read and tasks:write." },
  wms_webhook_secret: { label: "Webhook secret", hint: "The Simple TMS subscriber's secret in Simple WMS, 16 to 128 characters." },
  wms_timeout_seconds: { label: "Wait for Simple WMS (seconds)", numeric: true, hint: "Up to 60. Keep it under Simple WMS's own 10 s wait." },
  platen_url: { label: "Platen job intake", hint: "e.g. https://platen.example.com/intake/jobs. Empty: nothing is printed." },
  platen_key: { label: "Platen key", hint: "An operator key from Platen's People > Keys." },
  run_sheet_printer: { label: "Run sheet printer", hint: "A Platen printer's id or name." },
  manifest_printer: { label: "Carrier manifest printer", hint: "Empty: the run sheet printer." },
  position_retention_days: { label: "Keep positions (days)", numeric: true,
                             hint: "Positions older than this are deleted within the hour, and can't be brought back." },
  eta_minutes_per_stop: { label: "Minutes a stop takes, to start with", numeric: true, hint: "1 to 600: the ETA's guess until a driver has done a stop." },
  worker_poll_seconds: { label: "Look for work every (seconds)", numeric: true, hint: "1 to 300, when there was nothing to send." },
  worker_http_timeout_seconds: { label: "Wait for an answer (seconds)", numeric: true, hint: "Up to 120, for each call to Simple WMS or Platen." },
  database_url: { label: "Database" },
  secret_key: { label: "Session key" },
  pod_dir: { label: "Proof of delivery files" },
  map_tile_url: { label: "Map tiles" },
  map_tile_origin: { label: "Map tiles' origin" },
  map_attribution: { label: "Map credit" },
};

export const GROUPS: { title: string; names: string[] }[] = [
  { title: "Simple WMS", names: ["wms_url", "wms_api_key", "wms_webhook_secret", "wms_timeout_seconds"] },
  { title: "Printing through Platen", names: ["platen_url", "platen_key", "run_sheet_printer", "manifest_printer"] },
  { title: "Live location and ETAs", names: ["position_retention_days", "eta_minutes_per_stop"] },
  { title: "The worker", names: ["worker_poll_seconds", "worker_http_timeout_seconds"] },
];

/** What a setting shows in its box: its value in force, or nothing for a secret. */
export const shown = (s: SettingRow) => (s.kind === "secret" || s.value === null ? "" : String(s.value));

/** The changes to send for a group: an on-screen setting typed differently
 * from what is in force, and a secret typed at all. */
export function changesFor(rows: SettingRow[], drafts: Record<string, string>): Record<string, string> {
  const out: Record<string, string> = {};
  for (const s of rows) {
    const typed = drafts[s.name];
    if (typed === undefined) continue;
    if (s.kind === "secret" ? typed !== "" : typed.trim() !== shown(s)) out[s.name] = typed.trim();
  }
  return out;
}
```

Create `apps/desktop/src/pages/Settings.tsx`:
```tsx
import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { api } from "../api/client";
import type { SettingChange, SettingRow } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { apiProblems, said, type Problems } from "../lib/admin";
import { when } from "../lib/fleet";
import { changesFor, COPY, GROUPS, shown } from "../lib/settings";
import { Drawer } from "../ui/Drawer";
import { Field, focusFirstProblem } from "../ui/Field";

type Note = { tone: "r" | "g"; text: string } | null;

function source(s: SettingRow): string {
  if (!s.on_screen) return `From .env (${s.env}).`;
  const was = s.kind === "secret" ? "" : ` .env says ${s.default === "" || s.default === null ? "nothing" : String(s.default)}.`;
  return `Set here by ${s.changed_by}, ${when(s.changed_at!)}.${was}`;
}

/** One group of settings, saved together. */
function Group({ title, rows, onSaved, onReset }: {
  title: string; rows: SettingRow[];
  onSaved: (rows: SettingRow[]) => void; onReset: (s: SettingRow) => Promise<void>;
}) {
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [problems, setProblems] = useState<Problems>({});
  const [trouble, setTrouble] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const form = useRef<HTMLFormElement>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setTrouble(null);
    setProblems({});
    const body = changesFor(rows, drafts);
    if (!Object.keys(body).length) { setTrouble("Nothing here has changed."); return; }
    setBusy(true);
    try {
      onSaved(await api.patch<SettingRow[]>("/v1/settings", body));
      setDrafts({});
    } catch (err) {
      const theirs = apiProblems(err);
      if (theirs) {
        const { body: whole, ...fields } = theirs;
        setProblems(fields);
        if (whole) setTrouble(whole);
        focusFirstProblem(form.current);
      } else setTrouble(said(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form ref={form} className="adminform" aria-label={title} noValidate onSubmit={(e) => void submit(e)}>
      <div className="eyebrow">{title}</div>
      {rows.map((s) => {
        const copy = COPY[s.name] ?? { label: s.env };
        const secret = s.kind === "secret";
        return (
          <div key={s.name} className="acts" style={{ alignItems: "flex-end" }}>
            <div style={{ flex: "1 1 280px" }}>
              <Field label={copy.label} name={s.name} value={drafts[s.name] ?? shown(s)} error={problems[s.name]}
                     type={secret ? "password" : "text"} autoComplete={secret ? "new-password" : "off"}
                     inputMode={copy.numeric ? "decimal" : undefined} spellCheck={false}
                     placeholder={secret ? (s.is_set ? "Set: type a new one to replace it" : "Not set") : undefined}
                     onChange={(e) => setDrafts((d) => ({ ...d, [s.name]: e.target.value }))}
                     hint={<>{copy.hint ? `${copy.hint} ` : ""}{secret ? "Never shown again. " : ""}{source(s)}</>} />
            </div>
            {s.on_screen && (
              <button type="button" className="btn sm" disabled={busy} aria-label={`Use .env's ${copy.label.toLowerCase()}`}
                      onClick={() => void onReset(s)}>Use .env's</button>)}
          </div>
        );
      })}
      {trouble && <div className="msg r" role="alert">{trouble}</div>}
      <div className="acts">
        <button type="submit" className="btn primary" disabled={busy} aria-label={`Save ${title}`}>{busy ? "Saving…" : "Save"}</button>
      </div>
    </form>
  );
}

function historyLine(c: SettingChange): string {
  const label = COPY[c.name]?.label ?? c.env;
  if (c.action === "reset") return `${label}: back to .env's`;
  return c.secret ? `${label}: replaced` : `${label}: ${c.value === "" ? "emptied" : c.value}`;
}

/** Settings an admin may change without editing .env (GET and PATCH
 * /v1/settings). Opened from the account menu; admins only. */
export function Settings() {
  const { can } = useAuth();
  const admin = can("settings:write");
  const [rows, setRows] = useState<SettingRow[] | null>(null);
  const [history, setHistory] = useState<SettingChange[]>([]);
  const [note, setNote] = useState<Note>(null);

  const load = useCallback(async () => {
    const [s, h] = await Promise.all([api.get<SettingRow[]>("/v1/settings"),
                                      api.get<SettingChange[]>("/v1/settings/history", { limit: 10 })]);
    setRows(s);
    setHistory(h);
  }, []);

  useEffect(() => {
    if (admin) load().catch((e) => setNote({ tone: "r", text: said(e) }));
  }, [admin, load]);

  async function saved(fresh: SettingRow[]) {
    setRows(fresh);
    setNote({ tone: "g", text: "Saved. The API and the worker use it within 5 seconds." });
    await load().catch(() => undefined);
  }

  async function reset(s: SettingRow) {
    try {
      await saved(await api.patch<SettingRow[]>("/v1/settings", { [s.name]: null }));
      setNote({ tone: "g", text: `${COPY[s.name]?.label ?? s.env} is .env's again.` });
    } catch (e) {
      setNote({ tone: "r", text: said(e) });
    }
  }

  const all = rows ?? [];
  const fileOnly = all.filter((s) => s.kind === "file");
  return (
    <Drawer label="Settings" head={
      <div style={{ flex: 1, minWidth: 200 }}>
        <div className="eyebrow" style={{ color: "var(--color-green)" }}>Admin</div>
        <h1>Settings</h1>
        <div className="sub">Change these here instead of in .env. .env gives each its default; Use .env's puts one back.</div>
      </div>
    }>
      {!admin && <div className="msg a" role="status">Settings are for an admin.</div>}
      {note && <div className={`msg ${note.tone}`} role={note.tone === "r" ? "alert" : "status"}>{note.text}</div>}
      {rows && GROUPS.map((g) => (
        <Group key={g.title} title={g.title} rows={g.names.map((n) => all.find((s) => s.name === n)).filter((s): s is SettingRow => !!s)}
               onSaved={(fresh) => void saved(fresh)} onReset={reset} />))}
      {rows && (
        <section className="adminform" aria-label="In .env only">
          <div className="eyebrow">In .env only</div>
          <div className="small">These could lock everyone out, break the app or weaken security, or something else reads them
            when it starts. Change them in .env and restart: <span className="mono">docker compose up -d</span>.</div>
          <div className="box">
            {fileOnly.map((s) => (
              <div key={s.name} style={{ flexDirection: "column", alignItems: "flex-start", gap: 2 }}>
                <span><b>{COPY[s.name]?.label ?? s.env}</b> <span className="mono">{s.env}</span>{s.value !== null && s.value !== "" &&
                  <>{" · "}<span className="mono" style={{ wordBreak: "break-all" }}>{String(s.value)}</span></>}</span>
                <span className="small">{s.why_file_only}</span>
              </div>))}
            <div style={{ flexDirection: "column", alignItems: "flex-start", gap: 2 }}>
              <span><b>Ports, domain and database password</b> <span className="mono">TMS_DOMAIN, TMS_HTTP_PORT, TMS_HTTPS_PORT, TMS_API_PORT, POSTGRES_*</span></span>
              <span className="small">Compose and Caddy read them when they start.</span>
            </div>
          </div>
        </section>
      )}
      {history.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <div className="eyebrow">RECENT CHANGES</div>
          <div className="box">
            {history.map((c) => (
              <div key={c.id}><span>{historyLine(c)}</span>
                <span className="small" style={{ whiteSpace: "nowrap" }}>{when(c.changed_at)} · {c.changed_by}</span></div>))}
          </div>
        </div>
      )}
    </Drawer>
  );
}
```

Change `apps/desktop/src/App.tsx`:
```diff
--- a/apps/desktop/src/App.tsx
+++ b/apps/desktop/src/App.tsx
@@ -6,6 +6,7 @@ import { Consignments } from "./pages/Consignments";
 import { Fleet } from "./pages/Fleet";
 import { MapHome } from "./pages/MapHome";
 import { PlanRuns } from "./pages/PlanRuns";
+import { Settings } from "./pages/Settings";
 import { SignIn } from "./pages/SignIn";
 import { WhereIsIt } from "./pages/WhereIsIt";
 
@@ -22,6 +23,7 @@ export function AppRoutes() {
           <Route path="/carriers" element={<Carriers />} />
           <Route path="/fleet" element={<Fleet />} />
           <Route path="/where" element={<WhereIsIt />} />
+          <Route path="/settings" element={<Settings />} />
         </Route>
       </Route>
       <Route path="*" element={<Navigate to="/" replace />} />
```

Change `apps/desktop/src/pages/MapHome.tsx`:
```diff
--- a/apps/desktop/src/pages/MapHome.tsx
+++ b/apps/desktop/src/pages/MapHome.tsx
@@ -118,7 +118,11 @@ export function MapHome() {
               <div className="panel" style={{ position: "absolute", right: 0, top: 52, zIndex: 20, background: "#fff",
                                                borderRadius: 14, boxShadow: "var(--shadow-lift)", padding: 12, minWidth: 180 }}>
                 <div className="small" style={{ marginBottom: 8 }}>{user?.display_name} · {user?.role}</div>
-                <button type="button" className="btn sm" onClick={() => void signOut()}>Sign out</button>
+                <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
+                  {can("settings:write") && (
+                    <NavLink to="/settings" className="btn sm" onClick={() => setMenu(false)}>Settings</NavLink>)}
+                  <button type="button" className="btn sm" onClick={() => void signOut()}>Sign out</button>
+                </div>
               </div>
             )}
           </div>
```

**Step 4: Run them, then the suite**

Run: `npx vitest run src/test/Settings.test.tsx`
Expected: `Tests  6 passed (6)`.
Run: `npm test && npm run typecheck && npm run build`
Expected: `Tests  160 passed (160)`, no type errors, and the build ends `✓ built in …`.

**Step 5: Commit**

```bash
git add apps/desktop/src/lib/settings.ts apps/desktop/src/pages/Settings.tsx apps/desktop/src/App.tsx \
        apps/desktop/src/pages/MapHome.tsx apps/desktop/src/api/types.ts \
        apps/desktop/src/test/Settings.test.tsx
git commit -m "A Settings drawer for an admin, from the account menu"
```

## Part D — docs and checks

### Task 12: README and `.env.example`

The two Known limitations bullets this work makes untrue go ("Fleet doesn't add vehicles or drivers yet", "Carriers are added through the API"), with one in their place for what is still on the command line (users, Decision 9). Getting started, the driver's PIN, fleet care and Carriers step 1 point at the screens instead of `curl`; a **Settings** paragraph says what an admin can change there and what stays in `.env`; Connecting Simple WMS and Connecting Platen say the screen is the other way to set them. `.env.example` says which of its settings an admin can change on screen.

**Files:**
- Modify: `README.md`, `.env.example`

**Step 1: Change them**

Change `README.md`:
````diff
--- a/README.md
+++ b/README.md
@@ -48,7 +48,8 @@ docker compose exec api tms create-user --username you --role admin
 ```
 
 The desktop is at `https://<TMS_DOMAIN>/` (default `https://localhost/`,
-self-signed): sign in, then **Plan runs**. The API runs its migrations each
+self-signed): sign in, add your trucks and drivers in **Fleet** and the
+carriers you use in **Carriers**, then **Plan runs**. The API runs its migrations each
 time it starts; it is at `/v1`, its interactive docs at `/docs`, and it is
 also published on `127.0.0.1:8022` (`TMS_API_PORT`) for local work. The
 `worker` makes the calls to Simple WMS and sends run sheets and carrier
@@ -58,8 +59,9 @@ past the retention period; it needs no port. Rebuild the desktop (`npm run build
 `apps/desktop`; Caddy serves `apps/desktop/dist` as it is.
 
 The driver app is at `https://<TMS_DOMAIN>/driver/`. A driver signs in with
-their code (in any case) and PIN (given when the driver is made, `POST /v1/drivers`, or
-changed by a manager, `PATCH /v1/drivers/{code}` with a new `pin`), starts
+their code (in any case) and PIN (given when a fleet manager adds them in
+**Fleet** → **Drivers**, and set again there, which also unlocks a driver
+locked out by five wrong PINs), starts
 the day's run, and works through it one stop at a time; it keeps working with
 no signal and sends what waited when the signal is back, each confirmation
 once however often it is sent. A consignment that couldn't be delivered goes
@@ -85,11 +87,25 @@ office gives the run another truck meanwhile, the check (and any grounding)
 stays with the truck inspected, and the phone asks for a fresh check of the
 run's new truck. In **Fleet** a fleet manager also records each new rego expiry and
 each service (with its odometer reading), and puts a wrong odometer reading
-right; set how often a truck is serviced with `service_every_months` and/or
-`service_every_km` on `PATCH /v1/vehicles/{rego}`. A truck with nothing
-recorded is never due, so record each truck's rego expiry and last service
-when you start. The checklists are the design's six items for each type of
-vehicle; change them with `PUT /v1/checklists/{type}` (see `docs/api.md`).
+right, and adds, changes (type, limits, how often it is serviced),
+deactivates and reactivates trucks. A truck with nothing recorded is never
+due, so record each truck's rego expiry and last service when you start. The
+checklists start as the design's six items for each type of vehicle; a fleet
+manager changes them in **Fleet** → **Pre-start checklists** (a line taken out
+is retired, and checks already done keep it). A truck or a driver with a run
+planned or on the road can't be deactivated: **Fleet** names the run to give
+another truck or driver, or to finish, first.
+
+**Settings.** An admin changes some of `.env`'s settings in **Settings**
+(the account menu, top right) without editing `.env` or restarting:
+printers, the Simple WMS and Platen addresses and keys, waits, how long
+positions are kept and the ETA's first guess. `.env` gives each its default
+and **Use .env's** puts one back; the API and the worker take a change within
+5 seconds. Keys and secrets can be replaced there but are never shown again.
+The database, `TMS_SECRET_KEY`, the proof-of-delivery folder, the map tiles
+(Caddy builds the desktop's Content-Security-Policy from them when it
+starts), the ports and the domain stay in `.env` only. Each change is
+recorded with who made it (`docs/api.md`, "Settings").
 
 **Live location.** From **Start run** until the run is finished, the driver's
 phone tells the office where it is about every 30 seconds, and its top bar
@@ -284,7 +300,7 @@ Windows).
 ## Connecting Simple WMS
 
 Simple TMS is an ordinary subscriber to Simple WMS's signed events. In `.env`
-set `TMS_WMS_WEBHOOK_SECRET` (a long random string), `TMS_WMS_URL` (Simple
+(or as an admin, in **Settings**) set `TMS_WMS_WEBHOOK_SECRET` (a long random string), `TMS_WMS_URL` (Simple
 WMS's address) and `TMS_WMS_API_KEY` (a Simple WMS key with `tasks:read` and
 `tasks:write`: `tasks:read` to read a packed delivery's ship-to, `tasks:write`
 to say it shipped), then register a subscriber in Simple WMS with the same
@@ -302,7 +318,8 @@ call goes with the carrier's name and the con note as the tracking number.
 Run sheets print through Platen (or any print service that takes the same
 job). In Platen, make a page template with the id `run-sheet` and publish it
 (the fields it binds to are in `docs/api.md` under "Run sheets"), and make an
-operator key under People → Keys. In Simple TMS's `.env` set
+operator key under People → Keys. In Simple TMS's `.env` (or as an admin, in
+**Settings**, with no restart) set
 `TMS_PLATEN_URL=https://<platen>/intake/jobs`, `TMS_PLATEN_KEY` to that key
 and `TMS_RUN_SHEET_PRINTER` to the Platen printer's id or name, then
 `docker compose up -d api worker`.
@@ -364,13 +381,11 @@ Freight going with outside carriers is booked by hand in this version: book
 with the carrier as you always have (its website or a phone call), then in
 Simple TMS:
 
-1. **Add the carriers** you use, once, as a fleet manager or admin (there is
-   no screen for it yet):
-   ```
-   curl -s -X POST https://tms.example.com/v1/carriers -H "Authorization: Bearer <token>" \
-     -H 'Content-Type: application/json' \
-     -d '{"name": "<the carrier, as Simple WMS should record it>", "account": "<your account no.>", "pickup_time": "15:30"}'
-   ```
+1. **Add the carriers** you use, once, as a fleet manager or admin: in
+   **Carriers** → **Your carriers**, **Add a carrier** with its name as Simple
+   WMS should record it, your account number and when it usually calls.
+   Rename or retire one there too (a retired carrier takes no new bookings;
+   what is on its bay stays, to be collected).
 2. In **Plan runs**, send a consignment **With a carrier…** (from the tray,
    or off a planned run with **Move to…**). It goes on that carrier's
    manifest for the day being planned, and waits on the carrier bay.
@@ -522,9 +537,9 @@ stood in for by `fake-indexeddb`: `cd apps/driver && npm test`.
   the desktop (for a driver without a phone, who does a paper check) sends it
   without one. **Plan runs** says "No pre-start yet" on such a run. A
   grounded or overdue truck is refused either way.
-- **Fleet doesn't add vehicles or drivers yet.** Add them, and set how often
-  each is serviced, through the API (`POST /v1/vehicles`, `POST /v1/drivers`,
-  `PATCH …`); the checklists too (`PUT /v1/checklists/{type}`).
+- **Desktop users are made on the command line.** Dispatchers, fleet
+  managers and admins are added with `tms create-user` (see Getting started);
+  there is no screen for them yet.
 - **Due dates are on screen only.** The `vehicle.due` event in the design, for
   another system to send email or SMS, comes with the first system that
   subscribes.
@@ -535,8 +550,6 @@ stood in for by `fake-indexeddb`: `cd apps/driver && npm test`.
   consignment stays "Collected" in Simple TMS, and the carrier's own
   tracking says where it is. A carrier's own API can come later as an
   adapter (`api/tms/services/carrier_adapters.py`) without changing the data.
-- **Carriers are added through the API.** There is no screen to add or
-  change a carrier yet (see "Carriers" above; `docs/api.md` has the rest).
 - **Back from a run, then with a carrier: Simple WMS keeps the run.** A
   consignment that couldn't be delivered can go with a carrier next, but
   Simple WMS already has it as shipped by the run (`Own fleet`, `R-0007`)
````

Change `.env.example`:
```diff
--- a/.env.example
+++ b/.env.example
@@ -1,4 +1,10 @@
 # Copy to .env and fill in. Nothing here is committed.
+#
+# An admin can change some of these on screen (Settings, in the account
+# menu), without a restart: the printers, the Simple WMS and Platen
+# addresses and keys, the waits, TMS_POSITION_RETENTION_DAYS and
+# TMS_ETA_MINUTES_PER_STOP. What is here is then the default, which
+# "Use .env's" puts back. The rest are read from here only.
 
 # Postgres (used by the db and api containers)
 POSTGRES_DB=tms
```

**Step 2: Check nothing still says there is no screen**

Run: `grep -n "no screen\|through the API\|POST /v1/vehicles\|POST /v1/drivers\|POST /v1/carriers\|PUT /v1/checklists" README.md`
Expected: one line, "there is no screen for them yet." in the users bullet.

**Step 3: Commit**

```bash
git add README.md .env.example
git commit -m "README: vehicles, drivers, checklists and carriers on screen, and Settings"
```

### Task 13: Every suite, a clean Compose start, and a setting there and back

**Step 1: The suites, the type check and the builds**

```
cd api && caffeinate -i .venv/bin/pytest -q          # expect: 442 passed
cd ../apps/desktop && npm test && npm run typecheck && npm run build   # expect: 160 passed, no errors, ✓ built
cd ../driver && npm test && npm run build            # expect: 109 passed, ✓ built (it doesn't change)
```

**Step 2: The local stack on this branch**

```
cd "/Users/leightonlauton/Q7 Technology/Software/q7/simple-tms"
caffeinate -i docker compose up -d --build --wait
docker compose ps --format '{{.Name}} {{.Status}}'
docker compose exec -T db psql -U tms -d tms -c "select version_num from alembic_version"
```
Expected: `api`, `worker`, `db` and `caddy` up (`api` and `db` healthy), and `0010`. Caddy serves the freshly built `apps/desktop/dist`.

**Step 3: A setting there and back, through the API**

```
A=http://127.0.0.1:8022
token() { curl -s -X POST $A/v1/auth/login -H 'Content-Type: application/json' \
  -d "{\"username\":\"$1\",\"password\":\"$2\"}" | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])'; }
ADMIN=$(token leighton "a long enough password"); DEE=$(token dee "dispatch password")
set_it() { curl -s -X PATCH $A/v1/settings -H "Authorization: Bearer $ADMIN" -H 'Content-Type: application/json' -d "$1" \
  | python3 -c 'import sys,json;print([(s["name"],s["value"],s["on_screen"]) for s in json.load(sys.stdin) if s["name"]=="worker_poll_seconds"])'; }
set_it '{"worker_poll_seconds": 2}'
set_it '{"worker_poll_seconds": null}'
curl -s -X PATCH $A/v1/settings -H "Authorization: Bearer $ADMIN" -H 'Content-Type: application/json' -d '{"secret_key": "x"}'; echo
curl -s $A/v1/settings -H "Authorization: Bearer $DEE"; echo
```
Expected: `[('worker_poll_seconds', 2.0, True)]`, then `[('worker_poll_seconds', 5.0, False)]` (or your `.env`'s), then `{"errors":[{"field":"secret_key","message":"TMS_SECRET_KEY is set in .env only: it signs every session: a wrong one signs everyone out"}]}`, then `{"detail":"you do not have the settings:read scope"}`.

That the running worker takes a change without a restart was checked on a throwaway Compose project of this branch (Context): a ship call queued while Simple WMS wasn't set up was tried within 5 seconds of an address and key being given in **Settings** (`attempts` 0 → 1, `ConnectError: [Errno 111] Connection refused`), and the key was in neither container's log. It isn't repeated here: the local stack's Simple WMS settings are real, and pointing its worker elsewhere would spend tries of real ship calls.

### Task 14: Manual check on the local stack

Everything runs on this machine: Simple TMS on this branch (Task 13 left it up, and its data may be fresh demo data). Scripts and screenshots go in a working folder of your own in your scratchpad, never `/tmp` and never the repo: set `W` to it in each shell. Keep the Mac awake (`caffeinate -i` in front of the long commands). Docker's clock can lag after the Mac sleeps: if a time below looks wrong by minutes, `docker compose restart api worker`.

Everything the check makes is named with a suffix of your own (`S`), so a second go doesn't clash and nothing of the demo data is changed but the van checklist (a line added, then taken out again: it is retired, as any line is) and two settings (each put back to `.env`'s at the end). The truck, driver and carrier it adds are left deactivated or retired: nothing is deleted.

```
export W="<your scratchpad directory>/tms-admin-check"
mkdir -p "$W/shots"
cd "$W" && npm init -y >/dev/null && npm i playwright-core@1 >/dev/null
```
(or link the Playwright already installed: `ln -s /private/tmp/claude-501/-Users-leightonlauton-Q7-Technology-Software-q7/b99ff15f-cbf9-43e2-b895-4faddb63dc2a/scratchpad/pw/node_modules "$W/node_modules"`)

**Step 1: The browser script**

It drives the Chrome already installed: the admin adds a truck (a refused limit first), changes it, adds a driver and gives them a new PIN; a run made for that driver (by the dispatcher, through the API) keeps them from being deactivated, with the run named; the run cancelled, they are deactivated. Then a van checklist line added, made critical and moved, saved (7 lines), and taken out again (6); a carrier refused as "own fleet", added, renamed and retired; in **Settings** a minutes-per-stop of 0 refused beside its field, 25 saved, a new Platen address refused without its key, and the minutes put back to `.env`'s. Last, the dispatcher: no **Add a vehicle**, no checklists tab, no **Add a driver** or driver controls, no **Your carriers**, and `/settings` says "Settings are for an admin."; the API answers the dispatcher `403` for settings and for changing a truck.

Save as `$W/admin.mjs`:
```js
// The admin screens in a real Chrome: a fleet manager's (here the admin's)
// vehicle, driver, checklist and carrier, the run that keeps a truck from
// being deactivated, a setting changed and put back; then the dispatcher,
// who sees none of the controls.
// Env: W (working folder with node_modules and shots/), BASE (the desktop),
// A (the API), S (a suffix of your own, so a second go doesn't clash).
import { createRequire } from "module";
const { W, BASE, A, S } = process.env;
const require = createRequire(`${W}/`);
const { chromium } = require("playwright-core");
const shot = (page, name) => page.screenshot({ path: `${W}/shots/${name}.png`, fullPage: false });
const REGO = `CHK${S}`, CODE = `chk-${S}`, CARRIER = `Check Carrier ${S} (sample)`, LINE = `Tail lift ${S}`;
const today = new Date().toLocaleDateString("en-CA");

async function token(username, password) {
  const r = await fetch(`${A}/v1/auth/login`, { method: "POST", headers: { "Content-Type": "application/json" },
                                                 body: JSON.stringify({ username, password }) });
  return (await r.json()).token;
}
const call = async (tok, method, path, body) => {
  const r = await fetch(`${A}${path}`, { method, headers: { Authorization: `Bearer ${tok}`, "Content-Type": "application/json" },
                                         body: body === undefined ? undefined : JSON.stringify(body) });
  return { status: r.status, body: await r.json().catch(() => null) };
};

const browser = await chromium.launch({
  executablePath: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  headless: true, args: ["--ignore-certificate-errors"] });

async function signIn(username, password) {
  const page = await (await browser.newContext({ viewport: { width: 1440, height: 900 }, ignoreHTTPSErrors: true })).newPage();
  page.on("pageerror", (e) => console.log("pageerror:", e.message));
  page.on("dialog", (d) => { console.log("asked:", d.message()); void d.accept(); });
  await page.goto(`${BASE}/`);
  await page.getByLabel("Username").fill(username);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.getByRole("link", { name: /^Fleet/ }).waitFor();
  return page;
}
const said = async (page, text) => { await page.getByText(text).first().waitFor(); console.log("said:", text); };

// ---- the admin: a vehicle -------------------------------------------------------
const page = await signIn("leighton", "a long enough password");
await page.getByRole("link", { name: /^Fleet/ }).click();
await page.getByRole("button", { name: "Add a vehicle" }).click();
let form = page.getByRole("form", { name: "Add a vehicle" });
await form.getByLabel("Rego").fill(`chk ${S}`);
await form.getByLabel("Most it carries (kg)").fill("1200.1234");
await form.getByRole("button", { name: "Add the vehicle" }).click();
await said(page, "At most 3 decimal places.");
await shot(page, "1-vehicle-problems");
await form.getByLabel("Most it carries (kg)").fill("1,200");
await form.getByLabel("Most it carries (m³)").fill("8.5");
await form.getByLabel("Service every (months)").fill("6");
await form.getByRole("button", { name: "Add the vehicle" }).click();
await said(page, `${REGO} is added.`);
let aside = page.getByRole("complementary", { name: "Pre-start check record" });
// the new truck's row is picked once the list has it
await page.getByRole("button", { name: `Show ${REGO}`, pressed: true }).waitFor();
await aside.getByRole("button", { name: "Change type, limits or service" }).click();
form = aside.getByRole("form", { name: `Change ${REGO}` });
await form.getByLabel("Most it carries (kg)").fill("1300");
await form.getByRole("button", { name: "Save changes" }).click();
await said(page, `${REGO} is changed.`);
await aside.getByText("Van · up to 1,300 kg and 8.5 m³ · serviced every 6 months").waitFor();
console.log("aside:", await aside.getByText(/^Van · up to/).innerText());

// ---- a driver ---------------------------------------------------------------------
await page.getByRole("tab", { name: "Drivers" }).click();
await page.getByRole("button", { name: "Add a driver" }).click();
form = page.getByRole("form", { name: "Add a driver" });
await form.getByLabel("Code").fill(CODE.toUpperCase());
await form.getByLabel("Name").fill(`Check ${S}`);
await form.getByLabel("PIN").fill("2468");
await form.getByRole("button", { name: "Add the driver" }).click();
await said(page, `Check ${S} (${CODE}) is added.`);
aside = page.getByRole("complementary", { name: `Driver ${CODE}` });
await aside.getByLabel("New PIN", { exact: true }).fill("1357");
await aside.getByRole("button", { name: "Set the PIN" }).click();
await said(page, `Check ${S} has a new PIN.`);
await shot(page, "2-driver");

// ---- a run holds them: deactivating is refused, naming the run ------------------------
const dee = await token("dee", "dispatch password");
const run = await call(dee, "POST", "/v1/runs", { date: today, vehicle: REGO, driver: CODE });
console.log("run:", run.status, run.body?.run_no ?? run.body);
await aside.getByRole("button", { name: "Deactivate" }).click();
await aside.getByRole("alert").waitFor();
console.log("refused:", await aside.getByRole("alert").innerText());
await shot(page, "3-driver-on-a-run");
console.log("cancel:", (await call(dee, "POST", `/v1/runs/${run.body.id}/cancel`)).status);
await aside.getByRole("button", { name: "Deactivate" }).click();
await said(page, `Check ${S} (${CODE}) is deactivated.`);

// ---- a checklist line, added then taken out again -----------------------------------
await page.getByRole("tab", { name: "Pre-start checklists" }).click();
let list = page.getByRole("region", { name: "Van checklist" });
await list.getByRole("button", { name: "Add a line" }).click();
await list.getByRole("textbox", { name: "Line 7" }).fill(LINE);
await list.getByRole("checkbox", { name: `Critical (${LINE})` }).check();
await list.getByRole("button", { name: `Move ${LINE} up` }).click();
await list.getByRole("button", { name: "Save the van checklist" }).click();
await said(page, "The van checklist is saved: 7 lines.");
await shot(page, "4-checklist");
const admin = await token("leighton", "a long enough password");
console.log("van now:", (await call(admin, "GET", "/v1/checklists")).body.van.map((l) => `${l.label}${l.critical ? "*" : ""}`).join(", "));
await list.getByRole("button", { name: `Take out ${LINE}` }).click();
await list.getByRole("button", { name: "Save the van checklist" }).click();
await said(page, "The van checklist is saved: 6 lines.");

// ---- a carrier ------------------------------------------------------------------------
await page.keyboard.press("Escape");
await page.getByRole("link", { name: /^Carriers/ }).click();
const yours = page.getByRole("region", { name: "Your carriers" });
await yours.getByRole("button", { name: "Add a carrier" }).click();
form = yours.getByRole("form", { name: "Add a carrier" });
await form.getByLabel("Name").fill("own fleet");
await form.getByRole("button", { name: "Add the carrier" }).click();
await said(page, "Own fleet is what Simple WMS is told for your own trucks");
await form.getByLabel("Name").fill(CARRIER);
await form.getByLabel("Your account with them").fill("SAMPLE-1");
await form.getByLabel("Usually calls at").fill("15:30");
await form.getByRole("button", { name: "Add the carrier" }).click();
await said(page, `${CARRIER} is added.`);
await yours.getByRole("button", { name: `Change ${CARRIER}` }).click();
form = yours.getByRole("form", { name: `Change ${CARRIER}` });
await form.getByLabel("Name").fill(`${CARRIER} renamed`);
await form.getByRole("button", { name: "Save changes" }).click();
await said(page, `${CARRIER} is now ${CARRIER} renamed.`);
await shot(page, "5-carriers");
await yours.getByRole("button", { name: `Retire ${CARRIER} renamed` }).click();
await said(page, `${CARRIER} renamed is retired.`);

// ---- a setting, refused, changed and put back ----------------------------------------------
await page.keyboard.press("Escape");
await page.getByRole("button", { name: /^Account/ }).click();
await page.getByRole("link", { name: "Settings" }).click();
const eta = page.getByRole("form", { name: "Live location and ETAs" });
await eta.getByLabel("Minutes a stop takes, to start with").fill("0");
await eta.getByRole("button", { name: "Save Live location and ETAs" }).click();
await said(page, "Input should be greater than or equal to 1");
await eta.getByLabel("Minutes a stop takes, to start with").fill("25");
await eta.getByRole("button", { name: "Save Live location and ETAs" }).click();
await said(page, "Saved. The API and the worker use it within 5 seconds.");
const printing = page.getByRole("form", { name: "Printing through Platen" });
await printing.getByLabel("Platen job intake").fill("https://platen.invalid/intake/jobs");
await printing.getByRole("button", { name: "Save Printing through Platen" }).click();
await said(page, "Type the Platen key again with a new address");
await printing.getByLabel("Platen job intake").fill((await call(admin, "GET", "/v1/settings")).body.find((s) => s.name === "platen_url").value);
await shot(page, "6-settings");
const seen = (await call(admin, "GET", "/v1/settings")).body.find((s) => s.name === "eta_minutes_per_stop");
console.log("eta:", seen.value, "on screen:", seen.on_screen, "by", seen.changed_by);
await page.getByRole("button", { name: "Use .env's minutes a stop takes, to start with" }).click();
await said(page, "Minutes a stop takes, to start with is .env's again.");
const history = (await call(admin, "GET", "/v1/settings/history?limit=3")).body;
console.log("history:", history.map((h) => `${h.env} ${h.action}${h.value ? ` ${h.value}` : ""} by ${h.changed_by}`).join("; "));

// ---- the dispatcher sees none of it --------------------------------------------------------------
const desk = await signIn("dee", "dispatch password");
await desk.getByRole("link", { name: /^Fleet/ }).click();
await desk.getByRole("tab", { name: "Drivers" }).waitFor();
console.log("dispatcher: Add a vehicle", await desk.getByRole("button", { name: "Add a vehicle" }).count(),
            "· checklists tab", await desk.getByRole("tab", { name: "Pre-start checklists" }).count());
await desk.getByRole("tab", { name: "Drivers" }).click();
await desk.getByRole("cell", { name: CODE }).waitFor();
console.log("dispatcher: Add a driver", await desk.getByRole("button", { name: "Add a driver" }).count(),
            "· driver controls", await desk.getByRole("complementary").count());
await desk.keyboard.press("Escape");
await desk.getByRole("link", { name: /^Carriers/ }).click();
await desk.getByRole("dialog", { name: "Carriers" }).waitFor();
console.log("dispatcher: Your carriers", await desk.getByRole("region", { name: "Your carriers" }).count());
await desk.goto(`${BASE}/settings`);
await desk.getByText("Settings are for an admin.").waitFor();
console.log("dispatcher: settings", (await call(dee, "GET", "/v1/settings")).status,
            "· a vehicle", (await call(dee, "PATCH", `/v1/vehicles/${REGO}`, { max_kg: "9" })).status);
await shot(desk, "7-dispatcher-settings");
await browser.close();
console.log("done");
```

**Step 2: Run it**

```
cd "$W" && W="$W" BASE=https://localhost:8543 A=http://127.0.0.1:8022 S=$(date +%H%M%S) caffeinate -i node admin.mjs
```
Expected, in order (the numbers yours):
```
said: At most 3 decimal places.
said: CHK145932 is added.
said: CHK145932 is changed.
aside: Van · up to 1,300 kg and 8.5 m³ · serviced every 6 months
said: Check 145932 (chk-145932) is added.
said: Check 145932 has a new PIN.
run: 201 R-0003
asked: Deactivate Check 145932 (chk-145932)? They can't sign in until they are reactivated. Their runs and deliveries stay theirs.
refused: Check 145932 (chk-145932) is on R-0003 (planned for 15 Oct 2026). Give that run another driver, or cancel it, first.
cancel: 200
asked: Deactivate Check 145932 (chk-145932)? …
said: Check 145932 (chk-145932) is deactivated.
said: The van checklist is saved: 7 lines.
van now: Tyres, Lights*, Brakes*, Mirrors, Load restraints, Tail lift 145932*, Damage
said: The van checklist is saved: 6 lines.
said: Own fleet is what Simple WMS is told for your own trucks
said: Check Carrier 145932 (sample) is added.
said: Check Carrier 145932 (sample) is now Check Carrier 145932 (sample) renamed.
asked: Retire Check Carrier 145932 (sample) renamed? It takes no new bookings. What is on its bay stays, to be collected.
said: Check Carrier 145932 (sample) renamed is retired.
said: Input should be greater than or equal to 1
said: Saved. The API and the worker use it within 5 seconds.
said: Type the Platen key again with a new address
eta: 25 on screen: true by leighton
said: Minutes a stop takes, to start with is .env's again.
history: TMS_ETA_MINUTES_PER_STOP reset by leighton; TMS_ETA_MINUTES_PER_STOP set 25 by leighton
dispatcher: Add a vehicle 0 · checklists tab 0
dispatcher: Add a driver 0 · driver controls 0
dispatcher: Your carriers 0
dispatcher: settings 403 · a vehicle 403
done
```
(The van list is the demo data's with the new line sixth; if the demo data's van checklist has another number of lines, "saved: N lines" says that number plus one, then the number again: change the two `said(…saved: … lines.)` lines to match.)

**Step 3: Look at the screenshots**

`$W/shots/`: `1-vehicle-problems.png` (the two problems under their fields, in red ink, the first focused), `2-driver.png`, `3-driver-on-a-run.png` (the refusal in the aside), `4-checklist.png`, `5-carriers.png`, `6-settings.png` (the Platen key's problem under it; "Set here by leighton, Today …" under the minutes), `7-dispatcher-settings.png`. Check by eye that they sit in the Game Mode look of the drawers around them.

**Step 4: By hand, the keyboard**

Signed in as `leighton`, with the keyboard only: in **Fleet** Tab to **Drivers**, Enter; Tab to **Add a driver**, Enter: the focus is in **Code**; Tab through **Name**, **PIN**, **Add the driver**, **Cancel**; Enter on **Cancel** puts the focus back on **Add a driver**. On **Pre-start checklists**, Tab to a line's **Move … down** and press Enter twice: the focus stays on the line as it moves. Escape closes the drawer, as before.

**Step 5: A driver changed on screen signs in on the phone**

On a phone (or Chrome's device mode) at `https://localhost:8543/driver/`, sign in as the driver Step 2 made, with its code typed in capitals: it is refused (deactivated). In **Fleet** → **Drivers**, **Reactivate** them, then sign in with PIN `1357`: it works. Five wrong PINs lock them; **Set the PIN** to `2468` in **Fleet** unlocks them, and `2468` signs in. Deactivate them again afterwards.

---

## What is left for later

- **A users screen** (Decision 9): dispatchers, managers and admins, with their roles and passwords, never leaving no admin.
- **The map tiles on screen** (Decision 10): only a tile URL whose origin Caddy already lets in.
- **Two people saving one checklist at once** (Decision 2): a "seen" list on the `PUT`, as reordering stops has.
- **`vehicle.due` and the other events out**, still deferred as in steps 3 to 6.
