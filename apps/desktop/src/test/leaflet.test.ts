/** lib/slippy.ts overrides three of Leaflet's private TileLayer methods so a
 * dropped tile never points at a data: image (the CSP refuses data:). A
 * Leaflet upgrade that renames or drops them, or stops using data:, must
 * fail here rather than quietly in the browser. */
import L from "leaflet";

describe("the Leaflet slippy.ts is written against", () => {
  it("still has the private tile methods slippy.ts replaces", () => {
    const proto = L.TileLayer.prototype as unknown as Record<string, unknown>;
    for (const name of ["_abortLoading", "_removeTile", "_tileReady"]) {
      expect(typeof proto[name], name).toBe("function");
    }
  });

  it("still empties a dropped tile with a data: image", () => {
    expect(L.Util.emptyImageUrl.startsWith("data:")).toBe(true);
  });
});
