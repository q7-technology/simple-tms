import { useRef, useState } from "react";
import type { ChecklistLine, DriverRun } from "../api/types";
import { useSession } from "../auth/Session";
import { PhotoPicker } from "../ui/pictures";

interface Answer { result: "pass" | "fail" | null; note: string; photo: Blob | null }

const km = (n: number) => n.toLocaleString("en-AU");

/** "61,208" or "61 208" as typed: whole kilometres, or null. */
export function readOdometer(text: string): number | null {
  const digits = text.replace(/[\s,]/g, "");
  return /^\d{1,7}$/.test(digits) ? Number(digits) : null;
}

function PassFail({ line, answer, onChange }: { line: ChecklistLine; answer: Answer; onChange: (a: Answer) => void }) {
  const button = (value: "pass" | "fail", label: string, on: string) => {
    const chosen = answer.result === value;
    return (
      <button type="button" role="radio" aria-checked={chosen} onClick={() => onChange({ ...answer, result: value })}
              style={{ height: 38, minWidth: 60, borderRadius: 12, fontWeight: 800, fontSize: 14, cursor: "pointer",
                       border: `1px solid ${chosen ? on : "var(--color-line-2)"}`, background: chosen ? on : "#fff",
                       color: chosen ? "#fff" : "var(--color-ink-2)" }}>{label}</button>
    );
  };
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "6px 12px", borderTop: "1px solid var(--color-row)" }}>
      <div style={{ flex: 1, minWidth: 0 }}><div style={{ fontSize: 15, fontWeight: 800 }}>{line.label}</div>
        {line.critical && <div className="small">Critical item</div>}</div>
      <div role="radiogroup" aria-label={line.label} style={{ display: "flex", gap: 6 }}>
        {button("pass", "Pass", "var(--color-green)")}{button("fail", "Fail", "var(--color-red)")}
      </div>
    </div>
  );
}

/** The checklist half of DriverPrestart.html: the odometer, then each item
 * passed or failed, a failed one with a photo and a note. It goes through
 * the queue like any confirmation, with its own message_id, so a check made
 * in a black spot goes once there's signal, and is recorded once. */
export function PrestartCheck({ run }: { run: DriverRun }) {
  const { queue } = useSession();
  const lines = run.checklist ?? [];
  const last = run.vehicle.odometer_km ?? null;
  const [odometer, setOdometer] = useState("");
  // the driver is done with the reading (left the field, or pressed Go): only
  // then is a low one a mistake, not "6" on the way to "61,208"
  const [entered, setEntered] = useState(false);
  const [answers, setAnswers] = useState<Record<number, Answer>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // set at once, unlike state: a second tap before the screen updates does nothing
  const going = useRef(false);

  const answer = (id: number): Answer => answers[id] ?? { result: null, note: "", photo: null };
  const reading = readOdometer(odometer);
  const backwards = reading !== null && last !== null && reading < last;
  const passed = lines.filter((l) => answer(l.id).result === "pass").length;
  const ready = reading !== null && !backwards && lines.length > 0 && lines.every((l) => {
    const a = answer(l.id);
    return a.result === "pass" || (a.result === "fail" && !!a.photo && a.note.trim() !== "");
  });

  async function finish() {
    if (!ready || going.current) return;
    going.current = true;
    setBusy(true);
    setError(null);
    try {
      const failed = lines.map((l, i) => ({ l, i })).filter(({ l }) => answer(l.id).result === "fail");
      await queue.submit({
        path: `/v1/runs/${run.id}/prestart`,
        // the truck the driver was shown, so the check is kept against it even if the office swaps the run's truck
        body: { at: new Date().toISOString(), vehicle: run.vehicle.rego, odometer_km: reading,
                items: lines.map((l) => answer(l.id).result === "pass"
                  ? { item_id: l.id, result: "pass" }
                  : { item_id: l.id, result: "fail", note: answer(l.id).note.trim() }) },
        label: `Pre-start · ${run.vehicle.rego}`, run_id: run.id, consignment_id: null, outcome: "prestart",
        pictures: failed.map(({ l, i }) => ({ kind: "photo" as const, field: `items.${i}.photo_file`, blob: answer(l.id).photo! })),
      });
    } catch (e) {
      going.current = false;
      setError(e instanceof Error ? e.message : "That couldn't be kept on the phone. Try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="scroll">
        <label style={{ display: "flex", alignItems: "center", gap: 10, fontSize: 14, fontWeight: 800, padding: "8px 12px",
                        borderRadius: 16, background: "#fff", border: "1px solid var(--color-line)" }}>
          <span style={{ flex: 1 }}>Odometer{last !== null && <span className="small" style={{ display: "block", fontWeight: 600 }}>Last {km(last)} km</span>}</span>
          <input className="num" type="text" inputMode="numeric" autoComplete="off" value={odometer}
                 onChange={(e) => { setOdometer(e.target.value); setEntered(false); }}
                 onBlur={() => setEntered(true)} onKeyDown={(e) => { if (e.key === "Enter") setEntered(true); }}
                 style={{ width: 130, height: 44, padding: "0 12px", borderRadius: 12, border: "2px solid var(--color-line-2)",
                          fontSize: 17, fontWeight: 800, textAlign: "right" }} />
          <span style={{ color: "var(--color-ink-2)" }}>km</span>
        </label>
        {backwards && entered && <div className="msg r" role="alert">That's less than the last reading, {km(last!)} km. Check the odometer.</div>}
        <section aria-label={`Checklist for a ${run.vehicle.type}`}
                 style={{ borderRadius: 18, background: "#fff", border: "1px solid var(--color-line)", overflow: "hidden" }}>
          <div className="lbl" style={{ padding: "10px 12px 6px" }}>{run.vehicle.type} checklist · {passed} of {lines.length} pass</div>
          {lines.map((l) => {
            const a = answer(l.id);
            const set = (next: Answer) => setAnswers((all) => ({ ...all, [l.id]: next }));
            return (
              <div key={l.id}>
                <PassFail line={l} answer={a} onChange={set} />
                {a.result === "fail" && (
                  <div style={{ padding: "4px 12px 12px", display: "flex", flexDirection: "column", gap: 8,
                                background: "#FFF8F8", borderTop: "1px solid var(--color-red-line)" }}>
                    <div style={{ fontSize: 12, color: "var(--color-red-ink)", fontWeight: 700, paddingTop: 6 }}>A fail needs a photo and a note.</div>
                    <PhotoPicker photo={a.photo} onPhoto={(photo) => set({ ...a, photo })} hint={`${l.label}: needed`} />
                    <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 13, fontWeight: 800 }}>{l.label}: note
                      <input className="field" type="text" maxLength={500} value={a.note} onChange={(e) => set({ ...a, note: e.target.value })} /></label>
                    <div className="small">{l.critical
                      ? "A critical item: the truck can't go until a fleet manager marks it fixed. The office sees it as soon as it's sent."
                      : "Not a critical item, so you can still go. The fleet manager will see it."}</div>
                  </div>
                )}
              </div>
            );
          })}
        </section>
        {error && <div className="msg r" role="alert">{error}</div>}
      </div>
      <button type="button" className="big primary" style={{ flex: "none" }} disabled={!ready || busy} onClick={() => void finish()}>
        Finish check</button>
    </>
  );
}
