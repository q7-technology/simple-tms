/** What the admin forms (vehicles, drivers, checklists, carriers) check
 * before they send, with the API's own rules said in the screens' words.
 * The API checks everything again: these only save a trip and say what is
 * wrong beside the field. */
import { ApiError, Unreachable } from "../api/client";
import type { Carrier, ChecklistLine, Driver, Vehicle } from "../api/types";

/** A field's name, as the API names it, and what is wrong with it. */
export type Problems = Record<string, string>;

export const VEHICLE_TYPES = ["van", "rigid", "ute"] as const;
export type VehicleType = (typeof VEHICLE_TYPES)[number];
export const typeName = (t: string) => t[0].toUpperCase() + t.slice(1);

/** "1,200" and " 8.5 " as typed: spaces and thousands commas go. */
function digits(raw: string): string {
  return raw.replace(/[\s,]/g, "");
}

/** As the API stores it: "abc 123" and "ABC123" are the same plate. */
export function normalRego(raw: string): string {
  return raw.replace(/\s+/g, "").toUpperCase();
}

export function regoProblem(rego: string, vehicles: Vehicle[]): string | null {
  if (!rego) return "Type the rego.";
  if (!/^[A-Z0-9-]{1,12}$/.test(rego)) return "A rego is letters, digits and dashes, at most 12.";
  const there = vehicles.find((v) => v.rego === rego);
  if (there) return there.active ? `${rego} is already on file.` : `${rego} is already on file, deactivated: reactivate it instead.`;
  return null;
}

/** The most a truck carries, in kg or m³: above 0, at most 3 decimal places
 * and 9 whole digits (the API's 12 digits in all). Kept as the typed text,
 * never a float. */
export function amount(raw: string): string {
  const t = digits(raw);
  return t.includes(".") ? t.replace(/0+$/, "").replace(/\.$/, "") : t;
}

export function amountProblem(raw: string, unit: "kg" | "m³"): string | null {
  const t = amount(raw);
  if (!t) return `Give the most it carries, in ${unit}.`;
  const m = /^(\d+)(?:\.(\d+))?$/.exec(t);
  if (!m) return `A number, like ${unit === "kg" ? "1200" : "8.5"}.`;
  if ((m[2] ?? "").length > 3) return "At most 3 decimal places.";
  if (m[1].replace(/^0+/, "").length > 9) return "That is more than any truck carries.";
  if (!/[1-9]/.test(t)) return "More than 0.";
  return null;
}

/** A service interval: blank is none (never due that way), else a whole
 * number from 1 to the API's most. */
export function interval(raw: string): number | null {
  const t = digits(raw);
  return t ? Number(t) : null;
}

export function intervalProblem(raw: string, most: number, unit: string): string | null {
  const t = digits(raw);
  if (!t) return null;
  if (!/^\d+$/.test(t)) return `Whole ${unit}, or leave it blank.`;
  const n = Number(t);
  if (n < 1 || n > most) return `From 1 to ${most.toLocaleString("en-AU")} ${unit}, or leave it blank.`;
  return null;
}

/** As the API keeps it: lower case, so KIM and kim are one driver. */
export function normalCode(raw: string): string {
  return raw.trim().toLowerCase();
}

export function codeProblem(code: string, drivers: Driver[]): string | null {
  if (!code) return "Type the code the driver signs in with.";
  if (!/^[a-z0-9._-]{1,32}$/.test(code)) return "Letters, digits, dots, dashes and underscores, at most 32.";
  const there = drivers.find((d) => d.code === code);
  if (there) return there.active ? `${code} is already ${there.name}'s.` : `${code} is already ${there.name}'s, deactivated: reactivate them instead.`;
  return null;
}

export function nameProblem(name: string, most: number, what = "the name"): string | null {
  const t = name.trim();
  if (!t) return `Type ${what}.`;
  if (t.length > most) return `At most ${most} characters.`;
  return null;
}

export function pinProblem(pin: string): string | null {
  return /^[0-9]{4,8}$/.test(pin) ? null : "A PIN is 4 to 8 digits.";
}

/** One carrier per name whatever its case, and never "Own fleet": that is
 * what Simple WMS is told for your own trucks. */
export function carrierNameProblem(name: string, carriers: Carrier[], otherThan?: number): string | null {
  const t = name.trim();
  const short = nameProblem(t, 64, "the carrier's name");
  if (short) return short;
  if (t.toLowerCase() === "own fleet") return "Own fleet is what Simple WMS is told for your own trucks: give the carrier its own name.";
  const there = carriers.find((c) => c.id !== otherThan && c.name.toLowerCase() === t.toLowerCase());
  if (there) return there.active ? `${there.name} is already on file.` : `${there.name} is already on file, retired: bring it back instead.`;
  return null;
}

/** A checklist as it will be sent: each line's problem by its place, and
 * one for the list as a whole. */
export function checklistProblems(lines: ChecklistLine[]): { lines: (string | null)[]; list: string | null } {
  const seen = new Map<string, number>();
  const out = lines.map((l, i) => {
    const label = l.label.trim();
    if (!label) return "Type what to check, or take the line out.";
    if (label.length > 80) return "At most 80 characters.";
    const key = label.toLowerCase();
    if (seen.has(key)) return `Line ${seen.get(key)! + 1} says this already.`;
    seen.set(key, i);
    return null;
  });
  const list = lines.length === 0 ? "A checklist needs at least one line."
    : lines.length > 30 ? "At most 30 lines." : null;
  return { lines: out, list };
}

/** The API's own field errors (a 422), by field, or null for anything else. */
export function apiProblems(e: unknown): Problems | null {
  if (!(e instanceof ApiError) || e.status !== 422 || e.errors.length === 0) return null;
  return Object.fromEntries(e.errors.map((f) => [f.field, f.message]));
}

/** What to say when a request didn't work: the API's own words, or that
 * it couldn't be reached. */
export function said(e: unknown): string {
  if (e instanceof Unreachable) return e.message;
  return e instanceof ApiError ? e.message : "Simple TMS could not be reached. Check the connection and try again.";
}

/** Only the problems there are. */
export function found(problems: Record<string, string | null>): Problems {
  return Object.fromEntries(Object.entries(problems).filter((p): p is [string, string] => p[1] !== null));
}
