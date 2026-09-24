/**
 * Inspector de ventas del cupón: totales (pedidos, descuento en COP,
 * unidades del cupo) y la lista de ventas con el número de pedido como texto
 * (sin enlaces a otro plugin). La query se stubea.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, within } from "@testing-library/react";

import { ApiError } from "@/shared/sdk";

import type { CouponSales } from "@plugins/marketing/frontend/entities/coupon";

const salesMock = {
  data: undefined as CouponSales | undefined,
  isPending: false,
  error: null as Error | null,
};
const eventsMock = vi.fn();

vi.mock("@plugins/marketing/frontend/entities/coupon", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  useCouponSales: () => salesMock,
  useCouponOrdersEvents: (id: string | null) => eventsMock(id),
}));

import { CouponSalesInspector } from "./ui/CouponSalesInspector";

beforeEach(() => {
  salesMock.error = null;
  salesMock.data = {
    orders: 2,
    discountCop: 12_000,
    quotaUnits: 3,
    sales: [
      {
        orderId: "order_01",
        displayId: 31,
        createdAt: "2026-09-23T14:00:00Z",
        isDraft: false,
        quotaUnits: 2,
        discountCop: 8_000,
      },
      {
        orderId: "dorder_02",
        displayId: null,
        createdAt: "2026-09-23T15:00:00Z",
        isDraft: true,
        quotaUnits: 1,
        discountCop: 4_000,
      },
    ],
  };
});

describe("CouponSalesInspector", () => {
  it("muestra los totales: pedidos, descuento total y unidades del cupo", () => {
    const { getByTestId } = render(<CouponSalesInspector couponId="promo_01" />);
    const totals = getByTestId("coupon-sales-totals");
    expect(within(totals).getByText("Pedidos").nextSibling?.textContent).toBe("2");
    expect(within(totals).getByText("Descuento total").nextSibling?.textContent).toBe("$12.000");
    expect(within(totals).getByText("Unidades del cupo").nextSibling?.textContent).toBe("3");
  });

  it("lista cada venta con su número de pedido como texto (sin enlaces)", () => {
    const { getByText, container } = render(<CouponSalesInspector couponId="promo_01" />);
    expect(getByText("#31")).toBeTruthy();
    expect(getByText("Borrador")).toBeTruthy();
    expect(getByText("$8.000")).toBeTruthy();
    expect(container.querySelector("a")).toBeNull();
  });

  it("se suscribe a los eventos de pedidos (las ventas cambian solas)", () => {
    eventsMock.mockClear();
    render(<CouponSalesInspector couponId="promo_01" />);
    // Solo el cupón abierto (D12): nada de invalidar toda la central.
    expect(eventsMock).toHaveBeenCalledWith("promo_01");
  });

  it("sin ventas lo dice", () => {
    salesMock.data = { orders: 0, discountCop: 0, quotaUnits: 0, sales: [] };
    const { getByText } = render(<CouponSalesInspector couponId="promo_01" />);
    expect(getByText("Todavía no hay ventas con este cupón.")).toBeTruthy();
  });

  it("el 503 se muestra con su mensaje", () => {
    salesMock.data = undefined;
    salesMock.error = new ApiError(503, {
      detail: { message: "Medusa no responde ahora mismo; no se hizo ningún cambio. Reintenta en un momento." },
    });
    const { getByText } = render(<CouponSalesInspector couponId="promo_01" />);
    expect(getByText(/Medusa no responde ahora mismo/)).toBeTruthy();
  });
});
