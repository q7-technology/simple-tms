import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { fleetDepot, person } from "./fleetDepot";
import { DISPATCHER, json, renderApp } from "./harness";

async function driversTab() {
  const user = userEvent.setup();
  await user.click(await screen.findByRole("tab", { name: "Drivers" }));
  const panel = screen.getByRole("tabpanel", { name: "Drivers" });
  await within(panel).findByRole("table");
  return { user, panel };
}

describe("Fleet: drivers", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("lists the drivers and who is locked out for a dispatcher, with nothing to change", async () => {
    fleetDepot({}, DISPATCHER);
    renderApp("/fleet");
    const { panel } = await driversTab();
    expect(screen.getByRole("tab", { name: "Drivers" })).toHaveAttribute("aria-selected", "true");
    const rows = within(panel).getAllByRole("row");
    expect(rows[1]).toHaveTextContent("kimKimActive");
    expect(rows[2]).toHaveTextContent("tomTomLocked");
    expect(within(panel).queryByRole("button", { name: "Add a driver" })).not.toBeInTheDocument();
    expect(within(panel).queryByRole("complementary")).not.toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "Pre-start checklists" })).not.toBeInTheDocument();
  });

  it("adds a driver, the code kept lower case", async () => {
    let added = false;
    const { calls } = fleetDepot({
      "POST /v1/drivers": () => { added = true; return json(201, person("sam-02", "Sam")); },
      "GET /v1/drivers": () => json(200, { items: [person("kim", "Kim"), ...(added ? [person("sam-02", "Sam")] : [])], total: 1 }),
    });
    renderApp("/fleet");
    const { user, panel } = await driversTab();
    await user.click(within(panel).getByRole("button", { name: "Add a driver" }));
    const form = within(panel).getByRole("form", { name: "Add a driver" });
    expect(within(form).getByLabelText("Code")).toHaveFocus();
    await user.type(within(form).getByLabelText("Code"), " SAM-02 ");
    await user.type(within(form).getByLabelText("Name"), "Sam");
    await user.type(within(form).getByLabelText("PIN"), "1357");
    await user.click(within(form).getByRole("button", { name: "Add the driver" }));
    expect(await screen.findByText("Sam (sam-02) is added. They sign in with sam-02 and the PIN you gave.")).toHaveAttribute("role", "status");
    expect(calls.find((c) => c.method === "POST")!.body).toEqual({ code: "sam-02", name: "Sam", pin: "1357" });
    await waitFor(() => expect(within(panel).getByRole("button", { name: "Show Sam (sam-02)" })).toHaveAttribute("aria-pressed", "true"));
  });

  it("refuses a code already taken and a short PIN beside each field", async () => {
    const { calls } = fleetDepot();
    renderApp("/fleet");
    const { user, panel } = await driversTab();
    await user.click(within(panel).getByRole("button", { name: "Add a driver" }));
    const form = within(panel).getByRole("form", { name: "Add a driver" });
    await user.type(within(form).getByLabelText("Code"), "KIM");
    await user.type(within(form).getByLabelText("PIN"), "12");
    await user.click(within(form).getByRole("button", { name: "Add the driver" }));
    expect(within(form).getByLabelText("Code")).toHaveAccessibleDescription("What they sign in with, in any case. Kept lower case. kim is already Kim's.");
    expect(within(form).getByLabelText("Name")).toHaveAccessibleDescription("Type the name.");
    expect(within(form).getByLabelText("PIN")).toHaveAccessibleDescription("4 to 8 digits. Tell the driver: it isn't shown again. A PIN is 4 to 8 digits.");
    expect(calls.some((c) => c.method === "POST")).toBe(false);
  });

  it("opens on a locked driver, and unlocks them or gives them a new PIN", async () => {
    const { calls } = fleetDepot({
      "POST /v1/drivers/tom/unlock": () => json(200, person("tom", "Tom")),
      "PATCH /v1/drivers/tom": () => json(200, person("tom", "Tom")),
    });
    renderApp("/fleet");
    const { user, panel } = await driversTab();
    const aside = await within(panel).findByRole("complementary", { name: "Driver tom" });
    expect(aside).toHaveTextContent("Locked after 5 wrong PINs.");
    await user.click(within(aside).getByRole("button", { name: "Unlock" }));
    expect(await screen.findByText("Tom is unlocked.")).toBeInTheDocument();

    const pin = within(aside).getByRole("form", { name: "A new PIN" });
    await user.type(within(pin).getByLabelText("New PIN"), "86420");
    await user.click(within(pin).getByRole("button", { name: "Set the PIN" }));
    expect(await screen.findByText("Tom has a new PIN, and is unlocked.")).toBeInTheDocument();
    expect(calls.filter((c) => c.method !== "GET").map((c) => [c.method, c.url, c.body])).toEqual([
      ["POST", "/v1/drivers/tom/unlock", {}], ["PATCH", "/v1/drivers/tom", { pin: "86420" }]]);
    expect(within(pin).getByLabelText("New PIN")).toHaveValue("");
  });

  it("renames a driver, and says which run holds one the API won't deactivate", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    const { calls } = fleetDepot({
      "PATCH /v1/drivers/kim": () => json(200, person("kim", "Kim Lee")),
      "POST /v1/drivers/kim/deactivate": () => json(409, { code: "on_a_run",
        detail: "Kim (kim) is on R-0001 (on the road now). Deactivate them once that run is finished." }),
    });
    renderApp("/fleet");
    const { user, panel } = await driversTab();
    await user.click(within(panel).getByRole("button", { name: "Show Kim (kim)" }));
    const aside = within(panel).getByRole("complementary", { name: "Driver kim" });
    const name = within(aside).getByRole("form", { name: "Name" });
    await user.clear(within(name).getByLabelText("Name"));
    await user.type(within(name).getByLabelText("Name"), "Kim Lee");
    await user.click(within(name).getByRole("button", { name: "Save name" }));
    expect(await screen.findByText("kim's name is now Kim Lee.")).toBeInTheDocument();
    expect(calls.find((c) => c.method === "PATCH")!.body).toEqual({ name: "Kim Lee" });

    await user.click(within(aside).getByRole("button", { name: "Deactivate" }));
    expect(confirm).toHaveBeenCalledWith("Deactivate Kim (kim)? They can't sign in until they are reactivated. Their runs and deliveries stay theirs.");
    const refused = "Kim (kim) is on R-0001 (on the road now). Deactivate them once that run is finished.";
    const off = within(aside).getByRole("button", { name: "Deactivate" });
    expect(await within(off.closest(".acts") as HTMLElement).findByRole("alert")).toHaveTextContent(refused);
    expect(off).toHaveAccessibleDescription(refused);
    confirm.mockRestore();
  });
});
