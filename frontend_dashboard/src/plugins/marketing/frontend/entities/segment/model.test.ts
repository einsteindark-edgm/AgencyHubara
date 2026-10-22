import { describe, expect, it } from "vitest";

import { segmentLabel } from "./model";

describe("segmentLabel", () => {
  it("nombra el segmento de perfil «interesados sin compra» sin pedirle nada al backend", () => {
    // Las cards de campañas del sidebar pintan sus chips sin llamar a /segments
    // (operador, 2026-10-20: el perfil como público de las campañas).
    expect(segmentLabel("interesados_sin_compra")).toBe("Interesados sin compra");
  });

  it("los segmentos de siempre siguen igual", () => {
    expect(segmentLabel("frios")).toBe("Fríos");
    expect(segmentLabel("importados")).toBe("Importado");
  });
});
