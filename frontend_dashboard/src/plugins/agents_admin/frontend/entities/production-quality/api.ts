import { useQuery } from "@tanstack/react-query";

import { apiClient } from "@/shared/api";

import {
  conversationsSchema,
  engineDecisionsSchema,
  evaluationsSchema,
  jevReportSchema,
  threadSchema,
  turnTraceSchema,
} from "./contracts";
import { productionQualityKeys } from "./keys";
import type {
  JevReport,
  QualityBot,
  QualityConversations,
  QualityEngineDecision,
  QualityEvaluations,
  QualityThread,
  QualityTurnTrace,
} from "./model";

const BASE = "/api/agents/evals/production";

async function fetchConversations(days: number, bot: QualityBot | null, signal?: AbortSignal): Promise<QualityConversations> {
  const query = bot ? `days=${days}&bot=${bot}` : `days=${days}`;
  const raw = await apiClient.get<unknown>(`${BASE}/conversations?${query}`, { signal });
  return conversationsSchema.parse(raw);
}

async function fetchThread(sid: string, signal?: AbortSignal): Promise<QualityThread> {
  const raw = await apiClient.get<unknown>(`${BASE}/conversations/${encodeURIComponent(sid)}`, { signal });
  return threadSchema.parse(raw);
}

async function fetchTurnTrace(sid: string, turnKey: string, signal?: AbortSignal): Promise<QualityTurnTrace> {
  const query = new URLSearchParams({ turn_key: turnKey }).toString();
  const raw = await apiClient.get<unknown>(`${BASE}/conversations/${encodeURIComponent(sid)}/turns/trace?${query}`, { signal });
  return turnTraceSchema.parse(raw);
}

async function fetchEvaluations(sid: string, signal?: AbortSignal): Promise<QualityEvaluations> {
  const raw = await apiClient.get<unknown>(`${BASE}/conversations/${encodeURIComponent(sid)}/evaluations`, { signal });
  return evaluationsSchema.parse(raw);
}

async function fetchJevReport(days: number, bot: QualityBot, signal?: AbortSignal): Promise<JevReport> {
  const raw = await apiClient.get<unknown>(`${BASE}/jev?days=${days}&bot=${bot}`, { signal });
  return jevReportSchema.parse(raw);
}

/** Las conversaciones calificadas de la ventana (veredicto y bot por episodio). */
export function useQualityConversations(days: number, bot: QualityBot | null) {
  return useQuery({
    queryKey: productionQualityKeys.conversations(days, bot),
    queryFn: ({ signal }) => fetchConversations(days, bot, signal),
    // Lista pesada (lee las trazas de cada conversación): no se recarga con cada foco.
    staleTime: 60_000,
  });
}

/** El hilo de una conversación: sus mensajes y sus turnos. */
export function useQualityThread(sid: string | null) {
  return useQuery({
    queryKey: productionQualityKeys.thread(sid ?? ""),
    queryFn: ({ signal }) => fetchThread(sid as string, signal),
    enabled: sid !== null,
  });
}

/** El paso a paso de un turno y las decisiones de Jev. */
export function useQualityTurnTrace(sid: string, turnKey: string) {
  return useQuery({
    queryKey: productionQualityKeys.trace(sid, turnKey),
    queryFn: ({ signal }) => fetchTurnTrace(sid, turnKey, signal),
  });
}

/** Cada episodio de la conversación, calificado turno por turno. */
export function useQualityEvaluations(sid: string | null) {
  return useQuery({
    queryKey: productionQualityKeys.evaluations(sid ?? ""),
    queryFn: ({ signal }) => fetchEvaluations(sid as string, signal),
    enabled: sid !== null,
    staleTime: 60_000,
  });
}

/** El informe de Jev de los turnos reales de la ventana (por defecto, del bot Jev). */
export function useJevReport(days: number, bot: QualityBot = "nuevo") {
  return useQuery({
    queryKey: productionQualityKeys.jev(days, bot),
    queryFn: ({ signal }) => fetchJevReport(days, bot, signal),
    staleTime: 60_000,
  });
}

/** Las decisiones de Jev de la traza de un turno; las que no tienen forma se descartan. */
export function engineDecisionsOf(trace: Record<string, unknown>): QualityEngineDecision[] {
  return engineDecisionsSchema.parse(trace.decisions);
}
