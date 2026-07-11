/**
 * Máquina de estados del login nativo de la app móvil.
 *
 * Orquesta el cliente Cognito (`@/shared/api`) + la persistencia
 * (`@/shared/config` session-store) + el token-store en memoria que leen el
 * apiClient y el SSE (`setAccessToken`). Responsabilidades:
 *
 *   - Boot: rehidratar la sesión persistida; si el access token venció, lo
 *     renueva con el refresh token (sin re-tipear la contraseña).
 *   - `signIn` / `completeNewPassword` / `signOut`.
 *   - Auto-refresh: reprograma la renovación ANTES de que el access token
 *     venza, para que ninguna request ni el SSE reciban un 401.
 *
 * `decideBoot` es la decisión pura (testeable sin React ni timers).
 */

import { useCallback, useEffect, useRef, useState } from "react";

import {
  cognitoInitiateAuth,
  cognitoRefresh,
  cognitoRespondNewPassword,
  type CognitoConfig,
  type CognitoTokens,
} from "@/shared/api";
import {
  clearSession,
  computeExpiresAt,
  env,
  loadSession,
  saveSession,
  setAccessToken,
  type PersistedSession,
} from "@/shared/config";

export type MobileAuthState =
  | { status: "checking" }
  | { status: "unauthenticated"; error?: string }
  | { status: "new_password_required"; session: string; username: string; error?: string }
  | { status: "authenticated"; username: string };

export type BootDecision =
  | { action: "unauthenticated" }
  | { action: "authenticated"; session: PersistedSession }
  | { action: "refresh"; session: PersistedSession };

/** Qué hacer al arrancar dada la sesión persistida y el reloj. Pura. */
export function decideBoot(
  session: PersistedSession | null,
  now: number,
): BootDecision {
  if (!session) return { action: "unauthenticated" };
  if (session.accessToken && session.expiresAt > now) {
    return { action: "authenticated", session };
  }
  return { action: "refresh", session };
}

function cfg(): CognitoConfig {
  return { idpEndpoint: env.cognitoIdpEndpoint, clientId: env.cognitoClientId };
}

export function useMobileAuth() {
  // Estado inicial SÍNCRONO: si hay sesión vigente en disco, seteamos el token
  // en memoria ANTES del primer render (igual que el AuthGate web) para que el
  // SSE/apiClient lo lean al montar. El caso `refresh` (token vencido) arranca
  // en "checking" y lo resuelve el effect de boot (async).
  const [state, setState] = useState<MobileAuthState>(() => {
    const d = decideBoot(loadSession(), Date.now());
    if (d.action === "authenticated") {
      setAccessToken(d.session.accessToken);
      return { status: "authenticated", username: d.session.username };
    }
    if (d.action === "unauthenticated") return { status: "unauthenticated" };
    return { status: "checking" };
  });
  const [submitting, setSubmitting] = useState(false);
  const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Ref al último `applyTokens` — lo lee el timer de refresh (contexto async,
  // no render). Se asigna en un effect, nunca durante el render.
  const applyTokensRef = useRef<(t: CognitoTokens, username: string) => void>(
    () => {},
  );

  const cancelRefresh = useCallback(() => {
    if (refreshTimer.current) {
      clearTimeout(refreshTimer.current);
      refreshTimer.current = null;
    }
  }, []);

  const scheduleRefresh = useCallback(
    (refreshToken: string, expiresAt: number, username: string) => {
      cancelRefresh();
      const delay = Math.max(0, expiresAt - Date.now());
      refreshTimer.current = setTimeout(async () => {
        const out = await cognitoRefresh(cfg(), refreshToken);
        if (out.kind === "tokens") {
          applyTokensRef.current(out, username);
        } else {
          // El refresh falló (token revocado/expirado) → forzar re-login.
          cancelRefresh();
          clearSession();
          setAccessToken(null);
          setState({ status: "unauthenticated", error: "Tu sesión expiró." });
        }
      }, delay);
    },
    [cancelRefresh],
  );

  const applyTokens = useCallback(
    (t: CognitoTokens, username: string) => {
      const expiresAt = computeExpiresAt(t.expiresIn, Date.now());
      const persisted: PersistedSession = {
        accessToken: t.accessToken,
        idToken: t.idToken,
        refreshToken: t.refreshToken,
        expiresAt,
        username,
      };
      saveSession(persisted);
      setAccessToken(t.accessToken);
      setState({ status: "authenticated", username });
      scheduleRefresh(t.refreshToken, expiresAt, username);
    },
    [scheduleRefresh],
  );

  // Mantené el ref apuntando al applyTokens vigente (fuera de render).
  useEffect(() => {
    applyTokensRef.current = applyTokens;
  }, [applyTokens]);

  // Boot (una vez): sólo side-effects. Los estados síncronos ya los fijó el
  // initializer; acá agendamos el refresh de la sesión vigente o disparamos la
  // renovación async del token vencido (el setState va DESPUÉS del await).
  useEffect(() => {
    let cancelled = false;
    const d = decideBoot(loadSession(), Date.now());
    if (d.action === "authenticated") {
      scheduleRefresh(d.session.refreshToken, d.session.expiresAt, d.session.username);
    } else if (d.action === "refresh") {
      void (async () => {
        const out = await cognitoRefresh(cfg(), d.session.refreshToken);
        if (cancelled) return;
        if (out.kind === "tokens") {
          applyTokens(out, d.session.username);
        } else {
          clearSession();
          setAccessToken(null);
          setState({ status: "unauthenticated" });
        }
      })();
    }
    return () => {
      cancelled = true;
      cancelRefresh();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const signIn = useCallback(async (username: string, password: string) => {
    setSubmitting(true);
    const out = await cognitoInitiateAuth(cfg(), username.trim(), password);
    setSubmitting(false);
    if (out.kind === "tokens") {
      applyTokens(out, username.trim());
    } else if (out.kind === "new_password_required") {
      setState({
        status: "new_password_required",
        session: out.session,
        username: out.username,
      });
    } else {
      setState({ status: "unauthenticated", error: out.message });
    }
  }, [applyTokens]);

  const completeNewPassword = useCallback(
    async (newPassword: string) => {
      if (state.status !== "new_password_required") return;
      setSubmitting(true);
      const out = await cognitoRespondNewPassword(
        cfg(),
        state.username,
        state.session,
        newPassword,
      );
      setSubmitting(false);
      if (out.kind === "tokens") {
        applyTokens(out, state.username);
      } else {
        setState({
          status: "new_password_required",
          session: state.status === "new_password_required" ? state.session : "",
          username: state.username,
          error: out.kind === "error" ? out.message : "No se pudo cambiar la contraseña.",
        });
      }
    },
    [state],
  );

  const signOut = useCallback(() => {
    cancelRefresh();
    clearSession();
    setAccessToken(null);
    setState({ status: "unauthenticated" });
  }, [cancelRefresh]);

  return { state, submitting, signIn, completeNewPassword, signOut };
}
