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
const importMock = {
  mutate: vi.fn(),
  isPending: false,
  error: null as Error | null,
  data: undefined as unknown,
  reset: vi.fn(),
};
const clearContactsMock = {
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
  useImportContacts: () => importMock,
  useClearContacts: () => clearContactsMock,
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

const PRODUCTS = [
  {
    handle: "vela-buda",
    title: "Vela Buda Zen",
    sku: "HUB-BUDA",
    category: "Velas",
    priceAmount: 45_000,
    currency: "cop",
    thumbnail: null,
  },
  {
    handle: "cubo-love",
    title: "Cubo Love",
    sku: "HUB-CUBOLOVE",
    category: "Velas",
    priceAmount: 38_000,
    currency: "cop",
    thumbnail: null,
  },
];

vi.mock("@plugins/marketing/frontend/entities/promotion", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  usePromotions: () => ({
    data: {
      promotions: [
        {
          code: "MAMA15",
          discountType: "percentage",
          value: 15,
          targetType: "items",
          name: "Madres",
          endsAtMs: null,
          minSubtotalCop: null,
          productCount: 0,
        },
      ],
      unavailable: false,
    },
    isPending: false,
  }),
}));

vi.mock("@plugins/marketing/frontend/entities/product", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  useProducts: () => ({ data: PRODUCTS, isPending: false }),
}));

/** Audiencia REAL del endpoint — undefined = cargando (fallback a la
 *  estimación por segmento). */
const audienceMock = {
  data: undefined as
    | { recipients: never[]; skipped: never[]; total: number }
    | undefined,
  isPending: false,
  error: null as Error | null,
};

vi.mock("@plugins/marketing/frontend/entities/audience", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  useCampaignAudience: () => audienceMock,
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
    segments: ["clientes", "interesados"],
    message: {
      header: "¡Se acerca el Día del Padre!",
      body: "Tenemos 20% off en toda la tienda.",
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
    importedContacts: [],
    carouselHandles: [],
    ...over,
  };
}

beforeEach(() => {
  updateMock.mutate.mockClear();
  sendMock.mutate.mockClear();
  testMock.mutate.mockClear();
  cancelMock.mutate.mockClear();
  importMock.mutate.mockClear();
  clearContactsMock.mutate.mockClear();
  importMock.data = undefined;
  importMock.error = null;
  testMock.error = null;
  sendMock.error = null;
  audienceMock.data = undefined;
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
    // Sin data del endpoint: fallback a la estimación por segmento.
    // 18 + 24 = 42 destinatarios × 12.500 micros = US$0,53 ≈ $2.100 COP.
    // El total aparece en la barra de audiencia Y en el resumen de envío.
    expect(getAllByText(/42 destinatarios/).length).toBeGreaterThanOrEqual(2);
    expect(getAllByText(/US\$0,53/).length).toBeGreaterThanOrEqual(1);
    expect(getAllByText(/\$2\.100/).length).toBeGreaterThanOrEqual(1);
    expect(getByText(/6 contactos excluidos/)).toBeTruthy();
  });

  it("usa el total REAL del endpoint de audiencia cuando hay data", () => {
    // La curaduría manual (quitados/agregados) hace que el total real
    // difiera de la suma por segmento: 40 × 12.500 micros = US$0,50.
    audienceMock.data = { recipients: [], skipped: [], total: 40 };
    const { getAllByText, queryAllByText } = render(
      <CampaignBuilder campaign={makeCampaign()} />,
    );
    expect(getAllByText(/40 destinatarios/).length).toBeGreaterThanOrEqual(2);
    expect(getAllByText(/US\$0,50/).length).toBeGreaterThanOrEqual(1);
    expect(queryAllByText(/42 destinatarios/)).toHaveLength(0);
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

describe("CampaignBuilder — mensaje", () => {
  it("no ofrece pie ni botón: la plantilla aprobada no los tiene", () => {
    const { queryByText, getByText } = render(<CampaignBuilder campaign={makeCampaign()} />);
    expect(queryByText(/Pie \(fijo\)/)).toBeNull();
    expect(queryByText(/Botón \(fijo\)/)).toBeNull();
    // La ayuda dice qué SÍ viaja y que el texto de baja es fijo.
    expect(getByText(/viajan: encabezado, cuerpo y oferta/)).toBeTruthy();
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

  it("acepta el celular sin indicativo y lo manda tal cual (el backend normaliza)", () => {
    const { getByRole, getByPlaceholderText } = render(
      <CampaignBuilder campaign={makeCampaign()} />,
    );
    fireEvent.change(getByPlaceholderText(/573/), {
      target: { value: "300 123 4567" },
    });
    fireEvent.click(getByRole("button", { name: "Enviar prueba" }));
    expect(testMock.mutate).toHaveBeenCalledWith("300 123 4567");
  });

  it("superficie el detail del error del backend (422 número inválido / 502 Meta)", () => {
    testMock.error = new ApiError(502, {
      detail:
        "WhatsApp rechazó el envío de prueba: WhatsApp template send failed (non-retryable, code=132001)",
    });
    const { getByText, queryByText } = render(
      <CampaignBuilder campaign={makeCampaign()} />,
    );
    expect(getByText(/WhatsApp rechazó el envío de prueba/)).toBeTruthy();
    // La ayuda ya no exige conversación previa (sale del número del negocio).
    expect(queryByText(/debe haber chateado/)).toBeNull();
    expect(getByText(/No necesita conversación previa/)).toBeTruthy();
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

describe("CampaignBuilder — contactos importados (CSV)", () => {
  it("elegir un archivo dispara la importación con ese File", () => {
    const { getByLabelText } = render(<CampaignBuilder campaign={makeCampaign()} />);
    const input = getByLabelText("Importar contactos (CSV)") as HTMLInputElement;
    const file = new File(["3001234567\n"], "lista.csv", { type: "text/csv" });
    fireEvent.change(input, { target: { files: [file] } });
    expect(importMock.mutate).toHaveBeenCalledTimes(1);
    expect(importMock.mutate.mock.calls[0]?.[0]).toBe(file);
  });

  it("muestra cuántos contactos hay importados y permite vaciarlos", () => {
    const { getByText, getByRole } = render(
      <CampaignBuilder
        campaign={makeCampaign({
          importedContacts: [
            { phone: "573001234567", name: "Camila" },
            { phone: "573109876543", name: null },
          ],
        })}
      />,
    );
    expect(getByText(/2 contactos importados/)).toBeTruthy();
    fireEvent.click(getByRole("button", { name: "Quitar importados" }));
    expect(clearContactsMock.mutate).toHaveBeenCalledTimes(1);
  });

  it("resume la última importación: importados, duplicados y rechazados por línea", () => {
    importMock.data = {
      imported: 3,
      duplicates: 1,
      rejected: [{ line: 4, reason: "numero_invalido" }],
      rejectedCount: 1,
      total: 3,
      campaign: makeCampaign(),
    };
    const { getByText } = render(<CampaignBuilder campaign={makeCampaign()} />);
    expect(getByText(/3 nuevos/)).toBeTruthy();
    expect(getByText(/1 repetido/)).toBeTruthy();
    expect(getByText(/línea 4/)).toBeTruthy();
  });

  it("con solo importados (sin segmentos) la campaña se puede enviar", () => {
    const { getByRole } = render(
      <CampaignBuilder
        campaign={makeCampaign({
          goal: "discount_general",
          percent: 10,
          message: { header: "", body: "Hola" },
          segments: [],
          importedContacts: [{ phone: "573001234567", name: null }],
        })}
      />,
    );
    const button = getByRole("button", { name: "Enviar ahora" }) as HTMLButtonElement;
    expect(button.disabled).toBe(false);
  });
});

describe("CampaignBuilder — sin selector de producto único", () => {
  it.each(["discount_product", "launch"] as const)(
    "objetivo %s: solo el carrusel elige productos y el envío no exige un producto único",
    (goal) => {
      const { queryByRole, getByRole } = render(
        <CampaignBuilder
          campaign={makeCampaign({
            goal,
            percent: goal === "launch" ? 0 : 10,
            segments: ["clientes"],
            message: { header: "", body: "Hola" },
          })}
        />,
      );
      expect(queryByRole("button", { name: /Elegir producto del catálogo/ })).toBeNull();
      expect(getByRole("button", { name: /Agregar producto al carrusel/ })).toBeTruthy();
      expect((getByRole("button", { name: "Enviar ahora" }) as HTMLButtonElement).disabled).toBe(false);
    },
  );
});

describe("CampaignBuilder — carrusel de productos", () => {
  it("agregar un producto al carrusel dispara el PUT con carousel_handles", () => {
    const { getByRole, getByText } = render(
      <CampaignBuilder
        campaign={makeCampaign({ goal: "launch", carouselHandles: ["vela-buda"] })}
      />,
    );
    // El chip del ya elegido está; se agrega el segundo desde el picker.
    expect(getByText("Vela Buda Zen")).toBeTruthy();
    fireEvent.click(getByRole("button", { name: /Agregar producto al carrusel/ }));
    fireEvent.click(getByRole("button", { name: /Cubo Love/ }));
    expect(updateMock.mutate).toHaveBeenCalledTimes(1);
    expect(updateMock.mutate.mock.calls[0]?.[0]).toMatchObject({
      carouselHandles: ["vela-buda", "cubo-love"],
    });
  });

  it("quitar un producto del carrusel dispara el PUT sin ese handle", () => {
    const { getByRole } = render(
      <CampaignBuilder
        campaign={makeCampaign({
          goal: "launch",
          carouselHandles: ["vela-buda", "cubo-love"],
        })}
      />,
    );
    fireEvent.click(getByRole("button", { name: "Quitar Vela Buda Zen del carrusel" }));
    expect(updateMock.mutate.mock.calls[0]?.[0]).toMatchObject({
      carouselHandles: ["cubo-love"],
    });
  });

  it("con un solo producto avisa que el carrusel necesita 2 a 10", () => {
    const { getByText } = render(
      <CampaignBuilder campaign={makeCampaign({ goal: "launch", carouselHandles: ["vela-buda"] })} />,
    );
    expect(getByText(/entre 2 y 10/)).toBeTruthy();
  });
});

describe("CampaignBuilder — cupón", () => {
  it("el código se sanea a letras y números (VELAS_10 → VELAS10) al escribir", () => {
    const { getByPlaceholderText } = render(
      <CampaignBuilder campaign={makeCampaign({ goal: "discount_general" })} />,
    );
    const input = getByPlaceholderText("PAPA20") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "velas_10" } });
    expect(input.value).toBe("VELAS10");
  });

  it("ofrece los cupones vigentes de Medusa y elegir uno dispara el PUT", () => {
    const { getByRole } = render(
      <CampaignBuilder campaign={makeCampaign({ goal: "discount_general" })} />,
    );
    fireEvent.click(getByRole("button", { name: /MAMA15 · 15%/ }));
    expect(updateMock.mutate.mock.calls[0]?.[0]).toMatchObject({ couponCode: "MAMA15" });
  });
});
