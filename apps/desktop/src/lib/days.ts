/** The days a dispatcher plans: today and the week ahead, by the depot
 * computer's own calendar, as YYYY-MM-DD for the API. */

export function isoDay(d: Date): string {
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

/** "2026-10-13" as that day on this computer's calendar. */
export function fromIso(day: string): Date {
  const [y, m, d] = day.split("-").map(Number);
  return new Date(y, m - 1, d);
}

/** "Wed 14 Oct", without the comma en-AU puts after the weekday. */
export function dayLabel(d: Date): string {
  const parts = new Intl.DateTimeFormat("en-AU", { weekday: "short", day: "numeric", month: "short" })
    .formatToParts(d);
  const get = (type: string) => parts.find((p) => p.type === type)?.value ?? "";
  return `${get("weekday")} ${get("day")} ${get("month")}`;
}

export function dayOptions(today: Date, ahead = 6): { value: string; label: string }[] {
  const out = [];
  for (let i = 0; i <= ahead; i += 1) {
    const d = new Date(today.getFullYear(), today.getMonth(), today.getDate() + i);
    const name = i === 0 ? "Today · " : i === 1 ? "Tomorrow · " : "";
    out.push({ value: isoDay(d), label: name + dayLabel(d) });
  }
  return out;
}

export function clock(d: Date): string {
  return new Intl.DateTimeFormat("en-AU", { hour: "numeric", minute: "2-digit" }).format(d);
}
