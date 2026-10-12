import type { Vehicle } from "../api/types";
import { cantGo, checkCell, longDate, regoCell, serviceCell, statusPill } from "../lib/fleet";

const base: Vehicle = { id: 1, rego: "TMS041", type: "rigid", max_kg: "4500", max_m3: "22", active: true, state: "ok" };
const service = (over: Partial<NonNullable<Vehicle["service_due"]>>): Vehicle => ({
  ...base, service_every_months: 6, service_every_km: 15000,
  service_due: { last_on: "2026-07-14", last_km: 46900, due_on: "2027-01-14", due_km: 61900,
                 days_left: 93, km_left: 3000, state: "ok", ...over },
});

describe("how Fleet words a truck's standing", () => {
  it("gives dates as the calendar has them", () => {
    expect(longDate("2027-03-03")).toBe("3 Mar 2027");
  });

  it("says when the rego runs out, amber a month out and red after", () => {
    expect(regoCell(base)).toEqual({ text: "Not recorded", sub: null, tone: "" });
    const rego = (days_left: number, state: "ok" | "due" | "overdue") =>
      regoCell({ ...base, rego_due: { expires_on: "2026-10-25", days_left, state } });
    expect(rego(142, "ok")).toEqual({ text: "25 Oct 2026", sub: null, tone: "" });
    expect(rego(12, "due")).toEqual({ text: "25 Oct 2026", sub: "due in 12 days", tone: "a" });
    expect(rego(0, "due").sub).toBe("runs out today");
    expect(rego(-3, "overdue")).toEqual({ text: "25 Oct 2026", sub: "ran out 3 days ago", tone: "r" });
  });

  it("puts the nearer of the service date and kilometres first", () => {
    expect(serviceCell(service({}))).toEqual({ text: "14 Jan 2027", sub: "or 61,900 km", tone: "" });
    expect(serviceCell(service({ km_left: 640, state: "due" }))).toEqual(
      { text: "in 640 km", sub: "or 14 Jan 2027", tone: "a" });
    expect(serviceCell(service({ km_left: -100, days_left: 12, state: "overdue" }))).toEqual(
      { text: "100 km over", sub: "or 14 Jan 2027", tone: "r" });
    expect(serviceCell(service({ days_left: -2, state: "overdue" })).tone).toBe("r");
    expect(serviceCell(base).text).toBe("No schedule");
    expect(serviceCell({ ...base, service_every_km: 15000 }).text).toBe("Not recorded");
  });

  it("says when the last pre-start was and how it went", () => {
    const now = new Date(2026, 9, 14, 9, 0);
    const at = new Date(2026, 9, 14, 6, 52).toISOString();
    expect(checkCell({ ...base, last_prestart: { id: 1, at, result: "passed", by: "kim" } }, now))
      .toEqual({ text: "Today 6:52 am", sub: "Passed", tone: "" });
    expect(checkCell({ ...base, last_prestart: { id: 1, at, result: "grounded", by: "kim" } }, now).tone).toBe("r");
    expect(checkCell(base, now).text).toBe("None yet");
  });

  it("shows what most needs a person first", () => {
    expect(statusPill({ ...base, state: "grounded" }, true)).toEqual({ text: "Grounded", tone: "red" });
    expect(statusPill({ ...base, state: "overdue", rego_due: { expires_on: "2026-10-01", days_left: -13, state: "overdue" } }, false))
      .toEqual({ text: "Rego overdue", tone: "red" });
    expect(statusPill({ ...base, state: "due" }, false)).toEqual({ text: "Service due", tone: "amber" });
    expect(statusPill(base, true)).toEqual({ text: "On a run", tone: "green" });
    expect(statusPill({ ...base, active: false, state: "grounded" }, false).text).toBe("Deactivated");
    expect(cantGo([{ ...base, state: "grounded" }, { ...base, state: "overdue", active: false }, base])).toBe(1);
  });
});
