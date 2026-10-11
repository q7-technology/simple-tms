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

  function reopened() {
    api.setSession(null);
    window.localStorage.setItem("tms.refresh", "r1");   // from before the reload
    return render(<MemoryRouter initialEntries={["/"]}><AuthProvider><AppRoutes /></AuthProvider></MemoryRouter>);
  }

  it("keeps the session when Simple TMS can't be reached on opening, and says so", async () => {
    let up = false;
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (!up) return new Response("<html>Bad gateway</html>", { status: 502 });
      if (url === "/v1/auth/refresh") return json(200, { token: "t", refresh_token: "r2", expires_in: 900 });
      if (url === "/v1/auth/me") return json(200, { kind: "user", id: 1, name: "dee", display_name: "Dee Dispatch",
                                                    role: "dispatcher", scopes: [] });
      if (url.startsWith("/v1/consignments")) return json(200, { items: [], total: 0 });
      return json(404, {});
    }));
    reopened();
    expect(await screen.findByRole("alert")).toHaveTextContent("Can't reach Simple TMS right now.");
    expect(screen.queryByLabelText("Password")).not.toBeInTheDocument();
    expect(window.localStorage.getItem("tms.refresh")).toBe("r1");
    up = true;
    await userEvent.setup().click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("navigation", { name: "Main drawers" })).toBeInTheDocument();
    expect(window.localStorage.getItem("tms.refresh")).toBe("r2");
  });

  it("keeps the session when the network is down on opening", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("Failed to fetch"); }));
    reopened();
    expect(await screen.findByRole("alert")).toHaveTextContent("Can't reach Simple TMS right now.");
    expect(window.localStorage.getItem("tms.refresh")).toBe("r1");
  });

  it("signs out on opening when the refresh token is refused", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json(401, { detail: "session expired or invalid" })));
    reopened();
    expect(await screen.findByLabelText("Password")).toBeInTheDocument();
    expect(window.localStorage.getItem("tms.refresh")).toBeNull();
  });
});
