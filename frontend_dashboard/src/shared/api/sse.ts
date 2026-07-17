/**
 * Helper SSE. EventSource nativo no acepta `AbortSignal` ni custom headers;
 * encapsulamos el ciclo de vida (open/close/reconexión) y la decodificación JSON.
 *
 * Auth: como el EventSource NO puede mandar header `Authorization`, el
 * access-token de Cognito viaja por query param `access_token` (el backend
 * `require_auth` lo acepta ahí además del header). No-op en dev local sin sesión.
 *
 * Reconexión MANUAL (regresión prod 2026-07-16): el auto-retry nativo del
 * EventSource reusa la URL del constructor — con el `access_token` que estaba
 * vigente al montar. Cuando ese token vence (~1h) y la conexión se corta, el
 * retry nativo recibe 401 y por spec el browser abandona para siempre (tiempo
 * real muerto hasta recargar). Acá, ante `onerror`, cerramos el stream y lo
 * reabrimos con backoff leyendo el token FRESCO del store en cada intento.
 *
 * Se prefiere a `useEffect + new EventSource` esparcido en componentes:
 *   - cierra sólo cuando el caller lo decide
 *   - errores se propagan via callback en vez de ser silenciados
 *   - convive con TanStack Query vía el patrón "push al cache": el handler
 *     de onMessage hace `setQueryData` (sin refetch) — ver
 *     `useSessionsStream` en chats/entities/session/api.ts.
 */

import { getAccessToken } from "../config/auth-token";
import { env } from "../config/env";

export interface SseSubscription {
  close: () => void;
}

export interface SseHandlers<T> {
  onMessage: (data: T) => void;
  onError?: (err: Event) => void;
  onOpen?: () => void;
}

const RETRY_BASE_MS = 1_000;
const RETRY_MAX_MS = 30_000;

export function subscribeSse<T>(
  path: string,
  handlers: SseHandlers<T>,
): SseSubscription {
  const base = path.startsWith("http") ? path : `${env.apiUrl}${path}`;

  let source: EventSource | null = null;
  let retryTimer: ReturnType<typeof setTimeout> | null = null;
  let attempt = 0;
  let closed = false;

  // La URL se arma EN CADA conexión: el token del store es el vigente (el
  // AuthProvider lo refresca), no el que había cuando el caller se suscribió.
  const buildUrl = () => {
    const token = getAccessToken();
    return token
      ? `${base}${base.includes("?") ? "&" : "?"}access_token=${encodeURIComponent(token)}`
      : base;
  };

  const connect = () => {
    if (closed) return;
    source = new EventSource(buildUrl());

    source.onopen = () => {
      attempt = 0;
      handlers.onOpen?.();
    };
    source.onerror = (err) => {
      handlers.onError?.(err);
      // Nada de auto-retry nativo (URL congelada): reconexión propia.
      source?.close();
      source = null;
      if (closed) return;
      // Un solo retry en vuelo aunque onerror dispare más de una vez.
      if (retryTimer !== null) clearTimeout(retryTimer);
      const delay = Math.min(RETRY_MAX_MS, RETRY_BASE_MS * 2 ** attempt);
      attempt += 1;
      retryTimer = setTimeout(connect, delay);
    };
    source.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data) as T;
        handlers.onMessage(data);
      } catch (err) {
        // Mensaje malformado: lo reportamos por onError pero no rompemos la conexión.
        handlers.onError?.(new ErrorEvent("parse_error", { error: err }));
      }
    };
  };

  connect();

  return {
    close: () => {
      closed = true;
      if (retryTimer !== null) clearTimeout(retryTimer);
      source?.close();
    },
  };
}
