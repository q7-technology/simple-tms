# Simple TMS screens

The drawn screens for Simple TMS, made before any app code, as the design doc
asks. They follow Simple WMS's Game Mode: the dispatcher runs the day from a
small hand-drawn map, every job opens as a drawer over it, and the driver app
works like the WMS scanner.

- `index.html`: gallery of every screen. Open it in a browser.
- `screens/`: one static HTML file per screen, each in its starting state.
  - Desktop, 1440 × 900 (works down to about 960 wide): `Main.html` (the map,
    "Today"), `PlanRuns.html`, `Consignments.html`, `Carriers.html`,
    `Fleet.html`.
  - Driver app, 390 × 844: `DriverSignIn.html`, `DriverPrestart.html`,
    `DriverStop.html`, `DriverProof.html`, `DriverCantDeliver.html`,
    `DriverOffline.html`.
  - `region.svg`: the map picture (depot, roads, customer stops, trucks),
    shared by the desktop screens the way `warehouse.svg` is in Simple WMS.

No build step and no JavaScript in the screens; the gallery uses a few lines
of script only to scale its previews.

## Colours

Same bones as Simple WMS (Manrope, white cards on a light page, the same
spacing, radius and shadows); its own colour so the apps are told apart.

| Token | Colour | Used for |
| --- | --- | --- |
| page | `#F3F6F4` | background |
| ink / secondary | `#17251F` / `#4B5A53` | headings and values / prose |
| muted | `#5B6B63` | labels and small print |
| primary (road green) | `#1F8A5B`, hover `#146B45` | buttons, current nav, your trucks |
| green tint | `#E7F4EC` with `#146B45` text | selected things, "on the road" |
| amber | `#E8A317` fill, `#8A5A00` text, `#FFF3D6` tint | due soon, waiting on a carrier or con note, nearly full |
| red | `#C9343D` fill, `#A3242C` text, `#FCE9EA` tint | only when a person needs to look: grounded or overdue truck, failed delivery, over capacity |
| road / verge | `#5B6475` / `#E3F1E6` | the map |
| run colours (map only) | `#1F8A5B`, `#2F6FB8`, `#7B5CC4` | telling runs apart on the map and the plan board |

Amber and red text always use the darker text colour, never the fill, so it
passes contrast on white.

## Sample data

All names, numbers and references are made-up samples: customers, carriers
(marked "(sample)"), drivers (first names only), regos (`TMS-…`), runs
(`R-…`), consignments (`C-…`), con notes and WMS delivery numbers. The suburbs
are real places around Ballarat and central Victoria, used without street
numbers.
