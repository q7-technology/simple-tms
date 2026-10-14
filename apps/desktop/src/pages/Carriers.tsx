import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { useOutletContext } from "react-router-dom";
import { api, ApiError, Unreachable } from "../api/client";
import type { Carrier, Manifest, Page } from "../api/types";
import { bayStats, conNoteProblem, nextNeedingConNote, normalConNote, pickupLine, type Needing } from "../lib/carriers";
import { clock, dayLabel, fromIso, isoDay } from "../lib/days";
import { plain, toMicro } from "../lib/decimal";
import { Drawer } from "../ui/Drawer";
import { Icon } from "../ui/icons";

type Note = { tone: "r" | "a" | "g"; text: string } | null;

function said(e: unknown): string {
  if (e instanceof ApiError && e.code === "stale") return "Someone else changed this manifest. Here it is as it is now: check it and try again.";
  if (e instanceof Unreachable) return e.message;
  return e instanceof ApiError ? e.message : "Simple TMS could not be reached. Check the connection and try again.";
}

function printLine(m: Manifest): Note {
  const p = m.print_job;
  if (!p) return null;
  if (p.status === "printed") return { tone: "g", text: `Manifest printed on ${p.printer}.` };
  if (p.status === "failed") return { tone: "r", text: `Manifest didn't print: ${p.last_error ?? "Platen said it failed"}` };
  if (p.status === "accepted") return { tone: "g", text: `Manifest sent to ${p.printer}.` };
  return { tone: "a", text: p.last_error ? `Manifest waiting to go to Platen: ${p.last_error}` : "Manifest waiting to go to Platen." };
}

/** Carriers.html: freight going with outside carriers, a group per carrier's
 * manifest, the con note box (type it, or scan the label with a desk
 * scanner: it types the number and presses Enter), Print manifest, and
 * Mark collected once the carrier has been. */
export function Carriers() {
  const { reloadCounts } = useOutletContext<{ reloadCounts: () => void }>();
  const today = isoDay(new Date());
  const [manifests, setManifests] = useState<Manifest[] | null>(null);
  const [carriers, setCarriers] = useState<Carrier[]>([]);
  const [picked, setPicked] = useState<number | null>(null);
  const [carrierId, setCarrierId] = useState("");
  const [conNote, setConNote] = useState("");
  const [note, setNote] = useState<Note>(null);
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    const [m, c] = await Promise.all([
      // and the ones from earlier days collected since midnight, for Collected today
      api.get<Page<Manifest>>("/v1/manifests", { date: today, left_open: true,
                                                  collected_since: new Date(new Date().setHours(0, 0, 0, 0)).toISOString() }),
      api.get<Page<Carrier>>("/v1/carriers", { active: true }).catch(() => ({ items: [] as Carrier[], total: 0 })),
    ]);
    setManifests(m.items);
    setCarriers(c.items);
    return m.items;
  }, [today]);

  useEffect(() => {
    load().catch((e) => setNote({ tone: "r", text: said(e) }));
    // what another desk does turns up within 20 s
    const t = setInterval(() => { load().catch(() => undefined); }, 20_000);
    return () => clearInterval(t);
  }, [load]);

  const all = manifests ?? [];
  const open = all.filter((m) => m.status === "open");
  // the consignment the box is for: the one picked (or the one being typed
  // for: fixed on the first key), else the first without a con note
  const chosen: Needing | null = (() => {
    for (const m of open) {
      const l = m.lines.find((x) => x.consignment.id === picked);
      if (l) return { manifest: m, consignment: l.consignment };
    }
    return nextNeedingConNote(all);
  })();
  const chosenId = chosen?.consignment.id ?? null;
  const chosenLine = chosen?.manifest.lines.find((l) => l.consignment.id === chosenId);
  const lastChosen = useRef<Needing | null>(null);
  // the one picked, as last seen on the bay (kept once it has gone)
  if (chosen && (picked === null || chosenId === picked)) lastChosen.current = chosen;

  // the box starts empty, with the consignment's carrier, each time it moves
  // to another one. A con note it has already is shown beside it, not put in
  // the box: the next scan always replaces what is there, never adds to it.
  const chosenKey = chosen ? `${chosen.manifest.id}:${chosenId}` : "";
  useEffect(() => {
    setCarrierId(chosen ? String(chosen.manifest.carrier.id) : "");
  }, [chosenKey]);
  useEffect(() => { setConNote(""); }, [chosenId]);

  // the one being typed for has left the bay (another desk put it on a run,
  // or in the tray): say so, rather than move the half-typed number to another
  useEffect(() => {
    if (!manifests || picked === null) return;
    if (open.some((m) => m.lines.some((l) => l.consignment.id === picked))) return;
    const was = lastChosen.current;
    if (conNote.trim() && was?.consignment.id === picked) {
      const c = was.consignment;
      setNote({ tone: "a", text: `${c.customer} (${c.wms_ref}) has left the carrier bay, so the con note being typed for it was cleared.` });
    }
    setConNote("");
    setPicked(null);
  }, [manifests]);

  /** After a refusal the number stays, all selected: the next scan replaces it. */
  function selectBox() {
    setTimeout(() => { input.current?.focus(); input.current?.select(); }, 0);
  }

  function typed(value: string) {
    // the first key fixes the consignment it is for, whatever the poll brings
    if (picked === null && chosenId !== null) setPicked(chosenId);
    setConNote(value);
  }

  async function act(fn: () => Promise<unknown>, done: string) {
    setBusy(true);
    try {
      await fn();
      setNote({ tone: "g", text: done });
    } catch (e) {
      setNote({ tone: "r", text: said(e) });
    } finally {
      await load().catch(() => undefined);
      reloadCounts();
      setBusy(false);
    }
  }

  async function save(e: FormEvent) {
    e.preventDefault();
    if (!chosen || busy) return;
    const value = normalConNote(conNote);
    const problem = conNoteProblem(value);
    if (problem) { setNote({ tone: "r", text: problem }); selectBox(); return; }
    const { manifest: m, consignment: c } = chosen;
    setBusy(true);
    try {
      if (String(m.carrier.id) === carrierId) {
        await api.post(`/v1/manifests/${m.id}/con-note`, { consignment_id: c.id, con_note: value });
      } else {
        // to another carrier: for today, even if it has waited since an earlier day
        await api.post(`/v1/carriers/${carrierId}/book`, { consignment_id: c.id, date: today, con_note: value });
      }
      setNote({ tone: "g", text: `Con note ${value} saved for ${c.customer}.` });
      const fresh = await load().catch(() => all);
      // on to the next one without a con note, ready for the scanner
      setPicked(nextNeedingConNote(fresh, c.id)?.consignment.id ?? null);
      setConNote("");
      reloadCounts();
      setTimeout(() => input.current?.focus(), 0);
    } catch (err) {
      setNote({ tone: "r", text: said(err) });
      selectBox();
    } finally {
      setBusy(false);
    }
  }

  function collected(m: Manifest) {
    const n = m.lines.length;
    if (!window.confirm(`${m.carrier.name} has collected ${n} ${n === 1 ? "consignment" : "consignments"}? Each is marked shipped in Simple WMS with its con note.`)) return;
    void act(() => api.post(`/v1/manifests/${m.id}/collect`, {
      message_id: crypto.randomUUID(), consignment_ids: m.lines.map((l) => l.consignment.id),
    }), `${m.carrier.name} has collected ${m.manifest_no}. Simple WMS is being told.`);
  }

  const stats = bayStats(all, today);
  const needs = stats.firstNeeding;
  const went = stats.lastCollected;
  return (
    <Drawer label="Carriers" head={
      <div style={{ flex: 1, minWidth: 200 }}>
        <div className="eyebrow" style={{ color: "var(--color-green)" }}>Today · {dayLabel(new Date())}</div>
        <h1>Carriers</h1>
        <div className="sub">Freight going with outside carriers. Choose the carrier, type or scan the con note, print the manifest.</div>
      </div>
    }>
      <div className="stats">
        <div className={`stat${stats.needs ? " amber" : ""}`}><div className="eyebrow">Needs a con note</div><div className="v num">{stats.needs}</div>
          <div className="d">{needs ? `${needs.consignment.wms_ref} · ${needs.consignment.customer}` : "Every con note is in"}</div></div>
        <div className="stat"><div className="eyebrow">Waiting for pickup</div><div className="v num">{stats.waiting}</div>
          <div className="d">on the carrier bay · {stats.carriers} {stats.carriers === 1 ? "carrier" : "carriers"}</div></div>
        <div className="stat"><div className="eyebrow">Collected today</div><div className="v num">{stats.collected}</div>
          <div className="d">{went ? `${went.carrier.name} · ${clock(new Date(went.collected_at!))}` : "Nothing yet"}</div></div>
      </div>

      {note && <div className={`msg ${note.tone}`} role={note.tone === "r" ? "alert" : "status"}>{note.text}</div>}

      {chosen && (
        <form className="connote" aria-label="Add a con note" onSubmit={(e) => void save(e)}>
          <div style={{ display: "flex", alignItems: "center", gap: 12, flex: "1 1 100%" }}>
            <span style={{ color: "var(--color-green)" }}><Icon name="scan" size={28} /></span>
            <div><div style={{ fontSize: 16, fontWeight: 800 }}>Con note for {chosen.consignment.wms_ref} · {chosen.consignment.customer}</div>
              <div className="small">Scan the carrier's label with the desk scanner, or type the number.</div>
              {chosenLine?.con_note && <div className="small">{`Now ${chosenLine.con_note}: scan or type the new one.`}</div>}</div>
          </div>
          <label style={{ flex: "1 1 200px" }}>Carrier
            <select className="field" value={carrierId} onChange={(e) => setCarrierId(e.target.value)} disabled={busy}>
              {!carriers.some((c) => c.id === chosen.manifest.carrier.id) && (
                <option value={chosen.manifest.carrier.id}>{chosen.manifest.carrier.name}</option>)}
              {carriers.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select></label>
          <label style={{ flex: "2 1 240px" }}>Con note number
            {/* read-only while a save is on its way, so a scan can't land in it and be lost */}
            <input ref={input} className="field mono" type="text" autoComplete="off" spellCheck={false} autoFocus
                   readOnly={busy} aria-busy={busy} placeholder={chosenLine?.con_note ?? "e.g. RX-77310049"}
                   value={conNote} onChange={(e) => typed(e.target.value)} maxLength={64} /></label>
          <button type="submit" className="btn primary" disabled={busy || !conNote.trim()}>{busy ? "Saving…" : "Save con note"}</button>
        </form>
      )}

      {manifests && all.length === 0 && (
        <div className="small">Nothing is waiting for a carrier. Send a consignment with a carrier from Plan runs.</div>)}
      {manifests && carriers.length === 0 && (
        <div className="msg a" role="status">No carriers yet. A fleet manager adds them through the API (POST /v1/carriers; see docs/api.md).</div>)}

      {all.map((m) => {
        const isOpen = m.status === "open";
        const ready = isOpen && m.needs_con_note === 0;
        const earlier = isOpen && m.date < today;
        const printed = printLine(m);
        const why = `why-${m.id}`;
        return (
          <section key={m.id} className={`grp${isOpen ? "" : " done"}`} aria-label={`${m.carrier.name} · ${m.manifest_no}`}>
            <div className="ghead">
              <span className="gicon"><Icon name="truck" size={20} /></span>
              <div style={{ flex: 1, minWidth: 200 }}><div className="t">{m.carrier.name}</div>
                <div className="s">{earlier ? `Waiting since ${dayLabel(fromIso(m.date))} · ` : ""}{pickupLine(m)}</div></div>
              {!isOpen ? <span className="pill p-green"><Icon name="tick" size={13} width={3} />Collected</span>
                : ready ? <span className="pill p-green">Ready</span>
                : <span className="pill p-amber">{m.needs_con_note} {m.needs_con_note === 1 ? "needs" : "need"} a con note</span>}
              <button type="button" className={`btn${ready && !m.print_job ? " primary" : ""}`} disabled={busy || (isOpen && !ready)}
                      aria-describedby={isOpen && !ready ? why : undefined}
                      onClick={() => void act(() => api.post(`/v1/manifests/${m.id}/print`), `${m.manifest_no} sent to Platen.`)}>
                <Icon name="print" size={16} />{isOpen ? "Print manifest" : "Reprint"}</button>
              {isOpen && (
                <button type="button" className={`btn${ready && m.print_job ? " primary" : ""}`} disabled={busy || !ready}
                        onClick={() => collected(m)}><Icon name="tick" size={16} width={2.6} />Mark collected</button>)}
              {isOpen && !ready && (
                <span id={why} className="small" style={{ flexBasis: "100%", textAlign: "right" }}>
                  Add the missing con {m.needs_con_note === 1 ? "note" : "notes"} to print this manifest.</span>)}
            </div>
            {printed && <div className={`msg ${printed.tone}`} style={{ margin: "0 16px 10px" }}>{printed.text}</div>}
            {m.shipping.failed > 0 && (
              <div className="msg r" style={{ margin: "0 16px 10px" }}><span>{m.shipping.failed} not marked shipped in Simple WMS after five tries{m.shipping.last_error ? `: ${m.shipping.last_error}` : "."}</span>
                <button type="button" className="btn sm" disabled={busy}
                        onClick={() => void act(() => api.post(`/v1/manifests/${m.id}/retry-ship`), "Trying Simple WMS again.")}>Try again</button></div>)}
            {isOpen && (
              <div style={{ overflowX: "auto" }}><table style={{ minWidth: 700, tableLayout: "fixed" }}>
                <colgroup><col style={{ width: "16%" }} /><col style={{ width: "26%" }} /><col style={{ width: "7%" }} />
                  <col style={{ width: "7%" }} /><col style={{ width: "18%" }} /><col style={{ width: "14%" }} /><col style={{ width: "12%" }} /></colgroup>
                <thead><tr><th>CONSIGNMENT</th><th>CUSTOMER</th><th>CTN</th><th>KG</th><th>CON NOTE</th><th>STATUS</th><th><span className="sr">Back to the tray</span></th></tr></thead>
                <tbody>
                  {m.lines.map((l) => {
                    const c = l.consignment;
                    return (
                      <tr key={c.id} className={`${l.con_note ? "" : "wait"}${c.id === chosenId ? " sel" : ""}`}>
                        <td><button type="button" className="rowbtn" aria-pressed={c.id === chosenId} aria-label={`Con note for ${c.customer}`}
                                    onClick={() => { setPicked(c.id); setTimeout(() => input.current?.focus(), 0); }}>{c.wms_ref}</button></td>
                        <td style={{ whiteSpace: "nowrap" }}><b>{c.customer}</b><div className="small">{c.suburb}</div></td>
                        <td className="num">{c.cartons}</td>
                        <td className="num">{plain(toMicro(c.weight_kg))}</td>
                        <td>{l.con_note ? <span className="mono">{l.con_note}</span>
                          : <span className="small" style={{ color: "var(--color-amber-ink)", fontWeight: 700 }}>Not added yet</span>}</td>
                        <td>{l.con_note ? <span className="pill p-grey">On the bay</span> : <span className="pill p-amber">Needs con note</span>}</td>
                        <td><button type="button" className="btn sm" disabled={busy} aria-label={`Put ${c.customer} back in the tray`}
                                    onClick={() => void act(() => api.post(`/v1/manifests/${m.id}/unbook`, { consignment_id: c.id }),
                                                            `${c.customer} is back in the tray.`)}>Tray</button></td>
                      </tr>
                    );
                  })}
                </tbody>
              </table></div>
            )}
          </section>
        );
      })}

      <div className="small">Manifests print on the dispatch printer through Platen. Carrier bookings are by hand in this version: no carrier connections yet.</div>
    </Drawer>
  );
}
