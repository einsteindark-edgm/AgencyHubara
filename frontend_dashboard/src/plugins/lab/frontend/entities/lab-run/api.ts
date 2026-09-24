import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiClient } from "@/shared/sdk";

import {
  activeRunSchema,
  benchReportSchema,
  cancelResultSchema,
  conversationsSchema,
  estimateSchema,
  evaluationsSchema,
  launchResultSchema,
  armSummarySchema,
  runDiffSchema,
  runReportSchema,
  runsSchema,
  threadSchema,
  turnTraceSchema,
} from "./contracts";
import { labKeys } from "./keys";
import type { LaunchInput, TurnRef } from "./model";

/** Solo el cast propio (P-23): `/api/lab/*` → contrato `lab@v1` de chats. */
const BASE = "/api/lab";

function runBase(run: string): string {
  return `${BASE}/runs/${encodeURIComponent(run)}`;
}

function convBase(run: string, sid: string): string {
  return `${runBase(run)}/conversations/${encodeURIComponent(sid)}`;
}

async function fetchRuns(signal?: AbortSignal) {
  return runsSchema.parse(await apiClient.get<unknown>(`${BASE}/runs`, { signal }));
}

async function fetchActive(signal?: AbortSignal) {
  return activeRunSchema.parse(await apiClient.get<unknown>(`${BASE}/runs/active`, { signal }));
}

async function fetchEstimate(input: LaunchInput, signal?: AbortSignal) {
  const params = new URLSearchParams({ arms: input.arms.join(","), reps: String(input.reps), bench: input.bench });
  return estimateSchema.parse(await apiClient.get<unknown>(`${BASE}/estimate?${params.toString()}`, { signal }));
}

async function fetchBench(run: string, signal?: AbortSignal) {
  return benchReportSchema.parse(await apiClient.get<unknown>(`${runBase(run)}/bench`, { signal }));
}

async function fetchConversations(run: string, signal?: AbortSignal) {
  return conversationsSchema.parse(await apiClient.get<unknown>(`${runBase(run)}/conversations`, { signal }));
}

async function fetchThread(run: string, sid: string, episode: string | null, signal?: AbortSignal) {
  const query = episode ? `?${new URLSearchParams({ episode }).toString()}` : "";
  return threadSchema.parse(await apiClient.get<unknown>(`${convBase(run, sid)}${query}`, { signal }));
}

async function fetchTrace(ref: TurnRef, signal?: AbortSignal) {
  const params = new URLSearchParams({ turn_key: ref.turnKey, arm: ref.arm, rep: String(ref.rep) });
  return turnTraceSchema.parse(await apiClient.get<unknown>(`${convBase(ref.run, ref.sid)}/turns/trace?${params.toString()}`, { signal }));
}

async function fetchEvaluations(run: string, sid: string, arm: string, rep: number, signal?: AbortSignal) {
  const params = new URLSearchParams({ arm, rep: String(rep) });
  return evaluationsSchema.parse(await apiClient.get<unknown>(`${convBase(run, sid)}/evaluations?${params.toString()}`, { signal }));
}

async function postLaunch(input: LaunchInput) {
  return launchResultSchema.parse(await apiClient.post<unknown>(`${BASE}/runs`, input));
}

async function postCancel() {
  return cancelResultSchema.parse(await apiClient.post<unknown>(`${BASE}/runs/active/cancel`, {}));
}

/** Corridas publicadas (más recientes primero). */
export function useLabRuns() {
  return useQuery({
    queryKey: labKeys.runs(),
    queryFn: ({ signal }) => fetchRuns(signal),
    staleTime: 30_000,
  });
}

/**
 * La corrida en curso (o `null`). Una corrida dura horas: fallback lento de
 * 60 s (política realtime); lanzar o cancelar invalidan al instante.
 */
export function useActiveRun() {
  const client = useQueryClient();
  return useQuery({
    queryKey: labKeys.active(),
    queryFn: async ({ signal }) => {
      const before = client.getQueryData<{ active: unknown }>(labKeys.active());
      const now = await fetchActive(signal);
      // La corrida en curso terminó: la lista (fase, costo) se relee al instante.
      if (before?.active && !now.active) void client.invalidateQueries({ queryKey: labKeys.runs() });
      return now;
    },
    refetchInterval: 60_000,
    staleTime: 10_000,
  });
}

/** Costo estimado de la corrida que el operador está armando. */
export function useLabEstimate(input: LaunchInput, enabled: boolean) {
  return useQuery({
    queryKey: labKeys.estimate(input),
    queryFn: ({ signal }) => fetchEstimate(input, signal),
    enabled,
    staleTime: 60_000,
  });
}

export function useRunBench(run: string | null) {
  return useQuery({
    queryKey: labKeys.bench(run ?? ""),
    queryFn: ({ signal }) => fetchBench(run as string, signal),
    enabled: run !== null,
    staleTime: 5 * 60_000,
  });
}

export function useRunConversations(run: string | null) {
  return useQuery({
    queryKey: labKeys.conversations(run ?? ""),
    queryFn: ({ signal }) => fetchConversations(run as string, signal),
    enabled: run !== null,
    staleTime: 60_000,
  });
}

export function useRunThread(run: string | null, sid: string | null, episode: string | null = null) {
  return useQuery({
    queryKey: labKeys.thread(run ?? "", sid ?? "", episode),
    queryFn: ({ signal }) => fetchThread(run as string, sid as string, episode, signal),
    enabled: run !== null && sid !== null,
    staleTime: 5 * 60_000,
  });
}

export function useTurnTrace(ref: TurnRef | null) {
  return useQuery({
    queryKey: ref ? labKeys.trace(ref.run, ref.sid, ref.turnKey, ref.arm, ref.rep) : [...labKeys.all, "trace", "none"],
    queryFn: ({ signal }) => fetchTrace(ref as TurnRef, signal),
    enabled: ref !== null,
    staleTime: 5 * 60_000,
  });
}

export function useRunEvaluations(run: string | null, sid: string | null, arm: string, rep = 0) {
  return useQuery({
    queryKey: labKeys.evaluations(run ?? "", sid ?? "", arm, rep),
    queryFn: ({ signal }) => fetchEvaluations(run as string, sid as string, arm, rep, signal),
    enabled: run !== null && sid !== null,
    staleTime: 5 * 60_000,
  });
}

/** "Lanzar corrida": prende la caja del laboratorio (cuesta plata). */
async function fetchReport(run: string, signal?: AbortSignal) {
  return runReportSchema.parse(await apiClient.get<unknown>(`${runBase(run)}/report`, { signal }));
}

async function fetchSummary(run: string, arm: string, signal?: AbortSignal) {
  const query = new URLSearchParams({ arm }).toString();
  return armSummarySchema.parse(await apiClient.get<unknown>(`${runBase(run)}/summary?${query}`, { signal }));
}

async function fetchDiff(run: string, base: string, cand: string, signal?: AbortSignal) {
  const query = new URLSearchParams({ base, cand }).toString();
  return runDiffSchema.parse(await apiClient.get<unknown>(`${runBase(run)}/diff?${query}`, { signal }));
}

/** Lo que el Resumen muestra además de las gráficas (fidelidad, validación, arena). */
export function useRunReport(run: string | null) {
  return useQuery({
    queryKey: labKeys.report(run ?? ""),
    queryFn: ({ signal }) => fetchReport(run as string, signal),
    enabled: Boolean(run),
    staleTime: 60_000,
    retry: false,
  });
}

/** Las gráficas de Calidad LLM de un brazo de la corrida. */
export function useRunSummary(run: string | null, arm: string) {
  return useQuery({
    queryKey: labKeys.summary(run ?? "", arm),
    queryFn: ({ signal }) => fetchSummary(run as string, arm, signal),
    enabled: Boolean(run),
    staleTime: 60_000,
    retry: false,
  });
}

/** Diferencia pareada entre dos brazos, con intervalo y turnos que cambiaron. */
export function useRunDiff(run: string | null, base: string, cand: string) {
  return useQuery({
    queryKey: labKeys.diff(run ?? "", base, cand),
    queryFn: ({ signal }) => fetchDiff(run as string, base, cand, signal),
    enabled: Boolean(run) && base !== cand,
    staleTime: 60_000,
    retry: false,
  });
}

export function useLaunchRun() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: postLaunch,
    onSettled: () => {
      void client.invalidateQueries({ queryKey: labKeys.active() });
      void client.invalidateQueries({ queryKey: labKeys.runs() });
    },
  });
}

export function useCancelRun() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: postCancel,
    onSettled: () => {
      void client.invalidateQueries({ queryKey: labKeys.active() });
      void client.invalidateQueries({ queryKey: labKeys.runs() });
    },
  });
}
