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

export function useOrderFilters(orders: Order[]) {
  const [view, setView] = useState<ViewFilter>("all");
  const [payType, setPayType] = useState<PayTypeFilter>("all");

  const filtered = useMemo(() => {
    const today = new Date().toISOString().slice(0, 10);
    const tomorrow = new Date(Date.now() + 86_400_000).toISOString().slice(0, 10);
    const weekIsos = buildWeekIsos();
    return orders.filter((o) => {
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
  }, [orders, view, payType]);

  return { view, setView, payType, setPayType, filtered };
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
