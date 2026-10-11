import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { json, path, renderApp, stubApi } from "./harness";

describe("the map", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("shows the day's numbers and opens drawers from the bar", async () => {
    stubApi((_m, url) => {
      if (path(url) !== "/v1/consignments") return null;
      const status = new URLSearchParams(url.split("?")[1]).get("status");
      return json(200, { items: [], total: { packed: 14, departed: 21, delivered: 0 }[status ?? ""] ?? 0 });
    });
    renderApp("/");
    const nav = await screen.findByRole("navigation", { name: "Main drawers" });
    await waitFor(() => expect(nav).toHaveTextContent("Plan runs14"));
    expect(screen.getByText("21")).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("link", { name: /Fleet/ }));
    expect(screen.getByTestId("where")).toHaveTextContent("/fleet");
    expect(screen.getByText(/This screen arrives with build step 4/)).toBeInTheDocument();
  });

  it("signs out from the account button", async () => {
    const { calls } = stubApi(() => json(200, { items: [], total: 0 }));
    renderApp("/");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Account: Dee Dispatch" }));
    await user.click(screen.getByRole("button", { name: "Sign out" }));
    expect(await screen.findByRole("button", { name: "Sign in" })).toBeInTheDocument();
    expect(calls.some((c) => c.url === "/v1/auth/logout")).toBe(true);
  });
});
