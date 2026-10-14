import { useCallback, useEffect, useState, type FormEvent } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { FeedItem, Page, Run, TruckAt, Vehicle } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { clock, isoDay } from "../lib/days";
import { cantGo } from "../lib/fleet";
import { usePoll } from "../lib/poll";
import { leftOpen } from "../lib/live";
import { DrawerBoundary } from "../ui/DrawerBoundary";
import { Feed } from "../ui/Feed";
import { Icon, Logo } from "../ui/icons";
import { TruckCard, TruckMarkers } from "../ui/Trucks";

interface Counts { toPlan: number; onRoad: number; delivered: number }

async function count(status: string): Promise<number> {
  return (await api.get<Page<unknown>>("/v1/consignments", { status, limit: 0 })).total;
}

function initials(name: string): string {
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join("").toUpperCase() || "?";
}

/** Main.html: the region as a picture with the day's trucks on it, the day's
 * numbers and the picked truck's card over it, and the drawers. */
export function MapHome() {
  const { user, signOut, can } = useAuth();
  const canLocate = can("location:read");
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const [counts, setCounts] = useState<Counts | null>(null);
  // trucks grounded or overdue today: a person needs to look
  const [stuck, setStuck] = useState(0);
  const [blocked, setBlocked] = useState<Vehicle[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [feed, setFeed] = useState<FeedItem[] | null>(null);
  const [heardFrom, setHeardFrom] = useState<Map<number, TruckAt>>(new Map());
  // the truck whose card shows: the first on the road until one is picked
  const [picked, setPicked] = useState<number | null | "none">(null);
  const [now, setNow] = useState(() => new Date());
  const [menu, setMenu] = useState(false);
  const [q, setQ] = useState("");

  const load = useCallback(async () => {
    try {
      // to plan: packed, and what came back undelivered; delivered: today's drops
      // with the runs left open from earlier days, shown as such and not counted today
      const [toPlan, departed, listed] = await Promise.all([
        count("packed,failed"), count("departed"),
        api.get<Page<Run>>("/v1/runs", { date: isoDay(new Date()), left_open: true })]);
      const stale = listed.items.filter((r) => leftOpen(r));
      const today = listed.items.filter((r) => !leftOpen(r));
      const delivered = today.reduce(
        (n, run) => n + run.stops.filter((s) => s.outcome === "delivered").length, 0);
      // what is still on a truck left open is not on the road today
      const onRoad = departed - stale.reduce((n, run) => n + run.stops.filter((s) => !s.outcome).length, 0);
      setCounts({ toPlan, onRoad: Math.max(onRoad, 0), delivered });
      setRuns(listed.items);
    } catch { /* the numbers wait for the next go */ }
    try {
      const trucks = (await api.get<Page<Vehicle>>("/v1/vehicles", { date: isoDay(new Date()), active: true })).items;
      setStuck(cantGo(trucks));
      setBlocked(trucks.filter((v) => v.active && (v.state === "grounded" || v.state === "overdue")));
    } catch { /* so does this one */ }
    try {
      setFeed((await api.get<{ items: FeedItem[] }>("/v1/feed", { limit: 5 })).items);
    } catch { /* the feed waits for the next go */ }
    if (canLocate) {
      try {
        const at = await api.get<Page<TruckAt>>("/v1/positions", { date: isoDay(new Date()) });
        setHeardFrom(new Map(at.items.map((p) => [p.run_id, p])));
      } catch { /* the trucks stay where they were last drawn */ }
    }
  }, [canLocate]);

  useEffect(() => { void load(); }, [load]);
  // every 30 s while the map is seen
  usePoll(() => { setNow(new Date()); void load(); }, 30_000);

  function search(e: FormEvent) {
    e.preventDefault();
    navigate(`/consignments?q=${encodeURIComponent(q)}`);
  }

  // a truck on a run today is drawn with its run, not as one that can't go
  const onRun = new Set(runs.filter((r) => r.status === "planned" || r.status === "departed").map((r) => r.vehicle.rego));
  const shown = picked === "none" ? null
    : runs.find((r) => r.id === picked) ?? runs.find((r) => r.status === "departed" && !leftOpen(r)) ?? null;

  const weekday = new Intl.DateTimeFormat("en-AU", { weekday: "short" }).format(now);
  return (
    <div className="app">
      <header className="hdr">
        <div className="brand"><Logo /><span>Simple TMS</span></div>
        <form className="search" role="search" onSubmit={search}>
          <Icon name="search" />
          <label className="sr" htmlFor="find">Search</label>
          <input id="find" type="search" placeholder="Find a consignment, customer or con note"
                 value={q} onChange={(e) => setQ(e.target.value)} />
        </form>
        <div className="hright">
          <span className="live"><span className="dot" style={{ width: 8, height: 8, background: "#1F8A5B" }} />
            Live · {weekday} {clock(now)}</span>
          <div style={{ position: "relative" }}>
            <button type="button" className="avatar" aria-label={`Account: ${user?.display_name}`}
                    aria-expanded={menu} onClick={() => setMenu(!menu)}>
              {initials(user?.display_name ?? "")}
            </button>
            {menu && (
              <div className="panel" style={{ position: "absolute", right: 0, top: 52, zIndex: 20, background: "#fff",
                                               borderRadius: 14, boxShadow: "var(--shadow-lift)", padding: 12, minWidth: 180 }}>
                <div className="small" style={{ marginBottom: 8 }}>{user?.display_name} · {user?.role}</div>
                <button type="button" className="btn sm" onClick={() => void signOut()}>Sign out</button>
              </div>
            )}
          </div>
        </div>
      </header>

      <main className="stage">
        <div className="world">
          <img src="/region.svg" alt="The region: the depot with its carrier bay, the roads and customer stops" />
          <TruckMarkers runs={runs} heardFrom={heardFrom} blocked={blocked.filter((v) => !onRun.has(v.rego))}
                        picked={shown?.id ?? null} onPick={setPicked} now={now} />
        </div>
        <div className="ov" style={{ top: 20, left: 24, width: 318 }}>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(3,minmax(0,1fr))", gap: 8 }}>
            <NavLink to="/plan" className="hud">
              <div className="eyebrow" style={{ color: "var(--color-amber-ink)" }}>To plan</div>
              <div className="v num">{counts?.toPlan ?? "–"}</div>
            </NavLink>
            <div className="hud">
              <div className="eyebrow" style={{ color: "var(--color-green)" }}>On road</div>
              <div className="v num">{counts?.onRoad ?? "–"}</div>
            </div>
            <div className="hud">
              <div className="eyebrow" style={{ color: "var(--color-ink-2)" }}>Delivered</div>
              <div className="v num">{counts?.delivered ?? "–"}</div>
            </div>
          </div>
        </div>
        {shown && (
          <TruckCard run={shown} heardFrom={heardFrom.get(shown.id)} canLocate={canLocate} now={now}
                     onClose={() => setPicked("none")} />
        )}
        <Feed items={feed} now={now} />
        <nav className="ov nav" aria-label="Main drawers" style={{ bottom: 24, left: "50%", transform: "translateX(-50%)" }}>
          <NavLink to="/plan" className={({ isActive }) => (isActive ? "on" : "")}>
            <Icon name="route" />Plan runs{counts && counts.toPlan > 0 && <i>{counts.toPlan}</i>}
          </NavLink>
          <NavLink to="/consignments" className={({ isActive }) => (isActive ? "on" : "")}><Icon name="box" />Consignments</NavLink>
          <NavLink to="/carriers" className={({ isActive }) => (isActive ? "on" : "")}><Icon name="hand" />Carriers</NavLink>
          <NavLink to="/fleet" className={({ isActive }) => (isActive ? "on" : "")}><Icon name="truck" />Fleet
            {stuck > 0 && <i className="r" title={`${stuck} can't take a run`}>{stuck}</i>}</NavLink>
        </nav>
      </main>
      <DrawerBoundary key={pathname}><Outlet context={{ reloadCounts: load }} /></DrawerBoundary>
    </div>
  );
}
