/** Response shapes, as docs/api.md gives them. Decimals are strings. */

export interface Me {
  kind: "driver";
  id: number;
  name: string;
  display_name: string;
  role: string;
  scopes: string[];
}

export interface DriverSessionOut {
  token: string;
  expires_in: number;
  driver: Me;
}

/** Why a truck can't take its run: grounded, or rego or service overdue. */
export interface Blocked { code: string; message: string }

/** One line of the truck's pre-start checklist. */
export interface ChecklistLine { id: number; label: string; critical: boolean }

export type CheckResult = "passed" | "failed" | "grounded";

export interface DriverRun {
  id: number;
  run_no: string;
  date: string;
  status: "planned" | "departed" | "done" | "cancelled";
  vehicle: { rego: string; type: string; odometer_km?: number | null; blocked?: Blocked | null };
  stops: number;
  packages: number;
  delivered: number;
  failed: number;
  /** the latest pre-start of this truck for this run, if done */
  prestart?: { id: number; at: string; result: CheckResult } | null;
  checklist?: ChecklistLine[];
}

export interface PrestartOut {
  message_id: string;
  check_id: number;
  run_id: number;
  run_no: string;
  vehicle: string;
  result: CheckResult;
  failed: string[];
  grounded: boolean;
  odometer_km: number;
  at: string;
}

/** 409 vehicle_changed: the office gave the run another truck while the
 * driver checked the one they were shown. The check is kept against that
 * truck (and may ground it); the run's truck, `vehicle`, needs its own. */
export interface VehicleChanged {
  code: "vehicle_changed";
  detail: string;
  vehicle: string;
  check: PrestartOut;
}

export interface ShipTo {
  name?: string | null;
  address?: string | null;
  suburb?: string | null;
  state?: string | null;
  postcode?: string | null;
  country?: string | null;
  contact?: string | null;
  phone?: string | null;
}

export interface PackageLine {
  package_no: number;
  type: string;
  weight_kg: string | null;
  sscc: string | null;
}

export type Outcome = "delivered" | "failed";

export interface StopDetail {
  sequence: number;
  consignment_id: number;
  wms_ref: string;
  customer: string;
  ship_to: ShipTo;
  packages: PackageLine[];
  outcome: Outcome | null;
  outcome_at: string | null;
}

export interface RunStops {
  id: number;
  run_no: string;
  date: string;
  status: DriverRun["status"];
  vehicle: { rego: string; type: string };
  driver: { code: string; name: string };
  departed_at: string | null;
  finished_at: string | null;
  stops: StopDetail[];
}

export interface StartOut {
  message_id: string;
  run_id: number;
  run_no: string;
  status: DriverRun["status"];
  departed_at: string | null;
}
