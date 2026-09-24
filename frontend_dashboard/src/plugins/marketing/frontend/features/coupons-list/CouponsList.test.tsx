/**
 * Sidebar de cupones: chips de filtro por estado (con conteo), cada cupón
 * con código, %, estado y "quedan X de Y" cuando tiene cupo, selección y el
 * botón "Nuevo cupón" (el Page abre el formulario).
 */
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render } from "@testing-library/react";

import type { Coupon } from "@plugins/marketing/frontend/entities/coupon";

import { CouponsList } from "./ui/CouponsList";

function makeCoupon(over: Partial<Coupon> = {}): Coupon {
  return {
    promotionId: "promo_01",
    campaignId: "procamp_01",
    code: "AMOR27",
    campaignName: "AMOR Y AMISTAD 2026",
    percentage: 10,
    products: ["prod_cubo"],
    startsOn: "2026-09-22",
    endsOn: "2026-09-27",
    endsOnLabel: "27 de septiembre",
    status: "active",
    state: "active",
    manageable: true,
    unmanageableReason: null,
    acceptsUnits: true,
    units: { total: 5, left: 3 },
    ...over,
  };
}

const COUPONS = [
  makeCoupon(),
  makeCoupon({ promotionId: "promo_02", code: "PAPA20", percentage: 20, state: "scheduled", units: null }),
  makeCoupon({ promotionId: "promo_03", code: "BORRADOR1", percentage: 5, state: "draft", status: "draft", units: null }),
  makeCoupon({ promotionId: "promo_04", code: "VIEJO15", percentage: 15, state: "expired", units: null }),
];

function renderList(over: Partial<Parameters<typeof CouponsList>[0]> = {}) {
  const props = {
    coupons: COUPONS,
    selectedId: null,
    onSelect: vi.fn(),
    onNew: vi.fn(),
    ...over,
  };
  return { ...render(<CouponsList {...props} />), props };
}

describe("CouponsList", () => {
  it("muestra código, porcentaje, estado y lo que queda del cupo", () => {
    const { getByText, getAllByText } = renderList();
    expect(getByText("AMOR27")).toBeTruthy();
    expect(getByText("-10%")).toBeTruthy();
    expect(getAllByText("Activo").length).toBeGreaterThanOrEqual(1);
    expect(getByText("quedan 3 de 5")).toBeTruthy();
    // Sin cupo no se inventa el "quedan".
    expect(getAllByText(/quedan/)).toHaveLength(1);
  });

  it("filtra por estado con los chips (con conteo)", () => {
    const { getByRole, queryByText } = renderList();
    expect(getByRole("button", { name: "Todos 4" })).toBeTruthy();
    fireEvent.click(getByRole("button", { name: "Programados 1" }));
    expect(queryByText("PAPA20")).toBeTruthy();
    expect(queryByText("AMOR27")).toBeNull();

    fireEvent.click(getByRole("button", { name: "Vencidos 1" }));
    expect(queryByText("VIEJO15")).toBeTruthy();
    expect(queryByText("PAPA20")).toBeNull();

    fireEvent.click(getByRole("button", { name: "Pausados 0" }));
    expect(queryByText(/Sin cupones en este estado/)).toBeTruthy();
  });

  it("click en un cupón lo selecciona", () => {
    const { getByText, props } = renderList();
    fireEvent.click(getByText("PAPA20"));
    expect(props.onSelect).toHaveBeenCalledWith("promo_02");
  });

  it("'Nuevo cupón' pide el formulario de alta", () => {
    const { getByRole, props } = renderList({ coupons: [] });
    fireEvent.click(getByRole("button", { name: /Nuevo cupón/ }));
    expect(props.onNew).toHaveBeenCalledTimes(1);
  });

  it("muestra el aviso cuando Medusa no responde", () => {
    const { getByText } = renderList({
      coupons: [],
      notice: "Medusa no responde ahora mismo; no se hizo ningún cambio.",
    });
    expect(getByText(/Medusa no responde/)).toBeTruthy();
  });

  it("mientras carga no dice 0 ni 'Sin cupones' (D9)", () => {
    const { getByText, queryByText, queryAllByText } = renderList({ coupons: [], loading: true });
    expect(getByText("Cargando cupones…")).toBeTruthy();
    expect(queryByText(/Sin cupones en este estado/)).toBeNull();
    expect(queryAllByText("0")).toHaveLength(0);
  });

  it("pinta el slot de cabecera arriba de la lista (selector de la sección)", () => {
    const { getByTestId } = renderList({ header: <div data-testid="slot" /> });
    expect(getByTestId("slot")).toBeTruthy();
  });
});
