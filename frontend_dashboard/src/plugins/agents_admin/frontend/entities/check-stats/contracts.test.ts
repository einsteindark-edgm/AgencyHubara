import { describe, expect, it } from "vitest";

import statsFixture from "./fixtures/check-stats.json";
import { checkStatsSchema } from "./contracts";

describe("checkStatsSchema", () => {
  it("parsea veredictos, pareto, tendencia semanal y embudo", () => {
    const s = checkStatsSchema.parse(statsFixture);
    expect(s.days).toBe(56);
    expect(s.verdicts.FALLA).toBe(9);
    expect(s.pareto[0]).toMatchObject({ check_id: "DES-01", level: "mayor", failures: 14 });
    const tag01 = s.trend.find((t) => t.check_id === "TAG-01")!;
    expect(tag01.weeks[1]).toEqual({ week: "2026-08-03", applicable: 0, passed: 0, rate: null });
    expect(s.funnel.find((f) => f.stage === "confirmacion")?.FALLA).toBe(4);
  });

  it("tolera secciones ausentes y niveles nuevos", () => {
    const s = checkStatsSchema.parse({
      pareto: [{ check_id: "X-01", level: "catastrofico", failures: 2 }],
    });
    expect(s.pareto[0].level).toBe("menor");
    expect(s.trend).toEqual([]);
    expect(s.verdicts).toEqual({ FALLA: 0, ALERTA: 0, PASA: 0, SIN_DATOS: 0 });
  });
});
