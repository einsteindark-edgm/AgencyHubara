import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiClient } from "@/shared/sdk";

import { rolloutSchema } from "./contracts";
import { rolloutKeys } from "./keys";
import type { Rollout, RolloutChange } from "./model";

/** Solo el cast propio (P-23): `/api/agents/*` → contrato de chats. */
const PATH = "/api/agents/perception/rollout";

async function fetchRollout(signal?: AbortSignal): Promise<Rollout> {
  return rolloutSchema.parse(await apiClient.get<unknown>(PATH, { signal }));
}

async function putRollout(change: RolloutChange): Promise<Rollout> {
  return rolloutSchema.parse(await apiClient.put<unknown>(PATH, change));
}

export function usePerceptionRollout() {
  return useQuery({
    queryKey: rolloutKeys.current(),
    queryFn: ({ signal }) => fetchRollout(signal),
    staleTime: 30_000,
  });
}

export function useSetPerceptionRollout() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: putRollout,
    onSuccess: (data) => client.setQueryData(rolloutKeys.current(), data),
  });
}
