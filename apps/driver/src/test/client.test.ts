import { ApiError, DriverApi, Unreachable } from "../api/client";

function json(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

describe("the driver's API client", () => {
  it("sends the driver token and reads JSON", async () => {
    const seen: RequestInit[] = [];
    const api = new DriverApi(async (_url, init) => { seen.push(init); return json(200, [{ id: 7 }]); });
    api.token = "tms_d.abc";
    expect(await api.get("/v1/driver/runs?date=2026-10-14")).toEqual([{ id: 7 }]);
    expect((seen[0].headers as Record<string, string>).Authorization).toBe("Bearer tms_d.abc");
  });

  it("gives the API's reason and code when it says no", async () => {
    const api = new DriverApi(async () => json(409, { detail: "R-0007 is finished", code: "run_done" }));
    const e = (await api.post("/v1/runs/7/start", { message_id: "m" }).catch((x) => x)) as ApiError;
    expect(e).toBeInstanceOf(ApiError);
    expect([e.status, e.code, e.message]).toEqual([409, "run_done", "R-0007 is finished"]);
  });

  it("says no signal when there is none, or the answer is not the API's", async () => {
    const offline = new DriverApi(async () => { throw new TypeError("Failed to fetch"); });
    expect(await offline.get("/v1/driver/runs").catch((x) => x)).toBeInstanceOf(Unreachable);
    const proxy = new DriverApi(async () => new Response("<html>Bad gateway</html>", { status: 502 }));
    expect(await proxy.get("/v1/driver/runs").catch((x) => x)).toBeInstanceOf(Unreachable);
  });

  it("signs the driver out when the API no longer takes the token", async () => {
    const api = new DriverApi(async () => json(401, { detail: "driver session expired or invalid" }));
    const out = vi.fn();
    api.onSignedOut = out;
    await api.get("/v1/driver/runs").catch(() => undefined);
    await api.send({ method: "POST", path: "/v1/runs/7/finish", body: "{}", contentType: "application/json" });
    expect(out).toHaveBeenCalledTimes(2);
    // a wrong PIN is not a session ending
    await api.post("/v1/auth/driver-login", { code: "kim", pin: "0000" }).catch(() => undefined);
    expect(out).toHaveBeenCalledTimes(2);
  });
});
