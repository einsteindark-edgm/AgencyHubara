/**
 * Configuración Meta Business Agent normalizada de un agente. Fetch a
 * `GET /api/agents/{agent_id}/mba-config` (solo lectura: el backend deriva la
 * config del workspace REAL del agente; no llama a Meta). Validado con Zod en
 * el boundary HTTP.
 */
import { useQuery } from "@tanstack/react-query";
import { apiClient } from "@/shared/api/client";
import { mbaConfigKeys } from "./keys";
import { mbaConfigSchema } from "./contracts";
import type { MbaConfig } from "./model";

async function fetchMbaConfig(agentId: string, signal?: AbortSignal): Promise<MbaConfig> {
  const raw = await apiClient.get<unknown>(
    `/api/agents/${encodeURIComponent(agentId)}/mba-config`,
    { signal },
  );
  return mbaConfigSchema.parse(raw);
}

export function useMbaConfig(agentId: string) {
  return useQuery({
    queryKey: mbaConfigKeys.detail(agentId),
    queryFn: ({ signal }) => fetchMbaConfig(agentId, signal),
    enabled: Boolean(agentId),
    staleTime: 5 * 60 * 1000,
  });
}
