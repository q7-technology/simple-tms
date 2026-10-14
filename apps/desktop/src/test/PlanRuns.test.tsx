import { act, cleanup, createEvent, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { Card, Consignment, Run } from "../api/types";
import { dayLabel, isoDay } from "../lib/days";
import { json, path, renderApp, stubApi, type Call } from "./harness";

const card = (id: number, customer: string, weight_kg: string, volume_m3 = "1"): Card => ({
  id, wms_ref: `00800345${id}`, customer, suburb: "Ballarat", status: "assigned", packages: 3, cartons: 3,
  weight_kg, volume_m3,
});

const run = (id: number, driver: string, rego: string, stops: Card[], over: Partial<Run> = {}): Run => ({
  id, run_no: `R-000${id}`, date: isoDay(new Date()), status: "planned",
  vehicle: { rego, type: "rigid", max_kg: "4500", max_m3: "22", active: true },
  driver: { code: driver.toLowerCase(), name: driver }, created_by: "dee", created_at: "", departed_at: null,
  stops: stops.map((c, i) => ({ sequence: i + 1, consignment: c })),
  load: { weight_kg: "0", volume_m3: "0", max_kg: "4500", max_m3: "22", unknown_weight: 0, unknown_volume: 0,
          state: "ok", message: null },
  run_sheet: null, shipping: { pending: 0, done: 0, failed: 0 }, ...over,
});

const packed = (id: number, customer: string, suburb: string): Consignment => ({
  id, wms_ref: `00800345${id}`, warehouse: "BAL-WH01", owner: "DEFAULT", customer, ship_to: { suburb },
  required_by: null, status: "packed", created_at: "", weight_kg: "210", volume_m3: "1.4",
  packages: Array.from({ length: 8 }, (_, i) => ({ package_no: i + 1, type: "carton", weight_kg: null,
                                                   length_cm: null, width_cm: null, height_cm: null, sscc: null })),
});

const KIM = run(1, "Kim", "TMS041", [card(11, "Hillside Bakery", "84"), card(12, "Two Rivers Vet", "41"),
                                     card(13, "Ridge Auto Workshop", "620")]);
const MEL = run(2, "Mel", "TMS052", [card(21, "Wattle Flat Stockfeed", "1640"), card(22, "Clarkes Hill Builders", "3140")]);

function board(answers: Record<string, (call: Call) => Response | null | Promise<Response>> = {}) {
  return stubApi((method, url, body) => {
    const key = `${method} ${path(url)}`;
    if (answers[key]) return answers[key]({ method, url, body });
    if (key === "GET /v1/runs") return json(200, { items: [KIM, MEL], total: 2 });
    if (key === "GET /v1/consignments") {
      return json(200, { items: [packed(31, "Paddock Produce", "Clunes"), packed(32, "Kerbside Bikes", "Alfredton")], total: 2 });
    }
    if (key === "GET /v1/vehicles") {
      return json(200, { items: [
        { id: 1, rego: "TMS041", type: "rigid", max_kg: "4500", max_m3: "22", active: true },
        { id: 2, rego: "TMS052", type: "rigid", max_kg: "4500", max_m3: "22", active: true },
        { id: 3, rego: "TMS063", type: "van", max_kg: "1200", max_m3: "9", active: true },
        { id: 4, rego: "TMS027", type: "ute", max_kg: "600", max_m3: "3", active: false },
      ], total: 4 });
    }
    if (key === "GET /v1/drivers") return json(200, { items: [{ id: 1, code: "kim", name: "Kim", active: true }], total: 1 });
    if (method === "POST") return json(200, KIM);
    return null;
  });
}

const lane = (name: string) => screen.findByRole("region", { name });

describe("Plan runs", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("draws the tray, each run with its bars, and the truck that can't go", async () => {
    board();
    renderApp("/plan");
    const tray = await lane("Consignments to plan");
    expect(await within(tray).findByText("Paddock Produce")).toBeInTheDocument();
    expect(within(tray).getAllByText("8 ctn")).toHaveLength(2);
    expect(within(tray).getByText("From WMS delivery 0080034531")).toBeInTheDocument();

    const kim = await lane("Run R-0001");
    expect(within(kim).getByText("R-0001 · Kim")).toBeInTheDocument();
    expect(within(kim).getByText("3 stops")).toBeInTheDocument();
    expect(within(kim).getByRole("img", { name: "Weight 745 of 4,500 kg" })).toBeInTheDocument();

    const mel = await lane("Run R-0002");
    expect(within(mel).getByText("Over")).toBeInTheDocument();
    expect(within(mel).getByText("280 kg too heavy.")).toBeInTheDocument();
    expect(within(mel).getByText(/Move a stop to another run before printing the run sheet/)).toBeInTheDocument();
    expect(within(mel).getByRole("button", { name: /Leaves now/ })).toBeDisabled();

    const out = await lane("TMS027, unavailable");
    expect(within(out).getByText("Deactivated")).toBeInTheDocument();
    expect(screen.getByText(/1 truck free: TMS063/)).toBeInTheDocument();
  });

  it("puts a consignment on a run from the keyboard", async () => {
    const { calls } = board();
    renderApp("/plan");
    const user = userEvent.setup();
    await user.selectOptions(await screen.findByRole("combobox", { name: "Put Paddock Produce on a run" }), "1");
    await waitFor(() => expect(calls.find((c) => c.url === "/v1/runs/1/assign")?.body)
      .toEqual({ consignment_id: 31 }));
  });

  it("moves a stop down from the keyboard by sending the whole order", async () => {
    const { calls } = board();
    renderApp("/plan");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Move Hillside Bakery down" }));
    await waitFor(() => expect(calls.find((c) => c.url === "/v1/runs/1/reorder")?.body)
      .toEqual({ consignment_ids: [12, 11, 13], seen: [11, 12, 13] }));
    expect(screen.getByRole("button", { name: "Move Hillside Bakery up" })).toBeDisabled();
  });

  it("sends a stop back to the tray from the keyboard", async () => {
    const { calls } = board();
    renderApp("/plan");
    await userEvent.setup().selectOptions(
      await screen.findByRole("combobox", { name: "Move Two Rivers Vet to" }), "tray");
    await waitFor(() => expect(calls.find((c) => c.url === "/v1/runs/1/unassign")?.body)
      .toEqual({ consignment_id: 12 }));
  });

  it("drags a consignment from the tray onto a run, at the end", async () => {
    const { calls } = board();
    renderApp("/plan");
    const tray = await lane("Consignments to plan");
    const cardEl = (await within(tray).findByText("Kerbside Bikes")).closest(".con")!;
    const kim = await lane("Run R-0001");
    const data = { setData: vi.fn(), getData: vi.fn(), effectAllowed: "" };
    fireEvent.dragStart(cardEl, { dataTransfer: data });
    const over = createEvent.dragOver(kim, { dataTransfer: data });
    fireEvent(kim, over);
    expect(await within(kim).findByText("Drop here · Kerbside Bikes fits")).toBeInTheDocument();
    fireEvent.drop(kim, { dataTransfer: data });
    await waitFor(() => expect(calls.find((c) => c.url === "/v1/runs/1/assign")?.body)
      .toEqual({ consignment_id: 32, position: 4 }));
  });

  it("moves the stop that was dragged, even if the board was redrawn mid-drag", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const [a, b, c] = [card(11, "Hillside Bakery", "84"), card(12, "Two Rivers Vet", "41"),
                         card(13, "Ridge Auto Workshop", "620")];
      let stops = [a, b, c];
      const { calls } = board({
        "GET /v1/runs": () => json(200, { items: [run(1, "Kim", "TMS041", stops), MEL], total: 2 }),
      });
      renderApp("/plan");
      const kim = await lane("Run R-0001");
      const data = { setData: vi.fn(), getData: vi.fn(), effectAllowed: "" };
      fireEvent.dragStart((await within(kim).findByText("Hillside Bakery")).closest(".stop")!, { dataTransfer: data });
      // meanwhile someone else put Ridge Auto Workshop first, and the poll draws it
      stops = [c, a, b];
      await act(async () => { vi.advanceTimersByTime(20_000); });
      await waitFor(() => expect(within(kim).getAllByText(/^(Hillside Bakery|Ridge Auto Workshop)$/)[0])
        .toHaveTextContent("Ridge Auto Workshop"));
      fireEvent(kim, createEvent.dragOver(kim, { dataTransfer: data }));
      fireEvent.drop(kim, { dataTransfer: data });
      await waitFor(() => expect(calls.find((x) => x.url === "/v1/runs/1/reorder")?.body)
        .toEqual({ consignment_ids: [13, 12, 11], seen: [13, 11, 12] }));
    } finally {
      vi.useRealTimers();
    }
  });

  it("says plainly when someone else changed the run meanwhile, and shows it as it is now", async () => {
    const [a, b, c] = [card(11, "Hillside Bakery", "84"), card(12, "Two Rivers Vet", "41"),
                       card(13, "Ridge Auto Workshop", "620")];
    let stops = [a, b, c];
    const { calls } = board({
      "GET /v1/runs": () => json(200, { items: [run(1, "Kim", "TMS041", stops), MEL], total: 2 }),
      "POST /v1/runs/1/reorder": () => {
        stops = [c, a, b];      // someone else got there first
        return json(409, { code: "stale",
          detail: "R-0001 changed while you were looking; it has been reloaded, so try that again" });
      },
    });
    renderApp("/plan");
    const kim = await lane("Run R-0001");
    await userEvent.setup().click(await screen.findByRole("button", { name: "Move Two Rivers Vet up" }));
    await waitFor(() => expect(calls.find((x) => x.url === "/v1/runs/1/reorder")?.body)
      .toEqual({ consignment_ids: [12, 11, 13], seen: [11, 12, 13] }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Someone else changed this run. Here it is as it is now.");
    await waitFor(() => expect(within(kim).getAllByText(/^(Hillside Bakery|Ridge Auto Workshop)$/)[0])
      .toHaveTextContent("Ridge Auto Workshop"));
  });

  it("stays signed in when its session can't be refreshed for now, and says so", async () => {
    board({
      "POST /v1/runs/1/reorder": () => json(401, { detail: "session expired or invalid" }),
      "POST /v1/auth/refresh": () => new Response("<html>Bad gateway</html>", { status: 502 }),
    });
    renderApp("/plan");
    await userEvent.setup().click(await screen.findByRole("button", { name: "Move Two Rivers Vet up" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Can't reach Simple TMS right now.");
    expect(screen.getByRole("region", { name: "Run R-0001" })).toBeInTheDocument();
  });

  it("doesn't let a slow answer for the day before overwrite the day now shown", async () => {
    let first: string | null = null;
    let release = () => {};
    board({
      "GET /v1/runs": ({ url }) => {
        const day = new URL(url, "http://tms.test").searchParams.get("date");
        first ??= day;
        if (day === first) {
          return new Promise<Response>((done) => { release = () => done(json(200, { items: [KIM], total: 1 })); });
        }
        return json(200, { items: [MEL], total: 1 });
      },
    });
    renderApp("/plan");
    const pick = await screen.findByRole("combobox", { name: "Day" });
    await waitFor(() => expect(first).not.toBeNull());
    const other = [...(pick as HTMLSelectElement).options].find((o) => o.value !== first)!.value;
    await userEvent.setup().selectOptions(pick, other);
    expect(await lane("Run R-0002")).toBeInTheDocument();
    await act(async () => { release(); await new Promise((r) => setTimeout(r, 20)); });
    expect(screen.queryByRole("region", { name: "Run R-0001" })).not.toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Run R-0002" })).toBeInTheDocument();
  });

  it("prints every run sheet it can and says what it could not", async () => {
    const { calls } = board({
      "POST /v1/runs/2/print": () => json(409, { code: "over_capacity",
        detail: "R-0002: 280 kg too heavy. Move a stop to another run before printing the run sheet." }),
      "POST /v1/runs/1/print": () => json(202, { status: "pending" }),
    });
    renderApp("/plan");
    await userEvent.setup().click(await screen.findByRole("button", { name: "Print run sheets" }));
    expect(await screen.findByText(/Sent 1 run sheet to Platen\. R-0002: 280 kg too heavy/)).toBeInTheDocument();
    expect(calls.filter((c) => c.url.endsWith("/print")).map((c) => c.url)).toEqual(["/v1/runs/1/print", "/v1/runs/2/print"]);
  });

  it("asks before a run leaves, then departs it", async () => {
    const { calls } = board();
    const ask = vi.spyOn(window, "confirm").mockReturnValue(true);
    renderApp("/plan");
    await userEvent.setup().click(within(await lane("Run R-0001")).getByRole("button", { name: /Leaves now/ }));
    expect(ask).toHaveBeenCalledWith("R-0001 leaves now? Each consignment on it is marked shipped in Simple WMS.");
    await waitFor(() => expect(calls.some((c) => c.url === "/v1/runs/1/depart")).toBe(true));
    ask.mockRestore();
  });

  it("lets the keyboard look through a menu, and acts only on Enter", async () => {
    const { calls } = board();
    renderApp("/plan");
    const menu = await screen.findByRole("combobox", { name: "Put Paddock Produce on a run" });
    // on a closed menu, an arrow key (Windows, Linux) or type-ahead changes the value at once
    fireEvent.keyDown(menu, { key: "ArrowDown" });
    fireEvent.change(menu, { target: { value: "1" } });
    await new Promise((r) => setTimeout(r, 50));
    expect(calls.some((c) => c.url === "/v1/runs/1/assign")).toBe(false);
    expect(menu).toHaveValue("1");
    fireEvent.keyDown(menu, { key: "Enter" });
    await waitFor(() => expect(calls.find((c) => c.url === "/v1/runs/1/assign")?.body)
      .toEqual({ consignment_id: 31 }));
  });

  it("keeps the keyboard on what it moved", async () => {
    let stops = [card(11, "Hillside Bakery", "84"), card(12, "Two Rivers Vet", "41"), card(13, "Ridge Auto Workshop", "620")];
    let trayItems = [packed(31, "Paddock Produce", "Clunes")];
    const { calls } = board({
      "GET /v1/runs": () => json(200, { items: [run(1, "Kim", "TMS041", stops), MEL], total: 2 }),
      "GET /v1/consignments": () => json(200, { items: trayItems, total: trayItems.length }),
      "POST /v1/runs/1/reorder": ({ body }) => {
        const ids = (body as { consignment_ids: number[] }).consignment_ids;
        stops = ids.map((id) => stops.find((c) => c.id === id)!);
        return json(200, KIM);
      },
      "POST /v1/runs/1/assign": () => {
        trayItems = [];
        stops = [...stops, card(31, "Paddock Produce", "210")];
        return json(200, KIM);
      },
    });
    renderApp("/plan");
    const user = userEvent.setup();
    const kim = await lane("Run R-0001");
    await user.click(await within(kim).findByRole("button", { name: "Move Hillside Bakery down" }));
    await waitFor(() => expect(within(kim).getAllByText(/^(Hillside Bakery|Two Rivers Vet)$/)[0]).toHaveTextContent("Two Rivers Vet"));
    await waitFor(() => expect(screen.getByRole("button", { name: "Move Hillside Bakery down" })).toHaveFocus());

    // once it is last, down is off, so the keyboard lands on up
    await user.keyboard("{Enter}");
    await waitFor(() => expect(calls.filter((c) => c.url === "/v1/runs/1/reorder").at(-1)?.body)
      .toEqual({ consignment_ids: [12, 13, 11], seen: [12, 11, 13] }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Move Hillside Bakery up" })).toHaveFocus());

    // put on a run from the tray, the keyboard follows it to its stop
    const menu = screen.getByRole("combobox", { name: "Put Paddock Produce on a run" });
    menu.focus();
    await user.selectOptions(menu, "1");
    await waitFor(() => expect(within(kim).getByText("Paddock Produce").closest(".stop")).toHaveFocus());
  });

  it("shows what the driver recorded, and plans again what came back", async () => {
    const out = run(1, "Kim", "TMS041", [card(11, "Hillside Bakery", "84"), card(12, "Two Rivers Vet", "41"),
                                         card(13, "Ridge Auto Workshop", "620")],
                    { status: "done", departed_at: "2026-10-14T21:10:00Z", finished_at: "2026-10-15T05:12:00Z" });
    out.stops[0].outcome = "delivered";
    out.stops[1].outcome = "failed";
    out.stops[1].reason = "wrong_address";
    out.stops[2].outcome = "delivered";
    const back = { ...packed(12, "Two Rivers Vet", "Smythesdale"), status: "failed" };
    const { calls } = board({
      "GET /v1/runs": () => json(200, { items: [out, MEL], total: 2 }),
      "GET /v1/consignments": () => json(200, { items: [back], total: 1 }),
    });
    renderApp("/plan");
    const kim = await lane("Run R-0001");
    expect(within(kim).getByText("Done")).toBeInTheDocument();
    expect(within(kim).getAllByText("Delivered")).toHaveLength(2);
    expect(within(kim).getByText("Not delivered: wrong address")).toBeInTheDocument();
    expect(within(kim).getByText(/^Delivered 2 of 3 · 1 coming back · finished /)).toBeInTheDocument();
    const tray = await lane("Consignments to plan");
    expect(within(tray).getByText("Came back: not delivered")).toBeInTheDocument();
    expect(calls.find((c) => path(c.url) === "/v1/consignments")?.url).toContain("status=packed%2Cfailed");
  });

  it("finishes a run the driver left open, once every stop is done", async () => {
    const out = run(1, "Kim", "TMS041", [card(11, "Hillside Bakery", "84"), card(12, "Two Rivers Vet", "41")],
                    { status: "departed", departed_at: "2026-10-14T21:10:00Z" });
    out.stops[0].outcome = "delivered";
    const { calls } = board({ "GET /v1/runs": () => json(200, { items: [out], total: 1 }) });
    renderApp("/plan");
    const kim = await lane("Run R-0001");
    expect(within(kim).queryByRole("button", { name: "Finish run" })).not.toBeInTheDocument();

    out.stops[1].outcome = "failed";
    cleanup();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    renderApp("/plan");
    await userEvent.setup().click(await within(await lane("Run R-0001")).findByRole("button", { name: "Finish run" }));
    await waitFor(() => expect(calls.find((c) => c.url === "/v1/runs/1/finish")?.body)
      .toEqual({ message_id: expect.stringMatching(/^[0-9a-f-]{36}$/) }));
    expect(await screen.findByText("R-0001 is finished.")).toBeInTheDocument();
    confirm.mockRestore();
  });

  it("shows today the runs left open from an earlier day, for the office to finish", async () => {
    const yesterday = new Date(Date.now() - 86_400_000);
    const old = run(5, "Tom", "TMS063", [card(51, "Hillside Bakery", "84")],
                    { status: "departed", date: isoDay(yesterday), departed_at: "2026-10-13T21:10:00Z" });
    old.stops[0].outcome = "delivered";
    const { calls } = board({ "GET /v1/runs": () => json(200, { items: [old, KIM], total: 2 }) });
    renderApp("/plan");
    const user = userEvent.setup();
    // tomorrow is planned to start with; today has the ones left open
    await lane("Run R-0005");
    const asked = () => calls.filter((c) => path(c.url) === "/v1/runs").map((c) => new URLSearchParams(c.url.split("?")[1]));
    expect(asked()[0].get("left_open")).toBeNull();
    await user.selectOptions(screen.getByRole("combobox", { name: "Day" }), isoDay(new Date()));
    await waitFor(() => expect(asked().at(-1)?.get("left_open")).toBe("true"));
    const tom = await lane("Run R-0005");
    expect(within(tom).getByText(`Still open from ${dayLabel(yesterday)}`)).toHaveClass("p-amber");
    expect(within(tom).queryByText("Departed")).not.toBeInTheDocument();
    expect(within(tom).getByRole("button", { name: "Finish run" })).toBeInTheDocument();
  });

  describe("a truck that can't go (rule 3)", () => {
    const GROUNDED = { code: "vehicle_grounded",
                       message: "TMS041 is grounded: Brakes failed the pre-start. A fleet manager has to mark it fixed first." };
    const FLEET = [
      { id: 1, rego: "TMS041", type: "rigid", max_kg: "4500", max_m3: "22", active: true, blocked: GROUNDED },
      { id: 2, rego: "TMS052", type: "rigid", max_kg: "4500", max_m3: "22", active: true, blocked: null },
      { id: 3, rego: "TMS063", type: "van", max_kg: "1200", max_m3: "9", active: true, blocked: null },
      { id: 4, rego: "TMS018", type: "van", max_kg: "1200", max_m3: "9", active: true,
        blocked: { code: "vehicle_overdue", message: "TMS018's rego runs out on 10 Oct 2026. Record the new expiry in Fleet first." } },
    ];
    // the check that grounded it is never the run's pre-start (the API gives null)
    const grounded = { ...KIM, vehicle: { ...KIM.vehicle, blocked: GROUNDED }, prestart: null };
    const checked = { ...MEL, prestart: { id: 9, at: "2026-10-13T19:52:00Z", result: "failed" as const } };

    it("says so on its run's lane, takes nothing more on it, and gives the run another truck", async () => {
      const { calls } = board({
        "GET /v1/runs": () => json(200, { items: [grounded, checked], total: 2 }),
        "GET /v1/vehicles": () => json(200, { items: FLEET, total: 4 }),
      });
      renderApp("/plan");
      const kim = await lane("Run R-0001");
      expect(within(kim).getByText("Grounded")).toBeInTheDocument();
      expect(within(kim).getByText(GROUNDED.message)).toBeInTheDocument();
      expect(within(kim).getByText(/Give the run another truck, or move its stops/)).toBeInTheDocument();
      // its lane says why it can't go; it doesn't ask for a pre-start as well
      expect(within(kim).queryByText(/pre-start yet|^Pre-start /)).not.toBeInTheDocument();
      expect(within(await lane("Run R-0002")).getByText(/^Pre-start .*: an item failed\.$/)).toBeInTheDocument();
      expect(within(kim).getByRole("button", { name: /Leaves now/ })).toBeDisabled();
      expect(within(kim).getByRole("button", { name: /Print/ })).toBeDisabled();
      // the tray offers only the runs that can take stops
      const put = screen.getByRole("combobox", { name: "Put Paddock Produce on a run" });
      expect(within(put).queryByRole("option", { name: /R-0001/ })).not.toBeInTheDocument();
      // the overdue truck with no run waits with the ones that can't go; it isn't free
      expect(within(await lane("TMS018, unavailable")).getByText("Overdue")).toBeInTheDocument();
      expect(screen.getByText(/1 truck free: TMS063/)).toBeInTheDocument();
      await userEvent.setup().selectOptions(within(kim).getByRole("combobox", { name: "Give R-0001 another truck" }), "TMS063");
      await waitFor(() => expect(calls.find((c) => c.method === "PATCH")).toEqual(
        { method: "PATCH", url: "/v1/runs/1", body: { vehicle: "TMS063" } }));
    });

    it("asks for the trucks as they stand on the day being planned", async () => {
      const { calls } = board();
      renderApp("/plan");
      await lane("Run R-0001");
      expect(calls.find((c) => path(c.url) === "/v1/vehicles")!.url).toMatch(/^\/v1\/vehicles\?date=\d{4}-\d{2}-\d{2}$/);
      expect(within(await lane("Run R-0001")).getByText("No pre-start yet.")).toBeInTheDocument();
    });
  });
});
