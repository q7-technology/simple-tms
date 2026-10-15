import { ApiError } from "../api/client";
import type { Carrier, Driver, Vehicle } from "../api/types";
import {
  amount, amountProblem, apiProblems, carrierNameProblem, checklistProblems, codeProblem, found,
  interval, intervalProblem, nameProblem, normalCode, normalRego, pinProblem, regoProblem,
} from "../lib/admin";

const van = (rego: string, active = true) => ({ id: 1, rego, type: "van", max_kg: "1200", max_m3: "8.5", active }) as Vehicle;
const kim: Driver = { id: 1, code: "kim", name: "Kim", active: true };
const carrier = (id: number, name: string, active = true): Carrier =>
  ({ id, name, account: null, pickup_time: null, adapter: "manual", active, created_at: "" });

describe("the admin forms' checks", () => {
  it("reads a rego as the API does, and says when it is taken", () => {
    expect(normalRego(" abc 123 ")).toBe("ABC123");
    expect(regoProblem("ABC123", [])).toBeNull();
    expect(regoProblem("", [])).toBe("Type the rego.");
    expect(regoProblem("ABC_123", [])).toBe("A rego is letters, digits and dashes, at most 12.");
    expect(regoProblem("A".repeat(13), [])).toBe("A rego is letters, digits and dashes, at most 12.");
    expect(regoProblem("TMS041", [van("TMS041")])).toBe("TMS041 is already on file.");
    expect(regoProblem("TMS041", [van("TMS041", false)])).toBe("TMS041 is already on file, deactivated: reactivate it instead.");
  });

  it("takes a load limit as a decimal, never a float", () => {
    expect(amount(" 1,200 ")).toBe("1200");
    expect(amount("8.50")).toBe("8.5");
    expect(amount("12.000")).toBe("12");
    expect(amountProblem("1200", "kg")).toBeNull();
    expect(amountProblem("0.125", "m³")).toBeNull();
    expect(amountProblem("", "kg")).toBe("Give the most it carries, in kg.");
    expect(amountProblem("about 2t", "kg")).toBe("A number, like 1200.");
    expect(amountProblem("-3", "m³")).toBe("A number, like 8.5.");
    expect(amountProblem("8.1234", "m³")).toBe("At most 3 decimal places.");
    expect(amountProblem("0.000", "kg")).toBe("More than 0.");
    expect(amountProblem("1234567890", "kg")).toBe("That is more than any truck carries.");
  });

  it("refuses a decimal comma rather than reading 8,5 as 85", () => {
    const comma = "Use a point for decimals, like 8.5: a comma only groups thousands, like 1,200.";
    expect(amountProblem("8,5", "m³")).toBe(comma);
    expect(amountProblem("1200,5", "kg")).toBe(comma);
    expect(amountProblem("12,50", "kg")).toBe(comma);
    expect(amountProblem("1,200", "kg")).toBeNull();
    expect(amountProblem("12,000.5", "kg")).toBeNull();
    expect(amount("12,000.5")).toBe("12000.5");
    expect(intervalProblem("1,5", 120, "months")).toBe("Whole months, or leave it blank.");
    expect(intervalProblem("15,000", 1_000_000, "km")).toBeNull();
  });

  it("leaves a service interval blank for none, else a whole number in range", () => {
    expect(interval("")).toBeNull();
    expect(interval("15,000")).toBe(15000);
    expect(intervalProblem("", 120, "months")).toBeNull();
    expect(intervalProblem("6", 120, "months")).toBeNull();
    expect(intervalProblem("0", 120, "months")).toBe("From 1 to 120 months, or leave it blank.");
    expect(intervalProblem("1000001", 1_000_000, "km")).toBe("From 1 to 1,000,000 km, or leave it blank.");
    expect(intervalProblem("6.5", 120, "months")).toBe("Whole months, or leave it blank.");
  });

  it("keeps a driver's code lower case, once, and a PIN 4 to 8 digits", () => {
    expect(normalCode(" KIM-07 ")).toBe("kim-07");
    expect(codeProblem("kim-07", [kim])).toBeNull();
    expect(codeProblem("", [])).toBe("Type the code the driver signs in with.");
    expect(codeProblem("kim 07", [])).toBe("Letters, digits, dots, dashes and underscores, at most 32.");
    expect(codeProblem("kim", [kim])).toBe("kim is already Kim's.");
    expect(codeProblem("kim", [{ ...kim, active: false }])).toBe("kim is already Kim's, deactivated: reactivate them instead.");
    expect(pinProblem("2468")).toBeNull();
    expect(pinProblem("12345678")).toBeNull();
    for (const bad of ["246", "123456789", "24 68", "abcd"]) expect(pinProblem(bad)).toBe("A PIN is 4 to 8 digits.");
    expect(nameProblem("  ", 120)).toBe("Type the name.");
    expect(nameProblem("x".repeat(121), 120)).toBe("At most 120 characters.");
  });

  it("holds a carrier's name one whatever its case, and never Own fleet", () => {
    const all = [carrier(1, "Ridgeline Express (sample)"), carrier(2, "Plains Parcels (sample)", false)];
    expect(carrierNameProblem("Valley Freight (sample)", all)).toBeNull();
    expect(carrierNameProblem(" ", all)).toBe("Type the carrier's name.");
    expect(carrierNameProblem("own FLEET", all)).toBe("Own fleet is what Simple WMS is told for your own trucks: give the carrier its own name.");
    expect(carrierNameProblem("RIDGELINE EXPRESS (SAMPLE)", all)).toBe("Ridgeline Express (sample) is already on file.");
    expect(carrierNameProblem("plains parcels (sample)", all)).toBe("Plains Parcels (sample) is already on file, retired: bring it back instead.");
    // renaming one to its own name in another case is fine
    expect(carrierNameProblem("RIDGELINE EXPRESS (SAMPLE)", all, 1)).toBeNull();
    expect(carrierNameProblem("x".repeat(65), all)).toBe("At most 64 characters.");
  });

  it("finds a checklist's blank and repeated lines, and a list too short or long", () => {
    const line = (label: string) => ({ label, critical: false });
    expect(checklistProblems([line("Tyres"), line("Lights")])).toEqual({ lines: [null, null], list: null });
    expect(checklistProblems([line("Tyres"), line(" "), line("tyres ")])).toEqual({
      lines: [null, "Type what to check, or take the line out.", "Line 1 says this already."], list: null });
    expect(checklistProblems([]).list).toBe("A checklist needs at least one line.");
    expect(checklistProblems(Array.from({ length: 31 }, (_, i) => line(`Item ${i}`))).list).toBe("At most 30 lines.");
    expect(checklistProblems([line("x".repeat(81))]).lines).toEqual(["At most 80 characters."]);
  });

  it("reads the API's field errors, and only those", () => {
    const e = new ApiError(422, { errors: [{ field: "rego", message: "vehicle TMS041 already exists" }] });
    expect(apiProblems(e)).toEqual({ rego: "vehicle TMS041 already exists" });
    expect(apiProblems(new ApiError(409, { code: "on_a_run", detail: "TMS041 is on R-0001" }))).toBeNull();
    expect(apiProblems(new Error("x"))).toBeNull();
    expect(found({ rego: null, max_kg: "More than 0." })).toEqual({ max_kg: "More than 0." });
  });
});
