/**
 * Cada fila del reporte por producto trae los datos de su pedido (día,
 * ciudad, canal, etapa, pago), para leer «qué compró cada pedido» en una sola
 * tabla (2026-11-19: el operador no encontraba los productos de cada pedido).
 * El envío y el total del pedido van solo en su primera fila, para que al
 * sumar la columna no se cuenten dos veces. Datos inventados.
 */
import { describe, expect, it } from "vitest";
import { salesReportByProduct, type Order, type OrderItemDetail, type OrderLines } from "./index";

const RANGE = { from: "2026-11-01", to: "2026-11-05" };

function order(overrides: Partial<Order> & { id: string }): Order {
  return {
    customer: "Ana",
    short: "AN",
    color: "a",
    phone: "—",
    city: "Bogotá",
    channel: "WhatsApp",
    status: "preparing",
    payStatus: "pending",
    payType: "cod",
    items: 1,
    total: 0,
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

function item(title: string, quantity: number, unit: number): OrderItemDetail {
  return {
    title,
    sku: null,
    quantity,
    unit_price_cop: unit,
    total_cop: unit * quantity,
    variant_label: null,
    variant_label_mismatch: false,
    thumbnail: null,
    handle: null,
    variant_match_kind: null,
    selected_variant_title: null,
    variant_unresolved_tokens: [],
    variant_unresolved_tag_kinds: [],
    coupon_code: null,
    list_unit_price_cop: null,
    discount_unit_cop: 0,
    color: null,
    aroma: null,
    item_id: null,
    price_adjusted_reason: null,
  };
}

const ORDERS = [
  order({ id: "#1", city: "Medellín", status: "delivered", payStatus: "paid", total: 47000, shipping: 7000 }),
  order({ id: "#2", dueIso: "2026-11-03", total: 25000, shipping: 0 }),
];
const LINES: OrderLines[] = [
  { order_id: "o1", display_id: "#1", payment_method_label: null, items: [item("Vela Luna", 2, 15000), item("Vela Sol", 1, 10000)] },
  { order_id: "o2", display_id: "#2", payment_method_label: null, items: [item("Vela Luna", 1, 25000)] },
];

describe("reporte por producto: datos del pedido en cada fila", () => {
  it("cada fila trae el día, la ciudad, el canal, la etapa y el pago de su pedido", () => {
    const [first, second, third] = salesReportByProduct(ORDERS, LINES, RANGE).rows;

    expect(first).toMatchObject({ orderId: "#1", day: "2026-11-02", city: "Medellín", channel: "WhatsApp" });
    expect(first).toMatchObject({ status: "delivered", payStatus: "paid" });
    expect(second).toMatchObject({ orderId: "#1", day: "2026-11-02", city: "Medellín" });
    expect(third).toMatchObject({ orderId: "#2", day: "2026-11-03", status: "preparing", payStatus: "pending" });
  });

  it("el envío y el total del pedido van solo en su primera fila", () => {
    const rows = salesReportByProduct(ORDERS, LINES, RANGE).rows;

    expect(rows.map((r) => [r.orderId, r.orderShipping, r.orderTotal])).toEqual([
      ["#1", 7000, 47000],
      ["#1", null, null],
      ["#2", 0, 25000],
    ]);
  });
});
