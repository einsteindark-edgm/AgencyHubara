/**
 * Gate de auth para la app móvil: login nativo email+contraseña (sin navegador).
 *
 * Simétrico al `AuthGate` del web (que hace el redirect OIDC), pero para el
 * WebView Android donde ese redirect no funciona. Mientras rehidrata la sesión
 * muestra un loader; sin sesión muestra `LoginScreen`; autenticado renderiza la
 * app y registra el `signOut` en el puente `logout` para que el shell lo llame.
 */

import { useEffect, type ReactNode } from "react";

import { setLogoutHandler } from "@/shared/config";
import { LoginScreen } from "./LoginScreen";
import { useMobileAuth } from "./useMobileAuth";

export function MobileAuthGate({ children }: { children: ReactNode }) {
  const { state, submitting, signIn, completeNewPassword, signOut } =
    useMobileAuth();

  const authed = state.status === "authenticated";

  // Publicá el signOut al puente sólo mientras haya sesión (el botón del shell
  // lo lee vía `logout()`/`canLogout()`).
  useEffect(() => {
    setLogoutHandler(authed ? signOut : null);
    return () => setLogoutHandler(null);
  }, [authed, signOut]);

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
      />
    );
  }

  return <>{children}</>;
}
