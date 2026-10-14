import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, ApiError, Unreachable } from "../api/client";
import type { Consignment, ConsignmentDetail, Page } from "../api/types";
import { whereNow, type Tone } from "../lib/carriers";
import { clock } from "../lib/days";
import { plain, toMicro } from "../lib/decimal";
import { historyLine, proofOf, reasonWords } from "../lib/history";
import { Drawer } from "../ui/Drawer";
import { Icon } from "../ui/icons";
import { Photo } from "../ui/Photo";

const TABS = [
  { key: "all", label: "All", status: undefined, tone: "" },
  { key: "plan", label: "To plan", status: "packed,failed", tone: "a" },
  { key: "run", label: "On a run", status: "assigned,departed", tone: "" },
  { key: "carrier", label: "With carrier", status: "booked,collected", tone: "" },
  { key: "delivered", label: "Delivered", status: "delivered", tone: "" },
  { key: "failed", label: "Not delivered", status: "failed", tone: "r" },
] as const;

const PILL: Record<Tone, string> = { amber: "p-amber", red: "p-red", blue: "p-blue", green: "p-green", grey: "p-grey" };

function said(e: unknown): string {
  if (e instanceof Unreachable) return e.message;
  return e instanceof ApiError ? e.message : "Simple TMS could not be reached. Check the connection and try again.";
}

const sentence = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

function Pill({ c }: { c: Consignment }) {
  const w = whereNow(c);
  return <span className={`pill ${PILL[w.tone]}`}>{w.text}</span>;
}

/** The aside: what it is, where it is, its history, and its proof of delivery. */
function Details({ id }: { id: number }) {
  const [c, setC] = useState<ConsignmentDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    setC(null);
    setError(null);
    api.get<ConsignmentDetail>(`/v1/consignments/${id}`).then((d) => live && setC(d)).catch((e) => live && setError(said(e)));
    return () => { live = false; };
  }, [id]);
  if (error) return <aside className="aside" aria-label="Consignment details"><div className="msg r" role="alert">{error}</div></aside>;
  if (!c) return <aside className="aside" aria-label="Consignment details"><div className="small">Loading…</div></aside>;
  const cartons = c.packages.filter((p) => p.type === "carton").length;
  const pod = proofOf(c.history);
  const w = whereNow(c);
  return (
    <aside className="aside" aria-label="Consignment details">
      <div>
        <div className="eyebrow" style={{ color: "var(--color-green)" }}>Consignment · {w.text}</div>
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <span style={{ fontSize: 22, fontWeight: 800 }}>{c.wms_ref}</span><Pill c={c} /></div>
        <div style={{ fontSize: 13, color: "var(--color-ink-2)" }}>
          {[c.customer, c.ship_to?.suburb, `${cartons} ${cartons === 1 ? "carton" : "cartons"}`,
            `${plain(toMicro(c.weight_kg))} kg`, `${plain(toMicro(c.volume_m3))} m³`].filter(Boolean).join(" · ")}</div>
      </div>
      <div className="box">
        <div><span className="small">From WMS</span><b>{c.wms_ref}</b></div>
        {c.run && <div><span className="small">Run</span><b>{c.run.run_no} · {c.run.driver} · stop {c.run.sequence}</b></div>}
        {c.carrier && <div><span className="small">Carrier</span>
          <b>{c.carrier.name} · {c.carrier.con_note ? `con note ${c.carrier.con_note}` : "no con note yet"}</b></div>}
        {c.carrier?.collected_at && <div><span className="small">Collected</span>
          <b>{clock(new Date(c.carrier.collected_at))} · {c.carrier.manifest_no}</b></div>}
      </div>
      <div>
        <div className="eyebrow" style={{ marginBottom: 10 }}>History</div>
        <ol className="tl">
          {c.history.map((h, i) => {
            const l = historyLine(h);
            return (
              <li key={i}>
                <span className={`m${l.from === "wms" ? " wms" : ""}`}><Icon name={l.from === "wms" ? "box" : "tick"} size={13} width={2.6} /></span>
                <div><b>{l.title}</b><span className="w">{[clock(new Date(h.at)), l.more].filter(Boolean).join(" · ")}</span></div>
              </li>
            );
          })}
        </ol>
      </div>
      {pod && (
        <div className="pod">
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <span className="eyebrow">{pod.outcome === "delivered" ? "Proof of delivery" : "Not delivered"}</span>
            <span className="small">{clock(new Date(pod.at))}</span></div>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            {pod.signature && <Photo fileId={pod.signature} label={`Signature from ${pod.signedName ?? "the customer"}`} />}
            {pod.photo && <Photo fileId={pod.photo} label="Photo at the drop" />}
          </div>
          {pod.signedName && <div style={{ fontSize: 13 }}>Signed by <b>{pod.signedName}</b> (printed name)</div>}
          {pod.outcome === "failed" && pod.reason && (
            <div style={{ fontSize: 13 }}>{[sentence(reasonWords(pod.reason)), pod.note].filter(Boolean).join(" · ")}</div>)}
        </div>
      )}
    </aside>
  );
}

/** Consignments.html: every delivery that came from Simple WMS, where it is
 * now (a run, a carrier, delivered), and how it got there. Search by WMS
 * delivery, customer or con note; the map's search box opens it with ?q=. */
export function Consignments() {
  const [params, setParams] = useSearchParams();
  const [q, setQ] = useState(params.get("q") ?? "");
  const [tab, setTab] = useState<(typeof TABS)[number]["key"]>("all");
  const [items, setItems] = useState<Consignment[] | null>(null);
  const [total, setTotal] = useState(0);
  // the header's search, used while this is open, changes ?q=: follow it
  const urlQ = params.get("q") ?? "";
  useEffect(() => { setQ((now) => (now.trim() === urlQ.trim() ? now : urlQ)); }, [urlQ]);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [chosen, setChosen] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const latest = useRef(0);

  useEffect(() => {
    const mine = ++latest.current;
    const status = TABS.find((t) => t.key === tab)!.status;
    // a moment after the last key, not on each one
    const t = setTimeout(() => {
      api.get<Page<Consignment>>("/v1/consignments", { status, q: q.trim(), limit: 100 })
        .then((p) => { if (mine === latest.current) { setItems(p.items); setTotal(p.total); setError(null); } })
        .catch((e) => { if (mine === latest.current) setError(said(e)); });
      Promise.all(TABS.map((x) => api.get<Page<unknown>>("/v1/consignments", { status: x.status, q: q.trim(), limit: 0 })))
        .then((ps) => { if (mine === latest.current) setCounts(Object.fromEntries(TABS.map((x, i) => [x.key, ps[i].total]))); })
        .catch(() => undefined);
    }, 250);
    return () => clearTimeout(t);
  }, [q, tab]);

  function search(value: string) {
    setQ(value);
    setParams(value.trim() ? { q: value } : {}, { replace: true });
  }

  const shown = items ?? [];
  const picked = shown.find((c) => c.id === chosen) ?? shown[0] ?? null;
  return (
    <Drawer label="Consignments" head={
      <div style={{ flex: 1, minWidth: 200 }}>
        <div className="eyebrow" style={{ color: "var(--color-green)" }}>Consignments</div>
        <h1>Consignments</h1>
        <div className="sub">Every delivery that came from Simple WMS, where it is now, and how it got there.</div>
      </div>
    }>
      <label className="search"><Icon name="search" />
        <span className="sr">Search consignments</span>
        <input type="search" placeholder="WMS delivery, customer or con note" value={q} onChange={(e) => search(e.target.value)} /></label>
      <div className="tabs" role="tablist" aria-label="Consignment status">
        {TABS.map((t) => (
          <button key={t.key} type="button" className="tab" role="tab" aria-selected={tab === t.key} onClick={() => setTab(t.key)}>
            {t.label}<i className={t.tone}>{counts[t.key] ?? "–"}</i></button>
        ))}
      </div>
      {error && <div className="msg r" role="alert">{error}</div>}
      <div className="split">
        <div className="tablewrap">
          <table style={{ minWidth: 520 }}>
            <thead><tr><th>WMS DELIVERY</th><th>CUSTOMER</th><th>CTN</th><th>STATUS</th></tr></thead>
            <tbody>
              {shown.map((c) => {
                const on = c.id === picked?.id;
                return (
                  <tr key={c.id} className={on ? "sel" : undefined}>
                    <td><button type="button" className="rowbtn" aria-pressed={on} aria-label={`Show ${c.wms_ref}`}
                                onClick={() => setChosen(c.id)}>{c.wms_ref}</button></td>
                    <td style={{ whiteSpace: "nowrap" }}><b>{c.customer}</b><div className="small">{c.ship_to?.suburb}</div></td>
                    <td className="num">{c.packages.filter((p) => p.type === "carton").length}</td>
                    <td><Pill c={c} /></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {items && shown.length === 0 && <div className="small" style={{ padding: 16 }}>Nothing matches.</div>}
          {items && total > shown.length && (
            <div className="small" style={{ padding: 16 }}>Showing {shown.length} of {total}. Search to narrow it down.</div>)}
        </div>
        {picked && <Details key={picked.id} id={picked.id} />}
      </div>
    </Drawer>
  );
}
