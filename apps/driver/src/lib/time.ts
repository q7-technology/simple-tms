/** Dates and times as the phone has them. */

/** YYYY-MM-DD by the phone's own calendar: the day the driver means by "today". */
export function isoDay(d: Date): string {
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

/** "Tue 13 Oct", without the comma en-AU puts after the weekday. */
export function dayLabel(d: Date): string {
  const parts = new Intl.DateTimeFormat("en-AU", { weekday: "short", day: "numeric", month: "short" }).formatToParts(d);
  const get = (type: string) => parts.find((p) => p.type === type)?.value ?? "";
  return `${get("weekday")} ${get("day")} ${get("month")}`;
}

/** "10:41 am" */
export function clock(d: Date): string {
  return new Intl.DateTimeFormat("en-AU", { hour: "numeric", minute: "2-digit" }).format(d);
}
