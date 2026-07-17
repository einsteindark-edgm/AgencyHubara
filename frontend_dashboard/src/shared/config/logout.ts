/**
 * Puente de logout sin React, análogo a `auth-token`.
 *
 * El `MobileAuthGate` (capa `app/`) registra acá su `signOut`; el shell móvil
 * (capa `plugins/`) invoca `logout()` desde un botón. Así el plugin dispara el
 * cierre de sesión sin importar de `app/` (prohibido por FSD) — igual que el
 * apiClient lee el token de `auth-token` sin importar de `app/`.
 */

let _handler: (() => void) | null = null;

/** Lo setea el gate de auth (app) al montar; lo limpia al desmontar. */
export function setLogoutHandler(fn: (() => void) | null): void {
  _handler = fn;
}

/** Cierra la sesión si hay un handler registrado (no-op en dev sin auth). */
export function logout(): void {
  _handler?.();
}

/** True si hay una sesión que se puede cerrar (para mostrar/ocultar el botón). */
export function canLogout(): boolean {
  return _handler !== null;
}
