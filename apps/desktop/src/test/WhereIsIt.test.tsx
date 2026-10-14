import { act, screen, waitFor, within } from "@testing-library/react";
import { json, path, renderApp, stubApi } from "./harness";

const slippy = vi.hoisted(() => ({ mounted: [] as unknown[], shown: [] as unknown[], tileError: null as null | (() => void), gone: 0 }));
vi.mock("../lib/slippy", () => ({
  mountMap: (_el: HTMLElement, tiles: unknown, onTileError: () => void) => {
    slippy.mounted.push(tiles);
    slippy.tileError = onTileError;
    return { show: (trucks: unknown, focus: unknown) => slippy.shown.push({ trucks, focus }), destroy: () => { slippy.gone += 1; } };
  },
}));

const KIM = { run_id: 7, run_no: "R-0007", vehicle: { rego: "TMS041", type: "rigid" }, driver: { code: "kim", name: "Kim" },
              latitude: "-37.56221", longitude: "143.8503", accuracy_m: "12", at: new Date().toISOString(),
              received_at: new Date(Date.now() - 40_000).toISOString() };
const MEL = { ...KIM, run_id: 8, run_no: "R-0008", vehicle: { rego: "TMS052", type: "van" }, driver: { code: "mel", name: "Mel" },
              latitude: "-37.6", longitude: "143.9", accuracy_m: "30" };

function stub(tiles: object) {
  return stubApi((_m, url) => {
    if (path(url) === "/v1/map") return json(200, tiles);
    if (path(url) === "/v1/positions") return json(200, { items: [KIM, MEL], total: 2 });
    return json(200, { items: [], total: 0 });
  });
}

describe("where is it", () => {
  beforeEach(() => { slippy.mounted = []; slippy.shown = []; slippy.gone = 0; });
  afterEach(() => vi.unstubAllGlobals());

  it("lists each truck on the road with when it was last heard from, with no map when no tiles are set up", async () => {
    stub({ tile_url: null, attribution: null });
    renderApp("/where?run=7");
    const drawer = await screen.findByRole("dialog", { name: "Where is it" });
    expect(await within(drawer).findByText(/No map tiles are set up/)).toBeInTheDocument();
    const rows = within(within(drawer).getByRole("table")).getAllByRole("row").slice(1);
    expect(rows.map((r) => r.textContent)).toEqual([
      "R-0007TMS041Kim40 s ago-37.56221, 143.8503 (within 12 m)",
      "R-0008TMS052Mel40 s ago-37.6, 143.9 (within 30 m)",
    ]);
    // the truck asked about stands out
    expect(rows[0]).toHaveClass("sel");
    expect(slippy.mounted).toEqual([]);
  });

  it("shows them on a map from the tiles set up, the one asked about in the middle", async () => {
    stub({ tile_url: "/tiles/{z}/{x}/{y}.png", attribution: "© OpenStreetMap contributors" });
    renderApp("/where?run=7");
    await waitFor(() => expect(slippy.mounted).toEqual([{ tile_url: "/tiles/{z}/{x}/{y}.png", attribution: "© OpenStreetMap contributors" }]));
    await waitFor(() => expect(slippy.shown.at(-1)).toEqual({ trucks: [KIM, MEL], focus: 7 }));
    expect(screen.queryByText(/No map tiles are set up/)).not.toBeInTheDocument();
    act(() => slippy.tileError!());
    expect(await screen.findByText(/The map tiles didn't load/)).toBeInTheDocument();
  });
});
