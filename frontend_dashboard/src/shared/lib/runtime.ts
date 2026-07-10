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

/**
 * True en pantallas de teléfono. Combina dos señales: viewport angosto
 * (≤ 768px) — funciona en el browser para poder probar el layout móvil sin un
 * device — y, cuando exista, la plataforma nativa de Tauri. Se evalúa una vez
 * al cargar; un resize dramático (rotar tablet) no re-dispara render — el shell
 * móvil se decide al montar, que es cuando importa.
 */
export const IS_MOBILE: boolean =
  typeof window !== "undefined" &&
  typeof window.matchMedia === "function" &&
  window.matchMedia("(max-width: 768px)").matches;
