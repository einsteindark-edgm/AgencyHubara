/**
 * Mutaciones "Posponer" / "Quitar pospuesto" del inspector de Chats.
 *
 * Invalidan `sessionKeys.list()` (la fila entra o sale del filtro
 * "Pospuestos") y `sessionKeys.detail(id)`.
 */

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "@/shared/api/client";
import { sessionKeys } from "@plugins/chats/frontend/entities/session";
import {
  postponeResponseSchema,
  type PostponeInput,
  type PostponeResponse,
} from "./contracts";

function postponeUrl(sessionId: string): string {
  return `/api/chats/session-actions/${encodeURIComponent(sessionId)}/postpone`;
}

function useInvalidateSession(sessionId: string | null) {
  const qc = useQueryClient();
  return () => {
    if (!sessionId) return;
    qc.invalidateQueries({ queryKey: sessionKeys.detail(sessionId) });
    qc.invalidateQueries({ queryKey: sessionKeys.list() });
  };
}

export function usePostponeMutation(sessionId: string | null) {
  const invalidate = useInvalidateSession(sessionId);
  return useMutation<PostponeResponse, Error, PostponeInput>({
    mutationFn: async (input) => {
      if (!sessionId) throw new Error("No session selected");
      const raw = await apiClient.post<unknown>(postponeUrl(sessionId), input);
      return postponeResponseSchema.parse(raw);
    },
    onSuccess: invalidate,
  });
}

export function useClearPostponeMutation(sessionId: string | null) {
  const invalidate = useInvalidateSession(sessionId);
  return useMutation<PostponeResponse, Error, void>({
    mutationFn: async () => {
      if (!sessionId) throw new Error("No session selected");
      const raw = await apiClient.delete<unknown>(postponeUrl(sessionId));
      return postponeResponseSchema.parse(raw);
    },
    onSuccess: invalidate,
  });
}
