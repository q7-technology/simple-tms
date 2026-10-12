import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api } from "../api/client";
import type { DriverSessionOut, Me } from "../api/types";
import { indexedDbFiles } from "../lib/files";
import { RetryQueue, waitingCount, type Driver } from "../lib/queue";
import { keepRunsFor, pruneRuns } from "../lib/run";

interface Stored { token: string; expires_at: number; driver: Me }

interface State {
  driver: Me | null;
  online: boolean;
  queue: RetryQueue;
  /** the signed-in driver's confirmations still to send */
  waiting: number;
  /** other drivers' confirmations on this phone, waiting for them to sign in */
  others: { driver_id: number; name: string; count: number }[];
  signIn: (code: string, pin: string) => Promise<void>;
  signOut: () => void;
}

const Ctx = createContext<State | null>(null);
const SESSION_KEY = "tms.driver.session";
// what is waiting goes when the signal comes back, and every 15 s while it is patchy
const DRAIN_EVERY_MS = 15_000;

function readSession(): Stored | null {
  try {
    const s = JSON.parse(window.localStorage.getItem(SESSION_KEY) ?? "null") as Stored | null;
    return s && s.expires_at > Date.now() ? s : null;
  } catch {
    return null;
  }
}

export const SIGN_IN_NOT_KEPT =
  "This phone couldn't keep your sign-in, so it couldn't keep your deliveries either. Free some space on it, or call the office.";

/** A sign-in the phone can't keep is refused: a phone that can't keep it
 * can't keep the run's confirmations either. Signing out can't fail. */
function writeSession(s: Stored | null) {
  try {
    if (s) window.localStorage.setItem(SESSION_KEY, JSON.stringify(s));
    else window.localStorage.removeItem(SESSION_KEY);
  } catch {
    if (s) throw new Error(SIGN_IN_NOT_KEPT);
  }
}

/** Ask the browser not to clear what the phone keeps when it is short of space. */
function keepOnPhone() {
  try { void navigator.storage?.persist?.().catch(() => undefined); } catch { /* not offered */ }
}

function owner(s: Stored | null): Driver | null {
  return s ? { id: s.driver.id, name: s.driver.display_name } : null;
}

/** The token and the queue follow whoever is signed in, together. */
function signedInAs(queue: RetryQueue, s: Stored | null) {
  api.token = s?.token ?? null;
  api.driverId = s?.driver.id ?? null;
  queue.setDriver(owner(s));
}

export const defaultQueue = new RetryQueue({ send: (r) => api.send(r), files: indexedDbFiles });

export function SessionProvider({ children, queue = defaultQueue }: { children: ReactNode; queue?: RetryQueue }) {
  const [session, setSession] = useState<Stored | null>(() => {
    const s = readSession();
    signedInAs(queue, s);
    return s;
  });
  const [online, setOnline] = useState(typeof navigator === "undefined" ? true : navigator.onLine);
  const [waiting, setWaiting] = useState(() => waitingCount(queue.all()));
  const [others, setOthers] = useState(() => queue.othersWaiting());

  const signOut = useCallback(() => {
    // what is waiting stays on the phone, and goes once that driver signs in again
    signedInAs(queue, null);
    writeSession(null);
    setSession(null);
  }, [queue]);

  useEffect(() => {
    keepOnPhone();
    keepRunsFor(() => queue.runsInUse());
    pruneRuns(queue.runsInUse());
  }, [queue]);

  useEffect(() => {
    api.onSignedOut = signOut;
    const up = () => { setOnline(true); void queue.drain(); };
    const down = () => setOnline(false);
    window.addEventListener("online", up);
    window.addEventListener("offline", down);
    const unsubscribe = queue.subscribe(() => {
      setWaiting(waitingCount(queue.all()));
      setOthers(queue.othersWaiting());
    });
    const tick = setInterval(() => { if (navigator.onLine && queue.pending().length) void queue.drain(); }, DRAIN_EVERY_MS);
    return () => {
      window.removeEventListener("online", up);
      window.removeEventListener("offline", down);
      unsubscribe();
      clearInterval(tick);
    };
  }, [queue, signOut]);

  // signed in again: send what waited for it
  useEffect(() => { if (session) void queue.drain(); }, [session, queue]);

  const signIn = useCallback(async (code: string, pin: string) => {
    const out = await api.post<DriverSessionOut>("/v1/auth/driver-login", { code, pin });
    const s = { token: out.token, expires_at: Date.now() + out.expires_in * 1000, driver: out.driver };
    writeSession(s);
    signedInAs(queue, s);
    setSession(s);
  }, [queue]);

  const value = useMemo<State>(() => ({
    driver: session?.driver ?? null, online, queue, waiting, others, signIn, signOut,
  }), [session, online, queue, waiting, others, signIn, signOut]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useSession(): State {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useSession outside SessionProvider");
  return ctx;
}

/** Re-render whenever the queue changes. */
export function useQueueItems(runId: number) {
  const { queue } = useSession();
  const [, bump] = useState(0);
  useEffect(() => queue.subscribe(() => bump((n) => n + 1)), [queue]);
  return queue.forRun(runId);
}
