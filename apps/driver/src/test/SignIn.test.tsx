import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { json, renderApp, stubApi } from "./harness";

const KIM = { kind: "driver", id: 3, name: "kim", display_name: "Kim", role: "driver", scopes: [] };

describe("signing in", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("signs in with the driver code and a PIN on the keypad", async () => {
    const { calls } = stubApi((_method, url) => {
      if (url === "/v1/auth/driver-login") return json(200, { token: "tms_d.x", expires_in: 43200, driver: KIM });
      if (url.startsWith("/v1/driver/runs")) return json(200, []);
      return null;
    });
    renderApp("/sign-in");
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Driver code"), "kim");
    for (const d of ["2", "4", "6", "8"]) await user.click(screen.getByRole("button", { name: d }));
    expect(screen.getByRole("img", { name: "PIN, 4 numbers entered" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByText("No run for you today.")).toBeInTheDocument();
    expect(calls[0].body).toEqual({ code: "kim", pin: "2468" });
    expect(screen.getByTestId("where")).toHaveTextContent("/");
  });

  it("says a wrong PIN is wrong, and clears it", async () => {
    stubApi(() => json(401, { detail: "wrong driver code or PIN", code: "wrong_pin" }));
    renderApp("/sign-in");
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Driver code"), "kim");
    for (const d of ["1", "1", "1", "1"]) await user.click(screen.getByRole("button", { name: d }));
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Wrong driver code or PIN.");
    await waitFor(() => expect(screen.getByRole("img", { name: "PIN, 0 numbers entered" })).toBeInTheDocument());
  });
});
