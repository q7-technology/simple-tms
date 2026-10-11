import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api } from "../api/client";
import type { DriverSessionOut, Me } from "../api/types";
import { indexedDbFiles } from "../lib/files";
import { RetryQueue } from "../lib/queue";

interface Stored { token: string; expires_at: number; driver: Me }

interface State {
  driver: Me | null;
  online: boolean;
  queue: RetryQueue;
  /** confirmations still to send */
  waiting: number;
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

function writeSession(s: Stored | null) {
  try {
    if (s) window.localStorage.setItem(SESSION_KEY, JSON.stringify(s));
    else window.localStorage.removeItem(SESSION_KEY);
  } catch { /* private mode: signed in for this visit only */ }
}

export const defaultQueue = new RetryQueue({ send: (r) => api.send(r), files: indexedDbFiles });

export function SessionProvider({ children, queue = defaultQueue }: { children: ReactNode; queue?: RetryQueue }) {
  const [session, setSession] = useState<Stored | null>(() => {
    const s = readSession();
    api.token = s?.token ?? null;
    return s;
  });
  const [online, setOnline] = useState(typeof navigator === "undefined" ? true : navigator.onLine);
  const [waiting, setWaiting] = useState(queue.pending().length);

  const signOut = useCallback(() => {
    // what is waiting stays on the phone, and goes once a driver signs in again
    api.token = null;
    writeSession(null);
    setSession(null);
  }, []);

  useEffect(() => {
    api.onSignedOut = signOut;
    const up = () => { setOnline(true); void queue.drain(); };
    const down = () => setOnline(false);
    window.addEventListener("online", up);
    window.addEventListener("offline", down);
    const unsubscribe = queue.subscribe(() => setWaiting(queue.pending().length));
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
    api.token = s.token;
    writeSession(s);
    setSession(s);
  }, []);

  const value = useMemo<State>(() => ({
    driver: session?.driver ?? null, online, queue, waiting, signIn, signOut,
  }), [session, online, queue, waiting, signIn, signOut]);
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
