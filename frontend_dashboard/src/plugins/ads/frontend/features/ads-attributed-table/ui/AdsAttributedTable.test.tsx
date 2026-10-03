/**
 * Tests del badge CAPI por conversación atribuida:
 *  - `capiEvent: "Purchase"` → badge verde (token success).
 *  - `capiEvent: "LeadSubmitted"` → badge azul (token info).
 *  - `capiEvent: null` → sin badge (celda vacía).
 */

import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";

import type { AttributedConversation } from "@plugins/ads/frontend/entities/ads-campaign";

import { AdsAttributedTable } from "./AdsAttributedTable";

function makeRow(over: Partial<AttributedConversation> = {}): AttributedConversation {
  return {
    id: "wa_573001112233__ep_001",
    episodeId: "ep_001",
    short: "33",
    color: "a",
    started: "Hoy 11:42",
    msgs: 8,
    name: null,
    city: null,
    agent: null,
    state: null,
    value: null,
    lastMsg: null,
    ad: null,
    durationMs: null,
    llmCostUsd: null,
    llmTokens: null,
    capiEvent: null,
    ...over,
  };
}

describe("AdsAttributedTable — badge CAPI", () => {
  it("renderiza el badge Purchase en verde (token success)", () => {
    const { getByText } = render(
      <AdsAttributedTable rows={[makeRow({ capiEvent: "Purchase" })]} />,
    );
    const badge = getByText("Purchase");
    expect(badge.className).toContain("att-state");
    expect(badge.getAttribute("style")).toContain("--color-ok");
  });

  it("renderiza el badge LeadSubmitted en azul (token info)", () => {
    const { getByText } = render(
      <AdsAttributedTable rows={[makeRow({ capiEvent: "LeadSubmitted" })]} />,
    );
    const badge = getByText("LeadSubmitted");
    expect(badge.className).toContain("att-state");
    expect(badge.getAttribute("style")).toContain("--color-info");
  });

  it("renderiza el badge OrderCanceled en rojo (token danger)", () => {
    const { getByText } = render(
      <AdsAttributedTable rows={[makeRow({ capiEvent: "OrderCanceled" })]} />,
    );
    const badge = getByText("OrderCanceled");
    expect(badge.className).toContain("att-state");
    expect(badge.getAttribute("style")).toContain("--color-danger");
  });

  it("no renderiza badge cuando capiEvent es null", () => {
    const { queryByText } = render(
      <AdsAttributedTable rows={[makeRow({ capiEvent: null })]} />,
    );
    expect(queryByText("Purchase")).toBeNull();
    expect(queryByText("LeadSubmitted")).toBeNull();
  });
});

describe("AdsAttributedTable — pedido cancelado", () => {
  it("un perdido por pedido cancelado lo dice en el badge de estado", () => {
    const { getByText } = render(
      <AdsAttributedTable
        rows={[makeRow({ state: "perdido", stateReason: "order_cancelled" })]}
      />,
    );
    const badge = getByText(/Perdido · pedido cancelado/);
    expect(badge.getAttribute("title")).toContain("cancelado en Pedidos");
  });

  it("un perdido del chat (RECHAZO) no lleva motivo", () => {
    const { getByText, queryByText } = render(
      <AdsAttributedTable rows={[makeRow({ state: "perdido" })]} />,
    );
    expect(getByText("Perdido", { selector: ".att-state" })).toBeTruthy();
    expect(queryByText(/pedido cancelado/)).toBeNull();
  });
});

describe("AdsAttributedTable — costo de WhatsApp por conversación (2026-09-18)", () => {
  it("columna 'Costo WA': total en US$ + las categorías de Meta que usó", () => {
    const { getByText, getByTestId } = render(
      <AdsAttributedTable
        rows={[
          makeRow({
            waCostUsdMicros: 22_100,
            waCostByCategory: {
              service: { count: 12, usdMicros: 9_600 },
              marketing: { count: 1, usdMicros: 12_500 },
            },
          }),
        ]}
      />,
    );
    expect(getByText("Costo WA")).toBeTruthy();
    const cell = getByTestId("wa-cost-cell");
    expect(cell.textContent).toContain("US$0.0221");
    // Desglose compacto, marketing primero (lo más caro por mensaje).
    expect(cell.textContent).toContain("Marketing 1 · US$0.0125");
    expect(cell.textContent).toContain("Servicio 12 · US$0.0096");
  });

  it("conversación 100% gratis (ventana del anuncio): 'Gratis' + categorías usadas", () => {
    const { getByTestId } = render(
      <AdsAttributedTable
        rows={[
          makeRow({
            waCostUsdMicros: 0,
            waCostByCategory: { service: { count: 7, usdMicros: 0 } },
          }),
        ]}
      />,
    );
    const cell = getByTestId("wa-cost-cell");
    expect(cell.textContent).toContain("Gratis");
    expect(cell.textContent).toContain("Servicio 7");
  });

  it("sin dato de costo: marcador de dato faltante, no un 'US$0' inventado", () => {
    const { getByTestId } = render(
      <AdsAttributedTable rows={[makeRow({ waCostUsdMicros: null })]} />,
    );
    expect(getByTestId("wa-cost-cell").textContent).not.toContain("US$");
  });

  it("avisa si hay mensajes sin precio todavía", () => {
    const { getByTestId } = render(
      <AdsAttributedTable
        rows={[
          makeRow({
            waCostUsdMicros: 800,
            waCostByCategory: { service: { count: 1, usdMicros: 800 } },
            waMsgsPending: 2,
          }),
        ]}
      />,
    );
    expect(getByTestId("wa-cost-cell").textContent).toContain("+2 sin precio");
  });
  it("columna 'Costo Jev' después de LLM y WA: lo que costó Jev + cuántas preguntas", () => {
    const { getAllByRole, getByTestId } = render(
      <AdsAttributedTable rows={[makeRow({ jevCostUsdMicros: 105, jevCalls: 5 })]} />,
    );
    const headers = getAllByRole("columnheader").map((h) => h.textContent?.trim());
    expect(headers.slice(-4)).toEqual(["Costo LLM", "Costo WA", "Costo Jev", "Costo imágenes"]);
    const cell = getByTestId("jev-cost-cell");
    expect(cell.textContent).toContain("US$0.000105");
    expect(cell.textContent).toContain("5 preguntas");
  });

  it("si la conversación no le preguntó a Jev, lo dice en vez de un 'US$0'", () => {
    const { getByTestId } = render(<AdsAttributedTable rows={[makeRow({ jevCostUsdMicros: null })]} />);
    const cell = getByTestId("jev-cost-cell");
    expect(cell.textContent).not.toContain("US$");
    expect(cell.querySelector("[title]")?.getAttribute("title")).toBe("Esta conversación no le preguntó a Jev");
  });
  it("columna 'Costo imágenes': lo que costó leer las fotos + cuántas lecturas", () => {
    const { getByTestId } = render(
      <AdsAttributedTable rows={[makeRow({ visionCostUsdMicros: 940, visionCalls: 3 })]} />,
    );
    const cell = getByTestId("vision-cost-cell");
    expect(cell.textContent).toContain("US$0.00094");
    expect(cell.textContent).toContain("3 lecturas");
  });

  it("sin fotos, lo dice en vez de un 'US$0'", () => {
    const { getByTestId } = render(<AdsAttributedTable rows={[makeRow({ visionCostUsdMicros: null })]} />);
    const cell = getByTestId("vision-cost-cell");
    expect(cell.textContent).not.toContain("US$");
    expect(cell.querySelector("[title]")?.getAttribute("title")).toBe("El cliente no mandó fotos en esta conversación");
  });
});
