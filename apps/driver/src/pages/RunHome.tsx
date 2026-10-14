import { Link, Navigate, useLocation, useParams } from "react-router-dom";
import { useQueueItems, useSession } from "../auth/Session";
import { addressLine, mapsLink } from "../lib/maps";
import { finished, finishedByOffice, fromEarlierDay, nextStop, outcomes, plural } from "../lib/run";
import { dayLabel } from "../lib/time";
import { Icon } from "../ui/icons";
import { Goal, OfflineBanner, Phone, RunTop, SharingNote, WaitingList } from "../ui/kit";
import { useOnRun } from "./OnTheRun";

/** Between stops (DriverOffline.html): the next stop, what is waiting to
 * send, and the run's end. Works from the phone's copy of the run; reloads
 * it from the office when there is signal. */
export function RunHome() {
  const runId = Number(useParams().runId);
  const { online, waiting, queue } = useSession();
  const items = useQueueItems(runId);
  const note = (useLocation().state as { note?: string } | null)?.note;
  // the phone's copy, looked up again from the office by OnTheRun
  const run = useOnRun();

  if (!run) return <Navigate to="/" replace />;
  const done = outcomes(run, items);
  const next = nextStop(run, done);
  const over = finished(run, items);
  const byOffice = finishedByOffice(run, items);
  const delivered = [...done.values()].filter((o) => o === "delivered").length;
  const back = done.size - delivered;

  function finish() {
    void queue.submit({ path: `/v1/runs/${runId}/finish`, body: {}, label: `${run!.run_no} · finished`,
                        run_id: runId, consignment_id: null, outcome: "finished" });
  }

  function dismiss(id: string) {
    if (!window.confirm("Let this go? It won't be sent, and the office won't see it. Do the stop again, or call the office.")) return;
    void queue.dismiss(id);
  }

  return (
    <Phone label={`Run ${run.run_no}`}>
      <RunTop run={run} over={over} />
      <OfflineBanner online={online} waiting={waiting} />
      <Goal run={run} done={done} current={next?.consignment_id} />
      <div className="scroll">
        {note && <div className="msg a" role="status">{note}</div>}
        {byOffice && <div className="msg a" role="status">The office finished {run.run_no}.</div>}
        {!over && fromEarlierDay(run) && (
          <div className="msg a" role="status">
            {`${run.run_no} is from ${dayLabel(new Date(`${run.date}T00:00`))} and was never finished, so this phone isn't sharing where it is. `
              + "Finish it once every stop is done."}</div>)}
        {next && !over && (
          <section aria-label="Next stop" className="card" style={{ padding: 16, display: "flex", flexDirection: "column", gap: 8 }}>
            <div className="lbl">Stop {next.sequence} of {run.stops.length}</div>
            <div style={{ fontSize: 26, fontWeight: 800, letterSpacing: -0.4, lineHeight: 1.1 }}>{next.customer}</div>
            <div style={{ fontSize: 14, color: "var(--color-ink-2)" }}>
              {[addressLine(next.ship_to), plural(next.packages.length, "carton")].filter(Boolean).join(" · ")}</div>
            <a href={mapsLink(next.ship_to)} target="_blank" rel="noopener noreferrer" className="big ghost" style={{ height: 48, fontSize: 15 }}>
              <Icon name="map" />Open in Maps</a>
          </section>
        )}
        {!next && !over && (
          <section className="card" aria-label="Every stop done">
            <b style={{ fontSize: 20 }}>Every stop is done.</b>
            <div style={{ fontSize: 14, color: "var(--color-ink-2)", marginTop: 4 }}>
              {delivered} delivered{back ? `, ${back} coming back to the depot` : ""}. Finish the run when you're back.</div>
          </section>
        )}
        {over && (
          <section className="card" aria-label="Run finished">
            <b style={{ fontSize: 20 }}>{run.run_no} is finished.</b>
            <div style={{ fontSize: 14, color: "var(--color-ink-2)", marginTop: 4 }}>
              {delivered} delivered{back ? `, ${back} back at the depot` : ""}. Thanks, {run.driver.name}.</div>
          </section>
        )}
        <WaitingList items={items} onRetry={(id) => void queue.retry(id)} onDismiss={dismiss} />
        <SharingNote over={over} />
        <div style={{ display: "flex", gap: 8, alignItems: "flex-start", fontSize: 12, color: "var(--color-muted)" }}>
          <Icon name="info" size={16} width={2.2} />
          <span>Your whole run is saved on this phone, so keep going. Each confirmation has its own number, so sending it twice never records it twice.</span>
        </div>
      </div>
      <div style={{ display: "flex", gap: 10, flex: "none" }}>
        {waiting > 0 && (
          <button type="button" className="big ghost" style={{ width: 130 }} onClick={() => void queue.drain()}>Retry now</button>)}
        {next && !over && (
          <Link to={`/run/${runId}/stop/${next.consignment_id}`} className="big primary" style={{ flex: 1 }}>
            Start stop {next.sequence}<Icon name="go" size={20} width={2.6} /></Link>)}
        {!next && !over && (
          <button type="button" className="big primary" style={{ flex: 1 }} onClick={finish}>Finish the run</button>)}
        {over && <Link to="/" className="big ghost" style={{ flex: 1 }}>Back to today's runs</Link>}
      </div>
    </Phone>
  );
}
