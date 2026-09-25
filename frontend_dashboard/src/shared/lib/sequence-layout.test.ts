import { describe, expect, it } from "vitest";

import { LANE_X, SEQ_DY, SEQ_WIDTH, SEQ_Y0, layoutSequence, type TraceStep } from "./sequence-layout";

/**
 * Diagrama de secuencia del hilo de un turno (plan del laboratorio §11.1–§11.2).
 *
 * Función pura: recibe los `steps[]` de la traza v2 y devuelve filas con
 * carriles de origen y destino, color por estado, etiqueta corta, tiempo y la
 * geometría del diseño aprobado (carriles en x = 58, 173, 288, 403 y 518 sobre
 * 610; primer paso en y = 70; 44 px entre pasos).
 *
 * Carriles: Cliente · Workflow · clasificador · LLM · Tools. Las tools las
 * ejecuta el WORKFLOW (execute_tool de exoclaw) después de que el LLM las pide:
 * LLM → Workflow ("pide X"), Workflow → Tools y Tools → Workflow.
 */

const newBotTurn: TraceStep[] = [
  { i: 0, at_ms: 0, kind: "inbound", messages: [{ text: "me mandas el catálogo" }, { text: "y el envío" }] },
  { i: 1, at_ms: 100, dur_ms: 420, kind: "perception", model: "typesafe/jev-1.13", answers: [{ q: "topic.catalogo", p: 0.96, picked: true }] },
  { i: 2, at_ms: 530, kind: "plan", checklist: [{ topic: "catalogo" }, { topic: "envio" }] },
  { i: 3, at_ms: 540, dur_ms: 1900, kind: "llm", round: 1, finish: "tool_calls", tool_calls: ["search_products"], text_fate: "none" },
  { i: 4, at_ms: 2450, dur_ms: 300, kind: "tool", name: "search_products", ok: true },
  { i: 5, at_ms: 2750, dur_ms: 1600, kind: "llm", round: 2, finish: "tool_calls", tool_calls: ["present_products"], text_fate: "final" },
  { i: 6, at_ms: 4350, dur_ms: 380, kind: "verify", decision: "send" },
  { i: 7, at_ms: 4800, kind: "outbound", bubbles: [{ kind: "text", delivered: true }, { kind: "products_list", delivered: true }] },
];

describe("layoutSequence", () => {
  it("abre cada llamada en ida y vuelta, y la tool la ejecuta el workflow", () => {
    const { rows } = layoutSequence(newBotTurn);

    expect(rows.map((r) => [r.from, r.to])).toEqual([
      [0, 1], // la ráfaga llega
      [1, 2], [2, 1], // percepción: pregunta y respuesta
      [1, 1], // el plan, en código
      [1, 3], [3, 1], // ronda 1 y lo que pidió
      [1, 4], [4, 1], // el workflow ejecuta la tool
      [1, 3], [3, 1], // ronda 2
      [1, 2], [2, 1], // verificación
      [1, 0], // envío
    ]);
    expect(rows.every((r, idx) => r.index === idx)).toBe(true);
    expect(rows[5].short).toBe("pide search_products");
    expect(rows[6].short).toBe("search_products");
  });

  it("cada fila recuerda de qué paso de la traza sale (para el detalle)", () => {
    const { rows } = layoutSequence(newBotTurn);

    expect(rows.map((r) => r.stepIndex)).toEqual([0, 1, 1, 2, 3, 3, 4, 4, 5, 5, 6, 6, 7]);
  });

  it("usa la geometría del diseño aprobado", () => {
    const layout = layoutSequence(newBotTurn);

    expect(LANE_X).toEqual([58, 173, 288, 403, 518]);
    expect(layout.width).toBe(SEQ_WIDTH);
    expect(layout.rows.map((r) => r.y).slice(0, 3)).toEqual([SEQ_Y0, SEQ_Y0 + SEQ_DY, SEQ_Y0 + 2 * SEQ_DY]);
    expect(layout.height).toBe(SEQ_Y0 + (layout.rows.length - 1) * SEQ_DY + 34);
  });

  it("colorea por estado: clasificador en violeta, tools en cian, envío entregado en verde", () => {
    const { rows } = layoutSequence(newBotTurn);

    expect(rows[1].status).toBe("classifier");
    expect(rows[6].status).toBe("tool");
    expect(rows[12].status).toBe("ok");
    expect(rows[4].status).toBe("info");
  });

  it("las respuestas que vuelven a la izquierda van punteadas, salvo la que llega al cliente", () => {
    const { rows } = layoutSequence(newBotTurn);

    expect(rows[2].dashed).toBe(true); // clasificador → workflow
    expect(rows[12].dashed).toBe(false); // workflow → cliente
    expect(rows[4].dashed).toBe(false); // ida
  });

  it("formatea el tiempo desde el inicio del turno con coma decimal", () => {
    const { rows } = layoutSequence(newBotTurn);

    expect(rows[0].t).toBe("+0,0 s");
    expect(rows[6].t).toBe("+2,5 s");
    expect(rows[6].dur).toBe("0,3 s");
  });

  it("marca el carril del clasificador sin uso cuando el turno no lo llamó", () => {
    const current = layoutSequence([
      { i: 0, at_ms: 0, kind: "inbound", messages: [{ text: "hola" }] },
      { i: 1, at_ms: 10, kind: "llm", round: 1, finish: "stop", tool_calls: [], text_fate: "final" },
      { i: 2, at_ms: 900, kind: "outbound", bubbles: [{ kind: "text", delivered: true }] },
    ]);

    expect(current.usesClassifier).toBe(false);
    expect(layoutSequence(newBotTurn).usesClassifier).toBe(true);
  });

  it("una guarda que se llevó el texto es falla; un texto descartado junto a una tool es advertencia", () => {
    const { rows } = layoutSequence([
      { i: 0, at_ms: 0, kind: "llm", round: 1, finish: "tool_calls", tool_calls: ["send_shipping_rates"], text_fate: "discarded_default_deny", text: "¡Claro! te comparto" },
      { i: 1, at_ms: 10, kind: "guard", name: "variant_enumeration_guard", before: "Tenemos 11 aromas", after: "" },
      { i: 2, at_ms: 20, kind: "tool", name: "request_shipping_details", ok: false, error: "customer_deferred" },
    ]);

    expect(rows[1].status).toBe("warn");
    expect(rows[1].short).toBe("pide send_shipping_rates + texto");
    expect(rows[2]).toMatchObject({ from: 1, to: 1, status: "bad", short: "variant_enumeration_guard" });
    expect(rows[4]).toMatchObject({ from: 4, to: 1, status: "bad", short: "rechazada: customer_deferred" });
  });

  it("los cortes y reinicios del turno quedan dentro del workflow", () => {
    const { rows } = layoutSequence([
      { i: 0, at_ms: 0, kind: "cut", reason: "checkpoint_a" },
      { i: 1, at_ms: 5, kind: "restart", attempt: 1, drained: 1 },
      { i: 2, at_ms: 9, kind: "cut", reason: "awaits_customer" },
    ]);

    expect(rows.map((r) => [r.from, r.to, r.short])).toEqual([
      [1, 1, "corrientazo (A)"],
      [1, 1, "reinicio 1 · +1 mensaje"],
      [1, 1, "espera al cliente"],
    ]);
  });

  it("una traza v1 sin tiempos deja el tiempo vacío y sigue dibujando", () => {
    const { rows } = layoutSequence([
      { i: 1, at_ms: null, kind: "inbound", messages: [{ text: "hola" }] },
      { i: 2, at_ms: null, kind: "tool", name: "search_products", ok: true },
      { i: 3, at_ms: null, kind: "outbound", bubbles: [{ kind: "text", delivered: null }] },
    ]);

    expect(rows.map((r) => r.t)).toEqual(["", "", "", ""]);
    expect(rows[3].status).toBe("neutral");
  });
});
