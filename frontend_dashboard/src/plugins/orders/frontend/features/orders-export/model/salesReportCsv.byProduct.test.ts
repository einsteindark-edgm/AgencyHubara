/**
 * El CSV del reporte por producto (tarjeta del ciclo #49): debajo de lo que ya
 * traía (#18), una tabla con las columnas en el orden pedido, el aviso de las
 * columnas sin fuente y las mismas cifras que la pantalla. Datos inventados.
 */
import { describe, expect, it } from "vitest";
import {
  NO_COST_SOURCE_NOTE,
  salesReport,
  salesReportByProduct,
  type Order,
  type OrderItemDetail,
  type OrderLines,
} from "@plugins/orders/frontend/entities/order";
import { salesReportCsv } from "./salesReportCsv";

const RANGE = { from: "2026-11-01", to: "2026-11-05" };

const ORDERS: Order[] = [
  {
    id: "#1",
    customer: "Ana",
    short: "AN",
    color: "a",
    phone: "—",
    city: "Bogotá",
    channel: "WhatsApp",
    status: "preparing",
    payStatus: "pending",
    payType: "confirmed",
    items: 1,
    total: 32000,
    shipping: 0,
    freeShipping: true,
    storeShippingCost: 8000,
    dueIso: "2026-11-02",
    dueTime: "—",
    pieces: 2,
    agent: "—",
    priority: "normal",
    isDraft: false,
    isDueEstimated: false,
    createdIso: "",
  },
];

const ITEM: OrderItemDetail = {
  title: "Vela Luna",
  sku: null,
  quantity: 2,
  unit_price_cop: 16000,
  total_cop: 32000,
  variant_label: "Lavanda",
  variant_label_mismatch: false,
  thumbnail: null,
  handle: null,
  variant_match_kind: null,
  selected_variant_title: null,
  variant_unresolved_tokens: [],
  variant_unresolved_tag_kinds: [],
  coupon_code: "LUNA10",
  list_unit_price_cop: 18000,
  discount_unit_cop: 2000,
  color: null,
  aroma: null,
  item_id: null,
  price_adjusted_reason: null,
};

const LINES: OrderLines[] = [
  { order_id: "order_1", display_id: "#1", payment_method_label: "Link de pago", items: [ITEM] },
];

function csvRows(): string[][] {
  const report = salesReport(ORDERS, RANGE);
  const byProduct = salesReportByProduct(ORDERS, LINES, RANGE);
  return salesReportCsv(report, ORDERS, RANGE, byProduct)
    .replace(/^﻿/, "")
    .split("\r\n")
    .map((line) => line.split(";"));
}

const COLUMNS = [
  "Producto",
  "Producto en el catálogo (variante)",
  "Cantidad",
  "Costo unitario",
  "Precio unitario",
  "Costo total",
  "Venta total sin envío",
  "Ganancia",
  "Margen",
  "Cliente",
  "Número de pedido",
  "Línea de producto",
  "Costo completo unitario",
  "Canal de pago",
  "Precio de lista",
  "Descuento",
  "Motivo del descuento",
  "Envío asumido por la tienda",
  "Costo del envío asumido",
];

describe("salesReportCsv por producto", () => {
  it("trae la tabla por producto con las columnas en el orden pedido", () => {
    const rows = csvRows();
    const header = rows.findIndex((r) => r[0] === "Producto");

    expect(header).toBeGreaterThan(-1);
    expect(rows[header]).toEqual(COLUMNS);
  });

  it("la fila trae las mismas cifras que la pantalla y deja vacías las columnas sin fuente", () => {
    const rows = csvRows();
    const row = rows[rows.findIndex((r) => r[0] === "Producto") + 1];

    expect(row).toEqual([
      "Vela Luna",
      "Lavanda",
      "2",
      "",
      "16000",
      "",
      "32000",
      "",
      "",
      "Ana",
      "#1",
      "",
      "",
      "Link de pago",
      "18000",
      "4000",
      "Cupón LUNA10",
      "Sí",
      "8000",
    ]);
  });

  it("avisa encima de la tabla que esas columnas quedan vacías", () => {
    const rows = csvRows();
    const header = rows.findIndex((r) => r[0] === "Producto");

    expect(rows.slice(0, header).some((r) => r[0] === NO_COST_SOURCE_NOTE)).toBe(true);
  });

  it("sin el reporte por producto, el CSV queda como antes", () => {
    const report = salesReport(ORDERS, RANGE);

    expect(salesReportCsv(report, ORDERS, RANGE)).not.toContain("Venta total sin envío");
  });
});
