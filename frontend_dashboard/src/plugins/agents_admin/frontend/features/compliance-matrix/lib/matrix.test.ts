import { describe, expect, it } from "vitest";

import checksFixture from "@plugins/agents_admin/frontend/entities/scorecard/fixtures/checks.json";
import listFixture from "@plugins/agents_admin/frontend/entities/scorecard/fixtures/scorecards.json";
import { checkRegistrySchema } from "@plugins/agents_admin/frontend/entities/scorecard/contracts";
import { scorecardListSchema } from "@plugins/agents_admin/frontend/entities/scorecard/contracts";

import {
  cellStatus,
  filterScorecards,
  finalStageOptions,
  matrixColumns,
} from "./matrix";

const registry = checkRegistrySchema.parse(checksFixture);
const rows = scorecardListSchema.parse(listFixture).scorecards;
const ALL = { verdict: "todos", stage: null, checkId: null } as const;

describe("filterScorecards", () => {
  it("sin filtros devuelve todo en el orden del backend", () => {
    expect(filterScorecards(rows, ALL)).toHaveLength(7);
  });

  it("filtra por veredicto", () => {
    const r = filterScorecards(rows, { ...ALL, verdict: "ALERTA" });
    expect(r.map((x) => x.session_id)).toEqual(["wa_570000000003", "wa_570000000004"]);
  });

  it("filtra por etapa final", () => {
    const r = filterScorecards(rows, { ...ALL, stage: "cierre" });
    expect(r.map((x) => x.episode_id)).toEqual(["ep_012", "ep_002"]);
  });

  it("filtra por episodios que fallan un check (desde el Pareto)", () => {
    const r = filterScorecards(rows, { ...ALL, checkId: "VAR-07" });
    expect(r.map((x) => x.session_id)).toEqual(["wa_570000000003"]);
  });
});

describe("matrixColumns", () => {
  it("agrupa todos los checks del registro por etapa, en orden", () => {
    const groups = matrixColumns(registry, rows, { onlyFailing: false });
    expect(groups.map((g) => g.stage)).toEqual(registry.stages);
    expect(groups[0].label).toBe("descubrimiento");
    expect(groups.reduce((n, g) => n + g.checks.length, 0)).toBe(registry.checks.length);
  });

  it("con 'solo con fallas' deja los checks que fallan en alguna fila visible", () => {
    const groups = matrixColumns(registry, rows, { onlyFailing: true });
    const ids = groups.flatMap((g) => g.checks.map((c) => c.id));
    expect(ids).toEqual(
      expect.arrayContaining(["VAR-01", "CON-01", "TAG-01", "CIE-03", "APE-02"]),
    );
    expect(ids).not.toContain("APE-01");
    expect(groups.every((g) => g.checks.length > 0)).toBe(true);
  });
});

describe("celdas y filas", () => {
  it("la celda combina veredicto y nivel; sin resultado = sin evaluar", () => {
    const con01 = registry.checks.find((c) => c.id === "CON-01")!;
    const des04 = registry.checks.find((c) => c.id === "DES-04")!;
    expect(cellStatus(rows[0], con01)).toBe("critico");
    expect(cellStatus(rows[0], des04)).toBe("sin_resultado");
    expect(cellStatus(rows[4], des04)).toBe("pasa");
  });

  it("opciones de etapa final en orden del guion, sin nulos", () => {
    expect(finalStageOptions(rows)).toEqual([
      "descubrimiento",
      "variantes",
      "confirmacion",
      "datos_envio",
      "cierre",
    ]);
  });
});
