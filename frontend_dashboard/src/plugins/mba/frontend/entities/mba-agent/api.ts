/**
 * Agentes MBA autorados. Fetch a `GET /api/mba/agents` (solo lectura),
 * validado con Zod en el boundary HTTP.
 */
import { useQuery } from "@tanstack/react-query";
import { apiClient } from "@/shared/api/client";
import { mbaAgentKeys } from "./keys";
import { mbaAgentsResponseSchema } from "./contracts";
import type { MbaAgent } from "./model";

async function fetchMbaAgents(signal?: AbortSignal): Promise<MbaAgent[]> {
  const raw = await apiClient.get<unknown>("/api/mba/agents", { signal });
  return mbaAgentsResponseSchema.parse(raw).agents;
}

export function useMbaAgents() {
  return useQuery({
    queryKey: mbaAgentKeys.list(),
    queryFn: ({ signal }) => fetchMbaAgents(signal),
    staleTime: 5 * 60 * 1000,
  });
}
