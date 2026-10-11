/** "Open in Maps": a link the phone's own maps app opens, built from the
 * ship-to address. No maps service of our own, and nothing to pay for. */
import type { ShipTo } from "../api/types";

/** "12 Sturt St, Ballarat VIC 3350" */
export function addressLine(t: ShipTo): string {
  const place = [t.suburb, t.state, t.postcode].filter(Boolean).join(" ");
  return [t.address, place].filter(Boolean).join(", ");
}

/** Apple Maps on an iPhone or iPad, the Android maps chooser (geo:) on
 * Android, and Google Maps in a browser anywhere else. */
export function mapsLink(t: ShipTo, userAgent = typeof navigator === "undefined" ? "" : navigator.userAgent): string {
  const where = [addressLine(t), t.country === "AU" ? "Australia" : t.country].filter(Boolean).join(", ")
    || t.name || "";
  const q = encodeURIComponent(where);
  if (/iPhone|iPad|iPod/.test(userAgent)) return `https://maps.apple.com/?q=${q}`;
  if (/Android/.test(userAgent)) return `geo:0,0?q=${q}`;
  return `https://www.google.com/maps/search/?api=1&query=${q}`;
}
