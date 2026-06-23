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

import { getAccessToken } from "../config/auth-token";
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
    throw new ApiError(res.status, payload);
  }
  return payload as T;
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
};
