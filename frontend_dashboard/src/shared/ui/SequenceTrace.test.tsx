import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";

import { layoutSequence, type TraceStep } from "@/shared/lib";

import { SequenceTrace } from "./SequenceTrace";

/**
 * Diagrama de secuencia del hilo de un turno (plan del laboratorio §11.1).
 * Una fila por flecha; cada fila es un botón con su número y su título. En el
 * escritorio se dibuja el SVG; en el celular, la misma secuencia como lista.
 * Teclado: ↑/↓ recorren los pasos y Enter selecciona.
 */

const steps: TraceStep[] = [
  { i: 0, at_ms: 0, kind: "inbound", messages: [{ text: "me mandas el catálogo" }, { text: "y el envío" }] },
  { i: 1, at_ms: 10, dur_ms: 1900, kind: "llm", round: 1, finish: "tool_calls", tool_calls: ["send_shipping_rates"], text_fate: "discarded_default_deny" },
  { i: 2, at_ms: 1950, dur_ms: 300, kind: "tool", name: "send_shipping_rates", ok: true },
  { i: 3, at_ms: 2300, kind: "outbound", bubbles: [{ kind: "text", delivered: true }] },
];

function setup(selected = 0) {
  const layout = layoutSequence(steps);
  const onSelect = vi.fn();
  render(<SequenceTrace layout={layout} selected={selected} onSelect={onSelect} />);
  return { layout, onSelect };
}

describe("SequenceTrace", () => {
  it("dibuja una fila por flecha con su número y título", () => {
    const { layout } = setup();
    const diagram = screen.getByRole("group", { name: "Secuencia de pasos del turno" });

    const rows = within(diagram).getAllByRole("button");
    expect(rows).toHaveLength(layout.rows.length);
    expect(rows[0]).toHaveAccessibleName("Paso 1: El cliente escribió 2 mensajes seguidos");
    expect(rows[2]).toHaveAccessibleName("Paso 3: Pide enviar las tarifas de envío");
  });

  it("una protección con nombre largo cabe en su caja y el número no la tapa", () => {
    // Laboratorio 2026-09-29: la caja medía siempre 116 px con el texto
    // centrado; «retuvo la respuesta: antes debía consultar una herramienta»
    // se salía por los dos lados y el número quedaba encima del texto.
    const layout = layoutSequence([
      { i: 0, at_ms: 0, kind: "guard", name: "contract_extra_round", before: "No la manejamos", after: "[CONTRATO]", tools: ["send_reply"] },
    ]);
    const { container } = render(<SequenceTrace layout={layout} selected={0} onSelect={vi.fn()} />);
    const row = container.querySelector('[data-step="0"]') as SVGGElement;
    const box = row.querySelectorAll("rect")[1];
    const label = within(row as unknown as HTMLElement).getByText(layout.rows[0].short);
    const badge = row.querySelector("circle") as SVGCircleElement;
    const left = Number(box.getAttribute("x"));
    const boxWidth = Number(box.getAttribute("width"));

    expect(boxWidth).toBeGreaterThanOrEqual(layout.rows[0].short.length * 5.8);
    expect(Number(label.getAttribute("x"))).toBe(left + boxWidth / 2);
    expect(Number(badge.getAttribute("cx")) + Number(badge.getAttribute("r"))).toBeLessThanOrEqual(left);
  });

  it("la flecha a las herramientas nombra la que se ejecutó, en letra de código", () => {
    // Operador, 2026-09-30: «cuando va a herramientas, cuál fue la herramienta
    // que ejecutó». La lista del celular también lo dice.
    const { container } = render(<SequenceTrace layout={layoutSequence(steps)} selected={0} onSelect={vi.fn()} />);
    const row = container.querySelector('[data-step="3"]') as SVGGElement;
    const code = row.querySelector("tspan");

    expect(code?.textContent).toBe("send_shipping_rates");
    expect(code?.getAttribute("style") ?? "").toContain("var(--font-mono)");
    const list = screen.getByRole("list", { name: "Pasos del turno", hidden: true });
    expect(within(list).getAllByRole("button", { hidden: true })[3]).toHaveTextContent("send_shipping_rates");
  });

  it("avisa que el clasificador no se usó en este turno", () => {
    setup();
    expect(screen.getByText("Jev · sin uso")).toBeInTheDocument();
  });

  it("clic en una fila la selecciona", () => {
    const { onSelect } = setup();
    const diagram = screen.getByRole("group", { name: "Secuencia de pasos del turno" });

    fireEvent.click(within(diagram).getAllByRole("button")[3]);
    expect(onSelect).toHaveBeenCalledWith(3);
  });

  it("↓ y ↑ recorren los pasos; Enter selecciona; no se sale de los bordes", () => {
    const { onSelect } = setup(0);
    const diagram = screen.getByRole("group", { name: "Secuencia de pasos del turno" });
    const rows = within(diagram).getAllByRole("button");

    fireEvent.keyDown(rows[0], { key: "ArrowDown" });
    expect(onSelect).toHaveBeenLastCalledWith(1);
    fireEvent.keyDown(rows[0], { key: "ArrowUp" });
    expect(onSelect).toHaveBeenCalledTimes(1);
    fireEvent.keyDown(rows[4], { key: "Enter" });
    expect(onSelect).toHaveBeenLastCalledWith(4);
  });

  it("al cambiar la selección con el teclado, el foco sigue al paso elegido", () => {
    const layout = layoutSequence(steps);
    const { rerender } = render(<SequenceTrace layout={layout} selected={0} onSelect={() => {}} />);
    const diagram = screen.getByRole("group", { name: "Secuencia de pasos del turno" });
    within(diagram).getAllByRole("button")[0].focus();

    rerender(<SequenceTrace layout={layout} selected={1} onSelect={() => {}} />);

    expect(document.activeElement).toBe(within(diagram).getAllByRole("button")[1]);
  });

  it("en el celular muestra la misma secuencia como lista, con el paso actual marcado", () => {
    const { onSelect } = setup(2);
    const list = screen.getByRole("list", { name: "Pasos del turno" });

    const items = within(list).getAllByRole("button");
    expect(items[2]).toHaveAttribute("aria-current", "true");
    expect(items[2]).toHaveTextContent("Modelo de IA → Bot");
    fireEvent.click(items[5]);
    expect(onSelect).toHaveBeenCalledWith(5);
  });
});
