import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { loadRun } from "../lib/run";
import { json, renderApp, RUN, signedIn, stubApi } from "./harness";

const TODAY = { id: 7, run_no: "R-0007", date: "2026-10-14", status: "planned",
                vehicle: { rego: "TMS041", type: "rigid" }, stops: 2, packages: 4, delivered: 0, failed: 0 };

describe("today's run", () => {
  beforeEach(signedIn);
  afterEach(() => vi.unstubAllGlobals());

  it("loads the whole run onto the phone and starts it", async () => {
    let started = false;
    const { calls } = stubApi((_method, url) => {
      if (url.startsWith("/v1/driver/runs?date=")) return json(200, [TODAY]);
      if (url === "/v1/runs/7/stops") return json(200, started ? RUN : { ...RUN, status: "planned", departed_at: null });
      if (url === "/v1/runs/7/start") {
        started = true;
        return json(200, { message_id: "m", run_id: 7, run_no: "R-0007", status: "departed", departed_at: "2026-10-14T21:10:00Z" });
      }
      return null;
    });
    renderApp("/");
    expect(await screen.findByRole("radio", { name: /TMS041/ })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByText("2 stops · 4 packages")).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("button", { name: "Start run" }));
    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("/run/7"));
    const start = calls.find((c) => c.url === "/v1/runs/7/start")!;
    expect(start.body).toEqual({ message_id: expect.stringMatching(/^[0-9a-f-]{36}$/) });
    expect(loadRun(7)?.status).toBe("departed");
    expect(loadRun(7)?.stops).toHaveLength(2);
    expect(await screen.findByRole("link", { name: /Start stop 1/ })).toBeInTheDocument();
  });

  it("says starting needs signal, and sends the same start again after", async () => {
    let tries = 0;
    const { calls } = stubApi((_method, url) => {
      if (url.startsWith("/v1/driver/runs?date=")) return json(200, [TODAY]);
      if (url === "/v1/runs/7/stops") return json(200, { ...RUN, status: "planned" });
      if (url === "/v1/runs/7/start") {
        tries += 1;
        if (tries === 1) throw new TypeError("Failed to fetch");
        return json(200, { message_id: "m", run_id: 7, run_no: "R-0007", status: "departed", departed_at: null });
      }
      return null;
    });
    renderApp("/");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Start run" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("No signal. Starting a run needs a connection");
    await user.click(screen.getByRole("button", { name: "Start run" }));
    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("/run/7"));
    const starts = calls.filter((c) => c.url === "/v1/runs/7/start").map((c) => (c.body as { message_id: string }).message_id);
    expect(starts).toHaveLength(2);
    expect(starts[0]).toBe(starts[1]);
  });

  it("says when there is no run today", async () => {
    stubApi((_method, url) => (url.startsWith("/v1/driver/runs") ? json(200, []) : null));
    renderApp("/");
    expect(await screen.findByText("No run for you today.")).toBeInTheDocument();
  });
});
