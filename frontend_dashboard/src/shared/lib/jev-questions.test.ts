import { describe, expect, it } from "vitest";

import { jevChoiceLabel, jevQuestionLabel } from "./jev-questions";

/**
 * Las preguntas que Jev contesta en el paso «Jev lee el mensaje», en palabras
 * del operador. El operador preguntó «¿qué quiere decir topic.variante?»
 * (2026-09-30): el panel mostraba el id del cuestionario.
 */
describe("jevQuestionLabel", () => {
  it("cada asunto del mensaje es una pregunta en palabras", () => {
    expect(jevQuestionLabel("topic.variante")).toBe("¿Pregunta por colores o variantes?");
    expect(jevQuestionLabel("topic.precio")).toBe("¿Pregunta cuánto vale algo?");
    expect(jevQuestionLabel("topic.datos_envio")).toBe("¿Entrega sus datos de envío?");
  });

  it("la lectura del hilo y las preguntas de la etapa también", () => {
    expect(jevQuestionLabel("thread.bot_asked")).toBe("¿Qué le preguntó el asesor en su último mensaje?");
    expect(jevQuestionLabel("thread.answers_bot")).toBe("¿Responde la última pregunta del asesor?");
    expect(jevQuestionLabel("envio.costo")).toBe("¿Pregunta cuánto cuesta el envío?");
    expect(jevQuestionLabel("variantes.elige")).toBe("¿Elige color, aroma, diseño o cantidad?");
    expect(jevQuestionLabel("datos.nombre_recibe")).toBe("¿Dice quién recibe el pedido?");
  });

  it("las preguntas numeradas y la revisión de la respuesta nombran su asunto", () => {
    expect(jevQuestionLabel("msg.2.topic")).toBe("¿Cuál es el asunto principal del mensaje 2?");
    expect(jevQuestionLabel("cover.envio")).toBe("¿La respuesta atiende el envío?");
  });

  it("un id que no conoce se lee igual, sin puntos ni guiones bajos", () => {
    expect(jevQuestionLabel("nueva.pregunta_rara")).toBe("nueva · pregunta rara");
  });
});

describe("jevChoiceLabel", () => {
  it("la opción elegida se dice en palabras", () => {
    expect(jevChoiceLabel("thread.bot_asked", "elegir_variante")).toBe("que elija una variante");
    expect(jevChoiceLabel("thread.answer", "otra")).toBe("otra cosa (elige algo, pregunta o no la responde)");
    expect(jevChoiceLabel("msg.1.topic", "envio")).toBe("envío");
    expect(jevChoiceLabel("msg.1.topic", "ninguno")).toBe("ningún asunto de la lista");
  });

  it("una opción que no conoce se lee sin guiones bajos", () => {
    expect(jevChoiceLabel("thread.bot_asked", "algo_nuevo")).toBe("algo nuevo");
  });
});
