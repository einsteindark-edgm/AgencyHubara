import { useQuery } from "@tanstack/react-query";

import { apiClient } from "@/shared/api";

import { checkStatsSchema } from "./contracts";
import { checkStatsKeys } from "./keys";
import type { CheckStats } from "./model";

/** Qué bot respondió los episodios (encendido del bot nuevo, lab PR 18). */
export type StatsBot = "actual" | "nuevo";

async function fetchCheckStats(days: number, bot: StatsBot | null, signal?: AbortSignal): Promise<CheckStats> {
  const query = bot ? `days=${days}&bot=${bot}` : `days=${days}`;
  const raw = await apiClient.get<unknown>(`/api/agents/evals/checks/stats?${query}`, {
    signal,
  });
  return checkStatsSchema.parse(raw);
}

/** Agregados (veredictos, Pareto, tendencia semanal, embudo) de los últimos
 *  `days` días; `bot` deja solo los episodios de ese bot. */
export function useCheckStats(days = 56, bot: StatsBot | null = null) {
  return useQuery({
    queryKey: checkStatsKeys.detail(days, bot),
    queryFn: ({ signal }) => fetchCheckStats(days, bot, signal),
  });
}
