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

vi.mock("@plugins/ads/frontend/entities/ads-campaign", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  useAdsCampaigns: (...a: unknown[]) => useAdsCampaignsMock(...(a as [])),
  useAttributedConversations: (...a: unknown[]) =>
    useAttributedConversationsMock(...(a as [])),
  useDailySeries: (...a: unknown[]) => useDailySeriesMock(...(a as [])),
  useCampaignAdsets: (...a: unknown[]) => useCampaignAdsetsMock(...(a as [])),
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

vi.mock("@plugins/ads/frontend/features/ads-inspector", () => ({
  AdsInspector: () => <div data-testid="ads-inspector" />,
}));

vi.mock("@plugins/ads/frontend/features/ads-campaigns-list", () => ({
  AdsCampaignsList: (props: {
    onSelectAdset?: (campaignId: string, adsetId: string | null) => void;
  }) => (
    <button
      data-testid="pick-segment"
      onClick={() => props.onSelectAdset?.("CAMP_9", "ADSET_B")}
    >
      pick
    </button>
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
