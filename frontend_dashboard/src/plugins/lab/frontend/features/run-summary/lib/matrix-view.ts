/**
 * Matriz episodios × checks del Resumen: la misma vista que Calidad LLM
 * (`ComplianceMatrixTable` de `@/shared/ui`), con las filas de un bot de la
 * corrida. Lógica pura: filtros, columnas agrupadas por etapa del guion y la
 * fila con el número del cliente tapado.
 */

import {
  QUALITY_STAGE_ORDER,
  qualityStageColor,
  qualityStageLabel,
  qualityStageRank,
  type MatrixGroupView,
  type MatrixRowView,
} from "@/shared/lib";
import { customerLabel, type CheckSpec, type LabScorecardRow } from "@plugins/lab/frontend/entities/lab-run";

export type VerdictFilter = "todos" | "FALLA" | "ALERTA" | "PASA";

export function filterRows(rows: readonly LabScorecardRow[], f: { verdict: VerdictFilter; stage: string | null }): LabScorecardRow[] {
  return rows.filter((r) => (f.verdict === "todos" || r.verdict === f.verdict) && (f.stage === null || r.stage_final === f.stage));
}

/**
 * Columnas por etapa, en el orden del guion (las etapas desconocidas al
 * final); un check sin etapa no tiene columna. `onlyFailing` deja solo los
 * checks que fallan en alguna fila visible y los grupos vacíos desaparecen.
 */
export function matrixGroups(catalog: readonly CheckSpec[], rows: readonly LabScorecardRow[], opts: { onlyFailing: boolean }): MatrixGroupView[] {
  const stages: string[] = [...QUALITY_STAGE_ORDER];
  for (const c of catalog) if (c.stage && !stages.includes(c.stage)) stages.push(c.stage);
  const failing = (id: string) => rows.some((r) => r.checks[id] === "falla");
  return stages
    .map((stage) => ({
      key: stage,
      label: qualityStageLabel(stage),
      color: qualityStageColor(stage),
      columns: catalog
        .filter((c) => c.stage === stage && (!opts.onlyFailing || failing(c.id)))
        .map((c) => ({ id: c.id, name: c.name || c.id, level: c.level })),
    }))
    .filter((g) => g.columns.length > 0);
}

export function finalStages(rows: readonly LabScorecardRow[]): string[] {
  const set = new Set<string>();
  for (const r of rows) if (r.stage_final) set.add(r.stage_final);
  return [...set].sort((a, b) => qualityStageRank(a) - qualityStageRank(b));
}

export function rowKey(row: Pick<LabScorecardRow, "session_id" | "episode_id">): string {
  return `${row.session_id}::${row.episode_id}`;
}

export function toRowView(row: LabScorecardRow): MatrixRowView {
  const label = `${customerLabel(row.session_id)} · ${row.episode_id}`;
  return {
    key: rowKey(row),
    verdict: row.verdict,
    label,
    title: label,
    meta: [row.episode_date, row.stage_final ? `terminó en ${qualityStageLabel(row.stage_final)}` : null].filter(Boolean).join(" · "),
    checks: row.checks,
  };
}
