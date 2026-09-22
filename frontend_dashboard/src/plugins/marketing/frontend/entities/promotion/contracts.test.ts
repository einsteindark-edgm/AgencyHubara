/**
 * Contrato Zod de GET /api/marketing/promotions + helpers puros del cupón.
 */
import { describe, expect, it } from "vitest";

import { mapBackendPromotions } from "./api";
import { backendPromotionsResponseSchema } from "./contracts";
import { promotionLabel, sanitizeCouponCode } from "./model";

describe("backendPromotionsResponseSchema", () => {
  it("parsea el shape real del endpoint y mapea a camelCase", () => {
    const parsed = backendPromotionsResponseSchema.parse({
      promotions: [
        {
          code: "MAMA15",
          discount_type: "percentage",
          value: 15,
          target_type: "items",
          name: "Madres",
          ends_at_ms: 1_800_000_000_000,
          min_subtotal_cop: null,
          product_count: 1,
        },
      ],
      unavailable: false,
    });
    const info = mapBackendPromotions(parsed);
    expect(info.promotions[0]).toEqual({
      code: "MAMA15",
      discountType: "percentage",
      value: 15,
      targetType: "items",
      name: "Madres",
      endsAtMs: 1_800_000_000_000,
      minSubtotalCop: null,
      productCount: 1,
    });
    expect(promotionLabel(info.promotions[0]!)).toBe("15% · productos seleccionados");
  });

  it("tolera un backend viejo sin el endpoint poblado", () => {
    expect(backendPromotionsResponseSchema.parse({})).toEqual({
      promotions: [],
      unavailable: false,
    });
  });
});

describe("sanitizeCouponCode — espejo de COUPON_CODE_RE", () => {
  it("deja solo letras y números en mayúscula (VELAS_10 → VELAS10)", () => {
    expect(sanitizeCouponCode("velas_10")).toBe("VELAS10");
    expect(sanitizeCouponCode("papa-20 ")).toBe("PAPA20");
    expect(sanitizeCouponCode("a".repeat(30))).toHaveLength(14);
  });
});
