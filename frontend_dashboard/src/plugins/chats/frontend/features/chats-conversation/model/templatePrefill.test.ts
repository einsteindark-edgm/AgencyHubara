/**
 * «Reactivar conversación» se llena solo con el pedido del cliente: el número,
 * el monto y el estado salen del pedido, no los escribe el operador.
 */
import { describe, expect, it } from "vitest";
import type { CustomerOrder } from "@plugins/chats/frontend/entities/order-ref";
import { orderTemplateDefaults, pickTemplateOrder } from "./templatePrefill";

function order(over: Partial<CustomerOrder>): CustomerOrder {
  return { id: "order_x", display_id: "#1", status: "new", ...over } as CustomerOrder;
}

describe("pickTemplateOrder", () => {
  it("prefers the most recent ready order", () => {
    const orders = [
      order({ id: "a", status: "ready" }),
      order({ id: "b", status: "preparing" }),
      order({ id: "c", status: "ready" }),
      order({ id: "d", status: "delivered" }),
    ];
    expect(pickTemplateOrder(orders)?.id).toBe("c");
  });

  it("without a ready order takes the most recent one still in progress", () => {
    const orders = [
      order({ id: "a", status: "preparing" }),
      order({ id: "b", status: "shipping" }),
      order({ id: "c", status: "cancelled" }),
    ];
    expect(pickTemplateOrder(orders)?.id).toBe("b");
  });

  it("falls back to the last delivered order and never to a cancelled one", () => {
    expect(pickTemplateOrder([order({ id: "a", status: "delivered" })])?.id).toBe("a");
    expect(pickTemplateOrder([order({ id: "a", status: "cancelled" })])).toBeNull();
    expect(pickTemplateOrder([])).toBeNull();
    expect(pickTemplateOrder(undefined)).toBeNull();
  });
});

describe("orderTemplateDefaults", () => {
  it("fills the order number, the amount and the status in the customer's words", () => {
    const defaults = orderTemplateDefaults(
      order({ display_id: "#169", status: "ready", total_cop: 120000, currency_code: "cop" }),
    );
    expect(defaults).toEqual({
      order_reference: "#169",
      amount_currency: "$120.000 COP",
      status_label: "listo",
    });
  });

  it("adds the # when the number comes bare and skips what the order lacks", () => {
    expect(orderTemplateDefaults(order({ display_id: "31", status: "shipping" }))).toEqual({
      order_reference: "#31",
      status_label: "en camino",
    });
  });

  it("returns nothing without an order", () => {
    expect(orderTemplateDefaults(null)).toEqual({});
  });
});
