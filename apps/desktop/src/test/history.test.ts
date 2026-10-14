import { historyLine, proofOf } from "../lib/history";

const row = (kind: string, detail: Record<string, unknown>) => ({ kind, at: "2026-10-14T21:42:00Z", detail });

describe("a consignment's history, in words", () => {
  it("says each step as the Consignments drawer does", () => {
    expect(historyLine(row("packed", { packages: 4 }))).toEqual({ title: "Packed in Simple WMS", more: "4 packages", from: "wms" });
    expect(historyLine(row("assigned", { run: "R-0007", by: "dee" }))).toEqual({ title: "On run R-0007", more: "dee", from: "tms" });
    expect(historyLine(row("unassigned", { run: "R-0007", by: "dee" })).title).toBe("Off run R-0007");
    expect(historyLine(row("departed", { run: "R-0007", vehicle: "TMS041", driver: "kim", by: "dee" })))
      .toEqual({ title: "Departed", more: "TMS041 · kim", from: "tms" });
    expect(historyLine(row("delivered", { packages: 4, signed_name: "Priya", by: "kim" })))
      .toEqual({ title: "Delivered", more: "4 packages · signed by Priya", from: "tms" });
    expect(historyLine(row("packed", { packages: 1 })).more).toBe("1 package");
    expect(historyLine(row("failed", { reason: "wrong_address", by: "kim" })))
      .toEqual({ title: "Not delivered", more: "wrong address", from: "tms" });
    expect(historyLine(row("booked", { carrier: "Valley Freight (sample)", manifest: "M-0003", by: "dee" })))
      .toEqual({ title: "Booked with Valley Freight (sample)", more: "M-0003 · dee", from: "tms" });
    expect(historyLine(row("unbooked", { carrier: "Valley Freight (sample)", by: "dee" })).title)
      .toBe("Off Valley Freight (sample)'s manifest");
    expect(historyLine(row("con_note", { carrier: "Valley Freight (sample)", con_note: "VF-2", was: "VF-1", by: "dee" })))
      .toEqual({ title: "Con note VF-2", more: "Valley Freight (sample) · was VF-1 · dee", from: "tms" });
    expect(historyLine(row("collected", { carrier: "Valley Freight (sample)", con_note: "VF-2", manifest: "M-0003", by: "dee" })))
      .toEqual({ title: "Collected by Valley Freight (sample)", more: "con note VF-2 · M-0003 · dee", from: "tms" });
  });

  it("finds the proof of delivery in the history", () => {
    const h = [row("packed", {}), row("delivered", { signature_file: "s1", photo_file: "p1", signed_name: "Priya" })];
    expect(proofOf(h)).toEqual({ at: "2026-10-14T21:42:00Z", outcome: "delivered", signature: "s1", photo: "p1", signedName: "Priya", reason: null });
    expect(proofOf([row("packed", {})])).toBeNull();
  });
});
