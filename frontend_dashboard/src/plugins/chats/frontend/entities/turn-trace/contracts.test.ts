import { describe, expect, it } from "vitest";

import { turnThreadSchema } from "./contracts";

describe("turnThreadSchema", () => {
  it("lee la traza v2 con sus pasos", () => {
    const t = turnThreadSchema.parse({ fidelity: "v2", trace: { turn: 2 }, steps: [{ i: 0, kind: "inbound" }] });
    expect(t.fidelity).toBe("v2");
    expect(t.steps).toHaveLength(1);
  });

  it("tolera un cuerpo parcial (L-10)", () => {
    const t = turnThreadSchema.parse({ fidelity: "v9", steps: "x" });
    expect(t).toEqual({ fidelity: "v1", trace: {}, steps: [] });
  });
});
