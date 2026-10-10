# Simple TMS — design

A small, open-source transport management system for businesses that deliver
with their own trucks and also send freight with outside carriers. It sits
beside Simple WMS and Platen and works without either, the way they work
without each other.

## Decisions

| Question | Decided |
| --- | --- |
| Own trucks or carriers | Both: own fleet first, carriers on the same consignment records |
| Planning runs | A dispatcher drags consignments onto runs and orders the stops; no route optimisation |
| Fleet features in version one | Pre-start checks, rego and service due, live truck location during a run |
| Left out of version one | Driver hours and fatigue (NHVR work diaries), fuel cards, route optimisation, carrier APIs |
| Carriers in version one | Booked by hand: choose the carrier, type or scan the con note, print a manifest |
| Shape | A separate app, its own repo, the same stack and rules as Simple WMS |

## Stack

Python 3.12 + FastAPI, PostgreSQL 16, a Postgres-table queue drained by a
worker (no Redis), Vite + React + TypeScript + Tailwind for a desktop app and
a driver PWA, Docker Compose behind Caddy. The same choices as Simple WMS, so
one person can look after all three.

## How it connects

**From Simple WMS.** Simple TMS is an ordinary Simple WMS subscriber.
`delivery.packed` (and the transfer events) create a **consignment**: one
delivery to one place, with its packages, weights and dimensions, keeping the
WMS reference. Events arrive HMAC-signed; the TMS checks the signature and
ignores an event id it has already seen.

**Back to Simple WMS.** When a run departs, or a carrier collects, the TMS
calls `POST /v1/deliveries/{ref}/ship` (or the transfer equivalent) with the
carrier — "own fleet, run R-…" or the carrier's name — and the tracking or run
number. Each call carries a `message_id`, so the WMS's duplicate handling
makes a resend harmless. If the WMS is down the call waits in the TMS's queue
and is retried after 1 min, 5, 30 and 2 h. The WMS then fires
`delivery.shipped` to the ERP as it does today: nothing in the WMS changes.

**Through Platen.** Run sheets, consignment notes, delivery dockets and
carrier manifests are a template name and JSON, sent the way Simple WMS sends
its print jobs.

*Platen change needed:* today Platen reports printed or failed to the single
`WMS_URL` in its settings. With a second sender, the report has to go back to
whoever sent the job: either the job carries its callback address, or each
Platen key carries one. To be designed with the Platen change; both keep the
existing Simple WMS behaviour.

**Its own events.** `consignment.delivered` (with proof of delivery),
`consignment.failed` (with the reason), `run.completed` and `vehicle.due`, in
the same signed envelope and queue as Simple WMS's events.

## Look: Game Mode, like Simple WMS and Platen

The same idea as Simple WMS's Game Mode and Platen's live map: the main screen
is a small hand-drawn world, and everything else opens over it.

- **The desktop is a map.** The depot, a few roads, and each customer stop as
  a small building. Each run's truck drives its route as stops are delivered;
  carrier consignments wait on a loading bay at the depot until collected.
  Every other screen (consignments, carriers, fleet, settings) opens as a
  drawer over the map, as in Simple WMS. Drawn in SVG, like both siblings: no
  game engine, no 3D library.
- **The driver app is like the scanner.** Big buttons, one thing at a time, a
  "Ding!" when a stop is delivered, and the day's goal (stops left) at the top.
- **Game touches are per viewer.** Sounds, day and night following the depot's
  clock, trucks moving, the daily goal: preferences kept in the browser, never
  in the API, and all switched off in one click. The numbers underneath are the
  same either way.
- **Its own colours, the same bones.** Simple WMS's layout, Manrope font,
  white cards and light page, so the three feel like one family; a colour of
  its own so they are told apart at a glance. A first draft, to be confirmed
  on a drawn screen before the apps are built:

  | Token | Colour | Used for |
  | --- | --- | --- |
  | page | `#F3F6F4` | background |
  | ink / secondary | `#17251F` / `#4B5A53` | headings and values / prose |
  | primary (road green) | `#1F8A5B`, hover `#146B45` | buttons, current nav, your trucks |
  | amber | `#E8A317` fill, `#8A5A00` text | due soon, waiting on a carrier |
  | red | `#C9343D` | only a person needs to look: a grounded or overdue truck, a failed delivery |
  | road / verge | `#5B6475` / `#E3F1E6` | the map |

- **Screens before code.** Like Simple WMS, the screens are drawn first (a
  `design/` folder with static HTML per screen and a gallery), and the apps
  are built to them.

## Dispatcher desktop

- **Today** — a map of stops and trucks, a column per run. Unassigned
  consignments sit in a tray; drag them onto runs and reorder the stops. Each
  run shows weight and volume against the truck's limits and goes red when
  over. A grounded or overdue truck cannot be given a run.
- **Consignments** — search by WMS delivery, customer or con note; each one's
  status history.
- **Carriers** — today's carrier consignments, con note entry, Print manifest.
- **Fleet** — vehicles, drivers, pre-start results, rego and service due.

## Driver app

A PWA installed from the browser, like the Simple WMS scanner.

- Sign in with driver code and PIN, pick the truck, do the pre-start check.
- The run one stop at a time: address with a button that opens the phone's own
  maps app, the cartons to drop, delivery notes.
- Proof of delivery: scan or tick each carton off, then a signature on the
  screen with a printed name, a photo, or both, as the customer requires.
- Can't deliver: a reason (closed, refused, damaged, wrong address) and a
  photo; the consignment returns to the depot on the run.
- Black spots: the whole run loads at the start of the shift. Confirmations
  are queued with their `message_id`, so a retry never records twice, and send
  when coverage returns. The scanner's queue, nearly as it is.

## Fleet

**Pre-start checks.** A checklist per vehicle type (van, rigid, ute): tyres,
lights, brakes, mirrors, load restraints, damage. A failed item needs a photo
and a note. A failed critical item grounds the truck until a fleet manager
marks it fixed, with a note. The driver records the odometer, which drives
service-by-kilometres. Every check is kept and never overwritten.

**Rego and service due.** Per vehicle: rego expiry, last service, next service
by date or kilometres, whichever comes first. Amber 30 days or 1,000 km out,
red when overdue; a red truck cannot go on a run. On screen only in version
one; `vehicle.due` lets another system send email or SMS.

**Live location.** The driver app sends its position about every 30 seconds
from starting a run until finishing it, never outside a run, and says on
screen that it is doing so (some states regulate employee tracking, e.g. the
NSW Workplace Surveillance Act; each business checks its own obligations). The
dispatcher sees each truck and when it was last heard from. ETAs come from the
stop order and the driver's average time per stop so far, labelled as
estimates. Map tiles come from a configured tile provider or a self-hosted
server, not OpenStreetMap's public servers. Positions are kept 90 days by
default, then deleted.

## Data

`consignment` (+ `package`), `consignment_event` (append-only status history:
packed, assigned, departed, delivered, failed — never updated in place),
`run`, `stop`, `vehicle`, `driver`, `prestart_check`, `carrier`, `manifest`,
`pod` (signature, photo, name, time, position; files on disk, referenced from
the row), `position`, `inbound_event` (seen WMS event ids), `outbound_call`
(queued calls to the WMS and Platen).

## Testing

- API: pytest against a real Postgres, as Simple WMS does — consignment from a
  signed event, a repeated event ignored, run capacity limits, a grounded truck
  refused, the ship call and its retry, proof of delivery.
- Driver app: Vitest for the offline queue and the stop flow.
- End to end: pack in Simple WMS → plan a run → deliver in the driver app →
  `delivery.shipped` in the WMS and the docket printed by Platen.

## Build order

Each step is usable before the next starts.

1. Skeleton: repo, Docker, sign-in, vehicles and drivers, the WMS subscriber
   creating consignments.
2. Own fleet: runs, the drag-to-plan screen, run sheet printing, the ship call
   to the WMS.
3. Driver app: stops, proof of delivery, failed deliveries, the offline queue.
4. Fleet care: pre-start checks, rego and service due, grounding.
5. Live location: positions on the map, ETAs.
6. Carriers: con note entry, manifests, an adapter shape for carrier APIs.
