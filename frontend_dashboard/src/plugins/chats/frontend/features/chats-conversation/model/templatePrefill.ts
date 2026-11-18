/**
 * Datos del pedido que llenan solos el modal «Reactivar conversación».
 *
 * El chat ya conoce los pedidos del cliente (`useCustomerOrders`, cast de
 * Órdenes): el número, el monto y el estado salen de ahí, no los escribe el
 * operador. Las claves son los nombres de variable del catálogo de plantillas
 * (`order_reference`, `amount_currency`, `status_label`); una plantilla que no
 * las tiene simplemente no las usa.
 */
import {
  ORDER_REF_STATUS_META,
  type CustomerOrder,
} from "@plugins/chats/frontend/entities/order-ref";

function latest(
  orders: readonly CustomerOrder[],
  matches: (order: CustomerOrder) => boolean,
): CustomerOrder | null {
  return [...orders].reverse().find(matches) ?? null;
}

/**
 * El pedido del que habla la plantilla. La lista llega del más viejo al más
 * nuevo. Gana el listo más reciente (el caso «Pedido listo con foto»); si no
 * hay, el último que sigue en curso; si no, el último entregado. Uno cancelado
 * nunca.
 */
export function pickTemplateOrder(
  orders: readonly CustomerOrder[] | undefined,
): CustomerOrder | null {
  if (!orders?.length) return null;
  return (
    latest(orders, (o) => o.status === "ready") ??
    latest(orders, (o) => o.status !== "cancelled" && o.status !== "delivered") ??
    latest(orders, (o) => o.status !== "cancelled")
  );
}

const THOUSANDS = new Intl.NumberFormat("es-CO", { maximumFractionDigits: 0 });

/** Valores por defecto de las variables de la plantilla para ese pedido. */
export function orderTemplateDefaults(order: CustomerOrder | null): Record<string, string> {
  if (!order) return {};
  const defaults: Record<string, string> = {};
  const number = order.display_id?.replace(/^#+/, "").trim();
  if (number) defaults.order_reference = `#${number}`;
  if (typeof order.total_cop === "number") {
    const currency = (order.currency_code ?? "COP").toUpperCase();
    defaults.amount_currency = `$${THOUSANDS.format(order.total_cop)} ${currency}`;
  }
  const status = ORDER_REF_STATUS_META[order.status];
  if (status) defaults.status_label = status.label.toLowerCase();
  return defaults;
}
