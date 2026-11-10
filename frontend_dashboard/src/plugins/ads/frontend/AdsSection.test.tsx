/**
 * Regresión del incidente 2026-07-08 (vault vacío tras replacement de la caja):
 * con CERO campañas, la sección hacía early-return del empty-state ANTES del
 * header → el botón "Conectar con Meta" nunca se montaba. Pero la conexión a
 * Meta no depende de que haya campañas derivadas del vault — es al revés:
 * conectar es lo que llena los KPIs y muestra las campañas reales de Meta.
 *
 * Contrato observable: la sección monta `ConnectMeta` SIEMPRE, haya o no
 * campañas (los KPIs Meta por campaña viven en `CampaignMetaKpis`). Las features se stubean (acá se testea la
 * composición de la Page, no sus internals).
 */

import { describe, expect, it, vi } from "vitest";
import { render } from "@testing-library/react";

vi.mock("@/shared/lib", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  usePluginHost: () => ({ showSidebar: true, showInspector: true }),
}));

const useAdsCampaignsMock = vi.fn(() => ({ data: [] as unknown[] }));
const useAttributedConversationsMock = vi.fn(() => ({ data: [] }));
const useDailySeriesMock = vi.fn(() => ({ data: [] }));
const useCampaignAdsetsMock = vi.fn(() => ({ data: [] as unknown[] }));
const useAdsetAdsMock = vi.fn(() => ({ data: [] as unknown[] }));

vi.mock("@plugins/ads/frontend/entities/ads-campaign", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  useAdsCampaigns: (...a: unknown[]) => useAdsCampaignsMock(...(a as [])),
  useAttributedConversations: (...a: unknown[]) =>
    useAttributedConversationsMock(...(a as [])),
  useDailySeries: (...a: unknown[]) => useDailySeriesMock(...(a as [])),
  useCampaignAdsets: (...a: unknown[]) => useCampaignAdsetsMock(...(a as [])),
  useAdsetAds: (...a: unknown[]) => useAdsetAdsMock(...(a as [])),
  useAdsOrdersEvents: () => {},
  useAdsOrdersStale: () => false,
}));

vi.mock("@plugins/ads/frontend/entities/ad-analysis-run", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  useRun: () => ({ data: undefined }),
}));

vi.mock("@plugins/ads/frontend/features/connect-meta", () => ({
  ConnectMeta: () => <div data-testid="connect-meta" />,
}));

vi.mock("@plugins/ads/frontend/features/campaign-meta-kpis", () => ({
  CampaignMetaKpis: () => <div data-testid="campaign-meta-kpis" />,
}));

vi.mock("@plugins/ads/frontend/features/agent-referrals-kpi", () => ({
  AgentReferralsKpi: () => <div data-testid="agent-referrals-kpi" />,
}));

vi.mock("@plugins/ads/frontend/features/ads-inspector", () => ({
  AdsInspector: (props: { campaign: { id: string }; adId?: string | null }) => (
    <div data-testid="ads-inspector" data-scope={props.campaign.id} data-ad={props.adId ?? ""} />
  ),
}));

vi.mock("@plugins/ads/frontend/features/ads-creatives-table", () => ({
  AdsCreativesTable: (props: { onSelect: (adId: string | null) => void }) => (
    <button data-testid="pick-ad" onClick={() => props.onSelect("AD_1")}>
      pick ad
    </button>
  ),
}));

vi.mock("@plugins/ads/frontend/features/ads-campaigns-list", () => ({
  AdsCampaignsList: (props: {
    onSelectAdset?: (campaignId: string, adsetId: string | null) => void;
  }) => (
    <>
      <button
        data-testid="pick-segment"
        onClick={() => props.onSelectAdset?.("CAMP_9", "ADSET_B")}
      >
        pick
      </button>
      <button
        data-testid="pick-campaign"
        onClick={() => props.onSelectAdset?.("CAMP_9", null)}
      >
        pick campaign
      </button>
    </>
  ),
}));

vi.mock("@plugins/ads/frontend/features/purchase-places", () => ({
  CampaignPurchasePlaces: (props: {
    campaignId: string;
    adsetId: string | null;
    onOpenOverview?: () => void;
  }) => (
    <div
      data-testid="campaign-places"
      data-campaign={props.campaignId}
      data-adset={props.adsetId ?? ""}
    >
      <button data-testid="places-open-overview" onClick={props.onOpenOverview}>
        open
      </button>
    </div>
  ),
  PurchasePlacesOverview: (props: {
    onSelectCampaign?: (id: string) => void;
    isSelectable?: (id: string) => boolean;
  }) => (
    <div data-testid="places-overview">
      {["CAMP_OTHER", "sin_conversacion"].map((id) => (
        <button
          key={id}
          data-testid={`overview-pick-${id}`}
          data-selectable={String(props.isSelectable?.(id))}
          onClick={() => props.onSelectCampaign?.(id)}
        >
          {id}
        </button>
      ))}
    </div>
  ),
}));

import { AdsSection } from "./AdsSection";
import { fireEvent } from "@testing-library/react";

function makeCampaign(over: Record<string, unknown> = {}) {
  return {
    id: "CAMP_9", name: "Día del Padre", started: 5, dates: "1 may → 14 may",
    status: "active", objective: null, placement: null, audience: null,
    daysRun: null, metaCampaignId: "CAMP_9", metaAdsetId: null, adSet: null,
    creativeTitle: null, creativeThumbnailUrl: null, template: null,
    spend: null, impressions: null, reach: null, clicks: null,
    conversationsStarted: null, conversations: null, revenue: null,
    avgTicket: null, llmCostUsd: null, llmTokens: null,
    avgEpisodeDurationMs: null, firstResp: null, tendency: null,
    capiLeadsSent: 0, capiPurchasesSent: 0, capiFailed: 0,
    ...over,
  };
}

describe("AdsSection — empty-state (vault sin campañas)", () => {
  it("monta ConnectMeta aunque no haya campañas", () => {
    const { getByTestId, getByText } = render(<AdsSection />);

    expect(getByTestId("connect-meta")).toBeTruthy();
    // El empty-state sigue comunicando que no hay campañas derivadas del vault.
    expect(getByText(/sin campañas/i)).toBeTruthy();
  });

  it("muestra los referidos de agentes de IA aunque no haya campañas de Meta", () => {
    // Una tienda sin anuncios igual recibe conversaciones desde ChatGPT/Gemini.
    const { getByTestId } = render(<AdsSection />);

    expect(getByTestId("agent-referrals-kpi")).toBeTruthy();
  });
});

describe("AdsSection — costos de WhatsApp (2026-09-18)", () => {
  it("el panel principal muestra la sección 'Costos de WhatsApp' de la campaña", () => {
    useAdsCampaignsMock.mockReturnValue({
      data: [
        makeCampaign({
          waCostUsdMicros: 22_900,
          waCostByCategory: {
            service: { count: 12, usdMicros: 9_600 },
            marketing: { count: 1, usdMicros: 12_500 },
          },
          waMsgsPending: 0,
        }),
      ],
    });
    const { getByRole, getByTestId } = render(<AdsSection />);

    expect(getByRole("separator", { name: "Costos de WhatsApp" })).toBeTruthy();
    expect(getByTestId("wa-cost-marketing")).toBeTruthy();
  });
});

describe("AdsSection — scope por segmento (2026-07-10)", () => {
  it("al seleccionar un segmento, conversaciones y serie diaria se piden con ese adset_id", () => {
    useAdsCampaignsMock.mockReturnValue({ data: [makeCampaign()] });
    const { getByTestId } = render(<AdsSection />);

    fireEvent.click(getByTestId("pick-segment"));

    const lastConvCall = useAttributedConversationsMock.mock.calls.at(-1) as unknown[];
    expect(lastConvCall[0]).toBe("CAMP_9");
    expect(lastConvCall[2]).toBe("ADSET_B");

    const lastDailyCall = useDailySeriesMock.mock.calls.at(-1) as unknown[];
    expect(lastDailyCall[2]).toBe("ADSET_B");
  });

  it("si la campaña cambia por fallback implícito (cae fuera de la ventana), el segmento NO se arrastra", () => {
    // Hallazgo review 2026-07-10: con CAMP_9 seleccionada + segmento ADSET_B,
    // si la ventana cambia y CAMP_9 desaparece, el fallback elige otra campaña
    // — el adset viejo no debe scopear las queries de la campaña nueva
    // (produciría tabla/serie vacías con header de la campaña completa).
    useAdsCampaignsMock.mockReturnValue({ data: [makeCampaign()] });
    const { getByTestId, rerender } = render(<AdsSection />);
    fireEvent.click(getByTestId("pick-segment"));

    useAdsCampaignsMock.mockReturnValue({
      data: [makeCampaign({ id: "CAMP_OTHER", metaCampaignId: "CAMP_OTHER" })],
    });
    rerender(<AdsSection />);

    const lastConvCall = useAttributedConversationsMock.mock.calls.at(-1) as unknown[];
    expect(lastConvCall[0]).toBe("CAMP_OTHER");
    expect(lastConvCall[2]).toBeNull();

    const lastDailyCall = useDailySeriesMock.mock.calls.at(-1) as unknown[];
    expect(lastDailyCall[2]).toBeNull();
  });
});

describe("AdsSection — scope por anuncio (creativos, 2026-09-10)", () => {
  it("la tabla de creativos solo se monta con un segmento seleccionado", () => {
    useAdsCampaignsMock.mockReturnValue({ data: [makeCampaign()] });
    const { getByTestId, queryByTestId } = render(<AdsSection />);
    expect(queryByTestId("pick-ad")).toBeNull();
    fireEvent.click(getByTestId("pick-segment"));
    expect(getByTestId("pick-ad")).toBeTruthy();
    const lastAdsCall = useAdsetAdsMock.mock.calls.at(-1) as unknown[];
    expect(lastAdsCall[0]).toBe("CAMP_9");
    expect(lastAdsCall[1]).toBe("ADSET_B");
  });

  it("al seleccionar un anuncio, el canvas y el inspector se scopean a ese anuncio", () => {
    useAdsCampaignsMock.mockReturnValue({ data: [makeCampaign()] });
    useAdsetAdsMock.mockReturnValue({
      data: [makeCampaign({ id: "AD_1", name: "Video velas", metaAdsetId: "ADSET_B" })],
    });
    const { getByTestId } = render(<AdsSection />);
    fireEvent.click(getByTestId("pick-segment"));
    fireEvent.click(getByTestId("pick-ad"));

    // El inspector recibe la fila del anuncio + su id para el creativo.
    expect(getByTestId("ads-inspector").getAttribute("data-scope")).toBe("AD_1");
    expect(getByTestId("ads-inspector").getAttribute("data-ad")).toBe("AD_1");
    // Conversaciones y serie diaria se piden por el id crudo del anuncio
    // (source_id del vault — back-compat del backend), sin adset_id.
    const lastConvCall = useAttributedConversationsMock.mock.calls.at(-1) as unknown[];
    expect(lastConvCall[0]).toBe("AD_1");
    expect(lastConvCall[2]).toBeNull();
    const lastDailyCall = useDailySeriesMock.mock.calls.at(-1) as unknown[];
    expect(lastDailyCall[0]).toBe("AD_1");
  });

  it("volver a la campaña limpia el anuncio seleccionado", () => {
    useAdsCampaignsMock.mockReturnValue({ data: [makeCampaign()] });
    useAdsetAdsMock.mockReturnValue({
      data: [makeCampaign({ id: "AD_1", metaAdsetId: "ADSET_B" })],
    });
    const { getByTestId, queryByTestId } = render(<AdsSection />);
    fireEvent.click(getByTestId("pick-segment"));
    fireEvent.click(getByTestId("pick-ad"));
    fireEvent.click(getByTestId("pick-campaign"));
    expect(queryByTestId("pick-ad")).toBeNull();
    expect(getByTestId("ads-inspector").getAttribute("data-scope")).toBe("CAMP_9");
    expect(getByTestId("ads-inspector").getAttribute("data-ad")).toBe("");
  });
});

describe("AdsSection — dónde compran (2026-10-23)", () => {
  it("el canvas muestra dónde compran de la fila activa (campaña, segmento o anuncio)", () => {
    useAdsCampaignsMock.mockReturnValue({ data: [makeCampaign()] });
    useAdsetAdsMock.mockReturnValue({
      data: [makeCampaign({ id: "AD_1", metaAdsetId: "ADSET_B" })],
    });
    const { getByTestId } = render(<AdsSection />);
    expect(getByTestId("campaign-places").getAttribute("data-campaign")).toBe("CAMP_9");
    expect(getByTestId("campaign-places").getAttribute("data-adset")).toBe("");

    fireEvent.click(getByTestId("pick-segment"));
    expect(getByTestId("campaign-places").getAttribute("data-adset")).toBe("ADSET_B");

    fireEvent.click(getByTestId("pick-ad"));
    expect(getByTestId("campaign-places").getAttribute("data-campaign")).toBe("AD_1");
    expect(getByTestId("campaign-places").getAttribute("data-adset")).toBe("");
  });

  it("el botón «Dónde compran» abre la vista general de todas las compras", () => {
    useAdsCampaignsMock.mockReturnValue({ data: [makeCampaign()] });
    const { getByRole, getByTestId, queryByTestId } = render(<AdsSection />);
    expect(queryByTestId("places-overview")).toBeNull();

    fireEvent.click(getByRole("button", { name: /Dónde compran/ }));

    expect(getByTestId("places-overview")).toBeTruthy();
    fireEvent.click(getByRole("button", { name: "Cerrar" }));
    expect(queryByTestId("places-overview")).toBeNull();

    // También se abre desde el panel de la campaña.
    fireEvent.click(getByTestId("places-open-overview"));
    expect(getByTestId("places-overview")).toBeTruthy();
  });

  it("una campaña de la vista general se abre en el canvas y cierra la vista", () => {
    useAdsCampaignsMock.mockReturnValue({
      data: [makeCampaign(), makeCampaign({ id: "CAMP_OTHER", metaCampaignId: "CAMP_OTHER" })],
    });
    const { getByRole, getByTestId, queryByTestId } = render(<AdsSection />);
    fireEvent.click(getByRole("button", { name: /Dónde compran/ }));

    // Solo las campañas de la lista de Ads se pueden abrir.
    expect(getByTestId("overview-pick-sin_conversacion").getAttribute("data-selectable")).toBe("false");
    fireEvent.click(getByTestId("overview-pick-CAMP_OTHER"));

    expect(queryByTestId("places-overview")).toBeNull();
    expect(getByTestId("campaign-places").getAttribute("data-campaign")).toBe("CAMP_OTHER");
  });
});
