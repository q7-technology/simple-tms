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
