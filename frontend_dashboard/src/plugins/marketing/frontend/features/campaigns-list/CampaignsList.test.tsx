/**
 * Comportamiento del sidebar de campañas: lista + filtros por estado +
 * selección + alta de campaña nueva (la mutación se stubea — acá se testea
 * la UX, no el HTTP).
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render } from "@testing-library/react";

const createMutate = vi.fn();
const createMock = {
  mutate: createMutate,
  isPending: false,
  error: null as Error | null,
};

vi.mock("@plugins/marketing/frontend/entities/campaign", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  useCreateCampaign: () => createMock,
}));

import { CampaignsList } from "./ui/CampaignsList";
import type { Campaign } from "@plugins/marketing/frontend/entities/campaign";

function makeCampaign(over: Partial<Campaign> = {}): Campaign {
  return {
    id: "mkt-1",
    name: "Día del padre",
    status: "draft",
    goal: "discount_general",
    percent: 20,
    couponCode: "PAPA20",
    validUntil: "",
    productHandle: null,
    segments: ["clientes"],
    message: { header: "", body: "b", footer: "", cta: "" },
    templateName: "campaign_promo_marketing_v1",
    scheduleAtMs: null,
    createdAtMs: 2,
    updatedAtMs: 2,
    sentAtMs: null,
    sendResult: null,
    testSends: [],
    excludedSessionIds: [],
    extraSessionIds: [],
    ...over,
  };
}

const sent = makeCampaign({
  id: "mkt-2",
  name: "Amor y amistad",
  status: "sent",
  percent: 0,
  couponCode: "",
  segments: ["interesados"],
  createdAtMs: 1,
  sendResult: {
    planned: 42,
    sent: 40,
    failed: [],
    skipped: [],
    unitCostUsdMicros: 12_500,
    spentUsdMicros: 500_000,
  },
});

beforeEach(() => {
  createMutate.mockClear();
});

describe("CampaignsList", () => {
  it("lista campañas con badge de % y chips de segmentos; click selecciona", () => {
    const onSelect = vi.fn();
    const { getByText } = render(
      <CampaignsList
        campaigns={[makeCampaign(), sent]}
        selectedId="mkt-1"
        onSelect={onSelect}
        onCreated={vi.fn()}
      />,
    );
    expect(getByText("Día del padre")).toBeTruthy();
    expect(getByText("-20%")).toBeTruthy();
    expect(getByText("Clientes")).toBeTruthy();

    fireEvent.click(getByText("Amor y amistad"));
    expect(onSelect).toHaveBeenCalledWith("mkt-2");
  });

  it("la campaña enviada muestra el resumen de envío", () => {
    const { getByText } = render(
      <CampaignsList
        campaigns={[sent]}
        selectedId={null}
        onSelect={vi.fn()}
        onCreated={vi.fn()}
      />,
    );
    expect(getByText(/40 enviados/)).toBeTruthy();
  });

  it("filtra por estado con las pills", () => {
    const { getByRole, queryByText } = render(
      <CampaignsList
        campaigns={[makeCampaign(), sent]}
        selectedId={null}
        onSelect={vi.fn()}
        onCreated={vi.fn()}
      />,
    );
    // La pill de filtro (name "Enviada 1"), no la card (que arranca con el
    // nombre de la campaña).
    fireEvent.click(getByRole("button", { name: /^Enviada \d+$/ }));
    expect(queryByText("Día del padre")).toBeNull();
    expect(queryByText("Amor y amistad")).toBeTruthy();
  });

  it("'Nueva campaña' dispara el POST", () => {
    const { getByRole } = render(
      <CampaignsList
        campaigns={[]}
        selectedId={null}
        onSelect={vi.fn()}
        onCreated={vi.fn()}
      />,
    );
    fireEvent.click(getByRole("button", { name: /Nueva campaña/ }));
    expect(createMutate).toHaveBeenCalledTimes(1);
  });
});
