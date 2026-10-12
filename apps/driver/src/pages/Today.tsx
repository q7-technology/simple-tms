import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, ApiError, Unreachable } from "../api/client";
import type { DriverRun, RunStops, StartOut } from "../api/types";
import { useSession } from "../auth/Session";
import { currentRun, loadRun, plural, rememberCurrent, saveRun } from "../lib/run";
import { dayLabel, isoDay } from "../lib/time";
import { getPosition } from "../lib/where";
import { Icon } from "../ui/icons";
import { Phone } from "../ui/kit";

function said(e: unknown): string {
  if (e instanceof Unreachable) return "No signal. Starting a run needs a connection, to load the whole run onto this phone.";
  if (e instanceof ApiError && e.code === "over_capacity") return `${e.message} Ask the office to move a stop.`;
  return e instanceof Error ? e.message : "Something went wrong. Try again.";
}

function state(r: DriverRun): string {
  if (r.status === "planned") return "Ready to go. Starting tells the office you've left, and loads the whole run onto this phone.";
  if (r.status === "departed") return `On the road: ${r.delivered + r.failed} of ${plural(r.stops, "stop")} done.`;
  return `Finished: ${r.delivered} delivered, ${r.failed} coming back.`;
}

/** The top of DriverPrestart.html: the driver's run and truck for today.
 * The pre-start check that goes between picking and starting comes with
 * build step 4; until then the run starts from here. */
export function Today() {
  const { driver, queue, waiting, others, signOut } = useSession();
  const me = driver!.id;
  const navigate = useNavigate();
  const [runs, setRuns] = useState<DriverRun[] | null>(null);
  const [chosen, setChosen] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // one message_id per run for this visit: pressing Start again after a dropout is the same start
  const startIds = useRef<Record<number, string>>({});
  const saved = currentRun(me);
  const savedRun = saved ? loadRun(me, saved) : null;

  const load = useCallback(async () => {
    setError(null);
    try {
      const list = await api.get<DriverRun[]>(`/v1/driver/runs?date=${isoDay(new Date())}`);
      setRuns(list);
      setChosen((c) => c ?? list.find((r) => r.status !== "done")?.id ?? list[0]?.id ?? null);
    } catch (e) {
      setError(e instanceof Unreachable ? "No signal, so today's runs can't be loaded." : said(e));
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  async function go(r: DriverRun) {
    setBusy(true);
    setError(null);
    try {
      const whole = await api.get<RunStops>(`/v1/runs/${r.id}/stops`);
      saveRun(me, whole);
      if (whole.status === "planned") {
        // asked here, at the depot, rather than at the first drop
        void getPosition(8000);
        const id = (startIds.current[r.id] ??= crypto.randomUUID());
        const out = await api.post<StartOut>(`/v1/runs/${r.id}/start`, { message_id: id });
        saveRun(me, { ...whole, status: out.status, departed_at: out.departed_at });
        // the office may have changed it meanwhile: keep the run as it left
        try {
          saveRun(me, await api.get<RunStops>(`/v1/runs/${r.id}/stops`));
        } catch { /* the copy from before the start will do */ }
      }
      try { rememberCurrent(me, r.id); } catch { /* only for carrying on from memory */ }
      navigate(`/run/${r.id}`);
    } catch (e) {
      setError(said(e));
    } finally {
      setBusy(false);
    }
  }

  function leave() {
    if (waiting > 0 && !window.confirm(
      `${plural(waiting, "confirmation")} still waiting to send. They go once you sign in again. Sign out?`)) return;
    signOut();
  }

  const run = runs?.find((r) => r.id === chosen) ?? null;
  return (
    <Phone label="Today's run">
      <div className="top">
        <div style={{ flex: 1, minWidth: 0 }}><div className="ttl">Today's run</div>
          <div className="st">{driver?.display_name} · {dayLabel(new Date())}</div></div>
        <button type="button" className="chip" onClick={leave}>Sign out</button>
      </div>
      {runs && runs.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 6, flex: "none" }}>
          <span className="lbl">Your truck</span>
          <div role="radiogroup" aria-label="Pick your run" style={{ display: "flex", gap: 6 }}>
            {runs.map((r) => (
              <button key={r.id} type="button" role="radio" aria-checked={r.id === chosen} onClick={() => setChosen(r.id)}
                      className="choice" style={{ flex: 1, minWidth: 0, minHeight: 0, padding: "8px 6px", textAlign: "center", alignItems: "center" }}>
                <b style={{ display: "block", fontSize: 14 }}>{r.vehicle.rego}</b>
                <span style={{ fontSize: 11, fontWeight: 700, color: "var(--color-ink-2)", textTransform: "capitalize" }}>
                  {r.vehicle.type} · {r.run_no}</span>
              </button>
            ))}
          </div>
        </div>
      )}
      <div className="scroll">
        {run && (
          <section className="card" aria-label={`Run ${run.run_no}`} style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <div className="lbl">{run.run_no}{run.date !== isoDay(new Date()) ? ` · from ${dayLabel(new Date(`${run.date}T00:00`))}` : ""}</div>
            <div style={{ fontSize: 24, fontWeight: 800 }}>{plural(run.stops, "stop")} · {plural(run.packages, "package")}</div>
            <div style={{ fontSize: 14, color: "var(--color-ink-2)" }}>{state(run)}</div>
          </section>
        )}
        {runs?.length === 0 && (
          <section className="card"><b>No run for you today.</b>
            <div style={{ fontSize: 14, color: "var(--color-ink-2)", marginTop: 4 }}>Check with the office, then look again.</div></section>
        )}
        {others.map((o) => (
          <div key={o.driver_id} className="msg a" role="status">
            {`${plural(o.count, "confirmation")} from ${o.name} ${o.count === 1 ? "is" : "are"} waiting on this phone. `
              + `They go when ${o.name} signs in.`}</div>
        ))}
        {error && <div className="msg r" role="alert">{error}</div>}
        {error && savedRun && savedRun.status !== "done" && (
          <Link to={`/run/${savedRun.id}`} className="big ghost">Carry on with {savedRun.run_no} from memory</Link>
        )}
        {run?.status === "planned" && (
          <div style={{ display: "flex", gap: 8, fontSize: 12, color: "var(--color-muted)" }}>
            <Icon name="pin" size={16} width={2.2} />
            <span>Starting asks to use this phone's location. It is kept with each drop, and only while you're on a run.</span>
          </div>
        )}
      </div>
      {!run && (
        <button type="button" className="big ghost" style={{ flex: "none" }} onClick={() => void load()}>Look again</button>
      )}
      {run && run.status !== "done" && (
        <button type="button" className="big primary" style={{ flex: "none" }} disabled={busy} onClick={() => void go(run)}>
          <Icon name="truck" size={22} />{run.status === "planned" ? "Start run" : "Carry on"}</button>
      )}
      {queue.failed().length > 0 && (
        <div className="small" style={{ color: "var(--color-red-ink)" }}>
          {plural(queue.failed().length, "confirmation")} wasn't taken. Open the run to see why.</div>
      )}
    </Phone>
  );
}
