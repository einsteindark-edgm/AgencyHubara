import { useMemo, useState } from "react";

import {
  formatWeekLabel,
  qualityLevelLabel as levelLabel,
  sparklineGeometry,
  trendHasFailures as hasFailures,
  weeklyDelta,
  type TrendSeriesView as CheckTrendSeries,
} from "@/shared/lib";

interface Props {
  trend: readonly CheckTrendSeries[];
}

const DIMS = { width: 220, height: 56, padX: 5, padY: 6 };
/** Bajo este cumplimiento el valor se resalta en rojo. */
const ALERT_RATE = 0.9;

function pct(rate: number): string {
  return `${Math.round(rate * 100)} %`;
}

function Sparkline({ series }: { series: CheckTrendSeries }) {
  const g = sparklineGeometry(series.weeks, DIMS);
  const d = weeklyDelta(series.weeks);
  const first = series.weeks[0]?.week ?? "";
  const last = series.weeks.at(-1)?.week ?? "";
  const lastAlert = d.last !== null && d.last < ALERT_RATE;
  const aria =
    `${series.check_id}: cumplimiento semanal` +
    (d.last !== null ? `, última semana con datos ${pct(d.last)}` : ", sin datos") +
    (d.delta !== null ? `, ${d.delta >= 0 ? "sube" : "baja"} ${Math.abs(Math.round(d.delta * 100))} puntos` : "");

  return (
    <article className="flex flex-col gap-1 rounded-lg border border-line p-2.5">
      <header className="flex items-baseline justify-between gap-2">
        <span className="font-mono text-[11px] text-fg-muted">{series.check_id}</span>
        <span className={"text-sm font-semibold tabular-nums " + (lastAlert ? "text-red" : "text-fg")}>
          {d.last !== null ? pct(d.last) : "—"}
        </span>
      </header>
      <p className="truncate text-xs text-fg-soft" title={series.name}>
        {series.name || series.check_id}
        <span className="text-fg-faint"> · {levelLabel(series.level)}</span>
      </p>
      <svg viewBox={`0 0 ${DIMS.width} ${DIMS.height}`} className="h-auto w-full" role="img" aria-label={aria}>
        <line
          x1={DIMS.padX}
          x2={DIMS.width - DIMS.padX}
          y1={g.refY}
          y2={g.refY}
          stroke="var(--color-line-strong)"
          strokeDasharray="2 3"
        >
          <title>Referencia: 90 % de cumplimiento</title>
        </line>
        {g.areaPath && <path d={g.areaPath} fill="var(--color-accent-fg)" opacity={0.12} />}
        {g.linePath && <path d={g.linePath} fill="none" stroke="var(--color-accent-fg)" strokeWidth={1.75} />}
        {g.points.map((p) => (
          <circle key={p.week} cx={p.x} cy={p.y} r={4} fill="transparent">
            <title>{`Semana del ${formatWeekLabel(p.week)}: ${pct(p.rate)}`}</title>
          </circle>
        ))}
        {g.endpoint && (
          <circle
            cx={g.endpoint.x}
            cy={g.endpoint.y}
            r={3.5}
            fill={lastAlert ? "var(--color-red)" : "var(--color-accent-fg)"}
            stroke="var(--color-canvas)"
            strokeWidth={1.5}
          />
        )}
      </svg>
      <footer className="flex items-center justify-between gap-1 text-[10px] text-fg-faint">
        <span>{formatWeekLabel(first)}</span>
        {d.delta !== null ? (
          <span className={"font-semibold " + (d.delta >= 0 ? "text-green" : "text-red")}>
            {d.delta >= 0 ? "▲" : "▼"} {Math.abs(Math.round(d.delta * 100))} pts vs semana anterior
          </span>
        ) : (
          <span>sin semana previa</span>
        )}
        <span>{formatWeekLabel(last)}</span>
      </footer>
    </article>
  );
}

/**
 * Tendencia por check en small multiples: tasa semanal de cumplimiento con
 * referencia del 90 %, punto final resaltado, último valor y delta contra la
 * semana anterior. Por defecto solo los checks que fallaron en la ventana.
 */
export function CheckTrend({ trend }: Props) {
  const [showAll, setShowAll] = useState(false);
  const visible = useMemo(
    () => (showAll ? [...trend] : trend.filter(hasFailures)),
    [trend, showAll],
  );

  return (
    <section className="flex flex-col gap-2" aria-label="Tendencia semanal de cumplimiento por check">
      <div className="flex items-center gap-2">
        <p className="text-[11px] text-fg-faint">
          {showAll
            ? `${trend.length} checks`
            : `${visible.length} de ${trend.length} checks con al menos un fallo en la ventana`}
        </p>
        <button
          type="button"
          aria-pressed={showAll}
          onClick={() => setShowAll((v) => !v)}
          className={
            "ml-auto rounded-full border px-2.5 py-0.5 text-xs transition " +
            (showAll ? "border-fg bg-fg" : "border-line-strong hover:bg-white/5")
          }
        >
          <span className={showAll ? "text-win-bg" : "text-fg"}>Todos los checks</span>
        </button>
      </div>
      {visible.length === 0 ? (
        <p className="rounded-lg border border-line p-4 text-sm text-fg-muted">
          Ningún check falló en la ventana. Activa "Todos los checks" para ver sus tendencias.
        </p>
      ) : (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(13rem,1fr))] gap-2">
          {visible.map((s) => (
            <Sparkline key={s.check_id} series={s} />
          ))}
        </div>
      )}
    </section>
  );
}

export default CheckTrend;
