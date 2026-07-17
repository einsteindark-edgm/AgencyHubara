/**
 * Schemas Zod de la audiencia de campaña (boundary HTTP):
 * `GET /api/marketing/campaigns/{id}/audience` (la audiencia REAL con la
 * lógica del envío — quiet hours NO se evalúa acá, es del disparo) y
 * `GET /api/marketing/audience/{session_id}/conversation` (últimos 60
 * mensajes, read-only).
 *
 * Tolerante con `.default()`: campo opcional ausente no tumba el dashboard;
 * un shape roto (sin `session_id`) truena temprano con error legible.
 */

import { z } from "zod";

export const backendAudienceRecipientSchema = z.object({
  session_id: z.string(),
  phone: z.string().default(""),
  customer_name: z.string().nullable().default(null),
  segment: z.string().default(""),
});

export type BackendAudienceRecipient = z.infer<
  typeof backendAudienceRecipientSchema
>;

export const backendAudienceSkippedSchema = z.object({
  session_id: z.string(),
  phone: z.string().default(""),
  // "excluido" (humano u opt-out) | "campana_reciente" (<48h) — se traduce
  // a label legible en model.ts, tolerante a razones nuevas.
  reason: z.string().default(""),
});

export type BackendAudienceSkipped = z.infer<typeof backendAudienceSkippedSchema>;

export const backendCampaignAudienceSchema = z.object({
  recipients: z.array(backendAudienceRecipientSchema).default([]),
  skipped: z.array(backendAudienceSkippedSchema).default([]),
  total: z.number().int().default(0),
});

export type BackendCampaignAudience = z.infer<
  typeof backendCampaignAudienceSchema
>;

export const backendConversationMessageSchema = z.object({
  // Narrowing al enum del dominio en api.ts (tolerante a drift).
  role: z.string().default("assistant"),
  kind: z.string().default("text"),
  content: z.string().default(""),
  timestamp: z.string().nullable().default(null),
});

export type BackendConversationMessage = z.infer<
  typeof backendConversationMessageSchema
>;

export const backendAudienceConversationSchema = z.object({
  session_id: z.string(),
  messages: z.array(backendConversationMessageSchema).default([]),
});

export type BackendAudienceConversation = z.infer<
  typeof backendAudienceConversationSchema
>;
