/**
 * Token-store sin React — el puente FSD para la auth.
 *
 * `shared/*` no puede importar de `app/` (gate dep-cruiser "shared-no-internal").
 * El `<AuthProvider>` de react-oidc-context vive en `app/providers` y ALIMENTA
 * este store con el access-token de Cognito; el `apiClient` y el `subscribeSse`
 * (ambos en `shared/api`) lo LEEN. Flujo: push (app → store) / pull (shared →
 * store). Nunca shared importa de app.
 */

let _accessToken: string | null = null;

/** Lo setea el AuthProvider (app) en cada cambio de sesión / refresh del token. */
export function setAccessToken(token: string | null): void {
  _accessToken = token && token.trim() !== "" ? token : null;
}

/** Lo leen el apiClient (header Bearer) y el SSE (query param `access_token`). */
export function getAccessToken(): string | null {
  return _accessToken;
}

/**
 * PM2-A2: path REACTIVO de expiración. El ciclo de vida proactivo (un
 * `setTimeout` de refresh) muere cuando Android congela los timers del WebView
 * en background; sin esto, la app queda "autenticada" con TODAS las requests
 * en 401 y sin salida. El apiClient notifica acá cada 401; el gate de auth
 * (app) registra un handler que refresca el token (o cierra sesión si el
 * refresh falla). Mismo puente FSD que el token: push app→store, pull shared→store.
 */
let _onUnauthorized: (() => void) | null = null;

export function setUnauthorizedHandler(fn: (() => void) | null): void {
  _onUnauthorized = fn;
}

/** La llama el apiClient al recibir un 401 (fire-and-forget, nunca lanza). */
export function notifyUnauthorized(): void {
  try {
    _onUnauthorized?.();
  } catch {
    /* el handler jamás debe romper el request que lo notificó */
  }
}
