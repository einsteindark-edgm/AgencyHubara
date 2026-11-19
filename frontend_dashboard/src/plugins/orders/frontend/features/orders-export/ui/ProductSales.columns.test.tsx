/**
 * La pantalla esconde solo las columnas sin dato en el rango (2026-11-19):
 * cuando haya fuente de costo, costo y ganancia vuelven a verse; un descuento
 * en una sola fila muestra su columna. Datos inventados.
 */
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import type { OrderLinesResponse, ProductSaleRow, ProductSalesReport } from "@plugins/orders/frontend/entities/order";
import { ProductSales } from "./ProductSales";

const row: ProductSaleRow = {
  product: "Vela Luna",
  variant: "",
  quantity: 1,
  unitCost: null,
  unitPrice: 20000,
  totalCost: null,
  saleWithoutShipping: 20000,
  profit: null,
  margin: null,
  customer: "Ana",
  orderId: "#1",
  line: null,
  fullUnitCost: null,
  paymentChannel: "Contra entrega",
  listPrice: null,
  discount: 0,
  discountReason: "",
  storePaysShipping: false,
  storeShippingCost: null,
  day: "2026-11-02",
  city: "Bogotá",
  channel: "WhatsApp",
  status: "preparing",
  payStatus: "pending",
  orderShipping: 0,
  orderTotal: 20000,
};

const LINES: OrderLinesResponse = {
  orders: [],
  count: 0,
  offset: 0,
  limit: 100,
  catalog_available: true,
  error_detail: null,
};

function headers(rows: ProductSaleRow[]): string[] {
  const report: ProductSalesReport = {
    rows,
    sales: 0,
    costNote: null,
    byLine: [],
    mismatches: [],
    ordersWithoutLines: [],
  };
  render(<ProductSales lines={{ data: LINES, isLoading: false }} report={report} />);
  return screen.getAllByRole("columnheader").map((h) => h.textContent ?? "");
}

describe("ProductSales: columnas visibles", () => {
  it("con costo en alguna fila, muestra costo, ganancia y margen", () => {
    const withCost = { ...row, unitCost: 12000, totalCost: 12000, profit: 8000, margin: 40 };

    expect(headers([row, withCost])).toEqual(expect.arrayContaining(["Costo unitario", "Ganancia", "Margen"]));
  });

  it("un descuento en una sola fila muestra sus columnas", () => {
    const discounted = { ...row, listPrice: 25000, discount: 5000, discountReason: "Cupón LUNA" };

    expect(headers([row, discounted])).toEqual(
      expect.arrayContaining(["Precio de lista", "Descuento", "Motivo del descuento"]),
    );
  });

  it("una variante vacía en todas las filas no ocupa columna", () => {
    expect(headers([row])).not.toContain("Producto en el catálogo (variante)");
  });
});
