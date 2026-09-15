// Deep import (no barrel) para no cerrar un ciclo con scorecard/api (ver contracts.ts).
import { stageRank } from "@plugins/agents_admin/frontend/entities/scorecard/lib";

import type {
  CheckTrend,
  FunnelRow,
  ParetoItem,
  ParetoRow,
  TrendWeek,
  WeeklyDelta,
} from "./model";

/** Pareto: barras por fallos (desc) con acumulado y participación 0..1. */
export function paretoWithCumulative(items: readonly ParetoItem[]): ParetoRow[] {
  const sorted = [...items]
    .filter((i) => i.failures > 0)
    .sort((a, b) => b.failures - a.failures || a.check_id.localeCompare(b.check_id));
  const total = sorted.reduce((s, i) => s + i.failures, 0);
  let acc = 0;
  return sorted.map((i) => {
    acc += i.failures;
    return { ...i, cumulative: acc, share: total ? acc / total : 0 };
  });
}

/**
 * Última tasa semanal con dato vs. la anterior con dato (las semanas sin
 * episodios aplicables — `rate: null` — no cuentan como "semana anterior").
 */
export function weeklyDelta(weeks: readonly TrendWeek[]): WeeklyDelta {
  const rated = weeks.filter((w) => w.rate !== null);
  const last = rated.at(-1)?.rate ?? null;
  const previous = rated.length >= 2 ? (rated.at(-2)?.rate ?? null) : null;
  return {
    last,
    previous,
    delta: last !== null && previous !== null ? last - previous : null,
  };
}

/** ¿El check falló al menos una vez en la ventana? */
export function hasFailures(trend: CheckTrend): boolean {
  return trend.weeks.some((w) => w.passed < w.applicable);
}

export function funnelTotal(row: FunnelRow): number {
  return row.FALLA + row.ALERTA + row.PASA + row.SIN_DATOS;
}

/** Filas del embudo en el orden del guion de ventas. */
export function sortFunnel(rows: readonly FunnelRow[]): FunnelRow[] {
  return [...rows].sort((a, b) => stageRank(a.stage) - stageRank(b.stage));
}
