/**
 * Composición de la Page de marketing (las features se stubean — acá se
 * testea el layout de 3 paneles + el empty state + el fallback de selección,
 * no los internals):
 *  - sin campañas → CTA de crear (la conexión POST se stubea)
 *  - con campañas y sin selección → cae a la primera (sidebar + builder +
 *    inspector montados)
 *  - selección via useSelection("marketing") — Pages sin props (F5).
 *  - selector Campañas | Cupones: cada vista conserva SU selección (claves
 *    distintas del PluginHost), "Nuevo cupón" abre el formulario y el atajo
 *    "Crear cupón" del constructor salta a Cupones con el formulario.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render } from "@testing-library/react";
import type { ReactNode } from "react";

vi.mock("@/shared/sdk", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  usePluginHost: () => ({ showSidebar: true, showInspector: true }),
  useSelection: (key: string) => [selections[key] ?? null, (id: string | null) => selectionSet(key, id)],
}));

const selections: Record<string, string | null> = {};
const selectionSet = vi.fn();
const createMutate = vi.fn();
const useCampaignsMock = vi.fn(() => ({ data: [] as unknown[] }));

vi.mock("@plugins/marketing/frontend/entities/campaign", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  useCampaigns: () => useCampaignsMock(),
  useCreateCampaign: () => ({ mutate: createMutate, isPending: false, error: null }),
}));

const useCouponsMock = vi.fn(() => ({ data: [] as unknown[], error: null as Error | null }));

vi.mock("@plugins/marketing/frontend/entities/coupon", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  useCoupons: () => useCouponsMock(),
}));

vi.mock("@plugins/marketing/frontend/features/coupons-list", () => ({
  CouponsList: (props: { selectedId: string | null; onNew: () => void; header?: ReactNode }) => (
    <div data-testid="coupons-list" data-selected={props.selectedId ?? ""}>
      {props.header}
      <button type="button" onClick={props.onNew}>
        Nuevo cupón
      </button>
    </div>
  ),
}));
vi.mock("@plugins/marketing/frontend/features/coupon-form", () => ({
  CouponForm: (props: { onCreated?: (id: string) => void }) => (
    <div data-testid="coupon-form">
      <button type="button" onClick={() => props.onCreated?.("promo_new")}>
        Simular creado
      </button>
    </div>
  ),
}));
vi.mock("@plugins/marketing/frontend/features/coupon-detail", () => ({
  CouponDetail: (props: { couponId: string }) => (
    <div data-testid="coupon-detail" data-coupon={props.couponId} />
  ),
}));
vi.mock("@plugins/marketing/frontend/features/coupon-sales", () => ({
  CouponSalesInspector: (props: { couponId: string }) => (
    <div data-testid="coupon-sales" data-coupon={props.couponId} />
  ),
}));

vi.mock("@plugins/marketing/frontend/features/campaigns-list", () => ({
  CampaignsList: (props: { selectedId: string | null; header?: ReactNode }) => (
    <div data-testid="campaigns-list" data-selected={props.selectedId ?? ""}>
      {props.header}
    </div>
  ),
}));
vi.mock("@plugins/marketing/frontend/features/campaign-builder", () => ({
  CampaignBuilder: (props: { campaign: { id: string }; onCreateCoupon?: () => void }) => (
    <div data-testid="campaign-builder" data-campaign={props.campaign.id}>
      <button type="button" onClick={props.onCreateCoupon}>
        Crear cupón
      </button>
    </div>
  ),
}));
vi.mock("@plugins/marketing/frontend/features/campaign-inspector", () => ({
  CampaignInspector: () => <div data-testid="campaign-inspector" />,
}));

import { MarketingSection } from "./MarketingSection";

function makeCampaign(id: string) {
  return {
    id,
    name: "C",
    status: "draft",
    goal: "",
    percent: 0,
    couponCode: "",
    validUntil: "",
    segments: [],
    message: { header: "", body: "" },
    templateName: "t",
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
  };
}

function makeCoupon(promotionId: string) {
  return { promotionId, code: promotionId.toUpperCase(), state: "active" };
}

beforeEach(() => {
  createMutate.mockClear();
  selectionSet.mockClear();
  for (const k of Object.keys(selections)) delete selections[k];
  useCampaignsMock.mockReturnValue({ data: [] });
  useCouponsMock.mockReturnValue({ data: [], error: null });
});

describe("MarketingSection — empty state", () => {
  it("sin campañas: CTA de crear la primera", () => {
    const { getByRole, queryByTestId } = render(<MarketingSection />);
    const cta = getByRole("button", { name: /Crear la primera campaña/ });
    expect(queryByTestId("campaign-builder")).toBeNull();

    fireEvent.click(cta);
    expect(createMutate).toHaveBeenCalledTimes(1);
  });
});

describe("MarketingSection — composición de 3 paneles", () => {
  it("con campañas y sin selección cae a la primera", () => {
    useCampaignsMock.mockReturnValue({
      data: [makeCampaign("mkt-a"), makeCampaign("mkt-b")],
    });
    const { getByTestId } = render(<MarketingSection />);
    expect(getByTestId("campaigns-list").dataset.selected).toBe("mkt-a");
    expect(getByTestId("campaign-builder").dataset.campaign).toBe("mkt-a");
    expect(getByTestId("campaign-inspector")).toBeTruthy();
  });
});

describe("MarketingSection — selector Campañas | Cupones", () => {
  it("arranca en Campañas; Cupones muestra lista + detalle + ventas del primero", () => {
    useCampaignsMock.mockReturnValue({ data: [makeCampaign("mkt-a")] });
    useCouponsMock.mockReturnValue({
      data: [makeCoupon("promo_a"), makeCoupon("promo_b")],
      error: null,
    });
    const { getByRole, getByTestId, queryByTestId } = render(<MarketingSection />);
    expect(getByTestId("campaigns-list")).toBeTruthy();
    expect(queryByTestId("coupons-list")).toBeNull();

    fireEvent.click(getByRole("tab", { name: "Cupones" }));
    expect(queryByTestId("campaigns-list")).toBeNull();
    expect(queryByTestId("campaign-builder")).toBeNull();
    expect(getByTestId("coupons-list").dataset.selected).toBe("promo_a");
    expect(getByTestId("coupon-detail").dataset.coupon).toBe("promo_a");
    expect(getByTestId("coupon-sales").dataset.coupon).toBe("promo_a");
  });

  it("cada vista conserva su propia selección", () => {
    selections["marketing"] = "mkt-b";
    selections["marketing-coupons"] = "promo_b";
    useCampaignsMock.mockReturnValue({ data: [makeCampaign("mkt-a"), makeCampaign("mkt-b")] });
    useCouponsMock.mockReturnValue({
      data: [makeCoupon("promo_a"), makeCoupon("promo_b")],
      error: null,
    });
    const { getByRole, getByTestId } = render(<MarketingSection />);
    expect(getByTestId("campaign-builder").dataset.campaign).toBe("mkt-b");
    fireEvent.click(getByRole("tab", { name: "Cupones" }));
    expect(getByTestId("coupon-detail").dataset.coupon).toBe("promo_b");
    fireEvent.click(getByRole("tab", { name: "Campañas" }));
    expect(getByTestId("campaign-builder").dataset.campaign).toBe("mkt-b");
  });

  it("'Nuevo cupón' abre el formulario; al crearlo lo selecciona", () => {
    useCouponsMock.mockReturnValue({ data: [makeCoupon("promo_a")], error: null });
    const { getByRole, getByTestId, queryByTestId } = render(<MarketingSection />);
    fireEvent.click(getByRole("tab", { name: "Cupones" }));
    fireEvent.click(getByRole("button", { name: "Nuevo cupón" }));
    expect(getByTestId("coupon-form")).toBeTruthy();
    expect(queryByTestId("coupon-detail")).toBeNull();
    expect(queryByTestId("coupon-sales")).toBeNull();

    fireEvent.click(getByRole("button", { name: "Simular creado" }));
    expect(selectionSet).toHaveBeenCalledWith("marketing-coupons", "promo_new");
    expect(queryByTestId("coupon-form")).toBeNull();
  });

  it("'Crear cupón' del constructor salta a Cupones con el formulario", () => {
    useCampaignsMock.mockReturnValue({ data: [makeCampaign("mkt-a")] });
    const { getByRole, getByTestId } = render(<MarketingSection />);
    fireEvent.click(getByRole("button", { name: "Crear cupón" }));
    expect(getByRole("tab", { name: "Cupones" }).getAttribute("aria-selected")).toBe("true");
    expect(getByTestId("coupon-form")).toBeTruthy();
  });

  it("sin cupones: CTA para crear el primero", () => {
    const { getByRole, getByTestId } = render(<MarketingSection />);
    fireEvent.click(getByRole("tab", { name: "Cupones" }));
    fireEvent.click(getByRole("button", { name: "Crear el primer cupón" }));
    expect(getByTestId("coupon-form")).toBeTruthy();
  });
});
