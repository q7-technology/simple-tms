import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "../api/client";
import type { ChecklistItem, Checklists as Lists } from "../api/types";
import { checklistProblems, said, typeName, VEHICLE_TYPES, type VehicleType } from "../lib/admin";
import { Icon } from "../ui/icons";
import type { Say } from "./Drivers";

interface Row { key: string; id?: number; label: string; critical: boolean }

let next = 0;
const fresh = (): Row => ({ key: `new-${++next}`, label: "", critical: false });
const rows = (items: ChecklistItem[]): Row[] => items.map((i) => ({ key: `id-${i.id}`, id: i.id, label: i.label, critical: i.critical }));
const same = (a: Row[], b: ChecklistItem[]) =>
  a.length === b.length && a.every((r, i) => r.label.trim() === b[i].label && r.critical === b[i].critical);

/** The Pre-start checklists tab of Fleet, for a fleet manager: each type's
 * lines in order, each critical or not. Saving sends the type's whole list
 * (PUT /v1/checklists/{type}); a line kept as it was keeps its id, and one
 * taken out or reworded is retired, never deleted. */
export function Checklists({ say }: { say: Say }) {
  const [saved, setSaved] = useState<Lists | null>(null);
  const [type, setType] = useState<VehicleType>("van");
  const [drafts, setDrafts] = useState<Partial<Record<VehicleType, Row[]>>>({});
  const [checked, setChecked] = useState(false);
  const [trouble, setTrouble] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const list = useRef<HTMLOListElement>(null);

  useEffect(() => {
    api.get<Lists>("/v1/checklists").then(setSaved).catch((e) => say({ tone: "r", text: said(e) }));
  }, []);

  if (!saved) return null;
  const lines = drafts[type] ?? rows(saved[type]);
  const changed = (t: VehicleType) => drafts[t] !== undefined && !same(drafts[t]!, saved[t]);
  const problems = checklistProblems(lines);

  function edit(change: (rows: Row[]) => Row[]) {
    setDrafts((d) => ({ ...d, [type]: change(d[type] ?? rows(saved![type])) }));
    setTrouble(null);
  }

  /** after the list is drawn again: focus one of a row's controls */
  function focus(key: string, what: string) {
    setTimeout(() => list.current?.querySelector<HTMLElement>(`[data-key="${key}"] [data-act="${what}"]`)?.focus(), 0);
  }

  function move(i: number, by: -1 | 1) {
    const key = lines[i].key;
    edit((rs) => { const out = [...rs]; [out[i], out[i + by]] = [out[i + by], out[i]]; return out; });
    // at the end, the button pressed goes: stay on the row by its other one
    const lands = i + by;
    focus(key, (by === -1 && lands === 0) ? "down" : (by === 1 && lands === lines.length - 1) ? "up" : by === -1 ? "up" : "down");
  }

  function takeOut(i: number) {
    const after = lines[i + 1] ?? lines[i - 1];
    edit((rs) => rs.filter((_, n) => n !== i));
    if (after) focus(after.key, "label");
  }

  function add() {
    const row = fresh();
    edit((rs) => [...rs, row]);
    focus(row.key, "label");
  }

  function choose(t: VehicleType) {
    setType(t);
    setChecked(false);
    setTrouble(null);
  }

  async function save() {
    setChecked(true);
    if (problems.list || problems.lines.some(Boolean)) {
      setTimeout(() => list.current?.querySelector<HTMLElement>('[aria-invalid="true"]')?.focus(), 0);
      return;
    }
    setBusy(true);
    try {
      const items = await api.put<ChecklistItem[]>(`/v1/checklists/${type}`,
                                                    { items: lines.map((l) => ({ label: l.label.trim(), critical: l.critical })) });
      setSaved((s) => ({ ...s!, [type]: items }));
      setDrafts((d) => ({ ...d, [type]: undefined }));
      setChecked(false);
      say({ tone: "g", text: `The ${type} checklist is saved: ${items.length} ${items.length === 1 ? "line" : "lines"}.` });
    } catch (e) {
      setTrouble(e instanceof ApiError && e.errors.length ? e.errors.map((x) => x.message).join("; ") : said(e));
    } finally {
      setBusy(false);
    }
  }

  const show = checked ? problems : { lines: lines.map(() => null), list: null };
  return (
    <>
      <div className="small">Before each run the driver answers every line. A failed critical line grounds the truck until a fleet
        manager marks it fixed. A line taken out, or reworded, is retired: checks already done keep it as it was. A driver part way
        through a check when you save is asked to do it again.</div>
      <div className="tabs" role="group" aria-label="Checklist for">
        {VEHICLE_TYPES.map((t) => (
          <button key={t} type="button" className="tab" aria-pressed={t === type} onClick={() => choose(t)}>
            {typeName(t)}{changed(t) && <i className="a">not saved</i>}</button>))}
      </div>
      <section className="adminform" aria-label={`${typeName(type)} checklist`}>
        <div className="eyebrow">{typeName(type)} checklist · {lines.length} {lines.length === 1 ? "line" : "lines"}</div>
        <ol ref={list} className="lines">
          {lines.map((l, i) => {
            const err = show.lines[i];
            const name = l.label.trim() || `line ${i + 1}`;
            return (
              <li key={l.key} data-key={l.key}>
                <span className="n" aria-hidden="true">{i + 1}</span>
                <div className="fld" style={{ flex: "1 1 220px" }}>
                  <input className="field" data-act="label" aria-label={`Line ${i + 1}`} value={l.label} maxLength={90}
                         aria-invalid={err ? true : undefined} aria-describedby={err ? `${l.key}-err` : undefined}
                         onChange={(e) => edit((rs) => rs.map((r) => (r.key === l.key ? { ...r, label: e.target.value } : r)))} />
                  {err && <div id={`${l.key}-err`} className="ferr">{err}</div>}
                </div>
                <label className="crit"><input type="checkbox" checked={l.critical}
                  onChange={(e) => edit((rs) => rs.map((r) => (r.key === l.key ? { ...r, critical: e.target.checked } : r)))} />
                  Critical<span className="sr"> ({name})</span></label>
                <div className="acts">
                  <button type="button" className="btn sm" data-act="up" aria-label={`Move ${name} up`} disabled={i === 0} onClick={() => move(i, -1)}>
                    <Icon name="up" size={16} /></button>
                  <button type="button" className="btn sm" data-act="down" aria-label={`Move ${name} down`} disabled={i === lines.length - 1} onClick={() => move(i, 1)}>
                    <Icon name="down" size={16} /></button>
                  <button type="button" className="btn sm" aria-label={`Take out ${name}`} onClick={() => takeOut(i)}>
                    <Icon name="close" size={16} /></button>
                </div>
              </li>
            );
          })}
        </ol>
        {show.list && <div className="msg r" role="alert">{show.list}</div>}
        {trouble && <div className="msg r" role="alert">{trouble}</div>}
        <div className="acts">
          <button type="button" className="btn" onClick={add} disabled={lines.length >= 30}><Icon name="plus" size={16} width={2.6} />Add a line</button>
          <button type="button" className="btn primary" disabled={busy || !changed(type)} onClick={() => void save()}>
            {busy ? "Saving…" : `Save the ${type} checklist`}</button>
          <button type="button" className="btn" disabled={busy || drafts[type] === undefined}
                  onClick={() => { setDrafts((d) => ({ ...d, [type]: undefined })); setChecked(false); setTrouble(null); }}>Put back as it was</button>
        </div>
      </section>
    </>
  );
}
