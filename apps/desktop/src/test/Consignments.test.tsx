import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { Consignment } from "../api/types";
import { json, path, renderApp, stubApi } from "./harness";

const con = (id: number, customer: string, status: string, over: Partial<Consignment> = {}): Consignment => ({
  id, wms_ref: `00800344${id}`, warehouse: "BAL-WH01", owner: "DEFAULT", customer, ship_to: { suburb: "Buninyong" },
  required_by: null, status, created_at: "", weight_kg: "84", volume_m3: "0.6",
  packages: Array.from({ length: 4 }, (_, i) => ({ package_no: i + 1, type: "carton", weight_kg: "21", length_cm: null,
                                                   width_cm: null, height_cm: null, sscc: null })),
  run: null, carrier: null, ...over,
});

const BAKERY = con(88, "Hillside Bakery", "delivered", { run: { id: 7, run_no: "R-0007", driver: "Kim", sequence: 5 } });
const DELI = con(19, "Station Deli", "packed");
const FLORIST = con(97, "Mill Lane Florist", "booked", { carrier: { id: 2, name: "Valley Freight (sample)", con_note: null,
                                                                     manifest_id: 3, manifest_no: "M-0003", collected_at: null } });
const COUNTS: Record<string, number> = { "": 48, "packed,failed": 14, "assigned,departed": 21, "booked,collected": 6,
                                         delivered: 17, failed: 1 };

function desk() {
  return stubApi((_m, url) => {
    const p = new URLSearchParams(url.split("?")[1]);
    if (path(url) === "/v1/consignments" && p.get("limit") === "0") {
      return json(200, { items: [], total: COUNTS[p.get("status") ?? ""] ?? 0 });
    }
    if (path(url) === "/v1/consignments") return json(200, { items: [BAKERY, DELI, FLORIST], total: 3 });
    if (path(url) === "/v1/consignments/88") {
      return json(200, { ...BAKERY, history: [
        { kind: "packed", at: "2026-10-13T20:42:00Z", detail: { packages: 4 } },
        { kind: "assigned", at: "2026-10-13T21:05:00Z", detail: { run: "R-0007", by: "jo" } },
        { kind: "departed", at: "2026-10-13T21:31:00Z", detail: { run: "R-0007", vehicle: "TMS041", driver: "kim" } },
        { kind: "delivered", at: "2026-10-13T23:52:00Z", detail: { packages: 4, signed_name: "Priya", signature_file: "s1",
                                                                   photo_file: "p1" } }] });
    }
    if (path(url) === "/v1/consignments/97") {
      return json(200, { ...FLORIST, history: [{ kind: "booked", at: "2026-10-13T21:05:00Z",
                                                 detail: { carrier: "Valley Freight (sample)", manifest: "M-0003", by: "jo" } }] });
    }
    if (path(url).startsWith("/v1/pod-files/")) {
      return new Response(new Uint8Array([0x89, 0x50]), { status: 200, headers: { "Content-Type": "image/png" } });
    }
    return null;
  });
}

describe("Consignments", () => {
  beforeEach(() => { URL.createObjectURL = vi.fn(() => "blob:pod"); URL.revokeObjectURL = vi.fn(); });
  afterEach(() => vi.unstubAllGlobals());

  it("lists each with where it is now, and counts each tab", async () => {
    desk();
    renderApp("/consignments");
    const tabs = await screen.findByRole("tablist", { name: "Consignment status" });
    await waitFor(() => expect(within(tabs).getByRole("tab", { name: /To plan/ })).toHaveTextContent("To plan14"));
    expect(within(tabs).getByRole("tab", { name: /All/ })).toHaveAttribute("aria-selected", "true");
    expect(within(tabs).getByRole("tab", { name: /With carrier/ })).toHaveTextContent("6");
    const rows = screen.getAllByRole("row");
    expect(rows[1]).toHaveTextContent("0080034488Hillside BakeryBuninyong4Delivered");
    expect(rows[2]).toHaveTextContent("To plan");
    expect(rows[3]).toHaveTextContent("Needs con note");
  });

  it("shows the first one's history and its proof of delivery", async () => {
    desk();
    renderApp("/consignments");
    const aside = await screen.findByRole("complementary", { name: "Consignment details" });
    expect(await within(aside).findByText("Packed in Simple WMS")).toBeInTheDocument();
    expect(within(aside).getByText("Hillside Bakery · Buninyong · 4 cartons · 84 kg · 0.6 m³")).toBeInTheDocument();
    expect(within(aside).getByText("R-0007 · Kim · stop 5")).toBeInTheDocument();
    expect(within(aside).getAllByRole("listitem").map((li) => li.querySelector("b")!.textContent))
      .toEqual(["Packed in Simple WMS", "On run R-0007", "Departed", "Delivered"]);
    expect(await within(aside).findByRole("img", { name: "Signature from Priya" })).toBeInTheDocument();
    expect(within(aside).getByRole("img", { name: "Photo at the drop" })).toBeInTheDocument();
    expect(within(aside).getByText("Priya")).toBeInTheDocument();
  });

  it("finds by what was typed on the map, and by tab", async () => {
    const { calls } = desk();
    renderApp("/consignments?q=vf-0041");
    expect(await screen.findByRole("searchbox", { name: "Search consignments" })).toHaveValue("vf-0041");
    await waitFor(() => expect(calls.some((c) => c.url.includes("q=vf-0041") && !c.url.includes("limit=0"))).toBe(true));
    const user = userEvent.setup();
    await user.click(screen.getByRole("tab", { name: /With carrier/ }));
    await waitFor(() => expect(calls.some((c) => c.url.includes("status=booked%2Ccollected") && !c.url.includes("limit=0")))
      .toBe(true));
    await user.click(screen.getByRole("button", { name: "Show 0080034497" }));
    const aside = await screen.findByRole("complementary", { name: "Consignment details" });
    expect(await within(aside).findByText("Booked with Valley Freight (sample)")).toBeInTheDocument();
    expect(within(aside).getByText("Valley Freight (sample) · no con note yet")).toBeInTheDocument();
  });
});
