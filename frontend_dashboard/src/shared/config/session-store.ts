/**
 * Persistencia de la sesión del operador (app móvil).
 *
 * El refresh token se guarda en `localStorage` — que en el WebView de Tauri
 * Android vive en el almacenamiento PRIVADO de la app (sandbox por-app), no
 * accesible por otras apps. Al arrancar, la app rehidrata desde acá y renueva
 * el access token con el refresh token, así el operador no re-tipea la
 * contraseña en cada apertura.
 *
 * Nota de seguridad: para un tool INTERNO de operadores, el sandbox de la app
 * + CSP estricta es un piso razonable. El endurecimiento futuro es mover el
 * refresh token al Android Keystore (plugin nativo) — documentado en el
 * runbook. El access token es de vida corta (~1h) y se puede rotar.
 *
 * SOLO lo usa el login nativo móvil. El dashboard web sigue con su sesión OIDC.
 */

const STORAGE_KEY = "hubara.session.v1";

//: Colchón: consideramos el access token "vencido" 60s ANTES de su exp real,
//: para refrescar antes de que una request/SSE reciba un 401.
const EXPIRY_SKEW_MS = 60_000;

export interface PersistedSession {
  accessToken: string;
  idToken: string;
  refreshToken: string;
  /** epoch ms en que el access token debe considerarse vencido (con colchón). */
  expiresAt: number;
  username: string;
}

/** epoch ms de vencimiento efectivo dado `expiresIn` (segundos) de Cognito. */
export function computeExpiresAt(expiresIn: number, now: number): number {
  return now + expiresIn * 1000 - EXPIRY_SKEW_MS;
}

export function saveSession(session: PersistedSession): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(session));
  } catch {
    /* almacenamiento lleno/deshabilitado — la sesión seguirá en memoria */
  }
}

export function loadSession(): PersistedSession | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<PersistedSession>;
    // Sin refresh token la sesión no sirve (no se puede renovar) → descartar.
    if (!parsed.refreshToken || typeof parsed.refreshToken !== "string") {
      clearSession();
      return null;
    }
    return {
      accessToken: String(parsed.accessToken ?? ""),
      idToken: String(parsed.idToken ?? ""),
      refreshToken: parsed.refreshToken,
      expiresAt: Number(parsed.expiresAt ?? 0),
      username: String(parsed.username ?? ""),
    };
  } catch {
    clearSession();
    return null;
  }
}

export function clearSession(): void {
  try {
    localStorage.removeItem(STORAGE_KEY);
  } catch {
    /* no-op */
  }
}
