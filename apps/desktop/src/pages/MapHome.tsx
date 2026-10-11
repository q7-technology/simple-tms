import { useCallback, useEffect, useState, type FormEvent } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { Page } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { clock } from "../lib/days";
import { DrawerBoundary } from "../ui/DrawerBoundary";
import { Icon, Logo } from "../ui/icons";

interface Counts { toPlan: number; onRoad: number; delivered: number }

async function count(status: string): Promise<number> {
  return (await api.get<Page<unknown>>("/v1/consignments", { status, limit: 0 })).total;
}

function initials(name: string): string {
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join("").toUpperCase() || "?";
}

/** Main.html: the region as a picture, the day's numbers over it, and the
 * drawers. Trucks moving on the map come with live location (step 5). */
export function MapHome() {
  const { user, signOut } = useAuth();
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const [counts, setCounts] = useState<Counts | null>(null);
  const [now, setNow] = useState(() => new Date());
  const [menu, setMenu] = useState(false);
  const [q, setQ] = useState("");

  const load = useCallback(async () => {
    try {
      const [toPlan, onRoad, delivered] = await Promise.all(
        ["packed", "departed", "delivered"].map(count));
      setCounts({ toPlan, onRoad, delivered });
    } catch { /* the numbers wait for the next go */ }
  }, []);

  useEffect(() => {
    void load();
    const t = setInterval(() => { setNow(new Date()); void load(); }, 30_000);
    return () => clearInterval(t);
  }, [load]);

  function search(e: FormEvent) {
    e.preventDefault();
    navigate(`/consignments?q=${encodeURIComponent(q)}`);
  }

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
        <img src="/region.svg" alt="The region: the depot with its carrier bay, the roads and customer stops" />
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
        <nav className="ov nav" aria-label="Main drawers" style={{ bottom: 24, left: "50%", transform: "translateX(-50%)" }}>
          <NavLink to="/plan" className={({ isActive }) => (isActive ? "on" : "")}>
            <Icon name="route" />Plan runs{counts && counts.toPlan > 0 && <i>{counts.toPlan}</i>}
          </NavLink>
          <NavLink to="/consignments" className={({ isActive }) => (isActive ? "on" : "")}><Icon name="box" />Consignments</NavLink>
          <NavLink to="/carriers" className={({ isActive }) => (isActive ? "on" : "")}><Icon name="hand" />Carriers</NavLink>
          <NavLink to="/fleet" className={({ isActive }) => (isActive ? "on" : "")}><Icon name="truck" />Fleet</NavLink>
        </nav>
      </main>
      <DrawerBoundary key={pathname}><Outlet context={{ reloadCounts: load }} /></DrawerBoundary>
    </div>
  );
}
