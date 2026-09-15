import { describe, expect, it } from "vitest";

import statsFixture from "./fixtures/check-stats.json";
import { checkStatsSchema } from "./contracts";
import { funnelTotal, hasFailures, paretoWithCumulative, sortFunnel, weeklyDelta } from "./lib";

const stats = checkStatsSchema.parse(statsFixture);

describe("paretoWithCumulative", () => {
  it("ordena por fallos y acumula la participación", () => {
    const rows = paretoWithCumulative([
      { check_id: "B", name: "", level: "menor", failures: 1 },
      { check_id: "A", name: "", level: "critico", failures: 3 },
    ]);
    expect(rows.map((r) => r.check_id)).toEqual(["A", "B"]);
    expect(rows[0].cumulative).toBe(3);
    expect(rows[0].share).toBeCloseTo(0.75);
    expect(rows[1].share).toBe(1);
  });

  it("devuelve vacío sin fallos", () => {
    expect(paretoWithCumulative([])).toEqual([]);
  });
});

describe("weeklyDelta", () => {
  it("compara la última semana con dato contra la anterior con dato", () => {
    const con01 = stats.trend.find((t) => t.check_id === "CON-01")!;
    const d = weeklyDelta(con01.weeks);
    expect(d.last).toBe(1);
    expect(d.previous).toBeCloseTo(0.625);
    expect(d.delta).toBeCloseTo(0.375);
  });

  it("salta semanas sin episodios aplicables", () => {
    const d = weeklyDelta([
      { week: "a", applicable: 4, passed: 2, rate: 0.5 },
      { week: "b", applicable: 0, passed: 0, rate: null },
      { week: "c", applicable: 4, passed: 4, rate: 1 },
    ]);
    expect(d).toEqual({ last: 1, previous: 0.5, delta: 0.5 });
  });

  it("sin dos puntos no hay delta", () => {
    expect(weeklyDelta([{ week: "a", applicable: 1, passed: 1, rate: 1 }])).toEqual({
      last: 1,
      previous: null,
      delta: null,
    });
  });
});

describe("hasFailures", () => {
  it("detecta checks con al menos un fallo en la ventana", () => {
    expect(hasFailures(stats.trend.find((t) => t.check_id === "DES-01")!)).toBe(true);
    expect(hasFailures(stats.trend.find((t) => t.check_id === "APE-01")!)).toBe(false);
  });
});

describe("embudo", () => {
  it("suma los veredictos de una etapa y ordena por etapa del guion", () => {
    const rows = sortFunnel([
      { stage: "cierre", FALLA: 1, ALERTA: 0, PASA: 2, SIN_DATOS: 0 },
      { stage: "descubrimiento", FALLA: 0, ALERTA: 1, PASA: 1, SIN_DATOS: 1 },
    ]);
    expect(rows.map((r) => r.stage)).toEqual(["descubrimiento", "cierre"]);
    expect(funnelTotal(rows[0])).toBe(3);
  });
});
