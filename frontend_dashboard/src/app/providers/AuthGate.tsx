/**
 * Gate de autenticación OIDC (Cognito). Vive en `app/` (composition root), no en
 * `pages/` (anti-pattern FSD #3). Envuelve el árbol autenticado:
 *   - empuja el access-token al token-store ANTES de montar los hijos (el SSE lo
 *     lee al montar → se setea en render, no en useEffect: los hijos montan
 *     antes que un useEffect del padre);
 *   - si no hay sesión, dispara el redirect a la hosted UI de Cognito;
 *   - muestra estados de carga/error mientras tanto.
 */
import { useEffect, type ReactNode } from "react";
import { useAuth } from "react-oidc-context";
import { setAccessToken } from "@/shared/config";

function Centered({ children }: { children: ReactNode }) {
  return (
    <div
      style={{
        display: "flex",
        height: "100vh",
        alignItems: "center",
        justifyContent: "center",
        color: "var(--color-text-muted, #888)",
        fontFamily: "system-ui, sans-serif",
      }}
    >
      {children}
    </div>
  );
}

export function AuthGate({ children }: { children: ReactNode }) {
  const auth = useAuth();

  // Sin sesión (y sin flujo OIDC en curso) → a la hosted UI de Cognito.
  useEffect(() => {
    if (
      !auth.isLoading &&
      !auth.isAuthenticated &&
      !auth.activeNavigator &&
      !auth.error
    ) {
      void auth.signinRedirect();
    }
  }, [
    auth.isLoading,
    auth.isAuthenticated,
    auth.activeNavigator,
    auth.error,
    auth,
  ]);

  if (auth.isLoading || auth.activeNavigator) {
    return <Centered>Cargando sesión…</Centered>;
  }
  if (auth.error) {
    return <Centered>Error de autenticación: {auth.error.message}</Centered>;
  }
  if (!auth.isAuthenticated) {
    setAccessToken(null);
    return <Centered>Redirigiendo al login…</Centered>;
  }
  // Autenticado: empujá el token al store ANTES de montar el árbol. Side-effect
  // en render a propósito — el store NO es estado de React, y el orden importa
  // (el EventStreamProvider abre el SSE al montar, y lee el token de acá).
  setAccessToken(auth.user?.access_token ?? null);
  return <>{children}</>;
}
