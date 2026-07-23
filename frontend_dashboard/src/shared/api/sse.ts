/**
 * Helper SSE. EventSource nativo no acepta `AbortSignal` ni custom headers;
 * encapsulamos el ciclo de vida (open/close/reconexión) y la decodificación JSON.
 *
 * Auth (SEC-06): el EventSource NO puede mandar header `Authorization`, así que
 * en vez de poner el access-token de Cognito en la URL (quedaría en access logs
 * / proxies / referrer) pedimos un TICKET de corta vida: POST autenticado por
 * header a `/api/dashboard/sse-ticket` → el backend firma un ticket que expira
 * en ~30s y lo pasamos por query `?ticket=`. Aunque se loguee, expira enseguida
 * y NO sirve como bearer contra el resto de la API. No-op en dev sin sesión.
 *
 * Reconexión MANUAL (regresión prod 2026-07-16): el auto-retry nativo del
 * EventSource reusa la URL del constructor — con el ticket que estaba vigente al
 * montar. Cuando ese ticket/token vence y la conexión se corta, el retry nativo
 * recibe 401 y por spec el browser abandona para siempre (tiempo real muerto
 * hasta recargar). Acá, ante `onerror`, cerramos el stream y lo reabrimos con
 * backoff mintando un ticket FRESCO (con el token vigente del store) cada vez.
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

  // El ticket se pide EN CADA conexión con el token vigente del store (el
  // AuthProvider lo refresca) — no el que había cuando el caller se suscribió.
  // Devuelve null si no se pudo emitir (sin sesión / backend caído) → el caller
  // decide reintentar con backoff.
  const mintTicket = async (): Promise<string | null> => {
    const token = getAccessToken();
    try {
      const res = await fetch(`${env.apiUrl}/api/dashboard/sse-ticket`, {
        method: "POST",
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      if (!res.ok) return null;
      const data = (await res.json()) as { ticket?: string };
      return data.ticket ?? null;
    } catch {
      return null;
    }
  };

  const scheduleRetry = () => {
    if (closed) return;
    if (retryTimer !== null) clearTimeout(retryTimer);
    const delay = Math.min(RETRY_MAX_MS, RETRY_BASE_MS * 2 ** attempt);
    attempt += 1;
    retryTimer = setTimeout(connect, delay);
  };

  const connect = async () => {
    if (closed) return;
    const ticket = await mintTicket();
    if (closed) return;
    // Sin ticket (401/red): reintentamos con backoff (el token se refresca en el
    // store, así que el próximo intento puede emitir uno válido).
    if (ticket === null) {
      scheduleRetry();
      return;
    }
    const url = `${base}${base.includes("?") ? "&" : "?"}ticket=${encodeURIComponent(ticket)}`;
    source = new EventSource(url);

    source.onopen = () => {
      attempt = 0;
      handlers.onOpen?.();
    };
    source.onerror = (err) => {
      handlers.onError?.(err);
      // Nada de auto-retry nativo (URL congelada): reconexión propia con un
      // ticket fresco (scheduleRetry → connect → mintTicket).
      source?.close();
      source = null;
      scheduleRetry();
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
