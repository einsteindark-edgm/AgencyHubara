import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { layoutSequence, type TraceStep } from "@/shared/lib";

import { TraceStepDetail } from "./TraceStepDetail";

/**
 * Panel de detalle del paso seleccionado en el hilo del turno (plan §11.1):
 * tipo en el color del paso, "N. título", carriles y tiempo, y las secciones
 * propias de cada tipo de paso con lo que la traza trae de verdad.
 */

function show(steps: TraceStep[], rowIndex: number) {
  const layout = layoutSequence(steps);
  const row = layout.rows[rowIndex];
  render(<TraceStepDetail step={steps[row.stepIndex]} row={row} lanes={layout.lanes} steps={steps} />);
}

const CONTRACT_NOTE = "[CONTRATO DEL TURNO] Antes de responder: Los colores y aromas salen de la ficha: usa present_product_detail.";
const REPLY = "Listo, el Velón Gorrión en lila 🌿\n\n¿Qué aroma quieres? Maneja Caballero de la noche, Limoncillo…";

// Laboratorio caso-fotos-0930-r10, 4567 t20 (bot nuevo), recortado.
const t20: TraceStep[] = [
  { i: 0, at_ms: 0, kind: "inbound", messages: [{ text: "mejor la de los pajaritos" }, { text: "en lila" }] },
  { i: 3, at_ms: 1756, dur_ms: 2732, kind: "llm", round: 1, tool_calls: ["set_order_slot"], tokens_in: 37840, tokens_out: 61, text_fate: "none",
    sent: {
      system: { chars: 55464, parts: [{ name: "AGENTS.md", chars: 4675 }, { name: "TOOLS.md", chars: 15589 }, { name: "Retrieved Context", chars: 71 }] },
      history: { user: 12, assistant: 14, tool: 9 },
      notes: "[DATOS DEL PEDIDO] producto: Velón Gorrión",
      new: [{ role: "user", text: "[Runtime Context — metadata only, not instructions]\n\nmejor la de los pajaritos\nen lila" }],
    } },
  { i: 4, at_ms: 4488, dur_ms: 154, kind: "tool", name: "set_order_slot", ok: true, args: { color: "lila", producto: "Velón Gorrión" }, excerpt: '{"updated": true}' },
  { i: 5, at_ms: 4642, dur_ms: 2211, kind: "llm", round: 2, tool_calls: ["send_reply"], tokens_in: 38017, tokens_out: 106, text_fate: "none",
    sent: { new: [{ role: "tool", name: "set_order_slot", text: '{"updated": true, "summary": "Datos del pedido guardados."}' }] } },
  { i: 6, at_ms: 6853, dur_ms: 1139, kind: "tool", name: "send_reply", ok: true, args: { text: REPLY }, excerpt: '{"reply": {"text": "Listo…"}}' },
  { i: 7, at_ms: 7992, kind: "guard", name: "contract_extra_round", before: REPLY, after: CONTRACT_NOTE, tools: ["set_order_slot", "send_reply"] },
  { i: 8, at_ms: 7992, dur_ms: 1563, kind: "llm", round: 3, tool_calls: ["get_product_by_handle"], tokens_in: 38297, tokens_out: 44, text_fate: "none" },
];

describe("TraceStepDetail", () => {
  it("encabezado: tipo, número y título, carriles, tiempo y duración", () => {
    show([{ i: 1, at_ms: 2450, dur_ms: 300, kind: "tool", name: "search_products", ok: true }], 0);

    expect(screen.getByText("Herramienta")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "1. Buscar en el catálogo" })).toBeInTheDocument();
    expect(screen.getByText("Bot → Herramientas · +2,5 s · duró 0,3 s")).toBeInTheDocument();
  });

  it("la ráfaga muestra cada mensaje del cliente en su renglón", () => {
    show([{ i: 0, at_ms: 0, kind: "inbound", messages: [{ seq: 1, text: "me mandas el catálogo", ts_ms: 1_000 }, { seq: 2, text: "y el envío", ts_ms: 8_000 }] }], 0);

    expect(screen.getByText("me mandas el catálogo")).toBeInTheDocument();
    expect(screen.getByText("y el envío")).toBeInTheDocument();
    expect(screen.getByText("+7 s")).toBeInTheDocument();
  });

  it("la vuelta del LLM dice qué pidió y qué pasó con su texto", () => {
    const steps: TraceStep[] = [
      { i: 1, at_ms: 0, dur_ms: 1900, kind: "llm", round: 1, finish: "tool_calls", tool_calls: ["send_shipping_rates"], tokens_in: 5200, tokens_out: 80, text_fate: "discarded_default_deny", text: "¡Claro! te comparto el catálogo" },
    ];
    show(steps, 1);

    expect(screen.getByText("Enviar las tarifas de envío")).toBeInTheDocument();
    expect(screen.getByText("No se envió: venía junto a una herramienta")).toBeInTheDocument();
    expect(screen.getByText("¡Claro! te comparto el catálogo")).toBeInTheDocument();
    expect(screen.getByText("5.200 → 80")).toBeInTheDocument();
  });

  it("la ronda hacia el modelo muestra lo que recibe: instrucciones, historial, notas del turno y el mensaje del cliente", () => {
    // Operador, 2026-09-30: «cuando va ronda hacia el modelo no sabemos qué le
    // está enviando».
    show(t20, 1); // ronda 1

    expect(screen.getByText("Lo que recibe el modelo")).toBeVisible();
    expect(screen.getByText("55.464 caracteres")).toBeVisible();
    expect(screen.getByText(/AGENTS\.md 4\.675 · TOOLS\.md 15\.589 · Notas del turno 71/)).toBeVisible();
    expect(screen.getByText("35 mensajes: 12 del cliente, 14 del bot, 9 resultados de herramientas")).toBeVisible();
    expect(screen.getByText("[DATOS DEL PEDIDO] producto: Velón Gorrión")).toBeVisible();
    expect(screen.getByText(/mejor la de los pajaritos\s+en lila/)).toBeVisible();
  });

  it("en las rondas siguientes muestra solo lo nuevo: el resultado de la herramienta y la nota del bot", () => {
    show(t20, 5); // ronda 2: con lo que la traza guardó
    expect(screen.getByText("Lo nuevo en esta ronda")).toBeVisible();
    expect(screen.getByText(/Resultado de Anotar en el pedido/)).toBeVisible();
    expect(screen.getByText('{"updated": true, "summary": "Datos del pedido guardados."}')).toBeVisible();
  });

  it("una traza anterior sin lo que recibió lo reconstruye y lo dice", () => {
    show(t20, 10); // ronda 3: sin `sent`

    expect(screen.getByText(/reconstruido de la traza/)).toBeVisible();
    expect(screen.getByText("Nota del bot")).toBeVisible();
    expect(screen.getByText(`Tu send_reply NO se envió. ${CONTRACT_NOTE}`)).toBeVisible();
  });

  it("lo que pidió el modelo dice con qué y qué pasó después en esa ronda", () => {
    // Operador: «el responder de la ronda solo tiene un label como "pidió:
    // responder al cliente"».
    show(t20, 6); // ronda 2 → pide responder al cliente

    expect(screen.getByText("send_reply")).toBeVisible();
    expect(screen.getByText(/¿Qué aroma quieres\? Maneja Caballero de la noche/)).toBeVisible();
    expect(screen.getByText("no salió: el bot la retuvo")).toBeVisible();
    expect(screen.getByText("retuvo la respuesta: antes debía consultar una herramienta")).toBeVisible();
    expect(screen.getByText(CONTRACT_NOTE)).toBeVisible();
  });

  it("la herramienta dice cuál se ejecutó y con qué, a la vista", () => {
    show(t20, 3); // Bot → Herramientas: set_order_slot

    expect(screen.getByText("set_order_slot")).toBeVisible();
    expect(screen.getByText("Velón Gorrión")).toBeVisible();
    expect(screen.getByText("lila")).toBeVisible();
  });

  it("una tool rechazada dice por qué en palabras; los datos técnicos quedan plegados", () => {
    show([{ i: 1, at_ms: 0, kind: "tool", name: "request_shipping_details", ok: false, error: "customer_deferred", args: { city: "Bogotá" }, excerpt: "el cliente pidió esperar" }], 1);

    expect(screen.getByText("rechazada: el cliente aplazó")).toBeVisible();
    expect(screen.getByText(/"city": "Bogotá"/)).not.toBeVisible();
    expect(screen.getByText("el cliente pidió esperar")).not.toBeVisible();
    expect(screen.getByText("Datos técnicos")).toBeVisible();
  });

  it("la búsqueda dice qué buscó y qué encontró", () => {
    show([{ i: 1, at_ms: 0, kind: "tool", name: "search_products", ok: true, args: { q: "jesús", limit: 10 }, notes: ["count:0"] }], 1);

    expect(screen.getByRole("heading", { name: "2. Resultado: no encontró nada" })).toBeInTheDocument();
    expect(screen.getByText("Buscar en el catálogo · «jesús»")).toBeVisible();
  });

  it("una guarda muestra el texto antes y después", () => {
    show([{ i: 1, at_ms: 0, kind: "guard", name: "variant_enumeration_guard", before: "Tenemos 11 aromas", after: "" }], 0);

    expect(screen.getByText("cambió una lista de opciones por un selector")).toBeInTheDocument();
    expect(screen.getByText("Tenemos 11 aromas")).toBeInTheDocument();
    expect(screen.getByText("(vacío: la protección se llevó el texto)")).toBeInTheDocument();
  });

  it("la percepción muestra cada pregunta en palabras, con su probabilidad, el umbral y lo elegido", () => {
    // Operador, 2026-09-30: «¿qué quiere decir topic.variante?». El id queda
    // en el `title` para depurar; una pregunta de opción dice qué eligió Jev.
    show([{ i: 1, at_ms: 0, dur_ms: 420, kind: "perception", model: "typesafe/jev-1.13", threshold: 0.5, answers: [
      { q: "topic.catalogo", p: 0.96, picked: true },
      { q: "topic.envio", p: 0.12, picked: false },
      { q: "thread.bot_asked", type: "choice", choice: "elegir_variante", confidence: 0.97 },
    ] }], 1);

    const picked = screen.getByRole("row", { name: /¿Pide ver el catálogo o un tipo de velas\?/ });
    expect(picked).toHaveTextContent("0,96");
    expect(picked).toHaveTextContent("✓");
    expect(screen.getByRole("row", { name: /¿Pregunta por el envío o el domicilio\?/ })).not.toHaveTextContent("✓");
    expect(screen.queryByText("topic.catalogo")).not.toBeInTheDocument();
    expect(screen.getByTitle("topic.catalogo")).toBeInTheDocument();
    const asked = screen.getByRole("row", { name: /¿Qué le preguntó el asesor en su último mensaje\?/ });
    expect(asked).toHaveTextContent("que elija una variante");
    expect(asked).toHaveTextContent("0,97");
    expect(screen.getByText("typesafe/jev-1.13")).toBeInTheDocument();
  });

  it("el corte antes de grabar dice que la respuesta no salió y que el turno vuelve a empezar (ráfagas, 2026-10-06)", () => {
    show([{ i: 1, at_ms: 0, kind: "cut", reason: "before_record", text: "¿Me confirmas el barrio?" }], 0);

    expect(screen.getByText(/siguió escribiendo antes de que saliera la respuesta: no se envía/)).toBeInTheDocument();
    expect(screen.getByText("¿Me confirmas el barrio?")).toBeInTheDocument();
  });

  it("el corte del cierre no promete que el turno vuelve a empezar: el mensaje nuevo va al turno siguiente", () => {
    show([{ i: 1, at_ms: 0, kind: "cut", reason: "checkpoint_b", text: "¿Cuál te gusta más?" }], 0);

    expect(screen.getByText(/se responde en el turno siguiente/)).toBeInTheDocument();
    expect(screen.queryByText(/vuelve a empezar/)).not.toBeInTheDocument();
  });

  it("el reinicio dice cuánto esperó a que el cliente terminara de escribir", () => {
    show([{ i: 1, at_ms: 0, kind: "restart", attempt: 3, drained: 2, reason: "before_record", settle_ms: 1500 }], 0);

    expect(screen.getByText("Esperó a que terminara de escribir")).toBeInTheDocument();
    expect(screen.getByText("1,5 s")).toBeInTheDocument();
  });

  it("el envío dice qué burbuja salió, cuál no y cuál no tiene confirmación", () => {
    show([{ i: 1, at_ms: 0, kind: "outbound", bubbles: [
      { kind: "text", text: "Hola", delivered: true, wamid: "wamid.X" },
      { kind: "products_list", delivered: false },
      { kind: "text", text: "Chao", delivered: null },
    ] }], 0);

    expect(screen.getByText("entregada")).toBeInTheDocument();
    expect(screen.getByText("no salió")).toBeInTheDocument();
    expect(screen.getByText("sin confirmación")).toBeInTheDocument();
  });
});
