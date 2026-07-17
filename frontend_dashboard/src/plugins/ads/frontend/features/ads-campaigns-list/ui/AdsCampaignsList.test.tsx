/**
 * Tests de la card de campaña del sidebar:
 *  - celda CAPI: `↑ N` con desglose en title / warning / "—" discreto.
 *  - desplegable de segmentos (ad sets): chevron solo en campañas resueltas,
 *    fetch lazy al expandir, selección de segmento notifica al Page.
 */

import { describe, expect, it, vi } from "vitest";
import { fireEvent, render } from "@testing-library/react";

import type { AdsCampaign } from "@plugins/ads/frontend/entities/ads-campaign";

const useCampaignAdsetsMock = vi.fn();

vi.mock("@plugins/ads/frontend/entities/ads-campaign", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  useCampaignAdsets: (...args: unknown[]) => useCampaignAdsetsMock(...args),
}));

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
    metaAdsetId: null,
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

describe("AdsCampaignsList — desplegable de segmentos", () => {
  const segments: AdsCampaign[] = [
    makeCampaign({
      id: "ADSET_A",
      name: "Hombres 25-45",
      metaCampaignId: "CAMP_9",
      metaAdsetId: "ADSET_A",
      adSet: "Hombres 25-45",
      started: 3,
    }),
    makeCampaign({
      id: "ADSET_B",
      name: "Mujeres 30-50",
      metaCampaignId: "CAMP_9",
      metaAdsetId: "ADSET_B",
      adSet: "Mujeres 30-50",
      started: 2,
    }),
  ];

  it("expande los segmentos al click del chevron y notifica la selección", () => {
    useCampaignAdsetsMock.mockReturnValue({ data: segments, isLoading: false });
    const onSelectAdset = vi.fn();
    const campaign = makeCampaign({
      id: "CAMP_9",
      name: "Día del Padre",
      metaCampaignId: "CAMP_9",
    });
    const { getByLabelText, getByText, queryByText } = render(
      <AdsCampaignsList
        campaigns={[campaign]}
        selected="CAMP_9"
        onSelect={() => {}}
        selectedAdsetId={null}
        onSelectAdset={onSelectAdset}
      />,
    );
    // colapsado: los segmentos no están en el DOM
    expect(queryByText("Hombres 25-45")).toBeNull();

    fireEvent.click(getByLabelText(/segmentos de Día del Padre/i));
    expect(getByText("Hombres 25-45")).toBeTruthy();
    expect(getByText("Mujeres 30-50")).toBeTruthy();

    fireEvent.click(getByText("Hombres 25-45"));
    expect(onSelectAdset).toHaveBeenCalledWith("CAMP_9", "ADSET_A");
  });

  it("sin metaCampaignId (direct / sin resolver) no hay chevron", () => {
    useCampaignAdsetsMock.mockReturnValue({ data: [], isLoading: false });
    const campaign = makeCampaign({ id: "direct", metaCampaignId: null });
    const { queryByLabelText } = render(
      <AdsCampaignsList
        campaigns={[campaign]}
        selected="direct"
        onSelect={() => {}}
        selectedAdsetId={null}
        onSelectAdset={() => {}}
      />,
    );
    expect(queryByLabelText(/segmentos/i)).toBeNull();
  });
});

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
