/**
 * Schemas Zod para validar las respuestas del backend en el boundary HTTP.
 * Se activan en `api.ts` con `.parse(data)`, así un cambio de contrato del
 * endpoint `/api/eta/tracked-orders` truena temprano y con mensaje claro.
 *
 * Reflejan los tipos de `model.ts` (`TrackedOrder` / `TrackedEvent`). El backend
 * que los emite es `hubara_agency/src/plugins/eta/api/__init__.py`.
 */

import { z } from "zod";

export const trackedStageSchema = z.enum([
  "preparing",
  "ready",
  "shipping",
  "out",
  "delivered",
]);

/**
 * Stage de un EVENTO del timeline. Superset del stage del pedido: el agente
 * también notifica cancelaciones (`cancelled`), así que el historial puede
 * contenerlas aunque el pedido ya no esté cancelado (ej. re-activado) — el
 * tablero igual NO lista pedidos cuyo stage ACTUAL sea cancelled (filtra el
 * backend). Sin este valor, UN evento cancelled tumbaba el parse de TODA la
 * respuesta y la sección quedaba vacía en silencio (L-10).
 */
export const trackedEventStageSchema = z.enum([
  ...trackedStageSchema.options,
  "cancelled",
]);

export const trackedEventSchema = z.object({
  stage: trackedEventStageSchema,
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
  // Clave real de agrupación por cliente (el nombre puede ser genérico).
  // `.default("")` para tolerar un backend aún sin el campo.
  phone: z.string().optional().default(""),
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
