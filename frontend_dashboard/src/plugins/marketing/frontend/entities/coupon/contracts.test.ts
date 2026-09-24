/**
 * Contrato Zod de la central de cupones (`/api/marketing/coupons*`) —
 * fixtures copiados del JSON REAL de la API (`domain/coupons.py::coupon_json`,
 * `units_json`, `results_json` y `/coupon-products`).
 */
import { describe, expect, it } from "vitest";

import {
  mapBackendCoupon,
  mapBackendCouponDetail,
  mapBackendCouponProducts,
  mapBackendCouponSales,
  mapBackendCouponUnits,
} from "./api";
import {
  backendCouponDetailSchema,
  backendCouponProductsResponseSchema,
  backendCouponSalesSchema,
  backendCouponsResponseSchema,
  backendCouponUnitsSchema,
} from "./contracts";

const COUPON_FIXTURE = {
  promotion_id: "promo_01",
  campaign_id: "procamp_01",
  code: "AMOR27",
  campaign_name: "AMOR Y AMISTAD 2026",
  percentage: 10,
  products: ["prod_cubo"],
  starts_on: "2026-09-22",
  ends_on: "2026-09-27",
  ends_on_label: "27 de septiembre",
  status: "active",
  state: "active",
  manageable: true,
  unmanageable_reason: null,
  accepts_units: true,
  units: { total: 5, left: 3 },
};

const UNMANAGEABLE_FIXTURE = {
  promotion_id: "promo_02",
  campaign_id: null,
  code: "AMOR26",
  campaign_name: null,
  percentage: null,
  products: "all",
  starts_on: null,
  ends_on: null,
  ends_on_label: null,
  status: "inactive",
  state: "paused",
  manageable: false,
  unmanageable_reason:
    "Tiene una condición por etiquetas creada en Medusa; se ve en solo lectura.",
  accepts_units: false,
  units: null,
};

const UNIT_ROW = {
  id: "quota_01",
  product_id: "prod_cubo",
  handle: "cubo-love",
  title: "Cubo Love",
  color: "Rojo",
  aroma: null,
  units: 5,
  sold: 2,
  units_left: 3,
  oversold: false,
  created_by: "operadora",
};

describe("backendCouponsResponseSchema", () => {
  it("parsea la lista real y mapea a camelCase", () => {
    const parsed = backendCouponsResponseSchema.parse({
      coupons: [COUPON_FIXTURE, UNMANAGEABLE_FIXTURE],
      unavailable: false,
    });
    const [a, b] = parsed.coupons.map(mapBackendCoupon);
    expect(a).toEqual({
      promotionId: "promo_01",
      campaignId: "procamp_01",
      code: "AMOR27",
      campaignName: "AMOR Y AMISTAD 2026",
      percentage: 10,
      products: ["prod_cubo"],
      startsOn: "2026-09-22",
      endsOn: "2026-09-27",
      endsOnLabel: "27 de septiembre",
      status: "active",
      state: "active",
      manageable: true,
      unmanageableReason: null,
      acceptsUnits: true,
      units: { total: 5, left: 3 },
    });
    expect(b?.products).toBe("all");
    expect(b?.manageable).toBe(false);
    expect(b?.units).toBeNull();
  });

  it("un estado fuera del vocabulario cae a borrador (nunca rompe la lista)", () => {
    const parsed = backendCouponsResponseSchema.parse({
      coupons: [{ ...COUPON_FIXTURE, state: "raro" }],
    });
    expect(mapBackendCoupon(parsed.coupons[0]!).state).toBe("draft");
  });

  it("left null = no se pudieron leer las ventas", () => {
    const parsed = backendCouponsResponseSchema.parse({
      coupons: [{ ...COUPON_FIXTURE, units: { total: 5, left: null } }],
    });
    expect(mapBackendCoupon(parsed.coupons[0]!).units).toEqual({ total: 5, left: null });
  });
});

describe("backendCouponDetailSchema", () => {
  it("parsea cupón + filas del cupo + registro de cambios", () => {
    const detail = mapBackendCouponDetail(
      backendCouponDetailSchema.parse({
        coupon: COUPON_FIXTURE,
        units: { rows: [UNIT_ROW], show_units_left: true, unavailable: false },
        changes: [
          {
            ts: "2026-09-23T15:00:00Z",
            actor: "operadora",
            action: "update",
            detail: { percentage: [10, 15] },
          },
        ],
      }),
    );
    expect(detail.coupon.code).toBe("AMOR27");
    expect(detail.units.rows[0]).toEqual({
      id: "quota_01",
      productId: "prod_cubo",
      handle: "cubo-love",
      title: "Cubo Love",
      color: "Rojo",
      aroma: null,
      units: 5,
      sold: 2,
      unitsLeft: 3,
      oversold: false,
      createdBy: "operadora",
    });
    expect(detail.units.showUnitsLeft).toBe(true);
    expect(detail.units.unavailable).toBe(false);
    expect(detail.changes[0]).toEqual({
      ts: "2026-09-23T15:00:00Z",
      actor: "operadora",
      action: "update",
      detail: { percentage: [10, 15] },
    });
  });
});

describe("backendCouponUnitsSchema (GET/PUT /units)", () => {
  it("parsea las filas sin el flag unavailable", () => {
    const units = mapBackendCouponUnits(
      backendCouponUnitsSchema.parse({ rows: [UNIT_ROW], show_units_left: false }),
    );
    expect(units.rows).toHaveLength(1);
    expect(units.showUnitsLeft).toBe(false);
    expect(units.unavailable).toBe(false);
  });
});

describe("backendCouponSalesSchema", () => {
  it("parsea totales y ventas (display_id puede ser null)", () => {
    const sales = mapBackendCouponSales(
      backendCouponSalesSchema.parse({
        orders: 2,
        discount_cop: 12_000,
        quota_units: 3,
        sales: [
          {
            order_id: "order_01",
            display_id: 31,
            created_at: "2026-09-23T14:00:00Z",
            is_draft: false,
            quota_units: 2,
            discount_cop: 8_000,
          },
          {
            order_id: "dorder_02",
            display_id: null,
            created_at: "2026-09-23T15:00:00Z",
            is_draft: true,
            quota_units: 1,
            discount_cop: 4_000,
          },
        ],
      }),
    );
    expect(sales.orders).toBe(2);
    expect(sales.discountCop).toBe(12_000);
    expect(sales.quotaUnits).toBe(3);
    expect(sales.sales[1]).toEqual({
      orderId: "dorder_02",
      displayId: null,
      createdAt: "2026-09-23T15:00:00Z",
      isDraft: true,
      quotaUnits: 1,
      discountCop: 4_000,
    });
  });
});

describe("backendCouponProductsResponseSchema", () => {
  it("parsea productos con sus listas cerradas de color y aroma", () => {
    const products = mapBackendCouponProducts(
      backendCouponProductsResponseSchema.parse({
        products: [
          { id: "prod_cubo", handle: "cubo-love", title: "Cubo Love", colors: ["Rojo", "Blanco"], aromas: [] },
        ],
      }),
    );
    expect(products).toEqual([
      { id: "prod_cubo", handle: "cubo-love", title: "Cubo Love", colors: ["Rojo", "Blanco"], aromas: [] },
    ]);
  });
});
