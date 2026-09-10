/**
 * Schemas Zod para validar respuestas del backend en el boundary HTTP.
 * Los activamos en `api.ts` con `.parse(data)` así un cambio de contrato
 * truena temprano y con mensaje claro.
 */

import { z } from "zod";
import { chatMessageSchema } from "../message/contracts";

/** Origen REAL de la conversación (referral CTWA del ingest). `campaign_name`
 *  / `ad_name` los resuelve el backend vía Graph best-effort: pueden venir
 *  null aunque `source_id` (ad id) exista — el UI degrada al `headline`. */
export const sessionOriginSchema = z.object({
  channel: z.string().nullable(),
  source_id: z.string().nullable(),
  source_type: z.string().nullable(),
  headline: z.string().nullable(),
  /** Epoch ms del primer inbound de la sesión. */
  first_seen_ms: z.number().nullable(),
  campaign_name: z.string().nullable(),
  ad_name: z.string().nullable(),
});

export const chatSessionSchema = z.object({
  session_id: z.string(),
  phone_number: z.string(),
  tag: z.string(),
  motivo: z.string(),
  active_agent_route: z.string(),
  phone_number_id: z.string().nullable(),
  // Pedido esperando que un humano confirme el pago (id backend de Medusa), o
  // null. Lo deriva el backend del metadata del chat (registered_order +
  // escalation_reason). `.default(null)` tolera respuestas viejas sin el campo
  // durante el rollout. Enciende el botón "Confirmar pago" en el chat.
  pending_payment_order_id: z.string().nullable().default(null),
  last_updated_timestamp: z.number(),
  // `.default(null)` tolera snapshots viejos del SSE durante el rollout.
  origin: sessionOriginSchema.nullable().default(null),
});

export const statusHistoryEntrySchema = z.object({
  tag: z.string(),
  motivo: z.string(),
  active_route: z.string(),
  timestamp: z.number(),
});

export const sessionDetailsSchema = z.object({
  session_id: z.string(),
  phone_number: z.string(),
  tag: z.string(),
  motivo: z.string(),
  memory_content: z.string().nullable(),
  active_agent_route: z.string(),
  phone_number_id: z.string().nullable(),
  // Ver `chatSessionSchema.pending_payment_order_id`. El composer del chat lo
  // lee de aquí (vía `useSession`) para mostrar el botón "Confirmar pago".
  pending_payment_order_id: z.string().nullable().default(null),
  status_history: z.array(statusHistoryEntrySchema),
  origin: sessionOriginSchema.nullable().default(null),
  messages: z.array(chatMessageSchema),
});

export const sessionsListResponseSchema = z.object({
  sessions: z.array(chatSessionSchema),
});
