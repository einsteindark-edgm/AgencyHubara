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

/**
 * ORDEN a la que ya pertenece la conversación, o null. Solo órdenes reales con
 * número: un draft (venta registrada, entrega sin agendar) todavía no es una
 * orden y viaja como null. Lo computa el backend (`_compute_order_ref`) desde
 * `OrderFacts` — el mismo store de la vista Orders, en una lectura por bandeja.
 *
 * `payment` habla del PAGO, no de la logística: preparando / listo / en camino
 * lo muestra el panel de pedidos del chat. `display_id` es el número PELADO
 * ("31"); el "#" lo pone el adaptador. Nullable solo por tolerancia de rollout:
 * sin número no se pinta chip.
 */
export const sessionOrderRefSchema = z.object({
  order_id: z.string(),
  display_id: z.string().nullable().default(null),
  payment: z.enum(["pending", "confirmed", "cancelled"]),
  count: z.number().default(1),
});

/**
 * Cliente POSPUESTO («les escribo la otra semana»), o null. Lo decide el
 * backend (`postponed_view`): está desde que aplazó hasta que retoma la charla
 * o queda SIN_RESPUESTA. `status`: esperando la fecha / la cita llegó y no
 * salió / la cita salió y espera respuesta / `vencido` (pospuesto MANUAL del
 * equipo con la fecha pasada). `kind: "manual"` = lo puso el operador.
 * `until_ms` = epoch ms de la retoma.
 */
export const sessionPostponedSchema = z.object({
  status: z.enum(["esperando", "cita_pendiente", "cita_enviada", "vencido"]),
  kind: z.string().nullable().default(null),
  until_ms: z.number(),
  resume_label: z.string().nullable().default(null),
  text: z.string().default(""),
  // La fecha ya pasó: la fila se pinta en rojo (hay que retomar).
  overdue: z.boolean().default(false),
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
  // Pedido al que pertenece la conversación (ver `sessionOrderRefSchema`), o
  // null. Enciende el chip de pedido en la fila de la bandeja. `.default(null)`
  // tolera snapshots viejos durante el rollout.
  order_ref: sessionOrderRefSchema.nullable().default(null),
  last_updated_timestamp: z.number(),
  // Epoch ms del último mensaje DEL CLIENTE (null si no escribió). Distinto de
  // `last_updated_timestamp`, que se mueve también con turnos del bot. Es lo
  // que dispara el sonido de "mensaje nuevo" de la bandeja.
  last_inbound_ms: z.number().nullable().default(null),
  // `.default(null)` tolera snapshots viejos del SSE durante el rollout.
  origin: sessionOriginSchema.nullable().default(null),
  // Filtro "Pospuestos" (ver `sessionPostponedSchema`). `.default(null)`
  // tolera un backend sin desplegar todavía.
  postponed: sessionPostponedSchema.nullable().default(null),
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
  // Epoch ms en que cierra la ventana de servicio 24h de WhatsApp, o null si
  // no se conoce. Con la ventana cerrada el composer humano ofrece
  // "Reactivar conversación" (plantilla) en vez de texto libre.
  service_window_expires_at_ms: z.number().nullable().default(null),
  // Ver `chatSessionSchema.order_ref`.
  order_ref: sessionOrderRefSchema.nullable().default(null),
  status_history: z.array(statusHistoryEntrySchema),
  origin: sessionOriginSchema.nullable().default(null),
  messages: z.array(chatMessageSchema),
});

export const sessionsListResponseSchema = z.object({
  sessions: z.array(chatSessionSchema),
});
