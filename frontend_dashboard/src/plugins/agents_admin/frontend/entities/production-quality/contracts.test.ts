import { describe, expect, it } from "vitest";

import conversations from "./fixtures/conversations.json";
import evaluations from "./fixtures/evaluations.json";
import jev from "./fixtures/jev.json";
import thread from "./fixtures/thread.json";
import trace from "./fixtures/turn-trace.json";
import { conversationsSchema, engineDecisionsSchema, evaluationsSchema, jevReportSchema, threadSchema, turnTraceSchema } from "./contracts";

describe("contratos de Calidad LLM sobre producción", () => {
  it("leen las respuestas del backend", () => {
    expect(conversationsSchema.parse(conversations).conversations[0].bots).toEqual({ ep_007: "nuevo" });
    expect(threadSchema.parse(thread).turns.map((t) => t.turn)).toEqual([1, 3]);
    expect(evaluationsSchema.parse(evaluations).episodes[0].results).toHaveLength(3);
    expect(jevReportSchema.parse(jev).decisions?.jev_failed).toBe(1);
    const parsed = turnTraceSchema.parse(trace);
    expect(engineDecisionsSchema.parse(parsed.trace.decisions).map((d) => d.capability)).toEqual(["compra", "monto"]);
  });

  it("una fila rara se descarta sin vaciar la lista (L-10)", () => {
    const parsed = conversationsSchema.parse({ conversations: [{ nope: 1 }, { session_id: "wa_100000000009", bots: { ep_001: "otro" } }] });
    expect(parsed.conversations).toHaveLength(1);
    expect(parsed.conversations[0].bots).toEqual({});
  });

  it("un informe de Jev vacío no rompe la vista", () => {
    expect(jevReportSchema.parse({ episodes: 0, turns: 0 })).toMatchObject({ perception: null, decisions: null, cost_per_turn_usd: null });
  });
});
