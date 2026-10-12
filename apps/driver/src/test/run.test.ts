import type { QueueItem } from "../lib/queue";
import { currentRun, finished, loadRun, loadTicks, nextStop, outcomes, rememberCurrent, saveRun, saveTicks } from "../lib/run";
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
});
