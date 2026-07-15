/**
 * HTTP client minimalista. Centraliza:
 *   - base URL desde `env.apiUrl`
 *   - JSON parsing
 *   - errores tipados (`ApiError`) con status + body
 *   - `Authorization: Bearer <access-token>` de Cognito cuando hay sesión
 *     (lo empuja el AuthProvider al token-store; no-op en dev local sin login).
 *
 * No abstrae métodos custom (DELETE, PATCH) hasta que se necesiten.
 */

import { getAccessToken, notifyUnauthorized } from "../config/auth-token";
import { env } from "../config/env";

export class ApiError extends Error {
  readonly status: number;
  readonly body: unknown;

  constructor(status: number, body: unknown, message?: string) {
    super(message ?? `API error ${status}`);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
}

export interface ApiRequestInit extends Omit<RequestInit, "body"> {
  body?: unknown;
  signal?: AbortSignal;
}

async function request<T>(path: string, init: ApiRequestInit = {}): Promise<T> {
  const url = path.startsWith("http") ? path : `${env.apiUrl}${path}`;

  const headers = new Headers(init.headers);
  if (init.body !== undefined && !headers.has("content-type")) {
    headers.set("content-type", "application/json");
  }
  // Auth centralizada: adjuntá el access-token de Cognito una sola vez. El
  // token-store lo alimenta el AuthProvider (app/providers). Sin sesión (dev
  // local / API abierta) es null → no se manda header.
  const token = getAccessToken();
  if (token && !headers.has("authorization")) {
    headers.set("authorization", `Bearer ${token}`);
  }

  const res = await fetch(url, {
    ...init,
    headers,
    body: init.body !== undefined ? JSON.stringify(init.body) : undefined,
  });

  const contentType = res.headers.get("content-type") ?? "";
  const isJson = contentType.includes("application/json");
  const payload = isJson ? await res.json().catch(() => null) : await res.text();

  if (!res.ok) {
    // PM2-A2: un 401 con sesión activa = access token vencido de facto (timer
    // congelado en background, reloj corrido, usuario deshabilitado). Avisar
    // al gate de auth para que refresque/cierre sesión — sin esto la app queda
    // "autenticada" con todas las requests fallando.
    if (res.status === 401) notifyUnauthorized();
    throw new ApiError(res.status, payload);
  }
  return payload as T;
}

/**
 * POST crudo a un endpoint EXTERNO que no sigue las convenciones de nuestra API
 * (ni base `env.apiUrl`, ni Bearer de Cognito, ni el throw-on-error). Devuelve
 * `{status, data}` con el JSON parseado, sin lanzar por status ≠ 2xx — el caller
 * decide qué hacer con cada status.
 *
 * Existe para que clientes de terceros (p.ej. el IDP de Cognito, protocolo AWS
 * JSON-1.1) NO tengan que llamar `fetch` por su cuenta: TODO el I/O HTTP sigue
 * viviendo en este wrapper (R-FETCH). El caller pasa la URL absoluta y los
 * headers del protocolo externo.
 */
async function postExternal(
  url: string,
  body: unknown,
  headers: Record<string, string>,
): Promise<{ status: number; data: unknown }> {
  // PM2-A8: timeout duro — en red móvil colgada (frecuente en Android) un
  // fetch sin señal deja "Ingresando…" indefinido y un refresh colgado
  // bloquea el ciclo de renovación. 15s → cae al catch del caller (error
  // de red, reintenta).
  const res = await fetch(url, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(15_000),
  });
  const data = await res.json().catch(() => ({}));
  return { status: res.status, data };
}

export const apiClient = {
  get: <T>(path: string, init?: ApiRequestInit) =>
    request<T>(path, { ...init, method: "GET" }),
  post: <T>(path: string, body?: unknown, init?: ApiRequestInit) =>
    request<T>(path, { ...init, method: "POST", body }),
  put: <T>(path: string, body?: unknown, init?: ApiRequestInit) =>
    request<T>(path, { ...init, method: "PUT", body }),
  patch: <T>(path: string, body?: unknown, init?: ApiRequestInit) =>
    request<T>(path, { ...init, method: "PATCH", body }),
  delete: <T>(path: string, init?: ApiRequestInit) =>
    request<T>(path, { ...init, method: "DELETE" }),
  postExternal,
};
