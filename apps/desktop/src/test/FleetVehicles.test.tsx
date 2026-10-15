import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { fleetDepot, truck } from "./fleetDepot";
import { DISPATCHER, json, renderApp } from "./harness";

describe("Fleet: adding a vehicle", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("is for a fleet manager: a dispatcher sees no Add a vehicle", async () => {
    fleetDepot({}, DISPATCHER);
    renderApp("/fleet");
    await screen.findByRole("button", { name: "Show TMS041" });
    expect(screen.queryByRole("button", { name: "Add a vehicle" })).not.toBeInTheDocument();
  });

  it("adds one, its rego as the API keeps it and its limits as typed", async () => {
    let added = false;
    const made = truck("TMS050", { service_every_km: null });
    const { calls } = fleetDepot({
      "POST /v1/vehicles": () => { added = true; return json(201, made); },
      "GET /v1/vehicles": () => json(200, { items: added ? [truck("TMS041"), made] : [truck("TMS041")], total: 1 }),
    });
    renderApp("/fleet");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Add a vehicle" }));
    const form = screen.getByRole("form", { name: "Add a vehicle" });
    expect(within(form).getByLabelText("Rego")).toHaveFocus();
    await user.type(within(form).getByLabelText("Rego"), " tms 050");
    await user.selectOptions(within(form).getByLabelText("Type"), "rigid");
    await user.type(within(form).getByLabelText("Most it carries (kg)"), "4,500");
    await user.type(within(form).getByLabelText("Most it carries (m³)"), "22.50");
    await user.type(within(form).getByLabelText("Service every (months)"), "6");
    await user.click(within(form).getByRole("button", { name: "Add the vehicle" }));

    expect(await screen.findByText("TMS050 is added. Record its rego expiry and last service from its row.")).toHaveAttribute("role", "status");
    expect(calls.find((c) => c.method === "POST" && c.url === "/v1/vehicles")!.body).toEqual({
      rego: "TMS050", type: "rigid", max_kg: "4500", max_m3: "22.5", service_every_months: 6, service_every_km: null });
    expect(screen.queryByRole("form", { name: "Add a vehicle" })).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: "Show TMS050" })).toHaveAttribute("aria-pressed", "true"));
    expect(screen.getByRole("button", { name: "Add a vehicle" })).toHaveFocus();
  });

  it("says beside each field what is wrong, sends nothing, and puts the focus on the first", async () => {
    const { calls } = fleetDepot();
    renderApp("/fleet");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Add a vehicle" }));
    const form = screen.getByRole("form", { name: "Add a vehicle" });
    await user.type(within(form).getByLabelText("Rego"), "tms041");
    await user.type(within(form).getByLabelText("Most it carries (kg)"), "1200.1234");
    await user.type(within(form).getByLabelText("Service every (km)"), "0");
    await user.click(within(form).getByRole("button", { name: "Add the vehicle" }));

    const rego = within(form).getByLabelText("Rego");
    expect(rego).toHaveAttribute("aria-invalid", "true");
    expect(rego).toHaveAccessibleDescription("As on the plate. Spaces don't count. TMS041 is already on file.");
    expect(within(form).getByLabelText("Most it carries (kg)")).toHaveAccessibleDescription("At most 3 decimal places.");
    expect(within(form).getByLabelText("Most it carries (m³)")).toHaveAccessibleDescription("Give the most it carries, in m³.");
    expect(within(form).getByLabelText("Service every (km)")).toHaveAccessibleDescription("From 1 to 1,000,000 km, or leave it blank.");
    expect(within(form).getByLabelText("Service every (months)")).not.toHaveAttribute("aria-invalid");
    await waitFor(() => expect(rego).toHaveFocus());
    expect(calls.some((c) => c.method === "POST")).toBe(false);
  });

  it("shows the API's own refusal beside its field", async () => {
    fleetDepot({ "POST /v1/vehicles": () => json(422, { errors: [{ field: "rego", message: "vehicle TMS050 already exists" }] }) });
    renderApp("/fleet");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Add a vehicle" }));
    const form = screen.getByRole("form", { name: "Add a vehicle" });
    await user.type(within(form).getByLabelText("Rego"), "TMS050");
    await user.type(within(form).getByLabelText("Most it carries (kg)"), "1200");
    await user.type(within(form).getByLabelText("Most it carries (m³)"), "8.5");
    await user.click(within(form).getByRole("button", { name: "Add the vehicle" }));
    await waitFor(() => expect(within(form).getByLabelText("Rego")).toHaveAccessibleDescription(
      "As on the plate. Spaces don't count. vehicle TMS050 already exists"));
  });
});

describe("Fleet: changing a vehicle", () => {
  afterEach(() => vi.unstubAllGlobals());

  async function open(rego: string) {
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: `Show ${rego}` }));
    return { user, aside: screen.getByRole("complementary", { name: "Pre-start check record" }) };
  }

  it("shows a dispatcher what it carries, with nothing to change", async () => {
    fleetDepot({}, DISPATCHER);
    renderApp("/fleet");
    const { aside } = await open("TMS041");
    expect(within(aside).queryByRole("button", { name: "Change type, limits or service" })).not.toBeInTheDocument();
    expect(within(aside).queryByRole("button", { name: "Deactivate" })).not.toBeInTheDocument();
  });

  it("changes its limits and clears a service interval left blank", async () => {
    const { calls } = fleetDepot({ "PATCH /v1/vehicles/TMS041": () => json(200, truck("TMS041")) });
    renderApp("/fleet");
    const { user, aside } = await open("TMS041");
    expect(aside).toHaveTextContent("Rigid · up to 4,500 kg and 22 m³ · serviced every 6 months or 15,000 km");
    await user.click(within(aside).getByRole("button", { name: "Change type, limits or service" }));
    const form = within(aside).getByRole("form", { name: "Change TMS041" });
    expect(within(form).queryByLabelText("Rego")).not.toBeInTheDocument();
    expect(within(form).getByLabelText("Most it carries (kg)")).toHaveValue("4500");
    await user.clear(within(form).getByLabelText("Most it carries (kg)"));
    await user.type(within(form).getByLabelText("Most it carries (kg)"), "4750.5");
    await user.clear(within(form).getByLabelText("Service every (km)"));
    await user.click(within(form).getByRole("button", { name: "Save changes" }));
    expect(await screen.findByText("TMS041 is changed.")).toHaveAttribute("role", "status");
    expect(calls.find((c) => c.method === "PATCH")!.body).toEqual({
      type: "rigid", max_kg: "4750.5", max_m3: "22", service_every_months: 6, service_every_km: null });
  });

  it("says why an overdue truck's schedule can't be eased", async () => {
    fleetDepot({ "PATCH /v1/vehicles/TMS041": () => json(409, { code: "vehicle_overdue",
      detail: "TMS041's service is overdue. Record the service first: its schedule can't be eased or its type changed while it is overdue." }) });
    renderApp("/fleet");
    const { user, aside } = await open("TMS041");
    await user.click(within(aside).getByRole("button", { name: "Change type, limits or service" }));
    const form = within(aside).getByRole("form", { name: "Change TMS041" });
    await user.type(within(form).getByLabelText("Service every (months)"), "2");
    await user.click(within(form).getByRole("button", { name: "Save changes" }));
    expect(await within(form).findByRole("alert")).toHaveTextContent("TMS041's service is overdue. Record the service first");
  });

  it("deactivates one after asking, and says which run holds it when the API refuses", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    let off = false;
    const { calls } = fleetDepot({
      "POST /v1/vehicles/TMS018/deactivate": () => { off = true; return json(200, truck("TMS018", { active: false })); },
      "POST /v1/vehicles/TMS041/deactivate": () => json(409, { code: "on_a_run",
        detail: "TMS041 is on R-0001 (planned for 16 Oct 2026). Give that run another truck, or cancel it, first." }),
      "GET /v1/vehicles": () => json(200, { items: [truck("TMS041"), truck("TMS018", { active: !off })], total: 2 }),
    });
    renderApp("/fleet");
    let { user, aside } = await open("TMS041");
    await user.click(within(aside).getByRole("button", { name: "Deactivate" }));
    expect(confirm).toHaveBeenCalledWith("Deactivate TMS041? It can't take a run until it is reactivated. Its checks and history stay.");
    const refused = "TMS041 is on R-0001 (planned for 16 Oct 2026). Give that run another truck, or cancel it, first.";
    // beside the button, not at the top of the record
    const button = within(aside).getByRole("button", { name: "Deactivate" });
    expect(await within(button.closest(".acts") as HTMLElement).findByRole("alert")).toHaveTextContent(refused);
    expect(button).toHaveAccessibleDescription(refused);

    ({ user, aside } = await open("TMS018"));
    await user.click(within(aside).getByRole("button", { name: "Deactivate" }));
    expect(await screen.findByText("TMS018 is deactivated.")).toBeInTheDocument();
    aside = screen.getByRole("complementary", { name: "Pre-start check record" });
    expect(await within(aside).findByRole("button", { name: "Reactivate" })).toBeInTheDocument();
    expect(within(aside).getByText("Deactivated")).toBeInTheDocument();
    expect(calls.filter((c) => c.method === "POST").map((c) => c.url)).toEqual(
      ["/v1/vehicles/TMS041/deactivate", "/v1/vehicles/TMS018/deactivate"]);
    confirm.mockRestore();
  });
});
