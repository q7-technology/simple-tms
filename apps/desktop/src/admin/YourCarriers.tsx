import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { api } from "../api/client";
import type { Carrier, Page } from "../api/types";
import { pickupClock } from "../lib/carriers";
import { apiProblems, carrierNameProblem, found, said, type Problems } from "../lib/admin";
import { Field, focusFirstProblem } from "../ui/Field";
import { Icon } from "../ui/icons";

type Say = (note: { tone: "r" | "a" | "g"; text: string } | null) => void;

/** Add a carrier (no `carrier`), or change one: its name (what Simple WMS
 * is told), your account with it and when it usually calls. How it is
 * booked is shown, not chosen: by hand is the only way yet. */
function CarrierForm({ carrier, carriers, onDone, onCancel }: {
  carrier?: Carrier; carriers: Carrier[]; onDone: (text: string) => Promise<void>; onCancel: () => void;
}) {
  const [name, setName] = useState(carrier?.name ?? "");
  const [account, setAccount] = useState(carrier?.account ?? "");
  const [pickup, setPickup] = useState(carrier?.pickup_time ?? "");
  const [problems, setProblems] = useState<Problems>({});
  const [trouble, setTrouble] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const form = useRef<HTMLFormElement>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setTrouble(null);
    const mine = found({
      name: carrierNameProblem(name, carriers, carrier?.id),
      account: account.trim().length > 40 ? "At most 40 characters." : null,
    });
    setProblems(mine);
    if (Object.keys(mine).length) { focusFirstProblem(form.current); return; }
    const body = { name: name.trim(), account: account.trim() || null, pickup_time: pickup || null };
    setBusy(true);
    try {
      if (!carrier) {
        await api.post<Carrier>("/v1/carriers", body);
        await onDone(`${body.name} is added. Send consignments with it from Plan runs.`);
      } else {
        await api.patch<Carrier>(`/v1/carriers/${carrier.id}`, body);
        await onDone(carrier.name === body.name ? `${body.name} is changed.`
          : `${carrier.name} is now ${body.name}. Simple WMS is told the new name from its next collection.`);
      }
    } catch (err) {
      const theirs = apiProblems(err);
      if (theirs) { setProblems(theirs); focusFirstProblem(form.current); } else setTrouble(said(err));
    } finally {
      setBusy(false);
    }
  }

  const title = carrier ? `Change ${carrier.name}` : "Add a carrier";
  return (
    <form ref={form} className="adminform" aria-label={title} noValidate onSubmit={(e) => void submit(e)}>
      <div className="eyebrow">{carrier ? "CHANGE A CARRIER" : "ADD A CARRIER"}</div>
      <div className="row">
        <Field label="Name" value={name} onChange={(e) => setName(e.target.value)} error={problems.name} maxLength={70} autoFocus
               hint="What Simple WMS is told the freight went with." />
        <Field label="Your account with them" value={account} onChange={(e) => setAccount(e.target.value)} error={problems.account}
               maxLength={50} hint="Printed on the manifest. May be left blank." />
        <Field label="Usually calls at" type="time" value={pickup} onChange={(e) => setPickup(e.target.value)}
               error={problems.pickup_time} hint="By the depot's clock. May be left blank." />
      </div>
      <div className="small">Booked: <b>manual</b>, by hand: book with the carrier as you always have, then type or scan the
        con note here. Carrier connections come later.</div>
      {trouble && <div className="msg r" role="alert">{trouble}</div>}
      <div className="acts">
        <button type="submit" className="btn primary" disabled={busy}>{busy ? "Saving…" : carrier ? "Save changes" : "Add the carrier"}</button>
        <button type="button" className="btn" onClick={onCancel} disabled={busy}>Cancel</button>
      </div>
    </form>
  );
}

/** The bottom of the Carriers drawer, for a fleet manager: every carrier,
 * retired ones too, to add, rename, retire or bring back. */
export function YourCarriers({ say, onChanged }: { say: Say; onChanged: () => Promise<unknown> }) {
  const [carriers, setCarriers] = useState<Carrier[] | null>(null);
  // "new", a carrier's id being changed, or nothing open
  const [open, setOpen] = useState<"new" | number | null>(null);
  const [busy, setBusy] = useState(false);
  const addButton = useRef<HTMLButtonElement>(null);

  const load = useCallback(async () => {
    setCarriers((await api.get<Page<Carrier>>("/v1/carriers")).items);
  }, []);

  useEffect(() => {
    load().catch((e) => say({ tone: "r", text: said(e) }));
  }, [load]);

  async function done(text: string) {
    setOpen(null);
    say({ tone: "g", text });
    await Promise.all([load(), onChanged()]).catch(() => undefined);
  }

  async function retire(c: Carrier) {
    if (c.active && !window.confirm(`Retire ${c.name}? It takes no new bookings. What is on its bay stays, to be collected.`)) return;
    setBusy(true);
    try {
      await api.post(`/v1/carriers/${c.id}/${c.active ? "deactivate" : "reactivate"}`);
      await done(c.active ? `${c.name} is retired.` : `${c.name} is back, and takes bookings.`);
    } catch (e) {
      say({ tone: "r", text: said(e) });
    } finally {
      setBusy(false);
    }
  }

  const all = carriers ?? [];
  const changing = all.find((c) => c.id === open);
  return (
    <section className="grp" aria-label="Your carriers">
      <div className="ghead">
        <span className="gicon"><Icon name="hand" size={20} /></span>
        <div style={{ flex: 1, minWidth: 200 }}><div className="t">Your carriers</div>
          <div className="s">Who else takes your freight. A retired carrier takes no new bookings; its history stays.</div></div>
        <button ref={addButton} type="button" className="btn primary" aria-expanded={open === "new"}
                onClick={() => setOpen(open === "new" ? null : "new")}><Icon name="plus" size={16} width={2.6} />Add a carrier</button>
      </div>
      {open !== null && (
        <div style={{ padding: "0 16px 12px" }}>
          <CarrierForm key={String(open)} carrier={changing} carriers={all} onDone={done}
                       onCancel={() => { setOpen(null); setTimeout(() => addButton.current?.focus(), 0); }} />
        </div>)}
      <div style={{ overflowX: "auto" }}><table style={{ minWidth: 640 }}>
        <thead><tr><th>NAME</th><th>ACCOUNT</th><th>USUALLY CALLS</th><th>BOOKED</th><th>STATUS</th><th><span className="sr">Change</span></th></tr></thead>
        <tbody>
          {all.map((c) => (
            <tr key={c.id}>
              <td><b>{c.name}</b></td>
              <td>{c.account ?? <span className="small">None</span>}</td>
              <td>{c.pickup_time ? pickupClock(c.pickup_time) : <span className="small">Not set</span>}</td>
              <td><span className="mono">{c.adapter}</span></td>
              <td>{c.active ? <span className="pill p-green">Active</span> : <span className="pill p-grey">Retired</span>}</td>
              <td style={{ whiteSpace: "nowrap" }}>
                <button type="button" className="btn sm" aria-label={`Change ${c.name}`} aria-expanded={open === c.id}
                        onClick={() => setOpen(open === c.id ? null : c.id)}>Change</button>{" "}
                <button type="button" className="btn sm" disabled={busy} aria-label={`${c.active ? "Retire" : "Bring back"} ${c.name}`}
                        onClick={() => void retire(c)}>{c.active ? "Retire" : "Bring back"}</button></td>
            </tr>))}
          {carriers?.length === 0 && <tr><td colSpan={6} className="small">No carriers yet. Add the ones you use with Add a carrier.</td></tr>}
        </tbody>
      </table></div>
    </section>
  );
}
