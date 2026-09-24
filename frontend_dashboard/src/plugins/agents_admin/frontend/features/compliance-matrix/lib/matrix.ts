import type { MatrixGroupView, MatrixRowView } from "@/shared/lib";
import {
  STAGE_ORDER,
  checkStatus,
  episodeLabel,
  stageColor,
  stageLabel,
  stageRank,
  type CheckDefinition,
  type CheckRegistry,
  type CheckStatus,
  type ScorecardRow,
} from "@plugins/agents_admin/frontend/entities/scorecard";

/** Lógica pura de la matriz episodios × checks (filtros y columnas). */

export type VerdictFilter = "todos" | "FALLA" | "ALERTA" | "PASA";

export interface MatrixFilters {
  verdict: VerdictFilter;
  /** Etapa final; null = todas. */
  stage: string | null;
  /** Solo episodios que fallan este check (llega del Pareto); null = sin filtro. */
  checkId: string | null;
}

export function filterScorecards(
  rows: readonly ScorecardRow[],
  f: MatrixFilters,
): ScorecardRow[] {
  return rows.filter(
    (r) =>
      (f.verdict === "todos" || r.verdict === f.verdict) &&
      (f.stage === null || r.stage_final === f.stage) &&
      (f.checkId === null || r.checks[f.checkId] === "falla"),
  );
}

export interface MatrixColumnGroup {
  stage: string;
  label: string;
  checks: CheckDefinition[];
}

/**
 * Columnas agrupadas por etapa (orden del registro, o del guion si el registro
 * no trae `stages`). `onlyFailing` deja solo los checks que fallan en al menos
 * una de las filas visibles; los grupos vacíos desaparecen.
 */
export function matrixColumns(
  registry: CheckRegistry,
  rows: readonly ScorecardRow[],
  opts: { onlyFailing: boolean },
): MatrixColumnGroup[] {
  const stages = registry.stages.length ? [...registry.stages] : [...STAGE_ORDER];
  for (const c of registry.checks) if (!stages.includes(c.stage)) stages.push(c.stage);
  const failing = (id: string) => rows.some((r) => r.checks[id] === "falla");
  return stages
    .map((stage) => ({
      stage,
      label: stageLabel(stage),
      checks: registry.checks.filter(
        (c) => c.stage === stage && (!opts.onlyFailing || failing(c.id)),
      ),
    }))
    .filter((g) => g.checks.length > 0);
}

export function cellStatus(row: ScorecardRow, check: CheckDefinition): CheckStatus {
  return checkStatus(row.checks[check.id], check.level);
}

/** Grupos de columnas del dominio → vista genérica de `ComplianceMatrixTable`. */
export function toMatrixGroupsView(groups: readonly MatrixColumnGroup[]): MatrixGroupView[] {
  return groups.map((g) => ({
    key: g.stage,
    label: g.label,
    color: stageColor(g.stage),
    columns: g.checks.map((c) => ({ id: c.id, name: c.name, level: c.level })),
  }));
}

/** Clave única de una fila de la matriz: sesión + episodio. */
export function matrixRowKey(row: Pick<ScorecardRow, "session_id" | "episode_id">): string {
  return `${row.session_id}::${row.episode_id}`;
}

/** Fila del scorecard → vista genérica de `ComplianceMatrixTable`. */
export function toMatrixRowView(row: ScorecardRow): MatrixRowView {
  return {
    key: matrixRowKey(row),
    verdict: row.verdict,
    label: episodeLabel(row),
    title: `${row.session_id} · ${row.episode_id}`,
    meta:
      (row.episode_date ?? row.date) +
      (row.closing_tag ? ` · ${row.closing_tag}` : "") +
      (row.fidelity === "legacy" ? " · legado" : ""),
    checks: row.checks,
  };
}

export function finalStageOptions(rows: readonly ScorecardRow[]): string[] {
  const set = new Set<string>();
  for (const r of rows) if (r.stage_final) set.add(r.stage_final);
  return [...set].sort((a, b) => stageRank(a) - stageRank(b));
}
