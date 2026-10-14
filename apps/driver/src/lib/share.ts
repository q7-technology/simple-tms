/** Live location: while the run is on the road, the phone tells the office
 * where it is, about every 30 seconds, and the screen says so. Never before
 * Start, never once the run is finished, and only while the app is open: a
 * web app can't share from the background. A position is not a
 * confirmation: nothing is queued, so a black spot leaves no backlog, and
 * once the signal is back only the newest goes. */
import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "../api/client";

export const EVERY_MS = 30_000;
/** rougher than this would put the truck in the wrong suburb: the API refuses it too */
export const ROUGHEST_M = 1000;

/** waiting: watching, nothing heard from the phone's GPS yet; on: sharing;
 * denied: the driver hasn't let the app know where it is; off: not sharing */
export type Sharing = "waiting" | "on" | "denied" | "off";

type Send = (path: string, body: unknown) => Promise<unknown>;

export class Sharer {
  private watch: number | null = null;
  private timer: ReturnType<typeof setInterval> | null = null;
  private latest: Record<string, string> | null = null;
  private sent: Record<string, string> | null = null;
  private stopped = false;

  constructor(private runId: number, private send: Send, private onState: (s: Sharing) => void,
              private geo: Geolocation | undefined = typeof navigator === "undefined" ? undefined : navigator.geolocation,
              private onEnded: (code: string | null) => void = () => undefined) {}

  start() {
    if (!this.geo?.watchPosition) { this.onState("off"); return; }
    this.onState("waiting");
    this.watch = this.geo.watchPosition(
      (p) => {
        if (!Number.isFinite(p.coords.accuracy) || p.coords.accuracy > ROUGHEST_M) return;
        const first = this.latest === null;
        // decimal strings, as for a delivery's position: no float goes to the API
        this.latest = { at: new Date(p.timestamp).toISOString(), latitude: p.coords.latitude.toFixed(6),
                        longitude: p.coords.longitude.toFixed(6), accuracy_m: p.coords.accuracy.toFixed(1) };
        if (first) { this.onState("on"); void this.tick(); }
      },
      (e) => { if (e.code === 1) { this.stop(); this.onState("denied"); } },
      { enableHighAccuracy: true, maximumAge: 15_000, timeout: 60_000 },
    );
    this.timer = setInterval(() => void this.tick(), EVERY_MS);
  }

  stop() {
    this.stopped = true;
    if (this.watch !== null) this.geo?.clearWatch(this.watch);
    if (this.timer !== null) clearInterval(this.timer);
    this.watch = this.timer = null;
  }

  /** The newest position, if it hasn't gone already. One lost to a black
   * spot isn't tried again: the next newest goes instead. */
  private async tick() {
    const fix = this.latest;
    if (this.stopped || !fix || fix === this.sent) return;
    this.sent = fix;
    try {
      await this.send(`/v1/runs/${this.runId}/position`, fix);
    } catch (e) {
      // the run isn't on the road (finished, say, from the office): stop
      if (e instanceof ApiError && e.status === 409) { this.stop(); this.onState("off"); this.onEnded(e.code); }
    }
  }
}

/** Share while `active` (the run on the road, not finished); say how it is
 * going. `onEnded` hears why the API said the run isn't on the road
 * (run_done, run_cancelled, run_stale…), to look at the run again. */
export function useSharing(runId: number, active: boolean, onEnded?: (code: string | null) => void): Sharing {
  const [state, setState] = useState<Sharing>("off");
  const ended = useRef(onEnded);
  ended.current = onEnded;
  useEffect(() => {
    if (!active) { setState("off"); return; }
    const s = new Sharer(runId, (path, body) => api.post(path, body), setState, undefined, (code) => ended.current?.(code));
    s.start();
    return () => s.stop();
  }, [runId, active]);
  return state;
}
