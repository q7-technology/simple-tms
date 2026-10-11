import type { QueueItem } from "../lib/queue";
import { finished, loadRun, nextStop, outcomes, saveRun } from "../lib/run";
import { RUN } from "./samples";

const item = (over: Partial<QueueItem>): QueueItem => ({
  id: "i", message_id: "m", path: "", body: {}, files: [], label: "", run_id: 7, consignment_id: 31,
  outcome: "delivered", created_at: "", status: "queued", attempts: 0, ...over,
});

describe("the run on the phone", () => {
  it("is kept whole on the phone", () => {
    saveRun(RUN);
    expect(loadRun(7)).toEqual(RUN);
    expect(loadRun(8)).toBeNull();
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
