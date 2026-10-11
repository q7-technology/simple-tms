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

export interface DriverRun {
  id: number;
  run_no: string;
  date: string;
  status: "planned" | "departed" | "done" | "cancelled";
  vehicle: { rego: string; type: string };
  stops: number;
  packages: number;
  delivered: number;
  failed: number;
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
