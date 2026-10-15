import { useRef, useState, type FormEvent } from "react";
import { api } from "../api/client";
import type { Vehicle } from "../api/types";
import {
  amount, amountProblem, apiProblems, found, interval, intervalProblem, normalRego, regoProblem,
  said, typeName, VEHICLE_TYPES, type Problems,
} from "../lib/admin";
import { Field, focusFirstProblem, SelectField } from "../ui/Field";

/** Add a vehicle (no `vehicle`), or change one: its type, what it carries
 * and how often it is serviced. The rego is the truck's name for good. For
 * a fleet manager; the API says no to anyone else. */
export function VehicleForm({ vehicle, vehicles, onDone, onCancel }: {
  vehicle?: Vehicle; vehicles: Vehicle[];
  onDone: (message: string, rego: string) => void | Promise<void>; onCancel?: () => void;
}) {
  const adding = !vehicle;
  const [rego, setRego] = useState("");
  const [type, setType] = useState(vehicle?.type ?? "van");
  const [kg, setKg] = useState(vehicle?.max_kg ?? "");
  const [m3, setM3] = useState(vehicle?.max_m3 ?? "");
  const [months, setMonths] = useState(vehicle?.service_every_months?.toString() ?? "");
  const [km, setKm] = useState(vehicle?.service_every_km?.toString() ?? "");
  const [problems, setProblems] = useState<Problems>({});
  const [trouble, setTrouble] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const form = useRef<HTMLFormElement>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setTrouble(null);
    const plate = normalRego(rego);
    const mine = found({
      rego: adding ? regoProblem(plate, vehicles) : null,
      max_kg: amountProblem(kg, "kg"),
      max_m3: amountProblem(m3, "m³"),
      service_every_months: intervalProblem(months, 120, "months"),
      service_every_km: intervalProblem(km, 1_000_000, "km"),
    });
    setProblems(mine);
    if (Object.keys(mine).length) { focusFirstProblem(form.current); return; }
    const body = { type, max_kg: amount(kg), max_m3: amount(m3),
                   service_every_months: interval(months), service_every_km: interval(km) };
    setBusy(true);
    try {
      if (adding) {
        const made = await api.post<Vehicle>("/v1/vehicles", { rego: plate, ...body });
        await onDone(`${made.rego} is added. Record its rego expiry and last service from its row.`, made.rego);
      } else {
        await api.patch<Vehicle>(`/v1/vehicles/${vehicle.rego}`, body);
        await onDone(`${vehicle.rego} is changed.`, vehicle.rego);
      }
    } catch (err) {
      const theirs = apiProblems(err);
      if (theirs) { setProblems(theirs); focusFirstProblem(form.current); }
      else setTrouble(said(err));
    } finally {
      setBusy(false);
    }
  }

  const title = adding ? "Add a vehicle" : `Change ${vehicle.rego}`;
  return (
    <form ref={form} className="adminform" aria-label={title} noValidate onSubmit={(e) => void submit(e)}>
      {adding && <div className="eyebrow">ADD A VEHICLE</div>}
      <div className="row">
        {adding && <Field label="Rego" value={rego} onChange={(e) => setRego(e.target.value)} error={problems.rego}
                          maxLength={20} spellCheck={false} hint="As on the plate. Spaces don't count." autoFocus />}
        <SelectField label="Type" value={type} onChange={(e) => setType(e.target.value)} error={problems.type}
                     hint="Which pre-start checklist its driver answers.">
          {VEHICLE_TYPES.map((t) => <option key={t} value={t}>{typeName(t)}</option>)}
        </SelectField>
      </div>
      <div className="row">
        <Field label="Most it carries (kg)" inputMode="decimal" value={kg} onChange={(e) => setKg(e.target.value)} error={problems.max_kg} />
        <Field label="Most it carries (m³)" inputMode="decimal" value={m3} onChange={(e) => setM3(e.target.value)} error={problems.max_m3} />
      </div>
      <div className="row">
        <Field label="Service every (months)" inputMode="numeric" value={months} onChange={(e) => setMonths(e.target.value)}
               error={problems.service_every_months} />
        <Field label="Service every (km)" inputMode="numeric" value={km} onChange={(e) => setKm(e.target.value)}
               error={problems.service_every_km} />
      </div>
      <div className="small">The service falls due by months or kilometres, whichever comes first. Leave both blank and it is never due.</div>
      {trouble && <div className="msg r" role="alert">{trouble}</div>}
      <div className="acts">
        <button type="submit" className="btn primary" disabled={busy}>{busy ? "Saving…" : adding ? "Add the vehicle" : "Save changes"}</button>
        {onCancel && <button type="button" className="btn" onClick={onCancel} disabled={busy}>Cancel</button>}
      </div>
    </form>
  );
}
