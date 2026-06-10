/**
 * Tests de la lógica pura de selección de ventana (ads-campaign):
 *  - `selectionToParams` traduce preset → `days` y rango custom → `from`/`to`.
 *  - normaliza el orden del rango custom (from > to se intercambia).
 *  - `rangeDays` mapea cada preset a sus días (total → null).
 */

import { describe, expect, it } from "vitest";

import {
  ADS_DATE_RANGES,
  DEFAULT_ADS_SELECTION,
  rangeDays,
  selectionToParams,
} from "./model";

describe("selectionToParams — preset", () => {
  it("mapea un preset a `days` y deja from/to en null", () => {
    expect(selectionToParams({ kind: "preset", preset: "30d" })).toEqual({
      days: 30,
      from: null,
      to: null,
    });
  });

  it("mapea `total` a days null (sin límite inferior)", () => {
    expect(selectionToParams({ kind: "preset", preset: "total" })).toEqual({
      days: null,
      from: null,
      to: null,
    });
  });
});

describe("selectionToParams — custom", () => {
  it("mapea un rango custom a from/to y deja days en null", () => {
    expect(
      selectionToParams({
        kind: "custom",
        from: "2026-05-01",
        to: "2026-05-31",
      }),
    ).toEqual({ days: null, from: "2026-05-01", to: "2026-05-31" });
  });

  it("normaliza el orden cuando from > to (los intercambia)", () => {
    expect(
      selectionToParams({
        kind: "custom",
        from: "2026-05-31",
        to: "2026-05-01",
      }),
    ).toEqual({ days: null, from: "2026-05-01", to: "2026-05-31" });
  });
});

describe("rangeDays / DEFAULT_ADS_SELECTION", () => {
  it("mapea cada preset a sus días (total → null)", () => {
    expect(rangeDays("7d")).toBe(7);
    expect(rangeDays("30d")).toBe(30);
    expect(rangeDays("90d")).toBe(90);
    expect(rangeDays("total")).toBeNull();
  });

  it("el default es un preset acotado y existe en el segmented control", () => {
    expect(DEFAULT_ADS_SELECTION).toEqual({ kind: "preset", preset: "30d" });
    expect(ADS_DATE_RANGES.some((r) => r.key === "30d")).toBe(true);
  });
});
