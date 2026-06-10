/**
 * Schemas Zod para validar respuestas del backend en el boundary HTTP.
 * Los activamos en `api.ts` con `.parse(data)` así un cambio de contrato
 * truena temprano y con mensaje claro.
 */

import { z } from "zod";
import { chatMessageSchema } from "../message/contracts";

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
  messages: z.array(chatMessageSchema),
});

export const sessionsListResponseSchema = z.object({
  sessions: z.array(chatSessionSchema),
});
