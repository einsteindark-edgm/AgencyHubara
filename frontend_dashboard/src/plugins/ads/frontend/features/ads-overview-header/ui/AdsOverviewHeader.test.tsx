/**
 * KPIs de la cabecera de Ads — auditoría 2026-11-10.
 *
 * «Ganado» = el chat terminó en un pedido real (pagado o por cobrar); el
 * ingreso solo suma los pagados. Antes la cabecera decía «$100K · 4 ventas»
 * con 1 sola pagada, y el CAC dividía la inversión por los 4 pedidos. Ahora
 * dice cuántas están pagadas y cuántas por cobrar, y el CAC (como el ROAS) se
 * mide sobre ventas pagadas.
 */
import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";

import {
  DEFAULT_ADS_SELECTION,
  type AdsCampaign,
} from "@plugins/ads/frontend/entities/ads-campaign";

import { AdsOverviewHeader } from "./AdsOverviewHeader";

function campaign(over: Partial<AdsCampaign>): AdsCampaign {
  return {
    id: "AD_1",
    name: "Día del padre",
    started: 10,
    dates: "—",
    conversations: {
      no_reply: 2, nuevo: 0, activo: 1, calificado: 1, cotizado: 1, ganado: 4, perdido: 1,
    },
    spend: 200_000,
    revenue: 100_000,
    avgTicket: 100_000,
    paidSales: 1,
    daysRun: null,
    audience: null,
    metaCampaignId: null,
    status: null,
    llmCostUsd: null,
    llmTokens: null,
    ...over,
  } as AdsCampaign;
}

function kpi(container: HTMLElement, label: string): HTMLElement {
  const el = [...container.querySelectorAll(".ads-kpi")].find(
    (k) => k.querySelector(".ads-kpi-l")?.textContent === label,
  );
  if (!el) throw new Error(`KPI «${label}» no está`);
  return el as HTMLElement;
}

function header(c: AdsCampaign) {
  return render(
    <AdsOverviewHeader
      campaign={c}
      selection={DEFAULT_ADS_SELECTION}
      onSelectionChange={() => {}}
    />,
  );
}

describe("AdsOverviewHeader — ventas pagadas y por cobrar", () => {
  it("Ingresos dice cuántas ventas están pagadas y cuántas por cobrar", () => {
    const { container } = header(campaign({}));

    expect(kpi(container, "Ingresos atribuidos").textContent).toContain(
      "1 venta pagada · 3 por cobrar",
    );
  });

  it("con todo pagado no hay «por cobrar»", () => {
    const { container } = header(campaign({ paidSales: 4, revenue: 400_000 }));

    const text = kpi(container, "Ingresos atribuidos").textContent ?? "";
    expect(text).toContain("4 ventas pagadas");
    expect(text).not.toContain("por cobrar");
  });

  it("el CAC divide la inversión por las ventas pagadas, como el ROAS", () => {
    const { container } = header(campaign({}));

    expect(kpi(container, "CAC").querySelector(".ads-kpi-v")?.textContent).toBe("$200.000");
  });

  it("sin ventas pagadas el CAC no se calcula", () => {
    const { container } = header(campaign({ paidSales: 0, revenue: null }));

    expect(kpi(container, "CAC").querySelector(".ads-kpi-v")?.textContent).not.toMatch(/\$/);
  });
});
