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

/** Counts por estado conversacional — backend devuelve dict con TODAS las
 * 7 keys de `AdsState`. `null` solo si el bucket está vacío (caso edge).
 * El frontend consume esto para `AdsStateDistribution` y `AdsFunnel`. */
export const backendAdsConversationsCountsSchema = z
  .object({
    no_reply: z.number().int(),
    nuevo: z.number().int(),
    activo: z.number().int(),
    calificado: z.number().int(),
    cotizado: z.number().int(),
    ganado: z.number().int(),
    perdido: z.number().int(),
  })
  .nullable();

export const backendAdsCampaignSchema = z.object({
  // Disponibles hoy
  id: z.string(),
  name: z.string().nullable(),
  source_type: z.string().nullable(), // "ad" | "post" | "web_referral" | "direct"
  started: z.number().int(),
  first_seen_ms: z.number().nullable(),
  last_seen_ms: z.number().nullable(),
  conversations: backendAdsConversationsCountsSchema,

  // Derivados del vault (negocio congelado por episodio). `revenue`/`avg_ticket`
  // ahora se pueblan desde `episode.order_total_cop`; `llm_*` desde
  // `episode.llm_usage`; `avg_episode_duration_ms` de closed−started. Cada uno
  // es `null` solo si el bucket no tuvo data (venta / uso LLM / cierre).
  revenue: z.number().nullable(),
  avg_ticket: z.number().nullable(),
  llm_cost_usd: z.number().nullable(),
  llm_tokens: z.number().int().nullable(),
  avg_episode_duration_ms: z.number().int().nullable(),

  // Faltantes — backend serializa null hasta integrar Meta Ads API / orders
  spend: z.number().nullable(),
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
  id: z.string(), // "wa_<phone>__<episode_id>" o "wa_<phone>" (legacy)
  phone_number: z.string(),
  /** ID del episodio dentro de la sesión. `null` para sesiones legacy
   *  sin `episodes[]`. Cuando está presente, varias filas pueden
   *  compartir el mismo `phone_number` (cliente con múltiples conversaciones
   *  a lo largo del tiempo). */
  episode_id: z.string().nullable(),
  started_at_ms: z.number().int(),
  last_msg_at_ms: z.number().nullable(),
  msgs_count: z.number().int(),
  ad_headline: z.string().nullable(),
  agent: z.string().nullable(),
  state: z.string().nullable(), // AdsState derivado por classifier

  // `value` ahora se puebla desde `episode.order_total_cop` (COP). null si el
  // episodio no cerró venta o el total no es recuperable.
  value: z.number().nullable(),

  // Duración del episodio (ms) = closed_at_ms − started_at_ms. null si sigue
  // activo o no tiene timestamps válidos.
  duration_ms: z.number().int().nullable(),

  // Faltantes — backend devuelve null hasta integrar CRM / orders
  name: z.string().nullable(),
  city: z.string().nullable(),

  // Costo LLM del episodio (USD, congelado a la tarifa del momento) + tokens
  // totales — de `episode.llm_usage` en metadata.json. null si el episodio aún
  // no acumuló uso (sesión legacy / episodio sin turnos LLM).
  llm_cost_usd: z.number().nullable(),
  llm_tokens: z.number().int().nullable(),
});

export type BackendAttributedConversation = z.infer<
  typeof backendAttributedConversationSchema
>;

export const backendAttributedConversationsResponseSchema = z.object({
  campaign_id: z.string(),
  conversations: z.array(backendAttributedConversationSchema),
});

/* ── Serie diaria (response de GET /ads/campaigns/{id}/daily) ────────────── */

/** Un día de la serie: chats iniciados ese día por estado. El shape espeja
 *  exactamente `AdsDailyPoint` del modelo — el backend ya emite las 7 keys de
 *  estado + la etiqueta `d` ("21 may"). */
export const backendAdsDailyPointSchema = z.object({
  d: z.string(),
  ganado: z.number().int(),
  cotizado: z.number().int(),
  calificado: z.number().int(),
  activo: z.number().int(),
  nuevo: z.number().int(),
  no_reply: z.number().int(),
  perdido: z.number().int(),
});

export type BackendAdsDailyPoint = z.infer<typeof backendAdsDailyPointSchema>;

export const backendAdsDailyResponseSchema = z.object({
  campaign_id: z.string(),
  days: z.number().int(),
  series: z.array(backendAdsDailyPointSchema),
});
