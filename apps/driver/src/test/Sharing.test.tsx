import { cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { dayLabel, isoDay } from "../lib/time";
import { json, onThePhone, renderApp, RUN, signedIn, stubApi } from "./harness";

/** A phone that knows where it is (or won't say), counting what watches it. */
function phone(allowed = true) {
  const watching = { started: 0, cleared: 0 };
  Object.defineProperty(navigator, "geolocation", { configurable: true, value: {
    watchPosition: (ok: PositionCallback, no: PositionErrorCallback) => {
      watching.started += 1;
      setTimeout(() => (allowed
        ? ok({ coords: { latitude: -37.5622, longitude: 143.8503, accuracy: 9 }, timestamp: Date.now() } as GeolocationPosition)
        : no({ code: 1 } as GeolocationPositionError)), 0);
      return 1;
    },
    clearWatch: () => { watching.cleared += 1; },
    getCurrentPosition: () => undefined,
  } });
  return watching;
}

describe("the phone sharing where it is during the run", () => {
  beforeEach(() => { signedIn(); onThePhone(); });
  afterEach(() => {
    vi.unstubAllGlobals();
    delete (navigator as { geolocation?: unknown }).geolocation;
  });

  it("says it is sharing, and does, from one stop's screen to the next", async () => {
    const watching = phone();
    const { calls } = stubApi((method, url) => (method === "POST" ? new Response(null, { status: 204 }) : url === "/v1/runs/7/stops" ? json(200, RUN) : null));
    renderApp("/run/7");
    expect(await screen.findByText("Sharing location")).toBeInTheDocument();
    expect(screen.getByText(/shares where it is with the office about every 30 seconds/)).toBeInTheDocument();
    await waitFor(() => expect(calls.filter((c) => c.url === "/v1/runs/7/position")).toHaveLength(1));
    expect(calls.find((c) => c.url === "/v1/runs/7/position")!.body).toMatchObject({ latitude: "-37.562200", accuracy_m: "9.0" });
    await userEvent.setup().click(screen.getByRole("link", { name: /Start stop 1/ }));
    expect(await screen.findByText("Sharing location")).toBeInTheDocument();
    // the same watch carries on across the run's screens
    expect(watching).toEqual({ started: 1, cleared: 0 });
    cleanup();   // signed out, or the app closed
    expect(watching.cleared).toBe(1);
  });

  it("shares nothing once the run is finished", async () => {
    const watching = phone();
    const done = { ...RUN, status: "done" as const, stops: RUN.stops.map((s) => ({ ...s, outcome: "delivered" as const })) };
    onThePhone(done);
    const { calls } = stubApi(() => json(200, done));
    renderApp("/run/7");
    expect(await screen.findByText("R-0007 is finished.")).toBeInTheDocument();
    expect(screen.getByText("Location off")).toBeInTheDocument();
    expect(watching.started).toBe(0);
    expect(calls.some((c) => c.url === "/v1/runs/7/position")).toBe(false);
  });

  it("says plainly when the driver hasn't let it", async () => {
    phone(false);
    stubApi(() => json(200, RUN));
    renderApp("/run/7");
    expect(await screen.findByText(/Location is off for this app/)).toBeInTheDocument();
    expect(screen.getByText("Location off")).toBeInTheDocument();
  });

  it("tells the driver plainly when the office has finished the run, and stops sharing", async () => {
    const watching = phone();
    const done = { ...RUN, status: "done" as const, stops: RUN.stops.map((s) => ({ ...s, outcome: "delivered" as const })) };
    const { calls } = stubApi((method, url) => (method === "POST" ? new Response(null, { status: 204 }) : url === "/v1/runs/7/stops" ? json(200, done) : null));
    renderApp("/run/7");
    expect(await screen.findByText("The office finished R-0007.")).toBeInTheDocument();
    expect(screen.getByText("Location off")).toBeInTheDocument();
    expect(screen.getByText("R-0007 is finished.")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Start stop/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Next stop" })).not.toBeInTheDocument();
    await waitFor(() => expect(watching.cleared).toBe(watching.started));
    expect(calls.filter((c) => c.url === "/v1/runs/7/stops")).toHaveLength(1);
  });

  it("looks again when the office says the run is done, and says so", async () => {
    phone();
    const done = { ...RUN, status: "done" as const, stops: RUN.stops.map((s) => ({ ...s, outcome: "delivered" as const })) };
    let asked = 0;
    const { calls } = stubApi((method, url) => {
      if (method === "POST" && url === "/v1/runs/7/position") {
        return json(409, { code: "run_done", detail: "R-0007 is finished, so where it is isn't shared" });
      }
      if (url === "/v1/runs/7/stops") return json(200, asked++ === 0 ? RUN : done);
      return null;
    });
    renderApp("/run/7");
    expect(await screen.findByText("The office finished R-0007.")).toBeInTheDocument();
    expect(screen.getByText("Location off")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Start stop/ })).not.toBeInTheDocument();
    expect(calls.filter((c) => c.url === "/v1/runs/7/position")).toHaveLength(1);
  });

  it("shows it as finished from the phone's own copy when the office can't be asked", async () => {
    phone();
    let asked = 0;
    stubApi((method, url) => {
      if (method === "POST" && url === "/v1/runs/7/position") {
        return json(409, { code: "run_done", detail: "R-0007 is finished, so where it is isn't shared" });
      }
      if (url === "/v1/runs/7/stops") {
        // the first answer, then no signal
        if (asked++ > 0) throw new TypeError("Failed to fetch");
        return json(200, RUN);
      }
      return null;
    });
    renderApp("/run/7");
    expect(await screen.findByText("The office finished R-0007.")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Start stop/ })).not.toBeInTheDocument();
  });

  it("shows the stop screens the run as the office has it now", async () => {
    phone();
    const moved = { ...RUN, stops: [RUN.stops[0], { ...RUN.stops[1], customer: "Ridge Auto Workshop" }] };
    stubApi((method, url) => (method === "POST" ? new Response(null, { status: 204 }) : url === "/v1/runs/7/stops" ? json(200, moved) : null));
    renderApp("/run/7/stop/32");
    expect(await screen.findByText("Ridge Auto Workshop")).toBeInTheDocument();
  });

  it("shares nothing for a run left open from an earlier day, and says why", async () => {
    const watching = phone();
    const yesterday = new Date(Date.now() - 86_400_000);
    const old = { ...RUN, date: isoDay(yesterday) };
    onThePhone(old);
    const { calls } = stubApi((_m, url) => (url === "/v1/runs/7/stops" ? json(200, old) : null));
    renderApp("/run/7");
    expect(await screen.findByText(
      `R-0007 is from ${dayLabel(yesterday)} and was never finished, so this phone isn't sharing where it is. `
      + "Finish it once every stop is done.")).toBeInTheDocument();
    expect(screen.getByText("Location off")).toBeInTheDocument();
    // the driver can still carry on to finish it
    expect(screen.getByRole("link", { name: /Start stop 1/ })).toBeInTheDocument();
    expect(watching.started).toBe(0);
    expect(calls.some((c) => c.url === "/v1/runs/7/position")).toBe(false);
  });

  it("stops when the API says the run was left open", async () => {
    const watching = phone();
    const { calls } = stubApi((method, url) => {
      if (method === "POST") return json(409, { code: "run_stale", detail: "R-0007 was for 13 Oct 2026 and was left open" });
      return url === "/v1/runs/7/stops" ? json(200, RUN) : null;
    });
    renderApp("/run/7");
    await waitFor(() => expect(calls.filter((c) => c.url === "/v1/runs/7/position")).toHaveLength(1));
    await waitFor(() => expect(screen.getByText("Location off")).toBeInTheDocument());
    expect(watching.cleared).toBe(1);
  });

  it("says it is looking for GPS while the phone has no fix", async () => {
    Object.defineProperty(navigator, "geolocation", { configurable: true, value: {
      watchPosition: (_ok: PositionCallback, no: PositionErrorCallback) => {
        setTimeout(() => no({ code: 3 } as GeolocationPositionError), 0);
        return 1;
      },
      clearWatch: () => undefined,
      getCurrentPosition: () => undefined,
    } });
    stubApi((_m, url) => (url === "/v1/runs/7/stops" ? json(200, RUN) : null));
    renderApp("/run/7");
    expect(await screen.findByText("Looking for GPS")).toBeInTheDocument();
  });
});
