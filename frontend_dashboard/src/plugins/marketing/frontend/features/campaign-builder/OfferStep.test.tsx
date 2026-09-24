/**
 * Paso "Descuento / Producto" del constructor con la central de cupones:
 *  - el cupón se ELIGE de la central (solo activos y programados); ya no hay
 *    texto libre ni porcentaje/vigencia a mano
 *  - el % y "válido hasta <día>" salen del cupón (solo lectura) y se guardan
 *    en la campaña al elegirlo
 *  - atajo "Crear cupón" → el Page abre la vista Cupones con el formulario
 */
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render } from "@testing-library/react";

import type { Coupon } from "@plugins/marketing/frontend/entities/coupon";

function makeCoupon(over: Partial<Coupon> = {}): Coupon {
  return {
    promotionId: "promo_01",
    campaignId: "procamp_01",
    code: "AMOR27",
    campaignName: "AMOR Y AMISTAD 2026",
    percentage: 10,
    products: "all",
    startsOn: "2026-09-22",
    endsOn: "2026-09-27",
    endsOnLabel: "27 de septiembre",
    status: "active",
    state: "active",
    manageable: true,
    unmanageableReason: null,
    acceptsUnits: true,
    units: null,
    ...over,
  };
}

const couponsMock = {
  data: [
    makeCoupon(),
    makeCoupon({ promotionId: "promo_02", code: "PAPA20", percentage: 20, state: "scheduled", endsOnLabel: "21 de junio" }),
    makeCoupon({ promotionId: "promo_03", code: "PAUSADO5", percentage: 5, state: "paused" }),
    makeCoupon({ promotionId: "promo_04", code: "VIEJO15", percentage: 15, state: "expired" }),
    makeCoupon({ promotionId: "promo_05", code: "BORRADOR1", state: "draft" }),
    // Activo pero de monto fijo (creado en Medusa): la campaña no lo puede anunciar.
    makeCoupon({ promotionId: "promo_06", code: "FIJO5000", percentage: null, acceptsUnits: false, manageable: false }),
  ] as Coupon[] | undefined,
  isPending: false,
  error: null as Error | null,
};

vi.mock("@plugins/marketing/frontend/entities/coupon", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  useCoupons: () => couponsMock,
}));

vi.mock("@plugins/marketing/frontend/entities/product", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  useProducts: () => ({ data: [], isPending: false }),
}));

import type { CampaignDraft } from "./model/draft";
import { OfferStep } from "./ui/OfferStep";

function makeDraft(over: Partial<CampaignDraft> = {}): CampaignDraft {
  return {
    name: "Amor y amistad",
    goal: "discount_general",
    percent: 0,
    couponCode: "",
    validUntil: "",
    carouselHandles: [],
    segments: [],
    message: { header: "", body: "" },
    ...over,
  };
}

function renderStep(draft: CampaignDraft, onCreateCoupon = vi.fn()) {
  const onCommit = vi.fn();
  const utils = render(
    <OfferStep
      draft={draft}
      editable
      onPatch={vi.fn()}
      onCommit={onCommit}
      onCreateCoupon={onCreateCoupon}
    />,
  );
  return { ...utils, onCommit, onCreateCoupon };
}

describe("OfferStep — cupón de la central", () => {
  it("ofrece solo cupones activos y programados", () => {
    const { getByLabelText } = renderStep(makeDraft());
    const select = getByLabelText("Cupón") as HTMLSelectElement;
    const codes = [...select.options].map((o) => o.value).filter(Boolean);
    expect(codes).toEqual(["AMOR27", "PAPA20"]);
  });

  it("ya no acepta texto libre ni porcentaje/vigencia a mano", () => {
    const { queryByPlaceholderText, queryByRole } = renderStep(makeDraft());
    expect(queryByPlaceholderText("PAPA20")).toBeNull();
    expect(queryByPlaceholderText("15 de junio")).toBeNull();
    expect(queryByRole("spinbutton")).toBeNull();
  });

  it("elegir un cupón guarda código, % y vigencia del cupón", () => {
    const { getByLabelText, onCommit } = renderStep(makeDraft());
    fireEvent.change(getByLabelText("Cupón"), { target: { value: "PAPA20" } });
    expect(onCommit).toHaveBeenCalledWith({
      couponCode: "PAPA20",
      percent: 20,
      validUntil: "21 de junio",
    });
  });

  it("muestra el % y 'válido hasta' del cupón elegido, en solo lectura", () => {
    const { getByText } = renderStep(
      makeDraft({ couponCode: "AMOR27", percent: 10, validUntil: "27 de septiembre" }),
    );
    expect(getByText(/10% · válido hasta el 27 de septiembre/)).toBeTruthy();
  });

  it("avisa si el cupón de la campaña ya no está activo ni programado", () => {
    // Incidente AMOR/AMOR26: la campaña anunció un código que no servía.
    const { getByRole } = renderStep(makeDraft({ couponCode: "VIEJO15", percent: 15 }));
    expect(getByRole("alert").textContent).toMatch(/VIEJO15 no está activo ni programado/);
  });

  it("un cupón de monto fijo no se ofrece: la campaña solo anuncia porcentajes (D10)", () => {
    const { getByLabelText } = renderStep(makeDraft());
    const select = getByLabelText("Cupón") as HTMLSelectElement;
    expect([...select.options].map((o) => o.value)).not.toContain("FIJO5000");
  });

  it("si la campaña ya tiene un cupón de monto fijo, explica por qué hay que cambiarlo", () => {
    const { getByRole } = renderStep(makeDraft({ couponCode: "FIJO5000" }));
    expect(getByRole("alert").textContent).toMatch(
      /FIJO5000 es de monto fijo: la campaña solo puede anunciar cupones de porcentaje/,
    );
  });

  it("'Crear cupón' pide la vista Cupones con el formulario nuevo", () => {
    const { getByRole, onCreateCoupon } = renderStep(makeDraft());
    fireEvent.click(getByRole("button", { name: "Crear cupón" }));
    expect(onCreateCoupon).toHaveBeenCalledTimes(1);
  });

  it("un lanzamiento no lleva cupón", () => {
    const { queryByLabelText, getByText } = renderStep(makeDraft({ goal: "launch" }));
    expect(queryByLabelText("Cupón")).toBeNull();
    expect(getByText(/Un lanzamiento no lleva descuento/)).toBeTruthy();
  });
});
