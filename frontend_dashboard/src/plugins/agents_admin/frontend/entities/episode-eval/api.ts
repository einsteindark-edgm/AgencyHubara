import { useQuery } from "@tanstack/react-query";

import { apiClient } from "@/shared/api";

import { conversationEvalsSchema, evalTranscriptSchema } from "./contracts";
import { episodeEvalKeys } from "./keys";
import type { ConversationEvals, EvalTranscript } from "./model";

async function fetchConversations(
  days: number,
  suite: string,
): Promise<ConversationEvals> {
  const raw = await apiClient.get<unknown>(
    `/api/agents/evals/conversations?days=${days}&suite=${encodeURIComponent(suite)}`,
  );
  return conversationEvalsSchema.parse(raw);
}

async function fetchTranscript(
  sessionId: string,
  episodeId: string,
): Promise<EvalTranscript> {
  const params = new URLSearchParams({
    session_id: sessionId,
    episode_id: episodeId,
  });
  const raw = await apiClient.get<unknown>(
    `/api/agents/evals/transcript?${params.toString()}`,
  );
  return evalTranscriptSchema.parse(raw);
}

/** Evaluaciones agrupadas por conversación (sesión + episodio), últimos `days` días. */
export function useConversationEvals(days = 7, suite = "online") {
  return useQuery({
    queryKey: episodeEvalKeys.list(days, suite),
    queryFn: () => fetchConversations(days, suite),
  });
}

/** Transcript del episodio evaluado — lazy: solo cuando hay selección. */
export function useEvalTranscript(
  sessionId: string | null,
  episodeId: string,
) {
  return useQuery({
    queryKey: episodeEvalKeys.transcript(sessionId ?? "", episodeId),
    queryFn: () => fetchTranscript(sessionId!, episodeId),
    enabled: !!sessionId,
  });
}
