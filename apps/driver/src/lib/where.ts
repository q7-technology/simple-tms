/** Where the phone is, for a delivery's record, asked for only during a run
 * and only when a stop is confirmed. Never more than a few seconds' wait:
 * without a fix the confirmation goes without one. */

export interface Position {
  latitude: string;
  longitude: string;
  accuracy_m: string | null;
}

export function getPosition(timeoutMs = 4000): Promise<Position | null> {
  if (typeof navigator === "undefined" || !navigator.geolocation) return Promise.resolve(null);
  return new Promise((resolve) => {
    const give = setTimeout(() => resolve(null), timeoutMs + 500);
    navigator.geolocation.getCurrentPosition(
      (p) => {
        clearTimeout(give);
        // as decimal strings: no float goes to the API
        resolve({ latitude: p.coords.latitude.toFixed(6), longitude: p.coords.longitude.toFixed(6),
                  accuracy_m: Number.isFinite(p.coords.accuracy) ? p.coords.accuracy.toFixed(1) : null });
      },
      () => { clearTimeout(give); resolve(null); },
      { enableHighAccuracy: true, timeout: timeoutMs, maximumAge: 60_000 },
    );
  });
}

/** Whether the driver has let the app know where it is. */
export async function locationAllowed(): Promise<"granted" | "denied" | "prompt" | "unknown"> {
  try {
    const s = await navigator.permissions.query({ name: "geolocation" as PermissionName });
    return s.state;
  } catch {
    return "unknown";
  }
}
