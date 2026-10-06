import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiClient } from "@/shared/sdk";

import { decisionEngineSchema, rolloutSchema } from "./contracts";
import { rolloutKeys } from "./keys";
import type { CapabilityChange, DecisionEngine, Rollout, RolloutChange, WorkflowChange } from "./model";

/** Solo el cast propio (P-23): `/api/agents/*` → contrato de chats. */
const PATH = "/api/agents/perception/rollout";

async function fetchRollout(signal?: AbortSignal): Promise<Rollout> {
  return rolloutSchema.parse(await apiClient.get<unknown>(PATH, { signal }));
}

async function putRollout(change: RolloutChange): Promise<Rollout> {
  return rolloutSchema.parse(await apiClient.put<unknown>(PATH, change));
}

/** Motor de decisiones (F7): mismo cast, otros dos endpoints de chats. */
const CAPABILITIES_PATH = "/api/agents/perception/capabilities";
const WORKFLOW_PATH = "/api/agents/perception/workflow";

async function putCapability(change: CapabilityChange): Promise<Rollout> {
  return rolloutSchema.parse(await apiClient.put<unknown>(CAPABILITIES_PATH, change));
}

async function putWorkflow(change: WorkflowChange): Promise<Rollout> {
  return rolloutSchema.parse(await apiClient.put<unknown>(WORKFLOW_PATH, change));
}

export function usePerceptionRollout() {
  return useQuery({
    queryKey: rolloutKeys.current(),
    queryFn: ({ signal }) => fetchRollout(signal),
    staleTime: 30_000,
    // Red de seguridad: otro operador (u otra pestaña) puede haber movido el modo.
    refetchInterval: 60_000,
  });
}

/** Un error puede ser un 504 del cast ("PUEDE haberse aplicado"): se relee
 * el estado para que el panel nunca muestre un modo que ya no es. */
export function useSetPerceptionRollout() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: putRollout,
    onSuccess: (data) => client.setQueryData(rolloutKeys.current(), data),
    onError: () => client.invalidateQueries({ queryKey: rolloutKeys.current() }),
  });
}

/** Motor de decisiones (F7): el interruptor de una capacidad. Mismo manejo
 * de errores que el encendido (un 504 PUEDE haberse aplicado: se relee). */
export function useSetCapabilityMode() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: putCapability,
    onSuccess: (data) => {
      client.setQueryData(rolloutKeys.current(), data);
      // La pestaña del motor de decisiones dice quién decide cada cosa.
      void client.invalidateQueries({ queryKey: rolloutKeys.engine() });
    },
    onError: () => client.invalidateQueries({ queryKey: rolloutKeys.current() }),
  });
}

/** Motor de decisiones (F7): la versión del workflow de ventas. */
export function useSetWorkflowMode() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: putWorkflow,
    onSuccess: (data) => client.setQueryData(rolloutKeys.current(), data),
    onError: () => client.invalidateQueries({ queryKey: rolloutKeys.current() }),
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
    // Cambia con un deploy o con el interruptor de una capacidad.
    staleTime: 60_000,
  });
}
