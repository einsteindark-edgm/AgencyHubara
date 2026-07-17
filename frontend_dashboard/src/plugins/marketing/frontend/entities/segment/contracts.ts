/**
 * Schema Zod de GET /api/marketing/segments — los 3 segmentos de audiencia
 * derivados de los tags del clasificador + costo unitario EXPLÍCITO del rate
 * card (USD micros — cost_unit_lesson). Tolerante con `.default()`.
 */

import { z } from "zod";

export const backendSegmentSchema = z.object({
  key: z.string(),
  label: z.string().default(""),
  description: z.string().default(""),
  count: z.number().int().default(0),
});

export type BackendSegment = z.infer<typeof backendSegmentSchema>;

export const backendSegmentsResponseSchema = z.object({
  segments: z.array(backendSegmentSchema).default([]),
  excluded_count: z.number().int().default(0),
  unit_cost_usd_micros: z.number().int().default(0),
  currency: z.string().default("USD"),
});

export type BackendSegmentsResponse = z.infer<
  typeof backendSegmentsResponseSchema
>;
