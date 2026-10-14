import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { MapTiles, Page, TruckAt } from "../api/types";
import { isoDay } from "../lib/days";
import { heard } from "../lib/live";
import { usePoll } from "../lib/poll";
import type { SlippyMap } from "../lib/slippy";
import { Drawer } from "../ui/Drawer";

/** "Where is it": each truck on the road where its phone last said, on a real
 * map when tiles are set up (TMS_MAP_TILE_URL), and always as a list, which
 * is all there is without them. Only the latest position: no trails. */
export function WhereIsIt() {
  const focus = Number(useSearchParams()[0].get("run")) || null;
  const [tiles, setTiles] = useState<MapTiles | null>(null);
  const [trucks, setTrucks] = useState<TruckAt[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tilesFailed, setTilesFailed] = useState(false);
  const [now, setNow] = useState(() => new Date());
  const box = useRef<HTMLDivElement>(null);
  const map = useRef<SlippyMap | null>(null);
  // the map is ready to draw on: set once Leaflet has loaded and mounted
  const [ready, setReady] = useState(false);

  const load = useCallback(async () => {
    try {
      setTrucks((await api.get<Page<TruckAt>>("/v1/positions", { date: isoDay(new Date()) })).items);
      setNow(new Date());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Where the trucks are couldn't be loaded.");
    }
  }, []);

  useEffect(() => {
    api.get<MapTiles>("/v1/map").then(setTiles).catch(() => setTiles({ tile_url: null, attribution: null }));
    void load();
  }, [load]);
  // every 30 s while the drawer is seen
  usePoll(() => void load(), 30_000);

  // the map is loaded only if there are tiles to draw it with
  useEffect(() => {
    if (!tiles?.tile_url || !box.current) return;
    let gone = false;
    void import("../lib/slippy").then(({ mountMap }) => {
      if (gone || !box.current) return;
      map.current = mountMap(box.current, tiles, () => setTilesFailed(true));
      setReady(true);
    });
    return () => { gone = true; map.current?.destroy(); map.current = null; setReady(false); };
  }, [tiles]);

  useEffect(() => {
    if (ready && trucks) map.current?.show(trucks, focus);
  }, [ready, trucks, focus]);

  return (
    <Drawer label="Where is it" head={<div><h1>Where is it</h1>
      <div className="sub">Each truck on the road, where its driver's phone last said. Positions are deleted after the retention period.</div></div>}>
      {tiles && !tiles.tile_url && (
        <div className="msg a">No map tiles are set up, so each truck's position is listed without a map. To show a map,
          set TMS_MAP_TILE_URL: see "Map tiles" in the README.</div>)}
      {tilesFailed && (
        <div className="msg r" role="alert">The map tiles didn't load. Check TMS_MAP_TILE_URL, and that the desktop may
          load them (TMS_MAP_TILE_ORIGIN in the README).</div>)}
      {tiles?.tile_url && <div ref={box} className="slippy" aria-label="Map of the trucks on the road" role="region" />}
      {error && <div className="msg r" role="alert">{error}</div>}
      {trucks && trucks.length === 0 && (
        <div className="small">No truck on the road has been heard from today. A phone shares where it is from Start until the run is finished.</div>)}
      {trucks && trucks.length > 0 && (
        <div className="tablewrap" style={{ flex: "none" }}>
          <table>
            <thead><tr><th>RUN</th><th>TRUCK</th><th>DRIVER</th><th>LAST HEARD</th><th>WHERE</th></tr></thead>
            <tbody>
              {trucks.map((t) => {
                const h = heard(t.received_at, now);
                return (
                  <tr key={t.run_id} className={t.run_id === focus ? "sel" : ""}>
                    <td><b>{t.run_no}</b></td><td>{t.vehicle.rego}</td><td>{t.driver.name}</td>
                    <td className={h.stale ? "due-a" : ""}>{h.text}</td>
                    <td className="num">{t.latitude}, {t.longitude} (within {t.accuracy_m} m)</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </Drawer>
  );
}
