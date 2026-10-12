/** The run on the phone. It is loaded whole when the driver starts, kept in
 * localStorage so a black spot or a reload changes nothing, and read
 * together with the queue: a stop confirmed on the phone is done here at
 * once, whether or not it has reached the office yet. Kept under the
 * driver's id, so another driver signed in on the phone never sees it. */
import type { Outcome, RunStops, StopDetail } from "../api/types";
import type { QueueItem } from "./queue";

const RUN = (driver: number, id: number) => `tms.driver.run.${driver}.${id}`;
const TICKS = (driver: number, run: number, consignment: number) => `tms.driver.ticks.${driver}.${run}.${consignment}`;
const CURRENT = (driver: number) => `tms.driver.current.${driver}`;

function read<T>(key: string): T | null {
  try {
    const raw = window.localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : null;
  } catch {
    return null;
  }
}

function write(key: string, value: unknown) {
  try { window.localStorage.setItem(key, JSON.stringify(value)); } catch { /* full: kept for this visit only */ }
}

export function saveRun(driver: number, run: RunStops) { write(RUN(driver, run.id), run); }
export function loadRun(driver: number, id: number): RunStops | null { return read<RunStops>(RUN(driver, id)); }

/** The run the driver last carried on with, for "from memory" with no signal. */
export function rememberCurrent(driver: number, runId: number) { write(CURRENT(driver), runId); }
export function currentRun(driver: number): number | null { return Number(read<number>(CURRENT(driver))) || null; }

export type How = "scan" | "tick";
export type Ticks = Record<number, How>;

export function loadTicks(driver: number, runId: number, consignmentId: number): Ticks {
  return read<Ticks>(TICKS(driver, runId, consignmentId)) ?? {};
}
export function saveTicks(driver: number, runId: number, consignmentId: number, ticks: Ticks) {
  write(TICKS(driver, runId, consignmentId), ticks);
}

/** What happened at each stop: the office's record, and what is on its way
 * from this phone (a confirmation that was refused doesn't count). */
export function outcomes(run: RunStops, items: QueueItem[]): Map<number, Outcome> {
  const out = new Map<number, Outcome>();
  for (const s of run.stops) if (s.outcome) out.set(s.consignment_id, s.outcome);
  for (const i of items) {
    if (i.run_id !== run.id || i.status === "failed" || i.consignment_id === null) continue;
    if (i.outcome === "delivered" || i.outcome === "failed") {
      if (!out.has(i.consignment_id)) out.set(i.consignment_id, i.outcome);
    }
  }
  return out;
}

export function nextStop(run: RunStops, done: Map<number, Outcome>): StopDetail | null {
  return run.stops.find((s) => !done.has(s.consignment_id)) ?? null;
}

/** Finished on the office's record, or the phone has sent (or is sending) it. */
export function finished(run: RunStops, items: QueueItem[]): boolean {
  return run.status === "done"
    || items.some((i) => i.run_id === run.id && i.outcome === "finished" && i.status !== "failed");
}

export function stopOf(run: RunStops, consignmentId: number): StopDetail | null {
  return run.stops.find((s) => s.consignment_id === consignmentId) ?? null;
}

export function plural(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`;
}
