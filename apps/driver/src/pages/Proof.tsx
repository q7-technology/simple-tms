import { useRef, useState } from "react";
import { Link, Navigate, useParams } from "react-router-dom";
import { useQueueItems, useSession } from "../auth/Session";
import { toBlob } from "../lib/picture";
import type { PictureKind } from "../lib/queue";
import { loadTicks, nextStop, outcomes, plural, saveTicks, stopOf } from "../lib/run";
import { clock } from "../lib/time";
import { getPosition } from "../lib/where";
import { Icon } from "../ui/icons";
import { BackTop, Phone } from "../ui/kit";
import { useOnRun } from "./OnTheRun";
import { PhotoPicker, SignaturePad } from "../ui/pictures";

interface Dinged { sequence: number; doneCount: number; cartons: number; by: string; at: Date }

/** DriverProof.html: a signature with the printed name, a photo, or both;
 * then "Ding!" and on to the next stop. */
export function Proof() {
  const params = useParams();
  const runId = Number(params.runId);
  const consignmentId = Number(params.consignmentId);
  const { queue, driver } = useSession();
  const me = driver!.id;
  const items = useQueueItems(runId);
  const run = useOnRun();
  const stop = run ? stopOf(run, consignmentId) : null;
  const canvas = useRef<HTMLCanvasElement | null>(null);
  const [signed, setSigned] = useState(false);
  const [name, setName] = useState("");
  const [photo, setPhoto] = useState<Blob | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [ding, setDing] = useState<Dinged | null>(null);
  // set at once, unlike state: a second tap before the screen updates does nothing
  const going = useRef(false);
  // as the stop page left them: cleared on the phone once the delivery is kept
  const [ticks] = useState(() => loadTicks(me, runId, consignmentId));

  if (!run || !stop) return <Navigate to="/" replace />;
  // finished by the office meanwhile: the run says so
  if (!ding && run.status === "done") return <Navigate to={`/run/${runId}`} replace />;
  const back = `/run/${runId}/stop/${consignmentId}`;
  if (!ding && stop.packages.some((p) => !ticks[p.package_no])) return <Navigate to={back} replace />;
  const ready = (signed && name.trim().length > 0) || photo !== null;

  async function confirm() {
    if (!run || !stop || going.current) return;
    going.current = true;
    setBusy(true);
    setError(null);
    try {
      const pictures: { kind: PictureKind; field: string; blob: Blob }[] = [];
      const withSignature = signed && name.trim().length > 0 && canvas.current;
      if (withSignature) pictures.push({ kind: "signature", field: "signature_file", blob: await toBlob(canvas.current!, "image/png") });
      if (photo) pictures.push({ kind: "photo", field: "photo_file", blob: photo });
      const position = await getPosition();
      const at = new Date();
      await queue.submit({
        path: `/v1/runs/${runId}/stops/${consignmentId}/deliver`,
        body: {
          at: at.toISOString(),
          packages: stop.packages.map((p) => ({ package_no: p.package_no, how: ticks[p.package_no] })),
          ...(withSignature ? { signed_name: name.trim() } : {}),
          ...(position ? { position } : {}),
        },
        label: `Stop ${stop.sequence} · delivered · ${plural(stop.packages.length, "carton")}`,
        run_id: runId, consignment_id: consignmentId, outcome: "delivered", pictures,
      });
      try { saveTicks(me, runId, consignmentId, {}); } catch { /* the delivery is kept: the ticks no longer matter */ }
      const done = outcomes(run, queue.forRun(runId));
      setDing({ sequence: stop.sequence, doneCount: done.size, cartons: stop.packages.length,
                by: withSignature ? `signed by ${name.trim()}` : "photo taken", at });
    } catch (e) {
      going.current = false;
      setError(e instanceof Error ? e.message : "That couldn't be kept on the phone. Try again.");
    } finally {
      setBusy(false);
    }
  }

  const next = ding ? nextStop(run, outcomes(run, items)) : null;
  return (
    <Phone label="Proof of delivery">
      <BackTop to={back} label="Back to the stop" title="Proof of delivery"
               sub={`Stop ${stop.sequence} of ${run.stops.length} · ${stop.customer}`} />
      <div className="scroll" style={{ paddingBottom: ding ? 230 : 0 }}>
        <div style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 13, color: "var(--color-ink-2)" }}>
          <Icon name="info" size={16} />A signature and printed name, or a photo of where you left it if nobody is there to sign.</div>
        <SignaturePad canvas={canvas} onSigned={setSigned} />
        <label style={{ display: "flex", flexDirection: "column", gap: 6, fontSize: 14, fontWeight: 800 }}>Printed name
          <input className="input" type="text" autoComplete="off" maxLength={120} value={name}
                 onChange={(e) => setName(e.target.value)} /></label>
        <PhotoPicker photo={photo} onPhoto={setPhoto} hint={signed ? "Optional" : "Needed if there's no signature"} />
        {signed && !name.trim() && !photo && <div className="small">Add the printed name to go with the signature.</div>}
        {error && <div className="msg r" role="alert">{error}</div>}
      </div>
      {!ding && (
        <button type="button" className="big primary" style={{ flex: "none" }} disabled={!ready || busy} onClick={() => void confirm()}>
          <Icon name="check" size={22} width={2.6} />Confirm delivery</button>
      )}
      {ding && (
        <section role="status" aria-label="Delivered" className="ding">
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <span style={{ width: 52, height: 52, borderRadius: "50%", background: "#fff", color: "var(--color-green)",
                           display: "grid", placeItems: "center", flex: "none" }}><Icon name="check" size={30} width={3} /></span>
            <div><div style={{ fontSize: 30, fontWeight: 800, letterSpacing: -0.4, lineHeight: 1 }}>Ding!</div>
              <div style={{ fontSize: 15, fontWeight: 700, color: "#E7F4EC" }}>
                Stop {ding.sequence} delivered · {ding.doneCount} of {run.stops.length} done</div></div>
          </div>
          <div style={{ fontSize: 13, color: "#D5F0E1" }}>{plural(ding.cartons, "carton")} · {ding.by} · {clock(ding.at)}</div>
          {next
            ? <Link to={`/run/${runId}/stop/${next.consignment_id}`} className="big white">
                Next: stop {next.sequence} · {next.customer}<Icon name="go" size={20} width={2.6} /></Link>
            : <Link to={`/run/${runId}`} className="big white">Back to the run<Icon name="go" size={20} width={2.6} /></Link>}
        </section>
      )}
    </Phone>
  );
}
