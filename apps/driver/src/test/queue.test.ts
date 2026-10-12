import { memoryFiles } from "../lib/files";
import { RetryQueue, type QueueItem, type Request } from "../lib/queue";

function json(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const PNG = () => new Blob([new Uint8Array([0x89, 0x50, 0x4e, 0x47])], { type: "image/png" });

describe("the offline queue", () => {
  let sent: Request[];
  let answers: (Response | Error)[];
  let online: boolean;
  let store: Map<string, string>;
  let files: ReturnType<typeof memoryFiles>;

  const KIM = { id: 3, name: "Kim" };
  const MEL = { id: 4, name: "Mel" };
  function queue(driver: { id: number; name: string } | null = KIM) {
    const q = new RetryQueue({
      send: async (req) => {
        sent.push(req);
        const a = answers.shift() ?? json(200, { ok: true });
        if (a instanceof Error) throw a;
        return a;
      },
      files,
      storage: { get: (k) => store.get(k), set: (k, v) => store.set(k, v) },
      online: () => online,
    });
    q.setDriver(driver);
    return q;
  }
  const body = (r: Request) => JSON.parse(r.body as string);
  const item = (label: string, consignment_id = 1) => ({
    path: `/v1/runs/7/stops/${consignment_id}/fail`, body: { reason: "closed" }, label, run_id: 7,
    consignment_id, outcome: "failed" as const,
  });

  beforeEach(() => {
    sent = [];
    answers = [];
    online = true;
    store = new Map();
    files = memoryFiles();
  });

  it("gives each confirmation its message_id when it is made, and keeps it through a reload", async () => {
    online = false;
    const q = queue();
    const a = await q.submit(item("Stop 1 · couldn't deliver"));
    expect(a.message_id).toMatch(/^[0-9a-f-]{36}$/);
    expect(a.status).toBe("queued");
    const again = queue();
    expect(again.pending().map((i) => i.message_id)).toEqual([a.message_id]);
    expect(sent).toHaveLength(0);
  });

  it("keeps what waits in the browser's localStorage when given no other store", async () => {
    const q = new RetryQueue({ send: async () => json(200, {}), files, online: () => false });
    q.setDriver(KIM);
    const a = await q.submit(item("Stop 1"));
    expect(JSON.parse(window.localStorage.getItem("tms.driver.queue")!)[0].message_id).toBe(a.message_id);
    const reloaded = new RetryQueue({ send: async () => json(200, {}), files, online: () => false });
    reloaded.setDriver(KIM);
    expect(reloaded.pending().map((i) => i.message_id)).toEqual([a.message_id]);
  });

  it("sends the pictures first, then the confirmation that names them", async () => {
    const q = queue();
    const a = await q.submit({
      ...item("Stop 1 · delivered"), path: "/v1/runs/7/stops/1/deliver", outcome: "delivered",
      body: { signed_name: "Priya" },
      pictures: [{ kind: "signature", field: "signature_file", blob: PNG() }],
    });
    await q.drain();
    const fileId = a.files[0].id;
    expect(sent.map((r) => `${r.method} ${r.path}`)).toEqual([
      `PUT /v1/pod-files/${fileId}?kind=signature`, "POST /v1/runs/7/stops/1/deliver"]);
    expect(sent[0].contentType).toBe("image/png");
    expect(body(sent[1])).toEqual({ message_id: a.message_id, signed_name: "Priya", signature_file: fileId });
    expect(q.all()[0].status).toBe("sent");
    // sent, so the picture is off the phone
    expect(files.size()).toBe(0);
  });

  it("waits without signal, then sends everything in order, once each", async () => {
    online = false;
    const q = queue();
    await q.submit(item("Stop 1", 1));
    await q.submit(item("Stop 2", 2));
    expect(sent).toHaveLength(0);
    online = true;
    expect(await q.drain()).toBe(2);
    expect(sent.map((r) => r.path)).toEqual(["/v1/runs/7/stops/1/fail", "/v1/runs/7/stops/2/fail"]);
    expect(await q.drain()).toBe(0);
    expect(sent).toHaveLength(2);
  });

  it("keeps the order when the first can't go yet", async () => {
    online = false;
    const q = queue();
    await q.submit(item("Stop 1", 1));
    await q.submit(item("Stop 2", 2));
    online = true;
    answers.push(new TypeError("Failed to fetch"));
    expect(await q.drain()).toBe(0);
    expect(sent.map((r) => r.path)).toEqual(["/v1/runs/7/stops/1/fail"]);
    expect(q.pending()).toHaveLength(2);
    expect(q.pending()[0].error).toBe("Failed to fetch");
  });

  it("sends a confirmation whose reply was lost again with the same message_id", async () => {
    const q = queue();
    answers.push(new TypeError("Failed to fetch"));   // it got there; the answer didn't come back
    const a = await q.submit(item("Stop 1"));
    await q.drain();
    await q.drain();
    expect(sent).toHaveLength(2);
    expect(body(sent[0]).message_id).toBe(a.message_id);
    expect(body(sent[1]).message_id).toBe(a.message_id);
    expect(q.all()[0].status).toBe("sent");
  });

  it("doesn't send a picture again once it has gone", async () => {
    const q = queue();
    answers.push(json(201, {}), json(503, { detail: "starting up" }));
    await q.submit({ ...item("Stop 1"), pictures: [{ kind: "photo", field: "photo_file", blob: PNG() }] });
    await q.drain();
    expect(q.pending()[0].error).toBe("starting up");
    await q.drain();
    expect(sent.map((r) => r.method)).toEqual(["PUT", "POST", "POST"]);
  });

  it("marks a refusal failed and goes on with the rest", async () => {
    online = false;
    const q = queue();
    await q.submit(item("Stop 1", 1));
    await q.submit(item("Stop 2", 2));
    online = true;
    answers.push(json(409, { code: "stop_done", detail: "stop 1 on R-0007 is already recorded as delivered" }));
    expect(await q.drain()).toBe(1);
    expect(q.failed().map((i) => i.error)).toEqual(["stop 1 on R-0007 is already recorded as delivered"]);
    await q.drain();
    expect(sent).toHaveLength(2);
  });

  it("treats a message_id reused for another confirmation as a refusal, never sent again", async () => {
    const q = queue();
    answers.push(json(409, { code: "message_id_reused", detail: "that message_id was used for another confirmation" }));
    await q.submit(item("Stop 1"));
    await q.drain();
    await q.drain();
    expect(sent).toHaveLength(1);
    expect(q.pending()).toHaveLength(0);
    expect(q.failed().map((i) => i.error)).toEqual(["that message_id was used for another confirmation"]);
  });

  it("keeps one waiting when the session has run out, to go after signing in again", async () => {
    const q = queue();
    answers.push(json(401, { detail: "driver session expired or invalid" }));
    await q.submit(item("Stop 1"));
    await q.drain();
    expect(q.pending()).toHaveLength(1);
    await q.drain();
    expect(q.all()[0].status).toBe("sent");
  });

  it("doesn't send a confirmation whose picture was refused", async () => {
    const q = queue();
    answers.push(json(413, { detail: "a photo may be 5 MB at most", code: "too_large" }));
    await q.submit({ ...item("Stop 1"), pictures: [{ kind: "photo", field: "photo_file", blob: PNG() }] });
    await q.drain();
    expect(sent.map((r) => r.method)).toEqual(["PUT"]);
    expect(q.failed()[0].error).toBe("The photo wasn't taken: a photo may be 5 MB at most");
  });

  it("says so when a picture has gone from the phone", async () => {
    online = false;
    const q = queue();
    const a = await q.submit({ ...item("Stop 1"), pictures: [{ kind: "signature", field: "signature_file", blob: PNG() }] });
    await files.delete(a.files[0].id);
    online = true;
    await q.drain();
    expect(sent).toHaveLength(0);
    expect(q.failed()[0].error).toMatch(/The signature is no longer on this phone/);
  });

  it("drains one at a time however often it is asked", async () => {
    online = false;
    const q = queue();
    await q.submit(item("Stop 1", 1));
    await q.submit(item("Stop 2", 2));
    online = true;
    await Promise.all([q.drain(), q.drain(), q.drain()]);
    expect(sent).toHaveLength(2);
  });

  describe("belongs to the driver who made it, not the phone", () => {
    it("keeps who made each confirmation, and sends it with their sign-in only", async () => {
      online = false;
      const kims = queue(KIM);
      const a = await kims.submit({ ...item("Stop 1"), pictures: [{ kind: "photo", field: "photo_file", blob: PNG() }] });
      expect(a.driver_id).toBe(3);
      expect(sent).toHaveLength(0);

      // Mel signs in on the same phone
      online = true;
      const mels = queue(MEL);
      expect(await mels.drain()).toBe(0);
      expect(sent).toHaveLength(0);
      expect(mels.pending()).toEqual([]);
      expect(mels.all()).toEqual([]);
      expect(mels.othersWaiting()).toEqual([{ driver_id: 3, name: "Kim", count: 1 }]);

      // and Kim again
      mels.setDriver(KIM);
      expect(await mels.drain()).toBe(1);
      expect(sent.map((r) => r.driver_id)).toEqual([3, 3]);
      expect(mels.othersWaiting()).toEqual([]);
    });

    it("sends nothing with nobody signed in", async () => {
      online = false;
      await queue(KIM).submit(item("Stop 1"));
      online = true;
      expect(await queue(null).drain()).toBe(0);
      expect(sent).toHaveLength(0);
    });

    it("stops sending a driver's confirmations the moment they sign out", async () => {
      online = false;
      const q = queue(KIM);
      await q.submit(item("Stop 1", 1));
      await q.submit(item("Stop 2", 2));
      online = true;
      let first = true;
      const signedOut = new RetryQueue({
        send: async (req) => {
          sent.push(req);
          if (first) { first = false; signedOut.setDriver(MEL); }
          return json(200, {});
        },
        files, storage: { get: (k) => store.get(k), set: (k, v) => store.set(k, v) }, online: () => true,
      });
      signedOut.setDriver(KIM);
      expect(await signedOut.drain()).toBe(1);
      expect(sent).toHaveLength(1);
    });

    it("can't be made with nobody signed in", async () => {
      await expect(queue(null).submit(item("Stop 1"))).rejects.toThrow(/Sign in/);
    });
  });

  it("says so, and keeps nothing half, when the phone won't keep a confirmation", async () => {
    online = false;
    const q = new RetryQueue({
      send: async () => json(200, {}), files, online: () => false,
      storage: { get: (k) => store.get(k), set: () => { throw new DOMException("full", "QuotaExceededError"); } },
    });
    q.setDriver(KIM);
    await expect(q.submit({ ...item("Stop 1"), pictures: [{ kind: "photo", field: "photo_file", blob: PNG() }] }))
      .rejects.toThrow("This couldn't be kept on the phone. Don't leave the stop yet — try again or call the office.");
    expect(q.all()).toEqual([]);
    expect(files.size()).toBe(0);
  });

  it("names the runs it still needs, whoever's they are", async () => {
    online = false;
    await queue(KIM).submit(item("Stop 1"));
    await queue(MEL).submit({ ...item("Stop 1"), run_id: 9 });
    expect([...queue(null).runsInUse()].sort()).toEqual(["3.7", "4.9"]);
  });

  describe("when what is kept has been damaged", () => {
    const aside = () => [...store.keys()].filter((k) => k.startsWith("tms.driver.queue.corrupt-"));

    it("copies an unreadable queue aside before starting afresh", () => {
      store.set("tms.driver.queue", "[{\"message_id\": \"m\", trunc");
      const q = queue();
      expect(q.all()).toEqual([]);
      expect(aside()).toHaveLength(1);
      expect(store.get(aside()[0])).toBe("[{\"message_id\": \"m\", trunc");
    });

    it("copies aside a queue that isn't a list", () => {
      store.set("tms.driver.queue", "{\"message_id\": \"m\"}");
      expect(queue().all()).toEqual([]);
      expect(store.get(aside()[0])).toBe("{\"message_id\": \"m\"}");
    });

    it("drops the entries that aren't confirmations and keeps the rest", async () => {
      online = false;
      const good = await queue().submit(item("Stop 1"));
      const raw = JSON.stringify([null, 5, "x", { path: "/v1/x" }, { message_id: "m" }, good]);
      store.set("tms.driver.queue", raw);
      const q = queue();
      expect(q.pending().map((i) => i.message_id)).toEqual([good.message_id]);
      expect(store.get(aside()[0])).toBe(raw);
    });

    it("gives a confirmation kept before drivers were named to the next driver who signs in", async () => {
      online = false;
      const old = { ...(await queue().submit(item("Stop 1"))) } as Partial<QueueItem>;
      delete old.driver_id;
      delete old.driver_name;
      store.set("tms.driver.queue", JSON.stringify([old]));
      const q = queue(MEL);
      expect(q.pending().map((i) => [i.message_id, i.driver_id])).toEqual([[old.message_id, 4]]);
    });
  });

  describe("open twice on one phone (the installed app and a browser tab)", () => {
    it("keeps both one's and the other's confirmations, whichever writes last", async () => {
      online = false;
      const app = queue();
      const tab = queue();
      const a = await app.submit(item("Stop 1", 1));
      const b = await tab.submit(item("Stop 2", 2));
      expect(queue().pending().map((i) => i.message_id)).toEqual([a.message_id, b.message_id]);
    });

    it("never puts back as waiting what the other has sent", async () => {
      online = false;
      const app = queue();
      const tab = queue();
      const a = await app.submit(item("Stop 1", 1));
      tab.reload();
      online = true;
      expect(await app.drain()).toBe(1);
      online = false;
      // the tab still thinks stop 1 waits, and keeps another
      await tab.submit(item("Stop 2", 2));
      const kept = queue();
      expect(kept.all().map((i) => [i.message_id, i.status])).toEqual([
        [expect.any(String), "queued"], [a.message_id, "sent"]]);
      online = true;
      await tab.drain();
      expect(sent.map((r) => r.path)).toEqual(["/v1/runs/7/stops/1/fail", "/v1/runs/7/stops/2/fail"]);
    });

    it("sends from one at a time, under the phone's lock", async () => {
      online = false;
      await queue().submit(item("Stop 1"));
      online = true;
      const names: string[] = [];
      const q = new RetryQueue({
        send: async (req) => { sent.push(req); return json(200, {}); }, files, online: () => true,
        storage: { get: (k) => store.get(k), set: (k, v) => store.set(k, v) },
        locks: { request: (name, fn) => { names.push(name); return fn(); } },
      });
      q.setDriver(KIM);
      expect(await q.drain()).toBe(1);
      expect(names).toEqual(["tms-queue"]);
    });
  });
});
