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
  (see below).
- **Errors:** a missing, expired or unknown token is `401` with `{ "detail" }`.
  A token without the scope is `403` with `{ "detail" }`. Validation problems
  are `422` with `{ "errors": [{ "field", "message" }] }`; a body field is
  named as it is (`max_kg`, `data.packages.0.weight_kg`), a query parameter
  with `query.` in front (`query.limit`). A field the endpoint does not take is
  a `422` too. Something that is not there is `404` with `{ "detail" }`.
- **Never deleted:** drivers and vehicles are deactivated, not deleted. A
  consignment's history is append-only.

## Scopes

| Who | Scopes |
| --- | --- |
| `admin` | `*` |
| `manager` | `fleet:read`, `fleet:write`, `consignments:read` |
| `dispatcher` | `fleet:read`, `consignments:read` |
| a driver | `fleet:read` (to pick a truck) |

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
`{ "kind": "user" | "driver", "id", "name", "display_name", "role", "scopes" }`.
`name` is the username, or the driver's code; a driver's `role` is `driver`.

### POST /v1/auth/driver-login
`{ "code": "drv-01", "pin": "2468" }` →
`{ "token": "tms_d.…", "expires_in": 43200, "driver": { …as /me… } }`. The
token lasts a 12-hour shift; there is no refresh. A wrong PIN is `401` with
`{ "detail": "wrong driver code or PIN", "code": "wrong_pin" }`, and no
`tries_left`. An unknown or deactivated code gets exactly the same answer,
and takes as long, so driver codes cannot be found by trying them. Five wrong
PINs lock the driver (`"code": "locked"`) until a manager unlocks them; as
for desktop users, PINs sent all at once are counted one at a time. A right
PIN clears the count.

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
  "packages": [{ "package_no": 1, "type": "carton", "weight_kg": "9.5", "length_cm": "60",
                 "width_cm": "40", "height_cm": "40", "sscc": "093123450000000012" }]
}
```
`ship_to` is the delivery's ship-to as Simple WMS gave it when the freight
was packed; `customer` is its `name`. A package's weight, dimensions and SSCC
are `null` when Simple WMS had none.

### GET /v1/consignments/{id}
The same, plus its history, oldest first:
```json
"history": [{ "kind": "packed", "at": "2026-10-10T03:12:00Z",
              "detail": { "wms_event_id": "…", "packages": 1 } }]
```
Statuses in step 1: `packed`. Runs bring `assigned`, `departed`, `delivered`
and `failed`.

## From Simple WMS

### POST /v1/wms/events
Simple TMS is an ordinary Simple WMS subscriber. Register it in Simple WMS:
```
POST /v1/subscribers   (on Simple WMS, with an integration:admin key)
{ "name": "simple-tms", "url": "https://<tms>/v1/wms/events",
  "secret": "<the same value as TMS_WMS_WEBHOOK_SECRET>",
  "event_types": ["delivery.*"], "warehouses": ["*"], "owner": "*" }
```
and give Simple TMS a Simple WMS key with `tasks:read` in `TMS_WMS_API_KEY`
and the WMS address in `TMS_WMS_URL`.

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
| `503 { "detail", "code": "wms_unavailable" }` | the delivery could not be read from Simple WMS (unreachable, no answer within 5 s, or not a `200` with a delivery); nothing is kept, not even the event id, so Simple WMS's retry (1 min, 5, 30, 2 h) tries again |
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

## System

- `GET /v1/health` → `{ "status": "ok" }` once the database answers. No token.
