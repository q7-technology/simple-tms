import { afterAdding, loadOf } from "../lib/load";

const c = (weight_kg: string, volume_m3: string) => ({ weight_kg, volume_m3 });

describe("how full a run is", () => {
  it("is fine well under both limits", () => {
    const l = loadOf([c("2000", "10"), c("380", "1.2")], "4500", "22");
    expect(l.state).toBe("ok");
    expect(l.message).toBeNull();
    expect(l.weight.fill).toBeCloseTo(0.5289, 3);
  });

  it("is nearly full from 90 per cent of either limit", () => {
    const l = loadOf([c("1120", "8.6")], "1200", "9");
    expect(l.state).toBe("near");
    expect(l.weight.state).toBe("near");
    expect(l.volume.state).toBe("near");
    expect(l.message).toBe("80 kg and 0.4 m³ left. Small drops only.");
  });

  it("is over past either limit, says by how much, and only that bar goes red", () => {
    const l = loadOf([c("1640", "8"), c("3140", "11.4")], "4500", "22");
    expect(l.state).toBe("over");
    expect(l.weight.state).toBe("over");
    expect(l.volume.state).toBe("ok");
    expect(l.weight.fill).toBe(1);
    expect(l.headline).toBe("280 kg too heavy.");
    expect(l.message).toBe("Move a stop to another run before printing the run sheet.");
  });

  it("names both when both are over, rounding what is over up", () => {
    const l = loadOf([c("4500.2", "22.01")], "4500", "22");
    expect(l.headline).toBe("1 kg too heavy and 0.1 m³ too big.");
  });

  it("says what one more drop would do", () => {
    expect(afterAdding([c("1000", "1")], c("100", "1"), "1200", "9")).toBe("near");
    expect(afterAdding([c("1000", "1")], c("300", "1"), "1200", "9")).toBe("over");
    expect(afterAdding([], c("100", "1"), "1200", "9")).toBe("ok");
  });
});
