/**
 * Entidad "tracked-order": pedido que el ETA agent está siguiendo (desde
 * `preparing` en adelante). Cada cambio de stage produce un `TrackedEvent`
 * con el mensaje que el agente envió y la respuesta del cliente.
 */

export type TrackedStage = "preparing" | "ready" | "shipping" | "out" | "delivered";

/**
 * Stage de un evento del timeline: superset del stage de pedido — el agente
 * también notifica cancelaciones. El stage ACTUAL de un pedido del tablero
 * nunca es `cancelled` (los filtra el backend), pero su historial sí puede
 * contener esa notificación.
 */
export type TrackedEventStage = TrackedStage | "cancelled";

export interface TrackedStageMeta {
  key: TrackedStage;
  label: string;
  color: string;
}

export const TRACKED_STAGES: TrackedStageMeta[] = [
  { key: "preparing", label: "En preparación",   color: "var(--color-warn)" },
  { key: "ready",     label: "Lista para envío", color: "var(--color-violet)" },
  { key: "shipping",  label: "En camino",        color: "var(--color-cyan)" },
  { key: "out",       label: "En reparto",       color: "var(--color-info)" },
  { key: "delivered", label: "Entregada",        color: "var(--color-ok)" },
];

export interface TrackedEvent {
  stage: TrackedEventStage;
  time: string;
  date: string;
  note?: string;
  agentMsg: string;
  reply?: string | null;
  flagged?: boolean;
  flag?: string;
}

/**
 * "Contra entrega hoy" = COD que ya está EN LA CALLE (shipping/out): la plata
 * se cobra al entregar, así que es lo que el repartidor recauda hoy. Vive en
 * la entity porque lo comparten el banner/filtros del sidebar (eta-list) y
 * los headers de grupo por cliente (eta-cards) — FSD: feature → entity, nunca
 * feature → feature.
 */
export function isCodToday(o: TrackedOrder): boolean {
  return o.payType === "cod" && (o.current === "shipping" || o.current === "out");
}

export interface TrackedOrder {
  id: string;
  customer: string;
  short: string;
  color: "a" | "b" | "c" | "d" | "e" | "f";
  /** Teléfono del cliente — clave real del group-by (el nombre puede ser genérico). */
  phone: string;
  city: string;
  current: TrackedStage;
  channel: string;
  needs: boolean;
  payType: "cod" | "confirmed";
  total: number;
  messagesUnread: number;
  events: TrackedEvent[];
}
