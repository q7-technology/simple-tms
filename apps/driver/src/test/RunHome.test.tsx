import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { json, onThePhone, renderApp, RUN, signedIn, stubApi } from "./harness";

describe("between stops", () => {
  beforeEach(() => { signedIn(); onThePhone(); });
  afterEach(() => vi.unstubAllGlobals());

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

  it("finishes the run once every stop is done", async () => {
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
  });
});
