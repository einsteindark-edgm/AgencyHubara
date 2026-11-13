import type { CostRange } from "./model";

/** Query keys de Costos — las comparten todas las features del plugin. */
export const costKeys = {
  all: ["costs-report"] as const,
  catalog: () => [...costKeys.all, "catalog"] as const,
  reports: () => [...costKeys.all, "report"] as const,
  report: (providerId: string, range: CostRange) =>
    [...costKeys.reports(), providerId, range.start, range.end] as const,
} as const;
