/**
 * Schemas Zod para validar respuestas de `/api/marketing/*` (boundary HTTP).
 *
 * El shape canónico lo produce `new_campaign()` en el backend (dict
 * JSON-first). Tolerante con `.default()`: un backend viejo que no emita un
 * campo opcional no tumba el dashboard; un shape roto (sin `id`) truena
 * temprano con un error legible. Se activan en `api.ts` con `.parse()`.
 */

import { z } from "zod";

export const backendCampaignMessageSchema = z.object({
  header: z.string().default(""),
  body: z.string().default(""),
  footer: z.string().default(""),
  cta: z.string().default(""),
});

export const backendSkippedRecipientSchema = z.object({
  session_id: z.string(),
  reason: z.string(),
});

/** Resultado del envío que persiste el CampaignSendWorkflow. */
export const backendSendResultSchema = z.object({
  planned: z.number().int().default(0),
  sent: z.number().int().default(0),
  failed: z.array(z.string()).default([]),
  skipped: z.array(backendSkippedRecipientSchema).default([]),
  unit_cost_usd_micros: z.number().int().default(0),
  spent_usd_micros: z.number().int().default(0),
});

export const backendTestSendRecordSchema = z.object({
  phone: z.string(),
  at_ms: z.number().int(),
  wa_message_id: z.string().nullable().default(null),
});

export const backendCampaignSchema = z.object({
  id: z.string(),
  name: z.string().default(""),
  // Narrowing a los enums del dominio en api.ts (tolerante a drift).
  status: z.string().default("draft"),
  goal: z.string().default(""),
  percent: z.number().int().default(0),
  coupon_code: z.string().default(""),
  valid_until: z.string().default(""),
  product_handle: z.string().nullable().default(null),
  segments: z.array(z.string()).default([]),
  // Zod v4: `.default()` NO re-parsea el valor por el schema interno — el
  // default debe ser el output completo.
  message: backendCampaignMessageSchema.default({
    header: "",
    body: "",
    footer: "",
    cta: "",
  }),
  template_name: z.string().default("campaign_promo_marketing_v1"),
  schedule_at_ms: z.number().int().nullable().default(null),
  created_at_ms: z.number().int().default(0),
  updated_at_ms: z.number().int().default(0),
  sent_at_ms: z.number().int().nullable().default(null),
  send_result: backendSendResultSchema.nullable().default(null),
  test_sends: z.array(backendTestSendRecordSchema).default([]),
  // Curaduría manual del operador (PUT los REEMPLAZA completos):
  // quitados a mano del segmento / agregados a mano fuera del segmento.
  excluded_session_ids: z.array(z.string()).default([]),
  extra_session_ids: z.array(z.string()).default([]),
});

export type BackendCampaign = z.infer<typeof backendCampaignSchema>;

export const backendCampaignsResponseSchema = z.object({
  campaigns: z.array(backendCampaignSchema),
});

/** Response de POST /campaigns/{id}/send (envío ya o programado). */
export const backendSendResponseSchema = z.object({
  workflow_id: z.string(),
  run_id: z.string().default(""),
  scheduled: z.boolean(),
});

export type BackendSendResponse = z.infer<typeof backendSendResponseSchema>;

/** Response de POST /campaigns/{id}/test. */
export const backendTestSendResponseSchema = z.object({
  ok: z.boolean(),
  session_id: z.string(),
});

export type BackendTestSendResponse = z.infer<
  typeof backendTestSendResponseSchema
>;

/** Response de GET /campaigns/{id}/stats — send_result (nulls si aún no se
 *  envió) + atribución sobre el vault (`campaign_stats` del dominio). */
export const backendCampaignStatsSchema = z.object({
  campaign_id: z.string(),
  status: z.string().default("draft"),
  planned: z.number().int().nullable().default(null),
  sent: z.number().int().nullable().default(null),
  failed_count: z.number().int().default(0),
  skipped_count: z.number().int().default(0),
  unit_cost_usd_micros: z.number().int().nullable().default(null),
  spent_usd_micros: z.number().int().nullable().default(null),
  replied: z.number().int().default(0),
  attributed_orders: z.number().int().default(0),
  attributed_revenue_cop: z.number().int().default(0),
});

export type BackendCampaignStats = z.infer<typeof backendCampaignStatsSchema>;
