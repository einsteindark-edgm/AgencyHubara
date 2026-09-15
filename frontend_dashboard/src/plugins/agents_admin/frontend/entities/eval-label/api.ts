import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiClient } from "@/shared/api";

import {
  calibrationSchema,
  createLabelResponseSchema,
  labelQueueSchema,
  labelsListSchema,
} from "./contracts";
import { evalLabelKeys } from "./keys";
import type {
  Calibration,
  CreateLabelInput,
  CreateLabelResponse,
  LabelQueue,
  LabelsList,
} from "./model";

const BASE = "/api/agents/evals";

async function fetchLabels(
  sessionId: string,
  episodeId: string,
  signal?: AbortSignal,
): Promise<LabelsList> {
  const params = new URLSearchParams({ session_id: sessionId, episode_id: episodeId });
  const raw = await apiClient.get<unknown>(`${BASE}/labels?${params.toString()}`, { signal });
  return labelsListSchema.parse(raw);
}

async function fetchQueue(days: number, limit: number, signal?: AbortSignal): Promise<LabelQueue> {
  const raw = await apiClient.get<unknown>(`${BASE}/labels/queue?days=${days}&limit=${limit}`, {
    signal,
  });
  return labelQueueSchema.parse(raw);
}

async function fetchCalibration(signal?: AbortSignal): Promise<Calibration> {
  const raw = await apiClient.get<unknown>(`${BASE}/calibration`, { signal });
  return calibrationSchema.parse(raw);
}

async function postLabel(input: CreateLabelInput): Promise<CreateLabelResponse> {
  const raw = await apiClient.post<unknown>(`${BASE}/labels`, input);
  return createLabelResponseSchema.parse(raw);
}

/** Etiquetas humanas de un episodio — lazy: solo con episodio seleccionado. */
export function useEvalLabels(sessionId: string | null, episodeId: string) {
  return useQuery({
    queryKey: evalLabelKeys.labels(sessionId ?? "", episodeId),
    queryFn: ({ signal }) => fetchLabels(sessionId!, episodeId, signal),
    enabled: !!sessionId,
  });
}

/** Cola de etiquetado: veredictos del juez que conviene revisar a mano. */
export function useLabelQueue(days = 30, limit = 20) {
  return useQuery({
    queryKey: evalLabelKeys.queue(days, limit),
    queryFn: ({ signal }) => fetchQueue(days, limit, signal),
  });
}

/** Calibración del juez por check (TPR/TNR/kappa contra las etiquetas humanas). */
export function useJudgeCalibration() {
  return useQuery({
    queryKey: evalLabelKeys.calibration(),
    queryFn: ({ signal }) => fetchCalibration(signal),
  });
}

/** Crea una etiqueta humana; invalida etiquetas, cola y calibración. */
export function useCreateLabel() {
  const qc = useQueryClient();
  return useMutation<CreateLabelResponse, Error, CreateLabelInput>({
    mutationFn: postLabel,
    onSuccess: () => qc.invalidateQueries({ queryKey: evalLabelKeys.all }),
  });
}
