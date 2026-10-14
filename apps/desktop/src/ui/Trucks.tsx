/** The trucks on the drawn map (Main.html): each run on the road on a road of
 * its own, the ones loading and the ones that can't go in the depot yard, and
 * the card of the one picked. The buttons are the text alternative too: each
 * says the run, the driver and when it was last heard from. */
import { Link } from "react-router-dom";
import type { Run, TruckAt, Vehicle } from "../api/types";
import { about, heard, leftOpen, loadLeft, RUN_COLOURS, spot, stillOpenFrom, yard } from "../lib/live";
import { Icon } from "./icons";

const at = ([x, y]: [number, number]) => ({ left: `${x / 15.4}%`, top: `${y / 10}%` });

function TruckGlyph({ colour }: { colour: string }) {
  return (
    <svg width="34" height="24" viewBox="0 0 34 24" aria-hidden="true">
      <ellipse cx="17" cy="21" rx="15" ry="3" fill="#17251F" opacity=".15" />
      <rect x="1" y="3" width="20" height="14" rx="2" fill={colour} />
      <path d="M21 7h6.5L33 12.5V17H21Z" fill={colour} opacity=".85" />
      <circle cx="8" cy="18" r="3" fill="#17251F" /><circle cx="26" cy="18" r="3" fill="#17251F" />
    </svg>
  );
}

const done = (r: Run) => r.stops.filter((s) => s.outcome).length;

export function TruckMarkers({ runs, heardFrom, blocked, picked, onPick, now }: {
  runs: Run[]; heardFrom: Map<number, TruckAt>; blocked: Vehicle[]; picked: number | null;
  onPick: (id: number) => void; now: Date;
}) {
  const onRoad = runs.filter((r) => r.status === "departed" && !leftOpen(r));
  const loading = runs.filter((r) => r.status === "planned");
  // left open from an earlier day: most likely back, so in the yard, flagged
  const stale = runs.filter((r) => leftOpen(r));
  return (
    <>
      {onRoad.map((r, i) => {
        const h = heardFrom.get(r.id);
        const bad = !!r.vehicle.blocked;
        return (
          <button key={r.id} type="button" className={`truck${bad ? " bad" : ""}`} style={at(spot(i, done(r), r.stops.length))}
                  aria-pressed={picked === r.id} onClick={() => onPick(r.id)}
                  aria-label={`${r.run_no} · ${r.driver.name}: on the road${h ? `, last heard ${heard(h.received_at, now).text}` : ""}`}>
            <span className="tag"><span className="dot" style={{ width: 9, height: 9, background: bad ? "var(--color-red)" : RUN_COLOURS[i % 3] }} />
              {r.run_no} · {r.driver.name}</span>
            <TruckGlyph colour={RUN_COLOURS[i % 3]} />
          </button>
        );
      })}
      {loading.map((r, i) => (
        <button key={r.id} type="button" className="truck" style={at(yard(i))} aria-pressed={picked === r.id}
                onClick={() => onPick(r.id)} aria-label={`${r.run_no} · loading: ${r.driver.name}, ${r.vehicle.rego}`}>
          <span className="tag"><span className="dot" style={{ width: 9, height: 9, background: "var(--color-violet)" }} />
            {r.run_no} · loading</span>
          <TruckGlyph colour="#7B5CC4" />
        </button>
      ))}
      {stale.map((r, i) => (
        <button key={r.id} type="button" className="truck" style={at(yard(loading.length + i))} aria-pressed={picked === r.id}
                onClick={() => onPick(r.id)} aria-label={`${r.run_no} · ${r.driver.name}: ${stillOpenFrom(r).toLowerCase()}`}>
          <span className="tag"><span className="dot" style={{ width: 9, height: 9, background: "var(--color-amber)" }} />
            {r.run_no} · still open</span>
          <TruckGlyph colour="#9AA6A0" />
        </button>
      ))}
      {blocked.map((v, i) => (
        <Link key={v.rego} to="/fleet" className="truck bad" style={at(yard(loading.length + stale.length + i))}
              aria-label={`${v.rego} · ${v.state}: ${v.blocked?.message ?? ""}`}>
          <span className="tag"><span className="dot" style={{ width: 9, height: 9, background: "var(--color-red)" }} />
            {v.rego} · {v.state}</span>
          <TruckGlyph colour="#9AA6A0" />
        </Link>
      ))}
    </>
  );
}

/** The picked run's card: who and what, when it was last heard from, its
 * stops, and the estimates, labelled as such. */
export function TruckCard({ run, heardFrom, canLocate, now, onClose }: {
  run: Run; heardFrom: TruckAt | undefined; canLocate: boolean; now: Date; onClose: () => void;
}) {
  const total = run.stops.length;
  const finished = done(run);
  const next = run.stops.find((s) => !s.outcome);
  const stale = leftOpen(run);
  // a run left open from an earlier day has no times worth giving
  const e = stale ? null : run.estimate;
  const onRoad = run.status === "departed" && !stale;
  const nextAt = e && next ? e.stops.find((s) => s.consignment_id === next.consignment.id)?.at : undefined;
  const h = heardFrom ? heard(heardFrom.received_at, now) : null;
  const pill = stale ? <span className="pill p-amber">{stillOpenFrom(run)}</span>
    : run.status === "departed" ? <span className="pill p-green">On the road</span>
    : run.status === "planned" ? <span className="pill p-grey">Loading</span>
    : <span className="pill p-grey">Back at the depot</span>;
  return (
    <section className="ov panel truckcard" aria-label="Selected truck">
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <div style={{ width: 44, height: 44, borderRadius: 12, background: "var(--color-tint)", display: "grid", placeItems: "center", color: "var(--color-green)" }}>
          <Icon name="truck" size={24} /></div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 12, color: "var(--color-muted)", fontWeight: 700 }}>{run.vehicle.rego} · {run.driver.name} · {run.vehicle.type}</div>
          <div style={{ fontSize: 18, fontWeight: 800 }}>Run {run.run_no}</div>
        </div>
        <button type="button" className="closebtn" style={{ width: 36, height: 36 }} aria-label="Close" onClick={onClose}>
          <Icon name="close" size={16} width={2.4} /></button>
      </div>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        {pill}
        {onRoad && canLocate && (h
          ? <span className={h.stale ? "pill p-amber" : "small"}>Last heard {h.text}</span>
          : <span className="small">Not heard from yet</span>)}
        {run.status === "planned" && <span className="small">Not left yet</span>}
      </div>
      {stale && (
        <div className="msg a" role="status"><span>{run.stops.every((s) => s.outcome)
          ? "Every stop has an outcome: finish it in Plan runs."
          : "It was never finished. Once every stop has an outcome, finish it in Plan runs."}</span></div>)}
      <div>
        <div style={{ display: "flex", justifyContent: "space-between", fontSize: 13, marginBottom: 6 }}>
          <b>{finished} of {total} stops</b><span style={{ color: "var(--color-muted)" }}>{total - finished} to go</span></div>
        <div role="img" aria-label={`${finished} of ${total} stops done`}
             style={{ display: "grid", gridTemplateColumns: `repeat(${Math.max(total, 1)},minmax(0,1fr))`, gap: 3 }}>
          {run.stops.map((s) => (
            <span key={s.sequence} style={{ height: 8, borderRadius: 999,
              background: s.outcome === "failed" ? "var(--color-red)" : s.outcome ? "#1F8A5B" : s === next ? "#7FC3A0" : "#E3EDE6" }} />
          ))}
        </div>
      </div>
      <dl className="kv">
        <div><dt>Next stop</dt><dd>{next ? `${next.sequence} · ${next.consignment.customer}` : "Back to the depot"}</dd></div>
        {nextAt && <div><dt>Arrives</dt><dd>{about(nextAt, now)}</dd></div>}
        <div><dt>Load left</dt><dd>{loadLeft(run)}</dd></div>
        {e && <div><dt>Back at depot</dt><dd>{about(e.back_at_depot, now)}</dd></div>}
      </dl>
      {e && (
        <div className="small" style={{ marginTop: -4 }}>
          {e.basis === "average"
            ? `Times are estimates from the stop order and ${run.driver.name}'s average time per stop today.`
            : `Times are estimates from the stop order, at ${e.minutes_per_stop} minutes a stop until ${run.driver.name}'s first drop.`}
        </div>
      )}
      <div style={{ display: "grid", gridTemplateColumns: canLocate && onRoad ? "repeat(2,minmax(0,1fr))" : "1fr", gap: 8 }}>
        <Link to="/plan" className="btn primary" style={{ height: 46, fontSize: 15 }}>Open run<Icon name="go" /></Link>
        {canLocate && onRoad && (
          <Link to={`/where?run=${run.id}`} className="btn" style={{ height: 46 }}><Icon name="target" />Where is it</Link>)}
      </div>
    </section>
  );
}
