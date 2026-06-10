/**
 * Tipos del dominio "orden". Los datos vienen del backend en
 * `/api/orders/orders` (Medusa v2 v read-side), pero la UI del kanban
 * necesita un shape específico — este modelo es el contrato canónico
 * que consumen los componentes (`OrdersBoard`, `OrdersInspector`, etc.).
 *
 * Re-exporta tipos del backend (Zod-inferred en `contracts.ts`) + algunas
 * derivaciones puramente UI (overdue, etc.) que el cliente recalcula al
 * leer los datos.
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
  due: string;
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
  new:       { label: "Nueva",          color: "#5fa9ff", bg: "rgba(95,169,255,0.18)" },
  preparing: { label: "En preparación", color: "#ffb44a", bg: "rgba(255,180,74,0.18)" },
  ready:     { label: "Lista",          color: "#d68aff", bg: "rgba(214,138,255,0.18)" },
  shipping:  { label: "En camino",      color: "#5fdcff", bg: "rgba(95,220,255,0.18)" },
  delivered: { label: "Entregada",      color: "#5be07b", bg: "rgba(91,224,123,0.18)" },
  delayed:   { label: "Retrasada",      color: "#ff7269", bg: "rgba(255,114,105,0.2)"  },
  cancelled: { label: "Cancelada",      color: "#8e8e93", bg: "rgba(142,142,147,0.18)" },
};

export const PAY_STATUS_META: Record<PayStatus, { label: string; color: string }> = {
  paid:    { label: "Pagado",      color: "#5be07b" },
  partial: { label: "Parcial",     color: "#ffb44a" },
  pending: { label: "Pendiente",   color: "#ff7269" },
  refund:  { label: "Reembolsado", color: "#8e8e93" },
};

// Re-export the backend types for components that need the richer detail.
export type { OrderDetail, OrderItemDetail, OrderAddress, OrderSummary, OrderTimelineEvent };
