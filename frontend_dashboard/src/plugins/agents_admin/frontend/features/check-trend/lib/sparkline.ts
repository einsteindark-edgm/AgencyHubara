import type { TrendWeek } from "@plugins/agents_admin/frontend/entities/check-stats";

/** Geometría pura del sparkline semanal de cumplimiento (SVG sin librería). */

export interface SparkDims {
  width: number;
  height: number;
  padX: number;
  padY: number;
}

export interface SparkPoint {
  x: number;
  y: number;
  rate: number;
  week: string;
}

export interface SparkGeometry {
  domainMin: number;
  points: SparkPoint[];
  /** Tramos continuos (se cortan en semanas sin episodios aplicables). */
  segments: SparkPoint[][];
  linePath: string;
  areaPath: string;
  /** y de la referencia del 90 %. */
  refY: number;
  endpoint: SparkPoint | null;
}

const REFERENCE_RATE = 0.9;

export function sparklineGeometry(weeks: readonly TrendWeek[], dims: SparkDims): SparkGeometry {
  const rates = weeks.map((w) => w.rate).filter((r): r is number => r !== null);
  const minRate = rates.length ? Math.min(...rates) : 1;
  const domainMin = minRate < 0.5 ? Math.floor(minRate * 10) / 10 : 0.5;
  const innerW = dims.width - 2 * dims.padX;
  const innerH = dims.height - 2 * dims.padY;
  const x = (i: number) =>
    weeks.length <= 1 ? dims.padX + innerW / 2 : dims.padX + (i * innerW) / (weeks.length - 1);
  const y = (rate: number) =>
    dims.padY + innerH * (1 - (rate - domainMin) / (1 - domainMin || 1));
  const baseY = y(domainMin);

  const segments: SparkPoint[][] = [];
  let current: SparkPoint[] = [];
  weeks.forEach((w, i) => {
    if (w.rate === null) {
      if (current.length) segments.push(current);
      current = [];
      return;
    }
    current.push({ x: x(i), y: y(w.rate), rate: w.rate, week: w.week });
  });
  if (current.length) segments.push(current);

  const points = segments.flat();
  const fmt = (n: number) => Number(n.toFixed(2));
  const linePath = segments
    .map((seg) => seg.map((p, i) => `${i === 0 ? "M" : "L"}${fmt(p.x)},${fmt(p.y)}`).join(" "))
    .join(" ");
  const areaPath = segments
    .filter((seg) => seg.length > 1)
    .map(
      (seg) =>
        `M${fmt(seg[0].x)},${fmt(baseY)} ` +
        seg.map((p) => `L${fmt(p.x)},${fmt(p.y)}`).join(" ") +
        ` L${fmt(seg[seg.length - 1].x)},${fmt(baseY)} Z`,
    )
    .join(" ");

  return {
    domainMin,
    points,
    segments,
    linePath,
    areaPath,
    refY: y(REFERENCE_RATE),
    endpoint: points.at(-1) ?? null,
  };
}

const MONTHS = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"];

/** `2026-07-27` → `27 jul` (sin Date: la semana ya viene normalizada del backend). */
export function formatWeekLabel(iso: string): string {
  const m = /^\d{4}-(\d{2})-(\d{2})$/.exec(iso);
  if (!m) return iso;
  const month = MONTHS[Number(m[1]) - 1];
  return month ? `${Number(m[2])} ${month}` : iso;
}
