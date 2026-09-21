/**
 * Salto de etapas en el kanban. Caso real: el operador olvidó mover el pedido
 * y ya se entregó. Recorrer Lista → En camino → Entregada a destiempo le manda
 * al cliente un WhatsApp por paso, sin sentido; el kanban ofrece en cambio UN
 * salto forzado (`force`) con la nota en el historial y el aviso opcional.
 *
 * Espejo del camino lineal del DAG del backend (`_ALLOWED_TRANSITIONS` en
 * `platform/orders/state.py`); `cancelled` sale de cualquier etapa y no cuenta
 * como salto.
 */
import {
  ORDER_STATUS_META,
  type OrderStatus,
} from "@plugins/orders/frontend/entities/order";

const FLOW: OrderStatus[] = ["new", "preparing", "ready", "shipping", "delivered"];

/** Etapas intermedias que se omiten al ir de `from` a `to` (vacío si el
 *  movimiento es adyacente, hacia atrás o a/desde un estado fuera del flujo). */
export function skippedStages(from: OrderStatus, to: OrderStatus): OrderStatus[] {
  const i = FLOW.indexOf(from);
  const j = FLOW.indexOf(to);
  if (i < 0 || j < 0 || j - i < 2) return [];
  return FLOW.slice(i + 1, j);
}

/** Nota del stage history: deja la traza honesta de qué se omitió. */
export function skipNote(skipped: OrderStatus[]): string {
  return `Salto manual: se omitió ${skipped.map((s) => ORDER_STATUS_META[s].label).join(", ")}`;
}
