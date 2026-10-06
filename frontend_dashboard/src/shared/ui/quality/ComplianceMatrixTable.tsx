import { useMemo, useState, type KeyboardEvent } from "react";

import {
  matrixCellStatus,
  qualityStatusColor as statusColor,
  qualityStatusGlyph as statusGlyph,
  qualityStatusLabel as statusLabel,
  qualityVerdictLabel as episodeVerdictLabel,
  type MatrixGroupView,
  type MatrixRowView,
  type QualityStatus as CheckStatus,
  type QualityVerdict as EpisodeVerdict,
} from "@/shared/lib";

interface Props {
  groups: readonly MatrixGroupView[];
  /** Filas ya filtradas (no vacías: el vacío lo explica la composición). */
  rows: readonly MatrixRowView[];
  selectedKey: string | null;
  onSelectRow: (row: MatrixRowView) => void;
  /** Filas pintadas por tanda (64 columnas × cientos de episodios revientan el DOM). */
  rowCap: number;
  /** Cambiarla (p. ej. la clave de los filtros) vuelve al tope inicial de filas. */
  resetKey: string;
}

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

const CELL_BG: Partial<Record<CheckStatus, string>> = {
  no_aplica: "var(--color-neutral-soft)",
  sin_resultado: "transparent",
};

/**
 * Tabla de la matriz de cumplimiento: una fila por episodio, una columna por
 * check agrupada (p. ej. por etapa). El mismo ✗ repetido en una columna es el
 * mismo bug en clientes distintos. Encabezados y primera columna fijos; scroll
 * propio en ambas direcciones; pinta las filas por tandas de `rowCap`.
 */
export function ComplianceMatrixTable({ groups, rows, selectedKey, onSelectRow, rowCap, resetKey }: Props) {
  // El tope se guarda junto con la clave de filtros: cambiar un filtro vuelve
  // al tope inicial sin efectos (estado derivado en el render).
  const [capState, setCapState] = useState({ key: resetKey, cap: rowCap });
  const cap = capState.key === resetKey ? capState.cap : rowCap;
  const visible = useMemo(() => rows.slice(0, cap), [rows, cap]);
  const hidden = rows.length - visible.length;

  const onRowKey = (row: MatrixRowView) => (e: KeyboardEvent) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      onSelectRow(row);
    }
  };

  return (
    <>
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
                  key={g.key}
                  colSpan={g.columns.length}
                  scope="colgroup"
                  className="sticky top-0 z-20 h-6 border-b border-line bg-canvas p-0 text-left"
                >
                  <span
                    className="block truncate px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wide text-win-bg"
                    style={{ background: g.color }}
                  >
                    {g.label}
                  </span>
                </th>
              ))}
            </tr>
            <tr>
              {groups.flatMap((g) =>
                g.columns.map((c) => (
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
              const selected = selectedKey === r.key;
              return (
                <tr
                  key={r.key}
                  tabIndex={0}
                  aria-current={selected ? "true" : undefined}
                  onClick={() => onSelectRow(r)}
                  onKeyDown={onRowKey(r)}
                  className="group cursor-pointer outline-none focus-visible:[&>td:first-child]:ring-2 focus-visible:[&>td:first-child]:ring-accent"
                >
                  <td
                    className={
                      "sticky left-0 z-10 whitespace-nowrap border-r border-line px-2 py-1 group-hover:bg-toolbar " +
                      (selected ? "bg-accent-soft" : "bg-canvas")
                    }
                    title={r.title}
                  >
                    <span className={"mr-1.5 inline-block rounded px-1.5 py-px text-[10px] font-bold " + VERDICT_CHIP[r.verdict]}>
                      {episodeVerdictLabel(r.verdict).toUpperCase()}
                    </span>
                    <span className="font-mono text-[11px] text-fg">{r.label}</span>
                    <span className="ml-1.5 text-[11px] text-fg-faint">{r.meta}</span>
                  </td>
                  {groups.flatMap((g) =>
                    g.columns.map((c) => {
                      const st = matrixCellStatus(r, c);
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
      {hidden > 0 && (
        <button
          type="button"
          onClick={() => setCapState({ key: resetKey, cap: cap + rowCap })}
          className="self-start rounded-md border border-line-strong px-2.5 py-1 text-xs font-medium text-fg transition hover:bg-white/5"
        >
          Mostrar {Math.min(rowCap, hidden)} más ({hidden} sin pintar)
        </button>
      )}
    </>
  );
}

/** Leyenda de glifos de la matriz (nunca solo color). */
export function ComplianceMatrixLegend({
  hint = "Elige una fila para ver su trayectoria y su scorecard.",
}: {
  hint?: string;
}) {
  return (
    <p className="text-[11px] text-fg-faint">
      ✓ pasa · ✗ falla (rojo crítico, naranja mayor, amarillo menor) · – no aplica · ? desconocido · · sin
      evaluar. {hint}
    </p>
  );
}

export default ComplianceMatrixTable;
