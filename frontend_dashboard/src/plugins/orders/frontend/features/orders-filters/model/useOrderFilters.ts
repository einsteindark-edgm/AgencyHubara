/**
 * Filtros y agrupaciones de la vista Órdenes: la búsqueda y la vista +
 * modalidad del panel izquierdo (no hay agrupar/orden/cambio-a-tabla —
 * eliminados por feedback del usuario en la iteración del prototipo).
 *
 * Las vistas por fecha cortan el día en hora COLOMBIA, igual que los
 * contadores de la sidebar (`OrdersFilters`): `dueIso` es un día calendario
 * que el operador eligió a mano, no un instante.
 */

import { useMemo, useState } from "react";
import {
  addDaysBogotaIso,
  matchesSearch,
  nextDaysBogotaIsoSet,
  todayBogotaIso,
} from "@/shared/lib";
import type { Order, PayType } from "@plugins/orders/frontend/entities/order";

export type ViewFilter =
  | "all"
  | "unscheduled"
  | "today"
  | "overdue"
  | "tomorrow"
  | "week"
  | "inprocess"
  | "ship";
export type PayTypeFilter = "all" | PayType;

/** Lo que promete el placeholder ("# orden o cliente") + el teléfono, que es
 *  como el cliente se identifica cuando escribe o llama. */
function matchesOrder(o: Order, query: string): boolean {
  return matchesSearch(query, [o.id, o.customer, o.phone]);
}

export function useOrderFilters(orders: Order[]) {
  const [query, setQuery] = useState("");
  const [view, setView] = useState<ViewFilter>("all");
  const [payType, setPayType] = useState<PayTypeFilter>("all");

  /** Órdenes que coinciden con la búsqueda — sin vista ni modalidad. Es la
   *  base de los contadores de la sidebar: así dicen en qué vista quedó lo
   *  buscado en vez de seguir contando todo. */
  const searched = useMemo(
    () => orders.filter((o) => matchesOrder(o, query)),
    [orders, query],
  );

  // Día COLOMBIANO, el mismo corte que los contadores de la sidebar. En UTC
  // la frontera caía a las 19:00 locales: desde esa hora "Para hoy" llenaba el
  // tablero con las entregas de mañana mientras su contador contaba las de hoy.
  // Se lee en cada render y entra en las deps: congelado dentro del memo, pasada
  // la medianoche el filtro seguiría en el día anterior.
  const today = todayBogotaIso();
  const tomorrow = addDaysBogotaIso(1);

  const filtered = useMemo(() => {
    // Esta semana = próximos 7 días incluyendo hoy. Un Set no sirve de dep:
    // se arma acá y se rehace cada vez que cambia `today`.
    const weekIsos = nextDaysBogotaIsoSet(7);
    return searched.filter((o) => {
      if (payType !== "all" && o.payType !== payType) return false;
      // "No agendadas" = órdenes sin fecha de entrega asignada — típicamente
      // las que también viven en la columna "Nueva" del kanban porque el
      // operador todavía no las agendó.
      if (view === "unscheduled") return !o.dueIso;
      // Filtros de fecha: comparamos contra `dueIso` (fecha de entrega real,
      // no fecha de creación). Si no tiene dueIso, NO matchea ningún
      // filtro de fecha (porque no está agendada).
      if (view === "today")    return !!o.dueIso && o.dueIso === today;
      if (view === "overdue")  return o.overdue === true;
      if (view === "tomorrow") return !!o.dueIso && o.dueIso === tomorrow;
      if (view === "week")     return !!o.dueIso && weekIsos.has(o.dueIso);
      // "En proceso" = preparing + ready (operativas, no terminales ni new).
      if (view === "inprocess") return o.status === "preparing" || o.status === "ready";
      if (view === "ship")     return o.status === "shipping";
      return true;
    });
  }, [searched, view, payType, today, tomorrow]);

  return { query, setQuery, view, setView, payType, setPayType, searched, filtered };
}

export function filterLabel(view: ViewFilter): string {
  const map: Record<ViewFilter, string> = {
    all: "Todas las órdenes",
    unscheduled: "Órdenes sin agendar",
    today: "Órdenes para hoy",
    overdue: "Órdenes retrasadas",
    tomorrow: "Órdenes para mañana",
    week: "Órdenes de la semana",
    inprocess: "Órdenes en proceso",
    ship: "Órdenes en camino",
  };
  return map[view];
}
