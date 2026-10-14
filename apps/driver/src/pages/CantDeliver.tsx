import { useRef, useState } from "react";
import { Navigate, useNavigate, useParams } from "react-router-dom";
import { useSession } from "../auth/Session";
import { plural, saveTicks, stopOf } from "../lib/run";
import { getPosition } from "../lib/where";
import { Icon } from "../ui/icons";
import { BackTop, Phone } from "../ui/kit";
import { useOnRun } from "./OnTheRun";
import { PhotoPicker } from "../ui/pictures";

const REASONS = [
  { value: "closed", label: "Closed", hint: "Nobody there", icon: "closed" },
  { value: "refused", label: "Refused", hint: "Customer said no", icon: "refused" },
  { value: "damaged", label: "Damaged", hint: "Carton not fit to leave", icon: "damaged" },
  { value: "wrong_address", label: "Wrong address", hint: "Can't find it", icon: "wrong" },
] as const;

/** DriverCantDeliver.html: why, a photo if it helps, and the cartons go
 * back on the truck. */
export function CantDeliver() {
  const params = useParams();
  const runId = Number(params.runId);
  const consignmentId = Number(params.consignmentId);
  const { queue, online, driver } = useSession();
  const me = driver!.id;
  const navigate = useNavigate();
  const run = useOnRun();
  const stop = run ? stopOf(run, consignmentId) : null;
  const [reason, setReason] = useState<string | null>(null);
  const [photo, setPhoto] = useState<Blob | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // set at once, unlike state: a second tap before the screen updates does nothing
  const going = useRef(false);

  if (!run || !stop) return <Navigate to="/" replace />;
  // finished by the office meanwhile: the run says so
  if (run.status === "done") return <Navigate to={`/run/${runId}`} replace />;

  async function confirm() {
    if (!run || !stop || !reason || going.current) return;
    going.current = true;
    setBusy(true);
    setError(null);
    try {
      const position = await getPosition();
      await queue.submit({
        path: `/v1/runs/${runId}/stops/${consignmentId}/fail`,
        body: { at: new Date().toISOString(), reason, ...(note.trim() ? { note: note.trim() } : {}),
                ...(position ? { position } : {}) },
        label: `Stop ${stop.sequence} · couldn't deliver · ${REASONS.find((r) => r.value === reason)!.label.toLowerCase()}`,
        run_id: runId, consignment_id: consignmentId, outcome: "failed",
        pictures: photo ? [{ kind: "photo", field: "photo_file", blob: photo }] : [],
      });
      try { saveTicks(me, runId, consignmentId, {}); } catch { /* kept: the ticks no longer matter */ }
      navigate(`/run/${runId}`, { state: { note: `Stop ${stop.sequence} is coming back to the depot.` } });
    } catch (e) {
      going.current = false;
      setError(e instanceof Error ? e.message : "That couldn't be kept on the phone. Try again.");
      setBusy(false);
    }
  }

  const where = stop.ship_to.suburb ? ` · ${stop.ship_to.suburb}` : "";
  return (
    <Phone label="Can't deliver">
      <BackTop to={`/run/${runId}/stop/${consignmentId}`} label="Back to the stop" title="Can't deliver"
               sub={`Stop ${stop.sequence} of ${run.stops.length} · ${stop.customer}${where}`} />
      <div className="scroll">
        <span style={{ fontSize: 16, fontWeight: 800 }}>What happened?</span>
        <div role="radiogroup" aria-label="Reason" style={{ display: "grid", gridTemplateColumns: "repeat(2,minmax(0,1fr))", gap: 8 }}>
          {REASONS.map((r) => (
            <button key={r.value} type="button" role="radio" aria-checked={reason === r.value} className="choice"
                    onClick={() => setReason(r.value)}>
              <span style={{ color: reason === r.value ? "var(--color-green)" : "var(--color-ink-2)" }}><Icon name={r.icon} size={22} /></span>
              <b style={{ fontSize: 15 }}>{r.label}</b><span className="small">{r.hint}</span>
            </button>
          ))}
        </div>
        <span className="lbl" style={{ marginTop: 4 }}>Photo</span>
        <PhotoPicker photo={photo} onPhoto={setPhoto} hint="Optional" />
        <label style={{ display: "flex", flexDirection: "column", gap: 6, fontSize: 14, fontWeight: 800 }}>
          <span>Note <span className="small" style={{ fontWeight: 600 }}>Optional</span></span>
          <input className="field" style={{ height: 50 }} type="text" maxLength={500} value={note} onChange={(e) => setNote(e.target.value)} />
        </label>
        <div className="card" style={{ display: "flex", gap: 10, alignItems: "flex-start", padding: "12px 14px", fontSize: 13, color: "var(--color-ink-2)" }}>
          <span style={{ flex: "none", color: "var(--color-green)" }}><Icon name="returns" /></span>
          <span>The <b style={{ color: "var(--color-ink)" }}>{plural(stop.packages.length, "carton")} stay on the truck</b> and come back to the depot.
            {online ? " The office sees it straight away." : " The office sees it once you're back in range."}</span>
        </div>
        {error && <div className="msg r" role="alert">{error}</div>}
      </div>
      <button type="button" className="big primary" style={{ flex: "none" }} disabled={!reason || busy} onClick={() => void confirm()}>
        <Icon name="truck" size={22} />Back on the truck</button>
    </Phone>
  );
}
