/** The plan board's own logic, kept out of the components so it can be
 * tested on its own: cards from consignments, and where a drop lands. */
import type { Card, Consignment, HeldBy, Shipping } from "../api/types";
import { plain, toMicro } from "./decimal";

export function cardOf(c: Consignment): Card {
  return {
    id: c.id, wms_ref: c.wms_ref, customer: c.customer, suburb: c.ship_to?.suburb ?? null,
    status: c.status, packages: c.packages.length,
    cartons: c.packages.filter((p) => p.type === "carton").length,
    weight_kg: c.weight_kg, volume_m3: c.volume_m3,
  };
}

/** "5 ctn" when every package is a carton, "3 pkg" otherwise. */
export function packagesLabel(c: Card): string {
  return `${c.packages} ${c.packages > 0 && c.cartons === c.packages ? "ctn" : "pkg"}`;
}

export function kg(c: Card): string {
  return `${plain(toMicro(c.weight_kg))} kg`;
}

export function m3(c: Card): string {
  return `${plain(toMicro(c.volume_m3))} m³`;
}

/** The whole order after moving one stop from one place to another (both
 * counted from 0). Out of range is no move. */
export function moved(ids: number[], from: number, to: number): number[] {
  if (from < 0 || from >= ids.length || to < 0 || to >= ids.length || from === to) return ids;
  const out = [...ids];
  const [id] = out.splice(from, 1);
  out.splice(to, 0, id);
  return out;
}

/** Where a card dragged over a stop would go: above the stop when the
 * pointer is in its top half, below it otherwise. Positions count from 1,
 * as the API's do. */
export function dropPosition(stopIndex: number, pointerY: number, top: number,
                             height: number): number {
  return pointerY < top + height / 2 ? stopIndex + 1 : stopIndex + 2;
}

/** The API's position for a stop already on this run, dropped at `position`
 * among the stops as they are drawn (with it still in its old place). */
export function positionWithout(currentIndex: number, position: number): number {
  return currentIndex + 1 < position ? position - 1 : position;
}

const RUN_COLOURS = ["#1F8A5B", "#2F6FB8", "#7B5CC4"];

export function runColour(index: number): string {
  return RUN_COLOURS[index % RUN_COLOURS.length];
}

/** An earlier call to Simple WMS, in words: "R-0007's return to Simple WMS". */
export function heldCall(h: HeldBy): string {
  return `${h.run_no ?? h.manifest_no}'s ${h.kind === "return" ? "return" : "ship call"} to Simple WMS`;
}

/** What holds a run's or a manifest's calls to Simple WMS: the first
 * earlier call they wait on, in words, and whether it gave up (so Try again
 * starts it); null when nothing holds them. */
export function waitingOn(shipping: Shipping[]): { text: string; failed: boolean } | null {
  const h = shipping.flatMap((s) => (s.held ? s.held_by ?? [] : []))[0];
  if (!h) return null;
  return h.status === "failed"
    ? { text: `Waiting on ${heldCall(h)}, which failed${h.last_error ? `: ${h.last_error}` : "."}`, failed: true }
    : { text: `Waiting on ${heldCall(h)}, which is still being tried.`, failed: false };
}
