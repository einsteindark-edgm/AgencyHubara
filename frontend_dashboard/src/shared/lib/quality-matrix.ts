/**
 * La matriz episodios × checks (`ComplianceMatrixTable` de `@/shared/ui`) del
 * Resumen del laboratorio y de Calidad LLM de Agents (2026-10-02). Lógica
 * pura: filtros, columnas agrupadas por etapa del guion y la fila con el
 * número del cliente tapado.
 */

import { customerLabel, type CheckSpecView } from "./quality-checks";
import {
  QUALITY_STAGE_ORDER,
  qualityStageColor,
  qualityStageLabel,
  qualityStageRank,
  type MatrixGroupView,
  type MatrixRowView,
  type QualityCheckVerdict,
  type QualityVerdict,
} from "./quality-view";

/** Una fila de la matriz: el episodio con cada check agregado sobre sus turnos. */
export interface MatrixScorecardRow {
  session_id: string;
  episode_id: string;
  verdict: QualityVerdict;
  stage_final: string | null;
  episode_date: string | null;
  checks: Record<string, QualityCheckVerdict>;
}

export type MatrixVerdictFilter = "todos" | "FALLA" | "ALERTA" | "PASA";

export function filterMatrixRows<T extends MatrixScorecardRow>(rows: readonly T[], f: { verdict: MatrixVerdictFilter; stage: string | null }): T[] {
  return rows.filter((r) => (f.verdict === "todos" || r.verdict === f.verdict) && (f.stage === null || r.stage_final === f.stage));
}

/**
 * Columnas por etapa, en el orden del guion (las etapas desconocidas al
 * final); un check sin etapa no tiene columna. `onlyFailing` deja solo los
 * checks que fallan en alguna fila visible y los grupos vacíos desaparecen.
 */
export function matrixGroups(catalog: readonly CheckSpecView[], rows: readonly MatrixScorecardRow[], opts: { onlyFailing: boolean }): MatrixGroupView[] {
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

export function matrixFinalStages(rows: readonly MatrixScorecardRow[]): string[] {
  const set = new Set<string>();
  for (const r of rows) if (r.stage_final) set.add(r.stage_final);
  return [...set].sort((a, b) => qualityStageRank(a) - qualityStageRank(b));
}

export function matrixRowKey(row: Pick<MatrixScorecardRow, "session_id" | "episode_id">): string {
  return `${row.session_id}::${row.episode_id}`;
}

export function toMatrixRowView(row: MatrixScorecardRow): MatrixRowView {
  const label = `${customerLabel(row.session_id)} · ${row.episode_id}`;
  return {
    key: matrixRowKey(row),
    verdict: row.verdict,
    label,
    title: label,
    meta: [row.episode_date, row.stage_final ? `terminó en ${qualityStageLabel(row.stage_final)}` : null].filter(Boolean).join(" · "),
    checks: row.checks,
  };
}
