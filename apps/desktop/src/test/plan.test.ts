import type { Consignment } from "../api/types";
import { cardOf, dropPosition, kg, m3, moved, packagesLabel, positionWithout, runColour } from "../lib/plan";
import { dayLabel, dayOptions, isoDay } from "../lib/days";

const consignment = (over: Partial<Consignment> = {}): Consignment => ({
  id: 7, wms_ref: "0080034519", warehouse: "BAL-WH01", owner: "DEFAULT", customer: "Station Deli",
  ship_to: { name: "Station Deli", suburb: "Ballan" }, required_by: null, status: "packed",
  created_at: "2026-10-13T22:00:00Z", weight_kg: "72", volume_m3: "0.5",
  packages: [1, 2, 3, 4, 5].map((n) => ({
    package_no: n, type: "carton", weight_kg: null, length_cm: null, width_cm: null,
    height_cm: null, sscc: null,
  })),
  ...over,
});

describe("the plan board", () => {
  it("makes a card from a consignment", () => {
    const card = cardOf(consignment());
    expect(card).toEqual({ id: 7, wms_ref: "0080034519", customer: "Station Deli", suburb: "Ballan",
                           status: "packed", packages: 5, cartons: 5, weight_kg: "72", volume_m3: "0.5" });
    expect(packagesLabel(card)).toBe("5 ctn");
    expect(kg(card)).toBe("72 kg");
    expect(m3(card)).toBe("0.5 m³");
  });

  it("calls a mix of cartons and pallets packages", () => {
    const c = consignment();
    c.packages[0].type = "pallet";
    expect(packagesLabel(cardOf(c))).toBe("5 pkg");
  });

  it("moves one stop and keeps the rest in order", () => {
    expect(moved([1, 2, 3, 4], 0, 2)).toEqual([2, 3, 1, 4]);
    expect(moved([1, 2, 3, 4], 3, 0)).toEqual([4, 1, 2, 3]);
    expect(moved([1, 2, 3], 0, 5)).toEqual([1, 2, 3]);
  });

  it("drops above a stop in its top half and below it in the bottom half", () => {
    expect(dropPosition(0, 10, 0, 52)).toBe(1);
    expect(dropPosition(0, 40, 0, 52)).toBe(2);
    expect(dropPosition(2, 120, 104, 52)).toBe(3);
  });

  it("counts a stop's own old place out when it moves down its run", () => {
    // stops A B C; A dropped below C is position 4 as drawn, 3 once A has gone
    expect(positionWithout(0, 4)).toBe(3);
    expect(positionWithout(2, 1)).toBe(1);
  });

  it("gives runs the map's colours in turn", () => {
    expect([0, 1, 2, 3].map(runColour)).toEqual(["#1F8A5B", "#2F6FB8", "#7B5CC4", "#1F8A5B"]);
  });
});

describe("days", () => {
  it("offers today and the week ahead", () => {
    const opts = dayOptions(new Date(2026, 9, 13, 11, 16));
    expect(opts[0]).toEqual({ value: "2026-10-13", label: "Today · Tue 13 Oct" });
    expect(opts[1]).toEqual({ value: "2026-10-14", label: "Tomorrow · Wed 14 Oct" });
    expect(opts).toHaveLength(7);
    expect(isoDay(new Date(2026, 11, 31))).toBe("2026-12-31");
    expect(dayLabel(new Date(2027, 0, 1))).toBe("Fri 1 Jan");
  });
});
