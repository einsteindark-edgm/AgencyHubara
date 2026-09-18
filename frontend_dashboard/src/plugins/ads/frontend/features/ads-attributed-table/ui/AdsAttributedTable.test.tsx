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
