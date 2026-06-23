/**
 * Composición raíz de providers. Cualquier provider nuevo (Theme, Auth, etc.)
 * se enchufa acá — `main.tsx` queda inalterado.
 *
 * `ErrorBoundary scope="app"` es la última red (F2.1): si algo revienta fuera
 * del boundary por-sección del Dashboard, el operador ve un fallback con
 * "Reintentar" en vez de pantalla blanca.
 *
 * Auth (OIDC Cognito): cuando `env.cognitoEnabled` (prod), el `<AuthProvider>`
 * envuelve TODO (provee el access-token a apiClient + SSE) y el `<AuthGate>`
 * gatea el render hasta que haya sesión. En dev local sin Cognito configurado,
 * se omite — la app corre sin login (la API local está abierta).
 */

import type { ReactNode } from "react";
import { AuthProvider } from "react-oidc-context";
import { EventStreamProvider } from "@/shared/api";
import { env } from "@/shared/config";
import { ErrorBoundary } from "@/shared/ui";
import { AuthGate } from "./AuthGate";
import { QueryProvider } from "./QueryProvider";

// Config OIDC (Authorization Code + PKCE). authority/clientId/redirectUri salen
// de `env` (gate #13/#14: el read de import.meta.env y las URLs viven en env.ts).
const oidcConfig = {
  authority: env.cognitoAuthority,
  client_id: env.cognitoClientId,
  redirect_uri: env.cognitoRedirectUri,
  response_type: "code",
  scope: "openid email profile",
  // Tras intercambiar el `code`, limpiá `?code=&state=` de la URL.
  onSigninCallback: () => {
    window.history.replaceState({}, document.title, "/");
  },
};

function Shell({ children }: { children: ReactNode }) {
  return (
    <QueryProvider>
      {/* UNA conexión SSE para toda la app (F1) — los plugins se suscriben
          por dominio vía useDashboardEvents, nunca abren streams propios. */}
      <EventStreamProvider>{children}</EventStreamProvider>
    </QueryProvider>
  );
}

export function AppProviders({ children }: { children: ReactNode }) {
  return (
    <ErrorBoundary scope="app">
      {env.cognitoEnabled ? (
        <AuthProvider {...oidcConfig}>
          <AuthGate>
            <Shell>{children}</Shell>
          </AuthGate>
        </AuthProvider>
      ) : (
        <Shell>{children}</Shell>
      )}
    </ErrorBoundary>
  );
}
