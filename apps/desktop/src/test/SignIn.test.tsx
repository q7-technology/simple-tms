import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { api } from "../api/client";
import { AppRoutes } from "../App";
import { AuthProvider } from "../auth/AuthContext";
import { json } from "./harness";

function signedOut() {
  api.setSession(null);
  return render(<MemoryRouter initialEntries={["/"]}><AuthProvider><AppRoutes /></AuthProvider></MemoryRouter>);
}

describe("sign in", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("sends someone with no session to sign in, then to the map", async () => {
    const fetchMock = vi.fn(async (url: string) => {
      if (url === "/v1/auth/login") return json(200, { token: "t", refresh_token: "r", expires_in: 900 });
      if (url === "/v1/auth/me") return json(200, { kind: "user", id: 1, name: "dee", display_name: "Dee Dispatch",
                                                    role: "dispatcher", scopes: [] });
      if (url.startsWith("/v1/consignments")) return json(200, { items: [], total: 3 });
      return json(404, {});
    });
    vi.stubGlobal("fetch", fetchMock);
    signedOut();
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Username"), "dee");
    await user.type(screen.getByLabelText("Password"), "correct horse battery");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("navigation", { name: "Main drawers" })).toBeInTheDocument();
    const login = fetchMock.mock.calls.find((c) => c[0] === "/v1/auth/login") as unknown as [string, RequestInit];
    expect(JSON.parse(login[1].body as string)).toEqual({ username: "dee", password: "correct horse battery" });
  });

  it("says how many tries are left after a wrong password", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json(401, { detail: "wrong username or password",
                                                         code: "wrong_password", tries_left: 3 })));
    signedOut();
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Username"), "dee");
    await user.type(screen.getByLabelText("Password"), "nope");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Wrong username or password. 3 tries left before the account locks.");
  });
});
