/**
 * El CSV muestra qué compró cada pedido sin buscarlo (2026-11-19: el operador
 * abrió el archivo y no encontró los productos, que estaban en una segunda
 * tabla debajo de todos los pedidos). Ahora:
 *  - la tabla por producto va primero, justo debajo del resumen, y cada fila
 *    trae los datos de su pedido;
 *  - la tabla por pedido, debajo, dice en una columna qué compró cada uno.
 * Datos inventados.
 */
import { describe, expect, it } from "vitest";
import {
  salesReport,
  salesReportByProduct,
  type Order,
  type OrderItemDetail,
  type OrderLines,
} from "@plugins/orders/frontend/entities/order";
import { salesReportCsv } from "./salesReportCsv";

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

function item(title: string, quantity: number, unit: number, variant: string | null = null): OrderItemDetail {
  return {
    title,
    sku: null,
    quantity,
    unit_price_cop: unit,
    total_cop: unit * quantity,
    variant_label: variant,
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

const ORDERS = [order({ id: "#1", total: 47000, shipping: 7000 }), order({ id: "#2", total: 25000 })];
const LINES: OrderLines[] = [
  {
    order_id: "o1",
    display_id: "#1",
    payment_method_label: null,
    items: [item("Vela Luna", 2, 15000, "Lavanda"), item("Vela Sol", 1, 10000)],
  },
  { order_id: "o2", display_id: "#2", payment_method_label: null, items: [item("Vela Luna", 1, 25000)] },
];

function csvRows(): string[][] {
  const csv = salesReportCsv(
    salesReport(ORDERS, RANGE),
    ORDERS,
    RANGE,
    salesReportByProduct(ORDERS, LINES, RANGE),
  );
  return csv
    .replace(/^\uFEFF/, "")
    .split("\r\n")
    .map((line) => line.split(";"));
}

describe("salesReportCsv: qué compró cada pedido", () => {
  it("la tabla por producto va antes que la tabla por pedido", () => {
    const rows = csvRows();
    const byProduct = rows.findIndex((r) => r[0] === "Producto");
    const byOrder = rows.findIndex((r) => r[0] === "Pedido");

    expect(byProduct).toBeGreaterThan(-1);
    expect(byOrder).toBeGreaterThan(byProduct);
  });

  it("cada fila de producto trae los datos de su pedido, con envío y total una sola vez", () => {
    const rows = csvRows();
    const header = rows.findIndex((r) => r[0] === "Producto");
    const extra = (r: string[]) => r.slice(19);

    expect(extra(rows[header])).toEqual(["Día", "Ciudad", "Canal", "Etapa", "Pago", "Envío del pedido", "Total del pedido"]);
    expect(extra(rows[header + 1])).toEqual(["2026-11-02", "Bogotá", "WhatsApp", "En preparación", "Pendiente", "7000", "47000"]);
    expect(extra(rows[header + 2])).toEqual(["2026-11-02", "Bogotá", "WhatsApp", "En preparación", "Pendiente", "", ""]);
  });

  it("la tabla por pedido dice qué compró cada uno", () => {
    const rows = csvRows();
    const header = rows.findIndex((r) => r[0] === "Pedido");
    const productsOf = (id: string) => rows.slice(header).find((r) => r[0] === id)?.at(-1);

    expect(rows[header].at(-1)).toBe("Productos");
    expect(productsOf("#1")).toBe("2 × Vela Luna (Lavanda) + 1 × Vela Sol");
    expect(productsOf("#2")).toBe("1 × Vela Luna");
  });
});
