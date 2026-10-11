/** The desktop is an ordinary API client with a session token. Nothing here
 * a partner could not do with the same API. (As Simple WMS's desktop.) */

export interface Session {
  token: string;
  refresh_token: string;
  expires_in: number;
}

export interface FieldError {
  field: string;
  message: string;
}

export class ApiError extends Error {
  status: number;
  errors: FieldError[];
  /** The API's reason when it gives one: stale, over_capacity, wrong_password, locked, ... */
  code: string | null;
  body: unknown;
  constructor(status: number, body: unknown) {
    const errors = ((body as { errors?: FieldError[] })?.errors ?? []) as FieldError[];
    const detail = (body as { detail?: string })?.detail;
    const message =
      errors.length > 0
        ? errors.map((e) => e.message).join("; ")
        : typeof detail === "string"
          ? detail
          : `Simple TMS answered ${status}`;
    super(message);
    this.status = status;
    this.errors = errors;
    this.code = typeof (body as { code?: unknown })?.code === "string" ? (body as { code: string }).code : null;
    this.body = body;
  }
}

/** The API could not be reached, or did not answer properly. Nothing is
 * known about the session, so it is kept: the next try may well work. */
export class Unreachable extends Error {
  constructor() {
    super("Can't reach Simple TMS right now.");
  }
}

type Fetcher = (url: string, init: RequestInit) => Promise<Response>;
interface Storage {
  get(key: string): string | undefined;
  set(key: string, value: string): unknown;
  delete(key: string): unknown;
}

const REFRESH_KEY = "tms.refresh";
const NO_REFRESH = new Set(["/v1/auth/login", "/v1/auth/refresh", "/v1/auth/logout"]);

function browserStorage(): Storage {
  return {
    get: (k) => {
      try { return window.localStorage.getItem(k) ?? undefined; } catch { return undefined; }
    },
    set: (k, v) => { try { window.localStorage.setItem(k, v); } catch { /* private mode */ } },
    delete: (k) => { try { window.localStorage.removeItem(k); } catch { /* ignore */ } },
  };
}

export class ApiClient {
  session: Session | null = null;
  onSignedOut: (() => void) | null = null;
  private fetcher: Fetcher;
  private storage: Storage;
  private refreshing: Promise<boolean> | null = null;

  constructor(opts: { fetcher?: Fetcher; storage?: Storage } = {}) {
    this.fetcher = opts.fetcher ?? ((url, init) => fetch(url, init));
    this.storage = opts.storage ?? browserStorage();
  }

  setSession(session: Session | null) {
    this.session = session;
    if (session) this.storage.set(REFRESH_KEY, session.refresh_token);
    else this.storage.delete(REFRESH_KEY);
  }

  storedRefreshToken(): string | undefined {
    return this.storage.get(REFRESH_KEY);
  }

  /** Pick up where a reload left off, from the stored refresh token. */
  async resume(): Promise<boolean> {
    const refresh = this.storedRefreshToken();
    if (!refresh) return false;
    this.session = { token: "", refresh_token: refresh, expires_in: 0 };
    return this.refresh();
  }

  /** A new session from the refresh token. False, and signed out, only when
   * the API refuses the token (400 or 401); when it can't be reached or
   * doesn't answer properly, Unreachable, and the session is kept. */
  private async refresh(): Promise<boolean> {
    // one refresh at a time in this tab: the API spends a refresh token once
    if (this.refreshing) return this.refreshing;
    this.refreshing = (async () => {
      // another tab may have rotated the token since: the stored one is the newest
      let tried = this.storedRefreshToken() ?? this.session?.refresh_token;
      for (let attempt = 0; tried; attempt++) {
        let res: Response;
        try {
          res = await this.fetcher("/v1/auth/refresh", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ refresh_token: tried }),
          });
        } catch {
          throw new Unreachable();
        }
        if (res.ok) {
          let session: Session;
          try { session = (await res.json()) as Session; } catch { throw new Unreachable(); }
          this.setSession(session);
          return true;
        }
        if (res.status !== 400 && res.status !== 401) throw new Unreachable();
        const stored = this.storedRefreshToken();
        if (stored && stored !== tried) {
          // another tab spent it first and keeps the new one: try that, once
          if (attempt === 0) { tried = stored; continue; }
          this.session = null;      // leave the other tab's token where it is
          this.onSignedOut?.();
          return false;
        }
        break;
      }
      this.setSession(null);
      this.onSignedOut?.();
      return false;
    })();
    try {
      return await this.refreshing;
    } finally {
      this.refreshing = null;
    }
  }

  async request<T>(method: string, path: string, body?: unknown, retry = true): Promise<T> {
    const headers: Record<string, string> = { Accept: "application/json" };
    if (body !== undefined) headers["Content-Type"] = "application/json";
    if (this.session?.token) headers.Authorization = `Bearer ${this.session.token}`;
    const res = await this.fetcher(path, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (res.status === 401 && retry && this.session?.refresh_token && !NO_REFRESH.has(path)) {
      if (await this.refresh()) return this.request<T>(method, path, body, false);
    }
    if (res.status === 204) return undefined as T;
    const text = await res.text();
    let parsed: unknown = null;
    try {
      parsed = text ? JSON.parse(text) : null;
    } catch {
      // not the API's own answer: a proxy's error page, say. Its HTML means nothing to a dispatcher.
      parsed = { detail: `Simple TMS didn't answer properly (${res.status}). Try again in a moment.` };
    }
    if (!res.ok) throw new ApiError(res.status, parsed);
    return parsed as T;
  }

  get<T>(path: string, params?: Record<string, string | number | boolean | null | undefined>) {
    const qs = params
      ? Object.entries(params)
          .filter(([, v]) => v !== undefined && v !== null && v !== "")
          .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`)
          .join("&")
      : "";
    return this.request<T>("GET", qs ? `${path}?${qs}` : path);
  }
  post<T>(path: string, body?: unknown) { return this.request<T>("POST", path, body ?? {}); }
  patch<T>(path: string, body: unknown) { return this.request<T>("PATCH", path, body); }
}

export const api = new ApiClient();
