import { describe, expect, it } from "vitest";

import { capabilityLabel, decisionSentence, jevAnswers, type EngineDecisionView } from "./engine-decisions";

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

/** Aromas de otro producto (`aroma_ajeno`, ventas-7, incidente 2026-10-13). */
describe("aromas de otro producto", () => {
  const view = (value: unknown, answers: EngineDecisionView["answers"] = []): EngineDecisionView => ({
    stage: "turno",
    capability: "aroma_ajeno",
    by: "jev",
    provider: "jev",
    rule: ["Chanel", "Drakar"],
    value,
    answers,
  });

  it("se nombra en palabras", () => {
    expect(capabilityLabel("aroma_ajeno")).toBe("Aromas de otro producto");
  });

  it("dice qué aromas devolvió al modelo, o que ninguno", () => {
    expect(decisionSentence(view(["Drakar"])).text).toBe("Devuelve el texto al modelo: «Drakar» no es aroma de ese producto");
    expect(decisionSentence(view(["Drakar"])).tone).toBe("warn");
    expect(decisionSentence(view([])).text).toBe("Los aromas que nombra son del producto");
  });

  it("los colores y las presentaciones también se leen en palabras", () => {
    const color = { ...view(["Azul"]), capability: "color_ajeno" };
    const variant = (value: boolean): EngineDecisionView => ({ ...view(value), capability: "variante_ajena", rule: false });

    expect(capabilityLabel("color_ajeno")).toBe("Colores de otro producto");
    expect(capabilityLabel("variante_ajena")).toBe("Presentación que no existe");
    expect(decisionSentence(color).text).toBe("Devuelve el texto al modelo: «Azul» no es color de ese producto");
    expect(decisionSentence(variant(true)).text).toBe("Devuelve el texto al modelo: le ofrece una presentación que no tiene");
    expect(decisionSentence(variant(false)).text).toBe("Las presentaciones que ofrece son del producto");
    expect(jevAnswers({ ...color, rule: ["Azul"], answers: [{ q: "color_ajeno.termino_1", p: 0.9 }] })[0]).toContain(
      "¿El texto le ofrece «Azul» como color del producto?",
    );
    expect(jevAnswers({ ...variant(true), answers: [{ q: "variante.ajena", p: 0.95 }] })[0]).toContain(
      "¿El texto le ofrece una presentación que el producto no tiene?",
    );
  });

  it("los hechos que el texto afirma se leen en palabras", () => {
    const fact = (capability: string, value: unknown, rule: unknown = value): EngineDecisionView => ({
      ...view(value), capability, rule,
    });

    expect(capabilityLabel("precio_ajeno")).toBe("Precio que no es el del catálogo");
    expect(capabilityLabel("pago_ajeno")).toBe("Pago que no aplica al pedido");
    expect(capabilityLabel("estado_ajeno")).toBe("Estado del pedido que no es cierto");
    expect(capabilityLabel("cupon_ajeno")).toBe("Cupón que no está vigente");
    expect(capabilityLabel("negacion_falsa")).toBe("Negar lo que sí tenemos");
    expect(decisionSentence(fact("precio_ajeno", ["$23.000"])).text).toBe("Devuelve el texto al modelo: «$23.000» no es el precio del catálogo");
    expect(decisionSentence(fact("pago_ajeno", ["puede pagar su pedido contra entrega"])).text).toBe(
      "Devuelve el texto al modelo: le dijo que «puede pagar su pedido contra entrega», y no es así para su pedido",
    );
    expect(decisionSentence(fact("cupon_ajeno", ["AMOR2026"])).text).toBe("Devuelve el texto al modelo: «AMOR2026» no está vigente");
    expect(decisionSentence(fact("negacion_falsa", ["Plegaria de Luz"])).text).toBe("Devuelve el texto al modelo: «Plegaria de Luz» sí lo tenemos");
    expect(decisionSentence(fact("estado_ajeno", [])).text).toBe("Lo que dice del pedido coincide con el sistema");
    expect(jevAnswers({ ...fact("precio_ajeno", ["$23.000"]), answers: [{ q: "precio_ajeno.termino_1", p: 0.9 }] })[0]).toContain(
      "¿El texto le dice que «$23.000» es el precio del producto?",
    );
    expect(jevAnswers({ ...fact("negacion_falsa", ["Plegaria de Luz"]), answers: [{ q: "negacion_falsa.termino_1", p: 0.9 }] })[0]).toContain(
      "¿El texto le dice que no tenemos «Plegaria de Luz»?",
    );
  });

  it("cada pregunta de Jev nombra el aroma que revisó", () => {
    const answers = jevAnswers(view([], [{ q: "aroma_ajeno.termino_2", p: 0.07 }]));

    expect(answers[0]).toContain("¿El texto le ofrece «Drakar» como aroma del producto?");
  });
});
