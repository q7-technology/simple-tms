/** The driver app is an ordinary API client with a driver session token
 * (POST /v1/auth/driver-login, 12 hours, no refresh). Nothing here a partner
 * could not do with the same API. */
import type { Request } from "../lib/queue";

export interface FieldError {
  field: string;
  message: string;
}

export class ApiError extends Error {
  status: number;
  errors: FieldError[];
  /** The API's reason when it gives one: wrong_pin, locked, over_capacity, ... */
  code: string | null;
  body: unknown;
  constructor(status: number, body: unknown) {
    const errors = ((body as { errors?: FieldError[] })?.errors ?? []) as FieldError[];
    const detail = (body as { detail?: string })?.detail;
    super(errors.length > 0 ? errors.map((e) => e.message).join("; ")
      : typeof detail === "string" ? detail : `Simple TMS answered ${status}`);
    this.status = status;
    this.errors = errors;
    this.code = typeof (body as { code?: unknown })?.code === "string" ? (body as { code: string }).code : null;
    this.body = body;
  }
}

/** No signal, or no proper answer. */
export class Unreachable extends Error {
  constructor() {
    super("No signal: Simple TMS can't be reached right now.");
  }
}

type Fetcher = (url: string, init: RequestInit) => Promise<Response>;

export class DriverApi {
  token: string | null = null;
  /** The API no longer takes the token: the shift ran out, or the PIN changed. */
  onSignedOut: (() => void) | null = null;
  private fetcher: Fetcher;

  constructor(fetcher?: Fetcher) {
    this.fetcher = fetcher ?? ((url, init) => fetch(url, init));
  }

  private headers(extra: Record<string, string> = {}): Record<string, string> {
    return { Accept: "application/json", ...extra, ...(this.token ? { Authorization: `Bearer ${this.token}` } : {}) };
  }

  async request<T>(method: string, path: string, body?: unknown): Promise<T> {
    let res: Response;
    try {
      res = await this.fetcher(path, {
        method,
        headers: this.headers(body === undefined ? {} : { "Content-Type": "application/json" }),
        body: body === undefined ? undefined : JSON.stringify(body),
      });
    } catch {
      throw new Unreachable();
    }
    if (res.status === 401 && path !== "/v1/auth/driver-login") this.onSignedOut?.();
    const text = await res.text();
    let parsed: unknown = null;
    try {
      parsed = text ? JSON.parse(text) : null;
    } catch {
      // a proxy's error page, say: not the API's own answer
      if (res.ok || res.status >= 500) throw new Unreachable();
      parsed = { detail: `Simple TMS didn't answer properly (${res.status}).` };
    }
    if (!res.ok) throw new ApiError(res.status, parsed);
    return parsed as T;
  }

  get<T>(path: string) { return this.request<T>("GET", path); }
  post<T>(path: string, body: unknown = {}) { return this.request<T>("POST", path, body); }

  /** For the offline queue: one request as it stands, with today's token. */
  async send(req: Request): Promise<Response> {
    const res = await this.fetcher(req.path, {
      method: req.method, headers: this.headers({ "Content-Type": req.contentType }), body: req.body,
    });
    if (res.status === 401) this.onSignedOut?.();
    return res;
  }
}

export const api = new DriverApi();
