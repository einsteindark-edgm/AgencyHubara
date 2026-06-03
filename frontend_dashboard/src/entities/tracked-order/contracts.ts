/**
 * Schemas Zod para validar las respuestas del backend en el boundary HTTP.
 * Se activan en `api.ts` con `.parse(data)`, así un cambio de contrato del
 * endpoint `/api/chats/eta/tracked-orders` truena temprano y con mensaje claro.
 *
 * Reflejan los tipos de `model.ts` (`TrackedOrder` / `TrackedEvent`). El backend
 * que los emite es `hubara_agency/src/plugins/chats/api/eta.py`.
 */

import { z } from "zod";

export const trackedStageSchema = z.enum([
  "preparing",
  "ready",
  "shipping",
  "out",
  "delivered",
]);

export const trackedEventSchema = z.object({
  stage: trackedStageSchema,
  time: z.string(),
  date: z.string(),
  note: z.string().nullable().optional(),
  agentMsg: z.string(),
  reply: z.string().nullable().optional(),
  flagged: z.boolean().optional().default(false),
  flag: z.string().nullable().optional(),
});

export const trackedOrderSchema = z.object({
  id: z.string(),
  customer: z.string(),
  short: z.string(),
  color: z.enum(["a", "b", "c", "d", "e", "f"]),
  city: z.string(),
  current: trackedStageSchema,
  channel: z.string(),
  needs: z.boolean(),
  payType: z.enum(["cod", "confirmed"]),
  total: z.number(),
  messagesUnread: z.number(),
  events: z.array(trackedEventSchema),
});

export const trackedOrdersListResponseSchema = z.object({
  orders: z.array(trackedOrderSchema),
  count: z.number().optional(),
});
