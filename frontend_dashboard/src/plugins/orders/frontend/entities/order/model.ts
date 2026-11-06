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

import { isInDateRange, type DateRange } from "@/shared/lib";
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
  /** Total vigente: pedido + envío (estimado, o el real si ya se fijó). */
  total: number;
  /** Envío incluido en `total` (0 = desconocido). */
  shipping?: number;
  /** `true` cuando `shipping` es el valor real que fijó el operador al
   *  marcar "en camino"; `false` = tarifa mínima estimada del registro. */
  shippingConfirmed?: boolean;
  /** Envío gratis (desde N cajas de una campaña): el cliente no paga envío. */
  freeShipping?: boolean;
  /** Lo que la tienda paga por el envío de un pedido con envío gratis (el
   *  valor real del despacho); `null` = todavía no se escribió. */
  storeShippingCost?: number | null;
  dueIso: string;
  dueTime: string;
  overdue?: boolean;
  pieces: number;
  agent: string;
  priority: "alta" | "normal" | "baja";
  // Nuevos: meta del backend para que la UI sepa cuándo pintar markers.
  isDraft: boolean;
  /** Marcado "prueba" en el inspector: sigue en el tablero pero NO cuenta en
   *  ningún número (ver `countsInStats`). */
  isTest?: boolean;
  isDueEstimated: boolean; // true cuando dueIso es estimate (created+1d)
  /** Día de creación YYYY-MM-DD en America/Bogota ("" si el backend no lo
   *  mandó). Ubica en el calendario a las órdenes sin entrega agendada. */
  createdIso: string;
}

/**
 * El día con el que una orden cae en el filtro por fecha y en "Ingresos": la
 * ENTREGA agendada; si no está agendada, el día en que se creó. Con solo
 * `dueIso`, cualquier rango escondería las órdenes sin agendar (y una entregada
 * que saltó etapas sin fecha nunca sumaría como ingreso).
 */
export function orderDayIso(o: Pick<Order, "dueIso" | "createdIso">): string {
  return o.dueIso || o.createdIso;
}

/** Valor del pedido SIN envío: lo que se muestra al marcar "en camino", donde
 *  el operador escribe el envío real encima (sumar al `total` contaría el
 *  envío estimado dos veces). */
export function orderValueWithoutShipping(o: Pick<Order, "total" | "shipping">): number {
  return o.total - (o.shipping ?? 0);
}

/** ¿La orden entra en los números de la pantalla? Un pedido de prueba no:
 *  ni contadores, ni KPIs, ni totales de columna, ni ingresos. */
export function countsInStats(o: Pick<Order, "isTest">): boolean {
  return !o.isTest;
}

/** Ingreso = plata que YA entró: columna "Entregada" y pago "Pagado" (y no
 *  es un pedido de prueba). */
export function isCollectedRevenue(
  o: Pick<Order, "status" | "payStatus" | "isTest">,
): boolean {
  return o.status === "delivered" && o.payStatus === "paid" && countsInStats(o);
}

/** Una fila de un desglose del reporte de ventas: la clave (día, etapa, canal,
 *  ciudad) con cuántos pedidos y cuánto suman. */
export interface SalesBreakdown {
  key: string;
  orders: number;
  sales: number;
}

export interface SalesTotals {
  orders: number;
  sales: number;
}

/** Reporte de ventas de un rango (tarjeta del ciclo #18). */
export interface SalesReport {
  orders: number;
  sales: number;
  averageTicket: number;
  shipping: number;
  collected: number;
  toCollect: number;
  /** Pagado (prepago) pero todavía no entregado: está dentro de `toCollect`
   *  porque «Cobrado» es la cuenta del KPI «Ingresos» (entregado y pagado). */
  paidNotDelivered: number;
  refunded: SalesTotals;
  /** Envío estimado al vender (tarifa mínima) y el real que fijó el operador
   *  al despachar; `storeShippingCost` = lo que paga la tienda en los
   *  pedidos con envío gratis. */
  shippingEstimated: number;
  shippingReal: number;
  storeShippingCost: number;
  byPayType: Record<PayType, SalesTotals>;
  byStatus: SalesBreakdown[];
  byDay: SalesBreakdown[];
  byChannel: SalesBreakdown[];
  byCity: SalesBreakdown[];
  cancelled: SalesTotals;
}

function breakdown(orders: Order[], keyOf: (o: Order) => string): SalesBreakdown[] {
  const rows = new Map<string, SalesBreakdown>();
  for (const o of orders) {
    const key = keyOf(o) || "—";
    const row = rows.get(key) ?? { key, orders: 0, sales: 0 };
    row.orders += 1;
    row.sales += o.total;
    rows.set(key, row);
  }
  return [...rows.values()];
}

/** Orden de las etapas en el desglose: el mismo de las columnas del tablero. */
const STATUS_ORDER: OrderStatus[] = ["new", "preparing", "ready", "shipping", "delivered", "delayed", "cancelled"];

/**
 * Reporte de ventas de un rango (tarjeta del ciclo #18). Con las mismas reglas
 * que la vista Orders, así que cuadra con ella por construcción:
 *   - cada pedido cae en su día (`orderDayIso`: entrega, o creación);
 *   - los de prueba no cuentan (`countsInStats`);
 *   - los cancelados no son ventas: van en `cancelled`;
 *   - `collected` es la cuenta del KPI «Ingresos» (`isCollectedRevenue`).
 * `sales` es el total vigente del pedido (con envío); `shipping` es la parte
 * de envío incluida.
 */
export function salesReport(orders: Order[], range: DateRange): SalesReport {
  // El mismo corte de días que el calendario de la vista.
  const inPeriod = orders.filter((o) => countsInStats(o) && isInDateRange(orderDayIso(o), range));
  const sold = inPeriod.filter((o) => o.status !== "cancelled");
  const cancelled = inPeriod.filter((o) => o.status === "cancelled");
  const sum = (list: Order[], value: (o: Order) => number) => list.reduce((a, o) => a + value(o), 0);
  const totals = (list: Order[]): SalesTotals => ({ orders: list.length, sales: sum(list, (o) => o.total) });

  const sales = sum(sold, (o) => o.total);
  const collected = sum(sold.filter(isCollectedRevenue), (o) => o.total);
  // Un estado que no esté en la lista va al final (no primero, con -1).
  const rank = (key: string) => {
    const i = STATUS_ORDER.indexOf(key as OrderStatus);
    return i === -1 ? STATUS_ORDER.length : i;
  };
  const byStatus = breakdown(sold, (o) => o.status).sort((a, b) => rank(a.key) - rank(b.key));
  const bySales = (a: SalesBreakdown, b: SalesBreakdown) => b.sales - a.sales || a.key.localeCompare(b.key);

  return {
    orders: sold.length,
    sales,
    averageTicket: sold.length ? Math.round(sales / sold.length) : 0,
    shipping: sum(sold, (o) => o.shipping ?? 0),
    collected,
    toCollect: sales - collected,
    paidNotDelivered: sum(
      sold.filter((o) => o.payStatus === "paid" && o.status !== "delivered"),
      (o) => o.total,
    ),
    refunded: totals(sold.filter((o) => o.payStatus === "refund")),
    shippingEstimated: sum(sold.filter((o) => !o.shippingConfirmed), (o) => o.shipping ?? 0),
    shippingReal: sum(sold.filter((o) => o.shippingConfirmed), (o) => o.shipping ?? 0),
    storeShippingCost: sum(sold.filter((o) => o.freeShipping), (o) => o.storeShippingCost ?? 0),
    byPayType: {
      cod: totals(sold.filter((o) => o.payType === "cod")),
      confirmed: totals(sold.filter((o) => o.payType === "confirmed")),
    },
    byStatus,
    byDay: breakdown(sold, orderDayIso).sort((a, b) => a.key.localeCompare(b.key)),
    byChannel: breakdown(sold, (o) => o.channel).sort(bySales),
    byCity: breakdown(sold, (o) => o.city).sort(bySales),
    cancelled: totals(cancelled),
  };
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
