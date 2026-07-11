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
import { IS_MOBILE } from "@/shared/lib";
import { ErrorBoundary } from "@/shared/ui";
import { AuthGate } from "./AuthGate";
import { MobileAuthGate } from "./MobileAuthGate";
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
  // Sin Cognito configurado (dev local) → sin login, la API está abierta.
  if (!env.cognitoEnabled) {
    return (
      <ErrorBoundary scope="app">
        <Shell>{children}</Shell>
      </ErrorBoundary>
    );
  }
  // Móvil (WebView Android): login NATIVO email+contraseña — el redirect a la
  // hosted UI de Cognito no funciona en el WebView. El gate rehidrata la
  // sesión, muestra la pantalla de login y pone el token en el store que leen
  // el apiClient y el SSE (dentro de Shell).
  if (IS_MOBILE) {
    return (
      <ErrorBoundary scope="app">
        <MobileAuthGate>
          <Shell>{children}</Shell>
        </MobileAuthGate>
      </ErrorBoundary>
    );
  }
  // Web/desktop: flujo OIDC redirect (Authorization Code + PKCE) a la hosted UI.
  return (
    <ErrorBoundary scope="app">
      <AuthProvider {...oidcConfig}>
        <AuthGate>
          <Shell>{children}</Shell>
        </AuthGate>
      </AuthProvider>
    </ErrorBoundary>
  );
}
