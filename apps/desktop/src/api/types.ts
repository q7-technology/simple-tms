/** Response shapes, as docs/api.md gives them. Decimals are strings. */

export interface Page<T> { items: T[]; total: number }

export interface Me {
  kind: "user" | "driver" | "key";
  id: number;
  name: string;
  display_name: string;
  role: string;
  scopes: string[];
}

export interface Package {
  package_no: number;
  type: string;
  weight_kg: string | null;
  length_cm: string | null;
  width_cm: string | null;
  height_cm: string | null;
  sscc: string | null;
}

export interface Consignment {
  id: number;
  wms_ref: string;
  warehouse: string;
  owner: string;
  customer: string;
  ship_to: Record<string, string | null>;
  required_by: string | null;
  status: string;
  created_at: string;
  weight_kg: string;
  volume_m3: string;
  packages: Package[];
  /** where it is now: the run it is on (or was last on), or the carrier it is booked with */
  run?: { id: number; run_no: string; driver: string; sequence: number } | null;
  carrier?: { id: number; name: string; con_note: string | null; manifest_id: number; manifest_no: string;
              collected_at: string | null } | null;
}

/** A line of a consignment's history, oldest first (GET /v1/consignments/{id}). */
export interface HistoryRow { kind: string; at: string; detail: Record<string, unknown> }

export interface ConsignmentDetail extends Consignment { history: HistoryRow[] }

/** Why a truck can't take a run on a day: grounded, or rego or service overdue. */
export interface Blocked { code: "vehicle_grounded" | "vehicle_overdue" | string; message: string }

export type DueState = "ok" | "due" | "overdue" | "unknown";
export type CheckResult = "passed" | "failed" | "grounded";

/** A vehicle, and (from step 4) where it stands on the day asked about. */
export interface Vehicle {
  id: number;
  rego: string;
  type: string;
  max_kg: string;
  max_m3: string;
  active: boolean;
  service_every_months?: number | null;
  service_every_km?: number | null;
  odometer_km?: number | null;
  rego_due?: { expires_on: string | null; days_left: number | null; state: DueState };
  service_due?: { last_on: string | null; last_km: number | null; due_on: string | null; due_km: number | null;
                  days_left: number | null; km_left: number | null; state: DueState };
  grounded?: { check_id: number; at: string; failed: string[] } | null;
  last_prestart?: { id: number; at: string; result: CheckResult; by: string } | null;
  state?: "ok" | "due" | "overdue" | "grounded";
  blocked?: Blocked | null;
}

/** One pre-start check as the driver sent it (GET /v1/prestarts/{id}). */
export interface PrestartDetail {
  id: number;
  run_id: number;
  run_no: string;
  vehicle: string;
  vehicle_type: string;
  driver: { code: string; name: string } | null;
  at: string;
  recorded_at: string;
  odometer_km: number;
  result: CheckResult;
  items: { label: string; critical: boolean; passed: boolean; note: string | null; photo_file: string | null }[];
  fixed: { id: number; note: string; by: string; at: string } | null;
}

/** A line of a truck's history, newest first (GET /v1/vehicles/{rego}/history). */
export interface HistoryItem {
  kind: "prestart" | "fixed" | "service" | "rego" | "odometer";
  id: number;
  recorded_at: string;
  by: string;
  note: string | null;
  result: CheckResult | null;
  failed: string[];
  odometer_km: number | null;
  done_on: string | null;
  expires_on: string | null;
  check_id: number | null;
}

export interface Driver {
  id: number;
  code: string;
  name: string;
  active: boolean;
}

/** A consignment as the plan shows it, on a run or in the tray. */
export interface Card {
  id: number;
  wms_ref: string;
  customer: string;
  suburb: string | null;
  status: string;
  packages: number;
  cartons: number;
  weight_kg: string;
  volume_m3: string;
}

export interface RunSheet {
  job_id: string;
  status: "pending" | "accepted" | "printed" | "failed";
  printer: string;
  last_error: string | null;
  created_at: string;
  printed_at: string | null;
}

export interface Run {
  id: number;
  run_no: string;
  date: string;
  status: "planned" | "departed" | "done" | "cancelled";
  vehicle: { rego: string; type: string; max_kg: string; max_m3: string; active: boolean; blocked?: Blocked | null };
  driver: { code: string; name: string };
  created_by: string;
  created_at: string;
  departed_at: string | null;
  finished_at?: string | null;
  /** outcome: what the driver recorded there, with the reason one couldn't be delivered */
  stops: { sequence: number; consignment: Card; outcome?: "delivered" | "failed" | null;
           outcome_at?: string | null; reason?: string | null }[];
  load: {
    weight_kg: string; volume_m3: string; max_kg: string; max_m3: string;
    unknown_weight: number; unknown_volume: number; state: "ok" | "near" | "over"; message: string | null;
  };
  run_sheet: RunSheet | null;
  shipping: { pending: number; done: number; failed: number };
  /** the run's pre-start as Start counts it: the latest check of its truck
   * for it that didn't ground it, made since the truck was last grounded
   * and last marked fixed; else null */
  prestart?: { id: number; at: string; result: CheckResult } | null;
  /** while it is on the road: estimates from the stop order and the driver's
   * average time per stop so far on the run */
  estimate?: Estimate | null;
}

export interface Estimate {
  basis: "average" | "default";
  minutes_per_stop: number;
  stops: { consignment_id: number; sequence: number; at: string }[];
  back_at_depot: string;
}

/** A run on the road's latest position (GET /v1/positions). */
export interface TruckAt {
  run_id: number;
  run_no: string;
  vehicle: { rego: string; type: string };
  driver: { code: string; name: string };
  latitude: string;
  longitude: string;
  accuracy_m: string;
  at: string;
  /** when it was last heard from */
  received_at: string;
}

/** Where the map tiles come from (GET /v1/map); null: no map, a list only. */
export interface MapTiles { tile_url: string | null; attribution: string | null }

/** An outside carrier (GET /v1/carriers). */
export interface Carrier {
  id: number;
  name: string;
  account: string | null;
  /** when it usually calls, by the depot's clock: "15:30" */
  pickup_time: string | null;
  adapter: string;
  active: boolean;
  created_at: string;
}

export interface ManifestLine { consignment: Card; con_note: string | null; added_at: string }

/** One carrier's pickup on the carrier bay (GET /v1/manifests). */
export interface Manifest {
  id: number;
  manifest_no: string;
  date: string;
  status: "open" | "collected";
  carrier: Carrier;
  created_by: string;
  created_at: string;
  collected_at: string | null;
  collected_by: string | null;
  lines: ManifestLine[];
  totals: { consignments: number; packages: number; cartons: number; weight_kg: string };
  needs_con_note: number;
  print_job: RunSheet | null;
  shipping: { pending: number; done: number; failed: number };
  /** what Simple WMS had shipped already (back from its run): it kept what it had */
  already_shipped: AlreadyShipped[];
}

export interface AlreadyShipped { consignment_id: number; wms_ref: string; customer: string; carrier: string | null; tracking_no: string | null }

/** A line of what just happened (GET /v1/feed). */
export interface FeedItem {
  kind: "packed" | "delivered" | "failed" | "left" | "finished" | "prestart" | "fixed" | "con_note" | "collected";
  at: string;
  run_id: number | null;
  run_no: string | null;
  rego: string | null;
  driver: string | null;
  sequence: number | null;
  customer: string | null;
  wms_ref: string | null;
  reason: string | null;
  result: CheckResult | null;
  failed: string[];
  by: string | null;
  note: string | null;
  /** con_note and collected: the carrier, the con note, the manifest and how many went */
  carrier?: string | null;
  con_note?: string | null;
  manifest_no?: string | null;
  count?: number | null;
}
