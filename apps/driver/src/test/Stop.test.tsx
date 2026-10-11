import { fireEvent, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { onThePhone, renderApp, signedIn, stubApi } from "./harness";

describe("a stop", () => {
  beforeEach(() => { signedIn(); onThePhone(); stubApi(); });
  afterEach(() => vi.unstubAllGlobals());

  it("shows the address, a maps link and the cartons to drop", async () => {
    renderApp("/run/7/stop/31");
    expect(await screen.findByText("Hillside Bakery")).toBeInTheDocument();
    expect(screen.getByText("12 Sturt St, Ballarat VIC 3350")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open in Maps" })).toHaveAttribute(
      "href", "https://www.google.com/maps/search/?api=1&query=12%20Sturt%20St%2C%20Ballarat%20VIC%203350%2C%20Australia");
    expect(screen.getByRole("checkbox", { name: "008003451 · 1 of 2" })).toHaveAttribute("aria-checked", "false");
    expect(screen.getByRole("link", { name: "Call 03 5300 0000" })).toHaveAttribute("href", "tel:0353000000");
    expect(screen.getByRole("button", { name: "Delivered" })).toBeDisabled();
  });

  it("is delivered only once every carton is scanned or ticked", async () => {
    renderApp("/run/7/stop/31");
    const user = userEvent.setup();
    // a wedge scanner types into the scan field and presses Enter
    const field = await screen.findByLabelText("Carton label");
    await user.type(field, "(00)093123450000000101{Enter}");
    expect(await screen.findByText("Carton 1 of 2 scanned.")).toBeInTheDocument();
    expect(screen.getByText("Scanned")).toBeInTheDocument();
    await user.click(screen.getByRole("checkbox", { name: "008003451 · 2 of 2" }));
    expect(screen.getByText("Ticked")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Delivered" })).toHaveAttribute("href", "/run/7/stop/31/proof");
  });

  it("catches a carton for another stop", async () => {
    renderApp("/run/7/stop/31");
    const field = await screen.findByLabelText("Carton label");
    fireEvent.change(field, { target: { value: "093123450000000201" } });
    fireEvent.keyDown(field, { key: "Enter" });
    expect(await screen.findByText("That carton is for stop 2 · Two Rivers Vet. It stays on the truck."))
      .toBeInTheDocument();
  });
});
