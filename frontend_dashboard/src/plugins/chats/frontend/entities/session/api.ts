/**
 * Hooks de data-fetching del dominio "sesión".
 *
 * Win arquitectónico vs. el código legado:
 *   - `useSession(id)` es UNO solo — CenterCol y RightCol comparten el cache
 *     vía `queryKey`, eliminando el doble fetch cada 3s al mismo endpoint.
 *   - El polling se declara en el hook, no en cada `useEffect` por componente.
 *   - `useSessionsStream()` empuja el snapshot SSE al cache vía `setQueryData`,
 *     evitando un refetch redundante.
 */

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { apiClient } from "@/shared/api/client";
import { subscribeSse } from "@/shared/api/sse";
import { sessionKeys } from "./keys";
import {
  sessionDetailsSchema,
  sessionsListResponseSchema,
} from "./contracts";
import type { ChatSession, SessionDetails } from "./model";

const SESSION_DETAIL_REFETCH_MS = 3_000;

async function fetchSessions(): Promise<ChatSession[]> {
  const raw = await apiClient.get<unknown>("/api/dashboard/sessions");
  return sessionsListResponseSchema.parse(raw).sessions;
}

async function fetchSessionDetail(id: string): Promise<SessionDetails> {
  const raw = await apiClient.get<unknown>(`/api/dashboard/sessions/${id}`);
  return sessionDetailsSchema.parse(raw);
}

/** Lista paginada de sesiones. Se actualiza vía `useSessionsStream()`. */
export function useSessions() {
  return useQuery({
    queryKey: sessionKeys.list(),
    queryFn: fetchSessions,
  });
}

/**
 * Detalle de una sesión. `enabled` se desactiva si `id` es null para evitar
 * un fetch al string vacío. El refetch interval mantiene paridad con el
 * polling legado (3s) hasta que el backend exponga SSE per-sesión.
 */
export function useSession(id: string | null) {
  return useQuery({
    queryKey: sessionKeys.detail(id ?? ""),
    queryFn: () => fetchSessionDetail(id!),
    enabled: !!id,
    refetchInterval: SESSION_DETAIL_REFETCH_MS,
  });
}

/**
 * Suscripción al SSE de la lista. Cada mensaje sustituye el cache directamente
 * (sin refetch). Montar este hook UNA sola vez en el árbol — vive en el layout
 * raíz, no en componentes hoja.
 */
export function useSessionsStream() {
  const qc = useQueryClient();

  useEffect(() => {
    const sub = subscribeSse<unknown>("/api/dashboard/stream", {
      onMessage: (raw) => {
        const parsed = sessionsListResponseSchema.safeParse(raw);
        if (!parsed.success) {
          console.warn("SSE payload inválido", parsed.error);
          return;
        }
        qc.setQueryData(sessionKeys.list(), parsed.data.sessions);
      },
      onError: (err) => {
        console.warn("SSE error", err);
      },
    });
    return () => sub.close();
  }, [qc]);
}
