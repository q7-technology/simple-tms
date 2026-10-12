import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, ApiError, Unreachable } from "../api/client";
import type { DriverRun, PrestartOut, RunStops, StartOut } from "../api/types";
import { useSession } from "../auth/Session";
import { currentRun, loadRun, plural, rememberCurrent, saveRun } from "../lib/run";
import { clock, dayLabel, isoDay } from "../lib/time";
import { askForLocation } from "../lib/where";
import { Icon } from "../ui/icons";
import { Phone } from "../ui/kit";
import { PrestartCheck } from "./PrestartCheck";

function said(e: unknown): string {
  if (e instanceof Unreachable) return "No signal. Starting a run needs a connection, to load the whole run onto this phone.";
  if (e instanceof ApiError && e.code === "over_capacity") return `${e.message} Ask the office to move a stop.`;
  return e instanceof Error ? e.message : "Something went wrong. Try again.";
}

// the office has a reason the run can't start as the phone had it: look again
const RELOAD = new Set(["prestart_needed", "vehicle_grounded", "vehicle_overdue"]);

const CHECKED = { passed: "passed", failed: "an item failed, and the office will see it", grounded: "a critical item failed" };

function state(r: DriverRun): string {
  if (r.status === "planned" && r.prestart) {
    return `Pre-start done ${clock(new Date(r.prestart.at))}: ${CHECKED[r.prestart.result]}. `
      + "Starting tells the office you've left, and loads the whole run onto this phone.";
  }
  if (r.status === "planned") return "Do the pre-start check of the truck, then start the run.";
  if (r.status === "departed") return `On the road: ${r.delivered + r.failed} of ${plural(r.stops, "stop")} done.`;
  return `Finished: ${r.delivered} delivered, ${r.failed} coming back.`;
}

/** DriverPrestart.html: the driver's run and truck for today, the pre-start
 * check of the truck, then Start. The check may be done with no signal: it
 * waits on the phone like any confirmation, but the run starts only once the
 * office has it, since a failed critical item grounds the truck. */
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
  // the pre-start waiting on the phone, or sent, shows as it goes
  const [, bump] = useState(0);
  useEffect(() => queue.subscribe(() => bump((n) => n + 1)), [queue]);
  const reloadedFor = useRef(new Set<string>());
  // the pre-starts sent from here that the runs have been loaded again since
  const [heard, setHeard] = useState<ReadonlySet<string>>(new Set());

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
        const id = (startIds.current[r.id] ??= crypto.randomUUID());
        const out = await api.post<StartOut>(`/v1/runs/${r.id}/start`, { message_id: id });
        saveRun(me, { ...whole, status: out.status, departed_at: out.departed_at });
        // the run has started: ask now, at the depot, rather than at the first drop
        void askForLocation();
        // the office may have changed it meanwhile: keep the run as it left
        try {
          saveRun(me, await api.get<RunStops>(`/v1/runs/${r.id}/stops`));
        } catch { /* the copy from before the start will do */ }
      }
      try { rememberCurrent(me, r.id); } catch { /* only for carrying on from memory */ }
      navigate(`/run/${r.id}`);
    } catch (e) {
      setError(said(e));
      if (e instanceof ApiError && e.code && RELOAD.has(e.code)) void load();
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
  const blocked = run?.vehicle.blocked ?? null;
  // the newest pre-start this phone made for the run
  const made = run ? queue.forRun(run.id).find((i) => i.outcome === "prestart") : undefined;
  // the office has it now: load the run again to hear how it went
  useEffect(() => {
    if (made?.status === "sent" && !reloadedFor.current.has(made.id)) {
      const id = made.id;
      reloadedFor.current.add(id);
      void load().then(() => setHeard((h) => new Set(h).add(id)));
    }
  }, [made?.status, made?.id, load]);
  // one the office took of another truck no longer counts: the run was given
  // a new truck since, and that one needs its own check. Nor does one that
  // grounded the truck once it is marked fixed: the fixed truck is checked afresh
  const reply = made?.reply as PrestartOut | null | undefined;
  const fixedSince = reply?.result === "grounded" && heard.has(made!.id) && !blocked;
  const check = made && (made.status !== "sent" || (reply?.vehicle === run!.vehicle.rego && !fixedSince)
                         || !!run!.prestart) ? made : undefined;
  const checkSent = check?.status === "sent" && !run?.prestart;
  const checking = run?.status === "planned" && !blocked && !run.prestart && !check;
  const canStart = run?.status === "planned" && !blocked && !!run.prestart;

  function again() {
    if (check) void queue.dismiss(check.id);
  }

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
                {r.vehicle.blocked
                  ? <span style={{ fontSize: 11, fontWeight: 700, color: "var(--color-red-ink)" }}>
                      {r.vehicle.blocked.code === "vehicle_grounded" ? "Grounded" : "Overdue"} · {r.run_no}</span>
                  : <span style={{ fontSize: 11, fontWeight: 700, color: "var(--color-ink-2)", textTransform: "capitalize" }}>
                      {r.vehicle.type} · {r.run_no}</span>}
              </button>
            ))}
          </div>
        </div>
      )}
      {checking && run && <PrestartCheck run={run} />}
      {!checking && <div className="scroll">
        {run && blocked && run.status !== "done" && (
          <div className="msg r" role="alert">{blocked.message}{" "}
            {run.status === "planned" ? "Don't take it out: call the office." : "Call the office."}</div>
        )}
        {run && check?.status === "queued" && !run.prestart && (
          <div className="msg a" role="status">Your pre-start is kept on this phone. It goes as soon as there's signal,
            and then you can start.</div>
        )}
        {run && checkSent && <div className="msg g" role="status">Your pre-start has reached the office.</div>}
        {run && check?.status === "failed" && !run.prestart && (
          <div className="msg r" role="alert">Your pre-start wasn't taken: {check.error}
            <div style={{ marginTop: 8 }}><button type="button" className="chip" onClick={again}>Do the check again</button></div></div>
        )}
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
        {canStart && (
          <div style={{ display: "flex", gap: 8, fontSize: 12, color: "var(--color-muted)" }}>
            <Icon name="pin" size={16} width={2.2} />
            <span>Starting asks to use this phone's location. It is kept with each drop, and only while you're on a run.</span>
          </div>
        )}
      </div>}
      {!run && (
        <button type="button" className="big ghost" style={{ flex: "none" }} onClick={() => void load()}>Look again</button>
      )}
      {run && check?.status === "queued" && !run.prestart && (
        <button type="button" className="big ghost" style={{ flex: "none" }} onClick={() => void queue.drain()}>Send it now</button>
      )}
      {run && (blocked || checkSent) && run.status === "planned" && (
        <button type="button" className="big ghost" style={{ flex: "none" }} onClick={() => void load()}>Look again</button>
      )}
      {run && (canStart || run.status === "departed") && (
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
