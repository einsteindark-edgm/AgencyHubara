/**
 * Contrato Zod de GET /api/marketing/segments — fixture con el shape REAL
 * del endpoint (`api/__init__.py::list_segments`): 3 segmentos fijos +
 * excluidos + costo unitario del rate card (Colombia: 12.500 usd micros).
 */
import { describe, expect, it } from "vitest";

import { backendSegmentsResponseSchema } from "./contracts";

const fixture = {
  segments: [
    { key: "clientes", label: "Clientes", description: "Ya compraron · alto valor", count: 18 },
    { key: "interesados", label: "Interesados", description: "Mostraron intención o tienen pago pendiente", count: 24 },
    { key: "frios", label: "Fríos", description: "Consultaron sin etiqueta de compra", count: 57 },
  ],
  excluded_count: 6,
  unit_cost_usd_micros: 12_500,
  currency: "USD",
};

describe("backendSegmentsResponseSchema", () => {
  it("parsea la respuesta real del endpoint", () => {
    const parsed = backendSegmentsResponseSchema.parse(fixture);
    expect(parsed.segments).toHaveLength(3);
    expect(parsed.segments[0]?.key).toBe("clientes");
    expect(parsed.unit_cost_usd_micros).toBe(12_500);
    expect(parsed.excluded_count).toBe(6);
  });

  it("tolera campos faltantes con defaults (backend viejo)", () => {
    const parsed = backendSegmentsResponseSchema.parse({
      segments: [{ key: "frios", label: "Fríos", description: "", count: 0 }],
    });
    expect(parsed.excluded_count).toBe(0);
    expect(parsed.unit_cost_usd_micros).toBe(0);
    expect(parsed.currency).toBe("USD");
  });
});
