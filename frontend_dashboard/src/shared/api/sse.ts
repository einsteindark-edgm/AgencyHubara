/**
 * Helper SSE. EventSource nativo no acepta `AbortSignal` ni custom headers;
 * encapsulamos el ciclo de vida (open/close) y la decodificación JSON.
 *
 * Se prefiere a `useEffect + new EventSource` esparcido en componentes:
 *   - cierra sólo cuando el caller lo decide
 *   - errores se propagan via callback en vez de ser silenciados
 *   - lifecycle compatible con React Query `useQuery({ queryFn: () => ... })`
 *     mediante el patrón "refetch en onMessage" (ver entities/session/api.ts).
 */

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
  const url = path.startsWith("http") ? path : `${env.apiUrl}${path}`;
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
