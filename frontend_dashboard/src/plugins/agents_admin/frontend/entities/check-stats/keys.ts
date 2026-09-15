/** TanStack Query key factory para los agregados del scorecard. */
export const checkStatsKeys = {
  all: ["check-stats"] as const,
  detail: (days: number) => [...checkStatsKeys.all, days] as const,
} as const;
