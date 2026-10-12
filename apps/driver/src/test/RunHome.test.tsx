import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { json, onThePhone, renderApp, RUN, signedIn, stubApi } from "./harness";

describe("between stops", () => {
  beforeEach(() => { signedIn(); onThePhone(); });
  afterEach(() => {
    vi.unstubAllGlobals();
    delete (navigator as { permissions?: unknown }).permissions;
  });

  it("works from memory with no signal, and sends what waited once it's back", async () => {
    let online = false;
    const { calls } = stubApi((method) => {
      if (method === "POST") return json(200, { outcome: "failed" });
      return json(200, RUN);
    });
    const { queue } = renderApp("/run/7", { online: () => online });
    await act(async () => {
      await queue.submit({ path: "/v1/runs/7/stops/31/fail", body: { reason: "closed" }, label: "Stop 1 · couldn't deliver · closed",
                           run_id: 7, consignment_id: 31, outcome: "failed" });
    });
    expect(screen.getByText(/1 confirmation waiting/)).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "1 of 2 stops done" })).toBeInTheDocument();
    const next = screen.getByRole("region", { name: "Next stop" });
    expect(within(next).getByText("Two Rivers Vet")).toBeInTheDocument();
    expect(screen.getByText("Stop 1 · couldn't deliver · closed")).toBeInTheDocument();
    expect(screen.getByText(/Waiting since/)).toBeInTheDocument();
    expect(calls.filter((c) => c.method === "POST")).toHaveLength(0);

    online = true;
    await userEvent.setup().click(screen.getByRole("button", { name: "Retry now" }));
    await waitFor(() => expect(screen.getByText(/^Sent /)).toBeInTheDocument());
    expect(calls.filter((c) => c.url === "/v1/runs/7/stops/31/fail")).toHaveLength(1);
  });

  const PNG = () => new Blob([new Uint8Array([0x89, 0x50, 0x4e, 0x47])], { type: "image/png" });
  const waitingRows = () => {
    const list = screen.getByText("Waiting to send").nextElementSibling as HTMLElement;
    return [...list.children].map((r) => r.textContent);
  };

  it("folds a confirmation's pictures into its one row, and counts it once", async () => {
    stubApi(() => json(200, RUN));
    const { queue } = renderApp("/run/7", { online: () => false });
    await act(async () => {
      await queue.submit({ path: "/v1/runs/7/stops/32/fail", body: { reason: "closed" }, label: "Stop 2 · couldn't deliver · closed",
                           run_id: 7, consignment_id: 32, outcome: "failed",
                           pictures: [{ kind: "photo", field: "photo_file", blob: PNG() }] });
    });
    expect(screen.getByText(/^1 confirmation waiting/)).toBeInTheDocument();
    const rows = waitingRows();
    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatch(/^Stop 2 · couldn't deliver · closed · 1 photoWaiting since/);
  });

  it("shows a row for each confirmation waiting, as many as it counts", async () => {
    stubApi(() => json(200, RUN));
    const { queue } = renderApp("/run/7", { online: () => false });
    await act(async () => {
      await queue.submit({ path: "/v1/runs/7/stops/31/deliver", body: {}, label: "Stop 1 · delivered · 2 cartons",
                           run_id: 7, consignment_id: 31, outcome: "delivered",
                           pictures: [{ kind: "signature", field: "signature_file", blob: PNG() },
                                      { kind: "photo", field: "photo_file", blob: PNG() }] });
      await queue.submit({ path: "/v1/runs/7/stops/32/fail", body: { reason: "closed" }, label: "Stop 2 · couldn't deliver · closed",
                           run_id: 7, consignment_id: 32, outcome: "failed" });
    });
    expect(screen.getByText(/^2 confirmations waiting/)).toBeInTheDocument();
    const rows = waitingRows();
    expect(rows).toHaveLength(2);
    expect(rows[0]).toMatch(/^Stop 2 · couldn't deliver · closedWaiting since/);
    expect(rows[1]).toMatch(/^Stop 1 · delivered · 2 cartons · signature and 1 photoWaiting since/);
  });

  it("finishes the run once every stop is done", async () => {
    Object.defineProperty(navigator, "permissions", { configurable: true, value: { query: async () => ({ state: "granted" }) } });
    const done = { ...RUN, stops: RUN.stops.map((s, i) => ({ ...s, outcome: i ? "failed" as const : "delivered" as const })) };
    onThePhone(done);
    const { calls } = stubApi((_method, url) => {
      if (url === "/v1/runs/7/finish") return json(200, { status: "done" });
      return json(200, done);
    });
    renderApp("/run/7");
    expect(await screen.findByText("Every stop is done.")).toBeInTheDocument();
    expect(screen.getByText(/1 delivered, 1 coming back to the depot/)).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("button", { name: "Finish the run" }));
    expect(await screen.findByText("R-0007 is finished.")).toBeInTheDocument();
    await waitFor(() => expect(calls.some((c) => c.url === "/v1/runs/7/finish")).toBe(true));
    expect(screen.getByRole("link", { name: "Back to today's runs" })).toBeInTheDocument();
    // nothing is recorded once the run is over, so the top bar doesn't say it is
    expect(screen.getByText("Location off")).toBeInTheDocument();
  });

  it("tries a confirmation that wasn't taken again, or lets it go", async () => {
    let refuse = true;
    const { calls } = stubApi((method, url) => {
      if (method === "POST" && url === "/v1/runs/7/stops/31/fail") {
        return refuse ? json(409, { code: "run_cancelled", detail: "R-0007 was cancelled" }) : json(200, { outcome: "failed" });
      }
      if (method === "POST") return json(409, { code: "run_cancelled", detail: "R-0007 was cancelled" });
      return json(200, RUN);
    });
    const { queue } = renderApp("/run/7");
    await act(async () => {
      await queue.submit({ path: "/v1/runs/7/stops/31/fail", body: { reason: "closed" }, label: "Stop 1 · couldn't deliver · closed",
                           run_id: 7, consignment_id: 31, outcome: "failed" });
      await queue.submit({ path: "/v1/runs/7/stops/32/fail", body: { reason: "closed" }, label: "Stop 2 · couldn't deliver · closed",
                           run_id: 7, consignment_id: 32, outcome: "failed" });
      await queue.drain();
    });
    expect(await screen.findAllByText("R-0007 was cancelled")).toHaveLength(2);
    const user = userEvent.setup();

    refuse = false;
    await user.click(screen.getAllByRole("button", { name: "Try again" })[1]);
    await waitFor(() => expect(screen.getAllByText(/^Sent /)).toHaveLength(1));
    const ids = calls.filter((c) => c.url === "/v1/runs/7/stops/31/fail").map((c) => (c.body as { message_id: string }).message_id);
    expect(ids).toHaveLength(2);
    expect(ids[0]).toBe(ids[1]);

    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
    await user.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.getByText("Stop 2 · couldn't deliver · closed")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Dismiss" }));
    await waitFor(() => expect(screen.queryByText("Stop 2 · couldn't deliver · closed")).not.toBeInTheDocument());
    expect(confirm).toHaveBeenCalledTimes(2);
    expect(confirm.mock.calls[0][0]).toMatch(/won't be sent/);
    confirm.mockRestore();
  });
});
