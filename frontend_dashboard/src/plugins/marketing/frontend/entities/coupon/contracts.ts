/**
 * Schemas Zod de la central de cupones (`/api/marketing/coupons*` y
 * `/api/marketing/coupon-products`). Espejo de `domain/coupons.py`
 * (`coupon_json`, `units_json`, `results_json`) — la fuente de verdad del
 * shape. Los campos opcionales toleran ausencia con `.default()`.
 *
 * Errores (no son respuestas 2xx, se leen del `ApiError.body`):
 *  - 422 `{detail: {field, message}}` — dato inválido del formulario
 *  - 422 `{detail: {message, rows: [{row, field, message}]}}` — filas del cupo
 *  - 409/503/404 `{detail: {message}}` · 502 `{detail: {message, step, coupon}}`
 *  - 409 `{detail: {code: "units_changed", message}}` — el cupo cambió desde la
 *    versión que se editó (C-5, `expected_updated_at`)
 */

import { z } from "zod";

export const backendCouponUnitsSummarySchema = z.object({
  total: z.number().int(),
  left: z.number().int().nullable().default(null),
});

export const backendCouponSchema = z.object({
  promotion_id: z.string(),
  campaign_id: z.string().nullable().default(null),
  code: z.string(),
  campaign_name: z.string().nullable().default(null),
  percentage: z.number().int().nullable().default(null),
  /** Lista de ids de producto, o `"all"` = todo el catálogo. */
  products: z.union([z.literal("all"), z.array(z.string())]).default("all"),
  starts_on: z.string().nullable().default(null),
  ends_on: z.string().nullable().default(null),
  ends_on_label: z.string().nullable().default(null),
  status: z.string().default("draft"),
  state: z.string().default("draft"),
  manageable: z.boolean().default(false),
  unmanageable_reason: z.string().nullable().default(null),
  accepts_units: z.boolean().default(false),
  units: backendCouponUnitsSummarySchema.nullable().default(null),
});

export type BackendCoupon = z.infer<typeof backendCouponSchema>;

export const backendCouponsResponseSchema = z.object({
  coupons: z.array(backendCouponSchema).default([]),
  unavailable: z.boolean().default(false),
});

export const backendCouponUnitRowSchema = z.object({
  id: z.string(),
  product_id: z.string(),
  handle: z.string().default(""),
  title: z.string().default(""),
  color: z.string().nullable().default(null),
  aroma: z.string().nullable().default(null),
  units: z.number().int(),
  sold: z.number().int().default(0),
  units_left: z.number().int().default(0),
  oversold: z.boolean().default(false),
  created_by: z.string().default(""),
});

/** GET/PUT `/coupons/{id}/units` (el detalle agrega `unavailable`). */
export const backendCouponUnitsSchema = z.object({
  rows: z.array(backendCouponUnitRowSchema).default([]),
  show_units_left: z.boolean().default(true),
  unavailable: z.boolean().default(false),
  /** Versión de lo guardado (ISO; null = nunca se guardó) — C-5. */
  updated_at: z.string().nullable().default(null),
});

export type BackendCouponUnits = z.infer<typeof backendCouponUnitsSchema>;

export const backendCouponChangeSchema = z.object({
  ts: z.string(),
  actor: z.string().default(""),
  action: z.string(),
  detail: z.record(z.string(), z.unknown()).default({}),
});

export const backendCouponDetailSchema = z.object({
  coupon: backendCouponSchema,
  units: backendCouponUnitsSchema.default({
    rows: [],
    show_units_left: true,
    unavailable: false,
    updated_at: null,
  }),
  changes: z.array(backendCouponChangeSchema).default([]),
});

export type BackendCouponDetail = z.infer<typeof backendCouponDetailSchema>;

export const backendCouponSaleSchema = z.object({
  order_id: z.string(),
  display_id: z.number().int().nullable().default(null),
  created_at: z.string().default(""),
  is_draft: z.boolean().default(false),
  quota_units: z.number().int().default(0),
  discount_cop: z.number().int().default(0),
});

export const backendCouponSalesSchema = z.object({
  orders: z.number().int().default(0),
  discount_cop: z.number().int().default(0),
  quota_units: z.number().int().default(0),
  sales: z.array(backendCouponSaleSchema).default([]),
});

export type BackendCouponSales = z.infer<typeof backendCouponSalesSchema>;

export const backendCouponProductSchema = z.object({
  id: z.string(),
  handle: z.string().default(""),
  title: z.string().default(""),
  colors: z.array(z.string()).default([]),
  aromas: z.array(z.string()).default([]),
});

export const backendCouponProductsResponseSchema = z.object({
  products: z.array(backendCouponProductSchema).default([]),
});

export type BackendCouponProductsResponse = z.infer<
  typeof backendCouponProductsResponseSchema
>;

/* ── Errores de la API (body del ApiError) ─────────────────────────────── */

export const backendFieldErrorSchema = z.object({
  detail: z.object({ field: z.string(), message: z.string() }),
});

/** 409 del PUT del cupo: otra persona lo guardó después de la versión que
 *  se editó (C-5). */
export const backendUnitsConflictSchema = z.object({
  detail: z.object({ code: z.literal("units_changed"), message: z.string() }),
});

export const backendRowsErrorSchema = z.object({
  detail: z.object({
    message: z.string(),
    rows: z.array(
      z.object({ row: z.number().int(), field: z.string(), message: z.string() }),
    ),
  }),
});
