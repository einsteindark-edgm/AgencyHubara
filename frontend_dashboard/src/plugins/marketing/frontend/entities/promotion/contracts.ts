/**
 * Schema Zod de GET /api/marketing/promotions — cupones vigentes en Medusa
 * (Admin → Promotions), los mismos que el bot valida con `apply_coupon`.
 * Tolerante con `.default()`; `unavailable=true` = Medusa no respondió.
 */

import { z } from "zod";

export const backendPromotionSchema = z.object({
  code: z.string(),
  discount_type: z.string().default("percentage"),
  value: z.number().default(0),
  target_type: z.string().default("items"),
  name: z.string().nullable().default(null),
  ends_at_ms: z.number().int().nullable().default(null),
  min_subtotal_cop: z.number().int().nullable().default(null),
  product_count: z.number().int().default(0),
});

export type BackendPromotion = z.infer<typeof backendPromotionSchema>;

export const backendPromotionsResponseSchema = z.object({
  promotions: z.array(backendPromotionSchema).default([]),
  unavailable: z.boolean().default(false),
});

export type BackendPromotionsResponse = z.infer<
  typeof backendPromotionsResponseSchema
>;
