/**
 * Gate de auth para la app móvil: login nativo email+contraseña (sin navegador).
 *
 * Simétrico al `AuthGate` del web (que hace el redirect OIDC), pero para el
 * WebView Android donde ese redirect no funciona. Mientras rehidrata la sesión
 * muestra un loader; sin sesión muestra `LoginScreen`; autenticado renderiza la
 * app y registra el `signOut` en el puente `logout` para que el shell lo llame.
 */

import { useEffect, type ReactNode } from "react";

import { env, setLogoutHandler } from "@/shared/config";
import { LoginScreen } from "./LoginScreen";
import { useMobileAuth } from "./useMobileAuth";

export function MobileAuthGate({ children }: { children: ReactNode }) {
  const { state, submitting, signIn, completeNewPassword, backToSignIn, signOut } =
    useMobileAuth();

  const authed = state.status === "authenticated";

  // Publicá el signOut al puente sólo mientras haya sesión (el botón del shell
  // lo lee vía `logout()`/`canLogout()`).
  useEffect(() => {
    setLogoutHandler(authed ? signOut : null);
    return () => setLogoutHandler(null);
  }, [authed, signOut]);

  // PM2-A6: fail-fast de misconfig — con Cognito habilitado pero sin endpoint
  // IDP derivable (authority custom sin VITE_COGNITO_REGION), el POST del
  // login iría a `fetch("")` (el ORIGEN del WebView, con la contraseña) y el
  // operador vería "sin conexión" para siempre. Pantalla de error explícita.
  if (!env.cognitoIdpEndpoint) {
    return (
      <div className="login-screen">
        <div className="login-card" role="alert">
          <div className="login-brand">Hubara Chats</div>
          <p className="login-hint">
            Configuración de login incompleta: no se pudo derivar el endpoint
            de Cognito. Definí VITE_COGNITO_REGION (o un
            VITE_COGNITO_AUTHORITY estándar) y regenerá el build.
          </p>
        </div>
      </div>
    );
  }

  if (state.status === "checking") {
    return (
      <div className="login-screen">
        <div className="login-loading">Cargando…</div>
      </div>
    );
  }

  if (!authed) {
    return (
      <LoginScreen
        state={state}
        submitting={submitting}
        onSignIn={signIn}
        onCompleteNewPassword={completeNewPassword}
        onBackToSignIn={backToSignIn}
      />
    );
  }

  return <>{children}</>;
}
