import { useQuery } from "@tanstack/react-query";

import { apiClient } from "@/shared/api";

import { checkStatsSchema } from "./contracts";
import { checkStatsKeys } from "./keys";
import type { CheckStats } from "./model";

async function fetchCheckStats(days: number, signal?: AbortSignal): Promise<CheckStats> {
  const raw = await apiClient.get<unknown>(`/api/agents/evals/checks/stats?days=${days}`, {
    signal,
  });
  return checkStatsSchema.parse(raw);
}

/** Agregados (veredictos, Pareto, tendencia semanal, embudo) de los últimos `days` días. */
export function useCheckStats(days = 56) {
  return useQuery({
    queryKey: checkStatsKeys.detail(days),
    queryFn: ({ signal }) => fetchCheckStats(days, signal),
  });
}
