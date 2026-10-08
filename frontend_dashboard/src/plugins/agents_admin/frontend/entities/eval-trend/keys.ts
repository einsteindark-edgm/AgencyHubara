/** TanStack Query key factory para la tendencia de calidad. */
export const evalTrendKeys = {
  all: ["eval-trend"] as const,
  trend: (days: number, suite: string, bot: string | null = null) =>
    [...evalTrendKeys.all, days, suite, bot ?? "todos"] as const,
} as const;
