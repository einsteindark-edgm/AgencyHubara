/**
 * Hooks de `mba-rollout` (D2.3) contra `/api/mba/agents/{id}/rollout*`.
 * Estado (settings + allowlist + readiness) en TanStack Query; cada escritura
 * es una mutation que invalida el estado y el sync (comparten el vault).
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "@/shared/api/client";
import { mbaSyncKeys } from "@plugins/mba/frontend/entities/mba-sync/keys";
import { mbaRolloutOutcomeSchema, mbaRolloutStatusSchema } from "./contracts";
import { mbaRolloutKeys } from "./keys";
import type { MbaAudience, MbaRolloutOutcome, MbaRolloutStatus } from "./model";

const base = (agentId: string) => `/api/mba/agents/${encodeURIComponent(agentId)}/rollout`;

export function useMbaRollout(agentId: string) {
  return useQuery<MbaRolloutStatus>({
    queryKey: mbaRolloutKeys.status(agentId),
    queryFn: async ({ signal }) => mbaRolloutStatusSchema.parse(await apiClient.get<unknown>(base(agentId), { signal })),
    enabled: Boolean(agentId),
    staleTime: 30_000,
    retry: false,
  });
}

function useRolloutMutation<V>(agentId: string, run: (v: V) => Promise<unknown>) {
  const qc = useQueryClient();
  return useMutation<MbaRolloutOutcome, Error, V>({
    mutationFn: async (v) => mbaRolloutOutcomeSchema.parse(await run(v)),
    onSettled: () => {
      qc.invalidateQueries({ queryKey: mbaRolloutKeys.status(agentId) });
      qc.invalidateQueries({ queryKey: mbaSyncKeys.state(agentId) });
    },
  });
}

export function useAddRolloutPhone(agentId: string) {
  return useRolloutMutation<{ phone: string }>(agentId, ({ phone }) =>
    apiClient.post<unknown>(`${base(agentId)}/allowlist`, { phone }),
  );
}

export function useRemoveRolloutPhone(agentId: string) {
  return useRolloutMutation<{ entryId: string }>(agentId, ({ entryId }) =>
    apiClient.delete<unknown>(`${base(agentId)}/allowlist/${encodeURIComponent(entryId)}`),
  );
}

export function useSetRolloutAudience(agentId: string) {
  return useRolloutMutation<{ audience: MbaAudience; confirm: boolean }>(agentId, ({ audience, confirm }) =>
    apiClient.put<unknown>(`${base(agentId)}/audience`, { ai_audience: audience, confirm }),
  );
}

export function useSetRolloutEnabled(agentId: string) {
  return useRolloutMutation<{ enabled: boolean; confirm?: boolean }>(agentId, ({ enabled, confirm }) =>
    apiClient.put<unknown>(`${base(agentId)}/enabled`, confirm === undefined ? { enabled } : { enabled, confirm }),
  );
}
