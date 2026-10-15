import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { truck, fleetDepot } from "./fleetDepot";
import { DISPATCHER, json } from "./harness";
import { renderApp } from "./harness";

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
