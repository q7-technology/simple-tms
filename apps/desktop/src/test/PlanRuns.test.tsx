import { act, createEvent, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { Card, Consignment, Run } from "../api/types";
import { json, path, renderApp, stubApi, type Call } from "./harness";

const card = (id: number, customer: string, weight_kg: string, volume_m3 = "1"): Card => ({
  id, wms_ref: `00800345${id}`, customer, suburb: "Ballarat", status: "assigned", packages: 3, cartons: 3,
  weight_kg, volume_m3,
});

const run = (id: number, driver: string, rego: string, stops: Card[], over: Partial<Run> = {}): Run => ({
  id, run_no: `R-000${id}`, date: "2026-10-14", status: "planned",
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

function board(answers: Record<string, (call: Call) => Response | null> = {}) {
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
});

