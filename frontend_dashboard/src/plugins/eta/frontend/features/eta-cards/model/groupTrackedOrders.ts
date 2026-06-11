/**
 * Agrupa los pedidos rastreados POR CLIENTE para la pantalla central de ETA.
 *
 * La clave es el `phone` (la identidad real del cliente en WhatsApp) — el
 * nombre puede ser genérico ("Cliente WhatsApp") y colapsaría clientes
 * distintos. Pedidos sin teléfono no se agrupan entre sí: cada uno forma su
 * propio grupo (clave sintética por id) — mejor mostrar de más que mezclar
 * pedidos de clientes distintos bajo un mismo header.
 *
 * El orden de los grupos preserva el orden del backend (primera aparición),
 * igual que el orden de los pedidos dentro del grupo.
 */

import {
  isCodToday,
  type TrackedOrder,
} from "@plugins/eta/frontend/entities/tracked-order";

export interface TrackedOrderGroup {
  /** Clave estable del grupo: phone, o `id:<order_id>` si no hay teléfono. */
  key: string;
  customer: string;
  phone: string;
  short: string;
  color: TrackedOrder["color"];
  orders: TrackedOrder[];
  /** Algún pedido del grupo necesita atención. */
  needs: boolean;
  /** $ a cobrar HOY en este grupo (COD en la calle — mismo predicado del banner). */
  codPending: number;
}

export function groupTrackedOrders(orders: TrackedOrder[]): TrackedOrderGroup[] {
  const byKey = new Map<string, TrackedOrderGroup>();
  for (const o of orders) {
    const key = o.phone ? o.phone : `id:${o.id}`;
    let g = byKey.get(key);
    if (!g) {
      g = {
        key,
        customer: o.customer || "Cliente",
        phone: o.phone,
        short: o.short,
        color: o.color,
        orders: [],
        needs: false,
        codPending: 0,
      };
      byKey.set(key, g);
    }
    g.orders.push(o);
    g.needs ||= o.needs;
    if (isCodToday(o)) g.codPending += o.total;
  }
  return [...byKey.values()];
}
