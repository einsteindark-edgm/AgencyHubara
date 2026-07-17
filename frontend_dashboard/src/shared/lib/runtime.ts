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

/**
 * ¿El engine soporta el CSS que Tailwind v4 emite (piso oficial: Chrome 111)?
 *
 * Visto en device real (2026-07-15, Motorola Android 11 con el System WebView
 * de FÁBRICA = Chrome 86, nunca actualizado por Play Store): Tailwind v4 mete
 * los tokens en `@layer` (Chrome 99+) y usa `oklch` (Chrome 111+) — un WebView
 * viejo descarta el theme COMPLETO y la app queda negro-sobre-negro con el
 * login inusable, sin ningún error. `oklch` es el proxy del piso: si no lo
 * soporta, mejor mostrar la pantalla "actualizá Android System WebView" que
 * una app rota indescifrable.
 *
 * `cssSupports` inyectable para test; default el `CSS.supports` real.
 */
export function supportsModernCss(
  cssSupports: ((prop: string, value: string) => boolean) | undefined = typeof CSS !==
    "undefined" && typeof CSS.supports === "function"
    ? CSS.supports.bind(CSS)
    : undefined,
): boolean {
  if (!cssSupports) return false;
  try {
    return cssSupports("color", "oklch(0.5 0.1 200)");
  } catch {
    return false;
  }
}
