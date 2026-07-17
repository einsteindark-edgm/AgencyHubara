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
import { env, getAccessToken } from "@/shared/config";
import { sessionKeys } from "@plugins/chats/frontend/entities/session";
import {
  handoffResponseSchema,
  humanMessageResponseSchema,
  mediaUploadResponseSchema,
  type HandoffResponse,
  type HumanMessageResponse,
  type MediaUploadResponse,
  type ReturnToBotInput,
  type SendHumanMessageInput,
} from "./contracts";

/**
 * Fase A del envío de foto: sube el blob (ya comprimido) al backend, que lo
 * persiste + lo sube a Meta, devolviendo el `attachment_id`.
 *
 * Usa XHR y NO `apiClient` a propósito: XHR expone `upload.onprogress` (fetch
 * no lo hace en el WebView), clave para la barra de progreso en red celular.
 * `onProgress` recibe 0..1. Adjunta el Bearer de Cognito igual que el client.
 */
export function uploadHumanMedia(
  sessionId: string,
  blob: Blob,
  filename: string,
  onProgress?: (fraction: number) => void,
): Promise<MediaUploadResponse> {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    form.append("file", blob, filename);

    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${env.apiUrl}/api/dashboard/sessions/${sessionId}/media`);
    const token = getAccessToken();
    if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);

    // PM-F5: sin timeout, una red celular que muere sin cerrar el socket deja
    // el item en "uploading" con la barra congelada PARA SIEMPRE (el estado
    // failed es el único con botón Reintentar).
    xhr.timeout = 60_000;
    xhr.ontimeout = () =>
      reject(new Error("la subida tardó demasiado — revisá la señal y reintentá"));

    xhr.upload.onprogress = (e) => {
      if (onProgress && e.lengthComputable) onProgress(e.loaded / e.total);
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(mediaUploadResponseSchema.parse(JSON.parse(xhr.responseText)));
        } catch (err) {
          reject(err instanceof Error ? err : new Error("respuesta inválida"));
        }
      } else {
        reject(new Error(`upload falló (HTTP ${xhr.status})`));
      }
    };
    xhr.onerror = () => reject(new Error("error de red al subir la imagen"));
    xhr.send(form);
  });
}

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
  return useMutation<HumanMessageResponse, Error, SendHumanMessageInput>({
    mutationFn: async (input) => {
      if (!sessionId) throw new Error("No session selected");
      const raw = await apiClient.post<unknown>(
        `/api/dashboard/sessions/${sessionId}/messages`,
        input,
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
