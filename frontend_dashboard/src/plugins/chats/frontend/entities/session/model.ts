/**
 * Tipos del dominio "sesión". Espejo de los endpoints `/api/dashboard/sessions`
 * y `/api/dashboard/sessions/:id` (ver `hubara_agency/src/dashboard/api.py`).
 */

import type { ChatMessage } from "../message/model";

/** Origen real de la conversación (ver `sessionOriginSchema`). */
export interface SessionOrigin {
  channel: string | null;
  source_id: string | null;
  source_type: string | null;
  headline: string | null;
  first_seen_ms: number | null;
  campaign_name: string | null;
  ad_name: string | null;
}

/** Pedido al que ya pertenece la conversación (ver `sessionOrderRefSchema`). */
export interface SessionOrderRef {
  order_id: string;
  /** Número de orden pelado ("31"). Sin número no se pinta chip. */
  display_id: string | null;
  payment: "pending" | "confirmed" | "cancelled";
  /** Pedidos exitosos de esta sesión (el chip muestra "#33 +1"). */
  count: number;
}

/** Cliente pospuesto («les escribo la otra semana») — ver `sessionPostponedSchema`. */
export interface SessionPostponed {
  status: "esperando" | "cita_pendiente" | "cita_enviada";
  kind: string | null;
  /** Epoch ms en que el cliente dijo que retoma. */
  until_ms: number;
  resume_label: string | null;
  /** Lo que escribió el cliente al aplazar. */
  text: string;
}

/** Item de la lista de sesiones (panel izquierdo). */
export interface ChatSession {
  session_id: string;
  phone_number: string;
  tag: string;
  motivo: string;
  active_agent_route: string;
  phone_number_id: string | null;
  /** Pedido (id backend Medusa) esperando confirmación de pago humana, o null. */
  pending_payment_order_id: string | null;
  /** Pedido al que pertenece la conversación, o null si todavía no hay. */
  order_ref: SessionOrderRef | null;
  /** Unix epoch en segundos (lo que devuelve `stat().st_mtime`). */
  last_updated_timestamp: number;
  /** Epoch ms del último mensaje del cliente, o null si nunca escribió. */
  last_inbound_ms: number | null;
  origin: SessionOrigin | null;
  /** Pospuesto con fecha de retoma, o null. Opcional: backend sin desplegar. */
  postponed?: SessionPostponed | null;
}

export interface StatusHistoryEntry {
  tag: string;
  motivo: string;
  active_route: string;
  /** Unix epoch en segundos. */
  timestamp: number;
}

/** Detalle completo de una sesión (paneles centro + derecho). */
export interface SessionDetails {
  session_id: string;
  phone_number: string;
  tag: string;
  motivo: string;
  memory_content: string | null;
  active_agent_route: string;
  phone_number_id: string | null;
  /** Pedido (id backend Medusa) esperando confirmación de pago humana, o null. */
  pending_payment_order_id: string | null;
  /** Epoch ms del cierre de la ventana de servicio 24h, o null si se desconoce. */
  service_window_expires_at_ms: number | null;
  /** Pedido al que pertenece la conversación, o null si todavía no hay. */
  order_ref: SessionOrderRef | null;
  status_history: StatusHistoryEntry[];
  origin: SessionOrigin | null;
  messages: ChatMessage[];
}
