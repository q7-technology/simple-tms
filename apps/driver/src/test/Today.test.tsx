import { act, cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { loadRun } from "../lib/run";
import { json, KIM, MEL, onThePhone, renderApp, RUN, signedIn, stubApi } from "./harness";

const TODAY = { id: 7, run_no: "R-0007", date: "2026-10-14", status: "planned",
                vehicle: { rego: "TMS041", type: "rigid" }, stops: 2, packages: 4, delivered: 0, failed: 0 };

describe("today's run", () => {
  beforeEach(() => signedIn());
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
    expect(loadRun(3, 7)?.status).toBe("departed");
    expect(loadRun(3, 7)?.stops).toHaveLength(2);
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

  it("keeps the run as the office has it once started", async () => {
    let started = false;
    // the phone thinks it has no signal, so the run page doesn't load the run itself
    Object.defineProperty(navigator, "onLine", { value: false, configurable: true });
    const { calls } = stubApi((_method, url) => {
      if (url.startsWith("/v1/driver/runs?date=")) return json(200, [TODAY]);
      if (url === "/v1/runs/7/stops") {
        if (!started) return json(200, { ...RUN, status: "planned", departed_at: null });
        // the office moved a stop while the driver was starting
        return json(200, { ...RUN, stops: [RUN.stops[1], RUN.stops[0]] });
      }
      if (url === "/v1/runs/7/start") {
        started = true;
        return json(200, { message_id: "m", run_id: 7, run_no: "R-0007", status: "departed", departed_at: "2026-10-14T21:10:00Z" });
      }
      return null;
    });
    try {
      renderApp("/");
      await userEvent.setup().click(await screen.findByRole("button", { name: "Start run" }));
      await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("/run/7"));
    } finally {
      delete (navigator as { onLine?: boolean }).onLine;
    }
    expect(calls.filter((c) => c.url === "/v1/runs/7/stops")).toHaveLength(2);
    expect(loadRun(3, 7)?.stops.map((s) => s.consignment_id)).toEqual([32, 31]);
  });

  it("says when there is no run today", async () => {
    stubApi((_method, url) => (url.startsWith("/v1/driver/runs") ? json(200, []) : null));
    renderApp("/");
    expect(await screen.findByText("No run for you today.")).toBeInTheDocument();
  });

  describe("a phone two drivers use", () => {
    const FAIL = { path: "/v1/runs/7/stops/31/fail", body: { reason: "closed" }, label: "Stop 1 · couldn't deliver · closed",
                   run_id: 7, consignment_id: 31, outcome: "failed" as const };

    it("keeps Kim's confirmations for Kim while Mel is signed in, and sends them when Kim is back", async () => {
      // Kim, with no signal, at stop 1
      let online = false;
      const { calls } = stubApi((method, url) => {
        if (url.startsWith("/v1/driver/runs?date=")) return json(200, []);
        if (method === "POST") return json(200, { outcome: "failed" });
        return json(200, RUN);
      });
      onThePhone();
      const kim = renderApp("/run/7", { online: () => online });
      await act(async () => { await kim.queue.submit(FAIL); });
      await act(async () => { await kim.queue.submit({ ...FAIL, consignment_id: 32, path: "/v1/runs/7/stops/32/fail" }); });
      cleanup();

      // Mel signs in on the phone, in range
      online = true;
      signedIn(MEL);
      const mel = renderApp("/", { online: () => online, queue: kim.queue });
      expect(await screen.findByText(
        "2 confirmations from Kim are waiting on this phone. They go when Kim signs in.")).toBeInTheDocument();
      await act(async () => { await mel.queue.drain(); });
      expect(calls.filter((c) => c.method === "POST")).toHaveLength(0);
      cleanup();

      // Kim again
      signedIn(KIM);
      renderApp("/", { online: () => online, queue: kim.queue });
      await waitFor(() => expect(calls.filter((c) => c.method === "POST")).toHaveLength(2));
      expect(screen.queryByText(/from Kim are waiting/)).not.toBeInTheDocument();
    });

    it("doesn't show Mel the run Kim left on the phone", async () => {
      onThePhone();
      window.localStorage.setItem("tms.driver.current.3", "7");
      signedIn(MEL);
      stubApi(() => { throw new TypeError("Failed to fetch"); });
      renderApp("/run/7", { online: () => false });
      await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent(/^\/$/));
      expect(await screen.findByRole("alert")).toHaveTextContent("No signal");
      expect(screen.queryByRole("link", { name: /from memory/ })).not.toBeInTheDocument();
    });
  });
});
