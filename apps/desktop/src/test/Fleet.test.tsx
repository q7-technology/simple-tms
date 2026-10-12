import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { PrestartDetail, Vehicle } from "../api/types";
import { json, MANAGER, path, renderApp, stubApi, type Call } from "./harness";

const due = { expires_on: "2027-03-03", days_left: 142, state: "ok" as const };
const service = { last_on: "2026-07-14", last_km: 46900, due_on: "2027-01-14", due_km: 61900,
                  days_left: 92, km_left: 3000, state: "ok" as const };
const truck = (rego: string, over: Partial<Vehicle> = {}): Vehicle => ({
  id: 1, rego, type: "rigid", max_kg: "4500", max_m3: "22", active: true, service_every_months: 6,
  service_every_km: 15000, odometer_km: 58900, rego_due: due, service_due: service, grounded: null,
  last_prestart: null, state: "ok", blocked: null, ...over,
});

const GROUNDED = truck("TMS027", {
  type: "ute", state: "grounded", grounded: { check_id: 12, at: "2026-10-13T19:40:00Z", failed: ["Lights"] },
  last_prestart: { id: 12, at: "2026-10-13T19:40:00Z", result: "grounded", by: "tom" },
  blocked: { code: "vehicle_grounded", message: "TMS027 is grounded: Lights failed the pre-start. A fleet manager has to mark it fixed first." },
});
const FLEET = [
  truck("TMS041", { last_prestart: { id: 9, at: "2026-10-13T19:52:00Z", result: "passed", by: "kim" } }),
  truck("TMS018", { type: "van", state: "due", rego_due: { expires_on: "2026-10-25", days_left: 12, state: "due" } }),
  GROUNDED,
];
const CHECK: PrestartDetail = {
  id: 12, run_id: 4, run_no: "R-0004", vehicle: "TMS027", vehicle_type: "ute", driver: { code: "tom", name: "Tom" },
  at: "2026-10-13T19:40:00Z", recorded_at: "2026-10-13T19:40:05Z", odometer_km: 84312, result: "grounded",
  items: [
    { label: "Tyres", critical: false, passed: true, note: null, photo_file: null },
    { label: "Lights", critical: true, passed: false, note: "Left brake light out. Bulb or fuse, not sure.", photo_file: "3f2a" },
  ],
  fixed: null,
};

function depot(answers: Record<string, (c: Call) => Response | null> = {}, who?: object) {
  return stubApi((method, url, body) => {
    const key = `${method} ${path(url)}`;
    if (answers[key]) return answers[key]({ method, url, body });
    if (key === "GET /v1/vehicles") return json(200, { items: FLEET, total: FLEET.length });
    if (key === "GET /v1/runs") {
      return json(200, { items: [{ id: 1, run_no: "R-0001", status: "departed", vehicle: { rego: "TMS041" },
                                   driver: { code: "kim", name: "Kim" } }], total: 1 });
    }
    if (key === "GET /v1/prestarts/12") return json(200, CHECK);
    if (key.startsWith("GET /v1/vehicles/") && key.endsWith("/history")) {
      return json(200, [{ kind: "prestart", id: 12, recorded_at: "2026-10-13T19:40:05Z", by: "tom", note: null,
                          result: "grounded", failed: ["Lights"], odometer_km: 84312, done_on: null, expires_on: null, check_id: null }]);
    }
    if (key === "GET /v1/pod-files/3f2a") {
      return new Response(new Uint8Array([0xff, 0xd8, 0xff]), { status: 200, headers: { "Content-Type": "image/jpeg" } });
    }
    return null;
  }, who);
}

describe("Fleet", () => {
  beforeEach(() => {
    URL.createObjectURL = vi.fn(() => "blob:photo");
    URL.revokeObjectURL = vi.fn();
  });
  afterEach(() => vi.unstubAllGlobals());

  it("lists each truck with when its rego and service fall due, and opens on the grounded one", async () => {
    const { calls } = depot();
    renderApp("/fleet");
    const table = await screen.findByRole("table");
    const row = (rego: string) => within(table).getByRole("button", { name: `Show ${rego}` }).closest("tr")!;
    await waitFor(() => expect(row("TMS041")).toHaveTextContent("3 Mar 2027"));
    expect(row("TMS041")).toHaveTextContent("Kim");
    expect(row("TMS041")).toHaveTextContent("14 Jan 2027or 61,900 km");
    expect(row("TMS041")).toHaveTextContent("On a run");
    expect(row("TMS018")).toHaveTextContent("25 Oct 2026due in 12 days");
    expect(row("TMS018")).toHaveTextContent("Rego due");
    expect(row("TMS027")).toHaveTextContent("Grounded");
    expect(within(table).getByRole("button", { name: "Show TMS027" })).toHaveAttribute("aria-pressed", "true");
    expect(calls.find((c) => path(c.url) === "/v1/vehicles")!.url).toMatch(/date=\d{4}-\d{2}-\d{2}/);

    const record = screen.getByRole("complementary", { name: "Pre-start check record" });
    expect(await within(record).findByText("PRE-START CHECK · FAILED")).toBeInTheDocument();
    expect(record).toHaveTextContent("Ute · Tom ·");
    expect(record).toHaveTextContent("odometer 84,312 km");
    expect(record).toHaveTextContent("Tom's note · LightsLeft brake light out. Bulb or fuse, not sure.");
    expect(await within(record).findByRole("img", { name: "Tom's photo: Lights" })).toHaveAttribute("src", "blob:photo");
    expect(record).toHaveTextContent("It can't go on a run until a fleet manager marks it fixed.");
    // a dispatcher sees it, but marking fixed is for a fleet manager
    expect(within(record).queryByRole("button", { name: "Mark fixed" })).not.toBeInTheDocument();
    expect(screen.getByText("GROUNDED").parentElement).toHaveTextContent("TMS027 · lights");
  });

  it("lets a fleet manager mark it fixed with what was done", async () => {
    let fixed = false;
    const { calls } = depot({
      "POST /v1/vehicles/TMS027/fix": () => { fixed = true; return json(201, { kind: "fixed" }); },
      "GET /v1/vehicles": () => json(200, { items: fixed ? [FLEET[0], FLEET[1], truck("TMS027", { type: "ute" })] : FLEET, total: 3 }),
    }, MANAGER);
    renderApp("/fleet");
    const user = userEvent.setup();
    const button = await screen.findByRole("button", { name: "Mark fixed" });
    expect(button).toBeDisabled();
    await user.type(screen.getByLabelText("What was done"), "Replaced left brake globe, tested");
    await user.click(button);
    expect(await screen.findByText("TMS027 is marked fixed, and can take a run.")).toHaveAttribute("role", "status");
    expect(calls.find((c) => c.url === "/v1/vehicles/TMS027/fix")!.body).toEqual(
      { check_id: 12, note: "Replaced left brake globe, tested" });
    expect(screen.queryByRole("button", { name: "Mark fixed" })).not.toBeInTheDocument();
  });

  it("says so when another failed check came in meanwhile", async () => {
    depot({ "POST /v1/vehicles/TMS027/fix": () => json(409, { code: "stale",
      detail: "Another failed check of TMS027 has come in; it has been reloaded, so look at it and mark it fixed again" }) }, MANAGER);
    renderApp("/fleet");
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("What was done"), "New globe");
    await user.click(screen.getByRole("button", { name: "Mark fixed" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Another failed check of TMS027 has come in");
  });

  it("records a service with its reading and a note", async () => {
    const { calls } = depot({ "POST /v1/vehicles/TMS041/service": () => json(201, { kind: "service" }) }, MANAGER);
    renderApp("/fleet");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Show TMS041" }));
    const record = screen.getByRole("complementary", { name: "Pre-start check record" });
    await user.click(within(record).getByText("A service done"));
    const form = within(record).getByRole("form", { name: "A service done" });
    await user.type(within(form).getByLabelText("Done on"), "2026-10-13");
    await user.type(within(form).getByLabelText("Odometer (km)"), "61190");
    await user.type(within(form).getByLabelText("Note"), "60,000 km service at Ballarat Diesel");
    await user.click(within(form).getByRole("button", { name: "Record the service" }));
    expect(await screen.findByText("TMS041's service is recorded.")).toHaveAttribute("role", "status");
    expect(calls.find((c) => c.url === "/v1/vehicles/TMS041/service")!.body).toEqual(
      { done_on: "2026-10-13", odometer_km: 61190, note: "60,000 km service at Ballarat Diesel" });
  });
});
