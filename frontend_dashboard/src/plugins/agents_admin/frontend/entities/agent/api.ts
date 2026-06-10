/**
 * Datos de los agentes reales del sistema. Fetch a `GET /api/agents`, que
 * descubre los agentes de los manifests y devuelve el contenido VIVO de sus
 * workspaces (IDENTITY/SOUL/AGENTS/USER/TOOLS.md). Validado con Zod en el
 * boundary HTTP. Sin mocks.
 */
import { useQuery } from "@tanstack/react-query";
import { apiClient } from "@/shared/api/client";
import { agentKeys } from "./keys";
import { agentsListResponseSchema } from "./contracts";
import type { Agent } from "./model";

async function fetchAgents(): Promise<Agent[]> {
  const raw = await apiClient.get<unknown>("/api/agents");
  return agentsListResponseSchema.parse(raw).agents;
}

export function useAgents() {
  return useQuery({
    queryKey: agentKeys.list(),
    queryFn: fetchAgents,
    staleTime: 5 * 60 * 1000,
  });
}
