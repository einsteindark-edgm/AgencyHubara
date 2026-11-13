import { describe, expect, it } from "vitest";

import { presetRange, validateRange } from "./range";

const TODAY = "2026-11-04";

describe("presetRange", () => {
  it("last two weeks ends today and spans 14 days", () => {
    expect(presetRange("14d", TODAY)).toEqual({ start: "2026-10-22", end: "2026-11-04" });
  });

  it("this month starts on day one", () => {
    expect(presetRange("month", TODAY)).toEqual({ start: "2026-11-01", end: "2026-11-04" });
  });

  it("last month is the whole previous calendar month", () => {
    expect(presetRange("prev-month", TODAY)).toEqual({ start: "2026-10-01", end: "2026-10-31" });
    expect(presetRange("prev-month", "2026-01-15")).toEqual({ start: "2025-12-01", end: "2025-12-31" });
  });
});

describe("validateRange", () => {
  it("accepts a valid range", () => {
    expect(validateRange({ start: "2026-10-22", end: "2026-11-04" }, TODAY, 92)).toBeNull();
  });

  it("rejects start after end, a future end and a too long range, in Spanish", () => {
    expect(validateRange({ start: "2026-11-04", end: "2026-11-01" }, TODAY, 92)).toMatch(/inicio/);
    expect(validateRange({ start: "2026-11-01", end: "2026-11-05" }, TODAY, 92)).toMatch(/futur/);
    expect(validateRange({ start: "2026-07-01", end: "2026-11-04" }, TODAY, 92)).toMatch(/92 días/);
    expect(validateRange({ start: "", end: "2026-11-04" }, TODAY, 92)).toMatch(/fecha/);
  });
});
