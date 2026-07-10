/**
 * KPIs de Meta POR CAMPAÑA seleccionada (pedido 2026-07-09): gasto, impresiones,
 * clicks, conversaciones, CPC y costo/conv dentro de la vista de la campaña —
 * responden a la ventana de fecha (los datos vienen del merge del endpoint de
 * campañas, que ya recibe days/from/to). Gestión pausar/activar por campaña
 * (salvada del panel estático) cuando el token puede gestionar.
 */

import { describe, expect, it, vi } from "vitest";
import { render } from "@testing-library/react";

const mockConnection = vi.hoisted(() => ({ current: {} as object }));

vi.mock("@plugins/ads/frontend/entities/meta-connection", () => ({
  useMetaConnection: () => mockConnection.current,
  useSetCampaignStatus: () => ({ mutate: vi.fn(), isPending: false }),
}));

import { CampaignMetaKpis } from "./CampaignMetaKpis";
import type { AdsCampaign } from "@plugins/ads/frontend/entities/ads-campaign";

function campaign(over: Partial<AdsCampaign>): AdsCampaign {
  return {
    id: "c-1",
    name: "Día del padre",
    sourceType: "ad",
    started: 0,
    conversations: null,
    spend: null,
    impressions: null,
    reach: null,
    clicks: null,
    conversationsStarted: null,
    status: null,
    metaCampaignId: null,
    ...over,
  } as AdsCampaign;
}

describe("CampaignMetaKpis — métricas Meta de la campaña seleccionada", () => {
  it("sin datos Meta → no renderiza nada", () => {
    mockConnection.current = { data: { connected: true, canManage: false } };
    const { container } = render(<CampaignMetaKpis campaign={campaign({})} />);
    expect(container.innerHTML).toBe("");
  });

  it("pinta gasto/impresiones/clicks/conversaciones y deriva CPC + costo/conv", () => {
    mockConnection.current = { data: { connected: true, canManage: false } };
    const { getByText } = render(
      <CampaignMetaKpis
        campaign={campaign({
          metaCampaignId: "c-1",
          spend: 896823,
          impressions: 45000,
          clicks: 571,
          conversationsStarted: 205,
        })}
      />,
    );
    expect(getByText(/896\.823/)).toBeTruthy(); // gasto COP
    expect(getByText(/45\.000/)).toBeTruthy(); // impresiones
    expect(getByText("571")).toBeTruthy(); // clicks
    expect(getByText("205")).toBeTruthy(); // conversaciones
    expect(getByText(/1\.571/)).toBeTruthy(); // CPC = 896823/571
    expect(getByText(/4\.375/)).toBeTruthy(); // costo/conv = 896823/205
  });

  it("con canManage y campaña Meta → botón de pausar/activar", () => {
    mockConnection.current = { data: { connected: true, canManage: true } };
    const { queryByRole } = render(
      <CampaignMetaKpis
        campaign={campaign({ metaCampaignId: "c-1", spend: 100, status: "active" })}
      />,
    );
    expect(queryByRole("button", { name: /pausar/i })).toBeTruthy();
  });

  it("sin canManage → sin botón de gestión", () => {
    mockConnection.current = { data: { connected: true, canManage: false } };
    const { queryByRole } = render(
      <CampaignMetaKpis
        campaign={campaign({ metaCampaignId: "c-1", spend: 100, status: "active" })}
      />,
    );
    expect(queryByRole("button")).toBeNull();
  });
});
