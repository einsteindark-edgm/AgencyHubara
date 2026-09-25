/**
 * Panel "WhatsApp · envío de la campaña" (2026-09-25).
 *
 * Pedido del operador: una campaña de WhatsApp (sección Marketing) en Ads,
 * con las estadísticas de su envío como una campaña de Meta — enviados,
 * entregados, leídos, respuestas, ventas, gasto real, costo por respuesta y
 * por venta, ROAS — y su embudo. Los derivados se calculan en render.
 */
import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";
import type { AdsCampaign } from "@plugins/ads/frontend/entities/ads-campaign";

import { CampaignWhatsappKpis } from "./CampaignWhatsappKpis";

const SEND = {
  sent: 100,
  delivered: 90,
  read: 60,
  failed: 3,
  replied: 12,
  optedOut: 2,
  costUsdMicros: 1_125_000,
  costPending: 0,
  untracked: 0,
};

function campaign(overrides: Partial<AdsCampaign>): AdsCampaign {
  return {
    id: "mkt-amor",
    name: "Amor y amistad",
    sourceType: "hubara_campaign",
    started: 12,
    revenue: 94_000,
    conversations: {
      no_reply: 0, nuevo: 4, activo: 3, calificado: 2, cotizado: 1, ganado: 2, perdido: 0,
    },
    whatsappSend: SEND,
    ...overrides,
  } as AdsCampaign;
}

describe("CampaignWhatsappKpis", () => {
  it("pinta el embudo del envío: enviados → entregados → leídos → respondieron → ventas", () => {
    render(<CampaignWhatsappKpis campaign={campaign({})} />);

    const funnel = screen.getByRole("list", { name: "Embudo del envío" });
    const stages = within(funnel).getAllByRole("listitem");
    expect(stages.map((s) => s.getAttribute("data-stage"))).toEqual([
      "sent", "delivered", "read", "replied", "sales",
    ]);
    expect(within(screen.getByTestId("wa-send-delivered")).getByText("90")).toBeTruthy();
    // Cada etapa dice qué parte llegó sobre su base (entregados sobre enviados).
    expect(within(screen.getByTestId("wa-send-delivered")).getByText("90%")).toBeTruthy();
    expect(within(screen.getByTestId("wa-send-replied")).getByText("12")).toBeTruthy();
    expect(within(screen.getByTestId("wa-send-sales")).getByText("2")).toBeTruthy();
  });

  it("muestra el gasto real y lo que cuesta cada respuesta y cada venta", () => {
    render(<CampaignWhatsappKpis campaign={campaign({})} />);

    expect(within(screen.getByTestId("wa-send-spend")).getByText("US$1.13")).toBeTruthy();
    // ≈ COP a la tasa aproximada del dashboard (US$1 ≈ $4.000).
    expect(within(screen.getByTestId("wa-send-spend")).getByText("≈ $4.500")).toBeTruthy();
    expect(within(screen.getByTestId("wa-send-per-reply")).getByText("US$0.0938")).toBeTruthy();
    expect(within(screen.getByTestId("wa-send-per-sale")).getByText("US$0.5625")).toBeTruthy();
    // ROAS = ingresos / gasto en COP (94.000 / 4.500).
    expect(within(screen.getByTestId("wa-send-roas")).getByText("20.9×")).toBeTruthy();
    expect(within(screen.getByTestId("wa-send-failed")).getByText("3")).toBeTruthy();
    expect(within(screen.getByTestId("wa-send-opted-out")).getByText("2")).toBeTruthy();
  });

  it("avisa los mensajes que todavía no tienen precio y los enviados sin registro", () => {
    render(
      <CampaignWhatsappKpis
        campaign={campaign({ whatsappSend: { ...SEND, costPending: 7, untracked: 4 } })}
      />,
    );

    expect(screen.getByText(/7 mensajes todavía sin precio/)).toBeTruthy();
    expect(screen.getByText(/4 enviados antes del registro de entregas/)).toBeTruthy();
  });

  it("sin precio todavía, el gasto y sus derivados quedan pendientes (no $0)", () => {
    render(
      <CampaignWhatsappKpis
        campaign={campaign({ whatsappSend: { ...SEND, costUsdMicros: null, costPending: 100 } })}
      />,
    );

    expect(within(screen.getByTestId("wa-send-spend")).getByText("—")).toBeTruthy();
    expect(within(screen.getByTestId("wa-send-roas")).getByText("—")).toBeTruthy();
  });

  it("no aparece en una campaña de Meta", () => {
    const { container } = render(
      <CampaignWhatsappKpis campaign={campaign({ sourceType: "ad", whatsappSend: null })} />,
    );

    expect(container.innerHTML).toBe("");
  });
});
