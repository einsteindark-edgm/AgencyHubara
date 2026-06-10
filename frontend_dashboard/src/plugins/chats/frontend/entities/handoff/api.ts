/**
 * Hooks de mutación para human handoff. Cada uno invalida el cache
 * `sessionKeys.detail(id)` al éxito para que el panel central se refresque
 * y refleje el cambio de ruta / nuevos mensajes.
 *
 * No usamos `useQuery`: son acciones imperativas del operador (Intervenir,
 * Enviar, Devolver), no estado a leer.
 */

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "@/shared/api/client";
import { sessionKeys } from "@plugins/chats/frontend/entities/session";
import {
  handoffResponseSchema,
  humanMessageResponseSchema,
  type HandoffResponse,
  type HumanMessageResponse,
  type ReturnToBotInput,
} from "./contracts";

/** Toma el control de la conversación. Idempotente desde el servidor. */
export function useInterveneMutation(sessionId: string | null) {
  const qc = useQueryClient();
  return useMutation<HandoffResponse, Error, { motivo?: string }>({
    mutationFn: async ({ motivo }) => {
      if (!sessionId) throw new Error("No session selected");
      const raw = await apiClient.post<unknown>(
        `/api/dashboard/sessions/${sessionId}/intervene`,
        { motivo },
      );
      return handoffResponseSchema.parse(raw);
    },
    onSuccess: () => {
      if (sessionId) {
        qc.invalidateQueries({ queryKey: sessionKeys.detail(sessionId) });
        qc.invalidateQueries({ queryKey: sessionKeys.list() });
      }
    },
  });
}

/** Manda un mensaje del humano al cliente vía WhatsApp + persiste al JSONL.
 *
 * El backend rechaza con 409 si la sesión no está en ruta humano — el
 * componente debe ocultar/desactivar el composer cuando route !== "humano",
 * pero la mutación maneja el error igual para evitar inputs concurrentes.
 */
export function useSendHumanMessageMutation(sessionId: string | null) {
  const qc = useQueryClient();
  return useMutation<HumanMessageResponse, Error, { text: string }>({
    mutationFn: async ({ text }) => {
      if (!sessionId) throw new Error("No session selected");
      const raw = await apiClient.post<unknown>(
        `/api/dashboard/sessions/${sessionId}/messages`,
        { text },
      );
      return humanMessageResponseSchema.parse(raw);
    },
    onSuccess: () => {
      if (sessionId) {
        // Invalida sólo el detail para refetchear el JSONL con el mensaje nuevo.
        qc.invalidateQueries({ queryKey: sessionKeys.detail(sessionId) });
      }
    },
  });
}

/** Devuelve el control al bot. Para "remarketing" requiere motivo (el
 *  workflow lo usa para construir el gancho). */
export function useReturnToBotMutation(sessionId: string | null) {
  const qc = useQueryClient();
  return useMutation<HandoffResponse, Error, ReturnToBotInput>({
    mutationFn: async (input) => {
      if (!sessionId) throw new Error("No session selected");
      const raw = await apiClient.post<unknown>(
        `/api/dashboard/sessions/${sessionId}/return-to-bot`,
        input,
      );
      return handoffResponseSchema.parse(raw);
    },
    onSuccess: () => {
      if (sessionId) {
        qc.invalidateQueries({ queryKey: sessionKeys.detail(sessionId) });
        qc.invalidateQueries({ queryKey: sessionKeys.list() });
      }
    },
  });
}
