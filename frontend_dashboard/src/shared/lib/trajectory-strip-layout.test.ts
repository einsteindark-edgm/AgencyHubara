import { describe, expect, it } from "vitest";

import stripModelFixture from "./fixtures/strip-model.json";
import type { StripModel } from "./trajectory-strip";
import {
  COL_W,
  DOTS_PER_ROW,
  DOT_ROW_H,
  GAP_W,
  LABEL_W,
  PILL_ROW_H,
  dotOffset,
  splitColumnChecks,
  stripLayout,
} from "./trajectory-strip-layout";

/** Modelo de vista de un episodio real de 10 turnos (el que arma la entity scorecard de agents_admin). */
const model = stripModelFixture as StripModel;

describe("splitColumnChecks", () => {
  const last = model.columns[9];

  it("dibuja fallas y checks anclados; resume los sin turno por estado", () => {
    const { dots, collapsed } = splitColumnChecks(last, null);
    expect(dots.map((d) => d.checkId)).toEqual(["TAG-01", "TAG-02", "GHO-02", "TAG-05"]);
    const unknown = collapsed.find((g) => g.status === "desconocido")!;
    expect(unknown.checks.map((c) => c.checkId)).toEqual(["EST-08"]);
    expect(collapsed.find((g) => g.status === "pasa")!.checks.map((c) => c.checkId)).toEqual(
      expect.arrayContaining(["GHO-01", "TAG-01b", "TAG-06"]),
    );
  });

  it("el check seleccionado siempre se dibuja como punto", () => {
    const { dots, collapsed } = splitColumnChecks(last, "EST-03");
    expect(dots.map((d) => d.checkId)).toContain("EST-03");
    expect(collapsed.flatMap((g) => g.checks).map((c) => c.checkId)).not.toContain("EST-03");
  });
});

describe("stripLayout", () => {
  const layout = stripLayout(model);

  it("coloca una columna por turno y abre un hueco antes de un salto largo", () => {
    expect(layout.colX).toHaveLength(10);
    expect(layout.colX[0]).toBe(LABEL_W + COL_W / 2);
    expect(layout.colX[1] - layout.colX[0]).toBe(COL_W);
    // turno 9 viene tras 5 h: la columna se corre un hueco extra.
    expect(layout.colX[8] - layout.colX[7]).toBe(COL_W + GAP_W);
    expect(layout.width).toBeGreaterThan(layout.colX[9] + COL_W / 2);
  });

  it("apila los carriles en orden y dimensiona por el turno más cargado", () => {
    const { cliente, bot, tools, checks } = layout.lanes;
    expect(bot.y).toBe(cliente.y + cliente.h);
    expect(tools.h).toBeGreaterThan(layout.lanes.guardas.h);
    // Último turno: 4 puntos (fallas + anclados) + una fila de resumen de los sin turno.
    expect(checks.h).toBeGreaterThanOrEqual(DOT_ROW_H + PILL_ROW_H);
    expect(layout.height).toBeGreaterThan(checks.y + checks.h);
  });

  it("reparte los puntos de checks en filas centradas", () => {
    expect(dotOffset(0, 1)).toEqual({ dx: 0, row: 0 });
    const a = dotOffset(0, 2);
    const b = dotOffset(1, 2);
    expect(a.dx).toBe(-b.dx);
    expect(dotOffset(DOTS_PER_ROW, DOTS_PER_ROW + 1).row).toBe(1);
  });

  it("un modelo vacío tiene dimensiones mínimas", () => {
    const empty = stripLayout({ columns: [], bands: [], firstFailure: null, firstCritical: null });
    expect(empty.colX).toEqual([]);
    expect(empty.width).toBeGreaterThan(0);
  });
});
