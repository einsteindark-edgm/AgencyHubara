/**
 * Tests de la celda CAPI en la card de campaña:
 *  - muestra `↑ N` (LeadSubmitted + Purchase enviados) con desglose en title.
 *  - `capiFailed > 0` → estado visual de warning (clase `neg` del design system).
 *  - todo en 0 → "—" discreto (sin `↑ 0` ruidoso).
 */

import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";

import type { AdsCampaign } from "@plugins/ads/frontend/entities/ads-campaign";

import { AdsCampaignsList } from "./AdsCampaignsList";

function makeCampaign(over: Partial<AdsCampaign> = {}): AdsCampaign {
  return {
    id: "AD_X",
    name: "Velas artesanales",
    started: 10,
    dates: "1 may → 14 may",
    status: null,
    objective: null,
    placement: null,
    audience: null,
    daysRun: null,
    metaCampaignId: null,
    adSet: null,
    creativeTitle: null,
    creativeThumbnailUrl: null,
    template: null,
    spend: null,
    impressions: null,
    reach: null,
    clicks: null,
    conversationsStarted: null,
    conversations: null,
    revenue: null,
    avgTicket: null,
    llmCostUsd: null,
    llmTokens: null,
    avgEpisodeDurationMs: null,
    firstResp: null,
    tendency: null,
    capiLeadsSent: 0,
    capiPurchasesSent: 0,
    capiFailed: 0,
    ...over,
  };
}

function renderList(campaign: AdsCampaign) {
  return render(
    <AdsCampaignsList
      campaigns={[campaign]}
      selected=""
      onSelect={() => {}}
    />,
  );
}

describe("AdsCampaignsList — celda CAPI", () => {
  it("muestra el total enviado (leads + purchases) con desglose en el title", () => {
    const { getByText, getByTitle } = renderList(
      makeCampaign({ capiLeadsSent: 3, capiPurchasesSent: 1 }),
    );
    expect(getByText("↑ 4")).toBeTruthy();
    expect(
      getByTitle("3 LeadSubmitted · 1 Purchase · 0 fallos"),
    ).toBeTruthy();
  });

  it("marca en warning (clase neg) cuando hay envíos fallidos", () => {
    const { getByTitle } = renderList(
      makeCampaign({ capiLeadsSent: 1, capiFailed: 2 }),
    );
    const cell = getByTitle("1 LeadSubmitted · 0 Purchase · 2 fallos");
    expect(cell.className).toContain("neg");
    expect(cell.textContent).toContain("↑ 1");
  });

  it("todo en 0 → '—' discreto, sin '↑ 0'", () => {
    const { queryByText, getByTitle } = renderList(makeCampaign());
    expect(queryByText(/↑/)).toBeNull();
    expect(getByTitle("Sin eventos CAPI reportados a Meta")).toBeTruthy();
  });
});
