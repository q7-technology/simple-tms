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

// Leaflet stops a tile it no longer wants by pointing it at a data: GIF, which
// the desktop's CSP refuses (img-src has no data:). These tiles use a blob:
// copy of the same GIF instead: the same three methods, with the other image.
let empty: string | null = null;
const emptyImage = () => (empty ??= URL.createObjectURL(new Blob(
  [Uint8Array.from(atob("R0lGODlhAQABAAD/ACwAAAAAAQABAAACADs="), (c) => c.charCodeAt(0))], { type: "image/gif" })));
type Tile = { el: HTMLImageElement; coords: L.Coords };
const noop = () => {};
const Tiles = L.TileLayer.extend({
  _abortLoading(this: { _tiles: Record<string, Tile>; _tileZoom: number; fire: (e: string, d: object) => void }) {
    for (const key of Object.keys(this._tiles)) {
      const { el, coords } = this._tiles[key];
      if (coords.z === this._tileZoom) continue;
      el.onload = noop;
      el.onerror = noop;
      if (!el.complete) {
        el.src = emptyImage();
        el.remove();
        delete this._tiles[key];
        this.fire("tileabort", { tile: el, coords });
      }
    }
  },
  _removeTile(this: { _tiles: Record<string, Tile> }, key: string) {
    const tile = this._tiles[key];
    if (!tile) return;
    tile.el.setAttribute("src", emptyImage());
    return (L.GridLayer.prototype as unknown as { _removeTile: (k: string) => void })._removeTile.call(this, key);
  },
  _tileReady(this: { _map: unknown }, coords: L.Coords, err: unknown, tile?: HTMLImageElement) {
    if (!this._map || tile?.getAttribute("src") === emptyImage()) return;
    return (L.GridLayer.prototype as unknown as { _tileReady: (...a: unknown[]) => void })._tileReady.call(this, coords, err, tile);
  },
}) as unknown as new (url: string, options: L.TileLayerOptions) => L.TileLayer;

export function mountMap(el: HTMLElement, tiles: MapTiles, onTileError: () => void): SlippyMap {
  const map = L.map(el, { zoomControl: true, attributionControl: true }).setView([-37.56, 143.85], 11);
  map.attributionControl.setPrefix(false);
  const layer = new Tiles(tiles.tile_url!, { attribution: tiles.attribution ?? "", maxZoom: 19 }).addTo(map);
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
