import { describe, expect, it } from "vitest";

import { activeRunSchema, conversationsSchema, runsSchema } from "./contracts";

/**
 * Tolerancia POR ELEMENTO (L-10): un elemento raro no vacía la lista entera
 * (antes `.catch([])` a nivel lista dejaba la sección en blanco por uno solo).
 */
describe("listas del laboratorio", () => {
  it("un elemento con otra forma se descarta y el resto se muestra", () => {
    const parsed = runsSchema.parse({ runs: [{ run_id: "run-a" }, { run_id: 7 }, { run_id: "run-b" }] });

    expect(parsed.runs.map((r) => r.run_id)).toEqual(["run-a", "run-b"]);
  });

  it("las conversaciones también", () => {
    const parsed = conversationsSchema.parse({ conversations: [{ session_id: "wa_573001234567" }, null] });

    expect(parsed.conversations).toHaveLength(1);
  });

  it("una corrida sin reportes llega marcada y el cierre del lanzador trae la última", () => {
    expect(runsSchema.parse({ runs: [{ run_id: "run-a", stale: true }] }).runs[0].stale).toBe(true);
    expect(runsSchema.parse({ runs: [{ run_id: "run-a" }] }).runs[0].stale).toBeFalsy();

    const active = activeRunSchema.parse({ active: null, last: { phase: "failed", run_id: "run-a", error: "la caja no prendió" } });

    expect(active.last?.error).toBe("la caja no prendió");
  });
});
