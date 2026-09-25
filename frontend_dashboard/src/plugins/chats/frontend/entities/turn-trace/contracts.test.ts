import { describe, expect, it } from "vitest";

import { turnThreadSchema } from "./contracts";

describe("turnThreadSchema", () => {
  it("lee la traza v2 con sus pasos", () => {
    const t = turnThreadSchema.parse({ fidelity: "v2", trace: { turn: 2 }, steps: [{ i: 0, kind: "inbound" }] });
    expect(t.fidelity).toBe("v2");
    expect(t.steps).toHaveLength(1);
  });

  it("un paso roto se descarta solo y a un paso sin tipo se le dice desconocido (L-10)", () => {
    const t = turnThreadSchema.parse({
      fidelity: "v2",
      steps: [{ i: 0, kind: "inbound", at_ms: 0 }, "texto suelto", { at_ms: "x", name: "search_products" }],
    });

    expect(t.steps).toHaveLength(2);
    expect(t.steps[0]).toMatchObject({ kind: "inbound", at_ms: 0 });
    expect(t.steps[1]).toMatchObject({ kind: "desconocido", at_ms: null, name: "search_products" });
  });

  it("tolera un cuerpo parcial (L-10)", () => {
    const t = turnThreadSchema.parse({ fidelity: "v9", steps: "x" });
    expect(t).toEqual({ fidelity: "v1", trace: {}, steps: [] });
  });
});
