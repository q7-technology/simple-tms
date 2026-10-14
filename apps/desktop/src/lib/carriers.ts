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

/** Once every ship call is done. Back from its run, a consignment was
 * shipped in Simple WMS already: it keeps the run's carrier and number. */
function shippedLine(m: Manifest): string {
  const back = m.already_shipped ?? [];
  if (!back.length) return "marked shipped in Simple WMS";
  const runs = [...new Set(back.map((b) => b.tracking_no).filter(Boolean))];
  const known = back.every((b) => b.tracking_no);
  const said = !known ? "already shipped in Simple WMS: it keeps what it had"
    : `already shipped by ${back.length === 1 ? "its run" : "their runs"}`;
  if (back.length >= m.lines.length) return known ? `${said}: Simple WMS still has ${runs.join(", ")}` : said;
  const who = back.map((b) => b.customer).join(", ");
  return known ? `marked shipped in Simple WMS, but for ${who}: ${said}, Simple WMS still has ${runs.join(", ")}`
    : `marked shipped in Simple WMS, but for ${who}: ${said}`;
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
    else parts.push(shippedLine(m));
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
