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
}

export interface Vehicle {
  id: number;
  rego: string;
  type: string;
  max_kg: string;
  max_m3: string;
  active: boolean;
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
  vehicle: { rego: string; type: string; max_kg: string; max_m3: string; active: boolean };
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
}
