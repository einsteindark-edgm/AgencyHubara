/**
 * Lo que el operador VIO del cupón en "Crear pedido" (D1/D4): el descuento
 * contra el que se registra (`expected_discount_cop`) y, cuando el backend lo
 * calculó, el total. Sale de la sugerencia y después de las respuestas del
 * backend (cálculo sin registrar o `quota_changed` con montos).
 */
import { describe, expect, it } from "vitest";

import {
  createOrderResultSchema,
  orderSuggestionSchema,
} from "@plugins/chats/frontend/entities/order-intake";

import { couponReasonLabel, quoteFromResult, quoteFromSuggestion } from "./orderQuote";

const SUGGESTION = orderSuggestionSchema.parse({
  session_key: "wa_golden_intake",
  phone_number: "570000000000",
  shipping: {},
  subtotal_cop: 42000,
  shipping_cop: 7900,
  discount_cop: 0,
  total_cop: 49900,
  coupon_code: "AMOR26",
  coupon_reason: "quota_exhausted",
});

describe("quoteFromSuggestion", () => {
  it("toma el cupón de la sugerencia aunque dé $0, con su motivo y sin total", () => {
    expect(quoteFromSuggestion(SUGGESTION)).toEqual({
      code: "AMOR26",
      discountCop: 0,
      reason: "quota_exhausted",
      source: "suggestion",
      totals: null,
    });
  });

  it("sin cupón no hay nada que cotizar", () => {
    expect(quoteFromSuggestion({ ...SUGGESTION, coupon_code: null })).toBeNull();
  });
});

describe("quoteFromResult", () => {
  const prev = quoteFromSuggestion(SUGGESTION)!;

  it("adopta los montos que calculó el backend", () => {
    const result = createOrderResultSchema.parse({
      registered: false,
      error_detail: "quota_changed",
      subtotal_cop: 42000,
      shipping_cop: 7900,
      discount_cop: 4200,
      total_cop: 45700,
    });

    expect(quoteFromResult(prev, result)).toEqual({
      code: "AMOR26",
      discountCop: 4200,
      reason: null,
      source: "backend",
      totals: { subtotalCop: 42000, shippingCop: 7900, totalCop: 45700 },
    });
  });

  it("el cálculo trae el código del cupón que aplicó", () => {
    const result = createOrderResultSchema.parse({
      registered: false,
      dry_run: true,
      subtotal_cop: 21000,
      shipping_cop: 7900,
      discount_cop: 2100,
      total_cop: 26800,
      coupon_code: "OTRO10",
    });

    expect(quoteFromResult(prev, result)?.code).toBe("OTRO10");
  });

  it("sin montos no hay cotización nueva (hay que recalcular)", () => {
    const result = createOrderResultSchema.parse({ registered: false, error_detail: "quota_changed" });

    expect(quoteFromResult(prev, result)).toBeNull();
  });
});

describe("couponReasonLabel", () => {
  it("dice en español por qué el cupón no descuenta (o no todo)", () => {
    expect(couponReasonLabel("missing_attributes")).toBe("falta elegir color y aroma");
    expect(couponReasonLabel("quota_exhausted")).toBe("se agotaron las unidades con descuento");
    expect(couponReasonLabel("quota_unavailable")).toBe(
      "no se pudieron leer las unidades (intenta en un minuto)",
    );
  });

  it("sin motivo, o uno desconocido, no inventa nada", () => {
    expect(couponReasonLabel(null)).toBeNull();
    expect(couponReasonLabel("algo_nuevo")).toBeNull();
  });
});
