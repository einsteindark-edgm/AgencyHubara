import {
  paretoWithCumulative as sharedParetoWithCumulative,
  trendHasFailures,
  verdictCountsTotal,
  weeklyDelta as sharedWeeklyDelta,
} from "@/shared/lib";
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

/*
 * La matemática de las gráficas (acumulado del Pareto, delta semanal, fallos
 * en la ventana, total por veredicto) es genérica y vive en `@/shared/lib`
 * (`quality-view`) porque otros plugins pintan las mismas gráficas. Aquí se
 * expone con los nombres y tipos del contrato; el orden del guion sigue siendo
 * de este dominio.
 */

/** Pareto: barras por fallos (desc) con acumulado y participación 0..1. */
export function paretoWithCumulative(items: readonly ParetoItem[]): ParetoRow[] {
  return sharedParetoWithCumulative(items);
}

/**
 * Última tasa semanal con dato vs. la anterior con dato (las semanas sin
 * episodios aplicables — `rate: null` — no cuentan como "semana anterior").
 */
export function weeklyDelta(weeks: readonly TrendWeek[]): WeeklyDelta {
  return sharedWeeklyDelta(weeks);
}

/** ¿El check falló al menos una vez en la ventana? */
export function hasFailures(trend: CheckTrend): boolean {
  return trendHasFailures(trend);
}

export function funnelTotal(row: FunnelRow): number {
  return verdictCountsTotal(row);
}

/** Filas del embudo en el orden del guion de ventas. */
export function sortFunnel(rows: readonly FunnelRow[]): FunnelRow[] {
  return [...rows].sort((a, b) => stageRank(a.stage) - stageRank(b.stage));
}
