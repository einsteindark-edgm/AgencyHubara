/**
 * Tests del mapper `toLegacyOrder` — convierte `OrderSummary` (shape backend
 * snake_case) → `Order` (shape legacy camelCase que la UI espera).
 *
 * También verifica que el Zod schema (`orderListResponseSchema`) rechaza
 * respuestas inválidas — la primera línea de defensa contra contract drift.
 */

import { describe, expect, it } from "vitest";
import { toLegacyOrder } from "./api";
import {
  orderDetailSchema,
  orderListResponseSchema,
  type OrderSummary,
} from "./contracts";

const sampleSummary: OrderSummary = {
  id: "order_01HX",
  display_id: "#1247",
  customer: "María Camila",
  short: "MC",
  color: "a",
  phone: "3001234567",
  city: "Bogotá",
  channel: "WhatsApp",
  status: "preparing",
  pay_status: "paid",
  pay_type: "confirmed",
  items: 2,
  pieces: 3,
  total_cop: 124500,
  currency_code: "COP",
  is_draft: false,
  due_iso: "2026-05-23",
  due_time: "—",
  overdue: false,
  priority: "normal",
  agent: "—",
  created_at_ms: 1779800400000,
  updated_at_ms: 1779800400000,
};

describe("toLegacyOrder", () => {
  it("maps snake_case → camelCase keeping all visible UI fields", () => {
    const o = toLegacyOrder(sampleSummary);
    expect(o.id).toBe("#1247"); // displayId is what the UI uses as id
    expect(o.customer).toBe("María Camila");
    expect(o.short).toBe("MC");
    expect(o.color).toBe("a");
    expect(o.phone).toBe("3001234567");
    expect(o.city).toBe("Bogotá");
    expect(o.channel).toBe("WhatsApp");
    expect(o.status).toBe("preparing");
    expect(o.payStatus).toBe("paid");
    expect(o.payType).toBe("confirmed");
    expect(o.items).toBe(2);
    expect(o.total).toBe(124500);
    expect(o.pieces).toBe(3);
    expect(o.dueIso).toBe("2026-05-23");
    expect(o.dueTime).toBe("—");
    expect(o.priority).toBe("normal");
    expect(o.isDraft).toBe(false);
    expect(o.isDueEstimated).toBe(true);
  });

  it("renders '—' for null phone and city", () => {
    const o = toLegacyOrder({ ...sampleSummary, phone: null, city: null });
    expect(o.phone).toBe("—");
    expect(o.city).toBe("—");
  });

  it("marks draft orders explicitly", () => {
    const o = toLegacyOrder({ ...sampleSummary, is_draft: true });
    expect(o.isDraft).toBe(true);
  });

  it("computes overdue when due_iso is in the past", () => {
    const yesterday = new Date(Date.now() - 86_400_000)
      .toISOString()
      .slice(0, 10);
    const o = toLegacyOrder({ ...sampleSummary, due_iso: yesterday });
    expect(o.overdue).toBe(true);
  });

  it("does NOT flag overdue when due_iso is today or future", () => {
    const today = new Date().toISOString().slice(0, 10);
    const tomorrow = new Date(Date.now() + 86_400_000)
      .toISOString()
      .slice(0, 10);
    expect(toLegacyOrder({ ...sampleSummary, due_iso: today }).overdue).toBe(false);
    expect(toLegacyOrder({ ...sampleSummary, due_iso: tomorrow }).overdue).toBe(false);
  });

  it("humanizes due to 'hoy' / 'mañana' / 'ayer'", () => {
    const today = new Date().toISOString().slice(0, 10);
    const tomorrow = new Date(Date.now() + 86_400_000)
      .toISOString()
      .slice(0, 10);
    const yesterday = new Date(Date.now() - 86_400_000)
      .toISOString()
      .slice(0, 10);
    expect(toLegacyOrder({ ...sampleSummary, due_iso: today }).due).toBe("hoy");
    expect(toLegacyOrder({ ...sampleSummary, due_iso: tomorrow }).due).toBe("mañana");
    expect(toLegacyOrder({ ...sampleSummary, due_iso: yesterday }).due).toBe("ayer");
  });
});

describe("orderListResponseSchema", () => {
  it("accepts a valid backend response", () => {
    const valid = {
      orders: [sampleSummary],
      count: 1,
      offset: 0,
      limit: 100,
      catalog_available: true,
      error_detail: null,
    };
    expect(() => orderListResponseSchema.parse(valid)).not.toThrow();
  });

  it("accepts catalog_available=false + empty list (Medusa down)", () => {
    const valid = {
      orders: [],
      count: 0,
      offset: 0,
      limit: 100,
      catalog_available: false,
      error_detail: "medusa_api_error: HTTP 503",
    };
    expect(() => orderListResponseSchema.parse(valid)).not.toThrow();
  });

  it("rejects when status enum is unknown", () => {
    expect(() =>
      orderListResponseSchema.parse({
        orders: [{ ...sampleSummary, status: "fancy_new_status" }],
        count: 1,
        offset: 0,
        limit: 100,
        catalog_available: true,
        error_detail: null,
      }),
    ).toThrow();
  });

  it("rejects when total_cop is a string instead of int", () => {
    expect(() =>
      orderListResponseSchema.parse({
        orders: [{ ...sampleSummary, total_cop: "124500" }],
        count: 1,
        offset: 0,
        limit: 100,
        catalog_available: true,
        error_detail: null,
      }),
    ).toThrow();
  });
});

describe("orderDetailSchema", () => {
  it("accepts a full detail response", () => {
    const detail = {
      summary: sampleSummary,
      items_detail: [
        {
          title: "Vela Sagrado",
          sku: "VEL-001",
          quantity: 2,
          unit_price_cop: 17000,
          total_cop: 34000,
          variant_label: null,
          thumbnail: null,
          handle: "vela-sagrado",
        },
      ],
      shipping_address: {
        first_name: "María",
        last_name: "Camila",
        phone: "3001234567",
        address_1: "Calle 100",
        address_2: "Chapinero",
        city: "Bogotá",
        country_code: "co",
      },
      billing_address: null,
      subtotal_cop: 119500,
      shipping_cop: 5000,
      tax_total_cop: 0,
      discount_total_cop: 0,
      timeline: [
        {
          type: "created",
          label: "Pedido creado",
          timestamp_ms: 1779800400000,
          detail: null,
        },
      ],
      payment_method_label: "Transferencia",
      notes: [],
      data_completeness_missing: ["due_date", "agent", "notes"],
    };
    expect(() => orderDetailSchema.parse(detail)).not.toThrow();
  });
});
