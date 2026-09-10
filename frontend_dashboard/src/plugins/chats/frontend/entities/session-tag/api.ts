/**
 * Mutación "Reasignar tag" del inspector de Chats.
 *
 * Invalida `sessionKeys.detail(id)` + `sessionKeys.list()` al éxito: el tag
 * visible vive en ambos (bandeja + panel derecho) y el historial de estados
 * (`status_history`) se lee del detalle.
 */

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "@/shared/api/client";
import { sessionKeys } from "@plugins/chats/frontend/entities/session";
import {
  operatorTagResponseSchema,
  type OperatorTagResponse,
  type ReassignTagInput,
} from "./contracts";

export function useReassignTagMutation(sessionId: string | null) {
  const qc = useQueryClient();
  return useMutation<OperatorTagResponse, Error, ReassignTagInput>({
    mutationFn: async (input) => {
      if (!sessionId) throw new Error("No session selected");
      const raw = await apiClient.post<unknown>(
        `/api/chats/session-actions/${encodeURIComponent(sessionId)}/operator-tag`,
        input,
      );
      return operatorTagResponseSchema.parse(raw);
    },
    onSuccess: () => {
      if (sessionId) {
        qc.invalidateQueries({ queryKey: sessionKeys.detail(sessionId) });
        qc.invalidateQueries({ queryKey: sessionKeys.list() });
      }
    },
  });
}
