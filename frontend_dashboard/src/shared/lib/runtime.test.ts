/**
 * Contrato de `IS_MOBILE_APP`: la vista de chat se renderiza en la APP móvil
 * (build con `VITE_MOBILE_APP=1`), NO "en cualquier pantalla chica". Sin el
 * flag de build es false — un desktop con la ventana angosta conserva el
 * Dashboard, y la app móvil en un viewport ancho sigue siendo la app de chats.
 */

import { describe, it, expect, vi } from "vitest";

describe("supportsModernCss", () => {
  it("false cuando CSS.supports no reconoce oklch (WebView viejo, Chrome <111)", async () => {
    // Regresión device real 2026-07-15 (Motorola Android 11, System WebView 86
    // de fábrica): Tailwind v4 emite sus tokens dentro de @layer (Chrome 99+)
    // y colores oklch (Chrome 111+) — el WebView viejo descarta TODO el theme
    // y la app queda negro-sobre-negro, indescifrable para el operador.
    const { supportsModernCss } = await import("./runtime");
    expect(supportsModernCss((_p, v) => !v.includes("oklch"))).toBe(false);
  });
  it("true cuando el engine soporta oklch (Chrome 111+)", async () => {
    const { supportsModernCss } = await import("./runtime");
    expect(supportsModernCss(() => true)).toBe(true);
  });
  it("false cuando CSS.supports no existe (engine prehistórico)", async () => {
    const { supportsModernCss } = await import("./runtime");
    expect(supportsModernCss(undefined)).toBe(false);
  });
});

describe("IS_MOBILE_APP", () => {
  it("es false sin VITE_MOBILE_APP, sin importar el ancho de viewport", async () => {
    // Aunque el viewport se reporte como 'de teléfono', NO debe activarse: la
    // elección de app se decide por el build, no por matchMedia.
    vi.stubGlobal("matchMedia", () => ({ matches: true }) as MediaQueryList);
    const { IS_MOBILE_APP } = await import("./runtime");
    expect(IS_MOBILE_APP).toBe(false);
    vi.unstubAllGlobals();
  });
});
