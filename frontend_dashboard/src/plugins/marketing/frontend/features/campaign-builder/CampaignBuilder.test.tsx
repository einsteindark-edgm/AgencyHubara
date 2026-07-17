/**
 * Comportamiento del builder de 6 pasos:
 *  - read-only cuando la campaña ya no es editable (sent/sending/failed)
 *  - guardado explícito (PUT) al blur del nombre
 *  - barra de audiencia con destinatarios + costo + excluidos
 *  - "Enviar ahora" con confirmación inline de DOS pasos (regla #6: cero
 *    window.confirm) y el 404 del envío de prueba superficiado con su detail.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render } from "@testing-library/react";

import { ApiError } from "@/shared/sdk";

const updateMock = {
  mutate: vi.fn(),
  isPending: false,
  error: null as Error | null,
};
const sendMock = {
  mutate: vi.fn(),
  isPending: false,
  error: null as Error | null,
  data: undefined as unknown,
  reset: vi.fn(),
};
const testMock = {
  mutate: vi.fn(),
  isPending: false,
  error: null as Error | null,
  data: undefined as unknown,
  reset: vi.fn(),
};
const cancelMock = {
  mutate: vi.fn(),
  isPending: false,
  error: null as Error | null,
};

vi.mock("@plugins/marketing/frontend/entities/campaign", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  useUpdateCampaign: () => updateMock,
  useSendCampaign: () => sendMock,
  useTestSend: () => testMock,
  useCancelCampaign: () => cancelMock,
}));

vi.mock("@plugins/marketing/frontend/entities/segment", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  useSegmentsInfo: () => ({
    data: {
      segments: [
        { key: "clientes", label: "Clientes", description: "Ya compraron", count: 18 },
        { key: "interesados", label: "Interesados", description: "Con intención", count: 24 },
        { key: "frios", label: "Fríos", description: "Consultaron", count: 57 },
      ],
      excludedCount: 6,
      unitCostUsdMicros: 12_500,
      currency: "USD",
    },
  }),
}));

vi.mock("@plugins/marketing/frontend/entities/product", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  useProducts: () => ({ data: [], isPending: false }),
}));

import { CampaignBuilder } from "./ui/CampaignBuilder";
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
    segments: ["clientes", "interesados"],
    message: {
      header: "¡Se acerca el Día del Padre!",
      body: "Tenemos 20% off en toda la tienda.",
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
  updateMock.mutate.mockClear();
  sendMock.mutate.mockClear();
  testMock.mutate.mockClear();
  cancelMock.mutate.mockClear();
  testMock.error = null;
  sendMock.error = null;
});

describe("CampaignBuilder — campaña programada", () => {
  it("muestra 'Cancelar programación' y dispara el POST /cancel", () => {
    const { getByRole } = render(
      <CampaignBuilder
        campaign={makeCampaign({ status: "scheduled", scheduleAtMs: 9_999_999_999_999 })}
      />,
    );
    fireEvent.click(getByRole("button", { name: "Cancelar programación" }));
    expect(cancelMock.mutate).toHaveBeenCalledTimes(1);
  });

  it("una campaña draft NO muestra el botón de cancelar", () => {
    const { queryByRole } = render(<CampaignBuilder campaign={makeCampaign()} />);
    expect(queryByRole("button", { name: "Cancelar programación" })).toBeNull();
  });
});

describe("CampaignBuilder — read-only", () => {
  it("campaña enviada: banner de solo lectura + inputs deshabilitados", () => {
    const { getByText, getByLabelText } = render(
      <CampaignBuilder campaign={makeCampaign({ status: "sent" })} />,
    );
    expect(getByText(/solo lectura/i)).toBeTruthy();
    expect(
      (getByLabelText("Nombre de la campaña") as HTMLInputElement).disabled,
    ).toBe(true);
  });
});

describe("CampaignBuilder — guardado explícito", () => {
  it("blur del nombre dispara el PUT con el patch", () => {
    const { getByLabelText } = render(<CampaignBuilder campaign={makeCampaign()} />);
    const input = getByLabelText("Nombre de la campaña");
    fireEvent.change(input, { target: { value: "Padre 2026" } });
    fireEvent.blur(input);
    expect(updateMock.mutate).toHaveBeenCalledTimes(1);
    expect(updateMock.mutate.mock.calls[0]?.[0]).toMatchObject({
      name: "Padre 2026",
    });
  });
});

describe("CampaignBuilder — audiencia", () => {
  it("muestra destinatarios, costo estimado (USD + COP aprox) y excluidos", () => {
    const { getAllByText, getByText } = render(
      <CampaignBuilder campaign={makeCampaign()} />,
    );
    // 18 + 24 = 42 destinatarios × 12.500 micros = US$0,53 ≈ $2.100 COP.
    // El total aparece en la barra de audiencia Y en el resumen de envío.
    expect(getAllByText(/42 destinatarios/).length).toBeGreaterThanOrEqual(2);
    expect(getAllByText(/US\$0,53/).length).toBeGreaterThanOrEqual(1);
    expect(getAllByText(/\$2\.100/).length).toBeGreaterThanOrEqual(1);
    expect(getByText(/6 contactos excluidos/)).toBeTruthy();
  });
});

describe("CampaignBuilder — envío con confirmación de dos pasos", () => {
  it("primer click arma la confirmación; Confirmar dispara el POST", () => {
    const { getByRole, getByText } = render(
      <CampaignBuilder campaign={makeCampaign()} />,
    );
    fireEvent.click(getByRole("button", { name: "Enviar ahora" }));
    expect(sendMock.mutate).not.toHaveBeenCalled();
    expect(getByText(/¿Confirmar envío a 42 contactos por US\$0,53\?/)).toBeTruthy();

    fireEvent.click(getByRole("button", { name: "Confirmar" }));
    expect(sendMock.mutate).toHaveBeenCalledTimes(1);
    expect(sendMock.mutate.mock.calls[0]?.[0]).toBeNull();
  });

  it("Cancelar desarma la confirmación sin enviar", () => {
    const { getByRole, queryByText } = render(
      <CampaignBuilder campaign={makeCampaign()} />,
    );
    fireEvent.click(getByRole("button", { name: "Enviar ahora" }));
    fireEvent.click(getByRole("button", { name: "Cancelar" }));
    expect(queryByText(/¿Confirmar envío/)).toBeNull();
    expect(sendMock.mutate).not.toHaveBeenCalled();
  });

  it("campaña incompleta: el botón de envío queda deshabilitado", () => {
    const { getByRole } = render(
      <CampaignBuilder
        campaign={makeCampaign({ goal: "", segments: [], percent: 0, couponCode: "" })}
      />,
    );
    expect(
      (getByRole("button", { name: "Enviar ahora" }) as HTMLButtonElement).disabled,
    ).toBe(true);
  });
});

describe("CampaignBuilder — envío de prueba", () => {
  it("dispara el POST /test con el teléfono", () => {
    const { getByRole, getByPlaceholderText } = render(
      <CampaignBuilder campaign={makeCampaign()} />,
    );
    fireEvent.change(getByPlaceholderText(/573/), {
      target: { value: "573001234567" },
    });
    fireEvent.click(getByRole("button", { name: "Enviar prueba" }));
    expect(testMock.mutate).toHaveBeenCalledWith("573001234567");
  });

  it("superficie el detail del 404 (número sin conversación previa)", () => {
    testMock.error = new ApiError(404, {
      detail:
        "El número 573000000000 no tiene conversación previa con el bot — escríbele primero al WhatsApp del negocio y reintenta",
    });
    const { getByText } = render(<CampaignBuilder campaign={makeCampaign()} />);
    expect(getByText(/no tiene conversación previa con el bot/)).toBeTruthy();
  });

  it("lista el historial de pruebas enviadas", () => {
    const { getByText } = render(
      <CampaignBuilder
        campaign={makeCampaign({
          testSends: [{ phone: "573001234567", atMs: 1_784_650_000_000, waMessageId: "w" }],
        })}
      />,
    );
    expect(getByText(/573001234567/)).toBeTruthy();
  });
});
