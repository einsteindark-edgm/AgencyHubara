import { useQuery } from "@tanstack/react-query";

import { apiClient } from "@/shared/api";

import { evalTrendSchema } from "./contracts";
import { evalTrendKeys } from "./keys";
import type { EvalTrend } from "./model";

async function fetchTrend(days: number, suite: string): Promise<EvalTrend> {
  const raw = await apiClient.get<unknown>(
    `/api/chats/evals/history?days=${days}&suite=${encodeURIComponent(suite)}`,
  );
  return evalTrendSchema.parse(raw);
}

/** Tendencia de los scores de evaluación por métrica (últimos `days` días). */
export function useEvalTrend(days = 30, suite = "online") {
  return useQuery({
    queryKey: evalTrendKeys.trend(days, suite),
    queryFn: () => fetchTrend(days, suite),
  });
}
