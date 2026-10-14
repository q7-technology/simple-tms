import { useCallback, useEffect, useMemo, useRef, useState, type DragEvent, type FormEvent } from "react";
import { Link, useOutletContext } from "react-router-dom";
import { api, ApiError, Unreachable } from "../api/client";
import type { Card, Carrier, Consignment, Driver, Page, Run, Vehicle } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { clock, dayOptions } from "../lib/days";
import { leftOpen, stillOpenFrom } from "../lib/live";
import { plain, toMicro } from "../lib/decimal";
import { afterAdding, loadOf, type Gauge } from "../lib/load";
import { cardOf, dropPosition, kg, m3, moved, packagesLabel, positionWithout, runColour } from "../lib/plan";
import { ActionSelect } from "../ui/ActionSelect";
import { Drawer } from "../ui/Drawer";
import { Icon } from "../ui/icons";

type Note = { tone: "r" | "a" | "g"; text: string } | null;
interface Dragging { card: Card; from: number | null }
interface Target { run: number | null; position: number }

function said(e: unknown): string {
  // a reorder from a board that was out of date: the board reloads after every change
  if (e instanceof ApiError && e.code === "stale") return "Someone else changed this run. Here it is as it is now.";
  if (e instanceof Unreachable) return e.message;
  return e instanceof ApiError ? e.message : "Simple TMS could not be reached. Check the connection and try again.";
}

function Meter({ label, unit, g }: { label: string; unit: string; g: Gauge }) {
  const tone = g.state === "over" ? " r" : g.state === "near" ? " a" : "";
  return (
    <div className={`meter${tone}`}>
      <div className="row"><span>{label}</span>
        <span className="num"><b>{plain(g.used)}</b> / {plain(g.max)} {unit}</span></div>
      <div className="track" role="img" aria-label={`${label} ${plain(g.used)} of ${plain(g.max)} ${unit}`}>
        <span style={{ width: `${Math.round(g.fill * 100)}%` }} />
      </div>
    </div>
  );
}

/** Why a truck can't go, in a word: grounded, overdue or deactivated. */
function blockedWord(b: { code: string }): string {
  return b.code === "vehicle_grounded" ? "Grounded" : "Overdue";
}

/** A truck that can't take a run: deactivated, grounded by a failed
 * pre-start, or with its rego or service overdue on the day. */
function Unavailable({ v, reason, detail }: { v: Vehicle; reason: string; detail: string }) {
  return (
    <section className="lane out" aria-label={`${v.rego}, unavailable`}>
      <div className="lhead"><span className="swatch" style={{ background: "#A9B3AE" }} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div className="t" style={{ color: "var(--color-ink-2)" }}>{v.rego}</div>
          <div className="s">{v.type} · {plain(toMicro(v.max_kg))} kg</div></div></div>
      <span className="pill p-red" style={{ alignSelf: "flex-start" }}><Icon name="ban" size={13} width={2.4} />{reason}</span>
      <div className="small">{detail}</div>
      <Link to="/fleet" style={{ fontSize: 13, fontWeight: 700 }}>Open in Fleet</Link>
    </section>
  );
}

/** After moving a stop up or down: the same button, the other one once that
 * is off (the stop is now first or last), or the stop itself. */
function keepOn(id: number, way: "up" | "down"): string[] {
  const other = way === "up" ? "down" : "up";
  return [`[data-kb="${way}-${id}"]`, `[data-kb="${other}-${id}"]`, `[data-stop="${id}"]`];
}

const REASONS: Record<string, string> = {
  closed: "closed", refused: "refused", damaged: "damaged", wrong_address: "wrong address",
};

/** What the driver recorded at a stop, once the run is on the road. */
function outcomePill(s: Run["stops"][number]) {
  if (s.outcome === "delivered") return <span className="pill p-green">Delivered</span>;
  if (s.outcome === "failed") {
    return <span className="pill p-red">Not delivered{s.reason ? `: ${REASONS[s.reason] ?? s.reason}` : ""}</span>;
  }
  return null;
}

/** How a run on the road, or back, is going. */
function tripLine(run: Run): string {
  const delivered = run.stops.filter((s) => s.outcome === "delivered").length;
  const failed = run.stops.filter((s) => s.outcome === "failed").length;
  const parts = [`Delivered ${delivered} of ${run.stops.length}`];
  if (failed) parts.push(`${failed} coming back`);
  if (run.status === "done" && run.finished_at) parts.push(`finished ${clock(new Date(run.finished_at))}`);
  return parts.join(" · ") + ".";
}

function sheetLine(run: Run): Note {
  const s = run.run_sheet;
  if (!s) return null;
  if (s.status === "printed") return { tone: "g", text: `Run sheet printed on ${s.printer}.` };
  if (s.status === "failed") return { tone: "r", text: `Run sheet didn't print: ${s.last_error ?? "Platen said it failed"}` };
  if (s.status === "accepted") return { tone: "g", text: `Run sheet sent to ${s.printer}.` };
  return s.last_error
    ? { tone: "a", text: `Run sheet waiting to go to Platen: ${s.last_error}` }
    : { tone: "a", text: "Run sheet waiting to go to Platen." };
}

/** PlanRuns.html: the tray of consignments to plan, a lane per run, and the
 * trucks that can't take one. Drag to plan; every drag has a keyboard way too. */
export function PlanRuns() {
  const { reloadCounts } = useOutletContext<{ reloadCounts: () => void }>();
  const { can } = useAuth();
  const days = useMemo(() => dayOptions(new Date()), []);
  const [day, setDay] = useState(days[1].value);
  const [runs, setRuns] = useState<Run[]>([]);
  const [tray, setTray] = useState<Card[]>([]);
  const [vehicles, setVehicles] = useState<Vehicle[]>([]);
  const [drivers, setDrivers] = useState<Driver[]>([]);
  const [carriers, setCarriers] = useState<Carrier[]>([]);
  const [filter, setFilter] = useState("");
  const [note, setNote] = useState<Note>(null);
  const [savedAt, setSavedAt] = useState<Date | null>(null);
  const [busy, setBusy] = useState(false);
  const [adding, setAdding] = useState(false);
  const [drag, setDrag] = useState<Dragging | null>(null);
  const [target, setTarget] = useState<Target | null>(null);
  // Where the keyboard goes once a change is made and the board reloaded: the
  // control or the stop it moved, the first of these that is there and on.
  // Without it the moved stop's controls are redrawn and focus drops to the page.
  const focusNext = useRef<string[] | null>(null);

  useEffect(() => {
    if (busy || !focusNext.current) return;
    const wanted = focusNext.current;
    focusNext.current = null;
    for (const selector of wanted) {
      const el = document.querySelector<HTMLElement>(selector);
      if (el && !(el as HTMLButtonElement).disabled) {
        // the controls are hidden until their stop has focus, and a hidden
        // button cannot take it: the stop first, then the button
        el.closest<HTMLElement>(".stop, .con")?.focus();
        el.focus();
        return;
      }
    }
  }, [busy]);

  // Only the latest load draws the board: a slow answer for the day before, or
  // an old poll, must not overwrite a newer one.
  const latestLoad = useRef(0);
  const load = useCallback(async () => {
    const mine = ++latestLoad.current;
    let answers;
    try {
      answers = await Promise.all([
        // today, with the runs left open from earlier days, for the office to finish
        api.get<Page<Run>>("/v1/runs", day === days[0].value ? { date: day, left_open: true } : { date: day }),
        // packed, and any the driver couldn't deliver, back to be planned again
        api.get<Page<Consignment>>("/v1/consignments", { status: "packed,failed", limit: 2000 }),
        // where each truck stands on the day being planned
        api.get<Page<Vehicle>>("/v1/vehicles", { date: day }),
        api.get<Page<Driver>>("/v1/drivers", { active: true }),
      ]);
    } catch (e) {
      if (mine === latestLoad.current) throw e;
      return;
    }
    if (mine !== latestLoad.current) return;
    const [r, t, v, d] = answers;
    setRuns(r.items);
    setTray(t.items.map(cardOf));
    setVehicles(v.items);
    setDrivers(d.items);
    // the carriers a consignment can go with instead; the board doesn't wait on them
    const c = await api.get<Page<Carrier>>("/v1/carriers", { active: true }).catch(() => null);
    if (c && mine === latestLoad.current) setCarriers(c.items);
  }, [day, days]);

  useEffect(() => {
    load().catch((e) => setNote({ tone: "r", text: said(e) }));
    // what another dispatcher has done turns up within 20 s
    const t = setInterval(() => { load().catch(() => undefined); }, 20_000);
    return () => clearInterval(t);
  }, [load]);

  async function act(fn: () => Promise<unknown>, done: string | null = null) {
    setBusy(true);
    try {
      await fn();
      setSavedAt(new Date());
      setNote(done ? { tone: "g", text: done } : null);
    } catch (e) {
      setNote({ tone: "r", text: said(e) });
    } finally {
      await load().catch(() => undefined);
      reloadCounts();
      setBusy(false);
    }
  }

  // the runs that can take stops: planned, on a truck that can go
  const planned = runs.filter((r) => r.status === "planned" && r.vehicle.active && !r.vehicle.blocked);
  const assign = (runId: number, c: Card, position?: number) =>
    act(() => api.post(`/v1/runs/${runId}/assign`, { consignment_id: c.id, position }));
  const unassign = (runId: number, c: Card) =>
    act(() => api.post(`/v1/runs/${runId}/unassign`, { consignment_id: c.id }));
  /** Onto the carrier's manifest for the day being planned, off its run if it was on one. */
  function book(carrierId: string, c: Card) {
    const carrier = carriers.find((x) => String(x.id) === carrierId);
    void act(() => api.post(`/v1/carriers/${carrierId}/book`, { consignment_id: c.id, date: day }),
             `${c.customer} is on the carrier bay for ${carrier?.name ?? "the carrier"}. Add its con note in Carriers.`);
  }
  // with the order as drawn, so the API can refuse it if someone else has moved a stop since
  const reorder = (run: Run, ids: number[]) =>
    act(() => api.post(`/v1/runs/${run.id}/reorder`,
                       { consignment_ids: ids, seen: run.stops.map((s) => s.consignment.id) }));

  /** For a driver who is back but didn't finish on the phone. Every stop has
   * to be delivered or coming back first; the API checks it too. */
  function finish(run: Run) {
    if (!window.confirm(`${run.run_no} is back at the depot? It is marked finished, and nothing more can be recorded on it.`)) return;
    void act(() => api.post(`/v1/runs/${run.id}/finish`, { message_id: crypto.randomUUID() }),
             `${run.run_no} is finished.`);
  }

  function depart(run: Run) {
    if (!window.confirm(`${run.run_no} leaves now? Each consignment on it is marked shipped in Simple WMS.`)) return;
    void act(() => api.post(`/v1/runs/${run.id}/depart`), `${run.run_no} has left. Simple WMS is being told.`);
  }

  async function printAll() {
    const ready = runs.filter((r) => r.status !== "cancelled" && r.stops.length > 0);
    if (ready.length === 0) { setNote({ tone: "a", text: "There are no runs with stops to print." }); return; }
    setBusy(true);
    const sent: string[] = [];
    const refused: string[] = [];
    for (const r of ready) {
      try {
        await api.post(`/v1/runs/${r.id}/print`);
        sent.push(r.run_no);
      } catch (e) {
        refused.push(said(e));
      }
    }
    const parts = [];
    if (sent.length) parts.push(`Sent ${sent.length} run ${sent.length === 1 ? "sheet" : "sheets"} to Platen.`);
    parts.push(...new Set(refused));
    setNote({ tone: refused.length ? (sent.length ? "a" : "r") : "g", text: parts.join(" ") });
    await load().catch(() => undefined);
    setBusy(false);
  }

  // ---- dragging -----------------------------------------------------------
  function startDrag(e: DragEvent, d: Dragging) {
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", String(d.card.id));
    setDrag(d);
  }
  function endDrag() { setDrag(null); setTarget(null); }

  function overRun(e: DragEvent, run: Run) {
    if (!drag) return;
    e.preventDefault();
    const el = (e.target as HTMLElement).closest<HTMLElement>("[data-index]");
    const position = el
      ? dropPosition(Number(el.dataset.index), e.clientY, el.getBoundingClientRect().top,
                     el.getBoundingClientRect().height)
      : run.stops.length + 1;
    if (target?.run !== run.id || target.position !== position) setTarget({ run: run.id, position });
  }

  function dropOnRun(e: DragEvent, run: Run) {
    e.preventDefault();
    const d = drag;
    const position = target?.run === run.id ? target.position : run.stops.length + 1;
    endDrag();
    if (!d) return;
    if (d.from === run.id) {
      // the board as it is now: a poll may have redrawn it since the drag began
      const ids = run.stops.map((s) => s.consignment.id);
      const from = ids.indexOf(d.card.id);
      if (from < 0) { load().catch(() => undefined); return; }
      const to = positionWithout(from, position) - 1;
      if (to !== from) void reorder(run, moved(ids, from, to));
    } else {
      void assign(run.id, d.card, position);
    }
  }

  function dropOnTray(e: DragEvent) {
    e.preventDefault();
    const d = drag;
    endDrag();
    if (d && d.from !== null) void unassign(d.from, d.card);
  }

  function dropHint(run: Run) {
    if (!drag || target?.run !== run.id) return null;
    if (drag.from === run.id) return <div className="drop">Drop here</div>;
    const state = afterAdding(run.stops.map((s) => s.consignment), drag.card, run.vehicle.max_kg, run.vehicle.max_m3);
    if (state === "over") return <div className="drop r">Drop here · {drag.card.customer} won't fit</div>;
    return <div className="drop">Drop here · {drag.card.customer} fits{state === "near" ? ", nearly full" : ""}</div>;
  }

  // ---- adding a run ---------------------------------------------------------
  const busyTrucks = new Set(runs.map((r) => r.vehicle.rego));
  const free = vehicles.filter((v) => v.active && !v.blocked && !busyTrucks.has(v.rego));
  // a blocked truck with a run shows it on its lane instead
  const unavailable = vehicles.filter((v) => !v.active || (v.blocked && !busyTrucks.has(v.rego)));

  /** A run whose truck can't go, given one that can. */
  const swap = (run: Run, rego: string) =>
    act(() => api.patch(`/v1/runs/${run.id}`, { vehicle: rego }), `${run.run_no} goes on ${rego} now.`);

  function addRun(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    setAdding(false);
    void act(() => api.post("/v1/runs", { date: day, vehicle: form.get("vehicle"), driver: form.get("driver") }));
  }

  // ---- drawing --------------------------------------------------------------
  const shown = tray.filter((c) => {
    const q = filter.trim().toLowerCase();
    return !q || c.customer.toLowerCase().includes(q) || (c.suburb ?? "").toLowerCase().includes(q);
  });
  // A lane is as drawn (about 250 px) and no wider than 300 px with only a run
  // or two; with none, there is no repeat(0, …), which would void the grid.
  const columns = ["250px", ...runs.map(() => "minmax(210px, 300px)"), ...(unavailable.length ? ["172px"] : [])].join(" ");
  const minWidth = 262 + runs.length * 222 + (unavailable.length ? 184 : 0);

  return (
    <Drawer label="Plan runs" wide head={
      <>
        <div style={{ flex: 1, minWidth: 200 }}>
          <div className="eyebrow" style={{ color: "var(--color-green)" }}>Own fleet</div>
          <h1>Plan runs</h1>
          <div className="sub">Drag consignments onto a run, then drag stops into order. You know the roads, so the order is yours.</div>
        </div>
        <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13, fontWeight: 700, color: "var(--color-ink-2)" }}>Day
          <select className="field" style={{ width: "auto", fontWeight: 700 }} value={day} onChange={(e) => setDay(e.target.value)}>
            {days.map((d) => <option key={d.value} value={d.value}>{d.label}</option>)}
          </select>
        </label>
        <span className="small">
          {savedAt ? `Saved ${clock(savedAt)} · ` : ""}
          {free.length === 0 ? "No truck free" : `${free.length} ${free.length === 1 ? "truck" : "trucks"} free: ${free.map((v) => v.rego).join(", ")}`}
        </span>
        <button type="button" className="btn" onClick={() => setAdding(!adding)} disabled={busy}><Icon name="plus" size={16} width={2.6} />Add a run</button>
        <button type="button" className="btn primary" onClick={() => void printAll()} disabled={busy}><Icon name="print" size={16} />Print run sheets</button>
      </>
    }>
      {adding && (
        <form className="lane" style={{ flexDirection: "row", flexWrap: "wrap", alignItems: "flex-end", gap: 12 }} onSubmit={addRun} aria-label="Add a run">
          <label className="small" style={{ display: "flex", flexDirection: "column", gap: 4, fontWeight: 700 }}>Truck
            <select className="field" name="vehicle" required>
              {free.map((v) => <option key={v.rego} value={v.rego}>{v.rego} · {v.type} · {plain(toMicro(v.max_kg))} kg</option>)}
            </select></label>
          <label className="small" style={{ display: "flex", flexDirection: "column", gap: 4, fontWeight: 700 }}>Driver
            <select className="field" name="driver" required>
              {drivers.map((d) => <option key={d.code} value={d.code}>{d.name} ({d.code})</option>)}
            </select></label>
          <button className="btn primary" type="submit" disabled={free.length === 0 || drivers.length === 0}>Add the run</button>
          <button className="btn" type="button" onClick={() => setAdding(false)}>Cancel</button>
          {free.length === 0 && <span className="small">Every active truck has a run on this day.</span>}
        </form>
      )}

      {note && <div className={`msg ${note.tone}`} role={note.tone === "r" ? "alert" : "status"}>{note.text}</div>}

      <div className="boardwrap"><div className="board" style={{ gridTemplateColumns: columns, minWidth }}>
        <section className={`lane tray${drag?.from != null && target?.run === null ? " target" : ""}`} aria-label="Consignments to plan"
                 onDragOver={(e) => { if (drag?.from != null) { e.preventDefault(); setTarget({ run: null, position: 0 }); } }}
                 onDrop={dropOnTray}>
          <div className="lhead"><span className="t" style={{ flex: 1 }}>To plan</span><span className="pill p-amber">{tray.length}</span></div>
          <label className="search" style={{ height: 40, background: "#fff" }}><Icon name="search" size={16} />
            <span className="sr">Filter consignments</span>
            <input type="search" placeholder="Customer or suburb" value={filter} onChange={(e) => setFilter(e.target.value)} /></label>
          {drag?.from != null && <div className="drop">Drop here to take it off its run</div>}
          {shown.map((c) => (
            <div key={c.id} className={`con${drag?.card.id === c.id ? " dragging" : ""}`} data-card={c.id} draggable tabIndex={0}
                 onDragStart={(e) => startDrag(e, { card: c, from: null })} onDragEnd={endDrag}>
              <span style={{ width: 22, display: "grid", placeItems: "center", color: "#9AA8A0" }} aria-hidden="true"><Icon name="grip" width={2.4} /></span>
              <div className="c"><b>{c.customer}</b><span className="small">{c.suburb}</span>
                {c.status === "failed" && <div><span className="pill p-red">Came back: not delivered</span></div>}
                <div className="meta num"><span>{packagesLabel(c)}</span><span>{kg(c)}</span><span>{m3(c)}</span></div>
                <div className="ref">From WMS delivery {c.wms_ref}</div>
              </div>
              <div className="kb">
                <ActionSelect label={`Put ${c.customer} on a run`} placeholder="Put on a run…" disabled={busy || planned.length === 0}
                              onChoose={(to) => { focusNext.current = [`[data-stop="${c.id}"]`]; void assign(Number(to), c); }}>
                  {planned.map((r) => <option key={r.id} value={r.id}>{r.run_no} · {r.driver.name}</option>)}
                </ActionSelect>
                {carriers.length > 0 && (
                  <ActionSelect label={`Send ${c.customer} with a carrier`} placeholder="With a carrier…" disabled={busy}
                                onChoose={(to) => book(to, c)}>
                    {carriers.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
                  </ActionSelect>)}
              </div>
            </div>
          ))}
          {tray.length === 0 && <div className="small" style={{ textAlign: "center" }}>Nothing to plan. Packed deliveries from Simple WMS land here.</div>}
        </section>

        {runs.map((run, i) => {
          const colour = runColour(i);
          const open = run.status === "planned";
          const blocked = run.vehicle.blocked ?? null;
          // a planned run on a truck that can't go takes no more stops
          const takes = open && run.vehicle.active && !blocked;
          const l = loadOf(run.stops.map((s) => s.consignment), run.vehicle.max_kg, run.vehicle.max_m3);
          const ids = run.stops.map((s) => s.consignment.id);
          const others = planned.filter((r) => r.id !== run.id);
          const sheet = sheetLine(run);
          const pill = open && blocked ? <span className="pill p-red"><Icon name="ban" size={13} width={2.4} />{blockedWord(blocked)}</span>
            : leftOpen(run) ? <span className="pill p-amber">{stillOpenFrom(run)}</span>
            : run.status === "done" ? <span className="pill p-grey">Done</span>
            : !open ? <span className="pill p-grey">Departed</span>
            : l.state === "over" ? <span className="pill p-red">Over</span>
            : l.state === "near" ? <span className="pill p-amber">Nearly full</span>
            : <span className="pill p-green">{run.stops.length} {run.stops.length === 1 ? "stop" : "stops"}</span>;
          const hint = dropHint(run);
          const hintAt = target?.run === run.id ? target.position - 1 : -1;
          return (
            <section key={run.id} aria-label={`Run ${run.run_no}`}
                     className={`lane${l.state === "over" ? " over" : l.state === "near" ? " near" : ""}${hint ? " target" : ""}`}
                     onDragOver={takes ? (e) => overRun(e, run) : undefined}
                     onDrop={takes ? (e) => dropOnRun(e, run) : undefined}>
              <div className="lhead"><span className="swatch" style={{ background: colour }} />
                <div style={{ flex: 1, minWidth: 0 }}><div className="t">{run.run_no} · {run.driver.name}</div>
                  <div className="s">{run.vehicle.rego} · {run.vehicle.type}</div></div>{pill}</div>
              {blocked && run.status !== "done" && (
                <div className="msg r" role="status"><Icon name="ban" size={14} width={2.4} /><span><b>{blocked.message}</b>
                  {open ? " Give the run another truck, or move its stops to another run." : " It is out on this run: call the driver."}</span></div>)}
              {open && blocked && free.length > 0 && (
                <ActionSelect label={`Give ${run.run_no} another truck`} placeholder="Another truck…" disabled={busy}
                              onChoose={(rego) => void swap(run, rego)}>
                  {free.map((v) => <option key={v.rego} value={v.rego}>{v.rego} · {v.type}</option>)}
                </ActionSelect>)}
              <Meter label="Weight" unit="kg" g={l.weight} />
              <Meter label="Volume" unit="m³" g={l.volume} />
              {l.state === "over" && <div className="msg r"><Icon name="warn" size={14} width={2.4} /><span><b>{l.headline}</b> {l.message}</span></div>}
              {run.stops.map((s, j) => (
                <div key={s.consignment.id}>
                  {j === hintAt && hint}
                  <div className={`stop${drag?.card.id === s.consignment.id ? " dragging" : ""}`} data-index={j} data-stop={s.consignment.id} tabIndex={open ? 0 : undefined}
                       draggable={open} onDragStart={(e) => startDrag(e, { card: s.consignment, from: run.id })} onDragEnd={endDrag}>
                    {open && <span className="h" aria-hidden="true"><Icon name="grip" width={2.4} /></span>}
                    <span className="n" style={{ background: colour }}>{s.sequence}</span>
                    <span className="c"><b>{s.consignment.customer}</b>
                      <span>{[s.consignment.suburb, packagesLabel(s.consignment), kg(s.consignment)].filter(Boolean)
                        .map((part, k) => <span key={k}>{k > 0 && " · "}<span style={{ whiteSpace: "nowrap" }}>{part}</span></span>)}</span></span>
                    {!open && outcomePill(s)}
                    {open && (
                      <span className="kb">
                        <button type="button" aria-label={`Move ${s.consignment.customer} up`} disabled={busy || j === 0}
                                data-kb={`up-${s.consignment.id}`}
                                onClick={() => {
                                  focusNext.current = keepOn(s.consignment.id, "up");
                                  void reorder(run, moved(ids, j, j - 1));
                                }}><Icon name="up" size={14} /></button>
                        <button type="button" aria-label={`Move ${s.consignment.customer} down`} disabled={busy || j === ids.length - 1}
                                data-kb={`down-${s.consignment.id}`}
                                onClick={() => {
                                  focusNext.current = keepOn(s.consignment.id, "down");
                                  void reorder(run, moved(ids, j, j + 1));
                                }}><Icon name="down" size={14} /></button>
                        <ActionSelect label={`Move ${s.consignment.customer} to`} placeholder="Move to…" disabled={busy}
                                      onChoose={(to) => {
                                        if (to === "tray") {
                                          focusNext.current = [`[data-card="${s.consignment.id}"]`];
                                          void unassign(run.id, s.consignment);
                                        } else if (to.startsWith("carrier:")) {
                                          book(to.slice("carrier:".length), s.consignment);
                                        } else {
                                          focusNext.current = [`[data-stop="${s.consignment.id}"]`];
                                          void assign(Number(to), s.consignment);
                                        }
                                      }}>
                          <option value="tray">Back to the tray</option>
                          {others.map((r) => <option key={r.id} value={r.id}>{r.run_no} · {r.driver.name}</option>)}
                          {carriers.length > 0 && (
                            <optgroup label="With a carrier">
                              {carriers.map((x) => <option key={x.id} value={`carrier:${x.id}`}>{x.name}</option>)}
                            </optgroup>)}
                        </ActionSelect>
                      </span>
                    )}
                  </div>
                </div>
              ))}
              {hintAt >= run.stops.length && hint}
              {open && run.stops.length === 0 && !hint && <div className="small" style={{ textAlign: "center", padding: 10 }}>Drag consignments here.</div>}
              {l.state === "near" && <div className="msg a"><Icon name="info" size={14} width={2.4} /><span>{l.message}</span></div>}
              {(run.load.unknown_weight > 0 || run.load.unknown_volume > 0) && (
                <div className="small">Some packages have no weight or size, so the bars may read low.</div>)}
              {sheet && <div className={`msg ${sheet.tone}`}>{sheet.text}</div>}
              {open && !blocked && (
                <div className="small">{run.prestart
                  ? `Pre-start ${clock(new Date(run.prestart.at))}: ${run.prestart.result === "passed" ? "passed" : "an item failed"}.`
                  : "No pre-start yet."}</div>)}
              {!open && <div className="small">{tripLine(run)}</div>}
              {!open && (
                <div className="small">Marked shipped in Simple WMS: {run.shipping.done} of {run.shipping.done + run.shipping.pending + run.shipping.failed}.</div>)}
              {run.shipping.failed > 0 && (
                <div className="msg r"><span>{run.shipping.failed} not marked shipped in Simple WMS after five tries.</span>
                  <button type="button" className="btn sm" disabled={busy}
                          onClick={() => void act(() => api.post(`/v1/runs/${run.id}/retry-ship`), "Trying Simple WMS again.")}>Try again</button></div>)}
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                {run.stops.length > 0 && (
                  <button type="button" className="btn sm" disabled={busy || l.state === "over" || (open && !!blocked)}
                          onClick={() => void act(() => api.post(`/v1/runs/${run.id}/print`), `${run.run_no} run sheet sent to Platen.`)}>
                    <Icon name="print" size={14} />Print</button>)}
                {open && run.stops.length > 0 && (
                  <button type="button" className="btn sm primary" disabled={busy || l.state === "over" || !!blocked} onClick={() => depart(run)}>
                    Leaves now<Icon name="go" size={14} width={2.4} /></button>)}
                {run.status === "departed" && can("runs:write") && run.stops.length > 0
                  && run.stops.every((s) => s.outcome) && (
                  <button type="button" className="btn sm primary" disabled={busy} onClick={() => finish(run)}>
                    Finish run</button>)}
                {open && run.stops.length === 0 && (
                  <button type="button" className="btn sm" disabled={busy}
                          onClick={() => void act(() => api.post(`/v1/runs/${run.id}/cancel`))}>Remove run</button>)}
              </div>
            </section>
          );
        })}

        {unavailable.length > 0 && (
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            {unavailable.map((v) => (
              v.active && v.blocked
                ? <Unavailable key={v.rego} v={v} reason={blockedWord(v.blocked)} detail={v.blocked.message} />
                : <Unavailable key={v.rego} v={v} reason="Deactivated"
                               detail="Can't take a run until a fleet manager reactivates it." />))}
          </div>
        )}
      </div></div>
      <div className="small">Print run sheets sends each run to the dispatch printer through Platen. When a run leaves, each consignment is marked shipped in Simple WMS. A consignment sent with a carrier waits on the carrier bay: see Carriers.</div>
    </Drawer>
  );
}
