import { act, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { DriverRun } from "../api/types";
import { readOdometer } from "../pages/PrestartCheck";
import { json, renderApp, signedIn, stubApi, TODAY } from "./harness";

vi.mock("../lib/picture", () => ({
  shrinkPhoto: async () => new Blob([new Uint8Array([0xff, 0xd8, 0xff])], { type: "image/jpeg" }),
  toBlob: async () => new Blob([new Uint8Array([0x89, 0x50])], { type: "image/png" }),
}));

const UNCHECKED: DriverRun = { ...TODAY, prestart: null };
const GROUNDED = "TMS041 is grounded: Brakes failed the pre-start. A fleet manager has to mark it fixed first.";

/** The runs list as the office has it: unchecked until a pre-start arrives, then
 * as `after` says (checked and free to go, unless told otherwise). */
function office(after: Partial<DriverRun> = {}) {
  let checked = false;
  return stubApi((method, url) => {
    if (url.startsWith("/v1/driver/runs?date=")) {
      return json(200, [checked ? { ...TODAY, ...after } : UNCHECKED]);
    }
    if (method === "PUT") return json(201, {});
    if (url === "/v1/runs/7/prestart") {
      checked = true;
      return json(200, { result: after.vehicle?.blocked ? "grounded" : "passed" });
    }
    return null;
  });
}

async function passAll(user: ReturnType<typeof userEvent.setup>, except: string[] = []) {
  for (const label of ["Tyres", "Lights", "Brakes", "Mirrors", "Load restraints", "Damage"]) {
    if (except.includes(label)) continue;
    await user.click(within(screen.getByRole("radiogroup", { name: label })).getByRole("radio", { name: "Pass" }));
  }
}

describe("the pre-start check", () => {
  beforeEach(() => {
    signedIn();
    URL.createObjectURL = vi.fn(() => "blob:photo");
    URL.revokeObjectURL = vi.fn();
  });
  afterEach(() => vi.unstubAllGlobals());

  it("reads the odometer as the driver types it", () => {
    expect(readOdometer("61,208")).toBe(61208);
    expect(readOdometer(" 61 208 ")).toBe(61208);
    expect(readOdometer("61.2")).toBeNull();
    expect(readOdometer("")).toBeNull();
  });

  it("comes before Start, and once the office has it the run can start", async () => {
    const { calls } = office();
    renderApp("/");
    const user = userEvent.setup();
    expect(await screen.findByRole("region", { name: "Checklist for a rigid" })).toHaveTextContent("rigid checklist · 0 of 6 pass");
    expect(screen.queryByRole("button", { name: "Start run" })).not.toBeInTheDocument();
    const finish = screen.getByRole("button", { name: "Finish check" });
    await passAll(user);
    expect(finish).toBeDisabled();                 // no odometer yet
    await user.type(screen.getByLabelText(/Odometer/), "61,208");
    await user.click(finish);
    expect(await screen.findByRole("button", { name: "Start run" })).toBeInTheDocument();
    expect(screen.getByText(/Pre-start done .*: passed\./)).toBeInTheDocument();
    const sent = calls.find((c) => c.url === "/v1/runs/7/prestart")!.body as Record<string, unknown>;
    expect(sent).toEqual({ message_id: expect.any(String), at: expect.any(String), odometer_km: 61208,
                           items: [7, 8, 9, 10, 11, 12].map((id) => ({ item_id: id, result: "pass" })) });
  });

  it("asks a photo and a note of a failed item, and sends the photo first", async () => {
    const { calls } = office({ prestart: { id: 2, at: "2026-10-14T19:52:00Z", result: "failed" } });
    renderApp("/");
    const user = userEvent.setup();
    await screen.findByRole("region", { name: "Checklist for a rigid" });
    await user.type(screen.getByLabelText(/Odometer/), "61208");
    await passAll(user, ["Load restraints"]);
    await user.click(within(screen.getByRole("radiogroup", { name: "Load restraints" })).getByRole("radio", { name: "Fail" }));
    expect(screen.getByText("A fail needs a photo and a note.")).toBeInTheDocument();
    expect(screen.getByText(/Not a critical item, so you can still go/)).toBeInTheDocument();
    const finish = screen.getByRole("button", { name: "Finish check" });
    await user.type(screen.getByLabelText("Load restraints: note"), "Rear strap frayed near the hook.");
    expect(finish).toBeDisabled();
    await user.upload(screen.getByLabelText(/Add a photo/), new File(["x"], "strap.jpg", { type: "image/jpeg" }));
    await user.click(finish);
    expect(await screen.findByText(/Pre-start done .*: an item failed, and the office will see it\./)).toBeInTheDocument();
    const put = calls.find((c) => c.method === "PUT")!;
    const items = (calls.find((c) => c.url === "/v1/runs/7/prestart")!.body as { items: unknown[] }).items;
    expect(items[4]).toEqual({ item_id: 11, result: "fail", note: "Rear strap frayed near the hook.",
                               photo_file: put.url.split("/")[3].split("?")[0] });
    expect(calls.findIndex((c) => c.method === "PUT")).toBeLessThan(calls.findIndex((c) => c.url === "/v1/runs/7/prestart"));
  });

  it("says plainly when a critical item grounds the truck, and offers no Start", async () => {
    office({ prestart: { id: 2, at: "2026-10-14T19:52:00Z", result: "grounded" },
             vehicle: { ...TODAY.vehicle, blocked: { code: "vehicle_grounded", message: GROUNDED } } });
    renderApp("/");
    const user = userEvent.setup();
    await screen.findByRole("region", { name: "Checklist for a rigid" });
    await user.type(screen.getByLabelText(/Odometer/), "61208");
    await passAll(user, ["Brakes"]);
    await user.click(within(screen.getByRole("radiogroup", { name: "Brakes" })).getByRole("radio", { name: "Fail" }));
    expect(screen.getByText(/A critical item: the truck can't go/)).toBeInTheDocument();
    await user.type(screen.getByLabelText("Brakes: note"), "Pedal goes to the floor.");
    await user.upload(screen.getByLabelText(/Add a photo/), new File(["x"], "brakes.jpg", { type: "image/jpeg" }));
    await user.click(screen.getByRole("button", { name: "Finish check" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(`${GROUNDED} Don't take it out: call the office.`);
    expect(screen.getByRole("radio", { name: /TMS041/ })).toHaveTextContent("Grounded · R-0007");
    expect(screen.queryByRole("button", { name: "Start run" })).not.toBeInTheDocument();
  });

  it("keeps a check made with no signal, and the run starts once it has gone", async () => {
    let online = false;
    const { calls } = office();
    const { queue } = renderApp("/", { online: () => online });
    const user = userEvent.setup();
    await screen.findByRole("region", { name: "Checklist for a rigid" });
    await user.type(screen.getByLabelText(/Odometer/), "61208");
    await passAll(user);
    await user.click(screen.getByRole("button", { name: "Finish check" }));
    expect(await screen.findByText(/Your pre-start is kept on this phone/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Start run" })).not.toBeInTheDocument();
    expect(calls.some((c) => c.url === "/v1/runs/7/prestart")).toBe(false);
    online = true;
    await act(async () => { await queue.drain(); });
    expect(await screen.findByRole("button", { name: "Start run" })).toBeInTheDocument();
    expect(calls.filter((c) => c.url === "/v1/runs/7/prestart")).toHaveLength(1);
  });

  it("asks for a fresh check when the office gives the run another truck", async () => {
    let checked = false;
    stubApi((_method, url) => {
      if (url.startsWith("/v1/driver/runs?date=")) {
        return json(200, [checked ? { ...UNCHECKED, vehicle: { ...TODAY.vehicle, rego: "TMS052", type: "van" } } : UNCHECKED]);
      }
      if (url === "/v1/runs/7/prestart") {
        checked = true;
        return json(200, { vehicle: "TMS041", result: "passed" });
      }
      return null;
    });
    renderApp("/");
    const user = userEvent.setup();
    await screen.findByRole("region", { name: "Checklist for a rigid" });
    await user.type(screen.getByLabelText(/Odometer/), "61208");
    await passAll(user);
    await user.click(screen.getByRole("button", { name: "Finish check" }));
    expect(await screen.findByRole("region", { name: "Checklist for a van" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Start run" })).not.toBeInTheDocument();
  });

  it("won't take a reading below the truck's last one", async () => {
    office();
    renderApp("/");
    const user = userEvent.setup();
    await screen.findByRole("region", { name: "Checklist for a rigid" });
    expect(screen.getByText("Last 61,000 km")).toBeInTheDocument();
    await passAll(user);
    await user.type(screen.getByLabelText(/Odometer/), "6100");
    expect(screen.getByRole("alert")).toHaveTextContent("That's less than the last reading, 61,000 km.");
    expect(screen.getByRole("button", { name: "Finish check" })).toBeDisabled();
  });

  it("says why a check wasn't taken, and lets the driver do it again", async () => {
    stubApi((_method, url) => {
      if (url.startsWith("/v1/driver/runs?date=")) return json(200, [UNCHECKED]);
      if (url === "/v1/runs/7/prestart") {
        return json(409, { code: "checklist_changed", detail: "The rigid checklist has changed since this check began. Do the check again." });
      }
      return null;
    });
    renderApp("/");
    const user = userEvent.setup();
    await screen.findByRole("region", { name: "Checklist for a rigid" });
    await user.type(screen.getByLabelText(/Odometer/), "61208");
    await passAll(user);
    await user.click(screen.getByRole("button", { name: "Finish check" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Your pre-start wasn't taken: The rigid checklist has changed");
    await user.click(screen.getByRole("button", { name: "Do the check again" }));
    expect(await screen.findByRole("region", { name: "Checklist for a rigid" })).toBeInTheDocument();
  });
});
