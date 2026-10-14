# Simple TMS — API

Base path `/v1`. JSON in, JSON out. Weights, volumes and dimensions are
decimals, sent and returned as strings with no trailing zeros (`"9.5"`,
`"1200"`), never floats. Times are UTC, ISO 8601 with a `Z`. Interactive docs
are at `/docs`.

## Shared rules

- **Auth:** `Authorization: Bearer <token>`. A desktop user gets a token from
  `POST /v1/auth/login`, a driver from `POST /v1/auth/driver-login`. The
  desktop and the driver app use these endpoints and nothing else: the API is
  the only door. Simple WMS events are the exception: they are signed instead
  (see below). A system (Platen reporting a run sheet) uses an API key,
  `tms_k.…`, made with `tms create-api-key`.
- **Errors:** a missing, expired or unknown token is `401` with `{ "detail" }`.
  A token without the scope is `403` with `{ "detail" }`. Validation problems
  are `422` with `{ "errors": [{ "field", "message" }] }`; a body field is
  named as it is (`max_kg`, `data.packages.0.weight_kg`), a query parameter
  with `query.` in front (`query.limit`). A field the endpoint does not take is
  a `422` too. Something that is not there is `404` with `{ "detail" }`.
  Something well formed that cannot be done as things stand (a run that has
  left, a truck over its limit) is `409` with `{ "detail", "code" }`.
- **Confirmations** (from the driver app: pre-start, start, deliver, fail,
  finish; from the office: finish, and a carrier's manifest collected) carry a
  `message_id`, a UUID the phone or the desktop makes when the confirmation is made. The
  first time, the API does the work and keeps the reply; the same
  `message_id` again from the same sender gets that reply back (status and
  body) and changes nothing, however long after. A refusal is not kept, so the
  same message can be sent again once whatever stopped it is put right. (One
  `409` is kept, because the work behind it was done: a pre-start's
  `vehicle_changed`.) A
  `message_id` someone else has used is `409 message_id_taken`; one the same
  sender used for another confirmation (another action, or the same on
  another run or stop) is `409 message_id_reused`.
- **Never deleted:** drivers, vehicles and carriers are deactivated, runs
  cancelled, stops taken off and consignments taken off a carrier's manifest,
  never deleted. A consignment's history is append-only, and
  so are pre-start checks and what a fleet manager records about a truck: a
  mistake is put right by a newer record.
- **Except positions.** Where a truck was (live location, and the position on
  a proof of delivery) is personal information, not business history: it is
  never put in a consignment's history, and the worker deletes it once it is
  older than `TMS_POSITION_RETENTION_DAYS` (90 by default). See Live location.

## Scopes

| Who | Scopes |
| --- | --- |
| `admin` | `*` |
| `manager` | `fleet:read`, `fleet:write`, `consignments:read`, `runs:read`, `runs:write`, `location:read` |
| `dispatcher` | `fleet:read`, `consignments:read`, `runs:read`, `runs:write`, `location:read` |
| a driver | `fleet:read`, `deliveries:read`, `deliveries:write`, `location:write` (on their own runs only) |
| an API key | what it was made with: some of `printing:write`, `runs:read`, `runs:write`, `consignments:read`, `fleet:read`, `deliveries:read`, `deliveries:write`, `location:read`, `location:write` |

`location:read` is where the trucks on the road are: personal information
about the drivers, so a key has it only when it is named. `location:write`
is sending where a run is; a key has it only when it is named, too.

## Sign in

### POST /v1/auth/login
`{ "username", "password" }` →
```json
{
  "token": "tms_s.…", "expires_in": 900, "refresh_token": "tms_r.…",
  "user": { "id": 1, "username": "dee", "display_name": "Dee", "role": "dispatcher" }
}
```
The token lasts 15 minutes; the refresh token 14 days. A wrong password is
`401` with `{ "detail", "code": "wrong_password", "tries_left" }`. Five wrong
in a row lock the account for 15 minutes (`"code": "locked"`); while it is
locked even the right password gets `"locked"`. Attempts for one account are
weighed one at a time, so guesses sent all at once still lock it after five.
An unknown or deactivated username looks like a wrong password (without
`tries_left`), and takes as long to answer.

### POST /v1/auth/refresh
`{ "refresh_token" }` → the same as login, with a new refresh token good for
another 14 days. The old one is spent, once: the same refresh token sent
twice at once gets one new pair, and the other request a `401`. A spent,
revoked or expired refresh token, or one whose user is deactivated, is `401`.

### POST /v1/auth/logout
`{ "refresh_token" }` → `204`, whether or not the refresh token was still
good. Its refresh token stops working; the access token already handed out
runs out within its 15 minutes.

### GET /v1/auth/me
`{ "kind": "user" | "driver" | "key", "id", "name", "display_name", "role", "scopes" }`.
`name` is the username, the driver's code, or the key's name; a driver's
`role` is `driver`, an API key's `key`.

### POST /v1/auth/driver-login
`{ "code": "drv-01", "pin": "2468" }` →
`{ "token": "tms_d.…", "expires_in": 43200, "driver": { …as /me… } }`. The
token lasts a 12-hour shift; there is no refresh. A wrong PIN is `401` with
`{ "detail": "wrong driver code or PIN", "code": "wrong_pin" }`, and no
`tries_left`. An unknown or deactivated code gets exactly the same answer,
and takes as long, so driver codes cannot be found by trying them. Five wrong
PINs lock the driver (`"code": "locked"`) until a manager unlocks them; as
for desktop users, PINs sent all at once are counted one at a time. A right
PIN clears the count. The code is matched whatever its case.

### When a session ends early
A token stops working at once when its user or driver is deactivated, when a
driver is locked, and when the password or PIN changes: every session signed
in with the old one ends, access tokens and refresh tokens alike. A new driver
name keeps the driver signed in.

Desktop users are created on the command line in step 1:
`tms create-user --username you --role admin|manager|dispatcher` (the
password, at least 12 characters, is asked for, or given with `--password`).

## Vehicles

`fleet:read` to list and read, `fleet:write` to change.

- `POST /v1/vehicles` — `{ "rego": "1AB 2CD", "type": "van" | "rigid" | "ute",
  "max_kg": "1200", "max_m3": "8.5" }` → `201`. The rego is stored upper case
  without spaces (`1AB2CD`): letters, digits and `-`, at most 12. `max_kg` and
  `max_m3` are above zero, with at most 3 decimal places. A rego already on
  file is `422` on `rego`, also when another request took it a moment before.
- `GET /v1/vehicles?active=true|false` — `{ "items", "total" }`, by rego, all
  of them (no paging).
- `GET /v1/vehicles/{rego}` — the rego in the URL is read the same way, so
  `/v1/vehicles/1ab2cd` finds `1AB2CD`.
- `PATCH /v1/vehicles/{rego}` — any of `type`, `max_kg`, `max_m3`,
  `service_every_months`, `service_every_km`. A field left out is kept;
  `null` clears a service interval. The rego does not change.
- `POST /v1/vehicles/{rego}/deactivate`, `/reactivate`

`POST` and `PATCH` also take `service_every_months` (1 to 120) and
`service_every_km` (1 to 1,000,000): how often it is serviced, whichever
comes first. Leave both out and its service is never due. While a truck's
service is overdue, a `PATCH` that clears or lengthens either interval, or
changes its `type`, is `409 vehicle_overdue` ("Record the service first"); so
is a new `type` while its rego has run out. Overdue is cleared by a record
with a note (rule 3), not by easing the schedule. `GET` takes
`date=YYYY-MM-DD`, the day due and overdue are judged for: the caller's
today, as the driver app sends its own date. Left out, it is today in UTC
(in Australia, still yesterday until 10 or 11 am), so send it.

Each vehicle, with where it stands that day:
```json
{ "id": 1, "rego": "TMS041", "type": "rigid", "max_kg": "4500", "max_m3": "22",
  "active": true, "created_at": "…", "service_every_months": 6, "service_every_km": 15000,
  "odometer_km": 61208,
  "rego_due": { "expires_on": "2027-03-03", "days_left": 140, "state": "ok" },
  "service_due": { "last_on": "2026-07-14", "last_km": 46900, "due_on": "2027-01-14",
                   "due_km": 61900, "days_left": 92, "km_left": 692, "state": "due" },
  "grounded": null,
  "last_prestart": { "id": 12, "at": "…", "result": "passed", "by": "kim" },
  "state": "due", "blocked": null }
```
Kilometres are whole numbers. `odometer_km` is the highest reading from a
pre-start or a service since the last time a fleet manager put the reading
right (or that reading). `rego_due` and `service_due` are each `ok`, `due`
(30 days or 1,000 km or less), `overdue` (the day after the expiry or due
date, or past the due kilometres) or `unknown` (nothing recorded to judge
by: it does not stop the truck). The service falls due `service_every_months`
after the last service's date and `service_every_km` after its reading,
whichever comes first; a truck serviced by kilometres alone (or a service
recorded without a reading) is judged by the one it has. `grounded` is the check that grounded it,
`{ "check_id", "at", "failed": ["Brakes"] }`, until a fleet manager marks it
fixed. `state` is the worst of them: `grounded`, `overdue`, `due` or `ok`.
`blocked` is `{ "code", "message" }` when it can't take a run that day (rule
3): `vehicle_grounded` ("TMS041 is grounded: Brakes failed the pre-start. A
fleet manager has to mark it fixed first.") or `vehicle_overdue` ("TMS041's
rego runs out on 3 Oct 2026. Record the new expiry in Fleet first.", or the
service by date or kilometres).

## Fleet care

### Checklists
- `GET /v1/checklists` (`fleet:read`) — each type's pre-start checklist, in
  order: `{ "van": [{ "id": 1, "label": "Tyres", "critical": false }, …],
  "rigid": […], "ute": […] }`. A fresh install has the design's six for each
  type: Tyres, Lights (critical), Brakes (critical), Mirrors, Load
  restraints, Damage.
- `PUT /v1/checklists/{type}` (`fleet:write`) — `{ "items": [{ "label",
  "critical"? }] }`, 1 to 30, each label once (any case): the type's whole
  list, in order. A line with the same label and `critical` as before keeps
  its id; the others are retired, never deleted. Each check keeps its own
  copy of what it asked, so changing a list never changes a check.

### What a fleet manager records
`fleet:write` (managers and admins). Each is a new record with a `note`
(required, at most 500) and answers `201`:
```json
{ "id": 4, "vehicle": "TMS041", "kind": "service", "check_id": null, "done_on": "2026-10-13",
  "expires_on": null, "odometer_km": 61190, "note": "60,000 km service",
  "recorded_by": "fleet.mgr", "recorded_at": "…" }
```
- `POST /v1/vehicles/{rego}/rego` — `{ "expires_on", "note" }`. The newest
  rego record is the expiry.
- `POST /v1/vehicles/{rego}/service` — `{ "done_on", "odometer_km"?, "note" }`.
  The next service falls due from the newest one. A `done_on` more than a
  day after today in UTC is `422` on `done_on`: record a service once it is
  done. (A rego's `expires_on` is in the future by nature, so it isn't
  limited.)
- `POST /v1/vehicles/{rego}/odometer` — `{ "odometer_km", "note" }`: the reading
  put right (a slip of the finger, a new odometer). Readings from before it
  no longer count; it may be lower than them.
- `POST /v1/vehicles/{rego}/fix` — `{ "check_id", "note" }`: what grounded it
  is put right. `check_id` is the check the fleet manager looked at, which
  must be the latest that grounded the truck: `409 stale` if another came in
  meanwhile (look at it, then mark it fixed again), `409 not_grounded` if it
  isn't grounded. It clears that check and every one before it; the checks
  themselves never change.
- `GET /v1/vehicles/{rego}/history?limit=50` (`fleet:read`) — its checks and
  records, newest first: `[{ "kind": "prestart" | "fixed" | "service" | "rego"
  | "odometer", "id", "recorded_at", "by", "note", "result", "failed",
  "odometer_km", "done_on", "expires_on", "check_id" }]` (a check's `failed`
  names every item that failed).

### GET /v1/prestarts/{id}
`fleet:read`. One check as the driver sent it:
```json
{ "id": 12, "run_id": 7, "run_no": "R-0007", "vehicle": "TMS027", "vehicle_type": "ute",
  "driver": { "code": "tom", "name": "Tom" }, "at": "…", "recorded_at": "…",
  "odometer_km": 84312, "result": "grounded",
  "items": [{ "label": "Lights", "critical": true, "passed": false,
              "note": "Left brake light out.", "photo_file": "3f2a…" }],
  "fixed": { "id": 5, "note": "Replaced the globe, tested", "by": "fleet.mgr", "at": "…" } }
```
`result` is `passed`, `failed` (an item that isn't critical failed: the truck
may go) or `grounded`. `fixed` is the record that cleared a check that
grounded the truck, else `null`. The photos are at `GET /v1/pod-files/{id}`.

Who can read what: checks (`GET /v1/prestarts/{id}`) and a truck's history
(`GET /v1/vehicles/{rego}/history`) need `fleet:read`, which every role and
every driver has, so a driver can read any truck's checks, not only their
own. The photos are another matter: `GET /v1/pod-files/{id}` needs
`consignments:read` (not a driver's) or to be the one who sent the photo. So a
driver can fetch the photos they took and no one else's, and a key with
`fleet:read` but not `consignments:read` sees each photo's id in a check but
gets `404` fetching it.

Simple TMS doesn't send `vehicle.due` yet: due dates show on the desktop
only. It comes with the first system that subscribes.

## Drivers

`fleet:read` to list and read, `fleet:write` to change.

- `POST /v1/drivers` — `{ "code": "drv-01", "name": "Alex T.", "pin": "2468" }`
  → `201`. Codes are letters, digits, `.`, `_` and `-`, at most 32; a PIN is
  4 to 8 digits, stored hashed and never returned. A code already on file is
  `422` on `code`, also when another request took it a moment before.
  A code is kept lower case: `KIM-07` is stored, shown and signed in with as
  `kim-07`, and is found in the address however it is typed
  (`/v1/drivers/KIM-07`). A code that differs from one on file only by case
  is `422` on `code`.
- `GET /v1/drivers?active=true|false` — `{ "items", "total" }`, by code, all
  of them.
- `GET /v1/drivers/{code}`
- `PATCH /v1/drivers/{code}` — `name` and/or a new `pin`. A new PIN ends the
  driver's sessions.
- `POST /v1/drivers/{code}/deactivate`, `/reactivate`
- `POST /v1/drivers/{code}/unlock` — clears a PIN lock and the count.

Each driver: `{ "id", "code", "name", "active", "locked", "failed_attempts", "created_at" }`.

## Consignments

`consignments:read`. A driver cannot see them yet.

### GET /v1/consignments
Newest first. Filters: `status` (comma separated), `warehouse`, `q` (part of
the WMS delivery ref, the customer name or the con note, any case; `%` and `_` are plain
text; at most 64 characters), `limit` (0 to 2000, default 200), `offset`
(default 0). → `{ "items", "total" }`, where `total` counts every match, not
just this page. Each:
```json
{
  "id": 1, "wms_ref": "0080012345", "warehouse": "BAL-WH01", "owner": "DEFAULT",
  "customer": "Ballarat Hardware",
  "ship_to": { "name": "Ballarat Hardware", "address": "12 Sturt St", "suburb": "Ballarat",
               "state": "VIC", "postcode": "3350", "country": "AU", "phone": "03 5300 0000" },
  "required_by": "2026-10-11", "status": "packed", "created_at": "2026-10-10T03:12:04.218760Z",
  "weight_kg": "9.5", "volume_m3": "0.096",
  "packages": [{ "package_no": 1, "type": "carton", "weight_kg": "9.5", "length_cm": "60",
                 "width_cm": "40", "height_cm": "40", "sscc": "093123450000000012" }]
}
```
`ship_to` is the delivery's ship-to as Simple WMS gave it when the freight
was packed; `customer` is its `name`. A package's weight, dimensions and SSCC
are `null` when Simple WMS had none. `weight_kg` and `volume_m3` are the
packages added up (volume from length × width × height in cm); a package
without a weight, or without all three sides, adds nothing.

### GET /v1/consignments/{id}
The same, plus its history, oldest first:
```json
"history": [{ "kind": "packed", "at": "2026-10-10T03:12:00Z",
              "detail": { "wms_event_id": "…", "packages": 1 } }]
```
Statuses: `packed` (in the tray), `assigned` (on a planned run), `departed`
(its run has left), `delivered`, `failed` (the driver couldn't deliver
it: it comes back to the depot and waits in the tray to be planned again),
`booked` (on a carrier's manifest, on the carrier bay) and `collected` (the
carrier has it). The history has a row for each: `packed`, `assigned` and
`unassigned` (with the run and who), `departed` (with the run, vehicle,
driver and who), `delivered` and `failed` (see "The driver app"), `booked`
and `unbooked` (with the carrier, the manifest and who), `con_note` (each con
note set, with the one it replaced in `was`, the carrier and who) and
`collected` (with the carrier, the con note, the manifest and who). A
consignment planned again after `failed` gets `assigned` and `departed` rows
again.

Each consignment, in the list and on its own, also says where it is now:
```json
"run": { "id": 7, "run_no": "R-0007", "driver": "Kim", "sequence": 3 },
"carrier": null
```
`run` is the run it is on, or was last on once it is `delivered` or `failed`;
`carrier` is the carrier it is booked with or collected by,
`{ "id", "name", "con_note", "manifest_id", "manifest_no", "collected_at" }`.
Each is `null` otherwise. `q` also matches the con note.

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
  "vehicle": { "rego": "TMS041", "type": "rigid", "max_kg": "4500", "max_m3": "22", "active": true,
               "blocked": null },
  "driver": { "code": "kim", "name": "Kim" },
  "created_by": "dee", "created_at": "2026-10-13T22:01:09.114Z", "departed_at": null,
  "finished_at": null,
  "stops": [{ "sequence": 1, "outcome": null, "outcome_at": null, "reason": null,
      "consignment": {
      "id": 31, "wms_ref": "0080034519", "customer": "Station Deli", "suburb": "Ballan",
      "status": "assigned", "packages": 5, "cartons": 5, "weight_kg": "72", "volume_m3": "0.5" } }],
  "load": { "weight_kg": "72", "volume_m3": "0.5", "max_kg": "4500", "max_m3": "22",
            "unknown_weight": 0, "unknown_volume": 0, "state": "ok", "message": null },
  "run_sheet": null,
  "shipping": { "pending": 0, "done": 0, "failed": 0 },
  "prestart": null,
  "estimate": null
}
```
`status` is `planned`, `departed`, `done` (the driver finished it) or
`cancelled`. Once a run has left, each stop's `outcome` is `delivered`,
`failed` (with the driver's `reason`: `closed`, `refused`, `damaged`,
`wrong_address`) or `null` while it is still to go.

`load` adds up the stops' packages against the truck: `state` is `ok`, `near`
(90 % or more of either limit: "80 kg and 0.4 m³ left. Small drops only.") or
`over` (more than either: "280 kg too heavy. Move a stop to another run
before printing the run sheet."); what is over is rounded up, what is left
rounded down. `unknown_weight` and `unknown_volume` count packages with no
weight, or without all three sides, which add nothing. `run_sheet` is the
latest print of it (see Run sheets), `null` until it is first printed:
`{ "job_id", "status", "printer", "last_error", "created_at", "printed_at" }`.
`shipping` counts its ship calls to Simple WMS by state. `vehicle.blocked`
is why its truck can't take it, on its day (see Vehicles), else `null`: a run
planned for a truck that is then grounded stays as it is and says so.
`prestart` is the run's pre-start as Start counts it, `{ "id", "at",
"result" }`, else `null`: the latest check of its truck for it that didn't
ground it (`passed` or `failed`), made after that truck was last grounded and
last marked fixed. A check that grounded the truck is never the run's
pre-start, so once the truck is marked fixed it is `null` until a fresh check.

`estimate` is `null` unless the run is on the road (`departed`). Then it is
when the run should reach each stop still to go, and the depot:
```json
{ "basis": "average", "minutes_per_stop": 18,
  "stops": [{ "consignment_id": 33, "sequence": 3, "at": "2026-10-14T00:40:00Z" }],
  "back_at_depot": "2026-10-14T00:58:00Z" }
```
These are estimates, and are shown as such. A stop takes the driver's average
so far on this run (from leaving the depot to the latest stop done, delivered
or not: `basis` `average`); until the first stop is done, it takes
`TMS_ETA_MINUTES_PER_STOP` (20 unless set: `basis` `default`). Stops follow
the run's order, one average apart, the first one average after the latest
stop done (or leaving), and the drive back is one stop more. Nothing is due
before now: a driver running late is due now, and the rest move with it. No
routing service and no distances: the average already has the driving in it.

**Rule 3: a grounded or overdue truck does not go on a run.** Making a run
for it (`POST /v1/runs`), giving a run that truck (`PATCH`), putting a stop on
its run (`assign`), printing its run sheet while planned, and departing or
starting it are each `409 vehicle_grounded` or `409 vehicle_overdue`, with
the message from `blocked`. Overdue is judged for the run's own day, so a
truck whose rego runs out on the 14th can go on the 14th but not on the 15th.
Taking stops off, putting them in order, a new driver and cancelling are
still allowed, and so is everything on a run that has already left.

- `GET /v1/runs?date=2026-10-14` — that day's runs, oldest first, as
  `{ "items", "total" }`. Cancelled runs only with `status=cancelled` (or a
  comma list of statuses). `date` is required. With `left_open=true`, the
  runs from before that day still on the road (left and never finished) come
  first, by day: the desktop shows them as "Still open from Tue 13 Oct", and
  the office finishes them once every stop has an outcome.
- `POST /v1/runs` — `{ "date", "vehicle": "TMS041", "driver": "kim" }` →
  `201` with the run. The rego is read as for vehicles (`tms 041` is
  `TMS041`). A truck has one run a day (a cancelled one frees it); a driver
  may have several. A truck that is deactivated, unknown or already on a run
  that day, or a driver that is deactivated or unknown, is a `422` naming
  `vehicle` or `driver`.
- `GET /v1/runs/{id}`
- `PATCH /v1/runs/{id}` — `vehicle` and/or `driver`, read and refused as
  above, while it is planned (`409 run_not_planned` after). Giving a run
  whose truck can't go another truck that can is how it is mended.
- `POST /v1/runs/{id}/cancel` — only an empty planned run: `409 run_not_empty`
  while it has stops, `409 run_not_planned` once it has departed. Cancelling a
  cancelled run is a no-op.

### Planning the stops

Each answers with the run as it now is.

- `POST /v1/runs/{id}/assign` — `{ "consignment_id", "position"? }`. Puts a
  `packed` consignment (or a `failed` one, back from a run) on the run at
  `position` (1 is first; left out, last; past the end, last), or moves one already on this run to that place, or
  takes it off another planned run, or off the carrier bay (history
  `unbooked`), and puts it here. Already in that place:
  nothing changes (with no `position`, one already on this run moves to last). The history gets `assigned` (and `unassigned` from the other run)
  with the run and who. Past the truck's limits is allowed: the run shows
  `over`, and cannot print or leave until a stop moves.
- `POST /v1/runs/{id}/unassign` — `{ "consignment_id" }`. Back to the tray
  (`packed`, history `unassigned`). Not on this run: nothing happens.
- `POST /v1/runs/{id}/reorder` — `{ "consignment_ids": [ … ], "seen": [ … ] }`:
  every stop on the run in the new order, and the stops in the order you saw
  them before the move. If `seen` is not the run's order as it is now
  (someone else added, took off or moved a stop meanwhile), or
  `consignment_ids` is not exactly the run's stops (or an id is twice), it is
  `409 stale` and nothing changes: reload and move it again. So one
  dispatcher's reorder never silently undoes another's.

| `409` code | When |
| --- | --- |
| `run_not_planned` | the run has departed or been cancelled; its stops are fixed |
| `not_plannable` | the consignment has left, been delivered or been collected by a carrier |
| `vehicle_unavailable` | the run's truck has been deactivated |
| `vehicle_grounded`, `vehicle_overdue` | the run's truck can't go (rule 3) |
| `stale` | a reorder from an order that is not the run's as it is now |

An unknown consignment is a `422` on `consignment_id`; an unknown run `404`.

### POST /v1/runs/{id}/depart

The truck has left. Each consignment becomes `departed` (history `departed`
with the run, vehicle, driver and who), and a ship call to Simple WMS is
queued for each, all in one go. Departing a departed run changes nothing and
queues nothing more. Refused with `409`: `run_empty`, `vehicle_unavailable`,
`over_capacity` (the detail is the run number and the load's message:
"R-0007: 280 kg too heavy. Move a stop to another run before printing the run
sheet."), `vehicle_grounded`, `vehicle_overdue`, `run_not_planned` for a
cancelled run. Grounded and overdue are judged for the day it leaves: the
run's own day, or today (UTC) if that is later, so a run planned for an
earlier day can't leave on a rego that has run out since. It doesn't need a
pre-start: the office sends a driver without a phone. The driver's
`POST /v1/runs/{id}/start` does the same.

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
  scratch (no tries, no last error), each with its same `message_id`.

### Run sheets

- `POST /v1/runs/{id}/print` → `202` with the print job. Each call prints
  again. Refused with `409`: `printing_not_set_up` (no `TMS_PLATEN_URL`),
  `run_empty`, `run_cancelled`, `over_capacity`, and for a planned run
  `vehicle_grounded`, `vehicle_overdue`.

```json
{ "job_id": "uuid", "run_id": 7, "manifest_id": null, "template": "run-sheet",
  "printer": "dispatch", "status": "pending", "attempts": 0, "last_error": null,
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

How a print went, from Platen, for a run sheet or a carrier manifest: the body Simple WMS takes,
`{ "status": "accepted" | "printed" | "failed", "message"?, "printer"? }`.
Needs `printing:write` (an API key made for Platen). `printed` sets
`printed_at`; `failed` keeps `message` as `last_error`; a late `accepted` does
not undo `printed` or `failed`. Answers with the print job as it now is. An
unknown `job_id` is `404`, which Platen does not ask again.

## Carriers

Freight going with outside carriers, booked by hand in this version: the
dispatcher books with the carrier as they always have, then types or scans
the con note here. The carrier calls, takes what is on its manifest, and the
dispatcher marks it collected: each consignment is then marked shipped in
Simple WMS with the carrier's name and the con note, as a run's are when it
leaves.

A carrier is changed by a fleet manager (`fleet:write`) and read by the
office (`runs:read`). Each:
```json
{ "id": 2, "name": "Ridgeline Express (sample)", "account": "RX-4410",
  "pickup_time": "15:30", "adapter": "manual", "active": true,
  "created_at": "2026-10-15T00:02:11Z" }
```
`name` is what Simple WMS is told as the carrier (1 to 64 characters, one
carrier per name whatever its case, and never `Own fleet`, which is your own
trucks). `account` is your account with the carrier, printed on the manifest
(at most 40, or `null`). `pickup_time` is when it usually calls, by the
depot's clock (`HH:MM`, or `null`). `adapter` is how it is booked: `manual`
(by hand) is the only one yet; anything else is a `422`. A carrier's own API
comes later as another adapter, with no change to any of this.

- `GET /v1/carriers?active=true` — by name, as `{ "items", "total" }`.
- `POST /v1/carriers` — `{ "name", "account"?, "pickup_time"?, "adapter"? }` → `201`.
- `GET /v1/carriers/{id}`
- `PATCH /v1/carriers/{id}` — only what is given changes; `account` or
  `pickup_time` given as `null` is cleared. A new name is what Simple WMS is
  told from the next collection on.
- `POST /v1/carriers/{id}/deactivate`, `/reactivate` — a deactivated carrier
  takes no new bookings; what is already on its manifest stays, to be collected.

### The carrier bay

A **manifest** is one carrier's pickup: the consignments waiting on the
carrier bay for it on a day, then what it took. There is one open manifest
per carrier per day; once collected, the next booking for that carrier and
day starts a new one (a carrier that calls twice). `runs:read` to look,
`runs:write` to change. Each change is made one at a time with the planning
of runs, so a consignment is on one run or with one carrier, never both.
```json
{
  "id": 3, "manifest_no": "M-0003", "date": "2026-10-15", "status": "open",
  "carrier": { "id": 2, "name": "Ridgeline Express (sample)", "…": "…" },
  "created_by": "dee", "created_at": "…", "collected_at": null, "collected_by": null,
  "lines": [{ "con_note": "RX-77310042", "added_at": "…",
              "consignment": { "id": 31, "wms_ref": "0080034519", "customer": "Hilltop Joinery",
                               "suburb": "Bacchus Marsh", "status": "booked", "packages": 5,
                               "cartons": 5, "weight_kg": "72", "volume_m3": "0.5" } }],
  "totals": { "consignments": 1, "packages": 5, "cartons": 5, "weight_kg": "72" },
  "needs_con_note": 0,
  "print_job": null,
  "shipping": { "pending": 0, "done": 0, "failed": 0 },
  "already_shipped": []
}
```
`status` is `open` or `collected`. `print_job` is its latest print (as a
run's `run_sheet`), `shipping` its ship calls to Simple WMS by state, and
`already_shipped` the consignments Simple WMS had shipped already when this
carrier took them (see below).

- `GET /v1/manifests?date=2026-10-15` — that day's manifests with something
  on them, open or collected, oldest first, as `{ "items", "total" }`. With
  `left_open=true`, first the open ones from earlier days still waiting for
  their carrier. With `collected_since` (a time with its offset, such as the
  start of the dispatcher's day: `2026-10-15T00:00:00+11:00`), also those
  from earlier days collected since then, so a carrier that came a day late
  counts in Carriers' **Collected today**.
- `GET /v1/manifests/{id}`
- `POST /v1/carriers/{id}/book` — `{ "consignment_id", "date", "con_note"? }`.
  Puts a consignment on the carrier's manifest for that day (made if there is
  none): from the tray (`packed` or `failed`), off a planned run (history
  `unassigned`), or off another manifest (history `unbooked`). It becomes
  `booked` (history `booked`). Moved to another day with the same carrier it
  keeps its con note; to another carrier it doesn't, as a con note is one
  carrier's. Already there: nothing changes, but for a `con_note` given.
  Answers with the manifest it is on.
- `POST /v1/manifests/{id}/con-note` — `{ "consignment_id", "con_note" }`:
  typed or scanned, or put right, until it is collected. Each new con note is
  a `con_note` row in the history naming the one it replaced; the same again
  changes nothing. Not on this manifest: `404`.
- `POST /v1/manifests/{id}/unbook` — `{ "consignment_id" }`: back to the
  tray (`packed`, history `unbooked`). Not on it: nothing happens.

A **con note** is read as the carrier prints it: spaces around it are taken
off and letters made capitals (` rx-77310042 ` is `RX-77310042`); it must
then be 4 to 40 letters, digits and dashes, starting with a letter or digit,
or it is a `422` on `con_note` (a space inside is refused, not guessed at). A
con note is one consignment's with a carrier, for good: another consignment
with the same carrier and con note is a `422` naming the first, even once the
first has been taken off the bay or collected. The first may have it again
(back on the bay, or moved to another day), and a con note put right is free
for another. Another carrier's numbers are its own.

| `409` code | When |
| --- | --- |
| `not_plannable` | the consignment has left on a run, been delivered or been collected |
| `carrier_inactive` | the carrier is deactivated: nothing new can be booked with it |
| `manifest_collected` | the carrier has collected it; what is on it can't change |

An unknown consignment is a `422` on `consignment_id`; an unknown carrier or
manifest `404`.

### POST /v1/manifests/{id}/collect

`{ "message_id", "consignment_ids": [ … ] }`: the carrier has called and
taken it, with what is on the manifest as you saw it. Each consignment
becomes `collected` (history `collected` with the carrier, the con note, the
manifest and who), and a ship call to Simple WMS is queued for each, all in
one go; the manifest is `collected` with when and who. Once per `message_id`
(see Shared rules); a manifest already collected with just those on it is
answered as it is, and nothing more is queued.

| `409` code | When |
| --- | --- |
| `stale` | `consignment_ids` isn't exactly what is on it now (someone booked or took one off meanwhile): reload and check |
| `con_note_missing` | something on it has no con note: the detail names the first |
| `manifest_empty` | nothing is on it |
| `manifest_collected` | it was collected with something else on it |

The worker then makes, for each consignment, the same call as for a run
(see `POST /v1/runs/{id}/depart`), with the carrier and its con note:
```
POST {TMS_WMS_URL}/v1/deliveries/{wms_ref}/ship
{ "message_id": "<its own uuid>", "owner": "DEFAULT",
  "carrier": "Ridgeline Express (sample)", "tracking_no": "RX-77310042", "shipped_by": "dee" }
```
with the same retries, so Simple WMS ships each delivery once and fires
`delivery.shipped` with that carrier and tracking number.

A consignment that came back from its run (`failed`) and then went with a
carrier was already shipped in Simple WMS when the run left. Simple WMS
answers its ship call `409 already_shipped` and keeps `Own fleet` and the run
number: Simple TMS allows this, counts the call `done`, and lists it in the
manifest's `already_shipped` as
`{ "consignment_id", "wms_ref", "customer", "carrier": "Own fleet", "tracking_no": "R-0007" }`
(what Simple TMS first told Simple WMS for it; `null` if it never did).

- `POST /v1/manifests/{id}/retry-ship` — calls that gave up start again from
  scratch, each with its same `message_id`.

### Manifests

- `POST /v1/manifests/{id}/print` → `202` with the print job, as a run sheet's
  (`run_id` `null`, `manifest_id` set). Each call prints again: before it is
  collected, what is on the bay now; after, what went. Refused with `409`:
  `printing_not_set_up`, `manifest_empty`, and `con_note_missing` (the
  carrier's driver signs for each consignment by its con note).

The worker sends it to Platen as a run sheet goes, to the printer
`TMS_MANIFEST_PRINTER` (or `TMS_RUN_SHEET_PRINTER` when that is empty):
```json
{ "job_id": "uuid", "template": "carrier-manifest", "version": "v1", "printer": "dispatch",
  "copies": 1, "reference": { "type": "manifest", "ref": "M-0003" },
  "data": {
    "manifest_no": "M-0003", "date": "2026-10-15", "carrier": "Ridgeline Express (sample)",
    "account": "RX-4410", "pickup_time": "15:30", "consignment_count": 2,
    "package_count": 7, "total_kg": "126",
    "lines": [{ "line": 1, "con_note": "RX-77310042", "wms_ref": "0080034519",
                "customer": "Hilltop Joinery", "address": "4 Mill Rd", "suburb": "Bacchus Marsh",
                "state": "VIC", "postcode": "3340", "packages": 5, "weight_kg": "72" }]
  } }
```
Every value is text or a whole number, never `null`. In Platen,
`carrier-manifest` is a page template: one row per entry in `lines`, with the
rest beside it.

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
   "vehicle": { "rego": "TMS041", "type": "rigid", "odometer_km": 61208, "blocked": null },
   "stops": 12, "packages": 46, "delivered": 0, "failed": 0,
   "prestart": null,
   "checklist": [{ "id": 7, "label": "Tyres", "critical": false }, …] }]
```
`vehicle.odometer_km` is the truck's last reading (a new one may not be
lower), `vehicle.blocked` why it can't take the run (see Vehicles),
`prestart` the run's pre-start as Start counts it (see `GET /v1/runs/{id}`:
never the check that grounded the truck, nor one from before it was fixed),
and `checklist`
the truck's checklist as it is now.

### POST /v1/runs/{id}/prestart
The pre-start check of the run's truck, before starting:
```json
{ "message_id": "…", "at": "2026-10-14T06:40:00+11:00", "vehicle": "TMS041", "odometer_km": 61208,
  "items": [{ "item_id": 7, "result": "pass" },
            { "item_id": 11, "result": "fail", "note": "Rear strap frayed",
              "photo_file": "…" }] }
```
`vehicle` is the rego of the truck the driver inspected: the one the phone
showed them (`422` on `vehicle` if there is no such truck). The check, its
odometer reading and any grounding are that truck's. Each item of that
truck's checklist answered once (`409 checklist_changed` if
the list has changed since: do the check again). A failed item needs a
`note` and a `photo_file` (a photo sent first with `PUT /v1/pod-files`, by
the same sender, not already a stop's or another item's); a passed one takes
neither (`422`). A photo's problem is a `422` on the item it is on, as sent:
`items.4.photo_file`. `odometer_km` is whole kilometres and may not be lower than
the truck's last reading (`422` on `odometer_km`). →
```json
{ "message_id": "…", "check_id": 12, "run_id": 7, "run_no": "R-0007", "vehicle": "TMS041",
  "result": "failed", "failed": ["Load restraints"], "grounded": false,
  "odometer_km": 61208, "at": "…" }
```
A failed critical item grounds the truck there and then (`"result":
"grounded"`); any other failed item is kept and the truck may go
(`"failed"`). `grounded` says whether the truck is grounded now, by this
check or an earlier one. Taken on a planned run or one on the road (a check
kept on the phone in a black spot still counts); `409 run_done`,
`run_cancelled` after. A planned run more than a day ahead of today in UTC
is `409 too_early` ("R-0009 is for 16 Oct 2026. Do the pre-start on the
day."); the day's grace is because an Australian morning is still yesterday
in UTC. Kept as sent, for good, once per `message_id`. Another
check may follow (each is kept); a passed one never clears a grounding: only
a fleet manager does.

If the office has given the run another truck since the phone showed the
driver theirs, the check is still kept against the truck inspected (a failed
critical item grounds the truck that failed), and the answer is
`409 vehicle_changed`, naming the run's truck now, with the check as it was
kept:
```json
{ "code": "vehicle_changed", "vehicle": "TMS052",
  "detail": "R-0007 has been given TMS052 since this check began. Your check of TMS041 is with the office. Do a fresh check of TMS052 before starting.",
  "check": { "message_id": "…", "check_id": 13, "vehicle": "TMS041", "result": "grounded", "…": "…" } }
```
Unlike other refusals this answer is kept with the `message_id`: the same
message again gets it again and records nothing more. The run's truck has had
no check, so Start is `409 prestart_needed` until a fresh check of it (a new
`message_id`) is in.

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
as it is. A planned run starts only once its truck's pre-start for it is in:
`409 prestart_needed` ("Do the pre-start check of TMS041 before starting
R-0007.") until then. Only a check that didn't ground the truck counts, made
after the truck was last grounded and last marked fixed: after **Mark fixed**
the driver checks the truck again. A grounded or overdue truck is refused
for that (`vehicle_grounded`, `vehicle_overdue`) before any check is asked
for. `409`: `run_done`, `run_cancelled`, `prestart_needed`, and depart's `run_empty`, `vehicle_unavailable`, `vehicle_grounded`,
`vehicle_overdue`, `over_capacity`.

### PUT /v1/pod-files/{file_id}?kind=signature|photo
A signature or a photo, sent before the confirmation that names it (a
delivery, a failed delivery, or a pre-start's failed item).
`file_id` is a UUID the phone chose when the picture was made; the body is
the picture (`Content-Type: image/png` or `image/jpeg`). What counts is its
first bytes: a signature is a PNG of at most 1 MB, a photo a JPEG or PNG of
at most 5 MB (`415 unsupported_type`, `413 too_large`). A `kind` other
than `signature` or `photo` is `422`, before any of the body is read. → `201`:
```json
{ "id": "3f2a…", "kind": "signature", "content_type": "image/png", "size_bytes": 18886,
  "sha256": "…", "created_at": "2026-10-14T00:52:09.931Z" }
```
The same bytes again from the same sender: `200`, the same body. Other bytes,
another kind or another sender under that id: `409 file_id_taken`. Kept on
disk under `TMS_POD_DIR` (`/data/pod`, the `pod_files` volume in Compose).

### GET /v1/pod-files/{file_id}
The picture, for anyone with `consignments:read` and for whoever sent it;
anyone else gets `404`. Sent with `Cache-Control: private, no-store`,
`Content-Security-Policy: default-src 'none'; sandbox` and
`Content-Disposition: inline; filename="<id>.png"` (or `.jpg`). A
file on record that is missing from disk is `404` too, and logged.

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
| `message_id_reused` | that `message_id` was used for another confirmation |
| `file_id_taken` | that file id is already another picture |
| `prestart_needed` | start before the truck's pre-start for the run is in |
| `checklist_changed` | a pre-start that doesn't answer the checklist as it is now |
| `too_early` | a pre-start on a planned run more than a day ahead (UTC) |
| `vehicle_changed` | a pre-start of a truck the run no longer has: kept against that truck, and the run's truck needs its own |

A consignment that is not on the run is `404`.

Simple TMS sends no events of its own yet (`consignment.delivered`,
`consignment.failed`, `run.completed` in the design): they come with the
first system that subscribes.

## Live location

While a run is on the road, from **Start** until it is finished, the driver's
phone sends where it is about every 30 seconds, and the office sees each
truck's latest position and when it was last heard from. Never before Start,
never after the run is finished, and the phone says on screen that it is
sharing. Positions are kept `TMS_POSITION_RETENTION_DAYS` (90 unless set),
then the worker deletes them, and clears the position on each proof of
delivery older than that too; a delivery itself is kept for good.

### POST /v1/runs/{id}/position
`location:write`: the run's driver (another driver's run is `404`), or a
system with a key made with `location:write` named (a tracker in the truck,
say), which may post for any run on the road. `deliveries:write` alone is not
enough: a key that records deliveries is `403` here.
```json
{ "at": "2026-10-14T00:12:30Z", "latitude": "-37.562210", "longitude": "143.850300",
  "accuracy_m": "12" }
```
→ `204`. Decimal strings, as for a delivery's position; more than six places
is rounded. `at` is when the phone took it (with its time zone), taken as
now if it is later than now.
Not a confirmation: no `message_id`, and nothing is queued on the phone. A
lost one is simply followed by the next; with no signal, the phone sends only
its latest once it is back.

| Answer | When |
| --- | --- |
| `403` | the caller hasn't `location:write` (a dispatcher, or a key made without it) |
| `404` | no such run, or another driver's |
| `409 run_not_started` | the run hasn't left: nothing is shared before Start |
| `409 run_done`, `409 run_cancelled` | the run is finished or was cancelled: the phone stops |
| `409 run_stale` | the run is still on the road from an earlier day (its `date` is before the server's day, in UTC: in Australia, from 10 or 11 am the day after) and was left open: the phone stops; finish it |
| `422` on `latitude` / `longitude` | missing, or outside -90 to 90 / -180 to 180 |
| `422` on `accuracy_m` | missing, below 0, or rougher than 1,000 m |
| `422` on `at` | missing, without a time zone, more than 10 minutes ago (only the newest is ever sent), or more than a minute before the run left |
| `422` on any other field | not one of the four: nothing else is taken |
| `429 too_often` | another position for the run arrived less than 10 seconds ago |

The 10 seconds hold however many are sent at once: positions for a run are
taken one at a time (a lock per run), so two sent together can't both be
kept. A `422` names the field in `errors`, as everywhere.

### GET /v1/positions?date=2026-10-14
`location:read`. The latest position of each of that day's runs on the road,
by run, as `{ "items", "total" }`:
```json
{ "run_id": 7, "run_no": "R-0007", "vehicle": { "rego": "TMS041", "type": "rigid" },
  "driver": { "code": "kim", "name": "Kim" },
  "latitude": "-37.56221", "longitude": "143.8503", "accuracy_m": "12",
  "at": "2026-10-14T00:12:30Z", "received_at": "2026-10-14T00:12:31.204Z" }
```
`received_at` is when it was last heard from. A run that hasn't left, or is
finished, has none here, whatever was kept from it. There is no endpoint for
a truck's earlier positions: the office sees where each truck is now.

### GET /v1/map
`location:read`. Where the desktop's map tiles come from:
`{ "tile_url": "https://…/{z}/{x}/{y}.png?key=…", "attribution": "© …" }`, from
`TMS_MAP_TILE_URL` and `TMS_MAP_ATTRIBUTION`, or both `null` when no tiles are
set up (the desktop then lists each truck's position without a map). The
tile address is a provider's (with its key, which the browser sees: restrict
it to your domain at the provider) or a tile server of your own, an `https`
address or a path on this server; OpenStreetMap's own tile servers are
refused at start-up, as their policy asks, and so are `{s}` subdomains and a
`TMS_MAP_TILE_ORIGIN` that isn't the tile address's own origin. `attribution`
is HTML, as the map shows it.

## What just happened

### GET /v1/feed?limit=20
`runs:read`. The latest of what the office hears, newest first (`limit` 1 to
100, 20 unless given), read from the records already kept:
```json
{ "items": [{ "kind": "delivered", "at": "2026-10-14T00:31:07Z",
  "run_id": 7, "run_no": "R-0007", "rego": "TMS041", "driver": "Kim",
  "sequence": 3, "customer": "Main Street Chemist", "wms_ref": "0080034521",
  "reason": null, "result": null, "failed": [], "by": null, "note": null,
  "carrier": null, "con_note": null, "manifest_no": null, "count": null }] }
```

| `kind` | What | Fields with something in them |
| --- | --- | --- |
| `packed` | a consignment came from Simple WMS | `customer`, `wms_ref` |
| `delivered`, `failed` | a stop delivered, or not (with the `reason`) | the run, `rego`, `driver`, `sequence`, `customer`, `wms_ref` |
| `left`, `finished` | a run left the depot, or was finished | the run, `rego`, `driver` |
| `con_note` | a con note typed, scanned or put right | `carrier`, `con_note`, `customer`, `wms_ref`, `by` |
| `collected` | a carrier collected its manifest | `carrier`, `manifest_no`, `count` (how many on it), `by` |
| `prestart` | a pre-start check: `result` `passed`, `failed` or `grounded` | the run, `rego`, `driver` (or `by`), `failed` |
| `fixed` | a fleet manager marked a grounded truck fixed | `rego`, `by`, `note` |

`prestart` and `fixed` are the fleet's records: they are left out unless the
caller also has `fleet:read`. Nothing is written for the feed, and no position is in it.

## From Simple WMS

### POST /v1/wms/events
Simple TMS is an ordinary Simple WMS subscriber. Register it in Simple WMS:
```
POST /v1/subscribers   (on Simple WMS, with an integration:admin key)
{ "name": "simple-tms", "url": "https://<tms>/v1/wms/events",
  "secret": "<the same value as TMS_WMS_WEBHOOK_SECRET>",
  "event_types": ["delivery.*"], "warehouses": ["*"], "owner": "*" }
```
and give Simple TMS a Simple WMS key with `tasks:read` and `tasks:write` in
`TMS_WMS_API_KEY` (`tasks:read` to read a packed delivery, `tasks:write` to say
it shipped: see `POST /v1/runs/{id}/depart` and `POST /v1/manifests/{id}/collect`) and the WMS address in
`TMS_WMS_URL`.

The body is Simple WMS's event envelope, exactly as sent:
`{ "event_id", "event_type", "occurred_at", "warehouse", "owner", "external_ref", "data" }`.
`X-WMS-Signature: sha256=<hex HMAC-SHA256 of the raw body, keyed with the
secret>` must match, or the answer is `401` and nothing happens. No bearer
token: the signature is the authentication. The event id is taken from the
signed body, not from the `X-WMS-Event-Id` header. Numbers in the body are
read as decimals, never floats.

Every `200` answer is `{ "status", "event_id", "consignment_id" }`;
`consignment_id` is `null` unless the answer names one.

| Answer | When |
| --- | --- |
| `200 { "status": "created", … }` | `delivery.packed`: a consignment with its packages and a `packed` history row; `consignment_id` is the new one |
| `200 { "status": "duplicate", … }` | this `event_id` was seen before, or arrived twice at once and the other copy won; nothing happens |
| `200 { "status": "ignored", … }` | an event Simple TMS does not act on (logged), or a `delivery.packed` for a delivery that already has a consignment (logged; `consignment_id` is that one). The event id is remembered, so a repeat is a `duplicate` |
| `401 { "detail" }` | missing or wrong signature |
| `422 { "errors" }` | signed, but not JSON, not an event envelope, a `delivery.packed` whose packages do not read, or one with no `delivery_ref` or `external_ref`; or Simple WMS answered with a delivery that does not read (fields named `delivery.…`). Nothing is kept |
| `503 { "detail", "code": "wms_unavailable" }` | the delivery could not be read from Simple WMS (unreachable, no answer within 5 s, or not a `200` with a delivery); nothing is kept, not even the event id, so Simple WMS's retry (1 min, 5, 30, 2 h) tries again. A `401`, `403` or `404` from Simple WMS is also logged as an error naming the likely cause (the key, its `tasks:read` scope, its warehouses or owner), since a retry will not mend it |
| `503 { "detail", "code": "not_configured" }` | `TMS_WMS_WEBHOOK_SECRET` is not set, or is shorter than 16 characters once spaces around it are taken off (Simple WMS takes 16 to 128); Simple WMS keeps the event and retries |

On `delivery.packed` the packages (number, type, weight, dimensions, SSCC)
come from the event; the customer, ship-to address and required-by date come
from `GET /v1/deliveries/{ref}?owner=…` on Simple WMS, because the event does
not carry them. The ref is `data.delivery_ref`, or the envelope's
`external_ref` without it; the owner is `DEFAULT` when the envelope has none.

Both are read strictly, because a retry would not read them any better. A
package's `package_no` is 1 or more; its weight and dimensions are 0 or more,
with at most 12 digits before the point and 6 after: a 7th decimal place is
refused, not rounded. The delivery needs `ship_to` as an object (its `name`,
if any, as text; the customer is that name cut to 200 characters),
`required_by` as a date or `null`, and `warehouse` of at most 32 characters.
Anything else is a `422`, nothing is kept, and Simple WMS shows the event as
failed, with the reason, once its retries run out.
A delivery has at most one consignment per owner, however many events arrive
for it, and at once. Transfers are not consignments yet.

## API keys

For a system rather than a person; today, Platen saying how a run sheet
went. Made on the command line and shown once (only a hash is kept):
```
docker compose exec api tms create-api-key --name platen --scopes printing:write
docker compose exec api tms revoke-api-key --name platen
```
A key may be given some of `printing:write`, `runs:read`, `runs:write`,
`consignments:read`, `fleet:read`, `deliveries:read`, `deliveries:write`,
`location:read`, `location:write`. `GET /v1/auth/me` with a key answers
`"kind": "key"`, its name and scopes. A revoked key is a `401` at once; it is
kept, not deleted.

## System

- `GET /v1/health` → `{ "status": "ok" }` once the database answers. No token.
