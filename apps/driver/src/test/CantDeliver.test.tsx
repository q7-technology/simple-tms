import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { json, onThePhone, renderApp, RUN, signedIn, stubApi } from "./harness";

describe("can't deliver", () => {
  beforeEach(() => { signedIn(); onThePhone(); });
  afterEach(() => vi.unstubAllGlobals());

  it("sends the reason and puts the cartons back on the truck", async () => {
    const { calls } = stubApi((method) => (method === "POST" ? json(200, { outcome: "failed" }) : json(200, RUN)));
    renderApp("/run/7/stop/32/cant");
    const user = userEvent.setup();
    expect(await screen.findByText(/2 cartons stay on the truck/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Back on the truck/ })).toBeDisabled();
    await user.click(screen.getByRole("radio", { name: /Closed/ }));
    await user.type(screen.getByLabelText(/Note/), "Rang the number, no answer.");
    await user.click(screen.getByRole("button", { name: /Back on the truck/ }));
    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent(/^\/run\/7$/));
    expect(await screen.findByText("Stop 2 is coming back to the depot.")).toBeInTheDocument();
    await waitFor(() => expect(calls.some((c) => c.url === "/v1/runs/7/stops/32/fail")).toBe(true));
    expect(calls.find((c) => c.url === "/v1/runs/7/stops/32/fail")!.body).toEqual({
      message_id: expect.any(String), at: expect.any(String), reason: "closed", note: "Rang the number, no answer." });
  });
});
