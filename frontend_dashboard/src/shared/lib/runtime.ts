/**
 * Detección de entorno de ejecución. Hoy sólo distinguimos navegador-web vs
 * Tauri (desktop). El check usa `__TAURI_INTERNALS__`, el global expuesto por
 * Tauri 2.x — `@tauri-apps/api` 2.10 lo inyecta antes del primer render.
 *
 * Se calcula una sola vez (no cambia durante la sesión), así que es un
 * constante en vez de un hook.
 */

import { env } from "@/shared/config";

declare global {
  interface Window {
    __TAURI_INTERNALS__?: unknown;
  }
}

export const IS_DESKTOP: boolean =
  typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;

/**
 * True SOLO cuando corremos como la app móvil dedicada (build Android/iOS con
 * `VITE_MOBILE_APP=1`). Decide renderizar el shell de chats (una columna +
 * login nativo) en lugar del Dashboard completo.
 *
 * A propósito NO se deriva del ancho de pantalla: la vista de chat se renderiza
 * en la APP móvil, no "en cualquier pantalla chica". Así un desktop con la
 * ventana angosta conserva el Dashboard, y la app móvil en un viewport ancho
 * (tablet/landscape) sigue siendo la app de chats. El layout responsivo DENTRO
 * de la app puede seguir mirando el viewport, pero la ELECCIÓN de app no.
 */
export const IS_MOBILE_APP: boolean = env.mobileApp;
