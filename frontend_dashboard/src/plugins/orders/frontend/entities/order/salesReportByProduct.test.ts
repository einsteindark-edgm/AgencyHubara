/**
 * Reporte de ventas por producto (tarjeta del ciclo #49): una fila por producto
 * vendido, con las mismas reglas que el reporte por pedido (#18), así que las
 * sumas cuadran con él:
 *   - mismo día por pedido, sin pedidos de prueba, cancelados fuera;
 *   - «Venta total sin envío» suma lo mismo que las ventas del reporte por
 *     pedido menos el envío incluido;
 *   - envío gratis marcado, con su costo una sola vez por pedido;
 *   - descuento marcado con el precio de lista y el motivo;
 *   - costo, ganancia, margen y línea solo de una fuente: sin ella, vacíos y
 *     avisados, nunca un cero.
 * Pedidos y productos inventados.
 */
import { describe, expect, it } from "vitest";
import { NO_DATE_RANGE } from "@/shared/lib";
import {
  NO_COST_SOURCE_NOTE,
  salesReport,
  salesReportByProduct,
  type Order,
  type OrderItemDetail,
  type OrderLines,
} from "./index";

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

function item(overrides: Partial<OrderItemDetail> & { title: string }): OrderItemDetail {
  return {
    sku: null,
    quantity: 1,
    unit_price_cop: 10000,
    total_cop: 10000,
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
    ...overrides,
  };
}

function lines(displayId: string, items: OrderItemDetail[], payment: string | null = "Link de pago"): OrderLines {
  return { order_id: `order_${displayId}`, display_id: displayId, payment_method_label: payment, items };
}

const ORDERS: Order[] = [
  // Dos productos, uno con cupón; envío cobrado.
  order({ id: "#1", customer: "Ana", total: 49000, shipping: 5000, payType: "confirmed" }),
  // Envío gratis: la tienda pagó 8.000.
  order({ id: "#2", customer: "Beto", total: 30000, shipping: 0, freeShipping: true, storeShippingCost: 8000 }),
  // Prueba y cancelado: fuera.
  order({ id: "#3", total: 15000, isTest: true }),
  order({ id: "#4", total: 12000, status: "cancelled" }),
  // Fuera del rango.
  order({ id: "#5", total: 99000, dueIso: "2026-10-01" }),
];

const LINES: OrderLines[] = [
  lines("#1", [
    item({
      title: "Vela Luna",
      variant_label: "Lavanda",
      quantity: 2,
      unit_price_cop: 13500,
      total_cop: 27000,
      coupon_code: "LUNA10",
      list_unit_price_cop: 15000,
      discount_unit_cop: 1500,
    }),
    item({ title: "Portavelas Sol", quantity: 1, unit_price_cop: 17000, total_cop: 17000, color: "Dorado" }),
  ]),
  lines("#2", [
    item({ title: "Caja Deseos", quantity: 2, unit_price_cop: 10000, total_cop: 20000 }),
    item({ title: "Vela Luna", quantity: 1, unit_price_cop: 10000, total_cop: 10000 }),
  ], null),
  lines("#3", [item({ title: "Prueba", total_cop: 15000 })]),
  lines("#4", [item({ title: "Cancelado", total_cop: 12000 })]),
  lines("#5", [item({ title: "Viejo", total_cop: 99000 })]),
];

const RANGE = { from: "2026-11-01", to: "2026-11-05" };

describe("salesReportByProduct", () => {
  it("una fila por producto vendido del rango, sin pruebas ni cancelados", () => {
    const r = salesReportByProduct(ORDERS, LINES, RANGE);

    expect(r.rows.map((row) => [row.orderId, row.product])).toEqual([
      ["#1", "Vela Luna"],
      ["#1", "Portavelas Sol"],
      ["#2", "Caja Deseos"],
      ["#2", "Vela Luna"],
    ]);
  });

  it("la venta sin envío suma lo mismo que el reporte por pedido menos el envío incluido", () => {
    const byOrder = salesReport(ORDERS, RANGE);
    const r = salesReportByProduct(ORDERS, LINES, RANGE);

    expect(r.sales).toBe(byOrder.sales - byOrder.shipping);
    expect(r.rows.reduce((a, row) => a + row.saleWithoutShipping, 0)).toBe(r.sales);
    expect(r.mismatches).toEqual([]);
  });

  it("cada fila trae variante, cantidad, precio, cliente, pedido y canal de pago", () => {
    const [luna, sol, caja] = salesReportByProduct(ORDERS, LINES, RANGE).rows;

    expect(luna).toMatchObject({
      variant: "Lavanda",
      quantity: 2,
      unitPrice: 13500,
      saleWithoutShipping: 27000,
      customer: "Ana",
      orderId: "#1",
      paymentChannel: "Link de pago",
    });
    // Sin variante del catálogo: el color de la línea.
    expect(sol.variant).toBe("Dorado");
    // Sin medio de pago guardado: la modalidad del pedido.
    expect(caja.paymentChannel).toBe("Contra entrega");
  });

  it("el descuento sale con el precio de lista y el motivo", () => {
    const [luna, sol] = salesReportByProduct(ORDERS, LINES, RANGE).rows;

    expect(luna.listPrice).toBe(15000);
    expect(luna.discount).toBe(3000);
    expect(luna.discountReason).toBe("Cupón LUNA10");
    expect(sol.listPrice).toBeNull();
    expect(sol.discount).toBe(0);
    expect(sol.discountReason).toBe("");
  });

  it("un precio ajustado sale con su motivo", () => {
    const adjusted = [
      lines("#1", [
        item({
          title: "Vela Luna",
          quantity: 2,
          unit_price_cop: 13500,
          total_cop: 27000,
          list_unit_price_cop: 15000,
          price_adjusted_reason: "precio al por mayor",
        }),
        item({ title: "Portavelas Sol", unit_price_cop: 17000, total_cop: 17000 }),
      ]),
    ];

    const [luna] = salesReportByProduct([ORDERS[0]], adjusted, RANGE).rows;

    expect(luna.discount).toBe(3000);
    expect(luna.discountReason).toBe("Precio ajustado: precio al por mayor");
  });

  it("el envío gratis sale marcado y su costo una sola vez por pedido", () => {
    const rows = salesReportByProduct(ORDERS, LINES, RANGE).rows;
    const two = rows.filter((row) => row.orderId === "#2");

    expect(two.map((row) => row.storePaysShipping)).toEqual([true, true]);
    expect(two.map((row) => row.storeShippingCost)).toEqual([8000, null]);
    expect(rows.find((row) => row.orderId === "#1")?.storePaysShipping).toBe(false);
    expect(rows.reduce((a, row) => a + (row.storeShippingCost ?? 0), 0)).toBe(
      salesReport(ORDERS, RANGE).storeShippingCost,
    );
  });

  it("sin fuente de costo ni de línea, esas columnas quedan vacías y avisadas", () => {
    const r = salesReportByProduct(ORDERS, LINES, RANGE);

    expect(r.costNote).toBe(NO_COST_SOURCE_NOTE);
    for (const row of r.rows) {
      expect(row.unitCost).toBeNull();
      expect(row.totalCost).toBeNull();
      expect(row.profit).toBeNull();
      expect(row.margin).toBeNull();
      expect(row.line).toBeNull();
      expect(row.fullUnitCost).toBeNull();
    }
    expect(r.byLine).toEqual([]);
  });

  it("con una fuente, calcula costo, ganancia, margen y totales por línea; sin dato, vacío", () => {
    const costs = (it: OrderItemDetail) =>
      it.title === "Vela Luna" ? { unitCost: 6000, line: "Velas", fullUnitCost: null } : null;

    const r = salesReportByProduct(ORDERS, LINES, RANGE, costs);
    const [luna, sol] = r.rows;

    expect(luna).toMatchObject({ unitCost: 6000, totalCost: 12000, profit: 15000, margin: 55.6, line: "Velas" });
    expect(sol).toMatchObject({ unitCost: null, totalCost: null, profit: null, margin: null, line: null });
    expect(r.costNote).toBe("2 de 4 filas sin costo o sin línea: esas celdas quedan vacías");
    expect(r.byLine).toEqual([{ line: "Velas", quantity: 3, sales: 37000, profit: 19000, margin: 51.4 }]);
  });

  it("un pedido cuyas líneas no suman su valor sin envío sale avisado con la diferencia", () => {
    const off = [lines("#1", [item({ title: "Vela Luna", quantity: 2, unit_price_cop: 13500, total_cop: 27000 })])];

    const r = salesReportByProduct([ORDERS[0]], off, RANGE);

    expect(r.mismatches).toEqual([{ orderId: "#1", difference: 44000 - 27000 }]);
  });

  it("un pedido sin sus líneas cargadas sale avisado, no desaparece en silencio", () => {
    const r = salesReportByProduct(ORDERS, LINES.slice(1), RANGE);

    expect(r.ordersWithoutLines).toEqual(["#1"]);
  });

  it("con todas las fechas cuenta todo lo vendido", () => {
    const r = salesReportByProduct(ORDERS, LINES, NO_DATE_RANGE);

    expect(r.rows.map((row) => row.orderId)).toContain("#5");
  });
});
