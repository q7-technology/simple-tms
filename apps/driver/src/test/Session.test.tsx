import { act, waitFor } from "@testing-library/react";
import { json, onThePhone, renderApp, RUN, signedIn, stubApi } from "./harness";

const FAIL = { path: "/v1/runs/7/stops/31/fail", body: { reason: "closed" }, label: "Stop 1 · couldn't deliver · closed",
               run_id: 7, consignment_id: 31, outcome: "failed" as const };

describe("what waits goes by itself", () => {
  beforeEach(() => { signedIn(); onThePhone(); });
  afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

  function stub() {
    return stubApi((method) => (method === "POST" ? json(200, { outcome: "failed" }) : json(200, RUN))).calls;
  }

  it("when the signal comes back", async () => {
    let online = false;
    const calls = stub();
    const { queue } = renderApp("/run/7", { online: () => online });
    await act(async () => { await queue.submit(FAIL); });
    expect(calls.filter((c) => c.method === "POST")).toHaveLength(0);
    online = true;
    act(() => { window.dispatchEvent(new Event("online")); });
    await waitFor(() => expect(queue.pending()).toHaveLength(0));
    expect(calls.filter((c) => c.url === "/v1/runs/7/stops/31/fail")).toHaveLength(1);
  });

  it("every 15 seconds while anything waits", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    let online = false;
    const calls = stub();
    const { queue } = renderApp("/run/7", { online: () => online });
    await act(async () => { await queue.submit(FAIL); });
    online = true;
    await act(async () => { vi.advanceTimersByTime(14_999); });
    expect(calls.filter((c) => c.method === "POST")).toHaveLength(0);
    await act(async () => { vi.advanceTimersByTime(1); });
    await waitFor(() => expect(queue.pending()).toHaveLength(0));
    expect(calls.filter((c) => c.url === "/v1/runs/7/stops/31/fail")).toHaveLength(1);
  });

  it("asks the browser to keep what is on the phone", async () => {
    const persist = vi.fn(async () => true);
    Object.defineProperty(navigator, "storage", { value: { persist }, configurable: true });
    try {
      stubApi(() => json(200, RUN));
      renderApp("/run/7");
      await waitFor(() => expect(persist).toHaveBeenCalled());
    } finally {
      delete (navigator as { storage?: unknown }).storage;
    }
  });
});
