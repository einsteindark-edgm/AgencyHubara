/**
 * Vista mínima de los estados de pedido para el panel del chat móvil.
 *
 * Chats NO importa `@plugins/orders` (P-22) — duplica acá la vista mínima:
 * labels ES, tono de color, y el DAG de transiciones. El backend
 * (`platform/orders/state.py`) es la FUENTE DE VERDAD de las transiciones
 * válidas; este mapa solo decide qué botones mostrar (si drifta, el backend
 * rechaza la transición y el panel muestra el error).
 */
import type { OrderRefStatus } from "./contracts";

export const ORDER_REF_STATUS_META: Record<
  OrderRefStatus,
  { label: string; tone: string }
> = {
  new: { label: "Nuevo", tone: "new" },
  preparing: { label: "Preparando", tone: "preparing" },
  ready: { label: "Listo", tone: "ready" },
  shipping: { label: "En camino", tone: "shipping" },
  delivered: { label: "Entregado", tone: "delivered" },
  cancelled: { label: "Cancelado", tone: "cancelled" },
};

/** Transiciones válidas desde cada estado (DAG del backend). Los terminales
 *  (`delivered`, `cancelled`) no tienen destino. */
export const NEXT_STAGES: Record<OrderRefStatus, OrderRefStatus[]> = {
  new: ["preparing", "cancelled"],
  preparing: ["ready", "cancelled"],
  ready: ["shipping", "cancelled"],
  shipping: ["delivered", "cancelled"],
  delivered: [],
  cancelled: [],
};

/** Verbo del botón según el estado DESTINO (lo que hace avanzar el pedido). */
export const STAGE_ACTION_LABEL: Record<OrderRefStatus, string> = {
  new: "Reabrir",
  preparing: "Preparar",
  ready: "Marcar listo",
  shipping: "Despachar",
  delivered: "Marcar entregado",
  cancelled: "Cancelar",
};

/**
 * PM2-M11: fecha de entrega legible ("mar 15 jul") en vez del ISO crudo
 * ("2026-07-15"). Pura sobre una fecha FIJA (no deriva del reloj — regla 5 de
 * la política de estado). Un ISO malformado se devuelve tal cual (mejor crudo
 * que una tarjeta rota).
 */
export function formatDueDate(iso: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (!m) return iso;
  const date = new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3])));
  if (Number.isNaN(date.getTime())) return iso;
  return new Intl.DateTimeFormat("es-CO", {
    weekday: "short",
    day: "numeric",
    month: "short",
    timeZone: "UTC",
  }).format(date);
}
