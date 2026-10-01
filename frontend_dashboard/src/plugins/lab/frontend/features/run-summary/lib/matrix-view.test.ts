import { describe, expect, it } from "vitest";

import type { CheckSpec, LabScorecardRow } from "@plugins/lab/frontend/entities/lab-run";

import { filterRows, finalStages, matrixGroups, toRowView } from "./matrix-view";

/** Matriz episodios × checks del Resumen (la misma vista que Calidad LLM). */

function spec(id: string, stage: string, level: CheckSpec["level"] = "mayor"): CheckSpec {
  return { id, name: `Nombre ${id}`, level, kind: "code", applies: "", rule: "", family_label: "", stage };
}

function row(sid: string, episode: string, verdict: LabScorecardRow["verdict"], stage: string | null, checks: LabScorecardRow["checks"]): LabScorecardRow {
  return { session_id: sid, episode_id: episode, verdict, stage_final: stage, episode_date: "2026-09-30", checks };
}

const CATALOG = [spec("VAR-01", "variantes"), spec("APE-01", "descubrimiento"), spec("ENV-01", "datos_envio"), spec("X-01", "")];
const ROWS = [
  row("wa_573001234567", "ep_001", "FALLA", "variantes", { "APE-01": "pasa", "VAR-01": "falla" }),
  row("wa_573007654321", "ep_002", "PASA", "descubrimiento", { "APE-01": "pasa" }),
];

describe("matriz del Resumen", () => {
  it("agrupa las columnas por etapa en el orden del guion; un check sin etapa no tiene columna", () => {
    const groups = matrixGroups(CATALOG, ROWS, { onlyFailing: false });

    expect(groups.map((g) => [g.key, g.label, g.columns.map((c) => c.id)])).toEqual([
      ["descubrimiento", "descubrimiento", ["APE-01"]],
      ["variantes", "variantes", ["VAR-01"]],
      ["datos_envio", "datos de envío", ["ENV-01"]],
    ]);
    expect(groups[0].columns[0]).toEqual({ id: "APE-01", name: "Nombre APE-01", level: "mayor" });
  });

  it("«Solo checks con fallas» deja los checks que fallan en alguna fila visible", () => {
    expect(matrixGroups(CATALOG, ROWS, { onlyFailing: true }).map((g) => g.columns.map((c) => c.id))).toEqual([["VAR-01"]]);
  });

  it("filtra por veredicto y por etapa final", () => {
    expect(filterRows(ROWS, { verdict: "FALLA", stage: null }).map((r) => r.episode_id)).toEqual(["ep_001"]);
    expect(filterRows(ROWS, { verdict: "todos", stage: "descubrimiento" }).map((r) => r.episode_id)).toEqual(["ep_002"]);
    expect(filterRows(ROWS, { verdict: "todos", stage: null })).toHaveLength(2);
  });

  it("las etapas finales van en el orden del guion", () => {
    expect(finalStages(ROWS)).toEqual(["descubrimiento", "variantes"]);
  });

  it("la fila tapa el número del cliente (también en el tooltip)", () => {
    const view = toRowView(ROWS[0]);

    expect(view).toMatchObject({ verdict: "FALLA", label: "Cliente ···4567 · ep_001", meta: "2026-09-30 · terminó en variantes" });
    expect(`${view.label} ${view.meta} ${view.title}`).not.toContain("3001234567");
    expect(view.checks).toEqual({ "APE-01": "pasa", "VAR-01": "falla" });
  });
});
