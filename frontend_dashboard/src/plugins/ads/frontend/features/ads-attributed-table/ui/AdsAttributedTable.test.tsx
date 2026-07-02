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

  it("no renderiza badge cuando capiEvent es null", () => {
    const { queryByText } = render(
      <AdsAttributedTable rows={[makeRow({ capiEvent: null })]} />,
    );
    expect(queryByText("Purchase")).toBeNull();
    expect(queryByText("LeadSubmitted")).toBeNull();
  });
});
