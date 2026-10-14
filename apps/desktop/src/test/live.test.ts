import type { FeedItem, Run } from "../api/types";
import { about, ago, DEPOT_GATE, feedLine, heard, loadLeft, spot, toScreen } from "../lib/live";

const local = (h: number, m: number, s = 0) => new Date(2026, 9, 14, h, m, s);

describe("how the map says where and when", () => {
  it("rounds an estimate to five minutes and says it is one", () => {
    expect(about(local(11, 38).toISOString())).toBe("about 11:40 am");
    expect(about(local(14, 12, 40).toISOString())).toBe("about 2:15 pm");
    expect(about(local(9, 2).toISOString())).toBe("about 9:00 am");
  });

  it("says when a truck was last heard from, and when that is a while", () => {
    const now = local(11, 18);
    expect(heard(local(11, 17, 30).toISOString(), now)).toEqual({ text: "30 s ago", stale: false });
    expect(heard(local(11, 14).toISOString(), now)).toEqual({ text: "4 min ago", stale: false });
    expect(heard(local(11, 6).toISOString(), now)).toEqual({ text: "12 min ago", stale: true });
    expect(heard(local(8, 50).toISOString(), now)).toEqual({ text: "2 h ago", stale: true });
    // the desktop's clock a little behind the server's
    expect(heard(local(11, 18, 5).toISOString(), now).text).toBe("just now");
    expect(ago(local(11, 16).toISOString(), now)).toBe("2 min");
    expect(ago(local(7, 10).toISOString(), now)).toBe("4 h");
    expect(ago(local(11, 17, 40).toISOString(), now)).toBe("now");
  });

  it("weighs what is still on the truck: all but what was delivered", () => {
    const card = (weight_kg: string, volume_m3: string) =>
      ({ id: 1, wms_ref: "", customer: "", suburb: null, status: "", packages: 1, cartons: 1, weight_kg, volume_m3 });
    const run = { stops: [
      { sequence: 1, consignment: card("1200", "4.5"), outcome: "delivered" },
      { sequence: 2, consignment: card("940", "5.3"), outcome: "failed" },
      { sequence: 3, consignment: card("1200", "4.5"), outcome: null },
    ] } as unknown as Run;
    expect(loadLeft(run)).toBe("2,140 kg · 9.8 m³");
  });

  it("puts a run's truck along its own road on the drawn map, further as stops are done", () => {
    expect(toScreen([420, 357])).toEqual(DEPOT_GATE);
    const at = (done: number) => spot(0, done, 4);
    expect(at(0)).not.toEqual(at(2));
    // the first route runs the loop round the bottom block and back to the gate
    expect(spot(0, 4, 4)).not.toEqual(DEPOT_GATE);
    // another run takes another road
    expect(spot(1, 1, 4)).not.toEqual(spot(0, 1, 4));
    // a sixth run shares the first one's road, a little to one side
    expect(spot(5, 1, 4)).not.toEqual(spot(0, 1, 4));
  });
});

describe("what just happened, in words", () => {
  const item = (over: Partial<FeedItem>): FeedItem => ({
    kind: "delivered", at: "2026-10-14T00:31:07Z", run_id: 7, run_no: "R-0007", rego: "TMS041", driver: "Kim",
    sequence: 7, customer: "Main Street Chemist", wms_ref: "0080034521", reason: null, result: null, failed: [],
    by: null, note: null, ...over });

  it("says each kind as the design does", () => {
    expect(feedLine(item({}))).toEqual({ text: "Kim delivered stop 7 · Main Street Chemist", tone: "green" });
    expect(feedLine(item({ kind: "failed", reason: "wrong_address" }))).toEqual(
      { text: "Kim couldn't deliver stop 7 · Main Street Chemist · wrong address", tone: "red" });
    expect(feedLine(item({ kind: "packed", run_id: null, run_no: null, driver: null }))).toEqual(
      { text: "0080034521 packed in Simple WMS · Main Street Chemist", tone: "brown" });
    expect(feedLine(item({ kind: "left" })).text).toBe("Kim left on R-0007 in TMS041");
    expect(feedLine(item({ kind: "finished" })).text).toBe("Kim finished R-0007");
    expect(feedLine(item({ kind: "prestart", result: "passed", driver: "Mel", rego: "TMS052" }))).toEqual(
      { text: "Mel passed the pre-start on TMS052", tone: "violet" });
    expect(feedLine(item({ kind: "prestart", result: "failed", failed: ["Mirrors"] }))).toEqual(
      { text: "Kim's pre-start on TMS041: Mirrors failed", tone: "amber" });
    expect(feedLine(item({ kind: "prestart", result: "grounded", rego: "TMS027", failed: ["Lights", "Brakes"] }))).toEqual(
      { text: "TMS027 grounded · Lights and Brakes failed", tone: "red" });
    expect(feedLine(item({ kind: "fixed", rego: "TMS027", by: "leighton" }))).toEqual(
      { text: "TMS027 marked fixed by leighton", tone: "green" });
    // a check sent with a key names who sent it
    expect(feedLine(item({ kind: "prestart", result: "passed", driver: null, by: "tracker" })).text)
      .toBe("tracker passed the pre-start on TMS041");
  });
});
