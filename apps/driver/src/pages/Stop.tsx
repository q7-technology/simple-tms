import { useEffect, useState } from "react";
import { Link, Navigate, useParams } from "react-router-dom";
import { useQueueItems } from "../auth/Session";
import { addressLine, mapsLink } from "../lib/maps";
import { loadRun, loadTicks, outcomes, saveTicks, stopOf, type How, type Ticks } from "../lib/run";
import { matchScan } from "../lib/scan";
import { useScanWedge } from "../lib/useScanWedge";
import { useSession } from "../auth/Session";
import { Icon } from "../ui/icons";
import { Goal, OfflineBanner, Phone, RunTop } from "../ui/kit";

type Said = { tone: "g" | "a" | "r"; text: string } | null;

/** DriverStop.html: one stop. Each carton is scanned or ticked off before
 * it can be delivered. */
export function Stop() {
  const params = useParams();
  const runId = Number(params.runId);
  const consignmentId = Number(params.consignmentId);
  const { online, waiting, driver } = useSession();
  const me = driver!.id;
  const items = useQueueItems(runId);
  const run = loadRun(me, runId);
  const stop = run ? stopOf(run, consignmentId) : null;
  const [ticks, setTicks] = useState<Ticks>(() => loadTicks(me, runId, consignmentId));
  const [said, setSaid] = useState<Said>(null);

  function mark(packageNo: number, how: How | null) {
    setTicks((t) => {
      const next = { ...t };
      if (how) next[packageNo] = how; else delete next[packageNo];
      return next;
    });
  }

  // kept on the phone as they change, so a reload keeps them; said if it can't
  useEffect(() => {
    try {
      saveTicks(me, runId, consignmentId, ticks);
    } catch {
      setSaid({ tone: "r", text: "The cartons ticked couldn't be kept on the phone: its storage is full. Call the office." });
    }
  }, [me, runId, consignmentId, ticks]);

  useScanWedge((code) => {
    if (!run || !stop) return;
    const m = matchScan(code, run.stops, stop);
    if (m.kind === "here") {
      mark(m.package_no, "scan");
      setSaid({ tone: "g", text: `Carton ${m.package_no} of ${stop.packages.length} scanned.` });
    } else if (m.kind === "other") {
      setSaid({ tone: "a", text: `That carton is for stop ${m.stop.sequence} · ${m.stop.customer}. It stays on the truck.` });
    } else {
      setSaid({ tone: "r", text: "That label isn't on this run." });
    }
  });

  if (!run || !stop) return <Navigate to={run ? `/run/${runId}` : "/"} replace />;
  const done = outcomes(run, items);
  if (done.has(consignmentId)) return <Navigate to={`/run/${runId}`} replace />;
  const count = stop.packages.filter((p) => ticks[p.package_no]).length;
  const all = count === stop.packages.length;
  const t = stop.ship_to;

  return (
    <Phone label={`Stop ${stop.sequence} of ${run.stops.length}`}>
      <RunTop run={run} />
      <OfflineBanner online={online} waiting={waiting} />
      <Goal run={run} done={done} current={consignmentId} />
      <div className="scroll">
        <section aria-label="This stop" style={{ padding: "14px 16px", borderRadius: 18, background: "var(--color-green)", color: "#fff",
                                                 display: "flex", flexDirection: "column", gap: 6 }}>
          <span style={{ fontSize: 13, fontWeight: 800, letterSpacing: 0.6, color: "#D5F0E1" }}>
            STOP {stop.sequence} OF {run.stops.length}</span>
          <span style={{ fontSize: 28, fontWeight: 800, letterSpacing: -0.4, lineHeight: 1.1 }}>{stop.customer}</span>
          <span style={{ fontSize: 14, color: "#E7F4EC" }}>{addressLine(t) || "No address given"}</span>
          <a href={mapsLink(t)} target="_blank" rel="noopener noreferrer" className="big white" style={{ marginTop: 6, height: 50 }}>
            <Icon name="map" />Open in Maps</a>
        </section>
        <section aria-label="Cartons to drop" style={{ borderRadius: 18, background: "#fff", border: "1px solid var(--color-line)", overflow: "hidden" }}>
          <div style={{ display: "flex", justifyContent: "space-between", padding: "10px 12px 6px" }}>
            <span className="lbl">Cartons to drop</span>
            <span style={{ fontSize: 13, fontWeight: 800, color: "var(--color-green)" }}>{count} of {stop.packages.length}</span>
          </div>
          {stop.packages.map((p, i) => {
            const how = ticks[p.package_no];
            return (
              <div key={p.package_no} role="checkbox" aria-checked={!!how} tabIndex={0} className="carton"
                   aria-label={`${stop.wms_ref} · ${i + 1} of ${stop.packages.length}`}
                   onClick={() => mark(p.package_no, how ? null : "tick")}
                   onKeyDown={(e) => { if (e.key === " ") { e.preventDefault(); mark(p.package_no, how ? null : "tick"); } }}>
                <span className="tick"><Icon name="check" size={16} width={3} /></span>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 15, fontWeight: 800 }}>{stop.wms_ref} · {i + 1} of {stop.packages.length}</div>
                  <div className="small">
                    {[p.type.charAt(0).toUpperCase() + p.type.slice(1), p.weight_kg ? `${p.weight_kg} kg` : null]
                      .filter(Boolean).join(" · ")}</div>
                </div>
                {how === "scan" && <span className="pill p-green">Scanned</span>}
                {how === "tick" && <span className="pill p-grey">Ticked</span>}
              </div>
            );
          })}
        </section>
        <label className="scanbox">
          <span style={{ color: "var(--color-green)" }}><Icon name="scan" size={26} /></span>
          <span style={{ flex: 1 }}><b>Scan a carton label</b><small>Or tap a carton to tick it off.</small>
            <input data-scan="true" aria-label="Carton label" placeholder="or type it and press Enter" inputMode="text"
                   autoCapitalize="none" autoComplete="off" /></span>
        </label>
        {said && <div className={`msg ${said.tone}`} role="status">{said.text}</div>}
        {(t.contact || t.phone) && (
          <section aria-label="Delivery note" className="card" style={{ padding: "12px 14px", display: "flex", gap: 10 }}>
            <span style={{ color: "var(--color-ink-2)", flex: "none" }}><Icon name="note" /></span>
            <div style={{ fontSize: 14 }}><b>{t.contact ? `Ask for ${t.contact}` : `For ${stop.customer}`}</b>
              {t.phone && <div><a href={`tel:${t.phone.replace(/\s/g, "")}`}>Call {t.phone}</a></div>}</div>
          </section>
        )}
      </div>
      {!all && <div className="small" style={{ textAlign: "center", flex: "none" }}>Scan or tick every carton to deliver.</div>}
      <div style={{ display: "flex", gap: 8, flex: "none" }}>
        <Link to={`/run/${runId}/stop/${consignmentId}/cant`} className="big ghost" style={{ flex: 1 }}>Can't deliver</Link>
        {all
          ? <Link to={`/run/${runId}/stop/${consignmentId}/proof`} className="big primary" style={{ flex: 2 }}>
              <Icon name="check" size={22} width={2.6} />Delivered</Link>
          : <button type="button" className="big primary" style={{ flex: 2 }} disabled>
              <Icon name="check" size={22} width={2.6} />Delivered</button>}
      </div>
    </Phone>
  );
}
