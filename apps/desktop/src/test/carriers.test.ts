import type { Consignment, FeedItem } from "../api/types";
import { bayStats, conNoteProblem, nextNeedingConNote, normalConNote, pickupClock, pickupLine,
         whereNow } from "../lib/carriers";
import { feedLine } from "../lib/live";
import { line, manifest, PLAINS, RIDGELINE, VALLEY } from "./bay";

const RX = manifest(1, RIDGELINE, [line(26, "Mill Lane Florist", null), line(33, "Hilltop Joinery", "RX-77310042", 5, "72")]);
const VF = manifest(2, VALLEY, [line(24, "Paddock Produce", "VF-00418821")]);
const PP = manifest(3, PLAINS, [line(20, "Corner Hardware", "PP-1001")],
                    { status: "collected", collected_at: new Date(2026, 9, 15, 10, 15).toISOString(), collected_by: "dee" });

describe("the carrier bay", () => {
  it("reads a con note as the carrier prints it, and says what is wrong with one", () => {
    expect(normalConNote("  rx-77310042 ")).toBe("RX-77310042");
    expect(conNoteProblem("RX-77310042")).toBeNull();
    for (const bad of ["RX 7731", "RX", "-RX7731", "RX_7731", "R".repeat(41)]) {
      expect(conNoteProblem(normalConNote(bad))).toBe("A con note is 4 to 40 letters, digits and dashes, as on the carrier's label.");
    }
  });

  it("counts what needs a con note, what waits for pickup and what went today", () => {
    expect(bayStats([RX, VF, PP])).toEqual({
      needs: 1, firstNeeding: { manifest: RX, consignment: RX.lines[0].consignment },
      waiting: 3, carriers: 2, collected: 1, lastCollected: PP,
    });
    expect(bayStats([])).toEqual({ needs: 0, firstNeeding: null, waiting: 0, carriers: 0, collected: 0, lastCollected: null });
  });

  it("goes on to the next one with no con note", () => {
    const more = manifest(4, VALLEY, [line(40, "Old Mill Books", null)]);
    expect(nextNeedingConNote([RX, more])?.consignment.id).toBe(26);
    expect(nextNeedingConNote([RX, more], 26)?.consignment.id).toBe(40);
    expect(nextNeedingConNote([VF])).toBeNull();
  });

  it("says when each carrier calls and what it takes", () => {
    expect(pickupClock("15:30")).toBe("3:30 pm");
    expect(pickupClock("09:05")).toBe("9:05 am");
    expect(pickupLine(RX)).toBe("Pickup about 3:30 pm · 2 consignments · 7 cartons · 96 kg");
    expect(pickupLine({ ...VF, carrier: { ...VF.carrier, pickup_time: null } })).toBe("1 consignment · 2 cartons · 96 kg");
    expect(pickupLine({ ...PP, print_job: { job_id: "j", status: "printed", printer: "office", last_error: null, created_at: "", printed_at: "" },
                        shipping: { pending: 0, done: 1, failed: 0 } }))
      .toBe("Collected 10:15 am · manifest M-0003 printed by Platen · marked shipped in Simple WMS");
    expect(pickupLine({ ...PP, shipping: { pending: 1, done: 0, failed: 0 } }))
      .toBe("Collected 10:15 am · manifest M-0003 · telling Simple WMS");
  });
});

describe("where a consignment is now", () => {
  const c = (status: string, over: Partial<Consignment> = {}): Consignment => ({
    id: 1, wms_ref: "0080034519", warehouse: "BAL-WH01", owner: "DEFAULT", customer: "Station Deli", ship_to: {},
    required_by: null, status, created_at: "", weight_kg: "1", volume_m3: "1", packages: [], run: null, carrier: null, ...over,
  });
  const rx = { id: 1, name: "Ridgeline Express (sample)", con_note: null, manifest_id: 1, manifest_no: "M-0001", collected_at: null };

  it("in words, with the colour the design gives it", () => {
    expect(whereNow(c("packed"))).toEqual({ text: "To plan", tone: "amber" });
    expect(whereNow(c("failed"))).toEqual({ text: "Not delivered", tone: "red" });
    expect(whereNow(c("assigned", { run: { id: 8, run_no: "R-0008", driver: "Raj", sequence: 2 } })))
      .toEqual({ text: "On run R-0008", tone: "blue" });
    expect(whereNow(c("departed", { run: { id: 7, run_no: "R-0007", driver: "Kim", sequence: 5 } })))
      .toEqual({ text: "On run R-0007", tone: "green" });
    expect(whereNow(c("delivered"))).toEqual({ text: "Delivered", tone: "green" });
    expect(whereNow(c("booked", { carrier: rx }))).toEqual({ text: "Needs con note", tone: "amber" });
    expect(whereNow(c("booked", { carrier: { ...rx, con_note: "RX-1" } }))).toEqual({ text: "With carrier", tone: "grey" });
    expect(whereNow(c("collected", { carrier: { ...rx, con_note: "RX-1" } }))).toEqual({ text: "Collected", tone: "grey" });
  });
});

describe("the feed's carrier lines", () => {
  const item = (over: Partial<FeedItem>): FeedItem => ({
    kind: "con_note", at: "", run_id: null, run_no: null, rego: null, driver: null, sequence: null, customer: "Hilltop Joinery",
    wms_ref: "0080034519", reason: null, result: null, failed: [], by: "dee", note: null, carrier: "Ridgeline Express (sample)",
    con_note: "RX-77310042", manifest_no: null, count: null, ...over,
  });

  it("words a con note in and a carrier collecting", () => {
    expect(feedLine(item({}))).toEqual({ text: "Con note RX-77310042 for Hilltop Joinery · Ridgeline Express (sample)", tone: "amber" });
    expect(feedLine(item({ kind: "collected", manifest_no: "M-0003", count: 2 })))
      .toEqual({ text: "Ridgeline Express (sample) collected 2 consignments · M-0003", tone: "green" });
    expect(feedLine(item({ kind: "collected", manifest_no: "M-0004", count: 1 })).text)
      .toBe("Ridgeline Express (sample) collected 1 consignment · M-0004");
  });
});
