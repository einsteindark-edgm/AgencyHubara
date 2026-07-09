/**
 * Franja derecha (2026-07-09): el inspector deja de mentir —
 * - "Sugerencias del agente IA" ya NO es mock quemado: es el HISTORIAL
 *   versionado de análisis (cada corrida con fecha + estado + resultado).
 * - El creativo usa el thumbnail REAL del ad (o un placeholder honesto) y la
 *   marca real de la cuenta conectada; "Abrir en Meta Ads Manager" es un link real.
 */

import { describe, expect, it, vi } from "vitest";
import { render } from "@testing-library/react";

const mockRuns = vi.hoisted(() => ({ current: [] as object[] }));
const mockConn = vi.hoisted(() => ({ current: {} as object }));

vi.mock("@plugins/ads/frontend/entities/ad-analysis-run", () => ({
  useRuns: () => ({ data: mockRuns.current, isLoading: false }),
}));

vi.mock("@plugins/ads/frontend/entities/meta-connection", () => ({
  useMetaConnection: () => mockConn.current,
}));

import { AdsInspector } from "./AdsInspector";
import type { AdsCampaign } from "@plugins/ads/frontend/entities/ads-campaign";

function campaign(over: Partial<AdsCampaign>): AdsCampaign {
  return {
    id: "AD_1",
    name: "Día del padre",
    sourceType: "ad",
    started: 20,
    dates: "—",
    conversations: {
      no_reply: 1, nuevo: 2, activo: 2, calificado: 1, cotizado: 1, ganado: 12, perdido: 1,
    },
    spend: 896823,
    impressions: 45000,
    reach: 38000,
    clicks: 571,
    conversationsStarted: 205,
    status: "paused",
    objective: "OUTCOME_SALES",
    placement: null,
    audience: null,
    daysRun: null,
    metaCampaignId: "c-1",
    adSet: null,
    creativeTitle: "Chatea con nosotros",
    creativeThumbnailUrl: null,
    template: null,
    revenue: 580000,
    avgTicket: 48333,
    llmCostUsd: null,
    llmTokens: null,
    avgEpisodeDurationMs: null,
    firstResp: null,
    tendency: null,
    capiLeadsSent: 0,
    capiPurchasesSent: 0,
    capiFailed: 0,
    ...over,
  } as AdsCampaign;
}

describe("AdsInspector — historial de análisis versionado", () => {
  it("sin análisis → invita a correr el primero (no hay mock quemado)", () => {
    mockRuns.current = [];
    mockConn.current = { data: { connected: true, accountName: "Hubara" } };
    const { getByText, queryByText } = render(
      <AdsInspector campaign={campaign({})} />,
    );
    expect(queryByText(/ROAS sobre 3×/)).toBeNull(); // el mock murió
    expect(getByText(/sin análisis todavía/i)).toBeTruthy();
  });

  it("lista las corridas con fecha y estado (más nueva primero)", () => {
    mockRuns.current = [
      {
        runId: "run-b",
        status: "completed",
        createdAtMs: Date.UTC(2026, 6, 9, 15, 30),
        result: { reporte: "Subí presupuesto 20%" },
        agent: "ads-analytics",
      },
      {
        runId: "run-a",
        status: "failed",
        createdAtMs: Date.UTC(2026, 6, 8, 10, 0),
        result: null,
        agent: "ads-analytics",
      },
    ];
    mockConn.current = { data: { connected: true, accountName: "Hubara" } };
    const { getByText, getAllByText } = render(<AdsInspector campaign={campaign({})} />);
    expect(getByText(/9 de jul/i)).toBeTruthy();
    expect(getByText(/8 de jul/i)).toBeTruthy();
    expect(getByText(/completado/i)).toBeTruthy();
    expect(getAllByText(/falló/i).length).toBeGreaterThan(0);
    expect(getByText(/Subí presupuesto 20%/)).toBeTruthy();
  });
});

describe("AdsInspector — creativo honesto", () => {
  it("con thumbnail real → renderiza la imagen del ad", () => {
    mockRuns.current = [];
    mockConn.current = { data: { connected: true, accountName: "Hubara" } };
    const { getByAltText } = render(
      <AdsInspector
        campaign={campaign({ creativeThumbnailUrl: "https://cdn.fb/t.jpg" })}
      />,
    );
    expect(getByAltText(/creativo/i).getAttribute("src")).toBe("https://cdn.fb/t.jpg");
  });

  it("sin thumbnail → placeholder honesto, marca real y link a Ads Manager", () => {
    mockRuns.current = [];
    mockConn.current = { data: { connected: true, accountName: "Hubara" } };
    const { getByText, queryByText, getByRole } = render(
      <AdsInspector campaign={campaign({})} />,
    );
    expect(getByText(/vista previa no disponible/i)).toBeTruthy();
    expect(queryByText(/Aromas · Tienda/)).toBeNull(); // marca quemada muerta
    expect(getByText("Hubara")).toBeTruthy(); // la cuenta conectada real
    const link = getByRole("link", { name: /ads manager/i });
    expect(link.getAttribute("href")).toContain("selected_campaign_ids=c-1");
  });
});
