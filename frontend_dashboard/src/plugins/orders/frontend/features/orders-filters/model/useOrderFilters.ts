/**
 * Filtros y agrupaciones de la vista Órdenes. Único filtrado activo: el de la
 * vista del panel izquierdo (no hay agrupar/orden/cambio-a-tabla — eliminados
 * por feedback del usuario en la iteración del prototipo).
 *
 * Las fechas son dinámicas (Date.now()) — no hardcoded — para que el filtro
 * funcione correctamente para cualquier fecha de instalación.
 */

import { useMemo, useState } from "react";
import type { Order, PayType } from "@/entities/order";

export type ViewFilter =
  | "all" | "today" | "overdue" | "tomorrow" | "week" | "ship";
export type PayTypeFilter = "all" | PayType;

function buildWeekIsos(): Set<string> {
  const set = new Set<string>();
  for (let i = 0; i < 7; i++) {
    set.add(new Date(Date.now() + i * 86_400_000).toISOString().slice(0, 10));
  }
  return set;
}

export function useOrderFilters(orders: Order[]) {
  const [view, setView] = useState<ViewFilter>("today");
  const [payType, setPayType] = useState<PayTypeFilter>("all");

  const filtered = useMemo(() => {
    const today = new Date().toISOString().slice(0, 10);
    const tomorrow = new Date(Date.now() + 86_400_000).toISOString().slice(0, 10);
    const weekIsos = buildWeekIsos();
    return orders.filter((o) => {
      if (payType !== "all" && o.payType !== payType) return false;
      if (view === "today")    return o.dueIso === today;
      if (view === "overdue")  return o.overdue === true;
      if (view === "tomorrow") return o.dueIso === tomorrow;
      if (view === "week")     return weekIsos.has(o.dueIso);
      if (view === "ship")     return o.status === "shipping";
      return true;
    });
  }, [orders, view, payType]);

  return { view, setView, payType, setPayType, filtered };
}

export function filterLabel(view: ViewFilter): string {
  const map: Record<ViewFilter, string> = {
    all: "Todas las órdenes",
    today: "Órdenes para hoy",
    overdue: "Órdenes retrasadas",
    tomorrow: "Órdenes para mañana",
    week: "Órdenes de la semana",
    ship: "Órdenes en camino",
  };
  return map[view];
}
