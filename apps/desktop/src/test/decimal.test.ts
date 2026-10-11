import { ONE, plain, ratio, roundDown, roundUp, sum, toMicro } from "../lib/decimal";

describe("decimals without floats", () => {
  it("reads decimal strings into whole millionths", () => {
    expect(toMicro("9.5")).toBe(9_500_000n);
    expect(toMicro("1200")).toBe(1_200_000_000n);
    expect(toMicro("0.000001")).toBe(1n);
    expect(toMicro(null)).toBe(0n);
    expect(toMicro("-2")).toBe(-2_000_000n);
  });

  it("drops what is past a millionth", () => {
    expect(toMicro("0.001157625")).toBe(1157n);
  });

  it("refuses what is not a decimal", () => {
    expect(() => toMicro("1e3")).toThrow("not a decimal");
  });

  it("adds exactly where a float would not", () => {
    expect(sum(["0.1", "0.2"])).toBe(toMicro("0.3"));
    expect(plain(sum(["0.1", "0.2"]))).toBe("0.3");
  });

  it("writes numbers for people", () => {
    expect(plain(2_380_500_000n)).toBe("2,380.5");
    expect(plain(0n)).toBe("0");
    expect(plain(-1_200n * ONE)).toBe("-1,200");
  });

  it("rounds to a step", () => {
    expect(roundUp(toMicro("279.2"), ONE)).toBe(280n * ONE);
    expect(roundUp(toMicro("280"), ONE)).toBe(280n * ONE);
    expect(roundDown(toMicro("0.45"), ONE / 10n)).toBe(toMicro("0.4"));
  });

  it("gives a bar its width", () => {
    expect(ratio(toMicro("2380"), toMicro("4500"))).toBeCloseTo(0.5288, 4);
    expect(ratio(1n, 0n)).toBe(0);
  });
});
