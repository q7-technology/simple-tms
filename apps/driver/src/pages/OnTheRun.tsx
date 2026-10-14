import { createContext, useContext } from "react";
import { Outlet, useParams } from "react-router-dom";
import { useQueueItems, useSession } from "../auth/Session";
import { finished, loadRun } from "../lib/run";
import { useSharing, type Sharing } from "../lib/share";

const SharingCtx = createContext<Sharing>("off");

/** Every screen of a run, under one roof: the phone shares where it is from
 * here while the run is on the road and not finished, the same watch from
 * one stop's screen to the next. Leaving the run's screens (Today, signing
 * out, closing the app) stops it. */
export function OnTheRun() {
  const runId = Number(useParams().runId);
  const { driver } = useSession();
  const items = useQueueItems(runId);
  const run = loadRun(driver!.id, runId);
  const active = !!run && run.status === "departed" && !finished(run, items);
  const sharing = useSharing(runId, active);
  return <SharingCtx.Provider value={sharing}><Outlet /></SharingCtx.Provider>;
}

export function useSharingState(): Sharing {
  return useContext(SharingCtx);
}
