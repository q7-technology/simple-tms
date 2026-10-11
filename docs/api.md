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
- **Confirmations from the driver app** (start, deliver, fail, finish) carry a
  `message_id`, a UUID the phone makes when the confirmation is made. The
  first time, the API does the work and keeps the reply; the same
  `message_id` again from the same sender gets that reply back (status and
  body) and changes nothing, however long after. A refusal is not kept, so the
  same message can be sent again once whatever stopped it is put right. A
  `message_id` someone else has used is `409 message_id_taken`.
- **Never deleted:** drivers and vehicles are deactivated, runs cancelled and
  stops taken off, never deleted. A consignment's history is append-only.

## Scopes

| Who | Scopes |
| --- | --- |
| `admin` | `*` |
| `manager` | `fleet:read`, `fleet:write`, `consignments:read`, `runs:read`, `runs:write` |
| `dispatcher` | `fleet:read`, `consignments:read`, `runs:read`, `runs:write` |
| a driver | `fleet:read`, `deliveries:read`, `deliveries:write` (on their own runs only) |
| an API key | what it was made with: some of `printing:write`, `runs:read`, `runs:write`, `consignments:read`, `fleet:read`, `deliveries:read`, `deliveries:write` |

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
- `PATCH /v1/vehicles/{rego}` — any of `type`, `max_kg`, `max_m3`. The rego
  does not change.
- `POST /v1/vehicles/{rego}/deactivate`, `/reactivate`

Each vehicle: `{ "id", "rego", "type", "max_kg", "max_m3", "active", "created_at" }`.

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
the WMS delivery ref or the customer name, any case; `%` and `_` are plain
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
(its run has left), `delivered`, and `failed` (the driver couldn't deliver
it: it comes back to the depot and waits in the tray to be planned again).
The history has a row for each: `packed`, `assigned` and `unassigned` (with
the run and who), `departed` (with the run, vehicle, driver and who),
`delivered` and `failed` (see "The driver app"). A consignment planned again
after `failed` gets `assigned` and `departed` rows again.

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
  "finished_at": null,
  "stops": [{ "sequence": 1, "outcome": null, "outcome_at": null, "reason": null,
      "consignment": {
      "id": 31, "wms_ref": "0080034519", "customer": "Station Deli", "suburb": "Ballan",
      "status": "assigned", "packages": 5, "cartons": 5, "weight_kg": "72", "volume_m3": "0.5" } }],
  "load": { "weight_kg": "72", "volume_m3": "0.5", "max_kg": "4500", "max_m3": "22",
            "unknown_weight": 0, "unknown_volume": 0, "state": "ok", "message": null },
  "run_sheet": null,
  "shipping": { "pending": 0, "done": 0, "failed": 0 }
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
`shipping` counts its ship calls to Simple WMS by state.

- `GET /v1/runs?date=2026-10-14` — that day's runs, oldest first, as
  `{ "items", "total" }`. Cancelled runs only with `status=cancelled` (or a
  comma list of statuses). `date` is required.
- `POST /v1/runs` — `{ "date", "vehicle": "TMS041", "driver": "kim" }` →
  `201` with the run. The rego is read as for vehicles (`tms 041` is
  `TMS041`). A truck has one run a day (a cancelled one frees it); a driver
  may have several. A truck that is deactivated, unknown or already on a run
  that day, or a driver that is deactivated or unknown, is a `422` naming
  `vehicle` or `driver`.
- `GET /v1/runs/{id}`
- `PATCH /v1/runs/{id}` — `vehicle` and/or `driver`, read and refused as
  above, while it is planned (`409 run_not_planned` after).
- `POST /v1/runs/{id}/cancel` — only an empty planned run: `409 run_not_empty`
  while it has stops, `409 run_not_planned` once it has departed. Cancelling a
  cancelled run is a no-op.

### Planning the stops

Each answers with the run as it now is.

- `POST /v1/runs/{id}/assign` — `{ "consignment_id", "position"? }`. Puts a
  `packed` consignment (or a `failed` one, back from a run) on the run at
  `position` (1 is first; left out, last; past the end, last), or moves one already on this run to that place, or
  takes it off another planned run and puts it here. Already in that place:
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
| `not_plannable` | the consignment has left or been delivered |
| `vehicle_unavailable` | the run's truck has been deactivated |
| `stale` | a reorder from an order that is not the run's as it is now |

An unknown consignment is a `422` on `consignment_id`; an unknown run `404`.

### POST /v1/runs/{id}/depart

The truck has left. Each consignment becomes `departed` (history `departed`
with the run, vehicle, driver and who), and a ship call to Simple WMS is
queued for each, all in one go. Departing a departed run changes nothing and
queues nothing more. Refused with `409`: `run_empty`, `vehicle_unavailable`,
`over_capacity` (the detail is the run number and the load's message:
"R-0007: 280 kg too heavy. Move a stop to another run before printing the run
sheet."), `run_not_planned` for a cancelled run. The driver's
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
not undo `printed` or `failed`. Answers with the print job as it now is. An
unknown `job_id` is `404`, which Platen does not ask again.

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
it shipped: see `POST /v1/runs/{id}/depart`) and the WMS address in
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
`consignments:read`, `fleet:read`, `deliveries:read`, `deliveries:write`. `GET /v1/auth/me` with a key answers
`"kind": "key"`, its name and scopes. A revoked key is a `401` at once; it is
kept, not deleted.

## System

- `GET /v1/health` → `{ "status": "ok" }` once the database answers. No token.
