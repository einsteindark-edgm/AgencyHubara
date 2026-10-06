/** TanStack Query key factory para los agregados del scorecard. */
export const checkStatsKeys = {
  all: ["check-stats"] as const,
  detail: (days: number, bot: string | null = null) => [...checkStatsKeys.all, days, bot ?? "todos"] as const,
} as const;
