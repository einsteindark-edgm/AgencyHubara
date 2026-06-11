import type { ConversationEval } from "@plugins/agents_admin/frontend/entities/episode-eval";
import type { EvalTrendSeries } from "@plugins/agents_admin/frontend/entities/eval-trend";

/**
 * Helpers puros de la tendencia. Dos fuentes de líneas:
 *   - `lineFromAggregate`: el promedio diario de TODOS los episodios (vista global).
 *   - `linesFromEpisode`: la evolución de UN episodio a través de sus evals (sin
 *     contaminación de otros episodios — el caso que el operador pidió aislar).
 *
 * Pura y testeable: cero React, cero fetch.
 */

const MONTHS_ES = [
  "ene", "feb", "mar", "abr", "may", "jun",
  "jul", "ago", "sep", "oct", "nov", "dic",
];

/** "2026-06-01" → "1 jun". Tolerante: devuelve el crudo si no matchea. */
export function fmtDate(date: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(date);
  if (!m) return date;
  return `${Number(m[3])} ${MONTHS_ES[Number(m[2]) - 1] ?? m[2]}`;
}

/** ISO → "14:30" (vacío si no trae hora). */
export function fmtTime(ts: string): string {
  const m = /T(\d{2}):(\d{2})/.exec(ts);
  return m ? `${m[1]}:${m[2]}` : "";
}

export interface TrendLinePoint {
  key: string;
  /** Etiqueta legible del punto: "1 jun" (agregado) o "1 jun 14:30" (episodio). */
  label: string;
  /** Fecha cruda YYYY-MM-DD (para el click-a-día en modo agregado). */
  date: string;
  value: number;
  /** ¿Bajo el umbral? (agregado: avg<thr; episodio: la métrica está en `failed`). */
  below: boolean;
  hint: string;
}

export interface TrendLine {
  metric: string;
  points: TrendLinePoint[];
}

/** Serie agregada por día → línea de tendencia (una por métrica). */
export function lineFromAggregate(series: EvalTrendSeries, threshold: number): TrendLine {
  return {
    metric: series.metric,
    points: series.points.map((p) => ({
      key: p.date,
      label: fmtDate(p.date),
      date: p.date,
      value: p.avg,
      below: p.avg < threshold,
      hint: `${p.date}: ${p.avg.toFixed(2)} (min ${p.min.toFixed(2)}, n=${p.n})`,
    })),
  };
}

/**
 * Las evals de UN episodio → una línea por métrica (su evolución real).
 *
 * `below` se deriva del `failed` de cada eval (umbral POR métrica, no un umbral
 * global): un punto se pinta rojo solo si esa métrica falló en esa corrida.
 */
export function linesFromEpisode(conv: ConversationEval): TrendLine[] {
  const metrics = new Set<string>();
  conv.evals.forEach((e) => Object.keys(e.metrics).forEach((m) => metrics.add(m)));
  return [...metrics].sort().map((metric) => ({
    metric,
    points: conv.evals
      .filter((e) => metric in e.metrics)
      .map((e) => {
        const value = e.metrics[metric];
        const failed = e.failed.some((f) => f.metric === metric);
        const label = `${fmtDate(e.date)}${e.ts ? " " + fmtTime(e.ts) : ""}`;
        return {
          key: `${e.date}T${e.ts}`,
          label,
          date: e.date,
          value,
          below: failed,
          hint: `${label}: ${value.toFixed(2)}${failed ? " (falló)" : ""}`,
        };
      }),
  }));
}
