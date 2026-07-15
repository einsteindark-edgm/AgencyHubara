/**
 * Contrato de `IS_MOBILE_APP`: la vista de chat se renderiza en la APP móvil
 * (build con `VITE_MOBILE_APP=1`), NO "en cualquier pantalla chica". Sin el
 * flag de build es false — un desktop con la ventana angosta conserva el
 * Dashboard, y la app móvil en un viewport ancho sigue siendo la app de chats.
 */

import { describe, it, expect, vi } from "vitest";

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
