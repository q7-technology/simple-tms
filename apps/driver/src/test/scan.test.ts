import { matchScan, readLabel } from "../lib/scan";
import { mapsLink } from "../lib/maps";
import { RUN } from "./samples";

describe("reading a carton label", () => {
  it("reads an SSCC however the scanner sends it", () => {
    expect(readLabel("093123450000000101")).toEqual({ sscc: "093123450000000101" });
    expect(readLabel("00093123450000000101")).toEqual({ sscc: "093123450000000101" });
    expect(readLabel("(00) 093123450000000101")).toEqual({ sscc: "093123450000000101" });
    expect(readLabel("]C100093123450000000101")).toEqual({ sscc: "093123450000000101" });
  });

  it("reads a package number, alone or after its delivery", () => {
    expect(readLabel("2")).toEqual({ packageNo: 2 });
    expect(readLabel("0080034519-2")).toEqual({ ref: "0080034519", packageNo: 2 });
    expect(readLabel("TMS-RUN-1/3")).toEqual({ ref: "TMS-RUN-1", packageNo: 3 });
  });

  it("finds the carton on this stop, on another, or nowhere", () => {
    const [here, there] = RUN.stops;
    expect(matchScan("093123450000000101", RUN.stops, here)).toEqual({ kind: "here", package_no: 1 });
    expect(matchScan("2", RUN.stops, here)).toEqual({ kind: "here", package_no: 2 });
    expect(matchScan("008003452-2", RUN.stops, here)).toEqual({ kind: "other", stop: there, package_no: 2 });
    expect(matchScan("093123450000000999", RUN.stops, here)).toEqual({ kind: "unknown" });
    expect(matchScan("7", RUN.stops, here)).toEqual({ kind: "unknown" });
  });
});

describe("Open in Maps", () => {
  const to = RUN.stops[0].ship_to;
  it("opens the phone's own maps app", () => {
    expect(mapsLink(to, "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X)"))
      .toBe("https://maps.apple.com/?q=12%20Sturt%20St%2C%20Ballarat%20VIC%203350%2C%20Australia");
    expect(mapsLink(to, "Mozilla/5.0 (Linux; Android 15)")).toBe("geo:0,0?q=12%20Sturt%20St%2C%20Ballarat%20VIC%203350%2C%20Australia");
  });
});
