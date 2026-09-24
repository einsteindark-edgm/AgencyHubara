import { describe, expect, it } from "vitest";

import checksFixture from "@plugins/agents_admin/frontend/entities/scorecard/fixtures/checks.json";
import listFixture from "@plugins/agents_admin/frontend/entities/scorecard/fixtures/scorecards.json";
import { checkRegistrySchema } from "@plugins/agents_admin/frontend/entities/scorecard/contracts";
import { scorecardListSchema } from "@plugins/agents_admin/frontend/entities/scorecard/contracts";

import { matrixCellStatus } from "@/shared/lib";
import { stageColor } from "@plugins/agents_admin/frontend/entities/scorecard";

import {
  cellStatus,
  filterScorecards,
  finalStageOptions,
  matrixColumns,
  toMatrixGroupsView,
  toMatrixRowView,
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
    expect(r.map((x) => x.session_id)).toEqual(["wa_100000000003", "wa_100000000004"]);
  });

  it("filtra por etapa final", () => {
    const r = filterScorecards(rows, { ...ALL, stage: "cierre" });
    expect(r.map((x) => x.episode_id)).toEqual(["ep_012", "ep_002"]);
  });

  it("filtra por episodios que fallan un check (desde el Pareto)", () => {
    const r = filterScorecards(rows, { ...ALL, checkId: "VAR-07" });
    expect(r.map((x) => x.session_id)).toEqual(["wa_100000000003"]);
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

describe("mapeo a la vista genérica de la matriz", () => {
  it("cada grupo de etapa lleva clave, rótulo, color y columnas con nivel", () => {
    const groups = matrixColumns(registry, rows, { onlyFailing: false });
    const view = toMatrixGroupsView(groups);
    expect(view.map((g) => g.key)).toEqual(groups.map((g) => g.stage));
    expect(view[0]).toMatchObject({ label: "descubrimiento", color: stageColor("descubrimiento") });
    const con01 = registry.checks.find((c) => c.id === "CON-01")!;
    const col = view.flatMap((g) => g.columns).find((c) => c.id === "CON-01")!;
    expect(col).toEqual({ id: "CON-01", name: con01.name, level: con01.level });
  });

  it("cada fila lleva clave única, rótulo del episodio, título, meta y veredictos por check", () => {
    const view = toMatrixRowView(rows[0]);
    expect(view.key).toBe(`${rows[0].session_id}::${rows[0].episode_id}`);
    expect(view.verdict).toBe(rows[0].verdict);
    expect(view.label).toMatch(/ · ep_007$/);
    expect(view.title).toBe(`${rows[0].session_id} · ${rows[0].episode_id}`);
    expect(view.meta.startsWith(rows[0].episode_date ?? rows[0].date)).toBe(true);
    expect(view.checks).toBe(rows[0].checks);
    expect(new Set(rows.map((r) => toMatrixRowView(r).key)).size).toBe(rows.length);
  });

  it("la meta suma etiqueta de cierre y marca el legado", () => {
    const base = rows[0];
    const meta = toMatrixRowView({
      ...base,
      episode_date: "2026-09-10",
      closing_tag: "COMPRA_EXITOSA",
      fidelity: "legacy",
    }).meta;
    expect(meta).toBe("2026-09-10 · COMPRA_EXITOSA · legado");
    expect(toMatrixRowView({ ...base, episode_date: null, date: "2026-09-01", closing_tag: null, fidelity: "trace" }).meta).toBe(
      "2026-09-01",
    );
  });

  it("la celda de la vista coincide con la del dominio", () => {
    const groups = toMatrixGroupsView(matrixColumns(registry, rows, { onlyFailing: false }));
    for (const r of rows) {
      const view = toMatrixRowView(r);
      for (const col of groups.flatMap((g) => g.columns)) {
        const def = registry.checks.find((c) => c.id === col.id)!;
        expect(matrixCellStatus(view, col)).toBe(cellStatus(r, def));
      }
    }
  });
});
