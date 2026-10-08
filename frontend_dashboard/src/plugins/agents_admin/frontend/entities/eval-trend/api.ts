import { useQuery } from "@tanstack/react-query";

import { apiClient } from "@/shared/api";


import { evalTrendSchema } from "./contracts";
import { evalTrendKeys } from "./keys";
import type { EvalTrend, TrendBot } from "./model";

async function fetchTrend(
  days: number,
  suite: string,
  bot: TrendBot | null,
  signal?: AbortSignal,
): Promise<EvalTrend> {
  const query = `days=${days}&suite=${encodeURIComponent(suite)}${bot ? `&bot=${bot}` : ""}`;
  const raw = await apiClient.get<unknown>(`/api/agents/evals/history?${query}`, { signal });
  return evalTrendSchema.parse(raw);
}

/** Tendencia de los scores de evaluación por métrica (últimos `days` días);
 *  `bot` deja solo los episodios de ese bot (Botsito `actual`, Colossus `nuevo`). */
export function useEvalTrend(days = 30, suite = "online", bot: TrendBot | null = null) {
  return useQuery({
    queryKey: evalTrendKeys.trend(days, suite, bot),
    queryFn: ({ signal }) => fetchTrend(days, suite, bot, signal),
  });
}
