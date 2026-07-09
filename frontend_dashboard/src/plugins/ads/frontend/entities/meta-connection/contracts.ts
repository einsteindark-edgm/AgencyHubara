/**
 * Schemas Zod del backend de Meta (`/api/ads/meta/*`). Se validan en el boundary
 * con `.parse(...)` para que cualquier drift truene temprano y legible.
 */

import { z } from "zod";

/* ── GET /api/ads/meta/status ──────────────────────────────────────── */

export const backendMetaStatusSchema = z.union([
  z.object({ connected: z.literal(false) }),
  z.object({
    connected: z.literal(true),
    account_id: z.string().nullable(),
    account_name: z.string().nullable(),
    scopes: z.array(z.string()),
    expires_at: z.number().nullable(),
    expired: z.boolean().default(false),
    can_manage: z.boolean().default(false),
  }),
]);

/* ── GET /api/ads/meta/insights ────────────────────────────────────── */

export const backendMetaInsightsCampaignSchema = z.object({
  campaign_id: z.string(),
  name: z.string(),
  status: z.string().nullable(),
  objective: z.string().nullable(),
  spend: z.number(),
  impressions: z.number().int(),
  reach: z.number().int(),
  clicks: z.number().int(),
  messaging_conversations_started: z.number().int(),
});

export const backendMetaInsightsSchema = z.object({
  connected: z.boolean(),
  account_id: z.string().nullable().optional(),
  account_name: z.string().nullable().optional(),
  since: z.string().optional(),
  until: z.string().optional(),
  campaigns: z.array(backendMetaInsightsCampaignSchema),
});

/* ── GET /api/ads/meta/analysis-input (seed real para el pod ads-analytics) ── */

// Shape libre que consume el pod GraphAgents; validamos solo las 3 llaves top-level
// (el detalle lo parsea el pod). `unknown` (no `any`) respeta el boundary tipado.
export const backendMetaAnalysisInputSchema = z
  .object({
    meta_insights: z.unknown(),
    manual_sales: z.unknown(),
    entities_payload: z.unknown(),
  })
  .passthrough();

export type BackendMetaStatus = z.infer<typeof backendMetaStatusSchema>;
export type BackendMetaInsights = z.infer<typeof backendMetaInsightsSchema>;
