import { describe, expect, it } from "vitest";

import statsFixture from "@plugins/agents_admin/frontend/entities/check-stats/fixtures/check-stats.json";
import { checkStatsSchema } from "@plugins/agents_admin/frontend/entities/check-stats/contracts";
import { stageColor } from "@plugins/agents_admin/frontend/entities/scorecard";

import { toFunnelView } from "./funnel-view";

const stats = checkStatsSchema.parse(statsFixture);

describe("toFunnelView", () => {
  it("ordena por etapa del guion y resuelve rótulo y color de cada etapa", () => {
    const rows = toFunnelView([...stats.funnel].reverse());
    expect(rows.map((r) => r.label)).toEqual([
      "descubrimiento",
      "variantes",
      "confirmación",
      "datos de envío",
      "cierre",
    ]);
    expect(rows.map((r) => r.color)).toEqual(rows.map((r) => stageColor(r.stage)));
  });

  it("conserva los conteos por veredicto de cada etapa", () => {
    const [first] = toFunnelView(stats.funnel);
    const src = stats.funnel.find((r) => r.stage === first.stage)!;
    expect(first).toMatchObject({
      FALLA: src.FALLA,
      ALERTA: src.ALERTA,
      PASA: src.PASA,
      SIN_DATOS: src.SIN_DATOS,
    });
  });

  it("sin filas devuelve vacío", () => {
    expect(toFunnelView([])).toEqual([]);
  });
});
