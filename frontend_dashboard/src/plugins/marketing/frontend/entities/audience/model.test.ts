/**
 * Helpers puros del modelo de audiencia: tono por segmento (incluye el
 * "manual" de curaduría), razón de skip legible (incluye el
 * "quitado_por_operador") y la normalización de teléfono → session_id.
 */
import { describe, expect, it } from "vitest";

import { phoneToSessionId, segmentTone, skippedReasonLabel } from "./model";

describe("segmentTone", () => {
  it("clientes=ok, interesados=info, desconocidos=neutral", () => {
    expect(segmentTone("clientes")).toBe("ok");
    expect(segmentTone("interesados")).toBe("info");
    expect(segmentTone("frios")).toBe("neutral");
    expect(segmentTone("otro")).toBe("neutral");
  });

  it("manual (agregado por el operador) tiene tono propio violet", () => {
    expect(segmentTone("manual")).toBe("violet");
  });
});

describe("skippedReasonLabel", () => {
  it("quitado_por_operador se lee 'Quitado por vos'", () => {
    expect(skippedReasonLabel("quitado_por_operador")).toBe("Quitado por vos");
  });

  it("razones conocidas mantienen su label y las nuevas pasan crudas", () => {
    expect(skippedReasonLabel("excluido")).toBe("Atendido por humano u opt-out");
    expect(skippedReasonLabel("razon_nueva")).toBe("razon_nueva");
  });
});

describe("phoneToSessionId", () => {
  it("quita espacios y conserva el + del prefijo", () => {
    expect(phoneToSessionId("+57 300 123 4567")).toBe("wa_+573001234567");
  });

  it("quita guiones y paréntesis", () => {
    expect(phoneToSessionId("(+57) 300-123-4567")).toBe("wa_+573001234567");
  });

  it("un número sin decoración pasa tal cual", () => {
    expect(phoneToSessionId("+573001234567")).toBe("wa_+573001234567");
  });
});
