/** Reading a carton label: its SSCC (as GS1-128 gives it, with or without the
 * (00) in front), the package number on its own, or "<delivery>-<n>". The
 * whole run is on the phone, so a carton for another stop is caught here. */
import type { StopDetail } from "../api/types";

export type ScanMatch =
  | { kind: "here"; package_no: number }
  | { kind: "other"; stop: StopDetail; package_no: number }
  | { kind: "unknown" };

interface Label { sscc?: string; ref?: string; packageNo?: number }

export function readLabel(raw: string): Label {
  // a scanner's symbology prefix (]C1), GS1's group separator, brackets and spaces
  const code = raw.trim().replace(/^\][A-Za-z]\d/, "").replace(/[\u001d()\s]/g, "");
  if (/^\d+$/.test(code)) {
    if (code.length === 20 && code.startsWith("00")) return { sscc: code.slice(2) };
    if (code.length === 18) return { sscc: code };
    if (code.length <= 4) return { packageNo: Number(code) };
  }
  const m = /^(.+?)[-/](\d{1,4})$/.exec(raw.trim());
  if (m) return { ref: m[1].trim(), packageNo: Number(m[2]) };
  return {};
}

export function matchScan(raw: string, stops: StopDetail[], current: StopDetail): ScanMatch {
  const label = readLabel(raw);
  const others = stops.filter((s) => s.consignment_id !== current.consignment_id);
  if (label.sscc) {
    for (const stop of [current, ...others]) {
      const p = stop.packages.find((x) => x.sscc === label.sscc);
      if (p) return stop === current ? { kind: "here", package_no: p.package_no }
        : { kind: "other", stop, package_no: p.package_no };
    }
    return { kind: "unknown" };
  }
  if (label.packageNo !== undefined) {
    const stop = label.ref ? [current, ...others].find((s) => s.wms_ref === label.ref) : current;
    const p = stop?.packages.find((x) => x.package_no === label.packageNo);
    if (stop && p) return stop === current ? { kind: "here", package_no: p.package_no }
      : { kind: "other", stop, package_no: p.package_no };
  }
  return { kind: "unknown" };
}
