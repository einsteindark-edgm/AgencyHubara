/**
 * Schemas Zod para validar respuestas del backend de `ads`.
 *
 * El endpoint vive en el plugin chats (`/api/chats/ads/*`) porque las
 * campañas se derivan del estado clasificado por el ingest WhatsApp
 * (`origin` + `last_touch` en metadata.json). Los campos `nullable()` son
 * los que el backend aún no puede derivar — el frontend muestra "—" +
 * icono dataPending en esos slots para que sea visible qué falta.
 *
 * Se activan en `api.ts` con `.parse(data)` para que cualquier drift de
 * contrato truene temprano con un error legible.
 */

import { z } from "zod";

/* ── Campaña (response de GET /api/chats/ads/campaigns) ──────────────────── */

export const backendAdsCampaignSchema = z.object({
  // Disponibles hoy
  id: z.string(),
  name: z.string().nullable(),
  source_type: z.string().nullable(), // "ad" | "post" | "web_referral"
  started: z.number().int(),
  first_seen_ms: z.number().nullable(),
  last_seen_ms: z.number().nullable(),

  // Faltantes — backend serializa null hasta integrar Meta Ads API / orders
  spend: z.number().nullable(),
  revenue: z.number().nullable(),
  impressions: z.number().nullable(),
  reach: z.number().nullable(),
  clicks: z.number().nullable(),
  status: z.string().nullable(), // "active" | "paused"
  objective: z.string().nullable(),
  placement: z.string().nullable(),
  audience: z.string().nullable(),
  ad_set: z.string().nullable(),
  creative_title: z.string().nullable(),
  template: z.string().nullable(),
  meta_campaign_id: z.string().nullable(),
  avg_ticket: z.number().nullable(),
  first_resp: z.string().nullable(),
  tendency: z.string().nullable(),
  days_run: z.number().nullable(),
});

export type BackendAdsCampaign = z.infer<typeof backendAdsCampaignSchema>;

export const backendAdsCampaignsResponseSchema = z.object({
  campaigns: z.array(backendAdsCampaignSchema),
});

/* ── Conversación atribuida (response del endpoint /conversations) ───────── */

export const backendAttributedConversationSchema = z.object({
  // Disponibles hoy
  id: z.string(),
  phone_number: z.string(),
  started_at_ms: z.number().int(),
  last_msg_at_ms: z.number().nullable(),
  msgs_count: z.number().int(),
  ad_headline: z.string().nullable(),
  agent: z.string().nullable(),

  // Faltantes — backend devuelve null hasta integrar CRM / clasificador / orders
  name: z.string().nullable(),
  city: z.string().nullable(),
  state: z.string().nullable(), // AdsState — requiere clasificador conversacional
  value: z.number().nullable(),
});

export type BackendAttributedConversation = z.infer<
  typeof backendAttributedConversationSchema
>;

export const backendAttributedConversationsResponseSchema = z.object({
  campaign_id: z.string(),
  conversations: z.array(backendAttributedConversationSchema),
});
