/** The pieces every run screen shares: the run's top bar, the day's goal,
 * the no-signal banner and the list of what is waiting to send. */
import { useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import type { Outcome, RunStops } from "../api/types";
import { waitingRows, type QueueItem } from "../lib/queue";
import { plural } from "../lib/run";
import { clock } from "../lib/time";
import { useSharingState } from "../pages/OnTheRun";
import { Icon } from "./icons";

export function Phone({ label, children }: { label: string; children: ReactNode }) {
  return <main className="phone" aria-label={label}>{children}</main>;
}

export function BackTop({ to, label, title, sub }: { to: string; label: string; title: string; sub: string }) {
  return (
    <div className="top">
      <Link to={to} className="back" aria-label={label}><Icon name="back" size={22} width={2.2} /></Link>
      <div style={{ flex: 1, minWidth: 0 }}><div className="ttl">{title}</div><div className="st">{sub}</div></div>
    </div>
  );
}

/** The run, the truck, the driver and the time, and whether this phone is
 * sharing where it is. Only during a run, and it says so: a finished run
 * shares nothing. */
export function RunTop({ run, over = false }: { run: RunStops; over?: boolean }) {
  const [now, setNow] = useState(() => new Date());
  const sharing = useSharingState();
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 30_000);
    return () => clearInterval(t);
  }, []);
  const live = !over && run.status !== "done";
  const on = sharing === "on" && live;
  const looking = sharing === "waiting" && live;
  return (
    <div className="top">
      <span className="tile"><Icon name="truck" size={22} /></span>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div className="ttl">Run {run.run_no}</div>
        <div className="st">{run.vehicle.rego} · {run.driver.name} · {clock(now)}</div>
      </div>
      <span className={`chip${on || looking ? " on" : ""}`}><Icon name="pin" size={13} width={2.4} />
        {on ? "Sharing location" : looking ? "Looking for GPS" : "Location off"}</span>
    </div>
  );
}

/** What the phone does with where it is, said plainly under the run. */
export function SharingNote({ over }: { over: boolean }) {
  const sharing = useSharingState();
  if (over) return null;
  const text = sharing === "denied"
    ? "Location is off for this app, so the office can't see where you are. Each drop is still recorded."
    : sharing === "off"
      ? "This phone isn't sharing where it is."
      : "While this run is on the road, this phone shares where it is with the office about every 30 seconds, "
        + "with the app open. It stops when you finish the run or sign out.";
  return (
    <div style={{ display: "flex", gap: 8, alignItems: "flex-start", fontSize: 12, color: "var(--color-muted)" }}>
      <Icon name="pin" size={16} width={2.2} /><span>{text}</span>
    </div>
  );
}

export function Goal({ run, done, current }: { run: RunStops; done: Map<number, Outcome>; current?: number | null }) {
  const total = run.stops.length;
  const finished = run.stops.filter((s) => done.has(s.consignment_id)).length;
  return (
    <div className="goal">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", fontSize: 13, fontWeight: 800 }}>
        <span>Today's goal · {plural(total, "stop")}</span>
        <span style={{ color: "var(--color-muted)" }}>{finished} done · {total - finished} to go</span>
      </div>
      <div className="segs" role="img" aria-label={`${finished} of ${total} stops done`}
           style={{ gridTemplateColumns: `repeat(${Math.max(total, 1)},minmax(0,1fr))` }}>
        {run.stops.map((s) => {
          const o = done.get(s.consignment_id);
          const cls = o === "delivered" ? "d" : o === "failed" ? "x" : s.consignment_id === current ? "c" : "";
          return <span key={s.consignment_id} className={cls} />;
        })}
      </div>
    </div>
  );
}

export function OfflineBanner({ online, waiting }: { online: boolean; waiting: number }) {
  if (online && waiting === 0) return null;
  return (
    <div role="status" style={{ display: "flex", alignItems: "center", gap: 12, padding: "12px 14px", borderRadius: 16, flex: "none",
                                background: "var(--color-amber-bg)", color: "var(--color-amber-ink)" }}>
      <Icon name="nosignal" size={26} width={2.2} />
      <span style={{ flex: 1, minWidth: 0 }}>
        <span style={{ display: "block", fontSize: 15, fontWeight: 800 }}>
          {online ? `Sending · ${waiting} waiting` : "No signal · working from memory"}</span>
        <span style={{ display: "block", fontSize: 12, fontWeight: 600 }}>
          {waiting > 0
            ? `${plural(waiting, "confirmation")} waiting · they send by themselves when you're back in range`
            : "Your whole run is on this phone, so keep going."}</span>
      </span>
    </div>
  );
}

function since(iso: string): string {
  return clock(new Date(iso));
}

/** WAITING TO SEND: newest first, a row for each confirmation with its
 * pictures in it. One that wasn't taken can be tried again or let go. */
export function WaitingList({ items, onRetry, onDismiss }: {
  items: QueueItem[]; onRetry?: (id: string) => void; onDismiss?: (id: string) => void;
}) {
  if (items.length === 0) return null;
  const rows = waitingRows(items.slice(0, 8)).map((r) => ({
    key: r.id, text: r.text, error: r.error, failed: r.status === "failed",
    pill: r.status === "sent" ? <span className="pill p-green" style={{ fontSize: 11 }}>Sent {since(r.at)}</span>
      : r.status === "failed" ? <span className="pill p-red" style={{ fontSize: 11 }}>Not taken</span>
      : <span className="pill p-amber" style={{ fontSize: 11 }}>Waiting since {since(r.at)}</span>,
  }));
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <span className="lbl">Waiting to send</span>
      <div style={{ borderRadius: 16, background: "#fff", border: "1px solid var(--color-line)", overflow: "hidden" }}>
        {rows.map((r) => (
          <div key={r.key} style={{ padding: "11px 14px", borderTop: "1px solid var(--color-row)" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10 }}>
              <span style={{ fontSize: 14, fontWeight: 700 }}>{r.text}</span>{r.pill}
            </div>
            {r.error && <div className="small" style={{ color: "var(--color-red-ink)", marginTop: 4 }}>{r.error}</div>}
            {r.failed && (onRetry || onDismiss) && (
              <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
                {onRetry && <button type="button" className="chip" onClick={() => onRetry(r.key)}>Try again</button>}
                {onDismiss && <button type="button" className="chip" onClick={() => onDismiss(r.key)}>Dismiss</button>}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
