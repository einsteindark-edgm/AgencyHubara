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

/** Qué bot respondió los episodios (encendido del bot nuevo, lab PR 18). */
export type ScorecardBot = "actual" | "nuevo";

async function fetchScorecards(days: number, bot: ScorecardBot | null, signal?: AbortSignal): Promise<ScorecardList> {
  const query = bot ? `days=${days}&bot=${bot}` : `days=${days}`;
  const raw = await apiClient.get<unknown>(`${BASE}/scorecards?${query}`, { signal });
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
export function useScorecards(days = 30, bot: ScorecardBot | null = null) {
  return useQuery({
    queryKey: scorecardKeys.list(days, bot),
    queryFn: ({ signal }) => fetchScorecards(days, bot, signal),
    // Lista pesada (semanas de episodios × checks): no se recarga con cada foco.
    staleTime: 60_000,
  });
}

/**
 * Scorecard + trayectoria de un episodio — lazy: solo con selección.
 * `pollIntervalMs` decide, con los datos actuales, si seguir sondeando el
 * detalle (p. ej. mientras el juez corre en el worker tras un recálculo
 * encolado): devuelve los ms del siguiente sondeo o `false` para parar.
 */
export function useScorecard(
  sessionId: string | null,
  episodeId: string,
  opts: { pollIntervalMs?: (current: ScorecardDetail | undefined) => number | false } = {},
) {
  const { pollIntervalMs } = opts;
  return useQuery({
    queryKey: scorecardKeys.detail(sessionId ?? "", episodeId),
    queryFn: ({ signal }) => fetchScorecard(sessionId!, episodeId, signal),
    enabled: !!sessionId,
    refetchInterval: pollIntervalMs ? (query) => pollIntervalMs(query.state.data) : false,
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
