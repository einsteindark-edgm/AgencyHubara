import { useState } from "react";

import { useEvalTrend, type EvalTrendSeries } from "@plugins/agents_admin/frontend/entities/eval-trend";

const W = 160;
const H = 36;
const PAD = 3;

interface DaySelection {
  /** Día seleccionado (filtra la vista por-episodio de abajo). */
  selectedDate?: string | null;
  /** Click en un punto/día de la serie. `null` = limpiar. */
  onSelectDate?: (date: string | null) => void;
}

/** Sparkline SVG sin dependencias: línea del avg por día + umbral + puntos bajos en rojo. */
function Sparkline({
  series,
  threshold,
  selectedDate,
  onSelectDate,
}: {
  series: EvalTrendSeries;
  threshold: number;
} & DaySelection) {
  const pts = series.points;
  if (pts.length === 0) return <span className="text-xs text-fg-faint">sin datos</span>;

  const xs = (i: number) =>
    pts.length === 1 ? W / 2 : PAD + (i * (W - 2 * PAD)) / (pts.length - 1);
  const ys = (v: number) => H - PAD - v * (H - 2 * PAD); // 0..1 -> abajo..arriba
  const line = pts.map((p, i) => `${xs(i)},${ys(p.avg)}`).join(" ");
  const yThr = ys(threshold);

  return (
    <svg width={W} height={H} className="shrink-0" role="img" aria-label={`tendencia ${series.metric}`}>
      <line x1={PAD} y1={yThr} x2={W - PAD} y2={yThr} stroke="var(--color-line-strong)" strokeWidth={1} strokeDasharray="3 3" />
      <polyline points={line} fill="none" stroke="var(--color-accent-fg)" strokeWidth={1.5} />
      {pts.map((p, i) => (
        <circle
          key={p.date}
          cx={xs(i)}
          cy={ys(p.avg)}
          r={p.date === selectedDate ? 3.4 : p.avg < threshold ? 2.6 : 1.8}
          fill={p.avg < threshold ? "var(--color-red)" : "var(--color-accent-fg)"}
          stroke={p.date === selectedDate ? "var(--color-fg)" : "none"}
          strokeWidth={p.date === selectedDate ? 1 : 0}
          className={onSelectDate ? "cursor-pointer" : undefined}
          onClick={
            onSelectDate
              ? () => onSelectDate(p.date === selectedDate ? null : p.date)
              : undefined
          }
        >
          <title>{`${p.date}: ${p.avg.toFixed(2)} (min ${p.min.toFixed(2)}, n=${p.n})${onSelectDate ? " — click para ver las conversaciones de ese día" : ""}`}</title>
        </circle>
      ))}
    </svg>
  );
}

function MetricRow({
  series,
  threshold,
  selectedDate,
  onSelectDate,
}: { series: EvalTrendSeries; threshold: number } & DaySelection) {
  const pts = series.points;
  const last = pts.at(-1)?.avg ?? null;
  const first = pts[0]?.avg ?? null;
  const lowDates = pts.filter((p) => p.avg < threshold).map((p) => p.date);
  const dir =
    last === null || first === null || pts.length < 2
      ? "→"
      : last > first + 0.02
        ? "↑"
        : last < first - 0.02
          ? "↓"
          : "→";
  const dirColor = dir === "↑" ? "text-green" : dir === "↓" ? "text-red" : "text-fg-faint";
  const lastColor = last === null ? "text-fg-faint" : last < threshold ? "text-red" : "text-green";

  return (
    <div className="flex items-center gap-3 border-b border-line py-2">
      <div className="w-44 shrink-0 truncate text-sm font-medium text-fg" title={series.metric}>
        {series.metric}
      </div>
      <Sparkline
        series={series}
        threshold={threshold}
        selectedDate={selectedDate}
        onSelectDate={onSelectDate}
      />
      <div className={"w-12 shrink-0 text-right text-sm font-semibold " + lastColor}>
        {last === null ? "—" : last.toFixed(2)}
      </div>
      <div className={"w-5 shrink-0 text-center text-sm " + dirColor}>{dir}</div>
      <div className="min-w-0 flex-1 text-xs text-fg-muted">
        {lowDates.length === 0 ? (
          <span className="text-green/70">sin días bajos</span>
        ) : (
          <span title={lowDates.join(", ")}>
            <span className="font-semibold text-red">{lowDates.length}</span> día
            {lowDates.length > 1 ? "s" : ""} bajo {threshold}:{" "}
            {lowDates.slice(-3).map((d, i) => (
              <button
                key={d}
                type="button"
                onClick={() => onSelectDate?.(d === selectedDate ? null : d)}
                className={
                  "underline decoration-dotted underline-offset-2 hover:text-fg " +
                  (d === selectedDate ? "font-semibold text-fg" : "")
                }
                title="Ver las conversaciones evaluadas ese día"
              >
                {d}
                {i < Math.min(lowDates.length, 3) - 1 ? ", " : ""}
              </button>
            ))}
            {lowDates.length > 3 ? "…" : ""}
          </span>
        )}
      </div>
    </div>
  );
}

/**
 * Tendencia de calidad del agente: por cada métrica, su score por día (sparkline),
 * el umbral 0.7, y el registro de los días en que estuvo bajo. Lee
 * GET /api/agents/evals/history (lo alimenta el eval online por episodio).
 * Click en un día → la vista por-episodio de abajo se filtra a ese día (así un
 * día bajo se traza hasta LA conversación que lo causó).
 */
export function EvalTrendChart({ selectedDate, onSelectDate }: DaySelection) {
  const [suite, setSuite] = useState<"online" | "golden">("online");
  const { data, isLoading, isError } = useEvalTrend(30, suite);
  const threshold = data?.threshold ?? 0.7;

  return (
    <section className="rounded-lg border border-line p-4 text-fg">
      <header className="mb-3 flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-semibold">Tendencia de calidad</h3>
        <span className="text-xs text-fg-faint">
          últimos 30 días · umbral {threshold} · promedio de TODAS las
          conversaciones evaluadas cada día — el detalle por conversación está abajo
        </span>
        <div className="ml-auto flex gap-1 rounded-md bg-white/5 p-0.5 text-xs">
          {(["online", "golden"] as const).map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => setSuite(s)}
              className={
                "rounded px-2 py-0.5 " +
                (suite === s ? "bg-white/15 font-semibold text-fg" : "text-fg-muted")
              }
            >
              {s === "online" ? "conversaciones reales" : "golden (CI)"}
            </button>
          ))}
        </div>
      </header>

      {isLoading ? (
        <p className="py-6 text-center text-sm text-fg-faint">Cargando tendencia…</p>
      ) : isError ? (
        <p className="py-6 text-center text-sm text-red/70">
          No se pudo leer la tendencia.
        </p>
      ) : !data || data.series.length === 0 ? (
        <p className="py-6 text-center text-sm text-fg-faint">
          Aún no hay histórico para esta suite. Se llena con cada corrida del eval
          {suite === "online" ? " diario sobre conversaciones reales" : " golden"}.
        </p>
      ) : (
        <div>
          {data.series.map((s) => (
            <MetricRow
              key={s.metric}
              series={s}
              threshold={threshold}
              selectedDate={selectedDate}
              onSelectDate={suite === "online" ? onSelectDate : undefined}
            />
          ))}
        </div>
      )}
    </section>
  );
}

export default EvalTrendChart;
