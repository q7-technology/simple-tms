import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { json, path, renderApp, stubApi } from "./harness";

describe("the map", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("shows the day's numbers and opens drawers from the bar", async () => {
    const stop = (outcome: string | null) => ({ sequence: 1, consignment: {}, outcome });
    const { calls } = stubApi((_m, url) => {
      if (path(url) === "/v1/runs") {
        return json(200, { items: [{ stops: [stop("delivered"), stop("failed"), stop(null)] },
                                   { stops: [stop("delivered"), stop("delivered")] }], total: 2 });
      }
      if (path(url) !== "/v1/consignments") return null;
      const status = new URLSearchParams(url.split("?")[1]).get("status");
      return json(200, { items: [], total: { "packed,failed": 14, departed: 21 }[status ?? ""] ?? 0 });
    });
    renderApp("/");
    const nav = await screen.findByRole("navigation", { name: "Main drawers" });
    await waitFor(() => expect(nav).toHaveTextContent("Plan runs14"));
    expect(screen.getByText("21")).toBeInTheDocument();
    // delivered today: the stops the drivers have delivered on today's runs
    expect(screen.getByText("Delivered").nextSibling).toHaveTextContent("3");
    // every run of the day: on the road, loading, and back
    expect(calls.find((c) => path(c.url) === "/v1/runs")?.url).not.toMatch(/status=/);
    await userEvent.setup().click(screen.getByRole("link", { name: /Fleet/ }));
    expect(screen.getByTestId("where")).toHaveTextContent("/fleet");
    expect(screen.getByRole("dialog", { name: "Fleet" })).toBeInTheDocument();
  });

  it("counts on the Fleet button the trucks that can't take a run today", async () => {
    const { calls } = stubApi((_m, url) => {
      if (path(url) === "/v1/vehicles") {
        return json(200, { items: [{ rego: "TMS027", active: true, state: "grounded" }, { rego: "TMS018", active: true, state: "due" },
                                   { rego: "TMS041", active: true, state: "overdue" }], total: 3 });
      }
      return json(200, { items: [], total: 0 });
    });
    renderApp("/");
    const fleet = await screen.findByRole("link", { name: /Fleet/ });
    await waitFor(() => expect(fleet).toHaveTextContent("Fleet2"));
    expect(calls.find((c) => path(c.url) === "/v1/vehicles")!.url).toMatch(/active=true/);
  });

  it("signs out from the account button", async () => {
    const { calls } = stubApi(() => json(200, { items: [], total: 0 }));
    renderApp("/");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Account: Dee Dispatch" }));
    await user.click(screen.getByRole("button", { name: "Sign out" }));
    expect(await screen.findByRole("button", { name: "Sign in" })).toBeInTheDocument();
    expect(calls.some((c) => c.url === "/v1/auth/logout")).toBe(true);
  });

  it("shows a plain message, not a blank screen, when a drawer can't be drawn", async () => {
    const quiet = vi.spyOn(console, "error").mockImplementation(() => undefined);
    stubApi((_m, url) => {
      // a run with no truck: the plan board can't draw it
      if (path(url) === "/v1/runs") return json(200, { items: [{ id: 1, run_no: "R-0001", stops: [] }], total: 1 });
      return json(200, { items: [], total: 0 });
    });
    renderApp("/plan");
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Something went wrong showing this screen. Reload the page to try again.");
    expect(screen.getByRole("button", { name: "Reload" })).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Main drawers" })).toBeInTheDocument();
    quiet.mockRestore();
  });

  it("doesn't count a run left open from an earlier day as on the road today", async () => {
    const stop = (outcome: string | null) => ({ sequence: 1, consignment: {}, outcome });
    const earlier = new Date(Date.now() - 2 * 86_400_000);
    const p = (n: number) => String(n).padStart(2, "0");
    const day = `${earlier.getFullYear()}-${p(earlier.getMonth() + 1)}-${p(earlier.getDate())}`;
    stubApi((_m, url) => {
      if (path(url) === "/v1/runs") {
        return json(200, { items: [{ id: 5, date: day, status: "departed", run_no: "R-0005", driver: { name: "Tom" },
                                     vehicle: { rego: "TMS063" }, stops: [stop("delivered"), stop(null), stop(null)] }], total: 1 });
      }
      if (path(url) !== "/v1/consignments") return null;
      const status = new URLSearchParams(url.split("?")[1]).get("status");
      return json(200, { items: [], total: { "packed,failed": 14, departed: 21 }[status ?? ""] ?? 0 });
    });
    renderApp("/");
    expect(await screen.findByText("19")).toBeInTheDocument();
    // nor its drops as delivered today
    expect(screen.getByText("Delivered").nextSibling).toHaveTextContent("0");
  });
});
