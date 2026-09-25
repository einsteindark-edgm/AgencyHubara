import { describe, expect, it } from "vitest";

import { sparklineGeometry } from "./sparkline";

const DIMS = { width: 100, height: 50, padX: 0, padY: 0 };
const w = (rate: number | null, applicable = 4) => ({
  week: "w",
  applicable: rate === null ? 0 : applicable,
  passed: rate === null ? 0 : Math.round(rate * applicable),
  rate,
});

describe("sparklineGeometry", () => {
  it("mapea tasas a coordenadas con dominio [0.5, 1] por defecto", () => {
    const g = sparklineGeometry([w(0.5), w(1)], DIMS);
    expect(g.domainMin).toBe(0.5);
    expect(g.points).toEqual([
      { x: 0, y: 50, rate: 0.5, week: "w" },
      { x: 100, y: 0, rate: 1, week: "w" },
    ]);
    expect(g.refY).toBeCloseTo(10); // 90 % de cumplimiento
    expect(g.endpoint).toEqual({ x: 100, y: 0, rate: 1, week: "w" });
  });

  it("baja el piso del dominio si una tasa cae bajo 50 %", () => {
    expect(sparklineGeometry([w(0.25), w(1)], DIMS).domainMin).toBe(0.2);
  });

  it("corta la línea en semanas sin datos y conserva la posición temporal", () => {
    const g = sparklineGeometry([w(1), w(null), w(1)], DIMS);
    expect(g.segments).toHaveLength(2);
    expect(g.points.map((p) => p.x)).toEqual([0, 100]);
    expect(g.linePath.match(/M/g)).toHaveLength(2);
  });

  it("sin datos no hay línea ni punto final", () => {
    const g = sparklineGeometry([w(null), w(null)], DIMS);
    expect(g.points).toEqual([]);
    expect(g.linePath).toBe("");
    expect(g.areaPath).toBe("");
    expect(g.endpoint).toBeNull();
  });

  it("una sola semana con dato se centra", () => {
    const g = sparklineGeometry([w(0.75)], DIMS);
    expect(g.points[0].x).toBe(50);
  });
});

describe("formatWeekLabel", () => {
  it("abrevia la semana en español", async () => {
    const { formatWeekLabel } = await import("./sparkline");
    expect(formatWeekLabel("2026-07-27")).toBe("27 jul");
    expect(formatWeekLabel("2026-09-07")).toBe("7 sep");
    expect(formatWeekLabel("raro")).toBe("raro");
  });
});
