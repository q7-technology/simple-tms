import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { api } from "../api/client";
import type { Driver, Page } from "../api/types";
import { apiProblems, codeProblem, found, nameProblem, normalCode, pinProblem, said, type Problems } from "../lib/admin";
import { Field, focusFirstProblem } from "../ui/Field";
import { Icon } from "../ui/icons";

export type Say = (note: { tone: "r" | "g"; text: string } | null) => void;

function status(d: Driver): { text: string; tone: string } {
  if (!d.active) return { text: "Deactivated", tone: "grey" };
  if (d.locked) return { text: "Locked", tone: "red" };
  return { text: "Active", tone: "green" };
}

/** Add a driver: the code they sign in with, their name and a first PIN. */
function DriverForm({ drivers, onDone, onCancel }: {
  drivers: Driver[]; onDone: (text: string, code: string) => Promise<void>; onCancel: () => void;
}) {
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [pin, setPin] = useState("");
  const [problems, setProblems] = useState<Problems>({});
  const [trouble, setTrouble] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const form = useRef<HTMLFormElement>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setTrouble(null);
    const c = normalCode(code);
    const mine = found({ code: codeProblem(c, drivers), name: nameProblem(name, 120), pin: pinProblem(pin) });
    setProblems(mine);
    if (Object.keys(mine).length) { focusFirstProblem(form.current); return; }
    setBusy(true);
    try {
      const made = await api.post<Driver>("/v1/drivers", { code: c, name: name.trim(), pin });
      await onDone(`${made.name} (${made.code}) is added. They sign in with ${made.code} and the PIN you gave.`, made.code);
    } catch (err) {
      const theirs = apiProblems(err);
      if (theirs) { setProblems(theirs); focusFirstProblem(form.current); } else setTrouble(said(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form ref={form} className="adminform" aria-label="Add a driver" noValidate onSubmit={(e) => void submit(e)}>
      <div className="eyebrow">ADD A DRIVER</div>
      <div className="row">
        <Field label="Code" value={code} onChange={(e) => setCode(e.target.value)} error={problems.code} maxLength={40}
               spellCheck={false} autoFocus hint="What they sign in with, in any case. Kept lower case." />
        <Field label="Name" value={name} onChange={(e) => setName(e.target.value)} error={problems.name} maxLength={130} />
        <Field label="PIN" inputMode="numeric" value={pin} onChange={(e) => setPin(e.target.value)} error={problems.pin}
               maxLength={8} hint="4 to 8 digits. Tell the driver: it isn't shown again." />
      </div>
      {trouble && <div className="msg r" role="alert">{trouble}</div>}
      <div className="acts">
        <button type="submit" className="btn primary" disabled={busy}>{busy ? "Saving…" : "Add the driver"}</button>
        <button type="button" className="btn" onClick={onCancel} disabled={busy}>Cancel</button>
      </div>
    </form>
  );
}

/** One field and its button, sent on its own: a new name, or a new PIN. */
function OneField({ title, label, button, hint, initial = "", numeric, check, send }: {
  title: string; label: string; button: string; hint?: string; initial?: string; numeric?: boolean;
  check: (value: string) => string | null; send: (value: string) => Promise<void>;
}) {
  const [value, setValue] = useState(initial);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const form = useRef<HTMLFormElement>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    const mine = check(value);
    setProblem(mine);
    if (mine) { focusFirstProblem(form.current); return; }
    setBusy(true);
    try {
      await send(value);
      if (!initial) setValue("");
    } catch (err) {
      const theirs = apiProblems(err);
      setProblem(theirs ? Object.values(theirs).join("; ") : said(err));
      focusFirstProblem(form.current);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form ref={form} aria-label={title} noValidate onSubmit={(e) => void submit(e)} className="acts" style={{ alignItems: "flex-end" }}>
      <div style={{ flex: "1 1 160px" }}>
        <Field label={label} value={value} onChange={(e) => setValue(e.target.value)} error={problem} hint={hint}
               inputMode={numeric ? "numeric" : undefined} maxLength={numeric ? 8 : 130} />
      </div>
      <button type="submit" className="btn sm" disabled={busy}>{button}</button>
    </form>
  );
}

/** A fleet manager's controls for one driver. */
function DriverAside({ d, onChanged }: { d: Driver; onChanged: (text: string) => Promise<void> }) {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const who = `${d.name} (${d.code})`;

  async function act(path: string, done: string, ask?: string) {
    if (ask && !window.confirm(ask)) return;
    setError(null);
    setBusy(true);
    try {
      await api.post(`/v1/drivers/${d.code}/${path}`);
      await onChanged(done);
    } catch (e) {
      setError(said(e));
    } finally {
      setBusy(false);
    }
  }

  const pill = status(d);
  return (
    <aside className="aside" aria-label={`Driver ${d.code}`}>
      <div>
        <div className="eyebrow">DRIVER</div>
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <span style={{ fontSize: 22, fontWeight: 800 }}>{d.name}</span>
          <span className={`pill p-${pill.tone}`}>{pill.text}</span></div>
        <div className="small">Signs in as <b>{d.code}</b></div>
      </div>
      {error && <div className="msg r" role="alert">{error}</div>}
      {d.locked && (
        <div className="msg a" style={{ alignItems: "center", flexWrap: "wrap" }}>
          <span style={{ flex: 1 }}>Locked after {d.failed_attempts ?? 5} wrong PINs. Unlock keeps their PIN; a new PIN unlocks them too.</span>
          <button type="button" className="btn sm" disabled={busy} onClick={() => void act("unlock", `${d.name} is unlocked.`)}>Unlock</button>
        </div>
      )}
      <OneField title="Name" label="Name" button="Save name" initial={d.name} check={(v) => nameProblem(v, 120)}
                send={async (v) => { await api.patch(`/v1/drivers/${d.code}`, { name: v.trim() }); await onChanged(`${d.code}'s name is now ${v.trim()}.`); }} />
      <OneField title="A new PIN" label="New PIN" button="Set the PIN" numeric check={pinProblem}
                hint="Their phone asks them to sign in again with it; anything it hasn't sent waits until they do."
                send={async (v) => { await api.patch(`/v1/drivers/${d.code}`, { pin: v }); await onChanged(`${d.name} has a new PIN${d.locked ? ", and is unlocked" : ""}.`); }} />
      <div className="acts">
        {d.active
          ? <button type="button" className="btn sm" disabled={busy}
                    onClick={() => void act("deactivate", `${who} is deactivated.`,
                                            `Deactivate ${who}? They can't sign in until they are reactivated. Their runs and deliveries stay theirs.`)}>Deactivate</button>
          : <button type="button" className="btn sm" disabled={busy} onClick={() => void act("reactivate", `${who} is reactivated.`)}>Reactivate</button>}
      </div>
    </aside>
  );
}

/** The Drivers tab of Fleet: everyone sees who drives and who is locked
 * out; a fleet manager adds, renames, sets PINs, unlocks and deactivates. */
export function Drivers({ manager, say }: { manager: boolean; say: Say }) {
  const [drivers, setDrivers] = useState<Driver[] | null>(null);
  const [chosen, setChosen] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const addButton = useRef<HTMLButtonElement>(null);

  const load = useCallback(async () => {
    setDrivers((await api.get<Page<Driver>>("/v1/drivers")).items);
  }, []);

  useEffect(() => {
    load().catch((e) => say({ tone: "r", text: said(e) }));
  }, [load]);

  function closeAdd() {
    setAdding(false);
    setTimeout(() => addButton.current?.focus(), 0);
  }

  async function changed(text: string, code?: string) {
    if (code) setChosen(code);
    say({ tone: "g", text });
    await load().catch(() => undefined);
  }

  const all = drivers ?? [];
  const selected = manager ? all.find((d) => d.code === chosen) ?? all.find((d) => d.locked && d.active) ?? all[0] ?? null : null;
  return (
    <>
      {manager && (
        <div className="tools">
          <button ref={addButton} type="button" className="btn primary" aria-expanded={adding} onClick={() => setAdding((a) => !a)}>
            <Icon name="plus" size={16} width={2.6} />Add a driver</button>
        </div>)}
      {adding && <DriverForm drivers={all} onCancel={closeAdd} onDone={async (text, code) => { closeAdd(); await changed(text, code); }} />}
      <div className="split">
        <div className="tablewrap">
          <table>
            <thead><tr><th>CODE</th><th>NAME</th><th>STATUS</th></tr></thead>
            <tbody>
              {all.map((d) => {
                const on = selected?.code === d.code;
                const pill = status(d);
                return (
                  <tr key={d.code} className={on ? "sel" : undefined}>
                    <td>{manager
                      ? <button type="button" className="rowbtn" aria-pressed={on} aria-label={`Show ${d.name} (${d.code})`} onClick={() => setChosen(d.code)}>{d.code}</button>
                      : <b style={{ padding: "0 10px" }}>{d.code}</b>}</td>
                    <td>{d.name}</td>
                    <td><span className={`pill p-${pill.tone}`}>{pill.text}</span></td>
                  </tr>
                );
              })}
              {drivers?.length === 0 && (
                <tr><td colSpan={3} className="small">{manager ? "No drivers yet. Add each one with Add a driver." : "No drivers yet. A fleet manager adds them here."}</td></tr>)}
            </tbody>
          </table>
        </div>
        {selected && <DriverAside key={selected.code} d={selected} onChanged={(text) => changed(text, selected.code)} />}
      </div>
      <div className="small">A driver signs in to the driver app with their code, in any case, and their PIN. Five wrong PINs lock them out until a fleet manager unlocks them or gives them a new PIN.</div>
    </>
  );
}
