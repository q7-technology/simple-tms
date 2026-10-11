import { ApiClient, ApiError } from "../api/client";

function json(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

async function refusal(call: Promise<unknown>): Promise<ApiError> {
  try {
    await call;
  } catch (e) {
    return e as ApiError;
  }
  throw new Error("it was not refused");
}

describe("ApiClient", () => {
  let calls: { url: string; init: RequestInit }[];
  let answers: Response[];
  let client: ApiClient;

  beforeEach(() => {
    calls = [];
    answers = [];
    const fetcher = async (url: string, init: RequestInit) => {
      calls.push({ url, init });
      return answers.shift() ?? json(500, { detail: "no answer queued" });
    };
    client = new ApiClient({ fetcher, storage: new Map() });
  });

  it("sends the bearer token and reads JSON", async () => {
    client.setSession({ token: "tms_s.abc", refresh_token: "tms_r.xyz", expires_in: 900 });
    answers.push(json(200, { items: [], total: 0 }));
    const body = await client.get<{ total: number }>("/v1/runs", { date: "2026-10-14", status: undefined });
    expect(body.total).toBe(0);
    expect(calls[0].url).toBe("/v1/runs?date=2026-10-14");
    expect(new Headers(calls[0].init.headers).get("Authorization")).toBe("Bearer tms_s.abc");
  });

  it("refreshes once on a 401 and tries again", async () => {
    client.setSession({ token: "old", refresh_token: "r1", expires_in: 900 });
    answers.push(json(401, { detail: "session expired or invalid" }));
    answers.push(json(200, { token: "new", refresh_token: "r2", expires_in: 900 }));
    answers.push(json(200, { ok: true }));
    expect(await client.get<{ ok: boolean }>("/v1/auth/me")).toEqual({ ok: true });
    expect(calls.map((c) => c.url)).toEqual(["/v1/auth/me", "/v1/auth/refresh", "/v1/auth/me"]);
    expect(new Headers(calls[2].init.headers).get("Authorization")).toBe("Bearer new");
    expect(client.session?.refresh_token).toBe("r2");
  });

  it("signs out when the refresh fails too", async () => {
    const signedOut = vi.fn();
    client.onSignedOut = signedOut;
    client.setSession({ token: "old", refresh_token: "r1", expires_in: 900 });
    answers.push(json(401, {}), json(401, {}));
    await expect(client.get("/v1/auth/me")).rejects.toBeInstanceOf(ApiError);
    expect(client.session).toBeNull();
    expect(signedOut).toHaveBeenCalled();
  });

  it("keeps the API's plain message and its code", async () => {
    client.setSession({ token: "t", refresh_token: "r", expires_in: 900 });
    answers.push(json(409, { detail: "R-0003: 280 kg too heavy. Move a stop to another run before printing the run sheet.", code: "over_capacity" }));
    const err = await refusal(client.post("/v1/runs/3/print"));
    expect(err.status).toBe(409);
    expect(err.code).toBe("over_capacity");
    expect(err.message).toBe("R-0003: 280 kg too heavy. Move a stop to another run before printing the run sheet.");
  });

  it("gives a field error as its message alone", async () => {
    client.setSession({ token: "t", refresh_token: "r", expires_in: 900 });
    answers.push(json(422, { errors: [{ field: "vehicle", message: "TMS041 already has R-0001 on 2026-10-14" }] }));
    const err = await refusal(client.post("/v1/runs", {}));
    expect(err.message).toBe("TMS041 already has R-0001 on 2026-10-14");
  });

  it("copes with an answer that is not JSON, such as a proxy's error page", async () => {
    client.setSession({ token: "t", refresh_token: "r", expires_in: 900 });
    answers.push(new Response("<html>Bad gateway</html>", { status: 502 }));
    const err = await refusal(client.get("/v1/runs"));
    expect(err.status).toBe(502);
    expect(err.message).toBe("<html>Bad gateway</html>");
  });

  it("keeps the refresh token so a reload can pick up", () => {
    const storage = new Map<string, string>();
    new ApiClient({ fetcher: async () => json(200, {}), storage })
      .setSession({ token: "t", refresh_token: "keep-me", expires_in: 900 });
    expect(new ApiClient({ fetcher: async () => json(200, {}), storage }).storedRefreshToken()).toBe("keep-me");
  });
});
