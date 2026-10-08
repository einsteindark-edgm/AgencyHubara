import { useQuery } from "@tanstack/react-query";

import { apiClient } from "@/shared/api";

import { conversationEvalsSchema, evalTranscriptSchema } from "./contracts";
import { episodeEvalKeys } from "./keys";
import type { ConversationEvals, EvalTranscript } from "./model";

async function fetchConversations(
  days: number,
  suite: string,
  bot: "actual" | "nuevo" | null,
  signal?: AbortSignal,
): Promise<ConversationEvals> {
  const query = `days=${days}&suite=${encodeURIComponent(suite)}${bot ? `&bot=${bot}` : ""}`;
  const raw = await apiClient.get<unknown>(`/api/agents/evals/conversations?${query}`, { signal });
  return conversationEvalsSchema.parse(raw);
}

async function fetchTranscript(
  sessionId: string,
  episodeId: string,
  signal?: AbortSignal,
): Promise<EvalTranscript> {
  const params = new URLSearchParams({
    session_id: sessionId,
    episode_id: episodeId,
  });
  const raw = await apiClient.get<unknown>(
    `/api/agents/evals/transcript?${params.toString()}`,
    { signal },
  );
  return evalTranscriptSchema.parse(raw);
}

/** Evaluaciones agrupadas por conversación (sesión + episodio), últimos `days`
 *  días; `bot` deja solo los episodios de ese bot. */
export function useConversationEvals(days = 7, suite = "online", bot: "actual" | "nuevo" | null = null) {
  return useQuery({
    queryKey: episodeEvalKeys.list(days, suite, bot),
    queryFn: ({ signal }) => fetchConversations(days, suite, bot, signal),
  });
}

/** Transcript del episodio evaluado — lazy: solo cuando hay selección. */
export function useEvalTranscript(
  sessionId: string | null,
  episodeId: string,
) {
  return useQuery({
    queryKey: episodeEvalKeys.transcript(sessionId ?? "", episodeId),
    queryFn: ({ signal }) => fetchTranscript(sessionId!, episodeId, signal),
    enabled: !!sessionId,
  });
}
