import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, ApiError, Unreachable, type Session } from "../api/client";
import type { Me } from "../api/types";

interface AuthState {
  ready: boolean;
  user: Me | null;
  /** Why the session could not be picked up, when it is kept to try again. */
  trouble: string | null;
  retry: () => void;
  signIn: (username: string, password: string) => Promise<void>;
  signOut: () => Promise<void>;
  can: (scope: string) => boolean;
}

const Ctx = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [ready, setReady] = useState(false);
  const [user, setUser] = useState<Me | null>(null);
  const [trouble, setTrouble] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  const loadMe = useCallback(async () => {
    setUser(await api.get<Me>("/v1/auth/me"));
  }, []);

  useEffect(() => {
    api.onSignedOut = () => setUser(null);
    (async () => {
      try {
        // a session handed in already (a test, say), or one to pick up after a reload
        if (api.session?.token || (await api.resume())) await loadMe();
        setTrouble(null);
      } catch (e) {
        if (e instanceof ApiError && (e.status === 400 || e.status === 401)) {
          api.setSession(null);
          setTrouble(null);
        } else {
          // not reached, or no proper answer: keep the session for the next try
          setTrouble(e instanceof Unreachable ? e.message : new Unreachable().message);
        }
      } finally {
        setReady(true);
      }
    })();
  }, [loadMe, attempt]);

  const retry = useCallback(() => { setReady(false); setAttempt((n) => n + 1); }, []);

  const signIn = useCallback(async (username: string, password: string) => {
    api.setSession(await api.post<Session>("/v1/auth/login", { username, password }));
    await loadMe();
  }, [loadMe]);

  const signOut = useCallback(async () => {
    const refresh = api.session?.refresh_token;
    try {
      if (refresh) await api.post("/v1/auth/logout", { refresh_token: refresh });
    } finally {
      api.setSession(null);
      setUser(null);
    }
  }, []);

  const can = useCallback((scope: string) => {
    if (!user) return false;
    const area = scope.split(":")[0];
    return user.scopes.includes("*") || user.scopes.includes(scope) || user.scopes.includes(`${area}:*`);
  }, [user]);

  const value = useMemo(() => ({ ready, user, trouble, retry, signIn, signOut, can }),
                        [ready, user, trouble, retry, signIn, signOut, can]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useAuth outside AuthProvider");
  return ctx;
}
