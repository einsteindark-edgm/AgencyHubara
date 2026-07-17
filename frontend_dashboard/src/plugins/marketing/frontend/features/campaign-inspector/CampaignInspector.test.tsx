/**
 * Comportamiento del inspector: preview del template REAL (greeting +
 * header/body + línea de oferta + opt-out fijo), tab de validación con
 * checklist + stats reales para campañas enviadas, y tab de audiencia con
 * la audiencia REAL del envío + visor de conversaciones.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render } from "@testing-library/react";

import type {
  AudienceConversation,
  CampaignAudience,
} from "@plugins/marketing/frontend/entities/audience";

const statsMock = { data: undefined as unknown };

const audienceMock = {
  data: undefined as CampaignAudience | undefined,
  isPending: false,
  error: null as Error | null,
};

const conversationMock = {
  data: undefined as AudienceConversation | undefined,
  isPending: false,
  error: null as Error | null,
};

const updateMock = {
  mutate: vi.fn(),
  isPending: false,
  error: null as Error | null,
};

vi.mock("@plugins/marketing/frontend/entities/campaign", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  useCampaignStats: () => statsMock,
  useUpdateCampaign: () => updateMock,
}));

vi.mock("@plugins/marketing/frontend/entities/audience", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  useCampaignAudience: () => audienceMock,
  useAudienceConversation: () => conversationMock,
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
    excludedSessionIds: [],
    extraSessionIds: [],
    ...over,
  };
}

beforeEach(() => {
  updateMock.mutate.mockClear();
  statsMock.data = undefined;
  audienceMock.data = undefined;
  audienceMock.isPending = false;
  audienceMock.error = null;
  conversationMock.data = undefined;
  conversationMock.isPending = false;
  conversationMock.error = null;
});

const AUDIENCE: CampaignAudience = {
  recipients: [
    {
      sessionId: "wa_573001234567",
      phone: "+573001234567",
      customerName: "Camila",
      segment: "clientes",
    },
    {
      sessionId: "wa_573007654321",
      phone: "+573007654321",
      customerName: null,
      segment: "interesados",
    },
  ],
  skipped: [
    { sessionId: "wa_573000000001", phone: "+573000000001", reason: "excluido" },
    {
      sessionId: "wa_573000000002",
      phone: "+573000000002",
      reason: "campana_reciente",
    },
  ],
  total: 2,
};

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

describe("CampaignInspector — audiencia", () => {
  function openAudienceTab(campaign = makeCampaign()) {
    const utils = render(<CampaignInspector campaign={campaign} />);
    fireEvent.click(utils.getByRole("button", { name: /Audiencia/ }));
    return utils;
  }

  it("lista los destinatarios con total, nombre (o —), teléfono y chip de segmento", () => {
    audienceMock.data = AUDIENCE;
    const { getByText } = openAudienceTab();
    expect(getByText("2 destinatarios")).toBeTruthy();
    expect(getByText("Camila")).toBeTruthy();
    expect(getByText("—")).toBeTruthy(); // sin nombre
    expect(getByText("+573001234567")).toBeTruthy();
    expect(getByText("Clientes")).toBeTruthy();
    expect(getByText("Interesados")).toBeTruthy();
  });

  it("muestra los que no reciben con razón legible", () => {
    audienceMock.data = AUDIENCE;
    const { getByText } = openAudienceTab();
    expect(getByText("No reciben (2)")).toBeTruthy();
    expect(getByText("Atendido por humano u opt-out")).toBeTruthy();
    expect(getByText("Campaña reciente (<48h)")).toBeTruthy();
  });

  it("los quitados por el operador aparecen en No reciben con su label", () => {
    audienceMock.data = {
      ...AUDIENCE,
      skipped: [
        ...AUDIENCE.skipped,
        {
          sessionId: "wa_573001111111",
          phone: "+573001111111",
          reason: "quitado_por_operador",
        },
      ],
    };
    const { getByText } = openAudienceTab();
    expect(getByText("No reciben (3)")).toBeTruthy();
    expect(getByText("Quitado por vos")).toBeTruthy();
  });

  it("audiencia vacía: invita a elegir segmentos en el paso 4", () => {
    audienceMock.data = { recipients: [], skipped: [], total: 0 };
    const { getByText } = openAudienceTab(makeCampaign({ segments: [] }));
    expect(getByText(/Elegí segmentos en el paso 4/)).toBeTruthy();
  });

  it("click en una fila abre el visor con esa sesión y Escape lo cierra", () => {
    audienceMock.data = AUDIENCE;
    conversationMock.data = {
      sessionId: "wa_573007654321",
      messages: [
        { role: "user", kind: "text", content: "Hola bot", timestamp: null },
      ],
    };
    const { getByRole, getByText, queryByRole } = openAudienceTab();
    expect(queryByRole("dialog")).toBeNull();
    fireEvent.click(getByRole("button", { name: /\+573007654321/ }));
    expect(getByRole("dialog")).toBeTruthy();
    expect(getByText("Hola bot")).toBeTruthy();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(queryByRole("dialog")).toBeNull();
  });
});
