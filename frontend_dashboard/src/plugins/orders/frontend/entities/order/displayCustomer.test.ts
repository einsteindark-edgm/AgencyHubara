/**
 * Orders y el reporte de ventas muestran el nombre de quien compró, no el
 * relleno «Cliente WhatsApp» con el que el bot crea al cliente en la tienda
 * (2026-11-19: 75 de 110 pedidos del reporte salían así). El backend manda
 * ese nombre en `display_customer`; un backend viejo no lo manda.
 */
import { describe, expect, it } from "vitest";
import { toLegacyOrder } from "./api";
import { orderSummarySchema, type OrderSummary } from "./contracts";

const summary: OrderSummary = {
  id: "order_01HX",
  display_id: "#1247",
  customer: "Cliente WhatsApp",
  short: "AP",
  color: "a",
  phone: null,
  city: "Bogotá",
  channel: "WhatsApp",
  status: "preparing",
  pay_status: "pending",
  pay_type: "cod",
  items: 1,
  pieces: 1,
  total_cop: 50000,
  currency_code: "COP",
  is_draft: false,
  due_iso: null,
  due_time: null,
  overdue: false,
  priority: "normal",
  agent: "—",
  created_at_ms: 1779800400000,
  updated_at_ms: 1779800400000,
};

describe("nombre del cliente en Orders", () => {
  it("muestra el nombre que manda el backend en lugar del relleno", () => {
    const parsed = orderSummarySchema.parse({ ...summary, display_customer: "Ana Pérez" });

    expect(toLegacyOrder(parsed).customer).toBe("Ana Pérez");
  });

  it("con un backend que no lo manda, queda el de siempre", () => {
    expect(toLegacyOrder(orderSummarySchema.parse(summary)).customer).toBe("Cliente WhatsApp");
  });
});
