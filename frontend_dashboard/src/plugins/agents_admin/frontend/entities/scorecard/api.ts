import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiClient } from "@/shared/api";
import { checkStatsKeys } from "@plugins/agents_admin/frontend/entities/check-stats";

import {
  checkRegistrySchema,
  scorecardDetailSchema,
  scorecardListSchema,
} from "./contracts";
import { scorecardKeys } from "./keys";
import type { CheckRegistry, ScorecardDetail, ScorecardList } from "./model";

const BASE = "/api/agents/evals";

async function fetchRegistry(signal?: AbortSignal): Promise<CheckRegistry> {
  const raw = await apiClient.get<unknown>(`${BASE}/checks`, { signal });
  return checkRegistrySchema.parse(raw);
}

async function fetchScorecards(days: number, signal?: AbortSignal): Promise<ScorecardList> {
  const raw = await apiClient.get<unknown>(`${BASE}/scorecards?days=${days}`, { signal });
  return scorecardListSchema.parse(raw);
}

async function fetchScorecard(
  sessionId: string,
  episodeId: string,
  signal?: AbortSignal,
): Promise<ScorecardDetail> {
  const params = new URLSearchParams({ session_id: sessionId, episode_id: episodeId });
  const raw = await apiClient.get<unknown>(`${BASE}/scorecard?${params.toString()}`, { signal });
  return scorecardDetailSchema.parse(raw);
}

export interface RescoreInput {
  session_id: string;
  episode_id: string;
  judge: boolean;
}

async function postRescore(input: RescoreInput): Promise<ScorecardDetail> {
  const raw = await apiClient.post<unknown>(`${BASE}/scorecard/rescore`, input);
  return scorecardDetailSchema.parse(raw);
}

/** Registro de checks (cambia solo con deploy: cache larga). */
export function useCheckRegistry() {
  return useQuery({
    queryKey: scorecardKeys.registry(),
    queryFn: ({ signal }) => fetchRegistry(signal),
    staleTime: 5 * 60_000,
  });
}

/** Scorecards de los últimos `days` días (FALLA → ALERTA → PASA → SIN_DATOS, recientes primero). */
export function useScorecards(days = 30) {
  return useQuery({
    queryKey: scorecardKeys.list(days),
    queryFn: ({ signal }) => fetchScorecards(days, signal),
  });
}

/** Scorecard + trayectoria de un episodio — lazy: solo con selección. */
export function useScorecard(sessionId: string | null, episodeId: string) {
  return useQuery({
    queryKey: scorecardKeys.detail(sessionId ?? "", episodeId),
    queryFn: ({ signal }) => fetchScorecard(sessionId!, episodeId, signal),
    enabled: !!sessionId,
  });
}

/**
 * Recalcula el scorecard de un episodio (opcionalmente con el juez). Siembra el
 * detalle con la respuesta e invalida lista, detalle y agregados.
 */
export function useRescoreScorecard() {
  const qc = useQueryClient();
  return useMutation<ScorecardDetail, Error, RescoreInput>({
    mutationFn: postRescore,
    onSuccess: async (data, input) => {
      qc.setQueryData(scorecardKeys.detail(input.session_id, input.episode_id), data);
      await Promise.all([
        qc.invalidateQueries({ queryKey: scorecardKeys.lists() }),
        qc.invalidateQueries({ queryKey: scorecardKeys.detail(input.session_id, input.episode_id) }),
        qc.invalidateQueries({ queryKey: checkStatsKeys.all }),
      ]);
    },
  });
}
