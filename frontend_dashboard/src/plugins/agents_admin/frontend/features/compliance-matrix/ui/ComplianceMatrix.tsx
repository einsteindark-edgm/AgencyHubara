import { useMemo, useState, type KeyboardEvent } from "react";

import {
  episodeLabel,
  episodeVerdictLabel,
  stageColor,
  stageLabel,
  statusColor,
  statusGlyph,
  statusLabel,
  useCheckRegistry,
  useScorecards,
  type CheckStatus,
  type EpisodeRef,
  type EpisodeVerdict,
} from "@plugins/agents_admin/frontend/entities/scorecard";

import {
  cellStatus,
  filterScorecards,
  finalStageOptions,
  matrixColumns,
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

const VERDICT_CHIP: Record<EpisodeVerdict, string> = {
  FALLA: "bg-danger-soft text-red",
  ALERTA: "bg-warn-soft text-orange",
  PASA: "bg-ok-soft text-green",
  SIN_DATOS: "bg-neutral-soft text-fg-muted",
};

const CELL_TEXT: Partial<Record<CheckStatus, string>> = {
  no_aplica: "var(--color-fg-faint)",
  sin_resultado: "var(--color-fg-faint)",
};

const ROW_CAP = 120;

const CELL_BG: Partial<Record<CheckStatus, string>> = {
  no_aplica: "var(--color-neutral-soft)",
  sin_resultado: "transparent",
};

/**
 * Matriz de cumplimiento: una fila por episodio, una columna por check agrupada
 * por etapa. Es la vista que escala — el mismo ✗ repetido en una columna es el
 * mismo bug en clientes distintos. Encabezados y primera columna fijos; scroll
 * propio en ambas direcciones.
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
  // El tope se guarda junto con la clave de filtros: cambiar un filtro vuelve
  // al tope inicial sin efectos (estado derivado en el render).
  const filtersKey = `${verdictFilter}|${stage ?? ""}|${checkFilter ?? ""}`;
  const [capState, setCapState] = useState({ key: filtersKey, cap: rowCap });
  const cap = capState.key === filtersKey ? capState.cap : rowCap;
  const visible = useMemo(() => rows.slice(0, cap), [rows, cap]);
  const hidden = rows.length - visible.length;

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
  const select = (sessionId: string, episodeId: string) => onSelectEpisode(sessionId, episodeId);
  const onRowKey = (sessionId: string, episodeId: string) => (e: KeyboardEvent) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      select(sessionId, episodeId);
    }
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
        <div className="max-h-[26rem] overflow-auto rounded-lg border border-line">
          <table
            aria-label="Matriz de cumplimiento: episodios por checks"
            className="border-separate border-spacing-0 text-xs"
          >
            <thead>
              <tr>
                <th
                  rowSpan={2}
                  scope="col"
                  className="sticky left-0 top-0 z-30 border-b border-r border-line bg-canvas px-2 text-left align-bottom text-[10px] font-semibold uppercase tracking-wider text-fg-faint"
                >
                  Episodio
                </th>
                {groups.map((g) => (
                  <th
                    key={g.stage}
                    colSpan={g.checks.length}
                    scope="colgroup"
                    className="sticky top-0 z-20 h-6 border-b border-line bg-canvas p-0 text-left"
                  >
                    <span
                      className="block truncate px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wide text-win-bg"
                      style={{ background: stageColor(g.stage) }}
                    >
                      {g.label}
                    </span>
                  </th>
                ))}
              </tr>
              <tr>
                {groups.flatMap((g) =>
                  g.checks.map((c) => (
                    <th
                      key={c.id}
                      scope="col"
                      title={`${c.id} · ${c.name}`}
                      className="sticky top-6 z-20 h-16 border-b border-line bg-canvas px-0 py-1 align-bottom font-mono text-[10px] font-normal text-fg-muted"
                    >
                      <span className="inline-block rotate-180 [writing-mode:vertical-rl]">{c.id}</span>
                    </th>
                  )),
                )}
              </tr>
            </thead>
            <tbody>
              {visible.map((r) => {
                const selected =
                  selectedEpisode?.sessionId === r.session_id &&
                  selectedEpisode?.episodeId === r.episode_id;
                return (
                  <tr
                    key={`${r.session_id}::${r.episode_id}`}
                    tabIndex={0}
                    aria-current={selected ? "true" : undefined}
                    onClick={() => select(r.session_id, r.episode_id)}
                    onKeyDown={onRowKey(r.session_id, r.episode_id)}
                    className="group cursor-pointer outline-none focus-visible:[&>td:first-child]:ring-2 focus-visible:[&>td:first-child]:ring-accent"
                  >
                    <td
                      className={
                        "sticky left-0 z-10 whitespace-nowrap border-r border-line px-2 py-1 group-hover:bg-toolbar " +
                        (selected ? "bg-accent-soft" : "bg-canvas")
                      }
                      title={`${r.session_id} · ${r.episode_id}`}
                    >
                      <span className={"mr-1.5 inline-block rounded px-1.5 py-px text-[10px] font-bold " + VERDICT_CHIP[r.verdict]}>
                        {episodeVerdictLabel(r.verdict).toUpperCase()}
                      </span>
                      <span className="font-mono text-[11px] text-fg">{episodeLabel(r)}</span>
                      <span className="ml-1.5 text-[11px] text-fg-faint">
                        {r.episode_date ?? r.date}
                        {r.closing_tag ? ` · ${r.closing_tag}` : ""}
                        {r.fidelity === "legacy" ? " · legado" : ""}
                      </span>
                    </td>
                    {groups.flatMap((g) =>
                      g.checks.map((c) => {
                        const st = cellStatus(r, c);
                        return (
                          <td
                            key={c.id}
                            title={`${c.id} · ${statusLabel(st)}`}
                            className="h-6 w-6 min-w-6 rounded-[5px] border-2 border-canvas text-center text-[10px] font-bold"
                            style={{
                              background: CELL_BG[st] ?? statusColor(st),
                              color: CELL_TEXT[st] ?? "var(--color-win-bg)",
                            }}
                          >
                            {statusGlyph(st)}
                          </td>
                        );
                      }),
                    )}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {hidden > 0 && (
        <button
          type="button"
          onClick={() => setCapState({ key: filtersKey, cap: cap + rowCap })}
          className="self-start rounded-md border border-line-strong px-2.5 py-1 text-xs font-medium text-fg transition hover:bg-white/5"
        >
          Mostrar {Math.min(rowCap, hidden)} más ({hidden} sin pintar)
        </button>
      )}
      <p className="text-[11px] text-fg-faint">
        ✓ pasa · ✗ falla (rojo crítico, naranja mayor, amarillo menor) · – no aplica · ? desconocido · · sin
        evaluar. Elige una fila para ver su trayectoria y su scorecard.
      </p>
    </section>
  );
}

export default ComplianceMatrix;
