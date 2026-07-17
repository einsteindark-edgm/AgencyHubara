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
 * Ciclo de vida del token (premortem 2026-07-14): el timer proactivo NO
 * alcanza en Android — el WebView congela los timers en background. Tres
 * paths REACTIVOS lo respaldan:
 *   - PM2-A1: `visibilitychange` → al volver a foreground, si el token venció
 *     mientras el timer dormía, refresca de inmediato.
 *   - PM2-A2: el apiClient notifica cada 401 (`setUnauthorizedHandler`) →
 *     refresh forzado; si el refresh dice "revocado", logout limpio.
 *   - PM2-A3: un fallo de RED/throttling en el refresh NO borra la sesión
 *     (abrir la app sin señal ≠ token revocado) — reintenta con backoff.
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
  setUnauthorizedHandler,
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

/** PM2-A5: ningún access token de Cognito vive más de 24h. Un `expiresAt` más
 *  lejano que eso = el reloj del dispositivo saltó hacia atrás desde el save
 *  (NTP / cambio manual) → no confiar, refrescar. */
const MAX_PLAUSIBLE_TTL_MS = 24 * 60 * 60 * 1000;

/** Backoff del retry de refresh ante fallos TRANSITORIOS (red / throttling). */
const REFRESH_RETRY_BASE_MS = 5_000;
const REFRESH_RETRY_MAX_MS = 60_000;

/** Qué hacer al arrancar dada la sesión persistida y el reloj. Pura. */
export function decideBoot(
  session: PersistedSession | null,
  now: number,
): BootDecision {
  if (!session) return { action: "unauthenticated" };
  if (
    session.accessToken &&
    session.expiresAt > now &&
    session.expiresAt - now <= MAX_PLAUSIBLE_TTL_MS
  ) {
    return { action: "authenticated", session };
  }
  return { action: "refresh", session };
}

/** True si el fallo del refresh es transitorio (NO invalida la sesión). */
export function isTransientRefreshError(code: string): boolean {
  return (
    code === "network" ||
    code === "TooManyRequestsException" ||
    code === "LimitExceededException" ||
    code === "InternalErrorException" ||
    code === "ServiceUnavailableException"
  );
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
  // PM2-A4: generación de sesión — signOut/signIn la incrementan; cualquier
  // refresh que estaba EN VUELO al cambiar la generación descarta su resultado
  // (sin esto, un refresh resuelto post-logout re-persistía la sesión y la
  // app "resucitaba" al operador que acababa de cerrar sesión).
  const sessionGen = useRef(0);
  // Single-flight: un solo refresh a la vez (los 401 en ráfaga y el
  // visibilitychange no deben apilar N refreshes concurrentes).
  const refreshInFlight = useRef(false);
  const retryCount = useRef(0);
  // Refs a los callbacks vigentes — los leen contextos async (timer, handler
  // de 401, visibilitychange). Se asignan en un effect, nunca en render.
  const applyTokensRef = useRef<(t: CognitoTokens, username: string) => void>(
    () => {},
  );
  const runRefreshRef = useRef<(force: boolean) => void>(() => {});

  const cancelRefresh = useCallback(() => {
    if (refreshTimer.current) {
      clearTimeout(refreshTimer.current);
      refreshTimer.current = null;
    }
  }, []);

  /**
   * Refresca el access token desde la sesión persistida.
   * - `force=false`: solo si el token ya venció (path visibilitychange).
   * - tokens → aplicar; transitorio → conservar sesión + retry con backoff;
   *   revocado/expirado real → limpiar y volver al login.
   */
  const runRefresh = useCallback(
    async (force: boolean) => {
      const session = loadSession();
      if (!session) return;
      if (!force && session.expiresAt > Date.now()) return;
      if (refreshInFlight.current) return;
      refreshInFlight.current = true;
      const gen = sessionGen.current;
      try {
        const out = await cognitoRefresh(cfg(), session.refreshToken);
        if (gen !== sessionGen.current) return; // logout/login ganó — descartar
        if (out.kind === "tokens") {
          retryCount.current = 0;
          applyTokensRef.current(out, session.username);
          return;
        }
        const code = out.kind === "error" ? out.code : "refresh_failed";
        if (isTransientRefreshError(code)) {
          // PM2-A3: sin señal / throttling ≠ sesión inválida. Conservar la
          // sesión y reintentar — re-tipear la contraseña por un blip de red
          // es el modo de fallo más frecuente del móvil.
          const delay = Math.min(
            REFRESH_RETRY_MAX_MS,
            REFRESH_RETRY_BASE_MS * 2 ** retryCount.current,
          );
          retryCount.current += 1;
          cancelRefresh();
          refreshTimer.current = setTimeout(() => {
            void runRefreshRef.current(true);
          }, delay);
          // Si estábamos en el boot (checking), avisar sin destruir la sesión.
          setState((prev) =>
            prev.status === "checking"
              ? {
                  status: "unauthenticated",
                  error:
                    "Sin conexión con el servidor de login. Revisá la señal; reintentamos solos.",
                }
              : prev,
          );
          return;
        }
        // Refresh token revocado/expirado de verdad → re-login.
        cancelRefresh();
        clearSession();
        setAccessToken(null);
        setState({ status: "unauthenticated", error: "Tu sesión expiró." });
      } finally {
        refreshInFlight.current = false;
      }
    },
    [cancelRefresh],
  );

  const scheduleRefresh = useCallback(
    (expiresAt: number) => {
      cancelRefresh();
      const delay = Math.max(0, expiresAt - Date.now());
      refreshTimer.current = setTimeout(() => {
        void runRefreshRef.current(true);
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
      scheduleRefresh(expiresAt);
    },
    [scheduleRefresh],
  );

  // Mantené los refs apuntando a los callbacks vigentes (fuera de render).
  useEffect(() => {
    applyTokensRef.current = applyTokens;
    runRefreshRef.current = (force: boolean) => void runRefresh(force);
  }, [applyTokens, runRefresh]);

  // Boot (una vez): sólo side-effects. Los estados síncronos ya los fijó el
  // initializer; acá agendamos el refresh de la sesión vigente o disparamos la
  // renovación async del token vencido.
  useEffect(() => {
    const d = decideBoot(loadSession(), Date.now());
    if (d.action === "authenticated") {
      scheduleRefresh(d.session.expiresAt);
    } else if (d.action === "refresh") {
      void runRefresh(true);
    }
    return () => cancelRefresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // PM2-A1: al volver a FOREGROUND, chequear el token de inmediato — Android
  // congela los timers del WebView en background, así que el setTimeout de
  // refresh pudo no disparar nunca y el token estar vencido de facto.
  useEffect(() => {
    const onVisible = () => {
      if (typeof document !== "undefined" && document.visibilityState === "visible") {
        runRefreshRef.current(false);
      }
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, []);

  // PM2-A2: red de seguridad reactiva — cualquier 401 del apiClient fuerza un
  // refresh (single-flight absorbe la ráfaga). Si el refresh confirma que la
  // sesión murió, runRefresh ya hace el logout limpio.
  useEffect(() => {
    setUnauthorizedHandler(() => runRefreshRef.current(true));
    return () => setUnauthorizedHandler(null);
  }, []);

  const signIn = useCallback(async (username: string, password: string) => {
    // PM2-A10: normalizar el email — el autofill/teclado Android capitaliza y
    // con un pool case-sensitive eso es un "contraseña incorrecta" fantasma.
    const user = username.trim().toLowerCase();
    sessionGen.current += 1;
    setSubmitting(true);
    const out = await cognitoInitiateAuth(cfg(), user, password);
    setSubmitting(false);
    if (out.kind === "tokens") {
      applyTokens(out, user);
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
        return;
      }
      // PM2-A7: el `Session` del challenge expira (~3 min). Cognito responde
      // NotAuthorizedException — que el mapping genérico traduce a "email o
      // contraseña incorrectos" (falso y sin salida). Acá: mensaje honesto y
      // vuelta al login para re-entrar con la clave temporal.
      if (out.kind === "error" && out.code === "NotAuthorizedException") {
        setState({
          status: "unauthenticated",
          error:
            "La sesión para cambiar la contraseña expiró. Ingresá de nuevo con tu clave temporal.",
        });
        return;
      }
      setState({
        status: "new_password_required",
        session: state.session,
        username: state.username,
        error: out.kind === "error" ? out.message : "No se pudo cambiar la contraseña.",
      });
    },
    [state, applyTokens],
  );

  /** PM2-A7: salida manual del challenge (el operador quiere re-empezar). */
  const backToSignIn = useCallback(() => {
    setState({ status: "unauthenticated" });
  }, []);

  const signOut = useCallback(() => {
    sessionGen.current += 1; // PM2-A4: invalida cualquier refresh en vuelo
    cancelRefresh();
    clearSession();
    setAccessToken(null);
    setState({ status: "unauthenticated" });
  }, [cancelRefresh]);

  return { state, submitting, signIn, completeNewPassword, backToSignIn, signOut };
}
