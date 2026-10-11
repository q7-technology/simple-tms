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

  private async refresh(): Promise<boolean> {
    // one refresh at a time: the API spends a refresh token once
    if (this.refreshing) return this.refreshing;
    this.refreshing = (async () => {
      const refresh = this.session?.refresh_token;
      if (!refresh) return false;
      const res = await this.fetcher("/v1/auth/refresh", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: refresh }),
      });
      if (!res.ok) {
        this.setSession(null);
        this.onSignedOut?.();
        return false;
      }
      this.setSession((await res.json()) as Session);
      return true;
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
    try { parsed = text ? JSON.parse(text) : null; } catch { parsed = { detail: text.slice(0, 200) }; }
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
