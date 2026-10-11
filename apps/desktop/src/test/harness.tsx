import { render } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { api } from "../api/client";
import { AppRoutes } from "../App";
import { AuthProvider } from "../auth/AuthContext";

export function json(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

export interface Call { method: string; url: string; body: unknown }
type Handler = (method: string, url: string, body: unknown) => Response | null | Promise<Response | null>;

/** Signed in as a dispatcher; `more` answers everything else, or it is a 404. */
export function stubApi(more: Handler = () => null) {
  const calls: Call[] = [];
  const fetchMock = vi.fn(async (url: string, init: RequestInit = {}) => {
    const method = init.method ?? "GET";
    const body = init.body ? JSON.parse(init.body as string) : undefined;
    calls.push({ method, url, body });
    if (url === "/v1/auth/me") {
      return json(200, { kind: "user", id: 1, name: "dee", display_name: "Dee Dispatch", role: "dispatcher",
                         scopes: ["fleet:read", "consignments:read", "runs:read", "runs:write"] });
    }
    if (url === "/v1/auth/logout") return new Response(null, { status: 204 });
    const answer = await more(method, url, body);
    return answer ?? json(404, { detail: `no stub for ${method} ${url}` });
  });
  vi.stubGlobal("fetch", fetchMock);
  return { calls, fetchMock };
}

function Where() {
  return <output data-testid="where">{useLocation().pathname}</output>;
}

export function renderApp(path: string) {
  api.setSession({ token: "t", refresh_token: "r", expires_in: 900 });
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AuthProvider>
        <AppRoutes />
        <Where />
      </AuthProvider>
    </MemoryRouter>,
  );
}

export function path(url: string): string {
  return url.split("?")[0];
}
