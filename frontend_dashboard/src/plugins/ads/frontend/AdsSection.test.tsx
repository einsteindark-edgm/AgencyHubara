/**
 * Regresión del incidente 2026-07-08 (vault vacío tras replacement de la caja):
 * con CERO campañas, la sección hacía early-return del empty-state ANTES del
 * header → el botón "Conectar con Meta" nunca se montaba. Pero la conexión a
 * Meta no depende de que haya campañas derivadas del vault — es al revés:
 * conectar es lo que llena los KPIs y muestra las campañas reales de Meta.
 *
 * Contrato observable: la sección monta `ConnectMeta` y `MetaInsightsPanel`
 * SIEMPRE, haya o no campañas. Las features se stubean (acá se testea la
 * composición de la Page, no sus internals).
 */

import { describe, expect, it, vi } from "vitest";
import { render } from "@testing-library/react";

vi.mock("@/shared/lib", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  usePluginHost: () => ({ showSidebar: true, showInspector: true }),
}));

vi.mock("@plugins/ads/frontend/entities/ads-campaign", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  useAdsCampaigns: () => ({ data: [] }),
  useAttributedConversations: () => ({ data: [] }),
  useDailySeries: () => ({ data: [] }),
}));

vi.mock("@plugins/ads/frontend/entities/ad-analysis-run", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  useRun: () => ({ data: undefined }),
}));

vi.mock("@plugins/ads/frontend/features/connect-meta", () => ({
  ConnectMeta: () => <div data-testid="connect-meta" />,
}));

vi.mock("@plugins/ads/frontend/features/ads-meta-insights", () => ({
  MetaInsightsPanel: () => <div data-testid="meta-insights" />,
}));

import { AdsSection } from "./AdsSection";

describe("AdsSection — empty-state (vault sin campañas)", () => {
  it("monta ConnectMeta y MetaInsightsPanel aunque no haya campañas", () => {
    const { getByTestId, getByText } = render(<AdsSection />);

    expect(getByTestId("connect-meta")).toBeTruthy();
    expect(getByTestId("meta-insights")).toBeTruthy();
    // El empty-state sigue comunicando que no hay campañas derivadas del vault.
    expect(getByText(/sin campañas/i)).toBeTruthy();
  });
});
