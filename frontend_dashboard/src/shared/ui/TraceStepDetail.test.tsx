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
  render(<TraceStepDetail step={steps[row.stepIndex]} row={row} lanes={layout.lanes} />);
}

describe("TraceStepDetail", () => {
  it("encabezado: tipo, número y título, carriles, tiempo y duración", () => {
    show([{ i: 1, at_ms: 2450, dur_ms: 300, kind: "tool", name: "search_products", ok: true }], 0);

    expect(screen.getByText("Tool")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "1. search_products" })).toBeInTheDocument();
    expect(screen.getByText("Workflow → Tools · +2,5 s · duró 0,3 s")).toBeInTheDocument();
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

    expect(screen.getByText("send_shipping_rates")).toBeInTheDocument();
    expect(screen.getByText("Descartado: venía junto a una tool (default-deny)")).toBeInTheDocument();
    expect(screen.getByText("¡Claro! te comparto el catálogo")).toBeInTheDocument();
    expect(screen.getByText("5.200 → 80")).toBeInTheDocument();
  });

  it("una tool rechazada muestra el motivo, los argumentos y el extracto", () => {
    show([{ i: 1, at_ms: 0, kind: "tool", name: "request_shipping_details", ok: false, error: "customer_deferred", args: { city: "Bogotá" }, excerpt: "el cliente pidió esperar" }], 1);

    expect(screen.getByText("customer_deferred")).toBeInTheDocument();
    expect(screen.getByText(/"city": "Bogotá"/)).toBeInTheDocument();
    expect(screen.getByText("el cliente pidió esperar")).toBeInTheDocument();
  });

  it("una guarda muestra el texto antes y después", () => {
    show([{ i: 1, at_ms: 0, kind: "guard", name: "variant_enumeration_guard", before: "Tenemos 11 aromas", after: "" }], 0);

    expect(screen.getByText("Tenemos 11 aromas")).toBeInTheDocument();
    expect(screen.getByText("(vacío: la guarda se llevó el texto)")).toBeInTheDocument();
  });

  it("la percepción muestra cada respuesta con su probabilidad, el umbral y lo elegido", () => {
    show([{ i: 1, at_ms: 0, dur_ms: 420, kind: "perception", model: "typesafe/jev-1.13", threshold: 0.5, answers: [{ q: "topic.catalogo", p: 0.96, picked: true }, { q: "topic.envio", p: 0.12, picked: false }] }], 1);

    const picked = screen.getByRole("row", { name: /topic\.catalogo/ });
    expect(picked).toHaveTextContent("0,96");
    expect(picked).toHaveTextContent("✓");
    expect(screen.getByRole("row", { name: /topic\.envio/ })).not.toHaveTextContent("✓");
    expect(screen.getByText("typesafe/jev-1.13")).toBeInTheDocument();
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
