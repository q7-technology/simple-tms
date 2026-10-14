/** A consignment's history (GET /v1/consignments/{id}), in the words of
 * Consignments.html's timeline, and its proof of delivery. */
import type { HistoryRow } from "../api/types";

const REASONS: Record<string, string> = { closed: "closed", refused: "refused", damaged: "damaged",
                                          wrong_address: "wrong address" };

const text = (v: unknown) => (typeof v === "string" && v ? v : null);
const join = (...parts: (string | null)[]) => parts.filter(Boolean).join(" · ");

/** The title, the small line under it, and whether Simple WMS or Simple TMS did it. */
export function historyLine(h: HistoryRow): { title: string; more: string; from: "wms" | "tms" } {
  const d = h.detail;
  const by = text(d.by);
  switch (h.kind) {
    case "packed": return { title: "Packed in Simple WMS", more: d.packages ? `${d.packages} packages` : "", from: "wms" };
    case "assigned": return { title: `On run ${d.run}`, more: join(by), from: "tms" };
    case "unassigned": return { title: `Off run ${d.run}`, more: join(by), from: "tms" };
    case "departed": return { title: "Departed", more: join(text(d.vehicle), text(d.driver)), from: "tms" };
    case "delivered":
      return { title: "Delivered", more: join(d.packages ? `${d.packages} packages` : null,
                                              text(d.signed_name) ? `signed by ${d.signed_name}` : null), from: "tms" };
    case "failed": return { title: "Not delivered", more: REASONS[String(d.reason)] ?? String(d.reason ?? ""), from: "tms" };
    case "booked": return { title: `Booked with ${d.carrier}`, more: join(text(d.manifest), by), from: "tms" };
    case "unbooked": return { title: `Off ${d.carrier}'s manifest`, more: join(by), from: "tms" };
    case "con_note":
      return { title: `Con note ${d.con_note}`, more: join(text(d.carrier), text(d.was) ? `was ${d.was}` : null, by), from: "tms" };
    case "collected":
      return { title: `Collected by ${d.carrier}`, more: join(`con note ${d.con_note}`, text(d.manifest), by), from: "tms" };
    default: return { title: h.kind, more: join(by), from: "tms" };
  }
}

export interface Proof { at: string; outcome: "delivered" | "failed"; signature: string | null; photo: string | null;
                         signedName: string | null; reason: string | null }

/** The latest delivery, or failed delivery, and its pictures. */
export function proofOf(history: HistoryRow[]): Proof | null {
  const h = [...history].reverse().find((r) => r.kind === "delivered" || r.kind === "failed");
  if (!h) return null;
  return { at: h.at, outcome: h.kind as Proof["outcome"], signature: text(h.detail.signature_file),
           photo: text(h.detail.photo_file), signedName: text(h.detail.signed_name), reason: text(h.detail.reason) };
}
