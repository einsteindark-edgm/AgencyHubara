import { describe, expect, it } from "vitest";

import { modelRound, roundInput } from "./trace-rounds";
import type { TraceStep } from "./sequence-layout";

/**
 * Las rondas del modelo en el «Paso a paso» (pedido del operador,
 * 2026-09-30, laboratorio 4567 t20):
 *  - «ronda N» hacia el modelo: qué le manda el bot (lo que la traza guardó
 *    en `sent`; en trazas anteriores, lo que se puede reconstruir de ella);
 *  - lo que el modelo pidió: con qué y qué pasó después en esa ronda
 *    («pidió: responder al cliente» no decía nada más).
 */

const CONTRACT_NOTE =
  "[CONTRATO DEL TURNO] Antes de responder: Los colores y aromas salen de la ficha: usa present_product_detail.";
const REPLY = "Listo, el Velón Gorrión en lila 🌿\n\n¿Qué aroma quieres? Maneja Caballero de la noche, Limoncillo…";

// Laboratorio caso-fotos-0930-r10, 4567 t20 (bot nuevo), recortado.
const t20: TraceStep[] = [
  { i: 0, at_ms: 0, kind: "inbound", messages: [{ text: "mejor la de los pajaritos" }, { text: "en lila" }] },
  { i: 3, at_ms: 1756, kind: "llm", round: 1, tool_calls: ["set_order_slot"], text_fate: "none" },
  { i: 4, at_ms: 4488, kind: "tool", name: "set_order_slot", ok: true, args: { color: "lila", producto: "Velón Gorrión" }, excerpt: '{"updated": true}' },
  { i: 5, at_ms: 4642, kind: "llm", round: 2, tool_calls: ["send_reply"], text_fate: "none" },
  { i: 6, at_ms: 6853, kind: "tool", name: "send_reply", ok: true, args: { text: REPLY }, excerpt: '{"reply": {"text": "Listo…"}}' },
  { i: 7, at_ms: 7992, kind: "guard", name: "contract_extra_round", before: REPLY, after: CONTRACT_NOTE, tools: ["set_order_slot", "send_reply"] },
  { i: 8, at_ms: 7992, kind: "llm", round: 3, tool_calls: ["get_product_by_handle"], text_fate: "none" },
  { i: 9, at_ms: 9555, kind: "tool", name: "get_product_by_handle", ok: true, args: { handle: "velon-gorrion" }, excerpt: '{"found": true}' },
  { i: 14, at_ms: 15129, kind: "cut", reason: "send_reply", text: REPLY },
];

describe("modelRound (lo que pidió el modelo y qué pasó en esa ronda)", () => {
  it("empareja cada pedido con su ejecución y trae lo que pasó después, antes de la ronda siguiente", () => {
    const round = modelRound(t20, 3);

    expect(round.calls.map((c) => [c.name, c.step?.i])).toEqual([["send_reply", 6]]);
    expect(round.after.map((s) => [s.kind, s.name])).toEqual([["guard", "contract_extra_round"]]);
    expect(modelRound(t20, 6).after.map((s) => s.kind)).toEqual(["cut"]);
  });

  it("un pedido sin ejecución en la traza queda sin paso", () => {
    expect(modelRound([{ i: 1, at_ms: 0, kind: "llm", round: 1, tool_calls: ["search_products"] }], 0).calls).toEqual([
      { name: "search_products", step: null },
    ]);
  });
});

describe("roundInput (lo que el bot le manda al modelo en cada ronda)", () => {
  it("con lo que la traza guardó: instrucciones, historial, notas del turno y el mensaje como lo lee el modelo", () => {
    const withSent = t20.map((s) =>
      s.i === 3
        ? {
            ...s,
            sent: {
              system: { chars: 55464, parts: [{ name: "AGENTS.md", chars: 4675 }, { name: "Retrieved Context", chars: 71 }] },
              history: { user: 12, assistant: 14, tool: 9 },
              notes: "[DATOS DEL PEDIDO] producto: Velón Gorrión",
              new: [{ role: "user", text: "[Runtime Context …]\n\nmejor la de los pajaritos\nen lila" }],
            },
          }
        : s,
    );

    const input = roundInput(withSent, 1);

    expect(input).toMatchObject({ exact: true, first: true, notes: "[DATOS DEL PEDIDO] producto: Velón Gorrión" });
    expect(input.system?.chars).toBe(55464);
    expect(input.history).toEqual({ user: 12, assistant: 14, tool: 9 });
    expect(input.items).toEqual([{ kind: "customer", text: "[Runtime Context …]\n\nmejor la de los pajaritos\nen lila" }]);
  });

  it("en las rondas siguientes, solo lo nuevo: el resultado de las herramientas y la nota del bot", () => {
    const withSent = t20.map((s) =>
      s.i === 8
        ? { ...s, sent: { new: [{ role: "system", text: `Tu send_reply NO se envió. ${CONTRACT_NOTE}` }] } }
        : s,
    );

    expect(roundInput(withSent, 6)).toEqual({
      exact: true,
      first: false,
      items: [{ kind: "note", text: `Tu send_reply NO se envió. ${CONTRACT_NOTE}` }],
    });
  });

  it("una traza anterior (sin lo que recibió) lo reconstruye: el mensaje del cliente, los resultados y la nota", () => {
    expect(roundInput(t20, 1)).toEqual({
      exact: false,
      first: true,
      items: [{ kind: "customer", text: "mejor la de los pajaritos\nen lila" }],
    });
    expect(roundInput(t20, 3)).toEqual({
      exact: false,
      first: false,
      items: [{ kind: "tool", name: "set_order_slot", text: '{"updated": true}' }],
    });
    expect(roundInput(t20, 6).items).toEqual([
      { kind: "tool", name: "send_reply", text: '{"reply": {"text": "Listo…"}}' },
      { kind: "note", text: `Tu send_reply NO se envió. ${CONTRACT_NOTE}` },
    ]);
  });
});
