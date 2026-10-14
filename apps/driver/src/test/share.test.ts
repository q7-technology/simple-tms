import { ApiError, Unreachable } from "../api/client";
import { EVERY_MS, Sharer, type Sharing } from "../lib/share";

/** A phone's geolocation: the test says where it is, or that it may not say. */
function fakeGeo() {
  let ok: PositionCallback | null = null;
  let no: PositionErrorCallback | null = null;
  const cleared: number[] = [];
  const geo = {
    watchPosition: (a: PositionCallback, b?: PositionErrorCallback | null) => { ok = a; no = b ?? null; return 42; },
    clearWatch: (id: number) => cleared.push(id),
    getCurrentPosition: () => undefined,
  } as unknown as Geolocation;
  return {
    geo, cleared,
    at: (latitude: number, longitude: number, accuracy = 12) =>
      ok!({ coords: { latitude, longitude, accuracy }, timestamp: Date.now() } as GeolocationPosition),
    refuse: () => no!({ code: 1, PERMISSION_DENIED: 1 } as GeolocationPositionError),
  };
}

describe("sharing where the truck is", () => {
  let sent: { path: string; body: Record<string, string> }[];
  let answer: () => Promise<unknown>;
  let states: Sharing[];

  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-10-14T00:10:00Z"));
    sent = [];
    states = [];
    answer = async () => null;
  });
  afterEach(() => vi.useRealTimers());

  function sharer(geo: Geolocation | undefined) {
    const s = new Sharer(7, async (path, body) => { sent.push({ path, body: body as Record<string, string> }); return answer(); },
                         (state) => states.push(state), geo);
    s.start();
    return s;
  }

  it("sends the first position at once, then the newest about every 30 seconds", async () => {
    const g = fakeGeo();
    sharer(g.geo);
    expect(states).toEqual(["waiting"]);
    g.at(-37.5622104, 143.8503);
    await vi.advanceTimersByTimeAsync(0);
    expect(sent).toEqual([{ path: "/v1/runs/7/position", body: {
      at: "2026-10-14T00:10:00.000Z", latitude: "-37.562210", longitude: "143.850300", accuracy_m: "12.0" } }]);
    expect(states.at(-1)).toBe("on");
    g.at(-37.57, 143.86);
    g.at(-37.58, 143.87);
    await vi.advanceTimersByTimeAsync(EVERY_MS);
    expect(sent.map((s) => s.body.latitude)).toEqual(["-37.562210", "-37.580000"]);
    // nothing new: nothing sent
    await vi.advanceTimersByTimeAsync(EVERY_MS);
    expect(sent).toHaveLength(2);
  });

  it("keeps no backlog in a black spot: once back, only the newest goes", async () => {
    const g = fakeGeo();
    sharer(g.geo);
    answer = async () => { throw new Unreachable(); };
    g.at(-37.1, 143.1);
    await vi.advanceTimersByTimeAsync(0);
    g.at(-37.2, 143.2);
    await vi.advanceTimersByTimeAsync(EVERY_MS);
    g.at(-37.3, 143.3);
    answer = async () => null;
    await vi.advanceTimersByTimeAsync(EVERY_MS);
    expect(sent.map((s) => s.body.latitude)).toEqual(["-37.100000", "-37.200000", "-37.300000"]);
    await vi.advanceTimersByTimeAsync(EVERY_MS * 3);
    expect(sent).toHaveLength(3);
  });

  it("leaves out a position too rough to draw", async () => {
    const g = fakeGeo();
    sharer(g.geo);
    g.at(-37.1, 143.1, 1500);
    await vi.advanceTimersByTimeAsync(EVERY_MS);
    expect(sent).toEqual([]);
  });

  it("stops when the office says the run isn't on the road", async () => {
    const g = fakeGeo();
    sharer(g.geo);
    answer = async () => { throw new ApiError(409, { code: "run_done", detail: "R-0007 is finished" }); };
    g.at(-37.1, 143.1);
    await vi.advanceTimersByTimeAsync(0);
    expect(states.at(-1)).toBe("off");
    expect(g.cleared).toEqual([42]);
    g.at(-37.2, 143.2);
    await vi.advanceTimersByTimeAsync(EVERY_MS * 2);
    expect(sent).toHaveLength(1);
  });

  it("says so when the driver hasn't let it, and sends nothing", async () => {
    const g = fakeGeo();
    sharer(g.geo);
    g.refuse();
    expect(states.at(-1)).toBe("denied");
    await vi.advanceTimersByTimeAsync(EVERY_MS);
    expect(sent).toEqual([]);
  });

  it("stops watching when told to, and on a phone that can't tell where it is never starts", async () => {
    const g = fakeGeo();
    const s = sharer(g.geo);
    s.stop();
    expect(g.cleared).toEqual([42]);
    g.at(-37.1, 143.1);
    await vi.advanceTimersByTimeAsync(EVERY_MS);
    expect(sent).toEqual([]);
    states = [];
    sharer(undefined);
    expect(states).toEqual(["off"]);
  });
});
