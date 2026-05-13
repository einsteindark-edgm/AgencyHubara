/**
 * Filtros y agrupaciones de la vista Órdenes. Único filtrado activo: el de la
 * vista del panel izquierdo (no hay agrupar/orden/cambio-a-tabla — eliminados
 * por feedback del usuario en la iteración del prototipo).
 */

import { useMemo, useState } from "react";
import type { Order, PayType } from "@/entities/order";

export type ViewFilter =
  | "all" | "today" | "overdue" | "tomorrow" | "week" | "ship";
export type PayTypeFilter = "all" | PayType;

const WEEK_DAYS = [
  "2026-05-12", "2026-05-13", "2026-05-14", "2026-05-15",
  "2026-05-16", "2026-05-17", "2026-05-18",
];

export function useOrderFilters(orders: Order[]) {
  const [view, setView] = useState<ViewFilter>("today");
  const [payType, setPayType] = useState<PayTypeFilter>("all");

  const filtered = useMemo(() => {
    return orders.filter((o) => {
      if (payType !== "all" && o.payType !== payType) return false;
      if (view === "today")    return o.dueIso === "2026-05-12";
      if (view === "overdue")  return o.overdue === true;
      if (view === "tomorrow") return o.dueIso === "2026-05-13";
      if (view === "week")     return WEEK_DAYS.includes(o.dueIso);
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
