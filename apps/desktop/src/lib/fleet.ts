/** How the Fleet drawer words each truck's standing (Fleet.html). The API
 * works out what is due; this only says it, in the depot's own dates. Whole
 * kilometres throughout, as the API keeps them. */
import type { Vehicle } from "../api/types";
import { clock, dayLabel, isoDay } from "./days";

export type Tone = "" | "a" | "r";
export interface Cell { text: string; sub: string | null; tone: Tone }
export interface Pill { text: string; tone: "green" | "amber" | "red" | "grey" }

/** 61900 -> "61,900" */
export function km(n: number): string {
  return n.toLocaleString("en-AU");
}

/** "2027-03-03" -> "3 Mar 2027", read as the calendar date it is */
export function longDate(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  const month = new Intl.DateTimeFormat("en-AU", { month: "short" }).format(new Date(y, m - 1, d));
  return `${d} ${month} ${y}`;
}

const RANK = { unknown: 0, ok: 0, due: 1, overdue: 2 } as const;
const tone = (state: keyof typeof RANK): Tone => (state === "overdue" ? "r" : state === "due" ? "a" : "");

function days(n: number): string {
  return `${n} ${n === 1 ? "day" : "days"}`;
}

export function regoCell(v: Vehicle): Cell {
  const r = v.rego_due;
  if (!r?.expires_on) return { text: "Not recorded", sub: null, tone: "" };
  const left = r.days_left ?? 0;
  const sub = r.state === "overdue" ? `ran out ${days(-left)} ago`
    : r.state === "due" ? (left === 0 ? "runs out today" : `due in ${days(left)}`) : null;
  return { text: longDate(r.expires_on), sub, tone: tone(r.state) };
}

/** The next service by date or kilometres, the nearer first: the one in
 * worse standing, or the date when they stand alike. */
export function serviceCell(v: Vehicle): Cell {
  const s = v.service_due;
  if (!s || (!s.due_on && s.due_km == null)) {
    return { text: v.service_every_months || v.service_every_km ? "Not recorded" : "No schedule", sub: null, tone: "" };
  }
  const byDays = s.days_left == null ? "unknown" : s.days_left < 0 ? "overdue" : s.days_left <= 30 ? "due" : "ok";
  const byKm = s.km_left == null ? "unknown" : s.km_left < 0 ? "overdue" : s.km_left <= 1000 ? "due" : "ok";
  const kmText = s.km_left == null ? null : s.km_left < 0 ? `${km(-s.km_left)} km over` : `in ${km(s.km_left)} km`;
  if (kmText && (!s.due_on || RANK[byKm] > RANK[byDays])) {
    return { text: kmText, sub: s.due_on ? `or ${longDate(s.due_on)}` : null, tone: tone(byKm) };
  }
  return { text: longDate(s.due_on!), sub: s.due_km != null ? `or ${km(s.due_km)} km` : null,
           tone: tone(byDays === "unknown" ? "ok" : byDays) };
}

/** When the last pre-start was, by the depot's clock: "Today 6:52 am", "Mon 6:58 am". */
export function when(at: string, now = new Date()): string {
  const d = new Date(at);
  if (isoDay(d) === isoDay(now)) return `Today ${clock(d)}`;
  const age = (now.getTime() - d.getTime()) / 86_400_000;
  return age < 6 ? `${dayLabel(d).split(" ")[0]} ${clock(d)}` : dayLabel(d);
}

export function checkCell(v: Vehicle, now = new Date()): Cell {
  const c = v.last_prestart;
  if (!c) return { text: "None yet", sub: null, tone: "" };
  const sub = c.result === "passed" ? "Passed" : c.result === "grounded" ? "Failed" : "An item failed";
  return { text: when(c.at, now), sub, tone: c.result === "grounded" ? "r" : c.result === "failed" ? "a" : "" };
}

/** STATUS: what most needs a person, else where the truck is today. */
export function statusPill(v: Vehicle, onRun: boolean): Pill {
  if (!v.active) return { text: "Deactivated", tone: "grey" };
  if (v.state === "grounded") return { text: "Grounded", tone: "red" };
  if (v.state === "overdue") {
    return { text: v.rego_due?.state === "overdue" ? "Rego overdue" : "Service overdue", tone: "red" };
  }
  if (v.state === "due") return { text: v.rego_due?.state === "due" ? "Rego due" : "Service due", tone: "amber" };
  return onRun ? { text: "On a run", tone: "green" } : { text: "At depot", tone: "grey" };
}

/** Trucks that can't take a run today: for the map's Fleet button. */
export function cantGo(vehicles: Vehicle[]): number {
  return vehicles.filter((v) => v.active && (v.state === "grounded" || v.state === "overdue")).length;
}
