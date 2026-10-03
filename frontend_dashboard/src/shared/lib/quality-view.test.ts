import { describe, expect, it } from "vitest";

import {
  QUALITY_LEVEL_ORDER,
  QUALITY_STAGE_ORDER,
  QUALITY_VERDICT_ORDER,
  clipText,
  formatDuration,
  matrixCellStatus,
  paretoWithCumulative,
  qualityLevelColor,
  qualityLevelLabel,
  qualityStageLabel,
  qualityStatus,
  qualityStatusColor,
  qualityStatusGlyph,
  qualityStatusLabel,
  qualityVerdictColor,
  qualityVerdictLabel,
  toQualityFunnel,
  trendHasFailures,
  verdictCountsTotal,
  weeklyDelta,
  type MatrixRowView,
} from "./quality-view";

describe("vocabulario de calidad", () => {
  it("veredictos en orden Falla → Alerta → Pasa → Sin datos, con rótulo y token", () => {
    expect(QUALITY_VERDICT_ORDER.map(qualityVerdictLabel)).toEqual(["Falla", "Alerta", "Pasa", "Sin datos"]);
    expect(qualityVerdictColor("FALLA")).toBe("var(--color-red)");
    expect(QUALITY_VERDICT_ORDER.every((v) => qualityVerdictColor(v).startsWith("var(--color-"))).toBe(true);
  });

  it("niveles y estados: la falla toma el color de su nivel; nunca solo color (glifo + texto)", () => {
    expect(QUALITY_LEVEL_ORDER.map(qualityLevelLabel)).toEqual(["crítico", "mayor", "menor"]);
    expect(qualityLevelColor("mayor")).toBe(qualityStatusColor("mayor"));
    expect(qualityStatus("falla", "critico")).toBe("critico");
    expect(qualityStatus("pasa", "critico")).toBe("pasa");
    expect(qualityStatus(undefined, "menor")).toBe("sin_resultado");
    expect(qualityStatusGlyph("critico")).toBe("✗");
    expect(qualityStatusLabel("sin_resultado")).toBe("sin evaluar");
  });

  it("formatos: duración legible y recorte que cuenta la elipsis", () => {
    expect(formatDuration(19_520_000)).toBe("5 h 25 min");
    expect(formatDuration(45_000)).toBe("45 s");
    expect(clipText("abcdef", 4)).toBe("abc…");
    expect(clipText("abc", 4)).toBe("abc");
  });
});

describe("matemática de las gráficas", () => {
  it("Pareto: ordena por fallos, acumula y conserva los campos propios", () => {
    const rows = paretoWithCumulative([
      { check_id: "B", name: "", level: "menor", failures: 1, extra: "b" },
      { check_id: "A", name: "", level: "critico", failures: 3, extra: "a" },
      { check_id: "C", name: "", level: "mayor", failures: 0, extra: "c" },
    ]);
    expect(rows.map((r) => [r.check_id, r.extra, r.cumulative, r.share])).toEqual([
      ["A", "a", 3, 0.75],
      ["B", "b", 4, 1],
    ]);
  });

  it("tendencia: delta contra la semana anterior con dato y detección de fallos", () => {
    const weeks = [
      { week: "a", applicable: 4, passed: 2, rate: 0.5 },
      { week: "b", applicable: 0, passed: 0, rate: null },
      { week: "c", applicable: 4, passed: 4, rate: 1 },
    ];
    expect(weeklyDelta(weeks)).toEqual({ last: 1, previous: 0.5, delta: 0.5 });
    expect(trendHasFailures({ weeks })).toBe(true);
    expect(trendHasFailures({ weeks: weeks.slice(2) })).toBe(false);
  });

  it("embudo y matriz: total por veredicto y estado de celda", () => {
    expect(verdictCountsTotal({ FALLA: 1, ALERTA: 2, PASA: 3, SIN_DATOS: 4 })).toBe(10);
    const row: MatrixRowView = { key: "k", verdict: "FALLA", label: "", title: "", meta: "", checks: { X: "falla", Y: "no_aplica" } };
    expect(matrixCellStatus(row, { id: "X", name: "", level: "mayor" })).toBe("mayor");
    expect(matrixCellStatus(row, { id: "Y", name: "", level: "mayor" })).toBe("no_aplica");
    expect(matrixCellStatus(row, { id: "Z", name: "", level: "mayor" })).toBe("sin_resultado");
  });
});

describe("sin señal (modo turno del laboratorio)", () => {
  it("es su propio estado, distinto de desconocido y de sin evaluar", () => {
    const s = qualityStatus("sin_senal", "critico");
    expect(s).toBe("sin_senal");
    expect(qualityStatusLabel(s)).toBe("sin señal");
    expect(qualityStatusGlyph(s)).toBe("∅");
    expect(qualityStatusColor(s)).not.toBe(qualityStatusColor("sin_resultado"));
  });
});

describe("etapas del guion de ventas", () => {
  it("rotula, colorea y ordena el embudo", () => {
    const view = toQualityFunnel([
      { stage: "cierre", FALLA: 1, ALERTA: 0, PASA: 0, SIN_DATOS: 0 },
      { stage: "datos_envio", FALLA: 0, ALERTA: 1, PASA: 0, SIN_DATOS: 0 },
      { stage: "rara", FALLA: 0, ALERTA: 0, PASA: 1, SIN_DATOS: 0 },
    ]);
    expect(view.map((r) => r.label)).toEqual(["datos de envío", "cierre", "rara"]);
    expect(view[0].color).toBe("var(--color-yellow)");
    expect(qualityStageLabel(null)).toBe("sin etapa");
    expect(QUALITY_STAGE_ORDER[0]).toBe("descubrimiento");
  });
});
