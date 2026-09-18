/**
 * Filtros y agrupaciones de la vista Órdenes: la búsqueda y la vista +
 * modalidad del panel izquierdo (no hay agrupar/orden/cambio-a-tabla —
 * eliminados por feedback del usuario en la iteración del prototipo).
 *
 * Las fechas son dinámicas (Date.now()) — no hardcoded — para que el filtro
 * funcione correctamente para cualquier fecha de instalación.
 */

import { useMemo, useState } from "react";
import { matchesSearch } from "@/shared/lib";
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

function buildWeekIsos(): Set<string> {
  const set = new Set<string>();
  for (let i = 0; i < 7; i++) {
    set.add(new Date(Date.now() + i * 86_400_000).toISOString().slice(0, 10));
  }
  return set;
}

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

  const filtered = useMemo(() => {
    const today = new Date().toISOString().slice(0, 10);
    const tomorrow = new Date(Date.now() + 86_400_000).toISOString().slice(0, 10);
    const weekIsos = buildWeekIsos();
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
  }, [searched, view, payType]);

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
