import type { QueueItem } from "../lib/queue";
import { currentRun, finished, loadRun, loadTicks, nextStop, outcomes, rememberCurrent, saveRun, saveTicks } from "../lib/run";
import { pruneRuns } from "../lib/run";
import { RUN } from "./samples";

const item = (over: Partial<QueueItem>): QueueItem => ({
  id: "i", message_id: "m", driver_id: 3, driver_name: "Kim", path: "", body: {}, files: [], label: "", run_id: 7, consignment_id: 31,
  outcome: "delivered", created_at: "", status: "queued", attempts: 0, ...over,
});

describe("the run on the phone", () => {
  it("is kept whole on the phone, for the driver who loaded it", () => {
    saveRun(3, RUN);
    expect(loadRun(3, 7)).toEqual(RUN);
    expect(loadRun(3, 8)).toBeNull();
    // another driver signed in on the same phone doesn't see it
    expect(loadRun(4, 7)).toBeNull();
  });

  it("keeps each driver's ticks and current run apart", () => {
    saveTicks(3, 7, 31, { 1: "scan" });
    expect(loadTicks(3, 7, 31)).toEqual({ 1: "scan" });
    expect(loadTicks(4, 7, 31)).toEqual({});
    rememberCurrent(3, 7);
    expect(currentRun(3)).toBe(7);
    expect(currentRun(4)).toBeNull();
  });

  it("counts a stop done the moment the phone has it, sent or not", () => {
    const done = outcomes(RUN, [item({ status: "queued" })]);
    expect([...done]).toEqual([[31, "delivered"]]);
    expect(nextStop(RUN, done)?.consignment_id).toBe(32);
  });

  it("doesn't count a confirmation the office refused", () => {
    expect(outcomes(RUN, [item({ status: "failed" })]).size).toBe(0);
  });

  it("takes the office's record over the phone's", () => {
    const run = { ...RUN, stops: [{ ...RUN.stops[0], outcome: "failed" as const }, RUN.stops[1]] };
    expect(outcomes(run, [item({})]).get(31)).toBe("failed");
  });

  it("is finished when the office says so, or the phone has sent it", () => {
    expect(finished(RUN, [])).toBe(false);
    expect(finished(RUN, [item({ outcome: "finished", consignment_id: null })])).toBe(true);
    expect(finished(RUN, [item({ outcome: "finished", consignment_id: null, status: "failed" })])).toBe(false);
    expect(finished({ ...RUN, status: "done" }, [])).toBe(true);
  });

  describe("tidied away when nothing needs it", () => {
    const keys = () => Object.keys(window.localStorage).sort();
    const DONE = { ...RUN, status: "done" as const };

    it("forgets a finished run and its ticks once nothing for it waits", () => {
      saveRun(3, { ...DONE, id: 5 });
      saveTicks(3, 5, 31, { 1: "tick" });
      saveRun(3, { ...DONE, id: 6 });           // finished, but a confirmation for it still waits
      saveRun(3, RUN);                          // on the road
      saveTicks(3, 7, 31, { 1: "scan" });
      pruneRuns(new Set(["3.6"]), new Date("2026-10-14T23:00:00Z"));
      expect(keys()).toEqual(["tms.driver.run.3.6", "tms.driver.run.3.7", "tms.driver.ticks.3.7.31"]);
    });

    it("forgets a run left from more than a week ago, and copies kept the old way", () => {
      saveRun(3, { ...RUN, id: 4, date: "2026-10-01" });
      window.localStorage.setItem("tms.driver.run.7", JSON.stringify(RUN));
      window.localStorage.setItem("tms.driver.ticks.7.31", "{}");
      window.localStorage.setItem("tms.driver.current", "7");
      window.localStorage.setItem("tms.driver.queue", "[]");
      pruneRuns(new Set(), new Date("2026-10-14T23:00:00Z"));
      expect(keys()).toEqual(["tms.driver.queue"]);
    });

    it("makes room by tidying when the phone is full, and says so if that isn't enough", () => {
      saveRun(3, { ...DONE, id: 5 });
      const real = Storage.prototype.setItem;
      let refusals = 1;
      const spy = vi.spyOn(Storage.prototype, "setItem").mockImplementation(function (this: Storage, k: string, v: string) {
        if (k.startsWith("tms.driver.run.") && refusals-- > 0) throw new DOMException("full", "QuotaExceededError");
        real.call(this, k, v);
      });
      saveRun(3, RUN);
      expect(loadRun(3, 7)).toEqual(RUN);
      expect(loadRun(3, 5)).toBeNull();
      refusals = 9;
      expect(() => saveRun(3, { ...RUN, id: 8 })).toThrow(/couldn't be kept on the phone/);
      spy.mockRestore();
    });
  });
});
