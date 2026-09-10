/**
 * Hooks de `mba-sync` (D2.2) contra `/api/mba/agents/{id}/sync*`:
 *   - estado del último sync (vault; `state: null` antes del primero),
 *   - plan de solo lectura (lee Meta + diff), a pedido (`enabled`),
 *   - apply, confirmado con el `fingerprint` del plan que el operador vio.
 * Server state SOLO en TanStack Query; nada se copia a useState.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "@/shared/api/client";
import { mbaConfigKeys } from "@plugins/mba/frontend/entities/mba-config/keys";
import { mbaSyncOutcomeSchema, mbaSyncPlanSchema, mbaSyncStateSchema } from "./contracts";
import { mbaSyncKeys } from "./keys";
import type { MbaSyncOutcome, MbaSyncPlan, MbaSyncState } from "./model";

const base = (agentId: string) => `/api/mba/agents/${encodeURIComponent(agentId)}/sync`;

export function useMbaSyncState(agentId: string) {
  return useQuery<MbaSyncState>({
    queryKey: mbaSyncKeys.state(agentId),
    queryFn: async ({ signal }) => mbaSyncStateSchema.parse(await apiClient.get<unknown>(base(agentId), { signal })),
    enabled: Boolean(agentId),
    staleTime: 30_000,
  });
}

export function useMbaSyncPlan(agentId: string, enabled: boolean) {
  return useQuery<MbaSyncPlan>({
    queryKey: mbaSyncKeys.plan(agentId),
    queryFn: async ({ signal }) =>
      mbaSyncPlanSchema.parse(await apiClient.get<unknown>(`${base(agentId)}/plan`, { signal })),
    enabled: enabled && Boolean(agentId),
    // Un plan es una foto de Meta: siempre se relee al volver a pedirlo, pero
    // NUNCA por debajo del operador (un refetch al volver a la ventana
    // cambiaría la lista y el fingerprint bajo el botón "Confirmar").
    staleTime: 0,
    gcTime: 0,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    retry: false,
  });
}

export function useApplyMbaSync(agentId: string) {
  const qc = useQueryClient();
  return useMutation<MbaSyncOutcome, Error, { fingerprint: string }>({
    mutationFn: async ({ fingerprint }) =>
      mbaSyncOutcomeSchema.parse(await apiClient.post<unknown>(base(agentId), { fingerprint })),
    onSettled: () => {
      qc.invalidateQueries({ queryKey: mbaSyncKeys.state(agentId) });
      qc.invalidateQueries({ queryKey: mbaSyncKeys.plan(agentId) });
      qc.invalidateQueries({ queryKey: mbaConfigKeys.detail(agentId) });
    },
  });
}
