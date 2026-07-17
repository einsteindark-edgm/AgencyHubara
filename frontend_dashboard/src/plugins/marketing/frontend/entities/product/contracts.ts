/**
 * Schema Zod de GET /api/marketing/products — snapshot local del catálogo
 * para el picker del builder. Todo campo derivado de variante/precio puede
 * ser null; `price_amount` tolera string o número (drift del catalog client).
 */

import { z } from "zod";

export const backendProductSchema = z.object({
  handle: z.string(),
  title: z.string().default(""),
  sku: z.string().nullable().default(null),
  category: z.string().nullable().default(null),
  price_amount: z.union([z.string(), z.number()]).nullable().default(null),
  currency: z.string().nullable().default(null),
  thumbnail: z.string().nullable().default(null),
});

export type BackendProduct = z.infer<typeof backendProductSchema>;

export const backendProductsResponseSchema = z.object({
  products: z.array(backendProductSchema).default([]),
});

export type BackendProductsResponse = z.infer<
  typeof backendProductsResponseSchema
>;
