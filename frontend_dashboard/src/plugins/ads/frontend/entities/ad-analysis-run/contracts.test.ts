import { describe, expect, it } from "vitest";

import {
  agentsListResponseSchema,
  approveResponseSchema,
  runRecordSchema,
  triggerRunResponseSchema,
} from "./contracts";

describe("ad-analysis-run contracts", () => {
  it("agentsListResponseSchema mapea example_input → exampleInput y description", () => {
    const parsed = agentsListResponseSchema.parse([
      {
        id: "ads-analytics",
        label: "Análisis CTWA por campaña",
        description: "Cruza gasto de Meta con ventas y arma el embudo.",
        example_input: { meta_insights: {} },
      },
      { id: "numbers-qa", label: "Numbers QA", example_input: {} },
    ]);
    expect(parsed).toHaveLength(2);
    expect(parsed[0]).toEqual({
      id: "ads-analytics",
      label: "Análisis CTWA por campaña",
      // El selector la muestra bajo el combo: QUÉ análisis hace este agente.
      description: "Cruza gasto de Meta con ventas y arma el embudo.",
      exampleInput: { meta_insights: {} },
    });
    // `example_input` puede ser cualquier JSON, incluido `{}` o arrays.
    expect(parsed[1].exampleInput).toEqual({});
    // Catálogo legacy sin description → null (tolerante en rollout).
    expect(parsed[1].description).toBeNull();
  });

  it("runRecordSchema mapea snake_case → camelCase y default events=[]", () => {
    const parsed = runRecordSchema.parse({
      run_id: "run-abc123",
      agent: "ads-analytics",
      input: { meta_insights: {} },
      status: "running",
      // sin `events` — debe defaultear a []
      execution_id: "exec-1",
    });
    expect(parsed.runId).toBe("run-abc123");
    expect(parsed.executionId).toBe("exec-1");
    expect(parsed.events).toEqual([]);
    expect(parsed.status).toBe("running");
  });

  it("runRecordSchema preserva result/awaiting/error opacos y mapea events", () => {
    const parsed = runRecordSchema.parse({
      run_id: "run-1",
      agent: "ads-analytics",
      input: {},
      status: "awaiting_approval",
      events: [
        { event_id: "run-1:started", type: "run.started", payload: { execution_id: "e" } },
        { event_id: "run-1:awaiting_approval", type: "run.awaiting_approval", payload: { context: { k: 1 } } },
      ],
      awaiting: { context: { k: 1 } },
    });
    expect(parsed.events).toHaveLength(2);
    expect(parsed.events[1].eventId).toBe("run-1:awaiting_approval");
    expect(parsed.events[1].payload).toEqual({ context: { k: 1 } });
    expect(parsed.awaiting).toEqual({ context: { k: 1 } });
    expect(parsed.result).toBeUndefined();
  });

  it("runRecordSchema rechaza un status fuera del enum", () => {
    expect(() =>
      runRecordSchema.parse({
        run_id: "run-1",
        agent: "ads-analytics",
        input: {},
        status: "bogus",
      }),
    ).toThrow();
  });

  it("triggerRunResponseSchema exige run_id", () => {
    expect(triggerRunResponseSchema.parse({ run_id: "run-x" })).toEqual({
      run_id: "run-x",
    });
    expect(() => triggerRunResponseSchema.parse({})).toThrow();
  });

  it("approveResponseSchema acepta el status del resume", () => {
    expect(approveResponseSchema.parse({ status: "resumed" })).toEqual({
      status: "resumed",
    });
  });
});
