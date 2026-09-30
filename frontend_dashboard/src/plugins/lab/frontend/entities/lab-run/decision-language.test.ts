import { describe, expect, it } from "vitest";

import { engineDecisionSchema } from "./contracts";
import { decisionSentence, jevAnswers } from "./decision-language";

/**
 * Las decisiones del motor del bot nuevo en frases (revisión 2026-09-29): la
 * tabla decía `{"deferral":null,"courtesy":false}`, `—, text` o `p 0,91`.
 * Casos tomados de la corrida caso-fotos-0929-r2. El significado de cada
 * valor sale de `sales/decisions/capabilities/*.py` y `decisions/egress.py`.
 */

const decision = (raw: Record<string, unknown>) => engineDecisionSchema.parse({ stage: "turno", by: "jev", provider: "jev", ...raw });

describe("decisionSentence", () => {
  it("fuera de catálogo dice qué no aparece por nombre en el catálogo (el detector es literal: no afirma que no lo vendemos)", () => {
    expect(decisionSentence(decision({ capability: "fuera_de_catalogo", value: ["jesús"] }))).toEqual({
      text: "Avisa al modelo que no aparece por nombre en el catálogo: «jesús»",
      tone: "warn",
    });
    expect(decisionSentence(decision({ capability: "fuera_de_catalogo", value: [] })).text).toBe("Nada fuera del catálogo");
  });

  it("compra y aplazamiento en palabras", () => {
    expect(decisionSentence(decision({ capability: "compra", value: [null, "text"] })).text).toBe("No hay señal de compra");
    expect(decisionSentence(decision({ capability: "compra", value: ["affirmation", "button"] })).text).toBe("Confirma la compra (con el botón Confirmar)");
    expect(decisionSentence(decision({ capability: "compra", value: ["deferral", "text"] })).text).toBe("Aplaza la compra");
    expect(decisionSentence(decision({ capability: "retoma", value: { deferral: null, courtesy: false } })).text).toBe("No aplaza la conversación");
    expect(decisionSentence(decision({ capability: "retoma", value: { deferral: null, courtesy: true } })).text).toBe("Solo cortesía: no aplaza");
    expect(
      decisionSentence(decision({ capability: "retoma", value: { deferral: { kind: "fecha", until_ms: Date.UTC(2026, 9, 5, 15, 0) }, courtesy: false } })).text,
    ).toBe("Aplaza hasta el lunes, 5 de octubre");
    expect(decisionSentence(decision({ capability: "retoma", value: { deferral: { kind: "abierto", until_ms: 1 }, courtesy: false } })).text).toBe(
      "Aplaza sin fecha: no se le escribe por 7 días",
    );
  });

  it("el egreso: bienvenida, destinatario, muletilla, lista de opciones y afirmaciones", () => {
    expect(decisionSentence(decision({ capability: "saludo", value: true })).text).toBe("Agrega la bienvenida");
    expect(decisionSentence(decision({ capability: "saludo", value: false })).text).toBe("No hacía falta agregar la bienvenida");
    expect(decisionSentence(decision({ capability: "destinatario", value: false })).text).toBe("El texto es para el cliente");
    expect(decisionSentence(decision({ capability: "destinatario", value: true }))).toEqual({ text: "El texto no es para el cliente: se frena", tone: "bad" });
    expect(decisionSentence(decision({ capability: "destinatario", value: [] })).text).toBe("Todas las oraciones son para el cliente");
    expect(decisionSentence(decision({ capability: "destinatario", value: [1] })).text).toBe("Quita 1 oración que no es para el cliente");
    expect(decisionSentence(decision({ capability: "preambulo", value: "" })).text).toBe("No había muletilla que cortar");
    expect(decisionSentence(decision({ capability: "preambulo", value: "Aquí tienes:" }))).toEqual({ text: "Corta la muletilla «Aquí tienes:»", tone: "warn" });
    expect(decisionSentence(decision({ capability: "enumeracion", value: ["scent", ["Lavanda", "Café", "Coco", "Sándalo"]] }))).toEqual({
      text: "Cambia la lista de 4 aromas por un selector",
      tone: "warn",
    });
    expect(decisionSentence(decision({ capability: "enumeracion", value: [] })).text).toBe("No había lista de opciones");
    expect(decisionSentence(decision({ capability: "afirmacion", value: false })).text).toBe("No afirma nada sin haberlo consultado");
    expect(decisionSentence(decision({ capability: "afirmacion", value: true }))).toEqual({ text: "Afirma algo sin haberlo consultado", tone: "bad" });
  });

  it("el pedido: cantidad, datos, zona, categoría y cupón", () => {
    expect(decisionSentence(decision({ capability: "cantidad", value: { cantidad: null } })).text).toBe("No dio cantidad");
    expect(decisionSentence(decision({ capability: "cantidad", value: { cantidad: 2 } })).text).toBe("Cantidad: 2");
    expect(decisionSentence(decision({ capability: "datos", value: ["nombre_recibe"] }))).toEqual({
      text: "No guarda nombre de quien recibe: el cliente no lo dio",
      tone: "warn",
    });
    expect(decisionSentence(decision({ capability: "datos", value: [] })).text).toBe("Guarda todos los datos");
    expect(decisionSentence(decision({ capability: "zona_de_envio", value: { zona: "bogota" } })).text).toBe("Envío a Bogotá");
    expect(decisionSentence(decision({ capability: "zona_de_envio", value: { zona: null } })).text).toBe("Zona de envío sin definir");
    expect(decisionSentence(decision({ capability: "categoria", value: { categoria: "religiosas" } })).text).toBe("Categoría: religiosas");
    expect(decisionSentence(decision({ capability: "cupon", value: false })).text).toBe("No habla del cupón");
  });

  it("una capacidad nueva cae al valor tal cual, sin romper la vista", () => {
    expect(decisionSentence(decision({ capability: "una_nueva", value: { x: 1 } }))).toEqual({ text: '{"x":1}', tone: "neutral" });
  });
});

describe("jevAnswers", () => {
  it("cada pregunta de Jev en palabras, con su respuesta y qué tan seguro estaba", () => {
    expect(jevAnswers(decision({ capability: "fuera_de_catalogo", value: ["jesús"], answers: [{ q: "fuera_de_catalogo.termino_1", p: 0.91 }] }))).toEqual([
      "¿«jesús» es algo que el cliente pide o muestra? sí (91 %)",
    ]);
    expect(jevAnswers(decision({ capability: "compra", value: [null, "text"], answers: [{ q: "compra.que_hace", choice: "pregunta", confidence: 0.96 }] }))).toEqual([
      "¿Qué hace el cliente con su mensaje? pregunta (96 %)",
    ]);
    expect(jevAnswers(decision({ capability: "saludo", value: true, answers: [{ q: "egreso.saludo", p: 0.06 }] }))).toEqual([
      "¿Algún mensaje ya saluda? no (94 %)",
    ]);
    expect(jevAnswers(decision({ capability: "destinatario", value: false, answers: [{ q: "egreso.destinatario", choice: "mensaje_al_cliente", confidence: 1 }] }))).toEqual([
      "¿Para quién es el texto? para el cliente (100 %)",
    ]);
    expect(jevAnswers(decision({ capability: "datos", value: [], answers: [{ q: "datos.nombre_recibe", p: 0.08 }] }))).toEqual([
      "¿El cliente sí dio nombre de quien recibe? no (92 %)",
    ]);
    expect(jevAnswers(decision({ capability: "x", value: 1, answers: [{ q: "pregunta.nueva", p: 0.5 }] }))).toEqual(["pregunta.nueva: sí (50 %)"]);
  });
});
