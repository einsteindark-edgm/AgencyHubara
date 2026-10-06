export type {
  CheckStats,
  CheckTrend,
  FunnelRow,
  ParetoItem,
  ParetoRow,
  TrendWeek,
  VerdictTotals,
  WeeklyDelta,
} from "./model";
export { checkStatsKeys } from "./keys";
export { funnelTotal, hasFailures, paretoWithCumulative, sortFunnel, weeklyDelta } from "./lib";
export { useCheckStats, type StatsBot } from "./api";
