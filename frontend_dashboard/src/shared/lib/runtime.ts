/**
 * Detección de entorno de ejecución. Hoy sólo distinguimos navegador-web vs
 * Tauri (desktop). El check usa `__TAURI_INTERNALS__`, el global expuesto por
 * Tauri 2.x — `@tauri-apps/api` 2.10 lo inyecta antes del primer render.
 *
 * Se calcula una sola vez (no cambia durante la sesión), así que es un
 * constante en vez de un hook.
 */

declare global {
  interface Window {
    __TAURI_INTERNALS__?: unknown;
  }
}

export const IS_DESKTOP: boolean =
  typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
