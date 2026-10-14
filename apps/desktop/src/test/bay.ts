/** Manifests on the carrier bay, for the desktop's tests. The carrier names
 * are samples, never real carriers. */
import type { Manifest, ManifestLine } from "../api/types";

export const RIDGELINE = "Ridgeline Express (sample)";
export const VALLEY = "Valley Freight (sample)";
export const PLAINS = "Plains Parcels (sample)";

export const line = (id: number, customer: string, con_note: string | null, cartons = 2, weight_kg = "24"): ManifestLine => ({
  con_note, added_at: "",
  consignment: { id, wms_ref: `00800345${id}`, customer, suburb: "Daylesford", status: "booked", packages: cartons,
                 cartons, weight_kg, volume_m3: "0.1" },
});

export const manifest = (id: number, carrier: string, lines: ManifestLine[], over: Partial<Manifest> = {}): Manifest => ({
  id, manifest_no: `M-000${id}`, date: "2026-10-15", status: "open",
  carrier: { id, name: carrier, account: null, pickup_time: "15:30", adapter: "manual", active: true, created_at: "" },
  created_by: "dee", created_at: "", collected_at: null, collected_by: null, lines,
  totals: { consignments: lines.length, packages: lines.reduce((n, l) => n + l.consignment.packages, 0),
            cartons: lines.reduce((n, l) => n + l.consignment.cartons, 0), weight_kg: "96" },
  needs_con_note: lines.filter((l) => !l.con_note).length, print_job: null,
  shipping: { pending: 0, done: 0, failed: 0 }, already_shipped: [], ...over,
});
