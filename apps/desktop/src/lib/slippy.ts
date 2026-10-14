/** The real map behind "Where is it": Leaflet, raster tiles from wherever
 * TMS_MAP_TILE_URL says (a provider or a tile server of your own; never
 * OpenStreetMap's own servers). Loaded only when the drawer opens, so the
 * map screen doesn't carry it. Each truck is a dot, its accuracy a ring, and
 * its run and driver a label, set as text (never as HTML). */
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import type { MapTiles, TruckAt } from "../api/types";

export interface SlippyMap {
  show(trucks: TruckAt[], focus: number | null): void;
  destroy(): void;
}

export function mountMap(el: HTMLElement, tiles: MapTiles, onTileError: () => void): SlippyMap {
  const map = L.map(el, { zoomControl: true, attributionControl: true }).setView([-37.56, 143.85], 11);
  map.attributionControl.setPrefix(false);
  const layer = L.tileLayer(tiles.tile_url!, { attribution: tiles.attribution ?? "", maxZoom: 19 }).addTo(map);
  let warned = false;
  layer.on("tileerror", () => { if (!warned) { warned = true; onTileError(); } });
  const drawn = L.layerGroup().addTo(map);
  let framed = false;

  return {
    show(trucks, focus) {
      drawn.clearLayers();
      const points: L.LatLngExpression[] = [];
      for (const t of trucks) {
        const where: L.LatLngExpression = [Number(t.latitude), Number(t.longitude)];
        points.push(where);
        const mine = t.run_id === focus;
        L.circle(where, { radius: Number(t.accuracy_m), color: "#1F8A5B", weight: 1, fillOpacity: 0.08 }).addTo(drawn);
        const label = document.createElement("span");
        label.textContent = `${t.run_no} · ${t.driver.name} · ${t.vehicle.rego}`;
        L.circleMarker(where, { radius: mine ? 9 : 7, color: "#fff", weight: 2, fillColor: mine ? "#146B45" : "#1F8A5B", fillOpacity: 1 })
          .bindTooltip(label, { permanent: true, direction: "right", offset: [10, 0] }).addTo(drawn);
      }
      // framed once, so a dispatcher's own panning isn't undone every 30 s
      if (framed || points.length === 0) return;
      framed = true;
      const focused = trucks.find((t) => t.run_id === focus);
      if (focused) map.setView([Number(focused.latitude), Number(focused.longitude)], 14);
      else map.fitBounds(L.latLngBounds(points), { padding: [40, 40], maxZoom: 14 });
    },
    destroy() { map.remove(); },
  };
}
