/** A run of two stops, as GET /v1/runs/{id}/stops gives it; and as today's
 * runs list it, with its truck's checklist. */
import type { ChecklistLine, DriverRun, RunStops, StopDetail } from "../api/types";

export const stop = (n: number, customer: string, over: Partial<StopDetail> = {}): StopDetail => ({
  sequence: n, consignment_id: 30 + n, wms_ref: `00800345${n}`, customer,
  ship_to: { name: customer, address: "12 Sturt St", suburb: "Ballarat", state: "VIC", postcode: "3350",
             country: "AU", contact: "Priya", phone: "03 5300 0000" },
  packages: [
    { package_no: 1, type: "carton", weight_kg: "9.5", sscc: `093123450000000${n}01` },
    { package_no: 2, type: "carton", weight_kg: null, sscc: null },
  ],
  outcome: null, outcome_at: null, ...over,
});

export const RUN: RunStops = {
  id: 7, run_no: "R-0007", date: "2026-10-14", status: "departed",
  vehicle: { rego: "TMS041", type: "rigid" }, driver: { code: "kim", name: "Kim" },
  departed_at: "2026-10-14T21:10:00Z", finished_at: null,
  stops: [stop(1, "Hillside Bakery"), stop(2, "Two Rivers Vet")],
};

export const CHECKLIST: ChecklistLine[] = [
  { id: 7, label: "Tyres", critical: false }, { id: 8, label: "Lights", critical: true },
  { id: 9, label: "Brakes", critical: true }, { id: 10, label: "Mirrors", critical: false },
  { id: 11, label: "Load restraints", critical: false }, { id: 12, label: "Damage", critical: false },
];

/** R-0007 as GET /v1/driver/runs gives it: planned, its pre-start done unless said. */
export const TODAY: DriverRun = {
  id: 7, run_no: "R-0007", date: "2026-10-14", status: "planned",
  vehicle: { rego: "TMS041", type: "rigid", odometer_km: 61000, blocked: null },
  stops: 2, packages: 4, delivered: 0, failed: 0,
  prestart: { id: 1, at: "2026-10-14T19:52:00Z", result: "passed" }, checklist: CHECKLIST,
};
