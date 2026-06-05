import { useState } from "react";

import { useEvalTrend, type EvalTrendSeries } from "@/entities/eval-trend";

const W = 160;
const H = 36;
const PAD = 3;

/** Sparkline SVG sin dependencias: línea del avg por día + umbral + puntos bajos en rojo. */
function Sparkline({
  series,
  threshold,
}: {
  series: EvalTrendSeries;
  threshold: number;
}) {
  const pts = series.points;
  if (pts.length === 0) return <span className="text-xs text-black/30">sin datos</span>;

  const xs = (i: number) =>
    pts.length === 1 ? W / 2 : PAD + (i * (W - 2 * PAD)) / (pts.length - 1);
  const ys = (v: number) => H - PAD - v * (H - 2 * PAD); // 0..1 -> abajo..arriba
  const line = pts.map((p, i) => `${xs(i)},${ys(p.avg)}`).join(" ");
  const yThr = ys(threshold);

  return (
    <svg width={W} height={H} className="shrink-0" role="img" aria-label={`tendencia ${series.metric}`}>
      <line x1={PAD} y1={yThr} x2={W - PAD} y2={yThr} stroke="#d1d5db" strokeWidth={1} strokeDasharray="3 3" />
      <polyline points={line} fill="none" stroke="#6366f1" strokeWidth={1.5} />
      {pts.map((p, i) => (
        <circle
          key={p.date}
          cx={xs(i)}
          cy={ys(p.avg)}
          r={p.avg < threshold ? 2.6 : 1.8}
          fill={p.avg < threshold ? "#ef4444" : "#6366f1"}
        >
          <title>{`${p.date}: ${p.avg.toFixed(2)} (min ${p.min.toFixed(2)}, n=${p.n})`}</title>
        </circle>
      ))}
    </svg>
  );
}

function MetricRow({ series, threshold }: { series: EvalTrendSeries; threshold: number }) {
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
  const dirColor = dir === "↑" ? "text-emerald-600" : dir === "↓" ? "text-red-500" : "text-black/40";
  const lastColor = last === null ? "text-black/30" : last < threshold ? "text-red-500" : "text-emerald-600";

  return (
    <div className="flex items-center gap-3 border-b border-black/5 py-2">
      <div className="w-44 shrink-0 truncate text-sm font-medium" title={series.metric}>
        {series.metric}
      </div>
      <Sparkline series={series} threshold={threshold} />
      <div className={"w-12 shrink-0 text-right text-sm font-semibold " + lastColor}>
        {last === null ? "—" : last.toFixed(2)}
      </div>
      <div className={"w-5 shrink-0 text-center text-sm " + dirColor}>{dir}</div>
      <div className="min-w-0 flex-1 text-xs text-black/50">
        {lowDates.length === 0 ? (
          <span className="text-emerald-600/70">sin días bajos</span>
        ) : (
          <span title={lowDates.join(", ")}>
            <span className="font-semibold text-red-500">{lowDates.length}</span> día
            {lowDates.length > 1 ? "s" : ""} bajo {threshold}: {lowDates.slice(-3).join(", ")}
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
 * GET /api/chats/evals/history (lo alimenta el eval diario online).
 */
export function EvalTrendChart() {
  const [suite, setSuite] = useState<"online" | "golden">("online");
  const { data, isLoading, isError } = useEvalTrend(30, suite);
  const threshold = data?.threshold ?? 0.7;

  return (
    <section className="rounded-lg border border-black/10 p-4">
      <header className="mb-3 flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-semibold">Tendencia de calidad</h3>
        <span className="text-xs text-black/40">últimos 30 días · umbral {threshold}</span>
        <div className="ml-auto flex gap-1 rounded-md bg-black/5 p-0.5 text-xs">
          {(["online", "golden"] as const).map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => setSuite(s)}
              className={
                "rounded px-2 py-0.5 " +
                (suite === s ? "bg-white font-semibold shadow-sm" : "text-black/50")
              }
            >
              {s === "online" ? "conversaciones reales" : "golden (CI)"}
            </button>
          ))}
        </div>
      </header>

      {isLoading ? (
        <p className="py-6 text-center text-sm text-black/40">Cargando tendencia…</p>
      ) : isError ? (
        <p className="py-6 text-center text-sm text-red-500/70">
          No se pudo leer la tendencia.
        </p>
      ) : !data || data.series.length === 0 ? (
        <p className="py-6 text-center text-sm text-black/40">
          Aún no hay histórico para esta suite. Se llena con cada corrida del eval
          {suite === "online" ? " diario sobre conversaciones reales" : " golden"}.
        </p>
      ) : (
        <div>
          {data.series.map((s) => (
            <MetricRow key={s.metric} series={s} threshold={threshold} />
          ))}
        </div>
      )}
    </section>
  );
}

export default EvalTrendChart;
