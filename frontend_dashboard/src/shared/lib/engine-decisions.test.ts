import { describe, expect, it } from "vitest";

import { capabilityLabel, decisionSentence, type EngineDecisionView } from "./engine-decisions";

/** Las decisiones de la App Operador (paquete `operador`) se leen en palabras, como las de ventas. */
describe("decisiones de la App Operador", () => {
  const view = (capability: string, value: unknown): EngineDecisionView => ({
    stage: "operador",
    capability,
    by: "jev",
    provider: "jev",
    value,
    answers: [],
  });

  it("nombra cada decisión como el panel", () => {
    expect(capabilityLabel("burbuja")).toBe("Burbuja principal (App Operador)");
    expect(capabilityLabel("incendio")).toBe("Incendio de chat (App Operador)");
  });

  it("dice qué burbuja resalta, o que ninguna", () => {
    expect(decisionSentence(view("burbuja", "1")).text).toBe("Resalta la burbuja 2 de la lista");
    expect(decisionSentence(view("burbuja", "")).text).toBe("No resalta ninguna: hace falta una respuesta escrita");
  });

  it("dice qué tan grave es el incendio, qué plantea el cliente y si empeoró", () => {
    const grave = decisionSentence(view("incendio", { severity: "grave", kind: "angry", getting_worse: true }));
    const calm = decisionSentence(view("incendio", { severity: "espera", kind: "praise", getting_worse: false }));

    expect(grave.text).toBe("Grave · queja o molestia · empeora");
    expect(calm.text).toBe("Puede esperar · felicita o agradece");
    expect(grave.tone).not.toBe(calm.tone);
  });
});
