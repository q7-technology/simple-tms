import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { Outlet, useParams } from "react-router-dom";
import { api, ApiError } from "../api/client";
import type { RunStops } from "../api/types";
import { useQueueItems, useSession } from "../auth/Session";
import { finished, fromEarlierDay, loadRun, saveRun } from "../lib/run";
import { useSharing, type Sharing } from "../lib/share";

interface OnRun {
  /** the run as the phone has it: its own copy, then the office's once heard */
  run: RunStops | null;
  sharing: Sharing;
}

const OnRunCtx = createContext<OnRun>({ run: null, sharing: "off" });

/** Every screen of a run, under one roof. The run is read from the phone's
 * copy and looked up again from the office here, once (and when the signal
 * comes back, or the office says the run is over), so what the screens show
 * and whether the phone shares come from the same run. The phone shares
 * where it is while the run is on the road and not finished, the same watch
 * from one stop's screen to the next. Leaving the run's screens (Today,
 * signing out, closing the app) stops it. */
export function OnTheRun() {
  const runId = Number(useParams().runId);
  const { driver, online } = useSession();
  const me = driver!.id;
  const items = useQueueItems(runId);
  const [run, setRun] = useState<RunStops | null>(() => loadRun(me, runId));

  const keep = useCallback((fresh: RunStops) => {
    try { saveRun(me, fresh); } catch { /* the copy already on the phone will do */ }
    setRun(fresh);
  }, [me]);

  const refresh = useCallback(async () => {
    try {
      keep(await api.get<RunStops>(`/v1/runs/${runId}/stops`));
      return true;
    } catch (e) {
      // gone from the office (not this driver's any more): the copy will do
      return e instanceof ApiError && e.status === 404 ? true : false;
    }
  }, [runId, keep]);

  useEffect(() => {
    if (online) void refresh();
  }, [online, refresh]);

  // the API says the run isn't on the road: look again; finished by the
  // office and no signal to ask, the phone's copy says so too
  const ended = useCallback((code: string | null) => {
    void refresh().then((heard) => {
      if (heard || code !== "run_done") return;
      setRun((r) => {
        if (!r || r.status === "done") return r;
        const done = { ...r, status: "done" as const };
        try { saveRun(me, done); } catch { /* shown as finished all the same */ }
        return done;
      });
    });
  }, [refresh, me]);

  // a run left open from an earlier day may still be finished, but is never shared
  const active = !!run && run.status === "departed" && !finished(run, items) && !fromEarlierDay(run);
  const sharing = useSharing(runId, active, ended);
  return <OnRunCtx.Provider value={{ run, sharing }}><Outlet /></OnRunCtx.Provider>;
}

/** The run the screens show, as OnTheRun has it now. */
export function useOnRun(): RunStops | null {
  return useContext(OnRunCtx).run;
}

export function useSharingState(): Sharing {
  return useContext(OnRunCtx).sharing;
}
