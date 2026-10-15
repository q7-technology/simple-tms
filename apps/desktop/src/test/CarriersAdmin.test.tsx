import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { Carrier } from "../api/types";
import { PLAINS, RIDGELINE, VALLEY } from "./bay";
import { DISPATCHER, json, MANAGER, path, renderApp, stubApi, type Call } from "./harness";

const carrier = (id: number, name: string, over: Partial<Carrier> = {}): Carrier => ({
  id, name, account: null, pickup_time: null, adapter: "manual", active: true, created_at: "", ...over,
});
const RX = carrier(1, RIDGELINE, { account: "RX-4410", pickup_time: "15:30" });
const PP = carrier(3, PLAINS, { active: false });

function office(answers: Record<string, (c: Call) => Response> = {}, who: object = MANAGER, all: () => Carrier[] = () => [RX, PP]) {
  return stubApi((method, url, body) => {
    const key = `${method} ${path(url)}`;
    if (answers[key]) return answers[key]({ method, url, body });
    if (key === "GET /v1/manifests") return json(200, { items: [], total: 0 });
    if (key === "GET /v1/carriers") {
      const items = url.includes("active=true") ? all().filter((c) => c.active) : all();
      return json(200, { items, total: items.length });
    }
    return null;
  }, who);
}

const yours = () => screen.findByRole("region", { name: "Your carriers" });

describe("Carriers: your carriers", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("is for a fleet manager: a dispatcher doesn't see it", async () => {
    office({}, DISPATCHER);
    renderApp("/carriers");
    expect(await screen.findByText(/Nothing is waiting for a carrier/)).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Your carriers" })).not.toBeInTheDocument();
  });

  it("lists every carrier, retired ones too, with how each is booked", async () => {
    office();
    renderApp("/carriers");
    const list = await yours();
    await within(list).findByText(RIDGELINE);
    const rows = within(list).getAllByRole("row");
    expect(rows[1]).toHaveTextContent(`${RIDGELINE}RX-44103:30 pmmanualActive`);
    expect(rows[2]).toHaveTextContent(`${PLAINS}NoneNot setmanualRetired`);
    expect(within(list).getByRole("button", { name: `Bring back ${PLAINS}` })).toBeInTheDocument();
  });

  it("adds a carrier, and the con note box's list gets it", async () => {
    let added = false;
    const { calls } = office({ "POST /v1/carriers": () => { added = true; return json(201, carrier(2, VALLEY)); } }, MANAGER,
                             () => (added ? [RX, carrier(2, VALLEY), PP] : [RX, PP]));
    renderApp("/carriers");
    const user = userEvent.setup();
    const list = await yours();
    await user.click(within(list).getByRole("button", { name: "Add a carrier" }));
    const form = within(list).getByRole("form", { name: "Add a carrier" });
    expect(within(form).getByLabelText("Name")).toHaveFocus();
    expect(form).toHaveTextContent("Booked: manual, by hand");
    await user.type(within(form).getByLabelText("Name"), ` ${VALLEY} `);
    await user.type(within(form).getByLabelText("Usually calls at"), "14:00");
    await user.click(within(form).getByRole("button", { name: "Add the carrier" }));
    expect(await screen.findByText(`${VALLEY} is added. Send consignments with it from Plan runs.`)).toHaveAttribute("role", "status");
    expect(calls.find((c) => c.method === "POST")!.body).toEqual({ name: VALLEY, account: null, pickup_time: "14:00" });
    // the drawer's own list of active carriers is asked for again
    await waitFor(() => expect(calls.filter((c) => c.url.startsWith("/v1/carriers?active=true")).length).toBeGreaterThan(1));
    expect(await within(list).findByText(VALLEY)).toBeInTheDocument();
  });

  it("refuses a name already on file whatever its case, and Own fleet", async () => {
    const { calls } = office();
    renderApp("/carriers");
    const user = userEvent.setup();
    const list = await yours();
    await user.click(within(list).getByRole("button", { name: "Add a carrier" }));
    const form = within(list).getByRole("form", { name: "Add a carrier" });
    await user.type(within(form).getByLabelText("Name"), RIDGELINE.toUpperCase());
    await user.click(within(form).getByRole("button", { name: "Add the carrier" }));
    expect(within(form).getByLabelText("Name")).toHaveAccessibleDescription(
      `What Simple WMS is told the freight went with. ${RIDGELINE} is already on file.`);
    await user.clear(within(form).getByLabelText("Name"));
    await user.type(within(form).getByLabelText("Name"), "own fleet");
    await user.click(within(form).getByRole("button", { name: "Add the carrier" }));
    expect(within(form).getByLabelText("Name")).toHaveAccessibleDescription(
      "What Simple WMS is told the freight went with. Own fleet is what Simple WMS is told for your own trucks: give the carrier its own name.");
    expect(calls.some((c) => c.method === "POST")).toBe(false);
  });

  it("renames one, clearing its account, and says Simple WMS gets the new name from the next collection", async () => {
    const { calls } = office({ "PATCH /v1/carriers/1": () => json(200, RX) });
    renderApp("/carriers");
    const user = userEvent.setup();
    const list = await yours();
    await user.click(await within(list).findByRole("button", { name: `Change ${RIDGELINE}` }));
    const form = within(list).getByRole("form", { name: `Change ${RIDGELINE}` });
    expect(within(form).getByLabelText("Usually calls at")).toHaveValue("15:30");
    await user.clear(within(form).getByLabelText("Name"));
    await user.type(within(form).getByLabelText("Name"), "Ridgeline Freight (sample)");
    await user.clear(within(form).getByLabelText("Your account with them"));
    await user.click(within(form).getByRole("button", { name: "Save changes" }));
    expect(await screen.findByText(`${RIDGELINE} is now Ridgeline Freight (sample). Simple WMS is told the new name from its next collection.`))
      .toBeInTheDocument();
    expect(calls.find((c) => c.method === "PATCH")!.body).toEqual({ name: "Ridgeline Freight (sample)", account: null, pickup_time: "15:30" });
  });

  it("retires one after asking, and brings one back", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    const { calls } = office({
      "POST /v1/carriers/1/deactivate": () => json(200, { ...RX, active: false }),
      "POST /v1/carriers/3/reactivate": () => json(200, { ...PP, active: true }),
    });
    renderApp("/carriers");
    const user = userEvent.setup();
    const list = await yours();
    await user.click(await within(list).findByRole("button", { name: `Retire ${RIDGELINE}` }));
    expect(confirm).toHaveBeenCalledWith(`Retire ${RIDGELINE}? It takes no new bookings. What is on its bay stays, to be collected.`);
    expect(await screen.findByText(`${RIDGELINE} is retired.`)).toBeInTheDocument();
    await user.click(within(list).getByRole("button", { name: `Bring back ${PLAINS}` }));
    expect(await screen.findByText(`${PLAINS} is back, and takes bookings.`)).toBeInTheDocument();
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(calls.filter((c) => c.method === "POST").map((c) => c.url)).toEqual(["/v1/carriers/1/deactivate", "/v1/carriers/3/reactivate"]);
    confirm.mockRestore();
  });
});
