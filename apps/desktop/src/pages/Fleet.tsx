import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { useOutletContext } from "react-router-dom";
import { api, ApiError, Unreachable } from "../api/client";
import type { HistoryItem, Page, PrestartDetail, Run, Vehicle } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { clock, dayLabel, isoDay } from "../lib/days";
import { checkCell, km, longDate, regoCell, serviceCell, statusPill, when, type Cell } from "../lib/fleet";
import { Drawer } from "../ui/Drawer";
import { Icon } from "../ui/icons";
import { Photo } from "../ui/Photo";

type Note = { tone: "r" | "g"; text: string } | null;

function said(e: unknown): string {
  if (e instanceof Unreachable) return e.message;
  return e instanceof ApiError ? e.message : "Simple TMS could not be reached. Check the connection and try again.";
}

function CellOut({ c }: { c: Cell }) {
  const colour = c.tone === "r" ? "var(--color-red-ink)" : c.tone === "a" ? "var(--color-amber-ink)" : undefined;
  return (
    <td style={{ whiteSpace: "nowrap" }}>
      <span className={c.tone ? `due-${c.tone}` : undefined} style={c.text === "Not recorded" || c.text === "No schedule" || c.text === "None yet" ? { color: "var(--color-muted)" } : undefined}>{c.text}</span>
      {c.sub && <div className="small" style={colour ? { color: colour } : undefined}>{c.sub}</div>}
    </td>
  );
}

const RESULT = { passed: "PASSED", failed: "AN ITEM FAILED", grounded: "FAILED" } as const;
const KIND = { prestart: "Pre-start", fixed: "Marked fixed", service: "Service", rego: "Rego", odometer: "Odometer put right" } as const;

function historyLine(h: HistoryItem): string {
  if (h.kind === "prestart") {
    const how = h.result === "passed" ? "passed" : `failed: ${h.failed.join(", ")}`;
    return `Pre-start ${how} · ${km(h.odometer_km ?? 0)} km`;
  }
  const what = h.kind === "service" ? `${longDate(h.done_on!)}${h.odometer_km != null ? ` at ${km(h.odometer_km)} km` : ""}`
    : h.kind === "rego" ? `runs out ${longDate(h.expires_on!)}`
    : h.kind === "odometer" ? `${km(h.odometer_km ?? 0)} km` : "";
  return [KIND[h.kind], what, h.note].filter(Boolean).join(" · ");
}

/** One small form: a few fields and a note, sent as a new record. */
function RecordForm({ title, button, fields, send }: {
  title: string; button: string; fields: { name: string; label: string; type: "date" | "number"; required?: boolean }[];
  send: (values: Record<string, string>) => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = e.currentTarget;
    const values = Object.fromEntries([...new FormData(form).entries()].map(([k, v]) => [k, String(v).trim()]));
    setBusy(true);
    try { await send(values); form.reset(); } finally { setBusy(false); }
  }
  return (
    <details className="box" style={{ padding: "8px 12px" }}>
      <summary style={{ cursor: "pointer", fontSize: 13, fontWeight: 800 }}>{title}</summary>
      <form onSubmit={(e) => void submit(e)} aria-label={title} style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 8 }}>
        {fields.map((f) => (
          <label key={f.name} className="small" style={{ display: "flex", flexDirection: "column", gap: 4, fontWeight: 700 }}>{f.label}
            <input className="field" name={f.name} type={f.type} min={f.type === "number" ? 0 : undefined} required={f.required} /></label>
        ))}
        <label className="small" style={{ display: "flex", flexDirection: "column", gap: 4, fontWeight: 700 }}>Note
          <input className="field" name="note" type="text" maxLength={500} required /></label>
        <button type="submit" className="btn sm primary" disabled={busy} style={{ alignSelf: "flex-start" }}>{button}</button>
      </form>
    </details>
  );
}

/** The aside: the check that grounded the truck (or its last one), Mark
 * fixed, what a fleet manager records, and the truck's history. */
function VehicleAside({ v, driverName, onChanged }: { v: Vehicle; driverName: string | null; onChanged: (note: Note) => Promise<void> }) {
  const { can } = useAuth();
  const manager = can("fleet:write");
  const checkId = v.grounded?.check_id ?? v.last_prestart?.id ?? null;
  const [check, setCheck] = useState<PrestartDetail | null>(null);
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [fixNote, setFixNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    setCheck(null);
    setError(null);
    if (checkId !== null) {
      api.get<PrestartDetail>(`/v1/prestarts/${checkId}`).then((c) => live && setCheck(c)).catch((e) => live && setError(said(e)));
    }
    api.get<HistoryItem[]>(`/v1/vehicles/${v.rego}/history`, { limit: 8 }).then((h) => live && setHistory(h)).catch(() => undefined);
    return () => { live = false; };
  }, [v.rego, checkId, v.last_prestart?.id, v.odometer_km, v.rego_due?.expires_on, v.service_due?.last_on]);

  async function record(path: string, body: Record<string, unknown>, done: string) {
    setError(null);
    try {
      await api.post(`/v1/vehicles/${v.rego}/${path}`, body);
      setFixNote("");
      await onChanged({ tone: "g", text: done });
    } catch (e) {
      setError(said(e));
      // a failed check that came in meanwhile: show it
      if (e instanceof ApiError && e.code === "stale") await onChanged(null);
    }
  }

  async function fix(e: FormEvent) {
    e.preventDefault();
    if (!check || !fixNote.trim()) return;
    setBusy(true);
    await record("fix", { check_id: check.id, note: fixNote.trim() }, `${v.rego} is marked fixed, and can take a run.`);
    setBusy(false);
  }

  const grounded = v.state === "grounded" && check?.id === v.grounded?.check_id;
  const failed = check?.items.filter((i) => !i.passed) ?? [];
  const who = check?.driver?.name ?? driverName;
  return (
    <aside className="aside" aria-label="Pre-start check record">
      <div>
        <div className="eyebrow" style={{ color: check?.result === "grounded" ? "var(--color-red-ink)" : undefined }}>
          {check ? `PRE-START CHECK · ${RESULT[check.result]}` : checkId !== null ? "PRE-START CHECK" : "NO PRE-START YET"}</div>
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <span style={{ fontSize: 22, fontWeight: 800 }}>{v.rego}</span>
          {v.state === "grounded" && <span className="pill p-red">Grounded</span>}
          {v.state === "overdue" && <span className="pill p-red">Overdue</span>}</div>
        {check && (
          <div style={{ fontSize: 13, color: "var(--color-ink-2)", textTransform: "none" }}>
            {[v.type[0].toUpperCase() + v.type.slice(1), who, `${dayLabel(new Date(check.at))}, ${clock(new Date(check.at))}`,
              `odometer ${km(check.odometer_km)} km`].filter(Boolean).join(" · ")}</div>)}
      </div>
      {v.blocked && v.state === "overdue" && <div className="msg r">{v.blocked.message}</div>}
      {check && (
        <div className="box">
          {check.items.map((i) => (
            <div key={i.label} className="chk">
              <span className={`ic ${i.passed ? "ok" : "bad"}`}><Icon name={i.passed ? "tick" : "close"} size={12} width={3.2} /></span>
              <div style={{ flex: 1 }}><b>{i.label}</b>{i.critical && !i.passed && <div className="small" style={{ marginTop: 2 }}>Critical item</div>}</div>
              <span className="small" style={{ fontWeight: 700, color: i.passed ? "var(--color-muted)" : "var(--color-red-ink)" }}>{i.passed ? "Pass" : "Fail"}</span>
            </div>
          ))}
        </div>
      )}
      {failed.map((i) => (
        <div key={i.label} style={{ padding: 12, borderRadius: 14, background: "#fff", border: "1px solid var(--color-red-line)", display: "flex", gap: 12, alignItems: "flex-start" }}>
          {i.photo_file && <Photo fileId={i.photo_file} label={`${who ?? "The driver"}'s photo: ${i.label}`} />}
          <div style={{ fontSize: 13 }}><b>{who ? `${who}'s note` : "Note"} · {i.label}</b>
            <div style={{ color: "var(--color-ink-2)", marginTop: 2 }}>{i.note}</div></div>
        </div>
      ))}
      {check?.fixed && <div className="msg g">Marked fixed by {check.fixed.by}: {check.fixed.note}</div>}
      {error && <div className="msg r" role="alert">{error}</div>}
      {grounded && <div className="small">A failed critical item grounds the truck. It can't go on a run until a fleet manager marks it fixed.</div>}
      {grounded && manager && (
        <form onSubmit={(e) => void fix(e)} aria-label="Mark fixed"
              style={{ padding: 14, borderRadius: 14, background: "#fff", border: "1px solid var(--color-card-line)", display: "flex", flexDirection: "column", gap: 10 }}>
          <div className="eyebrow">MARK FIXED</div>
          <label style={{ display: "flex", flexDirection: "column", gap: 6, fontSize: 13, fontWeight: 700 }}>What was done
            <input className="field" type="text" maxLength={500} placeholder="e.g. Replaced left brake globe, tested"
                   value={fixNote} onChange={(e) => setFixNote(e.target.value)} /></label>
          <button type="submit" className="btn primary" disabled={busy || !fixNote.trim()}><Icon name="wrench" size={16} />Mark fixed</button>
          <div className="small">Every check is kept as it was. Marking fixed adds a new record; it never changes this one.</div>
        </form>
      )}
      {manager && (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <div className="eyebrow">RECORD</div>
          <RecordForm title="A new rego expiry" button="Record the rego" fields={[{ name: "expires_on", label: "Runs out on", type: "date", required: true }]}
                      send={(f) => record("rego", { expires_on: f.expires_on, note: f.note }, `${v.rego}'s rego is recorded.`)} />
          <RecordForm title="A service done" button="Record the service"
                      fields={[{ name: "done_on", label: "Done on", type: "date", required: true }, { name: "odometer_km", label: "Odometer (km)", type: "number" }]}
                      send={(f) => record("service", { done_on: f.done_on, note: f.note,
                                                       ...(f.odometer_km ? { odometer_km: Number(f.odometer_km) } : {}) },
                                          `${v.rego}'s service is recorded.`)} />
          <RecordForm title="The odometer put right" button="Record the reading"
                      fields={[{ name: "odometer_km", label: "Reading (km)", type: "number", required: true }]}
                      send={(f) => record("odometer", { odometer_km: Number(f.odometer_km), note: f.note }, `${v.rego}'s odometer is put right.`)} />
        </div>
      )}
      {history.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <div className="eyebrow">HISTORY</div>
          <div className="box">
            {history.map((h) => (
              <div key={`${h.kind}-${h.id}`}><span>{historyLine(h)}</span>
                <span className="small" style={{ whiteSpace: "nowrap" }}>{when(h.recorded_at)} · {h.by}</span></div>))}
          </div>
        </div>
      )}
    </aside>
  );
}

/** Fleet.html: the trucks, whether each can go, the pre-start that
 * grounded one, and what a fleet manager records. */
export function Fleet() {
  const { reloadCounts } = useOutletContext<{ reloadCounts: () => void }>();
  const today = useMemo(() => isoDay(new Date()), []);
  const [vehicles, setVehicles] = useState<Vehicle[] | null>(null);
  const [runs, setRuns] = useState<Run[]>([]);
  const [q, setQ] = useState("");
  const [chosen, setChosen] = useState<string | null>(null);
  const [note, setNote] = useState<Note>(null);

  const load = useCallback(async () => {
    const [v, r] = await Promise.all([
      api.get<Page<Vehicle>>("/v1/vehicles", { date: today }),
      api.get<Page<Run>>("/v1/runs", { date: today }),
    ]);
    setVehicles(v.items);
    setRuns(r.items);
  }, [today]);

  useEffect(() => {
    load().catch((e) => setNote({ tone: "r", text: said(e) }));
    const t = setInterval(() => { load().catch(() => undefined); }, 30_000);
    return () => clearInterval(t);
  }, [load]);

  async function changed(n: Note) {
    setNote(n);
    await load().catch(() => undefined);
    reloadCounts();
  }

  const runOf = (rego: string) => runs.find((r) => r.vehicle.rego === rego && r.status !== "cancelled");
  const all = vehicles ?? [];
  const shown = all.filter((v) => {
    const s = q.trim().toLowerCase();
    return !s || v.rego.toLowerCase().includes(s) || (runOf(v.rego)?.driver.name ?? "").toLowerCase().includes(s);
  });
  const selected = all.find((v) => v.rego === chosen) ?? all.find((v) => v.state === "grounded") ?? all[0] ?? null;
  const active = all.filter((v) => v.active);
  const grounded = active.filter((v) => v.state === "grounded");
  const overdue = active.filter((v) => v.state === "overdue");
  const due = active.filter((v) => v.state === "due");
  const checked = active.filter((v) => v.last_prestart && isoDay(new Date(v.last_prestart.at)) === today);
  const types = ["rigid", "van", "ute"].map((t) => [t, active.filter((v) => v.type === t).length] as const).filter(([, n]) => n > 0);

  return (
    <Drawer label="Fleet" head={
      <div style={{ flex: 1, minWidth: 200 }}>
        <div className="eyebrow" style={{ color: "var(--color-green)" }}>Own fleet</div>
        <h1>Fleet</h1>
        <div className="sub">Your trucks, pre-start checks, and when rego and service fall due.</div>
      </div>
    }>
      <label className="search" style={{ maxWidth: 420 }}><Icon name="search" />
        <span className="sr">Search vehicles</span>
        <input type="search" placeholder="Rego or driver" value={q} onChange={(e) => setQ(e.target.value)} /></label>
      {note && <div className={`msg ${note.tone}`} role={note.tone === "r" ? "alert" : "status"}>{note.text}</div>}
      <div className="stats">
        <div className="stat"><div className="eyebrow">VEHICLES</div><div className="v num">{active.length}</div>
          <div className="d">{types.map(([t, n]) => `${n} ${t}`).join(" · ")}</div></div>
        <div className={`stat${grounded.length ? " red" : ""}`}><div className="eyebrow">GROUNDED</div><div className="v num">{grounded.length}</div>
          <div className="d">{grounded.map((v) => `${v.rego} · ${v.grounded?.failed.join(", ").toLowerCase()}`).join("; ") || "None"}</div></div>
        {overdue.length > 0 && (
          <div className="stat red"><div className="eyebrow">OVERDUE</div><div className="v num">{overdue.length}</div>
            <div className="d">{overdue.map((v) => v.rego).join(", ")}</div></div>)}
        <div className={`stat${due.length ? " amber" : ""}`}><div className="eyebrow">DUE SOON</div><div className="v num">{due.length}</div>
          <div className="d">{due.map((v) => `${v.rego} ${v.rego_due?.state === "due" ? "rego" : "service"}`).join(" · ") || "Nothing in the next 30 days"}</div></div>
        <div className="stat"><div className="eyebrow">PRE-STARTS TODAY</div><div className="v num">{checked.length}</div>
          <div className="d">{checked.filter((v) => v.last_prestart!.result === "passed").length} passed · {checked.filter((v) => v.last_prestart!.result !== "passed").length} failed</div></div>
      </div>
      <div className="split">
        <div className="tablewrap">
          <table style={{ minWidth: 720 }}>
            <thead><tr><th>REGO</th><th>TYPE</th><th>DRIVER</th><th>REGO EXPIRY</th><th>NEXT SERVICE</th><th>LAST PRE-START</th><th>STATUS</th></tr></thead>
            <tbody>
              {shown.map((v) => {
                const run = runOf(v.rego);
                const pill = statusPill(v, run?.status === "departed");
                const on = selected?.rego === v.rego;
                return (
                  <tr key={v.rego} className={on ? `sel${v.state === "grounded" || v.state === "overdue" ? " bad" : ""}` : undefined}>
                    <td><button type="button" className="rowbtn" aria-pressed={on} aria-label={`Show ${v.rego}`} onClick={() => setChosen(v.rego)}>{v.rego}</button></td>
                    <td style={{ textTransform: "capitalize" }}>{v.type}</td>
                    <td>{run?.driver.name ?? <span className="small">No run today</span>}</td>
                    <CellOut c={regoCell(v)} />
                    <CellOut c={serviceCell(v)} />
                    <CellOut c={checkCell(v)} />
                    <td><span className={`pill p-${pill.tone}`}>{pill.text}</span></td>
                  </tr>
                );
              })}
              {vehicles?.length === 0 && <tr><td colSpan={7} className="small">No vehicles yet. Add them with POST /v1/vehicles.</td></tr>}
            </tbody>
          </table>
        </div>
        {selected && <VehicleAside key={selected.rego} v={selected} driverName={runOf(selected.rego)?.driver.name ?? null} onChanged={changed} />}
      </div>
      <div className="small">Amber 30 days or 1,000 km before it's due, red once it's overdue. A grounded or overdue truck can't take a run.
        Due dates show here only.</div>
    </Drawer>
  );
}
