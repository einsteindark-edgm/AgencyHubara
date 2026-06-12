/**
 * Hooks de data-fetching del dominio "sesión".
 *
 * F1 (auditoría 2026-06-10): cero polling propio. El stream multiplexado
 * (`useDashboardEvents`, una sola conexión app-level) empuja:
 *   - `chats.sessions_snapshot` → `setQueryData` de la lista (sin refetch)
 *   - `chats.session_updated(id)` → invalida SOLO ese detalle
 * `refetchInterval` queda únicamente como fallback lento (5 min) por si el
 * stream estuviera caído — la reconexión ya reconcilia vía
 * `useInvalidateOnReconnect`.
 */

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback } from "react";
import { apiClient } from "@/shared/api/client";
import {
  useDashboardEvents,
  useInvalidateOnReconnect,
} from "@/shared/api/events";
import { sessionKeys } from "./keys";
import {
  sessionDetailsSchema,
  sessionsListResponseSchema,
} from "./contracts";
import type { ChatSession, SessionDetails } from "./model";

// Fallback, NO el mecanismo primario (eso es el push del stream).
const SESSION_DETAIL_FALLBACK_REFETCH_MS = 5 * 60_000;

async function fetchSessions(signal?: AbortSignal): Promise<ChatSession[]> {
  const raw = await apiClient.get<unknown>("/api/dashboard/sessions", {
    signal,
  });
  return sessionsListResponseSchema.parse(raw).sessions;
}

async function fetchSessionDetail(
  id: string,
  signal?: AbortSignal,
): Promise<SessionDetails> {
  const raw = await apiClient.get<unknown>(`/api/dashboard/sessions/${id}`, {
    signal,
  });
  return sessionDetailsSchema.parse(raw);
}

/** Lista paginada de sesiones. Se actualiza vía `useSessionsStream()`. */
export function useSessions() {
  return useQuery({
    queryKey: sessionKeys.list(),
    queryFn: ({ signal }) => fetchSessions(signal),
  });
}

/**
 * Detalle de una sesión. `enabled` se desactiva si `id` es null para evitar
 * un fetch al string vacío. La actualización primaria llega por el evento
 * `chats.session_updated(id)` (ver `useSessionsStream`); el interval es solo
 * red de seguridad. (Antes: poll duro cada 3s.)
 */
export function useSession(id: string | null) {
  return useQuery({
    queryKey: sessionKeys.detail(id ?? ""),
    queryFn: ({ signal }) => fetchSessionDetail(id!, signal),
    enabled: !!id,
    refetchInterval: SESSION_DETAIL_FALLBACK_REFETCH_MS,
  });
}

/**
 * Suscripción del plugin chats al stream multiplexado del dashboard.
 * Montar UNA sola vez por sección (lo hace ChatsSection):
 *   - snapshot → reemplaza el cache de la lista (structural sharing evita
 *     re-renders si nada cambió)
 *   - session_updated → invalida el detalle puntual
 *   - reconexión → invalida todo el dominio (gap de eventos perdidos)
 */
export function useSessionsStream() {
  const qc = useQueryClient();

  useDashboardEvents(
    "chats",
    useCallback(
      (event) => {
        if (event.type === "sessions_snapshot") {
          const parsed = sessionsListResponseSchema.safeParse(event.payload);
          if (!parsed.success) {
            console.warn("snapshot de sesiones inválido", parsed.error);
            return;
          }
          qc.setQueryData(sessionKeys.list(), parsed.data.sessions);
        } else if (event.type === "session_updated" && event.id) {
          qc.invalidateQueries({ queryKey: sessionKeys.detail(event.id) });
        }
      },
      [qc],
    ),
  );

  useInvalidateOnReconnect(
    useCallback(
      () => qc.invalidateQueries({ queryKey: sessionKeys.all }),
      [qc],
    ),
  );
}
