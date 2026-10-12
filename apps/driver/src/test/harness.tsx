import { render } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { api } from "../api/client";
import type { RunStops } from "../api/types";
import { AppRoutes } from "../App";
import { SessionProvider } from "../auth/Session";
import { memoryFiles } from "../lib/files";
import { RetryQueue } from "../lib/queue";
import { saveRun } from "../lib/run";
import { RUN } from "./samples";

export { RUN, stop } from "./samples";

export function json(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

export interface Call { method: string; url: string; body: unknown; contentType: string | null }
type Handler = (method: string, url: string, body: unknown) => Response | null | Promise<Response | null>;

/** `answer` answers the API, or it is a 404; a thrown TypeError is no signal. */
export function stubApi(answer: Handler = () => null) {
  const calls: Call[] = [];
  const fetchMock = vi.fn(async (url: string, init: RequestInit = {}) => {
    const method = init.method ?? "GET";
    const headers = (init.headers ?? {}) as Record<string, string>;
    const raw = init.body;
    const body = typeof raw === "string" ? JSON.parse(raw) : raw;
    calls.push({ method, url, body, contentType: headers["Content-Type"] ?? null });
    const a = await answer(method, url, body);
    return a ?? json(404, { detail: `no stub for ${method} ${url}` });
  });
  vi.stubGlobal("fetch", fetchMock);
  return { calls };
}

export const KIM = { id: 3, code: "kim", name: "Kim" };
export const MEL = { id: 4, code: "mel", name: "Mel" };

export function signedIn(who: { id: number; code: string; name: string } = KIM) {
  window.localStorage.setItem("tms.driver.session", JSON.stringify({
    token: `tms_d.${who.code}`, expires_at: Date.now() + 3_600_000,
    driver: { kind: "driver", id: who.id, name: who.code, display_name: who.name, role: "driver",
              scopes: ["fleet:read", "deliveries:read", "deliveries:write"] },
  }));
}

function Where() {
  return <output data-testid="where">{useLocation().pathname}</output>;
}

export function renderApp(path: string, opts: { online?: () => boolean; queue?: RetryQueue } = {}) {
  api.token = null;
  const queue = opts.queue
    ?? new RetryQueue({ send: (r) => api.send(r), files: memoryFiles(), online: () => (opts.online ?? (() => true))() });
  render(
    <MemoryRouter initialEntries={[path]}>
      <SessionProvider queue={queue}>
        <AppRoutes />
        <Where />
      </SessionProvider>
    </MemoryRouter>,
  );
  return { queue };
}

/** The run already on the phone, as starting it leaves it (Kim's unless said). */
export function onThePhone(run: RunStops = RUN, driverId = KIM.id) {
  saveRun(driverId, run);
}
