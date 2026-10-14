import { screen, within } from "@testing-library/react";
import { json, path, renderApp, stubApi } from "./harness";

// half a minute over, so the map's clock (read when it opened) still counts the whole minutes
const ago = (min: number) => new Date(Date.now() - (min + 0.5) * 60_000).toISOString();
const line = (over: object) => ({ kind: "delivered", at: ago(2), run_id: 7, run_no: "R-0007", rego: "TMS041", driver: "Kim",
  sequence: 7, customer: "Main Street Chemist", wms_ref: "0080034521", reason: null, result: null, failed: [], by: null,
  note: null, ...over });

describe("what just happened", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("tells the latest, newest first, with how long ago", async () => {
    const { calls } = stubApi((_m, url) => {
      if (path(url) === "/v1/feed") {
        return json(200, { items: [
          line({}),
          line({ kind: "prestart", result: "passed", driver: "Mel", rego: "TMS052", at: ago(9) }),
          line({ kind: "packed", wms_ref: "0080030431", customer: "Ballan Hardware", at: ago(14) }),
          line({ kind: "prestart", result: "grounded", rego: "TMS027", failed: ["Lights"], at: ago(240) }),
        ] });
      }
      return json(200, { items: [], total: 0 });
    });
    renderApp("/");
    const feed = await screen.findByRole("region", { name: "What just happened" });
    const rows = await within(feed).findAllByRole("listitem");
    expect(rows.map((r) => r.textContent)).toEqual([
      "Kim delivered stop 7 · Main Street Chemist2 min",
      "Mel passed the pre-start on TMS0529 min",
      "0080030431 packed in Simple WMS · Ballan Hardware14 min",
      "TMS027 grounded · Lights failed4 h",
    ]);
    expect(calls.find((c) => path(c.url) === "/v1/feed")!.url).toMatch(/limit=5/);
  });

  it("says when nothing has happened yet", async () => {
    stubApi(() => json(200, { items: [], total: 0 }));
    renderApp("/");
    const feed = await screen.findByRole("region", { name: "What just happened" });
    expect(await within(feed).findByText("Nothing yet. Deliveries, pre-starts and runs leaving show here as they happen.")).toBeInTheDocument();
  });
});
