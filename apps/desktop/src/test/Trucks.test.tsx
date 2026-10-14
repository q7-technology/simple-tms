import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { Run } from "../api/types";
import { dayLabel, isoDay } from "../lib/days";
import { about } from "../lib/live";
import { DISPATCHER, json, path, renderApp, stubApi } from "./harness";

const card = (id: number, customer: string) => ({ id, wms_ref: `00800345${id}`, customer, suburb: "Ballarat", status: "departed",
                                                  packages: 2, cartons: 2, weight_kg: "120", volume_m3: "0.8" });
const minutes = (n: number) => new Date(Date.now() + n * 60_000).toISOString();

const ON_THE_ROAD = {
  id: 7, run_no: "R-0007", date: isoDay(new Date()), status: "departed",
  vehicle: { rego: "TMS041", type: "rigid", max_kg: "4500", max_m3: "22", active: true, blocked: null },
  driver: { code: "kim", name: "Kim" }, departed_at: minutes(-40), finished_at: null,
  stops: [{ sequence: 1, consignment: card(31, "Hillside Bakery"), outcome: "delivered", outcome_at: minutes(-20) },
          { sequence: 2, consignment: card(32, "Ridge Auto Workshop"), outcome: null },
          { sequence: 3, consignment: card(33, "Two Rivers Vet"), outcome: null }],
  estimate: { basis: "average", minutes_per_stop: 20, back_at_depot: minutes(60),
              stops: [{ consignment_id: 32, sequence: 2, at: minutes(0) }, { consignment_id: 33, sequence: 3, at: minutes(20) }] },
} as unknown as Run;
const LOADING = { ...ON_THE_ROAD, id: 8, run_no: "R-0008", status: "planned", departed_at: null, estimate: null,
                  vehicle: { ...ON_THE_ROAD.vehicle, rego: "TMS052", type: "van" }, driver: { code: "mel", name: "Mel" },
                  stops: ON_THE_ROAD.stops.map((s) => ({ ...s, outcome: null })) } as unknown as Run;
const HEARD = { run_id: 7, run_no: "R-0007", vehicle: { rego: "TMS041", type: "rigid" }, driver: { code: "kim", name: "Kim" },
                latitude: "-37.56", longitude: "143.85", accuracy_m: "9", at: minutes(-0.5), received_at: new Date(Date.now() - 30_000).toISOString() };

const YESTERDAY = new Date(Date.now() - 86_400_000);
// left on the road yesterday and never finished: the API lists it first
const LEFT_OPEN = { ...ON_THE_ROAD, id: 5, run_no: "R-0005", date: isoDay(YESTERDAY),
                    vehicle: { ...ON_THE_ROAD.vehicle, rego: "TMS063", type: "van" }, driver: { code: "tom", name: "Tom" },
                    stops: ON_THE_ROAD.stops.map((s) => ({ ...s, outcome: "delivered" })) } as unknown as Run;

function stub(who: object = DISPATCHER, heard: object[] = [HEARD], runs: Run[] = [ON_THE_ROAD, LOADING]) {
  return stubApi((_m, url) => {
    if (path(url) === "/v1/runs") return json(200, { items: runs, total: runs.length });
    if (path(url) === "/v1/positions") return json(200, { items: heard, total: heard.length });
    if (path(url) === "/v1/vehicles") {
      return json(200, { items: [{ rego: "TMS053", type: "van", active: true, state: "grounded",
                                   blocked: { code: "vehicle_grounded", message: "TMS053 is grounded: Brakes failed the pre-start." } }], total: 1 });
    }
    if (path(url) === "/v1/feed") return json(200, { items: [] });
    return json(200, { items: [], total: 0 });
  }, who);
}

describe("the trucks on the map", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("draws each run's truck, and the one on the road with when it was last heard from", async () => {
    stub();
    renderApp("/");
    const truck = await screen.findByRole("button", { name: /R-0007 · Kim/ });
    expect(truck).toHaveAccessibleName("R-0007 · Kim: on the road, last heard 30 s ago");
    expect(screen.getByRole("button", { name: /R-0008 · loading/ })).toHaveAccessibleName("R-0008 · loading: Mel, TMS052");
    // the first on the road is shown to start with, as Main.html has it
    const shown = screen.getByRole("region", { name: "Selected truck" });
    expect(shown).toHaveTextContent("TMS041 · Kim · rigid");
    expect(shown).toHaveTextContent("Run R-0007");
    expect(within(shown).getByText("On the road")).toBeInTheDocument();
    expect(within(shown).getByText("Last heard 30 s ago")).toBeInTheDocument();
    expect(within(shown).getByRole("img", { name: "1 of 3 stops done" })).toBeInTheDocument();
    expect(shown).toHaveTextContent("Next stop2 · Ridge Auto Workshop");
    expect(shown).toHaveTextContent(`Arrives${about(minutes(0))}`);
    expect(shown).toHaveTextContent("Load left240 kg · 1.6 m³");
    expect(shown).toHaveTextContent(`Back at depot${about(minutes(60))}`);
    expect(shown).toHaveTextContent("Times are estimates from the stop order and Kim's average time per stop today.");
    expect(within(shown).getByRole("link", { name: /Where is it/ })).toHaveAttribute("href", "/where?run=7");
    expect(within(shown).getByRole("link", { name: /Open run/ })).toHaveAttribute("href", "/plan");
  });

  it("shows a run still loading, with no times yet", async () => {
    stub();
    renderApp("/");
    await userEvent.setup().click(await screen.findByRole("button", { name: /R-0008 · loading/ }));
    const shown = screen.getByRole("region", { name: "Selected truck" });
    expect(shown).toHaveTextContent("TMS052 · Mel · van");
    expect(within(shown).getByText("Loading")).toBeInTheDocument();
    expect(shown).toHaveTextContent("Not left yet");
    expect(shown).not.toHaveTextContent("Arrives");
    expect(screen.getByRole("button", { name: /R-0008 · loading/ })).toHaveAttribute("aria-pressed", "true");
  });

  it("says when a truck on the road hasn't been heard from for a while, or yet", async () => {
    stub(DISPATCHER, [{ ...HEARD, received_at: new Date(Date.now() - 12 * 60_000).toISOString() }]);
    renderApp("/");
    const shown = await screen.findByRole("region", { name: "Selected truck" });
    await waitFor(() => expect(within(shown).getByText("Last heard 12 min ago")).toHaveClass("p-amber"));
  });

  it("draws a truck that can't go in grey at the depot, in red, for Fleet", async () => {
    stub();
    renderApp("/");
    const grounded = await screen.findByRole("link", { name: /TMS053 · grounded/ });
    expect(grounded).toHaveAttribute("href", "/fleet");
    expect(grounded).toHaveAccessibleName("TMS053 · grounded: TMS053 is grounded: Brakes failed the pre-start.");
  });

  it("asks where trucks are only of someone who may know", async () => {
    const { calls } = stub({ ...DISPATCHER, scopes: ["fleet:read", "consignments:read", "runs:read", "runs:write"] });
    renderApp("/");
    const shown = await screen.findByRole("region", { name: "Selected truck" });
    expect(shown).toHaveTextContent("Run R-0007");
    expect(calls.some((c) => path(c.url) === "/v1/positions")).toBe(false);
    expect(shown).not.toHaveTextContent("Last heard");
    expect(within(shown).queryByRole("link", { name: /Where is it/ })).not.toBeInTheDocument();
  });

  it("shows a run left open from an earlier day as still open, not on the road today", async () => {
    const { calls } = stub(DISPATCHER, [HEARD], [LEFT_OPEN, ON_THE_ROAD, LOADING]);
    renderApp("/");
    const old = await screen.findByRole("button", { name: /R-0005/ });
    const from = `Still open from ${dayLabel(YESTERDAY)}`;
    expect(old).toHaveAccessibleName(`R-0005 · Tom: ${from.toLowerCase()}`);
    expect(calls.find((c) => path(c.url) === "/v1/runs")?.url).toContain("left_open=true");
    // the card starts on today's run on the road, not the one left open
    expect(screen.getByRole("region", { name: "Selected truck" })).toHaveTextContent("Run R-0007");
    await userEvent.setup().click(old);
    const shown = screen.getByRole("region", { name: "Selected truck" });
    expect(within(shown).getByText(from)).toHaveClass("p-amber");
    expect(shown).toHaveTextContent("Every stop has an outcome: finish it in Plan runs.");
    expect(shown).not.toHaveTextContent("On the road");
    expect(shown).not.toHaveTextContent("Arrives");
    expect(shown).not.toHaveTextContent("Back at depot");
    expect(shown).not.toHaveTextContent("Last heard");
    expect(within(shown).queryByRole("link", { name: /Where is it/ })).not.toBeInTheDocument();
  });
});
