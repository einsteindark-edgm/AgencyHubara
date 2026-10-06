import { useQuery } from "@tanstack/react-query";

import { apiClient } from "@/shared/sdk";

import { decisionEngineSchema, rolloutSchema } from "./contracts";
import { rolloutKeys } from "./keys";
import type { DecisionEngine, Rollout } from "./model";

/** Solo el cast propio (P-23): `/api/agents/*` → contrato de chats. Solo
 * lectura: desde el 2026-10-06 el bot nuevo se cambia por comando
 * (`chats/agent/sales/decisions/control.py`), nunca desde el dashboard. */
const PATH = "/api/agents/perception/rollout";

async function fetchRollout(signal?: AbortSignal): Promise<Rollout> {
  return rolloutSchema.parse(await apiClient.get<unknown>(PATH, { signal }));
}

export function usePerceptionRollout() {
  return useQuery({
    queryKey: rolloutKeys.current(),
    queryFn: ({ signal }) => fetchRollout(signal),
    staleTime: 30_000,
    // Red de seguridad: un comando puede haber movido el modo.
    refetchInterval: 60_000,
  });
}

/** El motor de decisiones de la tienda: el paquete que corre (su versión), el
 * oráculo, y cada decisión con dónde actúa, qué resuelve y quién la decide hoy. */
const ENGINE_PATH = "/api/agents/perception/engine";

async function fetchDecisionEngine(signal?: AbortSignal): Promise<DecisionEngine> {
  return decisionEngineSchema.parse(await apiClient.get<unknown>(ENGINE_PATH, { signal }));
}

export function useDecisionEngine() {
  return useQuery({
    queryKey: rolloutKeys.engine(),
    queryFn: ({ signal }) => fetchDecisionEngine(signal),
    // Cambia con un deploy o con un comando que mueve una capacidad.
    staleTime: 60_000,
  });
}
