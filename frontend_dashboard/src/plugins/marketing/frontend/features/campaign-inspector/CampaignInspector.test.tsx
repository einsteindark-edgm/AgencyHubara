/**
 * Comportamiento del inspector: preview del template REAL (greeting +
 * header/body + línea de oferta + opt-out fijo) y tab de validación con
 * checklist + stats reales para campañas enviadas.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render } from "@testing-library/react";

const statsMock = { data: undefined as unknown };

vi.mock("@plugins/marketing/frontend/entities/campaign", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  useCampaignStats: () => statsMock,
}));

import { CampaignInspector } from "./ui/CampaignInspector";
import type { Campaign } from "@plugins/marketing/frontend/entities/campaign";

function makeCampaign(over: Partial<Campaign> = {}): Campaign {
  return {
    id: "mkt-1",
    name: "Día del padre",
    status: "draft",
    goal: "discount_general",
    percent: 20,
    couponCode: "PAPA20",
    validUntil: "15 de junio",
    productHandle: null,
    segments: ["clientes"],
    message: {
      header: "¡Se acerca el Día del Padre!",
      body: "Tenemos 20% off.",
      footer: "",
      cta: "Ver catálogo",
    },
    templateName: "campaign_promo_marketing_v1",
    scheduleAtMs: null,
    createdAtMs: 1,
    updatedAtMs: 1,
    sentAtMs: null,
    sendResult: null,
    testSends: [],
    ...over,
  };
}

beforeEach(() => {
  statsMock.data = undefined;
});

describe("CampaignInspector — preview", () => {
  it("muestra el template real: saludo, mensaje, oferta y opt-out fijo", () => {
    const { getByText } = render(<CampaignInspector campaign={makeCampaign()} />);
    expect(getByText(/¡Hola Camila!/)).toBeTruthy();
    expect(getByText(/¡Se acerca el Día del Padre!/)).toBeTruthy();
    expect(
      getByText("Usa el código PAPA20 al pagar — válido hasta 15 de junio."),
    ).toBeTruthy();
    expect(getByText(/respóndeme "NO MÁS" y te doy de baja/)).toBeTruthy();
  });

  it("sin cupón pero con % usa la frase de descuento", () => {
    const { getByText } = render(
      <CampaignInspector campaign={makeCampaign({ couponCode: "", validUntil: "" })} />,
    );
    expect(
      getByText(/Aprovecha el 20% de descuento\. Escríbeme aquí y te muestro el catálogo\./),
    ).toBeTruthy();
  });
});

describe("CampaignInspector — validación", () => {
  it("lista el checklist con el estado de cada requisito", () => {
    const { getByRole, getByText } = render(
      <CampaignInspector campaign={makeCampaign({ segments: [] })} />,
    );
    fireEvent.click(getByRole("button", { name: /Validación/ }));
    expect(getByText("Objetivo definido")).toBeTruthy();
    expect(getByText("Audiencia elegida")).toBeTruthy();
    expect(getByText("Cupón (opcional)")).toBeTruthy();
  });

  it("campaña enviada: muestra las stats reales del endpoint", () => {
    statsMock.data = {
      campaignId: "mkt-1",
      status: "sent",
      planned: 42,
      sent: 40,
      failedCount: 2,
      skippedCount: 1,
      unitCostUsdMicros: 12_500,
      spentUsdMicros: 500_000,
      replied: 9,
      attributedOrders: 3,
      attributedRevenueCop: 364_500,
    };
    const { getByRole, getByText } = render(
      <CampaignInspector campaign={makeCampaign({ status: "sent" })} />,
    );
    fireEvent.click(getByRole("button", { name: /Validación/ }));
    expect(getByText("40")).toBeTruthy(); // enviados
    expect(getByText("9")).toBeTruthy(); // respondieron
    expect(getByText("$364.500")).toBeTruthy(); // revenue COP
    expect(getByText("US$0,5000")).toBeTruthy(); // gastado, micros→US$ 4 dec
  });
});
