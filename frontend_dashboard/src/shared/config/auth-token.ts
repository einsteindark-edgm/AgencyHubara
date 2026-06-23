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
