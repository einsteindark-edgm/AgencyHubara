import { useMemo, useState } from "react";

import type { MatrixRowView } from "@/shared/lib";
import { ComplianceMatrixLegend, ComplianceMatrixTable } from "@/shared/ui";
import {
  stageLabel,
  useCheckRegistry,
  useScorecards,
  type EpisodeRef,
} from "@plugins/agents_admin/frontend/entities/scorecard";

import {
  filterScorecards,
  finalStageOptions,
  matrixColumns,
  matrixRowKey,
  toMatrixGroupsView,
  toMatrixRowView,
  type VerdictFilter,
} from "../lib/matrix";

interface Props {
  days?: number;
  verdictFilter: VerdictFilter;
  onVerdictFilterChange: (v: VerdictFilter) => void;
  /** Check elegido en el Pareto: solo episodios que lo fallan. */
  checkFilter: string | null;
  onClearCheckFilter: () => void;
  selectedEpisode: EpisodeRef | null;
  onSelectEpisode: (sessionId: string, episodeId: string) => void;
  /** Filas pintadas por tanda (64 columnas × cientos de episodios revientan el DOM). */
  rowCap?: number;
}

const VERDICT_OPTIONS: ReadonlyArray<{ value: VerdictFilter; label: string }> = [
  { value: "todos", label: "Todos" },
  { value: "FALLA", label: "Falla" },
  { value: "ALERTA", label: "Alerta" },
  { value: "PASA", label: "Pasa" },
];

const ROW_CAP = 120;

/**
 * Matriz de cumplimiento: una fila por episodio, una columna por check agrupada
 * por etapa. Es la vista que escala — el mismo ✗ repetido en una columna es el
 * mismo bug en clientes distintos. Esta composición es dueña de los filtros
 * (veredicto, etapa final, check del Pareto) y del mapeo scorecard → vista; la
 * tabla (encabezados fijos, scroll, tandas de filas) es `ComplianceMatrixTable`
 * de `@/shared/ui`.
 */
export function ComplianceMatrix({
  days = 30,
  verdictFilter,
  onVerdictFilterChange,
  checkFilter,
  onClearCheckFilter,
  selectedEpisode,
  onSelectEpisode,
  rowCap = ROW_CAP,
}: Props) {
  const list = useScorecards(days);
  const registry = useCheckRegistry();
  const [stage, setStage] = useState<string | null>(null);
  const [onlyFailing, setOnlyFailing] = useState(false);

  const allRows = useMemo(() => list.data?.scorecards ?? [], [list.data]);
  const rows = useMemo(
    () => filterScorecards(allRows, { verdict: verdictFilter, stage, checkId: checkFilter }),
    [allRows, verdictFilter, stage, checkFilter],
  );
  const groups = useMemo(
    () => (registry.data ? matrixColumns(registry.data, rows, { onlyFailing }) : []),
    [registry.data, rows, onlyFailing],
  );
  const stages = useMemo(() => finalStageOptions(allRows), [allRows]);
  const groupsView = useMemo(() => toMatrixGroupsView(groups), [groups]);
  const rowsView = useMemo(() => rows.map(toMatrixRowView), [rows]);
  const rowsByKey = useMemo(() => new Map(rows.map((r) => [matrixRowKey(r), r])), [rows]);
  // Cambiar un filtro vuelve la tabla al tope inicial de filas.
  const filtersKey = `${verdictFilter}|${stage ?? ""}|${checkFilter ?? ""}`;

  if (list.isLoading || registry.isLoading) {
    return <p className="p-3 text-sm text-fg-muted">Cargando scorecards…</p>;
  }
  if (list.isError || registry.isError) {
    return (
      <p className="p-3 text-sm text-red" role="alert">
        No se pudieron cargar los scorecards. Revisa que el API de evals esté disponible.
      </p>
    );
  }
  if (allRows.length === 0) {
    return (
      <p className="rounded-lg border border-line p-4 text-sm text-fg-muted">
        Aún no hay scorecards: se generan al cerrar cada episodio.
      </p>
    );
  }

  const hasFilters = verdictFilter !== "todos" || stage !== null || checkFilter !== null;
  const selectedKey = selectedEpisode
    ? matrixRowKey({ session_id: selectedEpisode.sessionId, episode_id: selectedEpisode.episodeId })
    : null;
  const selectRow = (view: MatrixRowView) => {
    const r = rowsByKey.get(view.key);
    if (r) onSelectEpisode(r.session_id, r.episode_id);
  };

  return (
    <section className="flex min-w-0 flex-col gap-2" aria-label="Matriz de cumplimiento por episodio">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <div role="group" aria-label="Filtrar por veredicto" className="flex items-center gap-1">
          <span className="mr-1 text-[10px] font-semibold uppercase tracking-wider text-fg-faint">Veredicto</span>
          {VERDICT_OPTIONS.map((o) => (
            <button
              key={o.value}
              type="button"
              aria-pressed={verdictFilter === o.value}
              onClick={() => onVerdictFilterChange(o.value)}
              className={
                "rounded-full border px-2.5 py-0.5 transition " +
                (verdictFilter === o.value
                  ? "border-fg bg-fg"
                  : "border-line-strong hover:bg-white/5")
              }
            >
              {/* El color va en el span: la regla global `button { color: inherit }`
                  de index.css no tiene capa y le gana a las utilidades en el botón. */}
              <span className={verdictFilter === o.value ? "text-win-bg" : "text-fg"}>{o.label}</span>
            </button>
          ))}
        </div>
        <label className="ml-2 inline-flex items-center gap-1.5 text-fg-muted">
          Etapa final
          <select
            value={stage ?? ""}
            onChange={(e) => setStage(e.target.value || null)}
            className="rounded-md border border-line-strong bg-canvas px-1.5 py-0.5 text-xs text-fg"
          >
            <option value="">Todas</option>
            {stages.map((s) => (
              <option key={s} value={s}>
                {stageLabel(s)}
              </option>
            ))}
          </select>
        </label>
        <label className="ml-2 inline-flex items-center gap-1.5 text-fg-muted">
          <input
            type="checkbox"
            checked={onlyFailing}
            onChange={(e) => setOnlyFailing(e.target.checked)}
            className="accent-accent"
          />
          Solo checks con fallas
        </label>
        {checkFilter && (
          <span className="inline-flex items-center gap-1 rounded-full bg-danger-soft px-2 py-0.5 text-red">
            Fallan <span className="font-mono">{checkFilter}</span>
            <button
              type="button"
              onClick={onClearCheckFilter}
              aria-label={`Quitar filtro ${checkFilter}`}
              className="ml-0.5 rounded-full px-1 hover:bg-red/25"
            >
              ×
            </button>
          </span>
        )}
        <span className="ml-auto text-fg-faint">
          {rows.length} de {allRows.length} episodios · últimos {days} días
        </span>
      </div>

      {rows.length === 0 ? (
        <div className="rounded-lg border border-line p-4 text-sm text-fg-muted">
          Ningún episodio coincide con los filtros.{" "}
          {hasFilters && (
            <button
              type="button"
              className="text-accent-fg underline"
              onClick={() => {
                onVerdictFilterChange("todos");
                setStage(null);
                onClearCheckFilter();
              }}
            >
              Quitar filtros
            </button>
          )}
        </div>
      ) : (
        <ComplianceMatrixTable
          groups={groupsView}
          rows={rowsView}
          selectedKey={selectedKey}
          onSelectRow={selectRow}
          rowCap={rowCap}
          resetKey={filtersKey}
        />
      )}
      <ComplianceMatrixLegend />
    </section>
  );
}

export default ComplianceMatrix;
