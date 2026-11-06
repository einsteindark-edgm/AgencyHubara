/**
 * Reporte de ventas (tarjeta del ciclo #18), revisión: la plata que ya entró
 * pero no se entregó, los reembolsos y el envío estimado contra el real.
 *   - «Cobrado» sigue siendo la cuenta del KPI «Ingresos» (entregado y
 *     pagado). Un prepago que todavía no se entrega se ve aparte, para que
 *     «Por cobrar» no parezca plata que falta cobrar.
 *   - Los reembolsos se ven aparte.
 *   - Envío: el estimado del registro, el real que fijó el operador al
 *     despachar y lo que paga la tienda en los pedidos con envío gratis.
 */
import { describe, expect, it } from "vitest";
import { salesReport, type Order } from "./index";

function order(overrides: Partial<Order> & { id: string }): Order {
  return {
    customer: "Cliente",
    short: "CL",
    color: "a",
    phone: "—",
    city: "Bogotá",
    channel: "WhatsApp",
    status: "preparing",
    payStatus: "pending",
    payType: "cod",
    items: 1,
    total: 10000,
    shipping: 0,
    dueIso: "2026-11-02",
    dueTime: "—",
    pieces: 1,
    agent: "—",
    priority: "normal",
    isDraft: false,
    isDueEstimated: false,
    createdIso: "",
    ...overrides,
  };
}

const RANGE = { from: "2026-11-01", to: "2026-11-05" };

describe("salesReport · plata y envío", () => {
  it("un prepago que todavía no se entrega va en «pagado sin entregar»", () => {
    const r = salesReport(
      [
        order({ id: "#1", total: 40000, status: "shipping", payStatus: "paid", payType: "confirmed" }),
        order({ id: "#2", total: 25000, status: "delivered", payStatus: "paid" }),
        order({ id: "#3", total: 15000 }),
      ],
      RANGE,
    );

    expect(r.collected).toBe(25000);
    expect(r.paidNotDelivered).toBe(40000);
  });

  it("los reembolsos se ven aparte", () => {
    const r = salesReport([order({ id: "#1", total: 30000, payStatus: "refund" }), order({ id: "#2" })], RANGE);

    expect(r.refunded).toEqual({ orders: 1, sales: 30000 });
  });

  it("separa el envío estimado del real y suma lo que paga la tienda con envío gratis", () => {
    const r = salesReport(
      [
        order({ id: "#1", shipping: 7000, shippingConfirmed: false }),
        order({ id: "#2", shipping: 12000, shippingConfirmed: true }),
        order({ id: "#3", shipping: 0, freeShipping: true, storeShippingCost: 9000 }),
        order({ id: "#4", shipping: 0, freeShipping: true, storeShippingCost: null }),
      ],
      RANGE,
    );

    expect(r.shippingEstimated).toBe(7000);
    expect(r.shippingReal).toBe(12000);
    expect(r.storeShippingCost).toBe(9000);
  });
});
