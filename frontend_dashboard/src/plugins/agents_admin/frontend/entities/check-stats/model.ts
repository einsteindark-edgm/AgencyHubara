import type { z } from "zod";

import type {
  checkStatsSchema,
  checkTrendSchema,
  funnelRowSchema,
  paretoItemSchema,
  trendWeekSchema,
  verdictTotalsSchema,
} from "./contracts";

export type CheckStats = z.infer<typeof checkStatsSchema>;
export type VerdictTotals = z.infer<typeof verdictTotalsSchema>;
export type ParetoItem = z.infer<typeof paretoItemSchema>;
export type TrendWeek = z.infer<typeof trendWeekSchema>;
export type CheckTrend = z.infer<typeof checkTrendSchema>;
export type FunnelRow = z.infer<typeof funnelRowSchema>;

export interface ParetoRow extends ParetoItem {
  /** Fallos acumulados hasta esta barra (inclusive). */
  cumulative: number;
  /** cumulative / total — 0..1. */
  share: number;
}

/** Última tasa semanal con dato vs. la anterior (forma de vista de `@/shared/lib`). */
export type { WeeklyDelta } from "@/shared/lib";
