/** Confirmations from the road, sent when there is signal. As the Simple WMS
 * scanner's queue: each gets its message_id the moment it is made, is kept on
 * the phone, and is sent oldest first, in order; a repeat gets the same reply
 * and records nothing twice. A refusal (a 4xx that a retry can't mend) is
 * marked failed and not sent again.
 *
 * A confirmation may carry pictures (a signature, a photo). They go first,
 * each to PUT /v1/pod-files/{id} under an id chosen when it was taken, so
 * sending one twice is harmless too; then the confirmation that names them.
 * The pictures wait in a FileStore; the rest in localStorage. localStorage
 * rather than IndexedDB for the items: it is read and written at once, with
 * no await in between, so a write can't interleave with another in this tab,
 * and the items are small text (the pictures, which are not, are already in
 * IndexedDB). A write that fails is never silent: a confirmation the phone
 * couldn't keep is refused there and then, so the driver doesn't leave the
 * stop thinking it went.
 *
 * The queue belongs to the driver, not the phone: each confirmation keeps who
 * made it, and is sent only while that driver is signed in, with their own
 * sign-in. Another driver on the same phone sees how many wait, and whose. */
import type { FileStore } from "./files";

export type PictureKind = "signature" | "photo";

export interface QueuedFile {
  id: string;
  kind: PictureKind;
  sent: boolean;
}

export interface Driver {
  id: number;
  name: string;
}

export interface QueueItem {
  id: string;
  message_id: string;
  /** who made it: only their sign-in sends it */
  driver_id: number;
  driver_name: string;
  path: string;
  body: Record<string, unknown>;
  files: QueuedFile[];
  label: string;
  run_id: number;
  /** the stop it records, with what it says happened there (a pre-start is the truck's) */
  consignment_id: number | null;
  outcome: "delivered" | "failed" | "finished" | "prestart";
  created_at: string;
  status: "queued" | "sent" | "failed";
  attempts: number;
  /** goes up with each change, so the newer copy wins when two tabs' meet */
  rev?: number;
  sent_at?: string;
  /** the API's answer: a sent item's reply, or a refused one's body */
  reply?: unknown;
  error?: string;
}

export interface NewItem {
  path: string;
  body: Record<string, unknown>;
  label: string;
  run_id: number;
  consignment_id: number | null;
  outcome: QueueItem["outcome"];
  /** each picture, and the body field its id goes in (signature_file, photo_file; a
   * dotted path into the body for one inside a list: items.4.photo_file) */
  pictures?: { kind: PictureKind; field: string; blob: Blob }[];
}

export interface Request {
  method: "POST" | "PUT";
  path: string;
  body: string | Blob;
  contentType: string;
  /** the driver whose confirmation this is: sent with their sign-in or not at all */
  driver_id?: number;
}
type Sender = (req: Request) => Promise<Response>;
interface Storage {
  get(key: string): string | undefined;
  /** throws when it can't keep it (full, or blocked) */
  set(key: string, value: string): unknown;
}

export const NOT_KEPT = "This couldn't be kept on the phone. Don't leave the stop yet — try again or call the office.";

const KEY = "tms.driver.queue";
/** the ids of confirmations let go on purpose, so another tab's copy doesn't bring them back */
const GONE = "tms.driver.queue.gone";
const GONE_MAX = 1000;
const MAX = 300;

function browserStorage(): Storage {
  return {
    get: (k) => { try { return window.localStorage.getItem(k) ?? undefined; } catch { return undefined; } },
    set: (k, v) => { window.localStorage.setItem(k, v); },
  };
}

/** A refusal: the API understood and said no, so sending again can't help.
 * A 401 is not one (sign in again and it goes), nor 408 or 429. */
function refused(status: number): boolean {
  return status >= 400 && status < 500 && status !== 401 && status !== 408 && status !== 429;
}

/** Put a picture's id where the body names it: photo_file, or items.4.photo_file. */
function place(body: Record<string, unknown>, field: string, id: string) {
  const keys = field.split(".");
  let at = body as Record<string, unknown>;
  for (const k of keys.slice(0, -1)) at = at[k] as Record<string, unknown>;
  at[keys[keys.length - 1]] = id;
}

export function describe(body: unknown, status: number): string {
  const errors = (body as { errors?: { field: string; message: string }[] })?.errors;
  if (errors?.length) return errors.map((e) => e.message).join("; ");
  const detail = (body as { detail?: string })?.detail;
  return typeof detail === "string" ? detail : `Simple TMS answered ${status}`;
}

/** Whatever is kept that looks like a confirmation: it has its message_id
 * and where it goes. Anything else is dropped rather than trip the app up. */
function confirmation(x: unknown): x is QueueItem {
  const i = x as Partial<QueueItem> | null;
  return !!i && typeof i === "object" && typeof i.message_id === "string" && typeof i.path === "string";
}

function tidy(i: QueueItem): QueueItem {
  return {
    ...i,
    id: typeof i.id === "string" ? i.id : i.message_id,
    body: i.body && typeof i.body === "object" ? i.body : {},
    files: Array.isArray(i.files) ? i.files : [],
    status: i.status === "sent" || i.status === "failed" ? i.status : "queued",
    attempts: Number(i.attempts) || 0,
  };
}

/** The queue as kept. One that can't be read whole is copied aside under
 * tms.driver.queue.corrupt-<time> first, so nothing is lost for good. */
function readItems(storage: Storage): QueueItem[] {
  const raw = storage.get(KEY);
  if (raw === undefined) return [];
  let parsed: unknown;
  try { parsed = JSON.parse(raw); } catch { parsed = undefined; }
  const list = Array.isArray(parsed) ? parsed : [];
  const good = list.filter(confirmation).map(tidy);
  if (!Array.isArray(parsed) || good.length !== list.length) {
    try { storage.set(`${KEY}.corrupt-${Date.now()}`, raw); } catch { /* nowhere to put it */ }
  }
  return good;
}

const RANK = { queued: 0, failed: 1, sent: 2 } as const;

/** Of two copies of one confirmation, the one further on: the later change,
 * or on a tie the further status. A picture sent in either copy is sent. */
function further(mine: QueueItem, theirs: QueueItem): QueueItem {
  const a = mine.rev ?? 0, b = theirs.rev ?? 0;
  const win = b > a || (b === a && RANK[theirs.status] > RANK[mine.status]) ? theirs : mine;
  const sent = new Set([...mine.files, ...theirs.files].filter((f) => f.sent).map((f) => f.id));
  return { ...win, files: win.files.map((f) => ({ ...f, sent: f.sent || sent.has(f.id) })) };
}

export interface Locks {
  request<T>(name: string, fn: () => Promise<T>): Promise<T>;
}

function browserLocks(): Locks | undefined {
  const locks = typeof navigator === "undefined" ? undefined : (navigator as { locks?: LockManager }).locks;
  return locks ? { request: <T,>(name: string, fn: () => Promise<T>) => locks.request(name, fn) as Promise<T> } : undefined;
}

async function readJson(res: Response): Promise<unknown> {
  const text = await res.text();
  try { return text ? JSON.parse(text) : null; } catch { return null; }
}

export class RetryQueue {
  private items: QueueItem[] = [];
  private send: Sender;
  private files: FileStore;
  private storage: Storage;
  private online: () => boolean;
  private draining: Promise<number> | null = null;
  private driver: Driver | null = null;
  private listeners = new Set<() => void>();
  private locks: Locks | undefined;
  private gone = new Set<string>();

  constructor(opts: { send: Sender; files: FileStore; storage?: Storage; online?: () => boolean; locks?: Locks }) {
    this.send = opts.send;
    this.files = opts.files;
    this.storage = opts.storage ?? browserStorage();
    this.online = opts.online ?? (() => typeof navigator === "undefined" || navigator.onLine);
    this.locks = opts.locks ?? browserLocks();
    this.items = readItems(this.storage);
  }

  private readGone(): Set<string> {
    try {
      const ids = JSON.parse(this.storage.get(GONE) ?? "[]");
      if (Array.isArray(ids)) for (const id of ids) if (typeof id === "string") this.gone.add(id);
    } catch { /* none, then */ }
    return this.gone;
  }

  /** Let a confirmation go for good, in every tab. */
  private forget(ids: string[]) {
    for (const id of ids) this.gone.add(id);
    this.items = this.items.filter((i) => !this.gone.has(i.id));
  }

  /** What is kept (perhaps by another tab of the app) and what is here, as one:
   * each confirmation once, the copy further on, in the order they were made. */
  private merge(): QueueItem[] {
    const gone = this.readGone();
    const here = new Map(this.items.map((i) => [i.id, i]));
    const out = new Map<string, QueueItem>();
    // what is kept first, in its order: it was made before what is only here
    for (const k of readItems(this.storage)) {
      if (gone.has(k.id)) continue;
      const mine = here.get(k.id);
      // the same object stays, changed in place, for whoever holds it
      out.set(k.id, mine ? Object.assign(mine, further(mine, k)) : k);
    }
    for (const i of this.items) if (!gone.has(i.id) && !out.has(i.id)) out.set(i.id, i);
    return [...out.values()].sort((a, b) => (a.created_at < b.created_at ? -1 : a.created_at > b.created_at ? 1 : 0));
  }

  /** Take in what another tab kept. */
  reload() {
    this.items = this.merge();
    this.listeners.forEach((fn) => fn());
  }

  /** Kept on the phone, or it throws. Read again and merged first, so two
   * tabs of the app never write over each other's confirmations. */
  private save() {
    this.items = this.merge();
    // forget the oldest that went, never one still waiting
    const over = this.items.length - MAX;
    if (over > 0) this.forget(this.items.filter((x) => x.status !== "queued").slice(0, over).map((x) => x.id));
    try {
      if (this.gone.size) this.storage.set(GONE, JSON.stringify([...this.gone].slice(-GONE_MAX)));
      this.storage.set(KEY, JSON.stringify(this.items));
    } finally {
      this.listeners.forEach((fn) => fn());
    }
  }

  /** While sending: a write that fails leaves the change in memory, and the
   * next write tries again. Nothing is lost by it: what is kept is at worst
   * an item still queued, which the API answers again with the same reply. */
  private saveQuietly() {
    try { this.save(); } catch { /* kept in memory until the next write */ }
  }

  subscribe(fn: () => void) {
    this.listeners.add(fn);
    return () => { this.listeners.delete(fn); };
  }

  /** Who is signed in: only their confirmations are shown, counted and sent. */
  setDriver(driver: Driver | null) {
    if (driver?.id === this.driver?.id && driver?.name === this.driver?.name) return;
    this.driver = driver;
    // kept before each confirmation named its driver: the next driver's
    const unnamed = driver ? this.items.filter((i) => typeof i.driver_id !== "number") : [];
    for (const i of unnamed) { i.driver_id = driver!.id; i.driver_name = driver!.name; }
    if (unnamed.length) this.saveQuietly();
    else this.listeners.forEach((fn) => fn());
  }

  private mine(): QueueItem[] {
    const id = this.driver?.id;
    return id === undefined ? [] : this.items.filter((i) => i.driver_id === id);
  }

  /** The signed-in driver's, newest first. */
  all(): QueueItem[] { return this.mine().reverse(); }
  pending(): QueueItem[] { return this.mine().filter((i) => i.status === "queued"); }
  failed(): QueueItem[] { return this.mine().filter((i) => i.status === "failed"); }

  /** Each run something still waits for or was refused on, as "<driver>.<run>":
   * its copy on the phone is kept while so. */
  runsInUse(): Set<string> {
    return new Set(this.items.filter((i) => i.status !== "sent").map((i) => `${i.driver_id}.${i.run_id}`));
  }

  /** Confirmations other drivers made on this phone, still to send: they go
   * when that driver signs in again. */
  othersWaiting(): { driver_id: number; name: string; count: number }[] {
    const out = new Map<number, { driver_id: number; name: string; count: number }>();
    for (const i of this.items) {
      if (i.status !== "queued" || i.driver_id === this.driver?.id) continue;
      const row = out.get(i.driver_id) ?? { driver_id: i.driver_id, name: i.driver_name, count: 0 };
      row.count += 1;
      out.set(i.driver_id, row);
    }
    return [...out.values()];
  }
  forRun(runId: number): QueueItem[] { return this.all().filter((i) => i.run_id === runId); }

  /** Keep it (pictures first), then send what is waiting if there is signal. */
  async submit(input: NewItem): Promise<QueueItem> {
    const driver = this.driver;
    if (!driver) throw new Error("Sign in again to keep this on the phone.");
    const files: QueuedFile[] = [];
    // a copy of its own, lists and all: the ids go into it, not the caller's
    const body = JSON.parse(JSON.stringify(input.body)) as Record<string, unknown>;
    for (const p of input.pictures ?? []) {
      const id = crypto.randomUUID();
      // on the phone before the confirmation that names it, so a reload never finds one without the other
      try {
        await this.files.put(id, p.blob);
      } catch {
        for (const f of files) await this.files.delete(f.id).catch(() => undefined);
        throw new Error(NOT_KEPT);
      }
      files.push({ id, kind: p.kind, sent: false });
      place(body, p.field, id);
    }
    const item: QueueItem = {
      id: crypto.randomUUID(), message_id: crypto.randomUUID(), driver_id: driver.id, driver_name: driver.name,
      path: input.path, body, files,
      label: input.label, run_id: input.run_id, consignment_id: input.consignment_id,
      outcome: input.outcome, created_at: new Date().toISOString(), status: "queued", attempts: 0,
    };
    this.items.push(item);
    try {
      this.save();
    } catch {
      this.items = this.items.filter((i) => i.id !== item.id);
      for (const f of files) await this.files.delete(f.id).catch(() => undefined);
      this.listeners.forEach((fn) => fn());
      throw new Error(NOT_KEPT);
    }
    // the stop done again: what it replaces, refused, goes, pictures and all
    const replaced = this.items.filter((i) => i.status === "failed" && i.id !== item.id && i.driver_id === item.driver_id
      && i.run_id === item.run_id && i.consignment_id === item.consignment_id);
    if (replaced.length) await this.letGo(replaced);
    void this.drain();
    return item;
  }

  private async letGo(items: QueueItem[]) {
    this.forget(items.map((i) => i.id));
    this.saveQuietly();
    for (const f of items.flatMap((i) => i.files)) await this.files.delete(f.id).catch(() => undefined);
  }

  /** A refused confirmation, sent again as itself (the same message_id),
   * once whatever stopped it has been put right. */
  async retry(id: string): Promise<void> {
    const item = this.failed().find((i) => i.id === id);
    if (!item) return;
    item.status = "queued";
    item.error = undefined;
    item.rev = (item.rev ?? 0) + 1;
    this.saveQuietly();
    await this.drain();
  }

  /** A refused confirmation the driver lets go: it, and its pictures, are gone for good. */
  async dismiss(id: string): Promise<void> {
    const item = this.failed().find((i) => i.id === id);
    if (item) await this.letGo([item]);
  }

  private fail(item: QueueItem, error: string) {
    item.status = "failed";
    item.error = error;
  }

  /** One go at an item: its pictures, then itself. It is sent, failed, or still queued after. */
  private async attempt(item: QueueItem): Promise<void> {
    item.attempts += 1;
    try {
      for (const f of item.files.filter((x) => !x.sent)) {
        const blob = await this.files.get(f.id);
        if (!blob) {
          this.fail(item, `The ${f.kind} is no longer on this phone, so this can't be sent. Do the stop again.`);
          return;
        }
        const res = await this.send({ method: "PUT", path: `/v1/pod-files/${f.id}?kind=${f.kind}`,
                                      body: blob, contentType: blob.type || "application/octet-stream",
                                      driver_id: item.driver_id });
        if (res.ok) {
          f.sent = true;
          item.rev = (item.rev ?? 0) + 1;
          this.saveQuietly();
          continue;
        }
        const why = describe(await readJson(res), res.status);
        if (refused(res.status)) this.fail(item, `The ${f.kind} wasn't taken: ${why}`);
        else item.error = why;
        return;
      }
      const res = await this.send({ method: "POST", path: item.path, contentType: "application/json",
                                    body: JSON.stringify({ message_id: item.message_id, ...item.body }),
                                    driver_id: item.driver_id });
      const parsed = await readJson(res);
      if (res.ok) {
        item.status = "sent";
        item.sent_at = new Date().toISOString();
        item.reply = parsed;
        item.error = undefined;
        for (const f of item.files) await this.files.delete(f.id).catch(() => undefined);
      } else if (refused(res.status)) {
        this.fail(item, describe(parsed, res.status));
        // kept, for a refusal that says more than why (a pre-start's vehicle_changed)
        item.reply = parsed;
      } else {
        item.error = describe(parsed, res.status);
      }
    } catch (e) {
      item.error = e instanceof Error && e.message ? e.message : "no signal";
    } finally {
      item.rev = (item.rev ?? 0) + 1;
      this.saveQuietly();
    }
  }

  /** Send the signed-in driver's confirmations, oldest first, stopping at the
   * first that has to wait, so they arrive in the order they were made. One
   * drain at a time; what is added meanwhile is picked up by the one running.
   * A sign-out (or another driver signing in) stops it before the next. */
  drain(): Promise<number> {
    // cleared once it has settled, never before it is stored
    // one tab of the app at a time, where the browser can say so
    const run = () => this.drainNow();
    this.draining ??= (this.locks ? this.locks.request("tms-queue", run) : run())
      .finally(() => { this.draining = null; });
    return this.draining;
  }

  private async drainNow(): Promise<number> {
    let sent = 0;
    const who = this.driver?.id;
    if (who === undefined) return 0;
    for (;;) {
      if (this.driver?.id !== who) break;
      // what another tab sent meanwhile isn't sent again
      this.items = this.merge();
      const next = this.items.find((i) => i.status === "queued" && i.driver_id === who);
      if (!next || !this.online()) break;
      await this.attempt(next);
      if (next.status === "sent") sent += 1;
      else if (next.status === "queued") break;
    }
    return sent;
  }
}

/** What the driver sees waiting: one row per confirmation, its pictures
 * folded into it (they belong to the confirmation that names them). The
 * banner's count and the WAITING TO SEND list both come from here, so they
 * always agree. */
export interface WaitingRow {
  id: string;
  text: string;
  status: QueueItem["status"];
  at: string;
  error?: string;
}

function pictures(files: QueuedFile[]): string {
  const signatures = files.filter((f) => f.kind === "signature").length;
  const photos = files.filter((f) => f.kind === "photo").length;
  const parts: string[] = [];
  if (signatures) parts.push(signatures === 1 ? "signature" : `${signatures} signatures`);
  if (photos) parts.push(`${photos} ${photos === 1 ? "photo" : "photos"}`);
  return parts.join(" and ");
}

export function waitingRows(items: QueueItem[]): WaitingRow[] {
  return items.map((i) => {
    const unsent = i.status === "queued" ? pictures(i.files.filter((f) => !f.sent)) : "";
    return {
      id: i.id, text: unsent ? `${i.label} · ${unsent}` : i.label, status: i.status,
      at: i.status === "sent" ? i.sent_at ?? i.created_at : i.created_at,
      error: i.status === "failed" ? i.error : undefined,
    };
  });
}

/** How many confirmations are still to send. */
export function waitingCount(items: QueueItem[]): number {
  return waitingRows(items).filter((r) => r.status === "queued").length;
}
