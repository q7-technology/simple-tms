import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { fleetDepot, VAN_LIST } from "./fleetDepot";
import { json, renderApp } from "./harness";

async function checklists() {
  const user = userEvent.setup();
  await user.click(await screen.findByRole("tab", { name: "Pre-start checklists" }));
  const panel = screen.getByRole("tabpanel", { name: "Pre-start checklists" });
  const list = await within(panel).findByRole("region", { name: "Van checklist" });
  return { user, panel, list };
}

const labels = (list: HTMLElement) => within(list).getAllByRole("textbox").map((i) => (i as HTMLInputElement).value);

describe("Fleet: pre-start checklists", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("shows each type's lines in order, the critical ones ticked", async () => {
    fleetDepot();
    renderApp("/fleet");
    const { list } = await checklists();
    expect(labels(list)).toEqual(["Tyres", "Lights", "Brakes", "Mirrors", "Load restraints", "Damage"]);
    expect(within(list).getByRole("checkbox", { name: "Critical (Lights)" })).toBeChecked();
    expect(within(list).getByRole("checkbox", { name: "Critical (Tyres)" })).not.toBeChecked();
    expect(within(list).getByRole("button", { name: "Save the van checklist" })).toBeDisabled();
    expect(within(list).getByRole("button", { name: "Move Tyres up" })).toBeDisabled();
  });

  it("adds, moves and takes out lines, then sends the whole list", async () => {
    const { calls } = fleetDepot({
      "PUT /v1/checklists/van": (c) => json(200, (c.body as { items: { label: string; critical: boolean }[] }).items
        .map((l, i) => ({ id: 100 + i, ...l }))),
    });
    renderApp("/fleet");
    const { user, panel, list } = await checklists();
    await user.click(within(list).getByRole("button", { name: "Add a line" }));
    await waitFor(() => expect(within(list).getByRole("textbox", { name: "Line 7" })).toHaveFocus());
    await user.keyboard("Tail lift");
    await user.click(within(list).getByRole("checkbox", { name: "Critical (Tail lift)" }));
    await user.click(within(list).getByRole("button", { name: "Move Tail lift up" }));
    await waitFor(() => expect(within(list).getByRole("button", { name: "Move Tail lift up" })).toHaveFocus());
    await user.click(within(list).getByRole("button", { name: "Take out Mirrors" }));
    expect(labels(list)).toEqual(["Tyres", "Lights", "Brakes", "Load restraints", "Tail lift", "Damage"]);
    expect(within(panel).getByRole("button", { name: /^Van/ })).toHaveTextContent("not saved");

    await user.click(within(list).getByRole("button", { name: "Save the van checklist" }));
    expect(await screen.findByText("The van checklist is saved: 6 lines.")).toHaveAttribute("role", "status");
    expect(calls.find((c) => c.method === "PUT")!.body).toEqual({ items: [
      { label: "Tyres", critical: false }, { label: "Lights", critical: true }, { label: "Brakes", critical: true },
      { label: "Load restraints", critical: false }, { label: "Tail lift", critical: true }, { label: "Damage", critical: false }] });
    expect(within(list).getByRole("button", { name: "Save the van checklist" })).toBeDisabled();
  });

  it("keeps each type's changes while another is looked at, and puts one back", async () => {
    fleetDepot();
    renderApp("/fleet");
    const { user, panel, list } = await checklists();
    await user.click(within(list).getByRole("button", { name: "Take out Damage" }));
    await user.click(within(panel).getByRole("button", { name: "Rigid" }));
    const rigid = within(panel).getByRole("region", { name: "Rigid checklist" });
    expect(labels(rigid)).toHaveLength(6);
    await user.click(within(panel).getByRole("button", { name: /^Van/ }));
    const van = within(panel).getByRole("region", { name: "Van checklist" });
    expect(labels(van)).toHaveLength(5);
    await user.click(within(van).getByRole("button", { name: "Put back as it was" }));
    expect(labels(van)).toEqual(VAN_LIST.map((l) => l.label));
  });

  it("says beside a line what is wrong with it, and sends nothing", async () => {
    const { calls } = fleetDepot();
    renderApp("/fleet");
    const { user, list } = await checklists();
    await user.clear(within(list).getByRole("textbox", { name: "Line 4" }));
    await user.type(within(list).getByRole("textbox", { name: "Line 4" }), "tyres");
    await user.click(within(list).getByRole("button", { name: "Add a line" }));
    await user.click(within(list).getByRole("button", { name: "Save the van checklist" }));
    expect(within(list).getByRole("textbox", { name: "Line 4" })).toHaveAccessibleDescription("Line 1 says this already.");
    expect(within(list).getByRole("textbox", { name: "Line 7" })).toHaveAccessibleDescription("Type what to check, or take the line out.");
    await waitFor(() => expect(within(list).getByRole("textbox", { name: "Line 4" })).toHaveFocus());
    expect(calls.some((c) => c.method === "PUT")).toBe(false);
  });
});
