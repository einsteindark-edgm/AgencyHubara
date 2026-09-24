import { describe, expect, it } from "vitest";

import { conclusion, intervalText, pct, points, topChecks } from "./summary-view";

describe("textos del Resumen", () => {
  it("porcentajes y puntos porcentuales con coma decimal", () => {
    expect(pct(0.9412)).toBe("94 %");
    expect(pct(null)).toBe("—");
    expect(points(0.1)).toBe("+10 pp");
    expect(points(-0.055)).toBe("−5,5 pp");
    expect(points(0)).toBe("0 pp");
  });

  it("el intervalo de 95 %", () => {
    expect(intervalText({ delta: 0.1, low: -0.05, high: 0.25, conclusive: false, sessions: 80 })).toBe("IC 95 %: −5 a +25 pp · 80 conversaciones");
    expect(intervalText({ delta: null, low: null, high: null, conclusive: false, sessions: 0 })).toBe("sin conversaciones en común");
  });

  it("la conclusión dice quién gana solo si el intervalo no cruza el cero", () => {
    const up = { delta: 0.12, low: 0.04, high: 0.2, conclusive: true, sessions: 80 };
    const down = { delta: -0.08, low: -0.15, high: -0.01, conclusive: true, sessions: 80 };
    const unsure = { delta: 0.05, low: -0.03, high: 0.12, conclusive: false, sessions: 80 };

    expect(conclusion("A1", "B", up)).toEqual({ tone: "ok", text: "Nuevo + Jev pasa 12 pp más episodios que Actual simulado" });
    expect(conclusion("A1", "C", down)).toEqual({ tone: "bad", text: "Nuevo + OpenAI pasa 8 pp menos episodios que Actual simulado" });
    expect(conclusion("A1", "B", unsure)).toEqual({ tone: "neutral", text: "Aún no concluyente: el intervalo cruza el cero" });
  });

  it("con pocas conversaciones en común no hay conclusión aunque el intervalo no cruce el cero", () => {
    const few = { delta: 0.3, low: 0.1, high: 0.5, conclusive: false, sessions: 9 };

    expect(conclusion("A1", "B", few)).toEqual({
      tone: "neutral",
      text: "Aún no concluyente: 9 conversaciones en común son pocas (hacen falta 15)",
    });
  });

  it("los checks que más se movieron primero, con los concluyentes antes", () => {
    const rows = [
      { check_id: "A", delta: 0.02, low: -0.1, high: 0.1, conclusive: false, sessions: 10 },
      { check_id: "B", delta: -0.3, low: -0.4, high: -0.2, conclusive: true, sessions: 10 },
      { check_id: "C", delta: 0.2, low: 0.1, high: 0.3, conclusive: true, sessions: 10 },
      { check_id: "D", delta: null, low: null, high: null, conclusive: false, sessions: 0 },
    ];
    expect(topChecks(rows, 3).map((r) => r.check_id)).toEqual(["B", "C", "A"]);
  });
});
