/** TanStack Query key factory para la tendencia de calidad. */
export const evalTrendKeys = {
  all: ["eval-trend"] as const,
  trend: (days: number, suite: string) =>
    [...evalTrendKeys.all, days, suite] as const,
} as const;
