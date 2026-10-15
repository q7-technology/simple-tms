/** A depot for the Fleet drawer's admin tests: trucks, drivers and the
 * checklists, as the API gives them; anything else answered by `answers`. */
import type { ChecklistItem, Driver, Vehicle } from "../api/types";
import { json, MANAGER, path, stubApi, type Call } from "./harness";

const due = { expires_on: "2027-03-03", days_left: 142, state: "ok" as const };
const service = { last_on: "2026-07-14", last_km: 46900, due_on: "2027-01-14", due_km: 61900,
                  days_left: 92, km_left: 3000, state: "ok" as const };

export const truck = (rego: string, over: Partial<Vehicle> = {}): Vehicle => ({
  id: 1, rego, type: "rigid", max_kg: "4500", max_m3: "22", active: true, service_every_months: 6,
  service_every_km: 15000, odometer_km: 58900, rego_due: due, service_due: service, grounded: null,
  last_prestart: null, state: "ok", blocked: null, ...over,
});

export const person = (code: string, name: string, over: Partial<Driver> = {}): Driver => ({
  id: 1, code, name, active: true, locked: false, failed_attempts: 0, created_at: "2026-10-01T00:00:00Z", ...over,
});

const line = (id: number, label: string, critical = false): ChecklistItem => ({ id, label, critical });
export const VAN_LIST = [line(1, "Tyres"), line(2, "Lights", true), line(3, "Brakes", true), line(4, "Mirrors"),
                         line(5, "Load restraints"), line(6, "Damage")];

export function fleetDepot(answers: Record<string, (c: Call) => Response | null> = {}, who: object = MANAGER, state: {
  vehicles?: Vehicle[]; drivers?: Driver[];
} = {}) {
  return stubApi((method, url, body) => {
    const key = `${method} ${path(url)}`;
    if (answers[key]) return answers[key]({ method, url, body });
    if (key === "GET /v1/vehicles") {
      const items = state.vehicles ?? [truck("TMS041"), truck("TMS018", { type: "van", max_kg: "1200", max_m3: "8.5" })];
      return json(200, { items, total: items.length });
    }
    if (key === "GET /v1/drivers") {
      const items = state.drivers ?? [person("kim", "Kim"), person("tom", "Tom", { locked: true, failed_attempts: 5 })];
      return json(200, { items, total: items.length });
    }
    if (key === "GET /v1/runs") return json(200, { items: [], total: 0 });
    if (key === "GET /v1/checklists") {
      return json(200, { van: VAN_LIST, rigid: VAN_LIST.map((l) => ({ ...l, id: l.id + 6 })),
                         ute: VAN_LIST.map((l) => ({ ...l, id: l.id + 12 })) });
    }
    if (key.startsWith("GET /v1/vehicles/") && key.endsWith("/history")) return json(200, []);
    return null;
  }, who);
}
