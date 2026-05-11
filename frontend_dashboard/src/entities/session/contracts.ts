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
  status_history: z.array(statusHistoryEntrySchema),
  messages: z.array(chatMessageSchema),
});

export const sessionsListResponseSchema = z.object({
  sessions: z.array(chatSessionSchema),
});
