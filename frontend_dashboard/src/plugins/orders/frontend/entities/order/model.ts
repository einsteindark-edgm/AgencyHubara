/**
 * Tipos del dominio "orden". Los datos vienen del backend en
 * `/api/orders/orders` (Medusa v2 v read-side), pero la UI del kanban
 * necesita un shape específico — este modelo es el contrato canónico
 * que consumen los componentes (`OrdersBoard`, `OrdersInspector`, etc.).
 *
 * Re-exporta tipos del backend (Zod-inferred en `contracts.ts`). Todo lo
 * dependiente del reloj (`overdue`) viene calculado del backend; los labels
 * relativos ("hoy/mañana") se derivan en render, nunca en el mapper
 * (auditoría 2026-06-10, F0.5).
 */

import type {
  OrderDetail,
  OrderItemDetail,
  OrderAddress,
  OrderSummary,
  OrderTimelineEvent,
} from "./contracts";

export type OrderStatus =
  | "new"
  | "preparing"
  | "ready"
  | "shipping"
  | "delivered"
  | "delayed"
  | "cancelled";

export type PayStatus = "paid" | "partial" | "pending" | "refund";
export type PayType = "cod" | "confirmed";

/**
 * Shape que consume `OrdersBoard` y `OrdersInspector`. Mantiene la forma
 * histórica del prototipo (camelCase + algunas derivaciones UI) — los
 * mappers en `api.ts` convierten `OrderSummary` del backend (snake_case)
 * a este shape para que las features no tengan que cambiar todavía.
 */
export interface Order {
  id: string;
  customer: string;
  short: string;
  color: "a" | "b" | "c" | "d" | "e" | "f";
  phone: string;
  city: string;
  channel: string;
  status: OrderStatus;
  payStatus: PayStatus;
  payType: PayType;
  items: number;
  total: number;
  dueIso: string;
  dueTime: string;
  overdue?: boolean;
  pieces: number;
  agent: string;
  priority: "alta" | "normal" | "baja";
  // Nuevos: meta del backend para que la UI sepa cuándo pintar markers.
  isDraft: boolean;
  isDueEstimated: boolean; // true cuando dueIso es estimate (created+1d)
}

export interface OrderStatusMeta {
  label: string;
  color: string;
  bg: string;
}

export const ORDER_STATUS_META: Record<OrderStatus, OrderStatusMeta> = {
  new:       { label: "Nueva",          color: "var(--color-info)", bg: "var(--color-info-soft)" },
  preparing: { label: "En preparación", color: "var(--color-warn)", bg: "var(--color-warn-soft)" },
  ready:     { label: "Lista",          color: "var(--color-violet)", bg: "var(--color-violet-soft)" },
  shipping:  { label: "En camino",      color: "var(--color-cyan)", bg: "var(--color-cyan-soft)" },
  delivered: { label: "Entregada",      color: "var(--color-ok)", bg: "var(--color-ok-soft)" },
  delayed:   { label: "Retrasada",      color: "var(--color-danger)", bg: "rgba(255,114,105,0.2)"  },
  cancelled: { label: "Cancelada",      color: "var(--color-neutral)", bg: "var(--color-neutral-soft)" },
};

export const PAY_STATUS_META: Record<PayStatus, { label: string; color: string }> = {
  paid:    { label: "Pagado",      color: "var(--color-ok)" },
  partial: { label: "Parcial",     color: "var(--color-warn)" },
  pending: { label: "Pendiente",   color: "var(--color-danger)" },
  refund:  { label: "Reembolsado", color: "var(--color-neutral)" },
};

// Re-export the backend types for components that need the richer detail.
export type { OrderDetail, OrderItemDetail, OrderAddress, OrderSummary, OrderTimelineEvent };
