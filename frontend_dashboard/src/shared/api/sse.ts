/**
 * Helper SSE. EventSource nativo no acepta `AbortSignal` ni custom headers;
 * encapsulamos el ciclo de vida (open/close) y la decodificación JSON.
 *
 * Auth: como el EventSource NO puede mandar header `Authorization`, el
 * access-token de Cognito viaja por query param `access_token` (el backend
 * `require_auth` lo acepta ahí además del header). No-op en dev local sin sesión.
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

export function subscribeSse<T>(
  path: string,
  handlers: SseHandlers<T>,
): SseSubscription {
  const base = path.startsWith("http") ? path : `${env.apiUrl}${path}`;
  // El token va por query (el EventSource no manda headers). Header-doc arriba.
  const token = getAccessToken();
  const url = token
    ? `${base}${base.includes("?") ? "&" : "?"}access_token=${encodeURIComponent(token)}`
    : base;
  const source = new EventSource(url);

  source.onopen = () => handlers.onOpen?.();
  source.onerror = (err) => handlers.onError?.(err);
  source.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data) as T;
      handlers.onMessage(data);
    } catch (err) {
      // Mensaje malformado: lo reportamos por onError pero no rompemos la conexión.
      handlers.onError?.(new ErrorEvent("parse_error", { error: err }));
    }
  };

  return {
    close: () => source.close(),
  };
}
