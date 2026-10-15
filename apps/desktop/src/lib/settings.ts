/** The Settings drawer's words for each TMS_* setting, and what a save
 * sends. Which may be changed, and how, is the API's (GET /v1/settings). */
import type { SettingRow } from "../api/types";

export interface Copy { label: string; hint?: string; numeric?: boolean }

export const COPY: Record<string, Copy> = {
  wms_url: { label: "Simple WMS address", hint: "Where Simple WMS's API is, e.g. https://wms.example.com. Empty: Simple TMS works without it." },
  wms_api_key: { label: "Simple WMS key", hint: "A WMS API key with tasks:read and tasks:write." },
  wms_webhook_secret: { label: "Webhook secret", hint: "The Simple TMS subscriber's secret in Simple WMS, 16 to 128 characters." },
  wms_timeout_seconds: { label: "Wait for Simple WMS (seconds)", numeric: true, hint: "Up to 60. Keep it under Simple WMS's own 10 s wait." },
  platen_url: { label: "Platen job intake", hint: "e.g. https://platen.example.com/intake/jobs. Empty: nothing is printed." },
  platen_key: { label: "Platen key", hint: "An operator key from Platen's People > Keys." },
  run_sheet_printer: { label: "Run sheet printer", hint: "A Platen printer's id or name." },
  manifest_printer: { label: "Carrier manifest printer", hint: "Empty: the run sheet printer." },
  position_retention_days: { label: "Keep positions (days)", numeric: true,
                             hint: "Positions older than this are deleted within the hour, and can't be brought back." },
  eta_minutes_per_stop: { label: "Minutes a stop takes, to start with", numeric: true, hint: "1 to 600: the ETA's guess until a driver has done a stop." },
  worker_poll_seconds: { label: "Look for work every (seconds)", numeric: true, hint: "1 to 300, when there was nothing to send." },
  worker_http_timeout_seconds: { label: "Wait for an answer (seconds)", numeric: true, hint: "Up to 120, for each call to Simple WMS or Platen." },
  database_url: { label: "Database" },
  secret_key: { label: "Session key" },
  pod_dir: { label: "Proof of delivery files" },
  map_tile_url: { label: "Map tiles" },
  map_tile_origin: { label: "Map tiles' origin" },
  map_attribution: { label: "Map credit" },
};

export const GROUPS: { title: string; names: string[] }[] = [
  { title: "Simple WMS", names: ["wms_url", "wms_api_key", "wms_webhook_secret", "wms_timeout_seconds"] },
  { title: "Printing through Platen", names: ["platen_url", "platen_key", "run_sheet_printer", "manifest_printer"] },
  { title: "Live location and ETAs", names: ["position_retention_days", "eta_minutes_per_stop"] },
  { title: "The worker", names: ["worker_poll_seconds", "worker_http_timeout_seconds"] },
];

/** An address and the key sent to it, a pair as the API keeps them: putting
 * the address back to .env's puts a key given here back too. */
export const KEY_FOR: Record<string, string> = { wms_url: "wms_api_key", platen_url: "platen_key" };

/** What a setting shows in its box: its value in force, or nothing for a secret. */
export const shown = (s: SettingRow) => (s.kind === "secret" || s.value === null ? "" : String(s.value));

/** The changes to send for a group: an on-screen setting typed differently
 * from what is in force, and a secret typed at all. */
export function changesFor(rows: SettingRow[], drafts: Record<string, string>): Record<string, string> {
  const out: Record<string, string> = {};
  for (const s of rows) {
    const typed = drafts[s.name];
    if (typed === undefined) continue;
    if (s.kind === "secret" ? typed !== "" : typed.trim() !== shown(s)) out[s.name] = typed.trim();
  }
  return out;
}
